"""
Keep vendor reply bodies on the campaign until the operator accepts the list.

A parse bug can be re-mapped from these files. They are not deleted when a smoke
run is approved, only when the finished list is accepted.
"""
from __future__ import annotations

import contextvars
import json
import re
from pathlib import Path
from typing import Any

_DIR: contextvars.ContextVar[Path | None] = contextvars.ContextVar("raw_replies_dir", default=None)
_SEQ: contextvars.ContextVar[int] = contextvars.ContextVar("raw_replies_seq", default=0)


def _safe(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", text or "reply").strip("-")
    return cleaned[:80] or "reply"


def _clip(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:4000] + "…"
    if isinstance(value, list):
        return [_clip(item, depth + 1) for item in value[:40]]
    if isinstance(value, dict) and depth < 6:
        return {str(key): _clip(item, depth + 1) for key, item in list(value.items())[:80]}
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


def bind_raw_replies(campaign_dir: Path, run_id: str) -> tuple[contextvars.Token, contextvars.Token]:
    folder = Path(campaign_dir) / "raw_replies" / _safe(run_id)
    folder.mkdir(parents=True, exist_ok=True)
    return _DIR.set(folder), _SEQ.set(0)


def unbind_raw_replies(tokens: tuple[contextvars.Token, contextvars.Token] | None) -> None:
    if not tokens:
        return
    _DIR.reset(tokens[0])
    _SEQ.reset(tokens[1])


def store_raw_reply(source: str, payload: Any, *, key: str = "") -> Path | None:
    """Write one reply when a run has bound a campaign folder. No-op otherwise."""
    folder = _DIR.get()
    if folder is None or payload is None:
        return None
    seq = _SEQ.get() + 1
    _SEQ.set(seq)
    body = {"source": source, "key": key, "payload": _clip(payload)}
    path = folder / f"{seq:04d}-{_safe(source)}.json"
    try:
        path.write_text(json.dumps(body, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    except OSError:
        return None
    return path


def raw_replies_kept(campaign_dir: Path) -> bool:
    root = Path(campaign_dir) / "raw_replies"
    return root.is_dir() and any(root.rglob("*.json"))


def clear_raw_replies(campaign_dir: Path) -> int:
    """Delete stored replies after the operator accepts the finished list."""
    root = Path(campaign_dir) / "raw_replies"
    if not root.is_dir():
        return 0
    removed = 0
    files = [path for path in root.rglob("*") if path.is_file()]
    for path in files:
        path.unlink()
        removed += 1
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        path.rmdir()
    root.rmdir()
    return removed
