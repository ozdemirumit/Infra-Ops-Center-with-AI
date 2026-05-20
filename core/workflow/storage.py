"""
Workflow run persistence. Atomic JSON file under repo root.
"""

from pathlib import Path
from typing import Optional

from logging_config.atomic_io import atomic_read_json, atomic_update_json, atomic_write_json

RUNS_FILE = Path(__file__).resolve().parent.parent.parent / "workflow_runs.json"
_MAX_RUNS = 200  # keep last N runs


def list_runs() -> list[dict]:
    """All known runs, newest first."""
    data = atomic_read_json(RUNS_FILE, default={"runs": []})
    runs = data.get("runs", [])
    return sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True)


def get_run(run_id: str) -> Optional[dict]:
    for r in list_runs():
        if r.get("id") == run_id:
            return r
    return None


def save_run(run: dict) -> None:
    """Insert or update a run by id."""
    def _mutate(data):
        if not isinstance(data, dict):
            data = {"runs": []}
        runs = data.get("runs", [])
        # Update in place if exists
        for i, r in enumerate(runs):
            if r.get("id") == run.get("id"):
                runs[i] = run
                data["runs"] = runs
                return data
        runs.append(run)
        # Cap
        if len(runs) > _MAX_RUNS:
            runs = sorted(runs, key=lambda r: r.get("created_at", ""))[-_MAX_RUNS:]
        data["runs"] = runs
        return data

    atomic_update_json(RUNS_FILE, _mutate, default={"runs": []})


def delete_run(run_id: str) -> bool:
    found = [False]
    def _mutate(data):
        if not isinstance(data, dict):
            return data
        runs = data.get("runs", [])
        new_runs = [r for r in runs if r.get("id") != run_id]
        if len(new_runs) != len(runs):
            found[0] = True
        data["runs"] = new_runs
        return data

    atomic_update_json(RUNS_FILE, _mutate, default={"runs": []})
    return found[0]
