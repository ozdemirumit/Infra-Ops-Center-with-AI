"""
Model Discovery — dynamically fetches available models from:
  - LLM Sentinel proxy (/v1/models endpoint)
  - Local Ollama instance (/api/tags endpoint)
  - Fallback: hardcoded provider lists

Cached for 5 minutes to avoid hammering endpoints.
"""

import time
import threading
from typing import Optional
import httpx

from config.settings import settings
from logging_config.logger import get_logger

logger = get_logger("model_discovery")

# Fallback model lists (used when discovery fails or provider is disabled)
FALLBACK_MODELS = {
    "anthropic": [
        "claude-opus-4-5",
        "claude-sonnet-4-5",
        "claude-3-5-haiku-latest",
        "claude-3-5-sonnet-latest",
    ],
    "openai": [
        "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo",
    ],
    "gemini": [
        "gemini-2.0-flash",
        "gemini-2.5-pro-preview-03-25",
        "gemini-1.5-pro",
    ],
    "ollama": [
        "llama3.1", "llama3.1:70b", "qwen2.5:32b", "qwen2.5:72b",
        "mistral", "mixtral", "command-r", "phi3",
    ],
}

# In-memory cache
_cache: dict = {}
_cache_lock = threading.Lock()
CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str) -> Optional[dict]:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < CACHE_TTL:
            return entry["data"]
        return None


def _cache_set(key: str, data: dict):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time()}


def invalidate_cache():
    """Force re-fetch on next discovery call."""
    with _cache_lock:
        _cache.clear()


# ─── Ollama Discovery ────────────────────────────────────────────────

def _fetch_ollama_models(base_url: str = None, timeout: float = 5.0) -> list[str]:
    """
    Query a local Ollama instance for installed models.
    Endpoint: GET /api/tags

    Returns list of model names, e.g. ["llama3.1:latest", "qwen2.5:32b"].
    """
    base = (base_url or settings.OLLAMA_URL).rstrip("/")
    try:
        resp = httpx.get(f"{base}/api/tags", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        if models:
            logger.info(f"Ollama: discovered {len(models)} model(s) at {base}")
        return models
    except Exception as e:
        logger.debug(f"Ollama discovery failed ({base}): {type(e).__name__}: {e}")
        return []


# ─── Proxy Discovery ─────────────────────────────────────────────────

def _extract_model_name(item) -> str:
    """Extract model name/id from a string or dict (multiple field name variants)."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for field in ("id", "name", "model", "model_name", "model_id", "default_model"):
            if item.get(field):
                return str(item[field])
    return ""


def _fetch_proxy_models(base_url: str = None, api_key: str = None, timeout: float = 5.0) -> dict[str, list[str]]:
    """
    Query LLM Sentinel proxy for available models grouped by provider.

    Calls multiple endpoints — each proxy implementation exposes things differently:
      • GET /v1/models                       (OpenAI-compatible list)
      • GET /v1/providers                    (custom provider definitions)
      • GET /v1/providers/{name}/models      (per-provider model list)
      • GET /v1/aliases                      (model aliases)

    Returns {provider_name: [model, ...]}
    Custom proxy-defined providers (e.g. "Heimdal") appear as their own entries.
    """
    base = (base_url or f"http://{settings.PROXY_HOST}:{settings.PROXY_PORT}").rstrip("/")
    key = api_key or settings.PROXY_API_KEY

    if not key:
        return {}

    headers = {"Authorization": f"Bearer {key}"}
    grouped: dict[str, list[str]] = {}

    # ─── 1. /v1/models — OpenAI-compatible list ───
    try:
        resp = httpx.get(f"{base}/v1/models", headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            logger.debug(f"Proxy /v1/models raw: {str(data)[:300]}")

            if isinstance(data, dict) and "data" in data:
                # OpenAI: {"data": [{"id": "...", "owned_by": "...", "provider": "..."}, ...]}
                for m in data["data"]:
                    provider = (m.get("owned_by") or m.get("provider") or m.get("type")
                                or m.get("provider_name") or "unknown")
                    model_id = _extract_model_name(m)
                    if model_id:
                        grouped.setdefault(provider, []).append(model_id)
            elif isinstance(data, dict) and "models" in data:
                for m in data["models"]:
                    provider = m.get("provider") or m.get("type") or "unknown"
                    model_id = _extract_model_name(m)
                    if model_id:
                        grouped.setdefault(provider, []).append(model_id)
            elif isinstance(data, dict):
                # Already provider-grouped dict
                for provider, models in data.items():
                    if isinstance(models, list):
                        names = [_extract_model_name(m) for m in models]
                        names = [n for n in names if n]
                        if names:
                            grouped[provider] = names
            elif isinstance(data, list):
                # Flat list of model objects
                for m in data:
                    if isinstance(m, dict):
                        provider = m.get("provider") or m.get("owned_by") or m.get("type") or "unknown"
                        model_id = _extract_model_name(m)
                        if model_id:
                            grouped.setdefault(provider, []).append(model_id)
    except Exception as e:
        logger.debug(f"Proxy /v1/models error: {type(e).__name__}: {e}")

    # ─── 2. /v1/providers — custom proxy provider definitions ───
    providers_seen = []
    try:
        resp = httpx.get(f"{base}/v1/providers", headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            logger.debug(f"Proxy /v1/providers raw: {str(data)[:500]}")

            # Try multiple container keys
            providers_list = None
            if isinstance(data, list):
                providers_list = data
            elif isinstance(data, dict):
                for k in ("providers", "data", "items", "results"):
                    if isinstance(data.get(k), list):
                        providers_list = data[k]
                        break
                if providers_list is None and data:
                    # Maybe dict keyed by provider name: {"Heimdal": {...}, "OpenAI": {...}}
                    providers_list = [
                        {"name": k, **v} if isinstance(v, dict) else {"name": k}
                        for k, v in data.items()
                    ]

            if providers_list:
                for p in providers_list:
                    if not isinstance(p, dict):
                        continue
                    # Extract provider name (many possible keys)
                    pname = (p.get("name") or p.get("id") or p.get("provider_name")
                             or p.get("provider") or "")
                    if not pname:
                        continue
                    providers_seen.append(pname)

                    # Determine model list — try every known field
                    pmodels = (p.get("models") or p.get("available_models")
                               or p.get("model_list") or [])
                    model_names = []
                    if isinstance(pmodels, list):
                        model_names = [_extract_model_name(m) for m in pmodels]
                        model_names = [m for m in model_names if m]

                    # Fallback: single default model
                    if not model_names:
                        default = (p.get("default_model") or p.get("model")
                                   or p.get("default") or "")
                        if default:
                            model_names = [str(default)]

                    # Try per-provider endpoint if still empty
                    if not model_names:
                        try:
                            r2 = httpx.get(
                                f"{base}/v1/providers/{pname}/models",
                                headers=headers, timeout=timeout,
                            )
                            if r2.status_code == 200:
                                pdata = r2.json()
                                pm_list = pdata if isinstance(pdata, list) else pdata.get("models", pdata.get("data", []))
                                if isinstance(pm_list, list):
                                    model_names = [_extract_model_name(m) for m in pm_list]
                                    model_names = [m for m in model_names if m]
                        except Exception:
                            pass

                    if model_names:
                        # Add as own provider group (preserving original name)
                        grouped.setdefault(pname, []).extend(model_names)
                        logger.info(f"Proxy provider '{pname}': {len(model_names)} model(s)")
                    else:
                        # No models found — register provider with placeholder
                        grouped.setdefault(pname, [])
                        logger.info(f"Proxy provider '{pname}' registered without models")
    except Exception as e:
        logger.debug(f"Proxy /v1/providers error: {type(e).__name__}: {e}")

    # ─── 3. /v1/aliases — model aliases ───
    try:
        resp = httpx.get(f"{base}/v1/aliases", headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            aliases_list = None
            if isinstance(data, list):
                aliases_list = data
            elif isinstance(data, dict):
                for k in ("aliases", "data", "items"):
                    if isinstance(data.get(k), list):
                        aliases_list = data[k]
                        break

            if aliases_list:
                for a in aliases_list:
                    if not isinstance(a, dict):
                        continue
                    alias_name = a.get("alias") or a.get("name") or a.get("id")
                    if alias_name:
                        grouped.setdefault("aliases", []).append(str(alias_name))
    except Exception as e:
        logger.debug(f"Proxy /v1/aliases error: {type(e).__name__}: {e}")

    if grouped:
        total = sum(len(v) for v in grouped.values())
        logger.info(
            f"Proxy: discovered {total} model(s) across {len(grouped)} provider(s): "
            f"{list(grouped.keys())}"
        )
    return grouped


# ─── Public API ──────────────────────────────────────────────────────

def get_available_models() -> dict[str, list[str]]:
    """
    Returns available models grouped by provider.

    Discovery order:
      1. LLM Sentinel proxy /v1/models, /v1/providers, /v1/aliases
         (only if PROXY_ENABLED and API key configured)
      2. Local Ollama /api/tags (always probed if reachable)
      3. Static FALLBACK_MODELS for cloud providers
         (always merged so user can pick known cloud models even without proxy)

    Result is cached for CACHE_TTL seconds.
    """
    cache_key = "all_models"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, list[str]] = {}

    # 1. Proxy discovery — includes proxy-defined providers + aliases
    if settings.PROXY_ENABLED and settings.PROXY_API_KEY:
        proxy_models = _fetch_proxy_models()
        if proxy_models:
            for provider, models in proxy_models.items():
                if models:
                    # Mark proxy-sourced entries with a 🛡️ prefix label-wise
                    # (stored as-is; UI decorates separately)
                    result.setdefault(provider, []).extend(models)

    # 2. Ollama discovery — local instance is authoritative for ollama provider
    ollama_models = _fetch_ollama_models()
    if ollama_models:
        result["ollama"] = ollama_models

    # 3. Static fallbacks — ALWAYS merge so cloud providers have models
    #    even when proxy is offline or not configured.
    for provider, fallback in FALLBACK_MODELS.items():
        existing = result.get(provider, [])
        # If we have proxy-sourced models, keep them and append fallbacks not seen
        seen = set(existing)
        for m in fallback:
            if m not in seen:
                existing.append(m)
                seen.add(m)
        result[provider] = existing

    # Deduplicate while preserving order
    for provider in list(result.keys()):
        seen = set()
        unique = []
        for m in result[provider]:
            if m not in seen:
                seen.add(m)
                unique.append(m)
        result[provider] = unique

    _cache_set(cache_key, result)
    return result


def get_provider_status() -> dict[str, dict]:
    """
    Returns status of each provider for UI display.
    {"anthropic": {"available": True, "source": "proxy"}, ...}
    """
    status = {}
    proxy_models = (
        _fetch_proxy_models()
        if (settings.PROXY_ENABLED and settings.PROXY_API_KEY)
        else {}
    )
    ollama_models = _fetch_ollama_models()

    for provider in ["anthropic", "openai", "gemini", "ollama"]:
        source = "fallback"
        available = False

        if provider in proxy_models and proxy_models[provider]:
            source = "proxy"
            available = True
        elif provider == "ollama" and ollama_models:
            source = "local"
            available = True
        elif provider == "anthropic" and settings.ANTHROPIC_API_KEY:
            source = "direct"
            available = True
        elif provider == "openai" and settings.OPENAI_API_KEY:
            source = "direct"
            available = True
        elif provider == "gemini" and settings.GEMINI_API_KEY:
            source = "direct"
            available = True

        status[provider] = {"available": available, "source": source}

    return status
