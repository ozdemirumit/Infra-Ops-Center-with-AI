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

def _fetch_proxy_models(base_url: str = None, api_key: str = None, timeout: float = 5.0) -> dict[str, list[str]]:
    """
    Query LLM Sentinel proxy for available models grouped by provider.

    Tries multiple endpoints (proxies expose models differently):
      1. GET /v1/models                 (OpenAI-compatible list)
      2. GET /v1/providers              (custom provider definitions)
      3. GET /v1/aliases                (model aliases)

    Returns {"anthropic": [...], "openai": [...], "ollama": [...], "custom-foo": [...]}.
    Models from /v1/aliases are prefixed with [alias] in the list.
    """
    base = (base_url or f"http://{settings.PROXY_HOST}:{settings.PROXY_PORT}").rstrip("/")
    key = api_key or settings.PROXY_API_KEY

    if not key:
        return {}

    headers = {"Authorization": f"Bearer {key}"}
    grouped: dict[str, list[str]] = {}

    # 1. /v1/models — main model list
    try:
        resp = httpx.get(f"{base}/v1/models", headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, dict) and "data" in data:
            # OpenAI-style: {"data": [{"id": "model-name", "owned_by": "anthropic"}, ...]}
            for m in data["data"]:
                provider = m.get("owned_by", m.get("provider", "unknown"))
                model_id = m.get("id", "")
                if model_id:
                    grouped.setdefault(provider, []).append(model_id)
        elif isinstance(data, dict) and "models" in data:
            for m in data["models"]:
                provider = m.get("provider", "unknown")
                model_id = m.get("name", m.get("id", ""))
                if model_id:
                    grouped.setdefault(provider, []).append(model_id)
        elif isinstance(data, dict):
            # Already provider-grouped: {"anthropic": [...], "openai": [...]}
            for provider, models in data.items():
                if isinstance(models, list):
                    grouped[provider] = [
                        (m if isinstance(m, str) else m.get("name", m.get("id", "")))
                        for m in models
                        if m
                    ]
    except Exception as e:
        logger.debug(f"Proxy /v1/models unavailable ({base}): {type(e).__name__}: {e}")

    # 2. /v1/providers — proxy-defined custom providers (e.g. private endpoints)
    try:
        resp = httpx.get(f"{base}/v1/providers", headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            # Format: [{"name": "custom-mistral", "models": ["foo", "bar"], "endpoint": "..."}, ...]
            providers_list = data if isinstance(data, list) else data.get("providers", [])
            for p in providers_list:
                if not isinstance(p, dict):
                    continue
                pname = p.get("name", p.get("id", ""))
                pmodels = p.get("models", [])
                if pname and pmodels:
                    # Normalize names to strings
                    model_names = [m if isinstance(m, str) else m.get("name", m.get("id", "")) for m in pmodels]
                    model_names = [m for m in model_names if m]
                    if model_names:
                        grouped.setdefault(pname, []).extend(model_names)
    except Exception as e:
        logger.debug(f"Proxy /v1/providers unavailable: {e}")

    # 3. /v1/aliases — model aliases (used as shortcut names)
    try:
        resp = httpx.get(f"{base}/v1/aliases", headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            aliases_list = data if isinstance(data, list) else data.get("aliases", [])
            for a in aliases_list:
                if not isinstance(a, dict):
                    continue
                alias_name = a.get("alias", "")
                provider = a.get("provider", "aliases")
                if alias_name:
                    # Add to a dedicated "aliases" provider AND to the underlying provider
                    grouped.setdefault("aliases", []).append(alias_name)
    except Exception as e:
        logger.debug(f"Proxy /v1/aliases unavailable: {e}")

    if grouped:
        total = sum(len(v) for v in grouped.values())
        logger.info(f"Proxy: discovered {total} model(s) across {len(grouped)} provider(s)")
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
