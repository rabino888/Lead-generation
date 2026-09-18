"""
Gated list run for talent_search: T01→T05 smoke → await approval → full T04–T06.

Called from ``list_run.start_smoke_gate`` / ``approve_full_batch`` when the
campaign plan is ``talent_search``. Reuses talent stage CLIs — never the
company deterministic funnel.
"""
from __future__ import annotations

import csv
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

log = logging.getLogger("leadgen.talent_list_run")

from agent.utils.list_run import load_list_run, update_list_run
from agent.utils.talent_people import (
    STAGE_T01_RAW_PEOPLE,
    STAGE_T02_DEDUPED,
    STAGE_T03_ICP_MATCHED,
    STAGE_T05_APOLLO_CONTACTABLE,
    read_people_csv,
    talent_stage_path,
)


def _run_stage(label: str, stage_main: Callable[[], int], argv: Sequence[str]) -> int:
    old = sys.argv
    try:
        sys.argv = [label, *argv]
        return int(stage_main() or 0)
    finally:
        sys.argv = old


def _count_people_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return sum(
                1
                for r in csv.DictReader(f)
                if (r.get("linkedin_url") or r.get("full_name") or "").strip()
            )
    except Exception:
        return 0


def people_seed_preview(campaign_dir: Path, *, limit: int = 40) -> list[dict[str, str]]:
    """Compact T01 people rows for builder/dashboard."""
    path = talent_stage_path(campaign_dir, STAGE_T01_RAW_PEOPLE)
    if not path.is_file():
        return []
    out: list[dict[str, str]] = []
    try:
        for r in read_people_csv(path):
            name = (r.get("full_name") or "").strip()
            li = (r.get("linkedin_url") or "").strip()
            if not name and not li:
                continue
            out.append(
                {
                    "full_name": name,
                    "title": (r.get("title") or r.get("headline") or "").strip(),
                    "location": (r.get("location") or "").strip(),
                    "company_name": (r.get("company_name") or "").strip(),
                    "linkedin_url": li,
                }
            )
            if len(out) >= limit:
                break
    except Exception:
        return []
    return out


def people_smoke_preview(
    rows_or_path: Path | list[dict[str, Any]],
    *,
    limit: int = 20,
) -> list[dict[str, str]]:
    """Preview smoked people from T05 (or fallback matched rows)."""
    rows: list[dict[str, Any]]
    if isinstance(rows_or_path, Path):
        if not rows_or_path.is_file():
            return []
        try:
            rows = list(read_people_csv(rows_or_path))
        except Exception:
            return []
    else:
        rows = list(rows_or_path or [])

    out: list[dict[str, str]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = (r.get("full_name") or "").strip()
        li = (r.get("linkedin_url") or "").strip()
        if not name and not li:
            continue
        email = (r.get("email") or "").strip()
        out.append(
            {
                "full_name": name,
                "title": (r.get("title") or r.get("headline") or "").strip(),
                "location": (r.get("location") or "").strip(),
                "company_name": (r.get("company_name") or "").strip(),
                "email": email or "(no email)",
                "linkedin_url": li,
            }
        )
        if len(out) >= limit:
            break
    return out


def _linkedin_people_configured(cfg: dict[str, Any]) -> bool:
    if cfg.get("urls") or cfg.get("url") or cfg.get("url_file"):
        return True
    if (cfg.get("query") or cfg.get("searchQuery") or "").strip():
        return True
    title = cfg.get("title") or cfg.get("titles") or ""
    if isinstance(title, list) and any(str(t).strip() for t in title):
        return True
    if isinstance(title, str) and title.strip():
        return True
    loc = cfg.get("location") or cfg.get("locations") or ""
    if isinstance(loc, list) and any(str(x).strip() for x in loc):
        return True
    if isinstance(loc, str) and loc.strip():
        return True
    return False


def assert_talent_seeds_ready(campaign: Any) -> None:
    """
    Raise FileNotFoundError when talent has no people seed path.

    Allows start when any of:
      - stages/T01_raw_people.csv has rows
      - people_seeds.csv exists
      - enabled people_csv_ingest with csv_path / file
      - enabled linkedin_people with query/title/location/urls
    """
    camp_dir = Path(campaign.campaign_dir)
    t01 = talent_stage_path(camp_dir, STAGE_T01_RAW_PEOPLE)
    if _count_people_rows(t01) > 0:
        return

    people_seeds = camp_dir / "people_seeds.csv"
    if people_seeds.is_file() and people_seeds.stat().st_size > 16:
        return

    seeds_cfg_path = camp_dir / "seed_sources.json"
    sources: list[Any] = []
    if seeds_cfg_path.is_file():
        try:
            cfg = json.loads(seeds_cfg_path.read_text(encoding="utf-8-sig"))
            if isinstance(cfg, list):
                sources = cfg
            elif isinstance(cfg, dict):
                sources = list(cfg.get("sources") or [])
            else:
                sources = []
        except (OSError, json.JSONDecodeError):
            sources = []

    for entry in sources:
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        sid = (
            entry.get("type") or entry.get("id") or entry.get("source_id") or ""
        ).strip().lower()
        conf = entry.get("config") or {}
        if not isinstance(conf, dict):
            conf = {}

        if sid == "people_csv_ingest" or sid.startswith("people_csv"):
            csv_path = (conf.get("csv_path") or conf.get("file") or "").strip()
            if csv_path:
                return
            if people_seeds.is_file():
                return
            continue

        if sid == "linkedin_people" or sid.startswith("linkedin_people"):
            if _linkedin_people_configured(conf):
                return

    raise FileNotFoundError(
        "No talent people seeds yet. Add people_seeds.csv, configure enabled "
        "people_csv_ingest (csv_path) or linkedin_people (query/title/location/urls), "
        "or run T01 so stages/T01_raw_people.csv has rows."
    )


def start_talent_smoke_gate(
    campaign_dir: Path,
    campaign_id: str,
    *,
    smoke_leads: int = 2,
    target_leads: int = 50,
) -> dict[str, Any]:
    """
    Talent gated smoke: T01 (if needed) → T02 → T03 → T04/T05 capped.
    Skips T06 phone on smoke. Stops at awaiting_approval.
    """
    from scripts.run_talent_campaign import talent_phone_gate
    from scripts.stage_talent_apollo import main as apollo_main
    from scripts.stage_talent_dedupe import main as dedupe_main
    from scripts.stage_talent_icp import main as icp_main
    from scripts.stage_talent_profile import main as profile_main
    from scripts.stage_talent_seed import main as seed_main

    from agent.utils.campaign_config import load_campaign
    from agent.utils.enrichment_plan import load_plan
    from agent.utils.icp_rules import ensure_stages_dir

    smoke_leads = max(1, min(int(smoke_leads or 2), 10))
    target_leads = max(smoke_leads, int(target_leads or 50))

    update_list_run(
        campaign_dir,
        campaign_id=campaign_id,
        phase="seeding",
        smoke_leads=smoke_leads,
        target_leads=target_leads,
        message="Building people seed list…",
        error="",
        smoke_run_id="",
        full_run_id="",
        counts={},
    )

    try:
        campaign = load_campaign(campaign_id)
        ensure_stages_dir(campaign)
        plan = load_plan(campaign.campaign_dir, campaign_id)
        camp_dir = Path(campaign.campaign_dir)

        t01_path = talent_stage_path(camp_dir, STAGE_T01_RAW_PEOPLE)
        if _count_people_rows(t01_path) == 0:
            update_list_run(
                campaign_dir,
                phase="seeding",
                message="T01 people seed…",
            )
            rc = _run_stage(
                "stage_talent_seed.py",
                seed_main,
                ["--campaign", campaign_id],
            )
            if rc != 0:
                raise RuntimeError(f"T01 seed failed (exit {rc})")

        seed_n = _count_people_rows(t01_path)
        if seed_n == 0:
            raise RuntimeError("T01_raw_people.csv is empty — nothing to smoke-test")

        update_list_run(
            campaign_dir,
            phase="matching",
            message="T02 dedupe + T03 person ICP…",
        )
        rc = _run_stage(
            "stage_talent_dedupe.py",
            dedupe_main,
            ["--campaign", campaign_id],
        )
        if rc != 0:
            raise RuntimeError(f"T02 dedupe failed (exit {rc})")

        rc = _run_stage(
            "stage_talent_icp.py",
            icp_main,
            ["--campaign", campaign_id],
        )
        if rc != 0:
            raise RuntimeError(f"T03 ICP failed (exit {rc})")

        t02_n = _count_people_rows(talent_stage_path(camp_dir, STAGE_T02_DEDUPED))
        t03_path = talent_stage_path(camp_dir, STAGE_T03_ICP_MATCHED)
        t03_n = _count_people_rows(t03_path)
        if t03_n == 0:
            raise RuntimeError("T03_icp_matched.csv is empty — nothing to smoke-test")

        smoke_n = min(smoke_leads, t03_n)
        update_list_run(
            campaign_dir,
            phase="smoking",
            message=f"Paid smoke on {smoke_n} person(s) (profile + Apollo)…",
            counts={
                "seeds": seed_n,
                "after_dedupe": t02_n,
                "after_icp": t03_n,
            },
        )

        rc = _run_stage(
            "stage_talent_profile.py",
            profile_main,
            ["--campaign", campaign_id, "--limit", str(smoke_n)],
        )
        if rc != 0:
            raise RuntimeError(f"T04 profile failed (exit {rc})")

        rc = _run_stage(
            "stage_talent_apollo.py",
            apollo_main,
            ["--campaign", campaign_id, "--max-leads", str(smoke_n)],
        )
        if rc != 0:
            raise RuntimeError(f"T05 Apollo failed (exit {rc})")

        # Prefer skip phone on smoke (even if plan has apollo_phone).
        phone_action, _ = talent_phone_gate(plan, skip_phone=True)
        assert phone_action == "skip_flag"

        t05_path = talent_stage_path(camp_dir, STAGE_T05_APOLLO_CONTACTABLE)
        contactable_n = _count_people_rows(t05_path)
        smoke_preview = people_smoke_preview(t05_path)
        if not smoke_preview:
            # Zero-contactable: still show who was in the ICP smoke slice
            matched = read_people_csv(t03_path)[:smoke_n]
            smoke_preview = people_smoke_preview(matched)

        seed_preview = people_seed_preview(camp_dir)
        counts = {
            "seeds": seed_n,
            "after_dedupe": t02_n,
            "after_icp": t03_n,
            "smoke_seeds": smoke_n,
            "smoke_contactable": contactable_n,
        }
        state = update_list_run(
            campaign_dir,
            phase="awaiting_approval",
            message=(
                f"Smoke complete: {contactable_n} contactable of {smoke_n} people smoked. "
                f"{seed_n} seeds on disk. Review results, then approve the full batch."
            ),
            smoke_run_id=f"talent-smoke:{campaign_id}",
            smoke_csv_path=str(t05_path) if t05_path.is_file() else "",
            seed_preview=seed_preview,
            smoke_preview=smoke_preview,
            counts=counts,
            error="",
        )
        try:
            from agent.dashboard.routes import rebuild_dashboard_files

            rebuild_dashboard_files(Path("data"))
        except Exception:
            log.debug("dashboard rebuild after talent smoke skipped", exc_info=True)
        return state
    except Exception as exc:
        log.exception("Talent smoke gate failed for %s", campaign_id)
        return update_list_run(
            campaign_dir,
            phase="error",
            message="Gated talent run failed",
            error=str(exc),
        )


def approve_talent_full_batch(campaign_dir: Path, campaign_id: str) -> dict[str, Any]:
    """Full talent batch: T04+T05 up to target_leads; T06 if modules.apollo_phone."""
    from scripts.run_talent_campaign import talent_phone_gate
    from scripts.stage_talent_apollo import main as apollo_main
    from scripts.stage_talent_phone import main as phone_main
    from scripts.stage_talent_profile import main as profile_main

    from agent.utils.campaign_config import load_campaign
    from agent.utils.enrichment_plan import load_plan

    state = load_list_run(campaign_dir, campaign_id)
    phase = state.get("phase")
    if phase == "completed":
        return state
    if phase not in ("awaiting_approval", "running_full"):
        raise RuntimeError(
            f"Cannot approve full batch from phase={phase!r}; "
            "finish smoke gate first."
        )

    smoke_leads = max(1, int(state.get("smoke_leads") or 2))
    target_leads = max(smoke_leads, int(state.get("target_leads") or 50))

    update_list_run(
        campaign_dir,
        phase="running_full",
        message=f"Full talent batch up to {target_leads} contactable leads…",
        error="",
    )

    try:
        campaign = load_campaign(campaign_id)
        plan = load_plan(campaign.campaign_dir, campaign_id)
        camp_dir = Path(campaign.campaign_dir)

        t03_path = talent_stage_path(camp_dir, STAGE_T03_ICP_MATCHED)
        t03_n = _count_people_rows(t03_path)
        if t03_n == 0:
            raise RuntimeError("No T03_icp_matched.csv — re-run the smoke gate")

        limit = min(target_leads, t03_n)
        rc = _run_stage(
            "stage_talent_profile.py",
            profile_main,
            ["--campaign", campaign_id, "--limit", str(limit)],
        )
        if rc != 0:
            raise RuntimeError(f"T04 profile failed (exit {rc})")

        rc = _run_stage(
            "stage_talent_apollo.py",
            apollo_main,
            ["--campaign", campaign_id, "--max-leads", str(limit)],
        )
        if rc != 0:
            raise RuntimeError(f"T05 Apollo failed (exit {rc})")

        phone_action, phone_err = talent_phone_gate(plan, skip_phone=False)
        if phone_action == "error":
            raise RuntimeError(phone_err or "T06 phone prereqs failed")
        if phone_action == "run":
            phone_argv = ["--campaign", campaign_id, "--limit", str(limit)]
            rc = _run_stage("stage_talent_phone.py", phone_main, phone_argv)
            if rc != 0:
                raise RuntimeError(f"T06 phone failed (exit {rc})")

        t05_path = talent_stage_path(camp_dir, STAGE_T05_APOLLO_CONTACTABLE)
        delivered_n = _count_people_rows(t05_path)
        counts = dict(state.get("counts") or {})
        counts["full_seeds"] = limit
        counts["full_contactable"] = delivered_n

        state = update_list_run(
            campaign_dir,
            phase="completed",
            message=f"Full talent batch finished — {delivered_n} contactable lead(s) on disk.",
            full_run_id=f"talent-full:{campaign_id}",
            smoke_csv_path=str(t05_path) if t05_path.is_file() else state.get("smoke_csv_path") or "",
            counts=counts,
            error="",
        )
        try:
            from agent.dashboard.routes import rebuild_dashboard_files

            rebuild_dashboard_files(Path("data"))
        except Exception:
            log.debug("dashboard rebuild after talent full batch skipped", exc_info=True)
        return state
    except Exception as exc:
        log.exception("Talent full batch failed for %s", campaign_id)
        return update_list_run(
            campaign_dir,
            phase="error",
            message="Full talent batch failed",
            error=str(exc),
        )


def resolve_campaign_type(campaign_dir: Path, campaign_id: str = "") -> str:
    """Return campaign_type from plan or campaign.json (default company_outreach)."""
    ctype = "company_outreach"
    camp_json = Path(campaign_dir) / "campaign.json"
    if camp_json.is_file():
        try:
            meta = json.loads(camp_json.read_text(encoding="utf-8-sig"))
            if isinstance(meta, dict) and (meta.get("campaign_type") or "").strip():
                ctype = str(meta["campaign_type"]).strip()
        except (OSError, json.JSONDecodeError):
            pass
    try:
        from agent.utils.enrichment_plan import load_plan

        plan = load_plan(Path(campaign_dir), campaign_id or Path(campaign_dir).name)
        plan_ctype = (plan.get("campaign_type") or "").strip()
        if plan_ctype:
            ctype = plan_ctype
    except Exception:
        pass
    return ctype
