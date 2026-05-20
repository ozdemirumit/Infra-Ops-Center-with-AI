"""
AI Proxy module — unified AI client supporting:
  - LLM Sentinel proxy (recommended for enterprise)
  - Direct API (Anthropic, OpenAI, Gemini)
  - On-prem Ollama (local models)

The active backend is chosen based on configuration + selected provider/model.
"""

import time
import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import streamlit as st
from config.settings import settings
from proxy.data_filter import sanitize_messages
from logging_config.logger import get_logger, audit_log, AuditEvent

logger = get_logger("proxy")


def _make_response(content_blocks, stop_reason, model, in_tokens, out_tokens):
    """Build the SimpleNamespace response used across the app."""
    blocks = []
    for b in content_blocks:
        blocks.append(SimpleNamespace(
            type=b.get("type"),
            text=b.get("text"),
            id=b.get("id"),
            name=b.get("name"),
            input=b.get("input"),
        ))
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason or "end_turn",
        model=model or "",
        usage=SimpleNamespace(input_tokens=in_tokens or 0, output_tokens=out_tokens or 0),
    )


class AIProxy:
    """
    Unified AI client. Routes calls to:
      - Proxy (if PROXY_ENABLED and provider != ollama)
      - Ollama (if provider == ollama)
      - Direct API (otherwise, using API keys)
    """

    def __init__(self):
        self._proxy_base = f"http://{settings.PROXY_HOST}:{settings.PROXY_PORT}"
        self._proxy_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.PROXY_API_KEY}",
        }

        if "proxy_stats" not in st.session_state:
            st.session_state.proxy_stats = {
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "errors": 0,
                "last_request_time": None,
            }

    @property
    def stats(self) -> dict:
        return st.session_state.proxy_stats

    def _update_stats(self, input_tokens: int = 0, output_tokens: int = 0, error: bool = False):
        s = st.session_state.proxy_stats
        s["total_requests"] += 1
        s["last_request_time"] = datetime.now().strftime("%H:%M:%S")
        if error:
            s["errors"] += 1
        else:
            s["total_input_tokens"] += input_tokens
            s["total_output_tokens"] += output_tokens

    # ─── SSH output filter (uses local regex if proxy not available) ──

    def filter_ssh_output(self, text: str) -> str:
        if not text:
            return text

        # Try proxy first (it may have additional patterns)
        if settings.PROXY_ENABLED and settings.PROXY_API_KEY:
            try:
                resp = httpx.post(
                    f"{self._proxy_base}/v1/filter",
                    json={"filter_type": "ssh_output", "text": text},
                    headers=self._proxy_headers,
                    timeout=10.0,
                )
                resp.raise_for_status()
                return resp.json().get("text", text)
            except Exception as e:
                logger.debug(f"Proxy filter unavailable, using local: {e}")

        # Fallback to local data_filter
        from proxy.data_filter import sanitize_ssh_output
        filtered, _ = sanitize_ssh_output(text)
        return filtered

    # ─── Routing logic ─────────────────────────────────────────────

    def _current_provider(self) -> str:
        return st.session_state.get("ai_provider", settings.DEFAULT_PROVIDER)

    def _current_model(self) -> str:
        return st.session_state.get("ai_model", settings.DEFAULT_MODEL)

    def _backend(self) -> str:
        """Choose backend: 'proxy' | 'ollama' | 'anthropic' | 'openai' | 'gemini'."""
        provider = self._current_provider()

        # Ollama is always local
        if provider == "ollama":
            return "ollama"

        # Use proxy if enabled
        if settings.PROXY_ENABLED and settings.PROXY_API_KEY:
            return "proxy"

        # Direct API based on provider
        return provider

    # ─── Public API ─────────────────────────────────────────────────

    def chat(self, messages: list, tools: list = None, system: str = None):
        """Main chat entry point — routes to the right backend."""
        if system is None:
            system = settings.SYSTEM_PROMPT

        filtered_messages, _ = sanitize_messages(messages)
        backend = self._backend()
        model = self._current_model()

        logger.info(f"AI request via {backend} | model={model} | msgs={len(filtered_messages)} | tools={len(tools or [])}")
        start = time.time()

        try:
            if backend == "proxy":
                response = self._chat_via_proxy(filtered_messages, tools, system, model)
            elif backend == "ollama":
                response = self._chat_via_ollama(filtered_messages, tools, system, model)
            elif backend == "anthropic":
                response = self._chat_via_anthropic(filtered_messages, tools, system, model)
            elif backend == "openai":
                response = self._chat_via_openai(filtered_messages, tools, system, model)
            elif backend == "gemini":
                response = self._chat_via_gemini(filtered_messages, tools, system, model)
            else:
                raise ValueError(f"Unsupported backend: {backend}")

            duration = time.time() - start
            logger.info(
                f"AI response | backend={backend} | stop={response.stop_reason} | "
                f"in={response.usage.input_tokens} | out={response.usage.output_tokens} | "
                f"dur={duration:.2f}s"
            )
            audit_log(
                AuditEvent.AI_RESPONSE,
                detail=f"{backend}/{model} | in={response.usage.input_tokens} out={response.usage.output_tokens} dur={duration:.1f}s",
            )
            self._update_stats(response.usage.input_tokens, response.usage.output_tokens)
            return response
        except Exception as e:
            self._update_stats(error=True)
            logger.error(f"AI request failed via {backend}: {type(e).__name__}: {e}")
            raise

    # ─── Backend: LLM Sentinel proxy ───────────────────────────────

    def _chat_via_proxy(self, messages, tools, system, model):
        if not settings.PROXY_API_KEY:
            raise ValueError("PROXY_API_KEY is not configured")

        payload: dict = {"messages": messages}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools
        if model:
            payload["model"] = model

        resp = httpx.post(
            f"{self._proxy_base}/v1/chat",
            json=payload,
            headers=self._proxy_headers,
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()

        u = data.get("usage", {})
        return _make_response(
            data.get("content", []),
            data.get("stop_reason", "end_turn"),
            data.get("model", model),
            u.get("input_tokens", 0),
            u.get("output_tokens", 0),
        )

    # ─── Backend: Direct Anthropic ─────────────────────────────────

    def _chat_via_anthropic(self, messages, tools, system, model):
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is not configured")

        headers = {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload = {
            "model": model or "claude-sonnet-4-5",
            "messages": [m for m in messages if m["role"] != "system"],
            "max_tokens": 4096,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            json=payload, headers=headers, timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()

        u = data.get("usage", {})
        return _make_response(
            data.get("content", []),
            data.get("stop_reason", "end_turn"),
            data.get("model", model),
            u.get("input_tokens", 0),
            u.get("output_tokens", 0),
        )

    # ─── Backend: Direct OpenAI ────────────────────────────────────

    def _chat_via_openai(self, messages, tools, system, model):
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not configured")

        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        # Convert to OpenAI format
        openai_messages = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        for m in messages:
            if isinstance(m["content"], str):
                openai_messages.append({"role": m["role"], "content": m["content"]})
            else:
                # tool_result / tool_use blocks
                for block in m["content"]:
                    if block.get("type") == "tool_result":
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": str(block.get("content", "")),
                        })

        payload = {
            "model": model or "gpt-4o",
            "messages": openai_messages,
            "max_tokens": 4096,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                }}
                for t in tools
            ]

        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            json=payload, headers=headers, timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()

        # Convert OpenAI response to Anthropic-style content blocks
        choice = data["choices"][0]
        msg = choice["message"]
        content_blocks = []
        if msg.get("content"):
            content_blocks.append({"type": "text", "text": msg["content"]})
        for tc in msg.get("tool_calls") or []:
            content_blocks.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["function"]["name"],
                "input": json.loads(tc["function"].get("arguments", "{}")),
            })

        stop_reason = "tool_use" if msg.get("tool_calls") else (
            "end_turn" if choice.get("finish_reason") == "stop" else choice.get("finish_reason", "end_turn")
        )

        u = data.get("usage", {})
        return _make_response(
            content_blocks,
            stop_reason,
            data.get("model", model),
            u.get("prompt_tokens", 0),
            u.get("completion_tokens", 0),
        )

    # ─── Backend: Direct Gemini ────────────────────────────────────

    def _chat_via_gemini(self, messages, tools, system, model):
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not configured")

        # Convert messages to Gemini format
        contents = []
        for m in messages:
            role = "user" if m["role"] == "user" else "model"
            if isinstance(m["content"], str):
                contents.append({"role": role, "parts": [{"text": m["content"]}]})
            else:
                # tool results
                parts = []
                for block in m["content"]:
                    if block.get("type") == "tool_result":
                        parts.append({"text": f"[tool_result] {block.get('content', '')}"})
                    elif block.get("type") == "text":
                        parts.append({"text": block.get("text", "")})
                if parts:
                    contents.append({"role": role, "parts": parts})

        payload = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        m = model or "gemini-2.0-flash"
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{m}:generateContent?key={settings.GEMINI_API_KEY}"
        )

        resp = httpx.post(url, json=payload, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()

        # Convert response
        content_blocks = []
        if data.get("candidates"):
            for part in data["candidates"][0].get("content", {}).get("parts", []):
                if "text" in part:
                    content_blocks.append({"type": "text", "text": part["text"]})

        u = data.get("usageMetadata", {})
        return _make_response(
            content_blocks,
            "end_turn",
            m,
            u.get("promptTokenCount", 0),
            u.get("candidatesTokenCount", 0),
        )

    # ─── Backend: On-prem Ollama ───────────────────────────────────

    def _chat_via_ollama(self, messages, tools, system, model):
        base = settings.OLLAMA_URL.rstrip("/")
        # Convert messages to Ollama format
        ollama_messages = []
        if system:
            ollama_messages.append({"role": "system", "content": system})
        for m in messages:
            if isinstance(m["content"], str):
                ollama_messages.append({"role": m["role"], "content": m["content"]})
            else:
                # Flatten tool blocks for Ollama (it doesn't fully support them natively)
                text_parts = []
                for block in m["content"]:
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        text_parts.append(f"[Tool output]: {block.get('content', '')}")
                if text_parts:
                    ollama_messages.append({"role": m["role"], "content": "\n".join(text_parts)})

        payload = {
            "model": model or settings.OLLAMA_MODEL,
            "messages": ollama_messages,
            "stream": False,
        }
        if tools:
            # Ollama supports OpenAI-style function calling on some models
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                }}
                for t in tools
            ]

        resp = httpx.post(
            f"{base}/api/chat",
            json=payload,
            timeout=300.0,  # Local models can be slow
        )
        resp.raise_for_status()
        data = resp.json()

        msg = data.get("message", {})
        content_blocks = []
        if msg.get("content"):
            content_blocks.append({"type": "text", "text": msg["content"]})
        for tc in msg.get("tool_calls") or []:
            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("function", {}).get("name", "tool_0"),
                "name": tc["function"]["name"],
                "input": tc["function"].get("arguments", {}),
            })

        return _make_response(
            content_blocks,
            "tool_use" if msg.get("tool_calls") else "end_turn",
            data.get("model", model),
            data.get("prompt_eval_count", 0),
            data.get("eval_count", 0),
        )
