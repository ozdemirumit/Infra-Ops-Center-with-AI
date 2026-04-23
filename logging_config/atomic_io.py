"""
Atomic JSON I/O utilities.

Prevents race conditions when multiple Streamlit reruns / threads
read and write to the same JSON file. Uses temp file + os.replace()
for atomic writes (POSIX + Windows compatible).
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

# Per-file locks — prevent concurrent writes to the same file
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _get_lock(path: str) -> threading.Lock:
    """Get or create a lock for the given file path."""
    with _locks_guard:
        if path not in _locks:
            _locks[path] = threading.Lock()
        return _locks[path]


def atomic_read_json(path: Path, default: Any = None) -> Any:
    """
    Read a JSON file safely. Returns default on any error.

    Args:
        path: File path
        default: Value to return if file missing or unreadable
    """
    path = Path(path)
    if not path.exists():
        return default if default is not None else {}

    lock = _get_lock(str(path))
    with lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default if default is not None else {}


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    """
    Write JSON atomically: write to temp file, then os.replace().
    This guarantees readers never see a partial/corrupt file.

    Args:
        path: Target file path
        data: JSON-serializable data
        indent: JSON indentation
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lock = _get_lock(str(path))
    with lock:
        # Write to temp file in the same directory (so rename is atomic)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=False, default=str)
                f.flush()
                os.fsync(f.fileno())  # Ensure data hits disk

            # Atomic rename — on POSIX and Windows (Python 3.3+)
            os.replace(tmp_path, path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def atomic_update_json(path: Path, mutator, default: Any = None) -> Any:
    """
    Atomically read-modify-write a JSON file.

    Args:
        path: File path
        mutator: callable(data) -> data. Receives current data, returns updated data.
        default: Default value if file doesn't exist

    Returns:
        The updated data
    """
    path = Path(path)
    lock = _get_lock(str(path))
    with lock:
        # Read
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = default if default is not None else {}
        else:
            data = default if default is not None else {}

        # Mutate
        updated = mutator(data)
        if updated is None:
            updated = data

        # Write atomically
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(updated, f, indent=2, ensure_ascii=False, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return updated
