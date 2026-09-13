"""
Guardrails for stage_website_repair — lock, run cap, and repair classification.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.utils.cost_manifest import load_manifest
from agent.utils.website_markdown_cache import has_cache

REPAIR_FAILURE_MARKERS = (
    "thin site",
    "analysis failed",
    "pitch analysis failed",
    "careers page only",
    "hiring or page signals only",
)

_PARTIAL_SIGNAL_COLUMNS = (
    "website_is_hiring",
    "services_offered",
    "website_open_roles",
    "website_careers_url",
    "about_summary",
    "has_blog",
    "blog_posts",
)


def repair_lock_path(campaign_dir: Path) -> Path:
    return campaign_dir / ".website_repair.lock"


def _lock_stale_seconds() -> int:
    return int(os.environ.get("WEBSITE_REPAIR_LOCK_STALE_SEC", "10800"))


def max_repair_runs_per_24h() -> int:
    return int(os.environ.get("WEBSITE_REPAIR_MAX_RUNS_PER_24H", "2"))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class RepairLock:
    """Exclusive lock — one website repair per campaign at a time."""

    campaign_dir: Path
    force: bool = False
    _held: bool = False

    def acquire(self) -> None:
        if self.force:
            return
        path = repair_lock_path(self.campaign_dir)
        now = datetime.now(UTC)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            started = _parse_utc(data.get("started_at_utc"))
            pid = int(data.get("pid") or 0)
            stale = (
                started is None
                or (now - started).total_seconds() > _lock_stale_seconds()
            )
            if not stale and _pid_alive(pid):
                raise RuntimeError(
                    f"Website repair already running for this campaign "
                    f"(pid {pid}, lock {path}). "
                    "Wait for it to finish or pass --force."
                )
            path.unlink(missing_ok=True)
        payload = {
            "pid": os.getpid(),
            "started_at_utc": now.isoformat(),
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        repair_lock_path(self.campaign_dir).unlink(missing_ok=True)
        self._held = False


def count_recent_repair_runs(campaign_dir: Path, *, hours: int = 24) -> int:
    manifest = load_manifest(campaign_dir, campaign_dir.name)
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    count = 0
    for run in manifest.get("runs") or []:
        if run.get("stage") != "website_repair":
            continue
        started = _parse_utc(run.get("started_at_utc"))
        if started and started >= cutoff:
            count += 1
    return count


def assert_repair_run_budget(campaign_dir: Path, *, force: bool = False) -> None:
    if force:
        return
    cap = max_repair_runs_per_24h()
    recent = count_recent_repair_runs(campaign_dir)
    if recent >= cap:
        raise RuntimeError(
            f"Website repair run cap reached: {recent}/{cap} runs in the last 24h "
            f"for {campaign_dir.name}. Pass --force to override."
        )


def summary_needs_repair(summary: str) -> bool:
    text = (summary or "").strip().lower()
    if not text:
        return True
    return any(marker in text for marker in REPAIR_FAILURE_MARKERS)


def row_needs_repair(row: dict[str, Any]) -> bool:
    if not (row.get("website") or "").strip():
        return False
    return summary_needs_repair(row.get("website_summary") or "")


def has_partial_website_signals(row: dict[str, Any]) -> bool:
    return any((row.get(col) or "").strip() for col in _PARTIAL_SIGNAL_COLUMNS)


def llm_only_eligible(row: dict[str, Any], campaign_dir: Path) -> bool:
    """
    True when crawl likely succeeded but summary/LLM failed — skip Firecrawl on repair.
    """
    website = (row.get("website") or "").strip()
    if not website:
        return False
    if has_cache(campaign_dir, website):
        return True
    summary = (row.get("website_summary") or "").strip()
    if not summary:
        return False
    if summary_needs_repair(summary) and has_partial_website_signals(row):
        return True
    return False


def classify_repair_rows(
    rows: list[dict[str, Any]],
    campaign_dir: Path,
    *,
    llm_only_mode: bool = False,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Returns (llm_only_rows, full_crawl_rows, skipped_rows).
    skipped = needs repair but llm_only_mode and not cache-eligible.
    """
    llm_only: list[dict] = []
    full: list[dict] = []
    skipped: list[dict] = []
    for row in rows:
        if llm_only_mode:
            if llm_only_eligible(row, campaign_dir):
                llm_only.append(row)
            else:
                skipped.append(row)
        elif llm_only_eligible(row, campaign_dir):
            llm_only.append(row)
        else:
            full.append(row)
    return llm_only, full, skipped
