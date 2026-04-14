"""
Session storage module.
Each task gets an independent session with message history.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from logging_config.logger import get_logger

logger = get_logger("sessions")

_BASE_DIR = Path(__file__).resolve().parent
_SESSION_FILE = _BASE_DIR / "sessions.json"

STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


def _load() -> list[dict]:
    if not _SESSION_FILE.exists():
        return []
    try:
        with open(_SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load sessions: {e}")
        return []


def _save(sessions: list[dict]):
    try:
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"Failed to save sessions: {e}")


def create_session(title: str, connections: dict) -> dict:
    session = {
        "id": str(uuid.uuid4()),
        "title": title,
        "status": STATUS_ACTIVE,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "messages": [],
        "connections_snapshot": connections,
    }
    sessions = _load()
    sessions.insert(0, session)
    _save(sessions)
    logger.info(f"Session created: {session['id']} — {title}")
    return session


def get_session(session_id: str) -> Optional[dict]:
    for s in _load():
        if s["id"] == session_id:
            return s
    return None


def list_sessions(limit: int = 50) -> list[dict]:
    return _load()[:limit]


def save_session(session: dict):
    session["updated_at"] = datetime.now().isoformat()
    sessions = _load()
    for i, s in enumerate(sessions):
        if s["id"] == session["id"]:
            sessions[i] = session
            _save(sessions)
            return
    sessions.insert(0, session)
    _save(sessions)


def delete_session(session_id: str) -> bool:
    sessions = _load()
    new_sessions = [s for s in sessions if s["id"] != session_id]
    if len(new_sessions) == len(sessions):
        return False
    _save(new_sessions)
    logger.info(f"Session deleted: {session_id}")
    return True


def set_session_completed(session_id: str, messages: list):
    session = get_session(session_id)
    if not session:
        return
    session["status"] = STATUS_COMPLETED
    session["messages"] = messages
    save_session(session)


def set_session_failed(session_id: str, messages: list, error: str = ""):
    session = get_session(session_id)
    if not session:
        return
    session["status"] = STATUS_FAILED
    session["messages"] = messages
    save_session(session)
    logger.error(f"Session failed: {session_id} — {error}")


def update_session_messages(session_id: str, messages: list):
    session = get_session(session_id)
    if not session:
        return
    session["messages"] = messages
    session["status"] = STATUS_ACTIVE
    save_session(session)


def status_badge(status: str) -> str:
    return {
        STATUS_ACTIVE: "🔵 Active",
        STATUS_COMPLETED: "✅ Completed",
        STATUS_FAILED: "❌ Failed",
    }.get(status, "❓ Unknown")
