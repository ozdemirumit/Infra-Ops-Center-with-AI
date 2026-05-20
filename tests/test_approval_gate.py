"""
Tests for core/approval_gate.py and dispatcher integration.
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet
os.environ.setdefault("DEVICE_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _reset():
    from core import approval_gate
    approval_gate.clear_all()


# ─── is_destructive ────────────────────────────────────────────────

def test_read_only_linux_commands_are_safe():
    from core.approval_gate import is_destructive
    safe = [
        {"command": "df -h"},
        {"command": "uptime"},
        {"command": "ls /var"},
        {"command": "ps aux | head -20"},
        {"command": "cat /etc/os-release"},
        {"command": "netstat -tnp"},
    ]
    for inp in safe:
        assert is_destructive("linux_ops", inp) is False, inp


def test_destructive_linux_commands_are_caught():
    from core.approval_gate import is_destructive
    bad = [
        {"command": "rm -rf /tmp/old"},
        {"command": "shutdown -h now"},
        {"command": "systemctl restart nginx"},
        {"command": "apt-get install nginx -y"},
        {"command": "useradd alice"},
        {"command": "kill -9 12345"},
        {"command": "iptables -A INPUT -j DROP"},
    ]
    for inp in bad:
        assert is_destructive("linux_ops", inp) is True, inp


def test_destructive_natural_language_actions():
    from core.approval_gate import is_destructive
    assert is_destructive("commvault_ops", {"action": "delete subclient ID:5"})
    assert is_destructive("commvault_ops", {"action": "create plan 'X'"})
    assert is_destructive("commvault_ops", {"action": "start backup ID:5 full"})
    # Turkish keywords
    assert is_destructive("commvault_ops", {"action": "client sil ID:42"})


def test_search_api_and_list_actions_are_safe():
    from core.approval_gate import is_destructive
    assert not is_destructive("commvault_ops",
                              {"action": "search_api", "query": "restore"})
    assert not is_destructive("commvault_ops", {"action": "list_actions"})
    assert not is_destructive("any_mcp", {"action": "list"})
    assert not is_destructive("any_mcp", {"action": "status"})


def test_raw_get_is_safe_raw_post_is_destructive():
    from core.approval_gate import is_destructive
    assert not is_destructive("commvault_ops", {
        "action": "raw", "method": "GET", "path": "/Job"
    })
    for m in ("POST", "PUT", "DELETE", "PATCH"):
        assert is_destructive("commvault_ops", {
            "action": "raw", "method": m, "path": "/Job/1"
        }), m


def test_unknown_command_is_treated_destructive():
    """Conservatively block weird invented commands."""
    from core.approval_gate import is_destructive
    assert is_destructive("linux_ops",
                          {"command": "abracadabra --force /everything"})


def test_workflow_ops_run_is_safe_decide_is_destructive():
    from core.approval_gate import is_destructive
    # Starting a workflow doesn't itself change a system — the workflow's
    # own steps will be gated.
    assert not is_destructive("workflow_ops", {"action": "run",
                                                "workflow": "x"})
    # But approve / reject act on the system queue.
    assert is_destructive("workflow_ops", {"action": "approve",
                                            "run_id": "x"})
    assert is_destructive("workflow_ops", {"action": "cancel",
                                            "run_id": "x"})


# ─── request_approval / decide ─────────────────────────────────────

def test_request_approval_returns_none_for_safe_calls():
    _reset()
    from core.approval_gate import request_approval
    assert request_approval("linux_ops", {"command": "df -h"},
                            interactive=True) is None


def test_request_approval_queues_destructive_calls():
    _reset()
    from core.approval_gate import request_approval, list_pending
    entry = request_approval(
        "linux_ops", {"command": "shutdown -h now"},
        target="srv01", interactive=True,
    )
    assert entry is not None
    assert entry["status"] == "waiting"
    assert entry["tool_name"] == "linux_ops"
    assert entry["target"] == "srv01"
    pending = list_pending()
    assert len(pending) == 1
    assert pending[0]["approval_id"] == entry["approval_id"]


def test_headless_request_returns_none():
    _reset()
    from core.approval_gate import request_approval
    # interactive=False — workflow / scheduled / incident path
    assert request_approval("linux_ops", {"command": "rm -rf /tmp"},
                            interactive=False) is None


def test_decide_approved_consumed_on_next_request():
    _reset()
    from core.approval_gate import request_approval, decide
    e1 = request_approval("linux_ops", {"command": "systemctl restart nginx"},
                          target="srv01", interactive=True)
    decide(e1["approval_id"], approved=True, note="ok by ops")
    # Same fingerprint → should now return "approved" once, then nothing
    e2 = request_approval("linux_ops", {"command": "systemctl restart nginx"},
                          target="srv01", interactive=True)
    assert e2 is not None
    assert e2["status"] == "approved"
    assert e2["note"] == "ok by ops"
    # And the next ask after consumption queues fresh
    e3 = request_approval("linux_ops", {"command": "systemctl restart nginx"},
                          target="srv01", interactive=True)
    assert e3["status"] == "waiting"
    assert e3["approval_id"] != e1["approval_id"]


def test_decide_rejected_consumed_on_next_request():
    _reset()
    from core.approval_gate import request_approval, decide
    e1 = request_approval("linux_ops", {"command": "rm -rf /tmp/old"},
                          interactive=True)
    decide(e1["approval_id"], approved=False, note="no")
    e2 = request_approval("linux_ops", {"command": "rm -rf /tmp/old"},
                          interactive=True)
    assert e2["status"] == "rejected"


# ─── Dispatcher integration ────────────────────────────────────────

def test_dispatcher_blocks_destructive_call_without_approval(monkeypatch):
    _reset()
    # Force interactive
    from core import approval_gate as ag
    monkeypatch.setattr(ag, "_running_in_streamlit", lambda: True)
    # Make sure the real dispatcher would have errored if reached
    import core.agent_loop as al
    called = []
    real_resolve = al._resolve_target_servers
    def boom(*a, **kw):
        called.append(a)
        return [], None
    monkeypatch.setattr(al, "_resolve_target_servers", boom)

    out = al._dispatch_tool(
        "linux_ops",
        {"command": "shutdown -h now", "target_host": "srv01"},
        {},
    )
    assert "PENDING APPROVAL" in out
    # The dispatcher must NOT have reached the SSH path
    assert called == []


def test_dispatcher_allows_safe_call(monkeypatch):
    _reset()
    from core import approval_gate as ag
    monkeypatch.setattr(ag, "_running_in_streamlit", lambda: True)

    import core.agent_loop as al
    # Stub the SSH bottom-half so we don't actually try to connect.
    monkeypatch.setattr(al, "execute_ssh_command",
                        lambda ip, u, p, c: f"ran {c}")
    # Make device storage return a single fake server
    import devices.storage as ds
    monkeypatch.setattr(ds.DeviceStorage, "get_by_type",
                        classmethod(lambda cls, t: [{
                            "id": "s1", "name": "srv01", "ip": "10.0.0.1",
                            "user": "u", "password": "p",
                        }]))

    out = al._dispatch_tool("linux_ops", {"command": "df -h"}, {})
    # Either the SSH stub ran, or we get the formatted result — either way,
    # no "PENDING APPROVAL" placeholder.
    assert "PENDING APPROVAL" not in out
    assert "ran df -h" in out


def test_dispatcher_passes_after_approval(monkeypatch):
    _reset()
    from core import approval_gate as ag
    monkeypatch.setattr(ag, "_running_in_streamlit", lambda: True)

    import core.agent_loop as al
    monkeypatch.setattr(al, "execute_ssh_command",
                        lambda ip, u, p, c: f"ran {c}")
    import devices.storage as ds
    monkeypatch.setattr(ds.DeviceStorage, "get_by_type",
                        classmethod(lambda cls, t: [{
                            "id": "s1", "name": "srv01", "ip": "10.0.0.1",
                            "user": "u", "password": "p",
                        }]))

    # First call → blocked
    out1 = al._dispatch_tool(
        "linux_ops",
        {"command": "systemctl restart nginx", "target_host": "srv01"},
        {},
    )
    assert "PENDING APPROVAL" in out1

    # Approve the queued entry
    pending = ag.list_pending()
    assert len(pending) == 1
    ag.decide(pending[0]["approval_id"], True, "go")

    # Same call retried → must execute now
    out2 = al._dispatch_tool(
        "linux_ops",
        {"command": "systemctl restart nginx", "target_host": "srv01"},
        {},
    )
    assert "PENDING APPROVAL" not in out2
    assert "ran systemctl restart nginx" in out2


def test_approval_bypass_skips_gate(monkeypatch):
    _reset()
    from core import approval_gate as ag
    monkeypatch.setattr(ag, "_running_in_streamlit", lambda: True)

    import core.agent_loop as al
    monkeypatch.setattr(al, "execute_ssh_command",
                        lambda ip, u, p, c: f"ran {c}")
    import devices.storage as ds
    monkeypatch.setattr(ds.DeviceStorage, "get_by_type",
                        classmethod(lambda cls, t: [{
                            "id": "s1", "name": "srv01", "ip": "10.0.0.1",
                            "user": "u", "password": "p",
                        }]))

    out = al._dispatch_tool(
        "linux_ops",
        {"command": "shutdown -h now", "target_host": "srv01",
         "_approval_bypass": True},
        {},
    )
    assert "PENDING APPROVAL" not in out
    assert "ran shutdown -h now" in out


def test_search_api_bypasses_gate(monkeypatch, tmp_path):
    """search_api should never trigger an approval popup."""
    _reset()
    from core import approval_gate as ag
    monkeypatch.setattr(ag, "_running_in_streamlit", lambda: True)

    # Make sure the dispatcher's search_api branch runs and returns its own
    # "no docs" message, without queuing an approval.
    from core.agent_loop import _dispatch_tool
    out = _dispatch_tool("linux_ops",
                         {"action": "search_api", "query": "anything"}, {})
    assert "PENDING APPROVAL" not in out
    assert ag.list_pending() == []
