"""
Per-lead enrichment cost tracking with configurable cap enforcement.

Usage is attributed to the lead currently being processed via a thread-local
lead context (`with tracker.lead_context(lead_id, company_name): ...`).
Integrations report usage directly through `add_usage()`, so per-lead costs
stay correct even when stages run leads concurrently.

If LEAD_COST_CONSECUTIVE_LIMIT leads in a row exceed LEAD_COST_CAP_USD
(default $0.70), the tracker flags a breach. Stages check `should_halt()`
before starting new leads, and the pipeline raises CostCapBreached at the
next stage boundary — completed enrichment work is kept, not discarded.
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional


class CostCapBreached(Exception):
  """Raised by the pipeline when consecutive per-lead costs exceed the cap."""

  def __init__(self, diagnostic: dict[str, Any]):
    self.diagnostic = diagnostic
    super().__init__(diagnostic.get("message", "Lead cost cap breached"))


_tracker: Optional["CostTracker"] = None


def init_cost_tracker(run_id: str, client_id: str) -> CostTracker:
  global _tracker
  _tracker = CostTracker(run_id=run_id, client_id=client_id)
  return _tracker


def get_cost_tracker() -> Optional[CostTracker]:
  return _tracker


def reset_cost_tracker() -> None:
  global _tracker
  _tracker = None


def _empty_breakdown() -> dict[str, Any]:
  return {
    "apollo_credits": 0,
    "apollo_usd": 0.0,
    "firecrawl_pages": 0,
    "firecrawl_usd": 0.0,
    "apify_usd": 0.0,
    "llm_tokens": 0,
    "llm_usd": 0.0,
  }


class CostTracker:
  def __init__(self, run_id: str, client_id: str):
    self.run_id = run_id
    self.client_id = client_id
    self.lead_cap_usd = float(os.environ.get("LEAD_COST_CAP_USD", "0.70"))
    self.consecutive_limit = int(os.environ.get("LEAD_COST_CONSECUTIVE_LIMIT", "2"))
    self.apollo_per_credit = float(os.environ.get("APOLLO_CREDIT_USD", "0.02"))
    self.firecrawl_per_page = float(os.environ.get("FIRECRAWL_PAGE_USD", "0.01"))
    self.llm_per_1k_tokens = float(os.environ.get("LLM_PER_1K_TOKENS_USD", "0.003"))

    self._lock = threading.Lock()
    self._local = threading.local()
    self._run_breakdown = _empty_breakdown()
    self._lead_totals: dict[str, dict[str, Any]] = {}
    self._lead_order: list[str] = []
    self._consecutive_over = 0
    self._breach: Optional[dict[str, Any]] = None

  # ── Lead context (thread-local attribution) ────────────────────────────────

  @contextmanager
  def lead_context(self, lead_id: str, company_name: str):
    """Attribute all usage reported by this thread to the given lead."""
    self.begin_lead(lead_id, company_name)
    previous = getattr(self._local, "lead_id", None)
    self._local.lead_id = lead_id
    try:
      yield
    finally:
      self._local.lead_id = previous

  def begin_lead(self, lead_id: str, company_name: str) -> None:
    with self._lock:
      if lead_id in self._lead_totals:
        return
      self._lead_order.append(lead_id)
      self._lead_totals[lead_id] = {
        "lead_id": lead_id,
        "company_name": company_name,
        "total_usd": 0.0,
        "breakdown": _empty_breakdown(),
        "checkpoints": [],
      }

  # ── Usage reporting (called from integrations) ─────────────────────────────

  def add_usage(
    self,
    *,
    apollo_credits: int = 0,
    firecrawl_pages: int = 0,
    llm_tokens: int = 0,
    apify_usd: float = 0.0,
  ) -> None:
    """Record usage against the run and the current thread's lead (if any)."""
    delta = {
      "apollo_credits": apollo_credits,
      "apollo_usd": round(apollo_credits * self.apollo_per_credit, 4),
      "firecrawl_pages": firecrawl_pages,
      "firecrawl_usd": round(firecrawl_pages * self.firecrawl_per_page, 4),
      "apify_usd": round(apify_usd, 4),
      "llm_tokens": llm_tokens,
      "llm_usd": round((llm_tokens / 1000) * self.llm_per_1k_tokens, 4),
    }
    lead_id = getattr(self._local, "lead_id", None)
    with self._lock:
      self._run_breakdown = _merge_breakdown(self._run_breakdown, delta)
      if lead_id and lead_id in self._lead_totals:
        record = self._lead_totals[lead_id]
        record["breakdown"] = _merge_breakdown(record["breakdown"], delta)
        record["total_usd"] = _breakdown_total(record["breakdown"])

  def record_apify_run(self, run: Any) -> float:
    cost = _extract_run_usd(run)
    if cost:
      self.add_usage(apify_usd=cost)
    return cost

  # ── Checkpoints + cap enforcement ──────────────────────────────────────────

  def accumulate_lead_cost(
    self,
    lead_id: str,
    company_name: str,
    checkpoint: str,
  ) -> dict[str, Any]:
    """
    Record a checkpoint snapshot of this lead's own accumulated cost and run
    the cap check. Does NOT raise — sets the breach flag so in-flight work in
    other threads can finish and be kept.
    """
    self.begin_lead(lead_id, company_name)
    with self._lock:
      record = self._lead_totals[lead_id]
      record["checkpoints"].append({
        "checkpoint": checkpoint,
        "total_usd": record["total_usd"],
        **record["breakdown"],
      })

      if record["total_usd"] > self.lead_cap_usd:
        self._consecutive_over += 1
      else:
        self._consecutive_over = 0

      if self._consecutive_over >= self.consecutive_limit and self._breach is None:
        self._breach = self.build_diagnostic(trigger_lead=record, checkpoint=checkpoint)

    return record

  def should_halt(self) -> bool:
    """Stages check this before starting work on the next lead."""
    return self._breach is not None

  def raise_if_breached(self) -> None:
    """Called by the pipeline at stage boundaries."""
    if self._breach is not None:
      raise CostCapBreached(self._breach)

  # ── Reporting ───────────────────────────────────────────────────────────────

  def build_diagnostic(self, trigger_lead: dict[str, Any], checkpoint: str) -> dict[str, Any]:
    recent = [self._lead_totals[lid] for lid in self._lead_order[-5:] if lid in self._lead_totals]
    totals = dict(self._run_breakdown)
    totals["total_usd"] = _breakdown_total(totals)
    return {
      "message": (
        f"{self.consecutive_limit} consecutive leads exceeded "
        f"${self.lead_cap_usd:.2f} enrichment cap at checkpoint '{checkpoint}'"
      ),
      "run_id": self.run_id,
      "client_id": self.client_id,
      "cap_usd": self.lead_cap_usd,
      "consecutive_over": self._consecutive_over,
      "checkpoint": checkpoint,
      "trigger_lead": trigger_lead,
      "recent_leads": recent,
      "run_totals": totals,
      "leads_processed": len(self._lead_totals),
      "recommendations": [
        "Per-lead costs are now attributed to the lead that incurred them (thread-local).",
        "If Apify dominates, set APIFY_SKIP_PERSON_PROFILE=1 (profile scraper is ~$0.25/lead).",
        "Verify APIFY_LINKEDIN_POSTS_ACTOR=harvestapi/linkedin-profile-posts (data-slayer ignores maxItems).",
        "Check FIRECRAWL page cap (default 5 pages per company).",
        "Raise LEAD_COST_CAP_USD if the enrichment depth is intentional.",
      ],
    }

  def run_summary(self) -> dict[str, Any]:
    totals = dict(self._run_breakdown)
    totals["total_usd"] = _breakdown_total(totals)
    return {
      "cap_usd": self.lead_cap_usd,
      "leads_tracked": len(self._lead_totals),
      "run_totals": totals,
      "per_lead": list(self._lead_totals.values()),
    }


def persist_cost_diagnostic(diagnostic: dict[str, Any]) -> str:
  client_id = diagnostic.get("client_id", "unknown")
  run_id = diagnostic.get("run_id", "unknown")
  out_dir = Path("data") / "clients" / client_id / "runs"
  out_dir.mkdir(parents=True, exist_ok=True)
  path = out_dir / f"{run_id}_cost_diagnostic.json"
  with open(path, "w", encoding="utf-8") as f:
    json.dump(diagnostic, f, indent=2, default=str)
  return str(path)


def _extract_run_usd(run: Any) -> float:
  """Prefer refetch so final usageTotalUsd is not missed (see agent.utils.apify_spend)."""
  try:
    from agent.utils.apify_spend import refetch_run_usd

    return refetch_run_usd(run)
  except Exception:
    for attr in ("usage_total_usd", "usageTotalUsd", "usage_usd", "usageUsd"):
      if hasattr(run, attr):
        value = getattr(run, attr)
        if value is not None:
          return float(value)
    if isinstance(run, dict):
      for key in ("usageTotalUsd", "usage_total_usd", "usageUsd", "usage_usd"):
        if run.get(key) is not None:
          return float(run[key])
    return 0.0


def _breakdown_total(breakdown: dict[str, Any]) -> float:
  return round(
    breakdown["apollo_usd"]
    + breakdown["firecrawl_usd"]
    + breakdown["apify_usd"]
    + breakdown["llm_usd"],
    4,
  )


def _merge_breakdown(existing: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
  return {
    "apollo_credits": existing["apollo_credits"] + delta["apollo_credits"],
    "apollo_usd": round(existing["apollo_usd"] + delta["apollo_usd"], 4),
    "firecrawl_pages": existing["firecrawl_pages"] + delta["firecrawl_pages"],
    "firecrawl_usd": round(existing["firecrawl_usd"] + delta["firecrawl_usd"], 4),
    "apify_usd": round(existing["apify_usd"] + delta["apify_usd"], 4),
    "llm_tokens": existing["llm_tokens"] + delta["llm_tokens"],
    "llm_usd": round(existing["llm_usd"] + delta["llm_usd"], 4),
  }
