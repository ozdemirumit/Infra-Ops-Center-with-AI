"""
Tests for core/action_plan.py — the text-based remediation plan generator.

We monkeypatch AIProxy.chat so no real LLM call is made. The mocked
response shape mirrors what AIProxy returns: an object with a
`.content` list of blocks each having `.type` and `.text`.
"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet
os.environ.setdefault("DEVICE_ENCRYPTION_KEY", Fernet.generate_key().decode())


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text, model="claude-test"):
        self.content = [_FakeBlock(text)]
        self.model = model


def _patch_proxy(monkeypatch, response_text: str, capture: list | None = None):
    """Replace AIProxy.chat with a fake returning `response_text`."""
    from proxy import ai_proxy

    class _FakeProxy:
        def __init__(self, *a, **kw):
            pass
        def chat(self, messages, tools, system):
            if capture is not None:
                capture.append({"messages": messages, "system": system,
                                "tools": tools})
            return _FakeResponse(response_text)

    monkeypatch.setattr(ai_proxy, "AIProxy", _FakeProxy)


def _patch_registry(monkeypatch, tools):
    import tools.registry as reg
    monkeypatch.setattr(reg, "get_active_tools", lambda: tools)


# ─── generation ────────────────────────────────────────────────────

def test_generate_returns_action_plan_with_markdown(monkeypatch):
    _patch_registry(monkeypatch, [
        {"name": "linux_ops", "description": "Bash on Linux servers"},
    ])
    _patch_proxy(monkeypatch, """\
# Plan: free up disk on srv01

**Risk:** medium

## Context
Disk is full because of journald logs.

## Steps

### Step 1 — Check disk usage

**What this does:** measures fill rate.

**Run via MCP:** `linux_ops`

**Command(s):**
```bash
df -h
```

**Expected output / success criteria:** free space increases.

**If it fails:** investigate inode exhaustion.
""")
    from core.action_plan import generate_action_plan
    plan = generate_action_plan("disk is full on srv01")
    assert plan.problem == "disk is full on srv01"
    assert "free up disk" in plan.plan_markdown.lower()
    assert "linux_ops" in plan.plan_markdown
    assert plan.risk == "medium"  # detected from the Risk header
    assert plan.available_mcps == ["linux_ops"]


def test_risk_falls_back_to_estimation_when_header_missing(monkeypatch):
    _patch_registry(monkeypatch, [])
    _patch_proxy(monkeypatch, "Run `rm -rf /tmp/old_logs` to free space.")
    from core.action_plan import generate_action_plan
    plan = generate_action_plan("free space")
    # No Risk header → fall back to keyword scan → "rm -rf" → high
    assert plan.risk == "high"


def test_low_risk_detected_for_readonly_plan(monkeypatch):
    _patch_registry(monkeypatch, [])
    _patch_proxy(monkeypatch, "Run `df -h` and `du -sh /var` to investigate.")
    from core.action_plan import generate_action_plan
    plan = generate_action_plan("disk fill investigation")
    assert plan.risk == "low"


def test_target_info_passed_in_user_message(monkeypatch):
    _patch_registry(monkeypatch, [])
    captured = []
    _patch_proxy(monkeypatch, "# Plan", capture=captured)
    from core.action_plan import generate_action_plan
    generate_action_plan(
        "memory pressure",
        target={"name": "srv-db01", "ip": "10.0.5.21", "device_type": "linux"},
        extra_context="No maintenance window before 22:00.",
    )
    user_msg = captured[0]["messages"][0]["content"]
    assert "srv-db01" in user_msg
    assert "10.0.5.21" in user_msg
    assert "maintenance window" in user_msg
    assert "memory pressure" in user_msg


def test_system_prompt_lists_only_active_mcps(monkeypatch):
    _patch_registry(monkeypatch, [
        {"name": "linux_ops", "description": "linux"},
        {"name": "router_ops", "description": "router"},
    ])
    captured = []
    _patch_proxy(monkeypatch, "# Plan", capture=captured)
    from core.action_plan import generate_action_plan
    generate_action_plan("test")
    system = captured[0]["system"]
    assert "`linux_ops`" in system
    assert "`router_ops`" in system
    assert "DO NOT invent MCP names" in system


def test_no_mcps_renders_explicit_note_in_system(monkeypatch):
    _patch_registry(monkeypatch, [])
    captured = []
    _patch_proxy(monkeypatch, "# Plan", capture=captured)
    from core.action_plan import generate_action_plan
    generate_action_plan("no tools available")
    assert "none currently registered" in captured[0]["system"]


def test_llm_failure_returns_degraded_plan_not_exception(monkeypatch):
    _patch_registry(monkeypatch, [])
    from proxy import ai_proxy

    class _BrokenProxy:
        def __init__(self): pass
        def chat(self, messages, tools, system):
            raise RuntimeError("provider unreachable")

    monkeypatch.setattr(ai_proxy, "AIProxy", _BrokenProxy)

    from core.action_plan import generate_action_plan
    plan = generate_action_plan("anything")
    # Should not raise — returns a plan with the error embedded
    assert "Plan generation failed" in plan.plan_markdown
    assert "provider unreachable" in plan.plan_markdown


def test_no_tool_calls_are_passed_to_proxy(monkeypatch):
    """generator must not pass any tools to the LLM — it's text-only."""
    _patch_registry(monkeypatch, [{"name": "x", "description": "y"}])
    captured = []
    _patch_proxy(monkeypatch, "# Plan", capture=captured)
    from core.action_plan import generate_action_plan
    generate_action_plan("anything")
    assert captured[0]["tools"] == []


# ─── runbook saving ────────────────────────────────────────────────

def test_save_as_runbook_writes_markdown(monkeypatch, tmp_path):
    _patch_registry(monkeypatch, [{"name": "linux_ops", "description": "x"}])
    _patch_proxy(monkeypatch, "# Plan: cleanup\n\n**Risk:** low\n\n## Steps")

    # Redirect knowledge base to tmp
    from config.settings import settings
    monkeypatch.setattr(settings, "KNOWLEDGE_BASE_DIR", str(tmp_path),
                        raising=False)
    # Silence RAG indexing (returns immediately)
    import core.rag_engine as rag
    class _Stub:
        def index_document(self, **kw):
            return {"status": "ok"}
    monkeypatch.setattr(rag, "RAGEngine", lambda: _Stub())

    from core.action_plan import generate_action_plan, save_plan_as_runbook
    plan = generate_action_plan("cleanup logs on srv01")
    path = save_plan_as_runbook(plan)
    assert path is not None
    p = Path(path)
    assert p.exists()
    body = p.read_text(encoding="utf-8")
    assert "Plan: cleanup" in body
    assert "Available MCPs at generation time" in body
    assert "linux_ops" in body


def test_save_as_runbook_safe_filename(monkeypatch, tmp_path):
    _patch_registry(monkeypatch, [])
    _patch_proxy(monkeypatch, "# Plan")
    from config.settings import settings
    monkeypatch.setattr(settings, "KNOWLEDGE_BASE_DIR", str(tmp_path),
                        raising=False)
    import core.rag_engine as rag
    monkeypatch.setattr(rag, "RAGEngine",
                        lambda: type("S", (), {"index_document":
                                                 lambda self, **kw: {}})())

    from core.action_plan import generate_action_plan, save_plan_as_runbook
    plan = generate_action_plan(
        "fix bug with /etc/passwd and shell command `rm -rf /tmp/*`!"
    )
    path = save_plan_as_runbook(plan)
    assert path is not None
    name = Path(path).name
    # No slashes, asterisks, or backticks
    for bad in ("/", "*", "`", "\\", "?"):
        assert bad not in name


def test_plan_to_dict_roundtrip(monkeypatch):
    _patch_registry(monkeypatch, [])
    _patch_proxy(monkeypatch, "# Plan\n\n**Risk:** high\n\n## Steps")
    from core.action_plan import generate_action_plan
    plan = generate_action_plan("test")
    d = plan.to_dict()
    assert d["problem"] == "test"
    assert d["risk"] == "high"
    assert "plan_markdown" in d
    assert d["model"] == "claude-test"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
