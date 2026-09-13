"""
In-memory run state tracker with JSON persistence on completion.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent.models import InputMode, RunState, RunStatus


# In-memory store: run_id → RunState
_runs: dict[str, RunState] = {}


def create_run(
    client_id: str,
    input_mode: InputMode,
    max_leads: int,
    keyword: Optional[str] = None,
    campaign_id: Optional[str] = None,
) -> RunState:
    """Create a new run, register it, and return the RunState."""
    run_id = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    state = RunState(
        run_id=run_id,
        client_id=client_id,
        campaign_id=campaign_id,
        status=RunStatus.PENDING,
        input_mode=input_mode,
        keyword=keyword,
        max_leads=max_leads,
    )
    _runs[run_id] = state
    return state


def update_run(run_id: str, **kwargs) -> RunState:
    """Update fields on an existing run."""
    state = _runs[run_id]
    for key, value in kwargs.items():
        setattr(state, key, value)
    return state


def get_run(run_id: str) -> Optional[RunState]:
    """Return the RunState for a run_id, or None if not found."""
    return _runs.get(run_id)


def complete_run(
    run_id: str,
    sheet_url: Optional[str],
    csv_path: Optional[str],
    recommendations: Optional[str],
    qualified_count: int,
    partial_count: int,
    apollo_credits: int = 0,
    firecrawl_pages: int = 0,
    llm_tokens: int = 0,
    total_cost_usd: Optional[float] = None,
) -> RunState:
    """Mark run as complete, persist to JSON, and return final state."""
    state = update_run(
        run_id,
        status=RunStatus.COMPLETE,
        current_stage="done",
        completed_at=datetime.utcnow(),
        sheet_url=sheet_url,
        csv_path=csv_path,
        recommendations=recommendations,
        qualified_count=qualified_count,
        partial_count=partial_count,
        apollo_credits_used=apollo_credits,
        firecrawl_pages_crawled=firecrawl_pages,
        llm_tokens_used=llm_tokens,
        total_cost_usd=total_cost_usd,
    )
    _persist(state)
    return state


def fail_run(run_id: str, error: str, **kwargs) -> RunState:
    """Mark run as failed and persist."""
    state = update_run(
        run_id,
        status=RunStatus.FAILED,
        completed_at=datetime.utcnow(),
        error=error,
        **kwargs,
    )
    _persist(state)
    return state


def _persist(state: RunState) -> None:
    """Save run summary JSON to data/clients/{client_id}/runs/{run_id}_summary.json"""
    summary_dir = Path("data") / "clients" / state.client_id / "runs"
    summary_dir.mkdir(parents=True, exist_ok=True)
    path = summary_dir / f"{state.run_id}_summary.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state.model_dump(mode="json"), f, indent=2, default=str)
