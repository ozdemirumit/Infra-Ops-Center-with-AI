"""
Unit tests for logging_config/atomic_io.py.
"""

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logging_config.atomic_io import atomic_read_json, atomic_write_json


def test_read_missing_file_returns_default():
    path = Path(tempfile.mktemp(suffix=".json"))
    result = atomic_read_json(path, default={"key": "value"})
    assert result == {"key": "value"}


def test_write_and_read():
    path = Path(tempfile.mktemp(suffix=".json"))
    data = {"devices": [{"id": 1, "name": "test"}]}
    atomic_write_json(path, data)
    result = atomic_read_json(path)
    assert result == data
    path.unlink()


def test_write_empty_list():
    path = Path(tempfile.mktemp(suffix=".json"))
    atomic_write_json(path, [])
    assert atomic_read_json(path, default=None) == []
    path.unlink()


def test_read_corrupt_file_returns_default():
    path = Path(tempfile.mktemp(suffix=".json"))
    path.write_text("{not valid json")
    result = atomic_read_json(path, default={"fallback": True})
    assert result == {"fallback": True}
    path.unlink()


def test_concurrent_writes_no_corruption():
    """Multiple threads writing should not corrupt the file."""
    path = Path(tempfile.mktemp(suffix=".json"))
    atomic_write_json(path, {"count": 0})

    def worker(n):
        for _ in range(10):
            atomic_write_json(path, {"writer": n, "item": "x" * 100})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # File should be readable and valid JSON
    result = atomic_read_json(path, default=None)
    assert result is not None
    assert "writer" in result
    path.unlink()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
