"""
Tests for tools/commvault_tool.py production hardening:
- Token cache (cached login skips HTTP, 401 invalidates and re-logs)
- Retry on transient 5xx / connection errors
- Friendly HTTP error mapping
- Direct-action fast path (action_str ∈ COMMVAULT_ACTIONS)
- Pagination helper
- list_actions / search_api meta actions
"""

import sys
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet
os.environ.setdefault("DEVICE_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _reset_cache():
    from tools import commvault_tool as cv
    cv._TOKEN_CACHE.clear()


# ─── Token cache ────────────────────────────────────────────────────

def test_token_cache_roundtrip():
    _reset_cache()
    from tools.commvault_tool import _cache_get, _cache_put, _cache_invalidate

    assert _cache_get("h1", "u") is None
    _cache_put("h1", "u", "tok-abc")
    assert _cache_get("h1", "u") == "tok-abc"

    # Different user → separate cache entry
    assert _cache_get("h1", "other") is None

    _cache_invalidate("h1", "u")
    assert _cache_get("h1", "u") is None


def test_token_cache_expires(monkeypatch):
    _reset_cache()
    from tools import commvault_tool as cv

    # Shrink the TTL for the test
    monkeypatch.setattr(cv, "_TOKEN_TTL_SECONDS", 1)
    cv._cache_put("h", "u", "tok")
    assert cv._cache_get("h", "u") == "tok"
    time.sleep(1.05)
    assert cv._cache_get("h", "u") is None


def test_login_uses_cached_token(monkeypatch):
    """A second login() with a cached token must NOT make any HTTP call."""
    _reset_cache()
    from tools.commvault_tool import CommvaultSession, _cache_put

    _cache_put("cv.example", "ops", "cached-token-123")

    sess = CommvaultSession("cv.example", "ops", "pwd")
    calls = []
    monkeypatch.setattr(sess.session, "post",
                        lambda *a, **kw: calls.append(("post", a)) or None)

    assert sess.login() is True
    assert sess._auth_token == "cached-token-123"
    assert sess.session.headers["Authtoken"] == "cached-token-123"
    assert calls == []  # NOT a single HTTP call


# ─── Friendly HTTP error mapping ────────────────────────────────────

def test_friendly_http_error_known_codes():
    from tools.commvault_tool import _friendly_http_error
    assert "expired or credentials invalid" in _friendly_http_error(401, "")
    assert "Permission denied" in _friendly_http_error(403, "")
    assert "not found" in _friendly_http_error(404, "")
    assert "service unavailable" in _friendly_http_error(503, "")


def test_friendly_http_error_unknown_code_falls_through():
    from tools.commvault_tool import _friendly_http_error
    msg = _friendly_http_error(418, "I'm a teapot")
    assert "418" in msg
    assert "teapot" in msg


# ─── Retry behaviour ────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, status, body="{}"):
        self.status_code = status
        self.text = body
    def json(self):
        import json
        return json.loads(self.text)
    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            err = requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err


def _stub_session(monkeypatch, sequence):
    """Replace the session.get with a deterministic sequence of responses."""
    from tools.commvault_tool import CommvaultSession, _cache_put
    _cache_put("cv.example", "ops", "tok")
    sess = CommvaultSession("cv.example", "ops", "pwd")
    sess._logged_in = True

    calls = []
    iter_seq = iter(sequence)

    def fake_get(url, params=None, timeout=None):
        nxt = next(iter_seq)
        calls.append(("GET", url, params))
        return nxt

    monkeypatch.setattr(sess.session, "get", fake_get)
    return sess, calls


def test_retry_on_503_then_success(monkeypatch):
    _reset_cache()
    # Speed up backoff so test stays fast
    from tools import commvault_tool as cv
    monkeypatch.setattr(cv, "_RETRY_BACKOFF_SECONDS", 0.01)

    sess, calls = _stub_session(monkeypatch, [
        _FakeResp(503, "down"),
        _FakeResp(503, "down"),
        _FakeResp(200, '{"ok": true}'),
    ])
    result = sess.get("Job")
    assert result == {"ok": True}
    assert len(calls) == 3


def test_retry_exhausted_returns_friendly_error(monkeypatch):
    _reset_cache()
    from tools import commvault_tool as cv
    monkeypatch.setattr(cv, "_RETRY_BACKOFF_SECONDS", 0.01)

    # 3 attempts (1 + _RETRY_MAX=2), all 503. The final raise_for_status()
    # raises HTTPError which the handler converts to a friendly message.
    sess, calls = _stub_session(monkeypatch, [
        _FakeResp(503, "down"),
        _FakeResp(503, "down"),
        _FakeResp(503, "down"),
    ])
    result = sess.get("Job")
    assert "error" in result
    assert "503" in result["error"]
    assert "service unavailable" in result["error"].lower()
    assert len(calls) == 3


def test_no_retry_for_400(monkeypatch):
    _reset_cache()
    from tools import commvault_tool as cv
    monkeypatch.setattr(cv, "_RETRY_BACKOFF_SECONDS", 0.01)
    sess, calls = _stub_session(monkeypatch, [_FakeResp(400, "bad")])
    result = sess.get("Job")
    assert "400" in result["error"]
    assert len(calls) == 1


def test_401_invalidates_cache_and_relogs(monkeypatch):
    """401 must invalidate the cached token and trigger ONE re-login."""
    _reset_cache()
    from tools.commvault_tool import CommvaultSession, _cache_put, _cache_get
    from tools import commvault_tool as cv
    monkeypatch.setattr(cv, "_RETRY_BACKOFF_SECONDS", 0.01)

    _cache_put("cv.example", "ops", "stale-token")
    sess = CommvaultSession("cv.example", "ops", "pwd")

    # First .get returns 401; after re-login (which we stub) returns 200.
    responses = iter([
        _FakeResp(401, "expired"),
        _FakeResp(200, '{"ok": true}'),
    ])
    monkeypatch.setattr(sess.session, "get",
                        lambda *a, **kw: next(responses))

    relogs = []
    def fake_login_post(url, json=None, timeout=None):
        relogs.append(json)
        # Mimic a real Login 200
        return _FakeResp(200, '{"token": "fresh-token"}')
    monkeypatch.setattr(sess.session, "post", fake_login_post)

    # First call: login() uses cached token, then 401 invalidates and
    # re-logs in via post(/Login).
    result = sess.get("Job")
    assert result == {"ok": True}
    assert len(relogs) == 1
    assert _cache_get("cv.example", "ops") == "fresh-token"


# ─── Direct-action fast path ────────────────────────────────────────

def test_direct_action_skips_keyword_matching(monkeypatch):
    """action='jobs_active' goes straight to /Job?jobCategory=Active."""
    _reset_cache()
    from tools.commvault_tool import execute_commvault_api, _cache_put
    _cache_put("cv.example", "ops", "tok")

    captured = {}
    # Patch the session method that gets called inside _request
    import tools.commvault_tool as cv_mod

    original_init = cv_mod.CommvaultSession.__init__
    def patched_init(self, host, user, pwd):
        original_init(self, host, user, pwd)
        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            return _FakeResp(200, '{"jobs": [{"jobId": 1}]}')
        self.session.get = fake_get
        # short-circuit login() — already cached anyway
    monkeypatch.setattr(cv_mod.CommvaultSession, "__init__", patched_init)

    out = execute_commvault_api("cv.example", "ops", "pwd", "jobs_active")
    assert "jobId" in out
    assert "/Job" in captured["url"]
    assert captured["params"]["jobCategory"] == "Active"


def test_direct_action_with_structured_entity_id(monkeypatch):
    """{action: 'job_detail', entity_id: 42} → /Job/42 — no ID extraction."""
    _reset_cache()
    from tools.commvault_tool import execute_commvault_api, _cache_put
    _cache_put("cv.example", "ops", "tok")

    captured = {}
    import tools.commvault_tool as cv_mod
    original_init = cv_mod.CommvaultSession.__init__

    def patched_init(self, host, user, pwd):
        original_init(self, host, user, pwd)
        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            return _FakeResp(200, '{"jobId": 42}')
        self.session.get = fake_get
    monkeypatch.setattr(cv_mod.CommvaultSession, "__init__", patched_init)

    # Pass action as a dict (structured)
    out = execute_commvault_api("cv.example", "ops", "pwd",
                                {"action": "job_detail", "entity_id": 42})
    assert "jobId" in out
    assert captured["url"].endswith("/Job/42")


def test_direct_action_missing_id_returns_warning():
    _reset_cache()
    from tools.commvault_tool import execute_commvault_api, _cache_put
    _cache_put("cv.example", "ops", "tok")
    # Calling job_detail without ID
    out = execute_commvault_api("cv.example", "ops", "pwd", "job_detail")
    assert "needs an ID" in out


# ─── Pagination helper ──────────────────────────────────────────────

def test_extract_items_finds_known_keys():
    from tools.commvault_tool import _extract_items
    assert _extract_items({"jobs": [{"id": 1}, {"id": 2}]}) == [
        {"id": 1}, {"id": 2}
    ]
    assert _extract_items({"users": [{"u": "a"}]}) == [{"u": "a"}]
    # Fallback: first list-valued field
    assert _extract_items({"weird": [{"x": 1}], "other": 5}) == [{"x": 1}]
    assert _extract_items({}) == []
    assert _extract_items({"nada": "no list"}) == []


def test_paginate_get_stops_on_partial_page(monkeypatch):
    """When a page returns fewer items than page_size, stop."""
    _reset_cache()
    from tools.commvault_tool import _paginate_get, CommvaultSession, _cache_put
    _cache_put("cv.example", "ops", "tok")
    sess = CommvaultSession("cv.example", "ops", "pwd")
    sess._logged_in = True

    pages = iter([
        _FakeResp(200, '{"jobs":[{"id":1},{"id":2}]}'),  # full page (size=2)
        _FakeResp(200, '{"jobs":[{"id":3}]}'),           # partial → stop
    ])
    monkeypatch.setattr(sess.session, "get",
                        lambda *a, **kw: next(pages))

    out = _paginate_get(sess, "Job", {}, use_v4=False,
                        page_size=2, max_pages=10)
    assert out["total"] == 3
    assert out["pages_fetched"] == 2
    assert out["truncated"] is False


def test_paginate_get_respects_max_pages(monkeypatch):
    _reset_cache()
    from tools.commvault_tool import _paginate_get, CommvaultSession, _cache_put
    _cache_put("cv.example", "ops", "tok")
    sess = CommvaultSession("cv.example", "ops", "pwd")
    sess._logged_in = True

    # Always returns a full page → would loop forever without the cap
    full = _FakeResp(200, '{"jobs":[{"id":1},{"id":2}]}')
    monkeypatch.setattr(sess.session, "get",
                        lambda *a, **kw: full)

    out = _paginate_get(sess, "Job", {}, use_v4=False,
                        page_size=2, max_pages=3)
    assert out["pages_fetched"] == 3
    assert out["truncated"] is True
    assert out["total"] == 6


def test_is_paginated_endpoint():
    from tools.commvault_tool import _is_paginated_endpoint
    assert _is_paginated_endpoint("Job")
    assert _is_paginated_endpoint("Client")
    assert _is_paginated_endpoint("AlertRule")
    assert not _is_paginated_endpoint("CommServ")
    assert not _is_paginated_endpoint("LicenseInfo")


def test_direct_action_with_pagination(monkeypatch):
    """Passing page_size on a list action triggers _paginate_get."""
    _reset_cache()
    from tools.commvault_tool import execute_commvault_api, _cache_put
    _cache_put("cv.example", "ops", "tok")

    pages = iter([
        _FakeResp(200, '{"clientProperties":[{"id":1},{"id":2}]}'),
        _FakeResp(200, '{"clientProperties":[{"id":3}]}'),
    ])

    import tools.commvault_tool as cv_mod
    original_init = cv_mod.CommvaultSession.__init__
    def patched_init(self, host, user, pwd):
        original_init(self, host, user, pwd)
        self.session.get = lambda *a, **kw: next(pages)
    monkeypatch.setattr(cv_mod.CommvaultSession, "__init__", patched_init)

    out = execute_commvault_api(
        "cv.example", "ops", "pwd",
        {"action": "clients", "page_size": 2, "max_pages": 5},
    )
    assert '"total": 3' in out or "paged: 3" in out


# ─── Meta-actions still work ────────────────────────────────────────

def test_list_actions_no_host_needed():
    from tools.commvault_tool import execute_commvault_api
    out = execute_commvault_api("", "", "", "list_actions")
    assert "Curated Commvault actions" in out
    assert "jobs_active" in out
    assert "Meta-actions" in out
    assert "search_api" in out
    assert "raw" in out


def test_raw_get_uses_session(monkeypatch):
    _reset_cache()
    from tools.commvault_tool import execute_commvault_api, _cache_put
    _cache_put("cv.example", "ops", "tok")

    captured = {}
    import tools.commvault_tool as cv_mod
    original_init = cv_mod.CommvaultSession.__init__
    def patched_init(self, host, user, pwd):
        original_init(self, host, user, pwd)
        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            return _FakeResp(200, '{"ok": true}')
        self.session.get = fake_get
    monkeypatch.setattr(cv_mod.CommvaultSession, "__init__", patched_init)

    out = execute_commvault_api(
        "cv.example", "ops", "pwd",
        {"action": "raw", "method": "GET", "path": "/CommServ"},
    )
    assert "ok" in out
    assert "/CommServ" in captured["url"]


def test_raw_destructive_requires_flag():
    _reset_cache()
    from tools.commvault_tool import execute_commvault_api, _cache_put
    _cache_put("cv.example", "ops", "tok")
    out = execute_commvault_api(
        "cv.example", "ops", "pwd",
        {"action": "raw", "method": "DELETE", "path": "/Client/123"},
    )
    assert "allow_destructive=true" in out


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
