"""
Unit tests for core/monitor.py — multi-backend metric monitoring.

Tests the pure helpers (value extraction, comparison, history) without
needing live SSH / MCP / HTTP targets.
"""

import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Use a throwaway encryption key for any vault touches
from cryptography.fernet import Fernet
os.environ.setdefault("DEVICE_ENCRYPTION_KEY", Fernet.generate_key().decode())


# ─── _extract_value ──────────────────────────────────────────────────

def test_extract_no_extractor_numeric():
    from core.monitor import _extract_value
    assert _extract_value("42.5") == 42.5


def test_extract_no_extractor_nonnumeric_returns_str():
    from core.monitor import _extract_value
    assert _extract_value("hello\n") == "hello"


def test_extract_regex_first_group():
    from core.monitor import _extract_value
    assert _extract_value("usage is 73% now", "regex:(\\d+)%") == 73.0


def test_extract_regex_no_match():
    from core.monitor import _extract_value
    assert _extract_value("no numbers", "regex:(\\d+)%") is None


def test_extract_json_path():
    from core.monitor import _extract_value
    raw = json.dumps({"data": {"cpu": {"usage": 88.4}}})
    assert _extract_value(raw, "json:data.cpu.usage") == 88.4


def test_extract_json_missing_key():
    from core.monitor import _extract_value
    raw = json.dumps({"data": {}})
    assert _extract_value(raw, "json:data.cpu.usage") is None


def test_extract_empty_input():
    from core.monitor import _extract_value
    assert _extract_value("") is None
    assert _extract_value(None) is None


# ─── _compare ────────────────────────────────────────────────────────

def test_compare_gt_true():
    from core.monitor import _compare
    assert _compare(90, 85, "gt") is True


def test_compare_gt_false():
    from core.monitor import _compare
    assert _compare(80, 85, "gt") is False


def test_compare_lte():
    from core.monitor import _compare
    assert _compare(5, 5, "lte") is True
    assert _compare(6, 5, "lte") is False


def test_compare_eq_string():
    from core.monitor import _compare
    assert _compare("DOWN", "DOWN", "eq") is True
    assert _compare("UP", "DOWN", "eq") is False


def test_compare_contains():
    from core.monitor import _compare
    assert _compare("system is degraded", "degraded", "contains") is True
    assert _compare("system is healthy", "degraded", "contains") is False


def test_compare_not_contains():
    from core.monitor import _compare
    assert _compare("ok", "error", "not_contains") is True


def test_compare_regex():
    from core.monitor import _compare
    assert _compare("ERR-503", "^ERR-\\d+$", "regex") is True
    assert _compare("ok", "^ERR-\\d+$", "regex") is False


def test_compare_none_value():
    from core.monitor import _compare
    assert _compare(None, 5, "gt") is False


def test_compare_unknown_op():
    from core.monitor import _compare
    assert _compare(5, 5, "bogus_op") is False


# ─── History ─────────────────────────────────────────────────────────

def _isolate_history():
    """Point history at a temp file."""
    from core import monitor
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name
    monitor._HISTORY_FILE = Path(tmp)
    return Path(tmp)


def test_history_append_and_read():
    from core.monitor import append_history, get_history
    _isolate_history()
    append_history("srv01", "cpu", 42.0, "ok", "2026-01-01T00:00:00")
    append_history("srv01", "cpu", 91.0, "critical", "2026-01-01T00:05:00")
    series = get_history("srv01", "cpu")
    assert len(series) == 2
    assert series[0]["v"] == 42.0
    assert series[1]["s"] == "critical"


def test_history_caps_at_max():
    from core import monitor
    from core.monitor import append_history, get_history
    _isolate_history()
    # Override max for speed
    original_max = monitor._HISTORY_MAX
    monitor._HISTORY_MAX = 5
    try:
        for i in range(10):
            append_history("srv01", "cpu", float(i), "ok", f"2026-01-01T00:0{i}:00")
        series = get_history("srv01", "cpu")
        assert len(series) == 5
        # Should be the last 5 samples
        assert series[0]["v"] == 5.0
        assert series[-1]["v"] == 9.0
    finally:
        monitor._HISTORY_MAX = original_max


def test_history_missing_returns_empty():
    from core.monitor import get_history
    _isolate_history()
    assert get_history("nope", "nope") == []


# ─── Config add / update ─────────────────────────────────────────────

def _isolate_state():
    """Redirect monitor state to a fresh temp file."""
    from core import monitor
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name
    monitor._STATE_FILE = Path(tmp)
    return Path(tmp)


def test_add_custom_mcp_check():
    _isolate_state()
    from core.monitor import (
        add_custom_check, get_checks_config, BACKEND_MCP,
    )
    add_custom_check(
        name="es_cluster_health",
        label="ES cluster health",
        threshold="green",
        backend=BACKEND_MCP,
        device_type="",
        mcp_tool="linux_ops",
        mcp_action="curl -s localhost:9200/_cluster/health | jq -r .status",
        value_extractor="",
        compare="eq",
        severity="critical",
        interval_minutes=5,
    )
    cfg = get_checks_config()["es_cluster_health"]
    assert cfg["backend"] == BACKEND_MCP
    assert cfg["mcp_tool"] == "linux_ops"
    assert cfg["compare"] == "eq"


def test_update_check_partial():
    _isolate_state()
    from core.monitor import (
        add_custom_check, update_check_config, get_checks_config, BACKEND_HTTP,
    )
    add_custom_check(
        name="api_latency", label="API latency", threshold=500,
        backend=BACKEND_HTTP, http_url="https://example.com",
        unit="ms",
    )
    # Update only some fields
    update_check_config("api_latency", threshold=750, http_json_path="data.latency")
    cfg = get_checks_config()["api_latency"]
    assert cfg["threshold"] == 750
    assert cfg["http_json_path"] == "data.latency"
    assert cfg["http_url"] == "https://example.com"  # untouched


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
