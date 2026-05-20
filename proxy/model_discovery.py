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
    Query LLM Sentinel proxy for all available models grouped by provider.
    Endpoint: GET /v1/models

    Returns {"anthropic": [...], "openai": [...], "ollama": [...], ...}
    """
    base = (base_url or f"http://{settings.PROXY_HOST}:{settings.PROXY_PORT}").rstrip("/")
    key = api_key or settings.PROXY_API_KEY

    if not key:
        return {}

    headers = {"Authorization": f"Bearer {key}"}

    try:
        resp = httpx.get(f"{base}/v1/models", headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        # Multiple response formats supported:
        # 1) OpenAI-style: {"data": [{"id": "model-name", "owned_by": "anthropic"}, ...]}
        # 2) Provider-grouped: {"anthropic": [...], "openai": [...]}
        # 3) Flat list: {"models": [{"name": "...", "provider": "..."}, ...]}

        grouped: dict[str, list[str]] = {}

        if isinstance(data, dict) and "data" in data:
            # OpenAI-style
            for m in data["data"]:
                provider = m.get("owned_by", "unknown")
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
            # Already provider-grouped
            for provider, models in data.items():
                if isinstance(models, list):
                    grouped[provider] = [
                        (m if isinstance(m, str) else m.get("name", m.get("id", "")))
                        for m in models
                        if m
                    ]

        if grouped:
            total = sum(len(v) for v in grouped.values())
            logger.info(f"Proxy: discovered {total} model(s) across {len(grouped)} provider(s)")
        return grouped
    except Exception as e:
        logger.debug(f"Proxy model discovery failed ({base}): {type(e).__name__}: {e}")
        return {}


# ─── Public API ──────────────────────────────────────────────────────

def get_available_models() -> dict[str, list[str]]:
    """
    Returns available models grouped by provider.
    Tries (in order):
      1. LLM Sentinel proxy (if enabled and configured)
      2. Local Ollama (always, since it's free to probe)
      3. Falls back to hardcoded lists for cloud providers

    Result is cached for CACHE_TTL seconds.
    """
    cache_key = "all_models"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, list[str]] = {}

    # 1. Proxy discovery
    if settings.PROXY_ENABLED and settings.PROXY_API_KEY:
        proxy_models = _fetch_proxy_models()
        if proxy_models:
            for provider, models in proxy_models.items():
                if models:
                    result.setdefault(provider, []).extend(models)

    # 2. Ollama discovery (local instance)
    ollama_models = _fetch_ollama_models()
    if ollama_models:
        # Replace any existing ollama list with the local one (it's authoritative)
        result["ollama"] = ollama_models

    # 3. Fill in fallbacks for missing providers
    for provider, fallback in FALLBACK_MODELS.items():
        if provider not in result or not result[provider]:
            result[provider] = list(fallback)

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
