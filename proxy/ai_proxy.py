"""
AI Proxy module — Communication layer with Enterprise AI Proxy.
All AI calls are made through the enterprise proxy.
No direct LLM API access; provider and model selection is managed by the proxy.
"""

import time
from datetime import datetime
from types import SimpleNamespace

import httpx
import streamlit as st
from config.settings import settings
from proxy.data_filter import sanitize_messages
from logging_config.logger import get_logger, audit_log, AuditEvent

logger = get_logger("proxy")


class AIProxy:
    """
    Enterprise AI Proxy client.
    All requests are routed to the enterprise proxy with a Bearer token.
    """

    def __init__(self):
        if not settings.PROXY_API_KEY:
            raise ValueError(
                "PROXY_API_KEY is not configured! "
                "Add PROXY_API_KEY to ai-ops-center/.env file."
            )
        self._base = f"http://{settings.PROXY_HOST}:{settings.PROXY_PORT}"
        self._hdrs = {
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

    def filter_ssh_output(self, text: str) -> str:
        """Filters SSH output through the enterprise proxy."""
        if not text:
            return text
        resp = httpx.post(
            f"{self._base}/v1/filter",
            json={"filter_type": "ssh_output", "text": text},
            headers=self._hdrs,
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json().get("text", text)

    def chat(self, messages: list, tools: list = None, system: str = None):
        """
        Makes an AI call through the enterprise proxy.
        Provider and model selection is managed by the proxy.
        Throws exception directly on error, no fallback.
        """
        if system is None:
            system = settings.SYSTEM_PROMPT

        filtered_messages, _ = sanitize_messages(messages)

        payload: dict = {"messages": filtered_messages}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools

        logger.info(
            f"REQUEST | proxy={self._base} | "
            f"messages={len(filtered_messages)} | tools={len(tools or [])}"
        )
        start = time.time()

        resp = httpx.post(
            f"{self._base}/v1/chat",
            json=payload,
            headers=self._hdrs,
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()

        blocks = []
        for b in data.get("content", []):
            blocks.append(SimpleNamespace(
                type=b.get("type"),
                text=b.get("text"),
                id=b.get("id"),
                name=b.get("name"),
                input=b.get("input"),
            ))
        u = data.get("usage", {})
        response = SimpleNamespace(
            content=blocks,
            stop_reason=data.get("stop_reason", "end_turn"),
            model=data.get("model", ""),
            usage=SimpleNamespace(
                input_tokens=u.get("input_tokens", 0),
                output_tokens=u.get("output_tokens", 0),
            ),
        )

        duration = time.time() - start
        logger.info(
            f"RESPONSE | stop={response.stop_reason} | "
            f"in={response.usage.input_tokens} | out={response.usage.output_tokens} | "
            f"dur={duration:.2f}s"
        )
        audit_log(
            AuditEvent.AI_RESPONSE,
            detail=(
                f"stop={response.stop_reason} "
                f"in={response.usage.input_tokens} "
                f"out={response.usage.output_tokens} "
                f"dur={duration:.1f}s"
            ),
        )
        self._update_stats(response.usage.input_tokens, response.usage.output_tokens)
        return response
