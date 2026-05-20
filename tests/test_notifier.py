"""
Tests for core/notifier.py — SMTP email helper.

We never touch a real SMTP server — every send is intercepted by patching
smtplib.SMTP / SMTP_SSL with a fake.
"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet
os.environ.setdefault("DEVICE_ENCRYPTION_KEY", Fernet.generate_key().decode())


class _FakeSMTP:
    """Captures sendmail calls without touching the network."""
    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_called = None
        self.sent = []
        self.quit_called = False
        self.noop_called = False
        _FakeSMTP.instances.append(self)

    def starttls(self):
        self.started_tls = True

    def login(self, user, pwd):
        self.login_called = (user, pwd)

    def sendmail(self, from_addr, to, body):
        self.sent.append({"from": from_addr, "to": to, "body": body})

    def noop(self):
        self.noop_called = True

    def quit(self):
        self.quit_called = True


def _fresh_vault():
    """Point vault at a temp file so set_secret never touches the real vault.json."""
    from auth import vault
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name
    vault._VAULT_FILE = Path(tmp)


def _configure(monkeypatch, **overrides):
    """Set SMTP_* on the live settings instance."""
    from config.settings import settings
    defaults = dict(
        SMTP_HOST="smtp.example.com", SMTP_PORT=587,
        SMTP_USER="ops@example.com", SMTP_FROM="ops@example.com",
        SMTP_USE_TLS=True, SMTP_USE_SSL=False, SMTP_TIMEOUT=10,
        SMTP_DEFAULT_TO="oncall@example.com",
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        monkeypatch.setattr(settings, k, v, raising=False)


def _install_fake(monkeypatch):
    _FakeSMTP.instances.clear()
    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)


def test_unconfigured_smtp_reports_error(monkeypatch):
    from core.notifier import send_email
    from config.settings import settings
    monkeypatch.setattr(settings, "SMTP_HOST", "", raising=False)
    monkeypatch.setattr(settings, "SMTP_FROM", "", raising=False)

    result = send_email("subject", "body", to="x@example.com")
    assert result["sent"] is False
    assert "SMTP not configured" in result["error"]


def test_no_recipient_reports_error(monkeypatch):
    _configure(monkeypatch, SMTP_DEFAULT_TO="")
    _install_fake(monkeypatch)
    from core.notifier import send_email
    res = send_email("s", "b")
    assert res["sent"] is False
    assert "recipient" in res["error"]


def test_send_uses_starttls_and_login(monkeypatch):
    _fresh_vault()
    _configure(monkeypatch)
    _install_fake(monkeypatch)
    from auth.vault import set_secret
    set_secret("api_keys", "smtp_password", "topsecret")

    from core.notifier import send_email
    res = send_email("Hello", "Body", to="alice@example.com")
    assert res["sent"] is True
    assert res["recipients"] == ["alice@example.com"]
    assert len(_FakeSMTP.instances) == 1
    fake = _FakeSMTP.instances[0]
    assert fake.host == "smtp.example.com"
    assert fake.port == 587
    assert fake.started_tls is True
    assert fake.login_called == ("ops@example.com", "topsecret")
    assert fake.quit_called is True


def test_send_to_multiple_recipients_string(monkeypatch):
    _fresh_vault()
    _configure(monkeypatch)
    _install_fake(monkeypatch)
    from core.notifier import send_email
    res = send_email("s", "b", to="a@x.com, b@x.com,  c@x.com")
    assert res["sent"] is True
    assert res["recipients"] == ["a@x.com", "b@x.com", "c@x.com"]
    assert _FakeSMTP.instances[0].sent[0]["to"] == ["a@x.com", "b@x.com", "c@x.com"]


def test_send_uses_ssl_when_configured(monkeypatch):
    _fresh_vault()
    _configure(monkeypatch, SMTP_USE_SSL=True, SMTP_USE_TLS=False, SMTP_PORT=465)
    _install_fake(monkeypatch)
    from core.notifier import send_email
    res = send_email("s", "b", to="x@y.com")
    assert res["sent"] is True
    # FakeSMTP doesn't distinguish, but starttls must NOT have been called for SSL mode
    assert _FakeSMTP.instances[0].started_tls is False


def test_send_html_includes_plain_fallback(monkeypatch):
    _fresh_vault()
    _configure(monkeypatch)
    _install_fake(monkeypatch)
    from core.notifier import send_email
    html = "<p>Hello <b>world</b></p>"
    res = send_email("subj", html, to="x@y.com", html=True)
    assert res["sent"] is True
    body = _FakeSMTP.instances[0].sent[0]["body"]
    # multipart/alternative with both plain and html
    assert "text/plain" in body
    assert "text/html" in body
    # Bodies are base64-encoded by the email library — decode and check
    import base64, re
    parts = re.findall(r"Content-Transfer-Encoding: base64\n\n([A-Za-z0-9+/=\n]+?)\n--",
                       body, re.MULTILINE)
    decoded = "".join(base64.b64decode(p).decode("utf-8") for p in parts)
    assert "Hello" in decoded


def test_send_error_returns_error_dict(monkeypatch):
    _fresh_vault()
    _configure(monkeypatch)

    class _Boom(_FakeSMTP):
        def sendmail(self, *a, **kw):
            raise RuntimeError("network down")

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", _Boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _Boom)

    from core.notifier import send_email
    res = send_email("s", "b", to="x@y.com")
    assert res["sent"] is False
    assert "network down" in res["error"]


def test_test_smtp_connection_probes_server(monkeypatch):
    _fresh_vault()
    _configure(monkeypatch)
    _install_fake(monkeypatch)
    from core.notifier import test_smtp_connection
    res = test_smtp_connection()
    assert res["ok"] is True
    assert _FakeSMTP.instances[0].noop_called is True


# ─── Workflow integration ──────────────────────────────────────────

def test_workflow_email_step_sends(monkeypatch):
    """notify channel:email must invoke send_email via the engine."""
    _fresh_vault()
    _configure(monkeypatch)
    _install_fake(monkeypatch)

    from core.workflow import WorkflowEngine, get_run, storage
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name
    storage.RUNS_FILE = Path(tmp)

    wf = {
        "name": "test_email",
        "steps": [
            {"id": "alert", "type": "notify", "channel": "email",
             "to": ["alice@example.com"],
             "subject": "Test alert",
             "message": "something happened"},
        ],
    }
    run_id = WorkflowEngine().start(wf)
    run = get_run(run_id)
    assert run["status"] == "completed"
    res = run["history"][0]["result"]
    assert res["channel"] == "email"
    assert res["sent"] is True
    assert res["recipients"] == ["alice@example.com"]
    assert res["subject"] == "Test alert"
    # And one real SMTP send happened
    assert len(_FakeSMTP.instances) == 1
    assert "alice@example.com" in _FakeSMTP.instances[0].sent[0]["to"]


def test_workflow_email_dry_run_does_not_send(monkeypatch):
    """In dry-run, the email step must NOT contact SMTP."""
    _fresh_vault()
    _configure(monkeypatch)
    _install_fake(monkeypatch)

    from core.workflow import WorkflowEngine, get_run, storage
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json").name
    storage.RUNS_FILE = Path(tmp)

    wf = {
        "name": "test_email_dry",
        "steps": [
            {"id": "alert", "type": "notify", "channel": "email",
             "to": ["x@y.com"], "subject": "s", "message": "m"},
        ],
    }
    run_id = WorkflowEngine().start(wf, dry_run=True)
    run = get_run(run_id)
    assert run["status"] == "completed"
    res = run["history"][0]["result"]
    assert res["sent"] is False
    assert res["dry_run"] is True
    assert _FakeSMTP.instances == []   # zero SMTP connections


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
