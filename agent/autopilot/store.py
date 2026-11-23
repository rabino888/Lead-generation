"""Run state and learning files under the local data root (gitignored)."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def runs_dir(data_root: Path) -> Path:
    return Path(data_root) / "autopilot" / "runs"


def run_dir(data_root: Path, run_id: str) -> Path:
    return runs_dir(data_root) / run_id


def state_path(data_root: Path, run_id: str) -> Path:
    return run_dir(data_root, run_id) / "state.json"


def global_learnings_path(data_root: Path) -> Path:
    return Path(data_root) / "autopilot" / "learnings" / "global.json"


def client_learnings_path(data_root: Path, client_id: str) -> Path:
    return Path(data_root) / "clients" / client_id / "autopilot_learnings.json"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def empty_learnings() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "entries": []}


def load_state(data_root: Path, run_id: str) -> dict[str, Any]:
    path = state_path(data_root, run_id)
    data = _read_json(path, None)
    if not isinstance(data, dict):
        raise FileNotFoundError(f"Autopilot run not found: {run_id}")
    return data


def save_state(data_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    state["updated_at"] = utc_now()
    with _LOCK:
        _write_json(state_path(data_root, state["run_id"]), state)
    return state


def list_states(data_root: Path) -> list[dict[str, Any]]:
    root = runs_dir(data_root)
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/state.json")):
        data = _read_json(path, None)
        if isinstance(data, dict):
            rows.append(data)
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return rows


def append_log(state: dict[str, Any], event: str, **detail: Any) -> None:
    state.setdefault("log", []).append(
        {"at": utc_now(), "event": event, **{k: v for k, v in detail.items() if v is not None}}
    )


def load_learnings(path: Path) -> dict[str, Any]:
    data = _read_json(path, empty_learnings())
    if not isinstance(data, dict):
        return empty_learnings()
    entries = data.get("entries")
    if not isinstance(entries, list):
        data["entries"] = []
    data.setdefault("schema_version", SCHEMA_VERSION)
    return data


def save_learnings(path: Path, doc: dict[str, Any]) -> None:
    with _LOCK:
        _write_json(path, doc)


def load_applicable_learnings(data_root: Path, client_id: str = "") -> list[dict[str, Any]]:
    """Global entries first, then this client's, newest last so callers can override."""
    rows = list(load_learnings(global_learnings_path(data_root)).get("entries") or [])
    if client_id:
        rows.extend(load_learnings(client_learnings_path(data_root, client_id)).get("entries") or [])
    return [row for row in rows if isinstance(row, dict)]


def append_learning(data_root: Path, entry: dict[str, Any]) -> None:
    scope = entry.get("scope") or "client"
    if scope == "global":
        path = global_learnings_path(data_root)
    else:
        client_id = (entry.get("client_id") or "").strip()
        if not client_id:
            return
        path = client_learnings_path(data_root, client_id)
    doc = load_learnings(path)
    doc.setdefault("entries", []).append(entry)
    save_learnings(path, doc)
