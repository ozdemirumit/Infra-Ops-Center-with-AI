"""
Notification helpers — email and webhook.

SMTP config comes from settings, the password from the encrypted vault
(`api_keys/smtp_password`). Designed to be called from workflow notify
steps, incident manager, and any future alerting code.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, make_msgid
from typing import Iterable, Optional

from config.settings import settings
from logging_config.logger import get_logger

logger = get_logger("notifier")


def _resolve_password() -> str:
    """Look up the SMTP password from the credential vault."""
    try:
        from auth.vault import get_secret
        return get_secret("api_keys", "smtp_password") or ""
    except Exception as e:
        logger.warning(f"Could not read smtp_password from vault: {e}")
        return ""


def is_email_configured() -> bool:
    """True if the SMTP settings look complete enough to attempt sending."""
    return bool(settings.SMTP_HOST and settings.SMTP_FROM)


def _normalize_recipients(to) -> list[str]:
    if to is None:
        return []
    if isinstance(to, str):
        return [r.strip() for r in to.split(",") if r.strip()]
    if isinstance(to, Iterable):
        out: list[str] = []
        for item in to:
            out.extend(_normalize_recipients(item))
        return out
    return []


def send_email(
    subject: str,
    body: str,
    to: Optional[Iterable[str] | str] = None,
    *,
    html: bool = False,
    sender: Optional[str] = None,
    timeout: Optional[int] = None,
) -> dict:
    """
    Send a plain-text (or HTML) email. Returns a result dict:
      {"sent": bool, "recipients": [...], "error": str | None}
    """
    if not is_email_configured():
        return {"sent": False, "recipients": [],
                "error": "SMTP not configured (set SMTP_HOST and SMTP_FROM)"}

    recipients = _normalize_recipients(to) or _normalize_recipients(settings.SMTP_DEFAULT_TO)
    if not recipients:
        return {"sent": False, "recipients": [],
                "error": "no recipient (pass `to:` or set SMTP_DEFAULT_TO)"}

    from_addr = sender or settings.SMTP_FROM
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject or "(no subject)"
    msg["From"] = formataddr(("AI Ops Center", from_addr))
    msg["To"] = ", ".join(recipients)
    msg["Message-ID"] = make_msgid(domain="ai-ops-center")

    if html:
        # Send both a text fallback and the HTML
        msg.attach(MIMEText(_strip_html(body), "plain", "utf-8"))
        msg.attach(MIMEText(body, "html", "utf-8"))
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))

    timeout = timeout or settings.SMTP_TIMEOUT
    password = _resolve_password()

    try:
        if settings.SMTP_USE_SSL:
            smtp = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=timeout)
        else:
            smtp = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=timeout)
            if settings.SMTP_USE_TLS:
                smtp.starttls()

        if settings.SMTP_USER and password:
            smtp.login(settings.SMTP_USER, password)

        smtp.sendmail(from_addr, recipients, msg.as_string())
        smtp.quit()

        logger.info(f"Email sent to {recipients}: {subject!r}")
        return {"sent": True, "recipients": recipients, "error": None}

    except Exception as e:
        # Never log the password — but type+host is fine for debugging
        err = f"{type(e).__name__}: {e}"
        logger.error(f"Email send failed via {settings.SMTP_HOST}: {err}")
        return {"sent": False, "recipients": recipients, "error": err}


def _strip_html(s: str) -> str:
    """Very small HTML-to-text fallback. Just enough for readers without HTML."""
    import re
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</\s*p\s*>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()


def test_smtp_connection() -> dict:
    """Probe the SMTP server without sending mail. Used by the Settings page."""
    if not is_email_configured():
        return {"ok": False, "error": "SMTP_HOST or SMTP_FROM not set"}
    try:
        if settings.SMTP_USE_SSL:
            smtp = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT,
                                    timeout=settings.SMTP_TIMEOUT)
        else:
            smtp = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT,
                                timeout=settings.SMTP_TIMEOUT)
            if settings.SMTP_USE_TLS:
                smtp.starttls()
        password = _resolve_password()
        if settings.SMTP_USER and password:
            smtp.login(settings.SMTP_USER, password)
        smtp.noop()
        smtp.quit()
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
