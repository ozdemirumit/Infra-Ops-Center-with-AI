"""
Tests for core/mcp_docs.py + RAG mcp filter + dispatcher search_api routing.
"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet
os.environ.setdefault("DEVICE_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _isolate_rag(monkeypatch, tmp_path):
    """Point the RAG engine at a clean knowledge base dir."""
    from config.settings import settings
    kb = tmp_path / "kb"
    kb.mkdir()
    monkeypatch.setattr(settings, "KNOWLEDGE_BASE_DIR", str(kb), raising=False)
    # Reset singleton
    import core.rag_engine as rag
    rag._rag_singleton = None
    return kb


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ─── RAG filter by mcp/source ───────────────────────────────────────

def test_rag_filter_by_mcp(monkeypatch, tmp_path):
    kb = _isolate_rag(monkeypatch, tmp_path)
    from core.mcp_docs import index_doc_for_mcp, search_mcp_docs

    cv_doc = _write(kb / "cv_api.txt",
                    "POST /Job/123/action/kill — kill a running backup job.\n"
                    "GET /Job — list jobs.\n"
                    "POST /Plan/45 — update plan settings.\n")
    sw_doc = _write(kb / "switch_api.txt",
                    "configure terminal\nvlan 10\n"
                    "interface gi0/1\nswitchport mode access\n"
                    "switchport access vlan 10\n")

    r1 = index_doc_for_mcp(str(cv_doc), "commvault_ops")
    r2 = index_doc_for_mcp(str(sw_doc), "switch_ops")
    assert r1["status"] == "ok"
    assert r2["status"] == "ok"

    # Searching commvault — must NOT return switch content
    matches = search_mcp_docs("commvault_ops", "kill a backup job")
    assert matches, "expected at least one match"
    assert all("vlan" not in m["text"].lower() for m in matches)
    assert any("/Job" in m["text"] for m in matches)

    # Reverse — switch search must not return commvault
    matches = search_mcp_docs("switch_ops", "configure vlan on port")
    assert matches
    assert all("/Job" not in m["text"] for m in matches)


def test_rag_filter_empty_when_no_docs_for_mcp(monkeypatch, tmp_path):
    _isolate_rag(monkeypatch, tmp_path)
    from core.mcp_docs import search_mcp_docs
    assert search_mcp_docs("never_indexed", "anything") == []


def test_list_indexed_mcps(monkeypatch, tmp_path):
    kb = _isolate_rag(monkeypatch, tmp_path)
    from core.mcp_docs import index_doc_for_mcp, list_indexed_mcps

    index_doc_for_mcp(str(_write(kb / "a.txt", "hello cv " * 50)),
                      "commvault_ops")
    index_doc_for_mcp(str(_write(kb / "b.txt", "hello sw " * 50)),
                      "switch_ops")
    rows = list_indexed_mcps()
    names = {r["mcp"] for r in rows}
    assert names == {"commvault_ops", "switch_ops"}
    for r in rows:
        assert r["chunks"] >= 1


def test_format_search_results_empty():
    from core.mcp_docs import format_search_results
    out = format_search_results([])
    assert "No matching endpoints" in out


def test_format_search_results_truncates():
    from core.mcp_docs import format_search_results
    long_text = "X" * 5000
    out = format_search_results([{
        "text": long_text, "filename": "doc.txt",
        "chunk_index": 0, "score": 0.5,
    }], max_chars_per_chunk=100)
    assert "(truncated)" in out


# ─── handle_search_api_action ───────────────────────────────────────

def test_handle_search_api_requires_query():
    from core.mcp_docs import handle_search_api_action
    out = handle_search_api_action("anything", {})
    assert "query" in out.lower()


def test_handle_search_api_routes_per_mcp(monkeypatch, tmp_path):
    kb = _isolate_rag(monkeypatch, tmp_path)
    from core.mcp_docs import index_doc_for_mcp, handle_search_api_action

    index_doc_for_mcp(
        str(_write(kb / "cv.txt",
                   "Restore endpoint POST /Restore needs taskInfo body.")),
        "commvault_ops",
    )
    out = handle_search_api_action(
        "commvault_ops", {"action": "search_api", "query": "restore endpoint"}
    )
    assert "Restore" in out or "/Restore" in out


# ─── Dispatcher routing ─────────────────────────────────────────────

def test_dispatcher_routes_search_api_for_any_tool(monkeypatch, tmp_path):
    kb = _isolate_rag(monkeypatch, tmp_path)
    from core.mcp_docs import index_doc_for_mcp
    index_doc_for_mcp(
        str(_write(kb / "fut.txt", "POST /futuristic/api/v9 — new endpoint.")),
        "future_mcp",
    )
    # _dispatch_tool should accept search_api on a tool name that was never
    # explicitly wired in agent_loop.
    from core.agent_loop import _dispatch_tool
    out = _dispatch_tool(
        "future_mcp",
        {"action": "search_api", "query": "futuristic endpoint",
         "_approval_bypass": True},
        {},
    )
    assert "futuristic" in out.lower() or "/futuristic" in out
