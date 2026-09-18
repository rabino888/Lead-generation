"""
Gated list run: seeds → ICP + dedupe → paid smoke → await approval → full batch.

SPEC-dashboard.md §3.4. State: campaign ``list_run.json``.
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger("leadgen.list_run")

LIST_RUN_FILE = "list_run.json"

PHASES = (
    "idle",
    "seeding",
    "matching",
    "smoking",
    "awaiting_approval",
    "running_full",
    "completed",
    "error",
)

ACTIVE_PHASES = frozenset(
    {"seeding", "matching", "smoking", "running_full"}
)


def list_run_path(campaign_dir: Path) -> Path:
    return campaign_dir / LIST_RUN_FILE


def default_list_run(campaign_id: str = "") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "phase": "idle",
        "smoke_leads": 2,
        "target_leads": 50,
        "message": "",
        "error": "",
        "smoke_run_id": "",
        "smoke_csv_path": "",
        "full_run_id": "",
        "sheet_url": "",
        "counts": {},
        "seed_preview": [],
        "smoke_preview": [],
        "updated_at_utc": "",
    }


def load_list_run(campaign_dir: Path, campaign_id: str = "") -> dict[str, Any]:
    path = list_run_path(campaign_dir)
    base = default_list_run(campaign_id or campaign_dir.name)
    if not path.is_file():
        return base
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(data, dict):
        return base
    out = {**base, **data}
    out["campaign_id"] = out.get("campaign_id") or campaign_id or campaign_dir.name
    if out.get("phase") not in PHASES:
        out["phase"] = "idle"
    return out


def save_list_run(campaign_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    out = dict(state)
    out["updated_at_utc"] = datetime.now(UTC).isoformat()
    path = list_run_path(campaign_dir)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def update_list_run(campaign_dir: Path, **fields: Any) -> dict[str, Any]:
    state = load_list_run(campaign_dir)
    state.update(fields)
    return save_list_run(campaign_dir, state)


@contextmanager
def apply_plan_runtime(plan: dict[str, Any]) -> Iterator[None]:
    """Apply enrichment_plan modules to process env for the duration of a run."""
    mods = (plan or {}).get("modules") or {}
    desired: dict[str, Optional[str]] = {}

    def _flag(on: bool) -> str:
        return "0" if on else "1"

    desired["APIFY_SKIP_PERSON_PROFILE"] = _flag(bool(mods.get("apify_dm_profile", True)))
    desired["APIFY_SKIP_LINKEDIN_POSTS"] = _flag(bool(mods.get("apify_dm_posts", True)))
    desired["APIFY_SKIP_LINKEDIN_JOBS"] = _flag(bool(mods.get("apify_company_jobs", False)))
    if mods.get("apollo_phone"):
        desired["APOLLO_SKIP_PHONE"] = "0"
    else:
        desired["APOLLO_SKIP_PHONE"] = "1"

    crawler = (plan or {}).get("website_crawler") or "apify"
    if crawler in ("apify", "firecrawl"):
        desired["WEBSITE_CRAWLER"] = crawler

    # Website scrape off → skip via empty crawl budget if supported; else leave crawler
    # (pipeline still may call website — optional modules without full runner hooks stay soft).

    previous: dict[str, Optional[str]] = {}
    for key, val in desired.items():
        previous[key] = os.environ.get(key)
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _count_seed_rows(campaign_id: str) -> int:
    """Full seed count from stages/01_raw_seeds.csv (not capped preview length)."""
    from agent.utils.campaign_config import load_campaign
    from agent.utils.icp_rules import stage_path

    try:
        campaign = load_campaign(campaign_id)
        raw = stage_path(campaign, "raw_seeds")
        if not raw.is_file():
            return 0
        with raw.open(encoding="utf-8-sig", newline="") as f:
            return sum(1 for r in csv.DictReader(f) if (r.get("company_name") or "").strip())
    except Exception:
        return 0


def _seed_preview_rows(campaign_id: str, *, limit: int = 40) -> list[dict[str, str]]:
    """Compact seed rows for dashboard (name + website + location)."""
    from agent.utils.campaign_config import load_campaign
    from agent.utils.icp_rules import stage_path

    campaign = load_campaign(campaign_id)
    raw = stage_path(campaign, "raw_seeds")
    if not raw.is_file():
        return []
    out: list[dict[str, str]] = []
    try:
        with raw.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                name = (r.get("company_name") or "").strip()
                if not name:
                    continue
                out.append(
                    {
                        "company_name": name,
                        "website": (r.get("website") or "").strip(),
                        "location": (r.get("location") or "").strip(),
                        "industry": (r.get("industry") or "").strip(),
                    }
                )
                if len(out) >= limit:
                    break
    except Exception:
        return []
    return out


def _smoke_preview_from_rows(rows: list[Any], *, limit: int = 20) -> list[dict[str, str]]:
    """Preview smoked companies even when Apollo returned no contactable CSV."""
    out: list[dict[str, str]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = (r.get("company_name") or "").strip()
        if not name:
            continue
        email = (r.get("decision_maker_email") or "").strip()
        out.append(
            {
                "company_name": name,
                "website": (r.get("website") or "").strip(),
                "decision_maker_name": (r.get("decision_maker_name") or "").strip(),
                "decision_maker_email": email or "(no email)",
                "decision_maker_title": (r.get("decision_maker_title") or "").strip(),
            }
        )
        if len(out) >= limit:
            break
    return out


def _smoke_leads_preview(csv_path: Path, *, limit: int = 20) -> list[dict[str, str]]:
    if not csv_path.is_file():
        return []
    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            return _smoke_preview_from_rows(list(csv.DictReader(f)), limit=limit)
    except Exception:
        return []


def _smoke_preview_from_run_log(log_path: Path, *, limit: int = 20) -> list[dict[str, str]]:
    """
    Recover a partial smoke preview from a pipeline log when the worker died
    before writing a contactable CSV (e.g. portal restart mid-run).

    Parses Stage 4 Apollo lines:
      Qualified: {company} ({email})
      Rejected: {company} — {reason}
    """
    import re

    if not log_path.is_file():
        return []
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    qualified_re = re.compile(
        r"Qualified:\s*(.+?)\s*\(([^)]+@[^)]+)\)\s*$",
        re.MULTILINE,
    )
    rejected_re = re.compile(
        r"Rejected:\s*(.+?)\s*[—\-]\s*.+$",
        re.MULTILINE,
    )
    by_company: dict[str, dict[str, str]] = {}
    order: list[str] = []

    def _add(name: str, email: str) -> None:
        key = name.strip()
        if not key or key in by_company:
            return
        by_company[key] = {
            "company_name": key,
            "website": "",
            "decision_maker_name": "",
            "decision_maker_title": "",
            "decision_maker_email": email,
        }
        order.append(key)

    for m in qualified_re.finditer(text):
        _add(m.group(1), (m.group(2) or "").strip())
    for m in rejected_re.finditer(text):
        _add(m.group(1), "(no email)")

    out = [by_company[k] for k in order[:limit]]
    return out


def find_latest_smoke_run_log(client_id: str, *, run_id: str = "") -> Optional[Path]:
    """Prefer an explicit run_id log, else newest *.log under clients/{id}/smoke/runs/."""
    if not (client_id or "").strip():
        return None
    from agent.utils.client_paths import client_runs_dir

    runs = client_runs_dir(client_id.strip(), smoke=True)
    if not runs.is_dir():
        return None
    if run_id:
        cand = runs / f"{run_id}.log"
        if cand.is_file():
            return cand
    logs = sorted(
        (p for p in runs.glob("run_*.log") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return logs[0] if logs else None


def _ensure_seeds(campaign_id: str) -> Path:
    from agent.utils.campaign_config import load_campaign
    from agent.utils.icp_rules import stage_path
    from agent.utils.linkedin_company_seeds import persist_auto_linkedin_company_seed_sources
    from agent.utils.seed_sources import run_source
    from agent.utils.seeds import load_company_seeds_csv

    campaign = load_campaign(campaign_id)
    raw = stage_path(campaign, "raw_seeds")
    if raw.is_file() and raw.stat().st_size > 32:
        try:
            with raw.open(encoding="utf-8-sig", newline="") as f:
                rows = [r for r in csv.DictReader(f) if (r.get("company_name") or "").strip()]
            if rows:
                return raw
        except Exception:
            pass

    if campaign.seeds_csv.is_file():
        try:
            if load_company_seeds_csv(campaign.seeds_csv):
                return run_source(campaign_id, "csv_ingest", csv_path=str(campaign.seeds_csv))
        except Exception:
            pass

    seeds_cfg_path = campaign.campaign_dir / "seed_sources.json"
    sources: list[dict] = []
    if seeds_cfg_path.is_file():
        try:
            cfg = json.loads(seeds_cfg_path.read_text(encoding="utf-8"))
            sources = list(cfg.get("sources") or [])
        except (OSError, json.JSONDecodeError):
            sources = []

    # Never linkedin_jobs. Prefer company search, then maps, then csv.
    for prefer in ("linkedin_companies", "google_maps", "csv_ingest"):
        for entry in sources:
            if not isinstance(entry, dict) or entry.get("enabled") is False:
                continue
            sid = (entry.get("id") or entry.get("source_id") or entry.get("type") or "").strip()
            if sid != prefer:
                continue
            cfg_u = entry.get("config") if isinstance(entry.get("config"), dict) else {}
            if sid == "linkedin_companies":
                return run_source(
                    campaign_id,
                    "linkedin_companies",
                    queries=list(cfg_u.get("queries") or []),
                    urls=list(cfg_u.get("urls") or []),
                    max_results=int(cfg_u.get("max_results") or 80),
                    actor=cfg_u.get("actor"),
                    geo_segment=cfg_u.get("geo_segment") or "",
                    scraper_mode=cfg_u.get("scraper_mode") or "full",
                )
            if sid == "google_maps":
                return run_source(
                    campaign_id,
                    "google_maps",
                    queries=list(cfg_u.get("queries") or []),
                    max_results=int(cfg_u.get("max_results") or 80),
                    geo_segment=cfg_u.get("geo_segment") or "",
                    firmographics=cfg_u.get("firmographics", True),
                )
            if sid == "csv_ingest":
                csv_path = cfg_u.get("csv_path") or entry.get("csv_path") or str(campaign.seeds_csv)
                try:
                    if load_company_seeds_csv(csv_path):
                        return run_source(campaign_id, "csv_ingest", csv_path=csv_path)
                except Exception:
                    continue

    icp_data: dict[str, Any] = {}
    if campaign.icp_path.is_file():
        try:
            loaded = json.loads(campaign.icp_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                icp_data = loaded
        except (OSError, json.JSONDecodeError):
            icp_data = {}

    try:
        entry = persist_auto_linkedin_company_seed_sources(
            campaign.campaign_dir,
            campaign_id,
            icp_data,
            seed_count=80,
        )
    except ValueError as exc:
        raise FileNotFoundError(str(exc)) from exc

    cfg_u = entry.get("config") or {}
    log.info(
        "Auto LinkedIn company-search seed for %s — %d quer(y/ies)",
        campaign_id,
        len(cfg_u.get("queries") or []),
    )
    return run_source(
        campaign_id,
        "linkedin_companies",
        queries=list(cfg_u.get("queries") or []),
        urls=list(cfg_u.get("urls") or []),
        max_results=int(cfg_u.get("max_results") or 80),
        actor=cfg_u.get("actor"),
        geo_segment=cfg_u.get("geo_segment") or "",
        scraper_mode=cfg_u.get("scraper_mode") or "full",
    )


def _run_match_and_dedupe(campaign_id: str) -> dict[str, int]:
    """ICP match + dedupe + pre-Apollo. Returns stage counts."""
    from agent.utils.campaign_config import load_campaign
    from agent.utils.icp_rules import ensure_stages_dir, load_icp_rules
    from agent.utils.pre_apollo_gate import filter_pre_apollo
    from agent.utils.stage_artifacts import dedupe_seed_rows, read_stage_csv, write_stage_csv
    from scripts.match_campaign_icp import main as match_main
    import sys

    campaign = load_campaign(campaign_id)
    ensure_stages_dir(campaign)
    rules = load_icp_rules(campaign)

    old = sys.argv
    try:
        sys.argv = ["match_campaign_icp.py", "--campaign", campaign_id]
        rc = int(match_main() or 0)
    finally:
        sys.argv = old
    if rc != 0:
        raise RuntimeError(f"ICP match failed (exit {rc})")

    matched = read_stage_csv(campaign, "icp_matched")
    if not matched:
        raise RuntimeError("No ICP-matched seeds — check icp.json rules vs seed list")

    kept, dropped = dedupe_seed_rows(matched)
    fields = list(matched[0].keys()) if matched else []
    if "dedupe_reason" not in fields:
        fields = fields + ["dedupe_reason"]
    write_stage_csv(campaign, "deduped", kept, fields)

    pre_kept, pre_rejected = filter_pre_apollo(kept, rules, campaign.campaign_dir)
    if pre_rejected:
        rej_fields = list(fields)
        if "pre_apollo_reason" not in rej_fields:
            rej_fields.append("pre_apollo_reason")
        write_stage_csv(campaign, "pre_apollo_rejected", pre_rejected, rej_fields)
    kept = pre_kept
    write_stage_csv(campaign, "deduped", kept, fields)

    return {
        "matched": len(matched),
        "deduped": len(kept),
        "in_batch_dupes": len(dropped),
        "pre_apollo_rejected": len(pre_rejected),
    }


def _rows_to_seeds(rows: list[dict]) -> list:
    from agent.models import CompanySeed

    seeds = []
    for row in rows:
        name = (row.get("company_name") or "").strip()
        if not name:
            continue
        seeds.append(
            CompanySeed(
                company_name=name,
                website=(row.get("website") or "").strip() or None,
                industry=(row.get("industry") or "").strip() or None,
                location=(row.get("location") or "").strip() or None,
                company_size=(row.get("company_size") or "").strip() or None,
                source_url=(row.get("source_url") or "").strip() or None,
                notes=(row.get("notes") or "").strip() or None,
            )
        )
    return seeds


def ensure_run_payload(campaign_id: str) -> dict[str, Any]:
    """Load run_payload.json or synthesize one from campaign icp + client.json."""
    from agent.utils.campaign_config import load_campaign

    campaign = load_campaign(campaign_id)
    path = campaign.run_payload_path
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))

    icp_raw: dict[str, Any] = {}
    if campaign.icp_path.is_file():
        try:
            icp_raw = json.loads(campaign.icp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            icp_raw = {}

    meta: dict[str, Any] = {}
    camp_json = campaign.campaign_dir / "campaign.json"
    if camp_json.is_file():
        try:
            meta = json.loads(camp_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}

    client_id = (meta.get("client_id") or icp_raw.get("client_id") or "local").strip()
    client_name = (meta.get("client_name") or client_id).strip()
    client_path = Path(os.environ.get("DATA_ROOT", "data")) / "clients" / client_id / "client.json"
    # Prefer absolute data root next to campaign
    try:
        data_root = campaign.campaign_dir
        for _ in range(4):
            if (data_root / "clients").is_dir():
                break
            data_root = data_root.parent
        alt = data_root / "clients" / client_id / "client.json"
        if alt.is_file():
            client_path = alt
    except Exception:
        pass

    if client_path.is_file():
        try:
            cj = json.loads(client_path.read_text(encoding="utf-8"))
            client_name = (cj.get("client_name") or cj.get("name") or client_name).strip()
        except (OSError, json.JSONDecodeError):
            pass

    titles = list(icp_raw.get("job_titles") or [])
    locations = list(icp_raw.get("contact_locations") or [])
    if not locations and icp_raw.get("location"):
        locations = [icp_raw["location"]]

    payload = {
        "sender": {
            "client_id": client_id,
            "name": client_name,
            "business_description": (meta.get("brief") or "")[:500],
            "core_offer": (meta.get("brief") or "")[:200],
            "services": [],
            "proof_points": [],
            "target_pain_points": list(icp_raw.get("pain_points") or [])[:8],
            "positioning_notes": "",
        },
        "icp": {
            "industry": (icp_raw.get("industries") or [""])[0]
            if isinstance(icp_raw.get("industries"), list) and icp_raw.get("industries")
            else (icp_raw.get("industry") or ""),
            "location": icp_raw.get("location") or (locations[0] if locations else ""),
            "company_size_min": icp_raw.get("company_size_min") or icp_raw.get("size_min"),
            "company_size_max": icp_raw.get("company_size_max") or icp_raw.get("size_max"),
            "job_titles": titles,
            "contact_locations": locations,
            "prefilter_threshold": 30,
        },
        "mode": "curated_seeds",
        "campaign_id": campaign_id,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("Synthesized run_payload.json for %s", campaign_id)
    return payload


def _run_paid_slice(
    campaign_id: str,
    *,
    seeds: list,
    max_leads: int,
    client_suffix: str,
    sheet_label: str,
    keyword: str,
) -> str:
    """Run Apollo+enrich pipeline on a seed slice. Returns run_id."""
    from agent.models import ICPProfile, InputMode, RunRequest, SenderProfile
    from agent.pipeline import run_pipeline
    from agent.utils import run_tracker
    from agent.utils.campaign_config import load_campaign
    from agent.utils.client_paths import is_smoke_run
    from agent.utils.enrichment_plan import load_plan
    from agent.utils.icp_rules import load_icp_rules
    from main import _client_from_request

    campaign = load_campaign(campaign_id)
    payload = ensure_run_payload(campaign_id)
    sender = SenderProfile(**payload["sender"])
    sender.client_id = f"{sender.client_id}{client_suffix}"

    rules = load_icp_rules(campaign)
    icp_data = dict(payload.get("icp") or {})
    if rules.job_titles:
        icp_data["job_titles"] = rules.job_titles
    if rules.contact_locations:
        icp_data["contact_locations"] = rules.contact_locations
    icp = ICPProfile(**icp_data)

    plan = load_plan(campaign.campaign_dir, campaign_id)
    request = RunRequest(
        mode=InputMode.CURATED_SEEDS,
        max_leads=max_leads,
        sender=sender,
        icp=icp,
        company_seeds=seeds,
        campaign_id=campaign_id,
        output_sheet_name=sheet_label,
    )
    client = _client_from_request(request)
    run_state = run_tracker.create_run(
        client_id=client.client_id,
        input_mode=InputMode.CURATED_SEEDS,
        max_leads=max_leads,
        keyword=keyword,
        campaign_id=campaign_id,
    )
    prev_subdir = os.environ.get("LEADGEN_RUNS_SUBDIR")
    if is_smoke_run(keyword=keyword):
        os.environ["LEADGEN_RUNS_SUBDIR"] = "smoke"
    try:
        with apply_plan_runtime(plan):
            asyncio.run(run_pipeline(run_state.run_id, request, client))
    finally:
        if prev_subdir is None:
            os.environ.pop("LEADGEN_RUNS_SUBDIR", None)
        else:
            os.environ["LEADGEN_RUNS_SUBDIR"] = prev_subdir
    return run_state.run_id


def assert_can_start_smoke_gate(campaign_id: str) -> None:
    """Raise FileNotFoundError / RuntimeError / ValueError if gated run cannot start."""
    from agent.utils.campaign_config import load_campaign
    from agent.utils.icp_readiness import assert_icp_ready_for_run
    from agent.utils.icp_rules import stage_path
    from agent.utils.linkedin_company_seeds import can_auto_seed_linkedin_companies_from_icp
    from agent.utils.seeds import load_company_seeds_csv

    campaign = load_campaign(campaign_id)
    if not campaign.icp_path.is_file():
        raise FileNotFoundError(f"Missing icp.json for {campaign_id}")

    try:
        icp_data = json.loads(campaign.icp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FileNotFoundError(f"Could not read icp.json for {campaign_id}: {exc}") from exc

    meta: dict[str, Any] = {}
    camp_json = campaign.campaign_dir / "campaign.json"
    if camp_json.is_file():
        try:
            meta = json.loads(camp_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    ctype = (meta.get("campaign_type") if isinstance(meta, dict) else None) or "company_outreach"
    try:
        from agent.utils.enrichment_plan import load_plan

        plan = load_plan(campaign.campaign_dir, campaign_id)
        plan_ctype = (plan.get("campaign_type") or "").strip()
        if plan_ctype:
            ctype = plan_ctype
    except Exception:
        pass
    assert_icp_ready_for_run(icp_data if isinstance(icp_data, dict) else {}, campaign_type=ctype)

    if ctype == "talent_search":
        from agent.utils.talent_list_run import assert_talent_seeds_ready

        assert_talent_seeds_ready(campaign)
        return

    raw = stage_path(campaign, "raw_seeds")
    if raw.is_file() and raw.stat().st_size > 32:
        try:
            with raw.open(encoding="utf-8-sig", newline="") as f:
                rows = [r for r in csv.DictReader(f) if (r.get("company_name") or "").strip()]
            if rows:
                return
        except Exception:
            pass

    if campaign.seeds_csv.is_file():
        try:
            if load_company_seeds_csv(campaign.seeds_csv):
                return
        except Exception:
            pass

    seeds_cfg_path = campaign.campaign_dir / "seed_sources.json"
    if seeds_cfg_path.is_file():
        try:
            cfg = json.loads(seeds_cfg_path.read_text(encoding="utf-8"))
            sources = list(cfg.get("sources") or [])
        except (OSError, json.JSONDecodeError):
            sources = []
        for entry in sources:
            if not isinstance(entry, dict) or entry.get("enabled") is False:
                continue
            sid = (entry.get("id") or entry.get("source_id") or entry.get("type") or "").strip()
            if sid == "linkedin_jobs":
                continue  # never count jobs as a valid seed path
            if sid == "linkedin_companies":
                cfg_u = entry.get("config") or {}
                if cfg_u.get("queries") or cfg_u.get("urls"):
                    return
            if sid == "google_maps":
                cfg_u = entry.get("config") or {}
                if cfg_u.get("queries") or cfg_u.get("keyword"):
                    return
            if sid == "csv_ingest":
                continue

    if can_auto_seed_linkedin_companies_from_icp(icp_data if isinstance(icp_data, dict) else {}):
        return

    raise FileNotFoundError(
        "No seeds yet and ICP cannot auto-build LinkedIn company-search URLs. "
        "Add seeds.csv (company_name, website), configure linkedin_companies, "
        "or ensure the ICP has include_keywords/industries plus geo."
    )


def start_smoke_gate(
    campaign_dir: Path,
    campaign_id: str,
    *,
    smoke_leads: int = 2,
    target_leads: int = 50,
) -> dict[str, Any]:
    """
    Synchronous gated run through paid smoke, then stop at awaiting_approval.
    Intended for BackgroundTasks / CLI.
    """
    from agent.utils.talent_list_run import resolve_campaign_type, start_talent_smoke_gate

    if resolve_campaign_type(campaign_dir, campaign_id) == "talent_search":
        return start_talent_smoke_gate(
            campaign_dir,
            campaign_id,
            smoke_leads=smoke_leads,
            target_leads=target_leads,
        )

    smoke_leads = max(1, min(int(smoke_leads or 2), 10))
    target_leads = max(smoke_leads, int(target_leads or 50))

    state = update_list_run(
        campaign_dir,
        campaign_id=campaign_id,
        phase="seeding",
        smoke_leads=smoke_leads,
        target_leads=target_leads,
        message="Building seed list…",
        error="",
        smoke_run_id="",
        full_run_id="",
        counts={},
    )

    try:
        _ensure_seeds(campaign_id)
        update_list_run(
            campaign_dir,
            phase="matching",
            message="ICP match + dedupe + pre-Apollo gate…",
        )
        counts = _run_match_and_dedupe(campaign_id)

        from agent.utils.campaign_config import load_campaign
        from agent.utils.stage_artifacts import read_stage_csv

        campaign = load_campaign(campaign_id)
        deduped = read_stage_csv(campaign, "deduped")
        if not deduped:
            raise RuntimeError("Deduped stage is empty — nothing to smoke-test")

        smoke_n = min(smoke_leads, len(deduped))
        smoke_rows = deduped[:smoke_n]
        seeds = _rows_to_seeds(smoke_rows)
        if not seeds:
            raise RuntimeError("Could not build smoke seeds from deduped rows")

        update_list_run(
            campaign_dir,
            phase="smoking",
            message=f"Paid smoke on {len(seeds)} lead(s) (Apollo + enrichment)…",
            counts=counts,
        )
        run_id = _run_paid_slice(
            campaign_id,
            seeds=seeds,
            max_leads=len(seeds),
            client_suffix="",  # same client as campaign — shows on Decoracel dashboard
            sheet_label=f"SMOKE — {campaign_id}",
            keyword=f"smoke-gate:{campaign_id}",
        )
        contactable_n = 0
        smoke_csv = ""
        smoke_preview: list[dict[str, str]] = []
        try:
            from agent.utils import run_tracker
            import shutil

            final = run_tracker.get_run(run_id)
            csv_path = final.csv_path if final else None
            if csv_path and Path(csv_path).is_file():
                smoke_csv = str(csv_path)
                with Path(csv_path).open(encoding="utf-8", newline="") as f:
                    contactable_n = sum(
                        1 for r in csv.DictReader(f)
                        if (r.get("decision_maker_email") or "").strip()
                    )
                smoke_preview = _smoke_leads_preview(Path(csv_path))
                # Mirror into campaign stages so seed list + smoke sit together
                stages = campaign.campaign_dir / "stages"
                stages.mkdir(parents=True, exist_ok=True)
                dest = stages / "04_smoke_contactable.csv"
                shutil.copy2(csv_path, dest)
                smoke_csv = str(dest)
        except Exception:
            contactable_n = 0
        if not smoke_preview:
            # Zero-contactable smoke still needs a reviewable list of who was tested
            smoke_preview = _smoke_preview_from_rows(smoke_rows)
        seed_preview = _seed_preview_rows(campaign_id)
        seed_n = _count_seed_rows(campaign_id) or len(seed_preview) or counts.get("matched") or 0
        counts = {
            **counts,
            "seeds": seed_n,
            "smoke_seeds": len(seeds),
            "smoke_contactable": contactable_n,
        }
        state = update_list_run(
            campaign_dir,
            phase="awaiting_approval",
            message=(
                f"Smoke complete: {contactable_n} contactable of {len(seeds)} companies smoked. "
                f"{seed_n} seeds on disk. Review results, then approve the full batch."
            ),
            smoke_run_id=run_id,
            smoke_csv_path=smoke_csv,
            seed_preview=seed_preview,
            smoke_preview=smoke_preview,
            counts=counts,
            error="",
        )
        # Keep cost dashboard tiles in sync without a manual rebuild
        try:
            from agent.dashboard.routes import rebuild_dashboard_files

            rebuild_dashboard_files(Path("data"))
        except Exception:
            log.debug("dashboard rebuild after smoke skipped", exc_info=True)
        return state
    except Exception as exc:
        log.exception("Smoke gate failed for %s", campaign_id)
        return update_list_run(
            campaign_dir,
            phase="error",
            message="Gated run failed",
            error=str(exc),
        )


def _mirror_run_csv_to_stages(
    campaign_dir: Path,
    run_id: str,
    *,
    csv_path: str | Path | None = None,
) -> dict[str, str]:
    """Copy delivered run CSV into campaign stages so the portal can list leads.

    Gated smoke/full-batch write under data/clients/{id}/runs/ (or smoke/runs/),
    but the dashboard lead viewer only reads campaign stages/*.csv.
    """
    import shutil

    from agent.utils.campaign_config import (
        STAGE_APOLLO_CONTACTABLE,
        STAGE_ENRICHED,
        STAGE_SCORED,
    )

    src: Optional[Path] = Path(csv_path) if csv_path else None
    if src is None or not src.is_file():
        # Prefer run_tracker path, then client runs folders
        try:
            from agent.utils import run_tracker

            final = run_tracker.get_run(run_id)
            if final and final.csv_path:
                cand = Path(final.csv_path)
                if cand.is_file():
                    src = cand
        except Exception:
            pass
    if src is None or not src.is_file():
        meta = {}
        try:
            meta = json.loads((campaign_dir / "campaign.json").read_text(encoding="utf-8-sig"))
        except Exception:
            meta = {}
        cid = (meta.get("client_id") or "").strip()
        candidates: list[Path] = []
        if cid:
            from agent.utils.client_paths import client_runs_dir

            candidates.append(client_runs_dir(cid, smoke=False) / f"{run_id}.csv")
            candidates.append(client_runs_dir(cid, smoke=True) / f"{run_id}.csv")
            candidates.append(Path("data") / "clients" / cid / "runs" / f"{run_id}.csv")
        for cand in candidates:
            if cand.is_file():
                src = cand
                break
    if src is None or not src.is_file():
        return {}

    stages = campaign_dir / "stages"
    stages.mkdir(parents=True, exist_ok=True)
    # Enrichment+score columns present → score/enriched; always keep contactable for funnel tiles
    dest_contactable = stages / STAGE_APOLLO_CONTACTABLE
    shutil.copy2(src, dest_contactable)
    out = {"contactable": str(dest_contactable)}
    try:
        with src.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        has_score = any((r.get("lead_score") or r.get("score_method") or "").strip() for r in rows)
        has_enrich = any(
            (r.get("website_summary") or r.get("enrichment_status") or "").strip() for r in rows
        )
        if has_score:
            dest = stages / STAGE_SCORED
            shutil.copy2(src, dest)
            out["scored"] = str(dest)
        if has_enrich:
            dest = stages / STAGE_ENRICHED
            shutil.copy2(src, dest)
            out["enriched"] = str(dest)
    except Exception:
        pass
    return out


def _record_gated_run_cost(
    campaign_dir: Path,
    *,
    campaign_id: str,
    run_id: str,
    phase: str,
    label: str,
) -> None:
    """Append a cost_runs.json row from run summary / CostTracker if available."""
    from datetime import datetime, timezone

    from agent.utils.cost_manifest import append_manifest_run, load_manifest

    meta: dict[str, Any] = {}
    try:
        meta = json.loads((campaign_dir / "campaign.json").read_text(encoding="utf-8-sig"))
    except Exception:
        meta = {}
    client_id = (meta.get("client_id") or "").strip() or "unknown"

    summary: dict[str, Any] = {}
    try:
        from agent.utils.client_paths import client_runs_dir

        for smoke in (False, True):
            p = client_runs_dir(client_id, smoke=smoke) / f"{run_id}_summary.json"
            if p.is_file():
                summary = json.loads(p.read_text(encoding="utf-8-sig"))
                break
    except Exception:
        summary = {}

    total = summary.get("total_cost_usd")
    llm_tokens = summary.get("llm_tokens_used")
    apollo_credits = summary.get("apollo_credits_used")
    if total is None and llm_tokens is None and apollo_credits is None:
        return

    def _aware(ts: Any) -> Optional[str]:
        if not ts:
            return None
        s = str(ts).strip()
        if not s:
            return None
        if s.endswith("Z") or "+" in s[10:] or s.endswith("+00:00"):
            return s
        return s + "+00:00"

    total_f = float(total) if total is not None else None
    llm_usd = None
    try:
        if llm_tokens:
            llm_usd = round(float(llm_tokens) / 1_000_000.0 * 0.15, 4)
    except Exception:
        llm_usd = None

    # Attribute unexplained remainder to Apify (seed + enrichment actors) so portal
    # spend is not understated when CostTracker only logged LLM.
    apify_usd = None
    if total_f is not None:
        apify_usd = round(max(0.0, total_f - (llm_usd or 0.0)), 4)

    entry: dict[str, Any] = {
        "run_key": f"{phase}_{run_id}",
        "label": label,
        "stage": "enrichment" if ("enrich" in phase or "full" in phase) else "seed",
        "run_id": run_id,
        "started_at_utc": _aware(summary.get("started_at")),
        "ended_at_utc": _aware(summary.get("completed_at")),
        "outcome": {
            "qualified_count": summary.get("qualified_count"),
            "partial_count": summary.get("partial_count"),
            "status": summary.get("status"),
            "contactable": summary.get("qualified_count"),
        },
        "cost_tracker": {
            "total_usd": total_f,
            "llm_tokens": llm_tokens,
            "llm_usd": llm_usd,
            "apollo_credits": apollo_credits,
            "apify_usd": apify_usd,
            "source": "run_summary.json (gated list_run)",
        },
    }
    if llm_usd is not None or llm_tokens:
        entry["llm"] = {
            "tokens": llm_tokens,
            "usd": llm_usd,
            "basis": "run_summary_est",
            "source": "gated list_run summary",
        }
    if apify_usd:
        entry["apify"] = {
            "cost_tracker_usd": apify_usd,
            "api_truth_usd": apify_usd,
            "drift_usd": 0,
            "source": "run_summary residual (seed+enrich Apify est.)",
        }

    load_manifest(campaign_dir, campaign_id)  # ensure file exists
    append_manifest_run(campaign_dir, entry, campaign_id=campaign_id)


def approve_full_batch(campaign_dir: Path, campaign_id: str) -> dict[str, Any]:
    """Run remaining deduped companies up to target_leads after smoke approval."""
    from agent.utils.talent_list_run import approve_talent_full_batch, resolve_campaign_type

    if resolve_campaign_type(campaign_dir, campaign_id) == "talent_search":
        return approve_talent_full_batch(campaign_dir, campaign_id)

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
        message=f"Full batch up to {target_leads} contactable leads…",
        error="",
    )

    try:
        from agent.utils.campaign_config import load_campaign
        from agent.utils.stage_artifacts import read_stage_csv

        campaign = load_campaign(campaign_id)
        deduped = read_stage_csv(campaign, "deduped")
        if not deduped:
            raise RuntimeError("No 03_deduped.csv — re-run the smoke gate")

        # Prefer companies after the smoke slice; if fewer remain, use all up to target.
        remaining = deduped[smoke_leads:] if len(deduped) > smoke_leads else deduped
        seeds = _rows_to_seeds(remaining)
        if not seeds:
            seeds = _rows_to_seeds(deduped)
        if not seeds:
            raise RuntimeError("No company seeds for full batch")

        run_id = _run_paid_slice(
            campaign_id,
            seeds=seeds,
            max_leads=target_leads,
            client_suffix="",
            sheet_label=f"{campaign_id} — full batch",
            keyword=f"full-batch:{campaign_id}",
        )
        mirrored = _mirror_run_csv_to_stages(campaign_dir, run_id)
        delivered_n = 0
        sheet_url = ""
        try:
            from agent.utils import run_tracker

            final = run_tracker.get_run(run_id)
            if final and getattr(final, "sheet_url", None):
                sheet_url = str(final.sheet_url or "")
        except Exception:
            sheet_url = ""
        try:
            contactable = Path(mirrored["contactable"]) if mirrored.get("contactable") else None
            if contactable and contactable.is_file():
                with contactable.open(encoding="utf-8-sig", newline="") as f:
                    delivered_n = sum(
                        1 for r in csv.DictReader(f)
                        if (r.get("decision_maker_email") or "").strip()
                    )
        except Exception:
            delivered_n = 0
        counts = dict(state.get("counts") or {})
        counts["full_seeds"] = len(seeds)
        counts["full_contactable"] = delivered_n
        state = update_list_run(
            campaign_dir,
            phase="completed",
            message=f"Full batch finished — {delivered_n} contactable lead(s) on disk.",
            full_run_id=run_id,
            sheet_url=sheet_url,
            counts=counts,
            error="",
        )
        # Best-effort: record pipeline CostTracker totals into cost_runs for the portal
        try:
            _record_gated_run_cost(
                campaign_dir,
                campaign_id=campaign_id,
                run_id=run_id,
                phase="full_batch_enrichment",
                label=f"Full batch — {campaign_id}",
            )
        except Exception:
            log.debug("cost manifest append after full batch skipped", exc_info=True)
        try:
            from agent.dashboard.routes import rebuild_dashboard_files

            rebuild_dashboard_files(Path("data"))
        except Exception:
            log.debug("dashboard rebuild after full batch skipped", exc_info=True)
        return state
    except Exception as exc:
        log.exception("Full batch failed for %s", campaign_id)
        return update_list_run(
            campaign_dir,
            phase="error",
            message="Full batch failed",
            error=str(exc),
        )
