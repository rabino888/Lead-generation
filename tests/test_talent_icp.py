"""Talent T03: person ICP match on seed fields (no LLM)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from agent.utils.talent_icp import (
    apply_talent_icp,
    load_talent_icp_rules,
    match_person,
    rules_from_icp_dict,
    run_talent_icp_stage,
)
from agent.utils.talent_people import (
    STAGE_T02_DEDUPED,
    STAGE_T03_ICP_MATCHED,
    STAGE_T03_ICP_REJECTED,
    T01_FIELDS,
    write_people_csv,
)


def _spec_icp() -> dict:
    return {
        "campaign_type": "talent_search",
        "job_titles": [
            "Senior Backend Engineer",
            "Staff Backend Engineer",
            "Backend Engineer",
        ],
        "contact_locations": ["Spain"],
        "keywords": ["Python", "distributed systems", "PostgreSQL"],
        "excluded_keywords": ["recruitment", "staffing", "bootcamp"],
        "person_match": {
            "require_title_signal": True,
            "min_keyword_hits": 1,
        },
    }


def _row(**kwargs) -> dict:
    base = {k: "" for k in T01_FIELDS}
    base.update(
        {
            "person_id": "p-1",
            "linkedin_url": "https://www.linkedin.com/in/ada",
            "full_name": "Ada Lovelace",
            "title": "Backend Engineer",
            "headline": "Backend Engineer | Python",
            "location": "Madrid, Spain",
            "company_name": "Acme",
        }
    )
    base.update(kwargs)
    return base


def test_pass_spec_style_seed():
    rules = rules_from_icp_dict(_spec_icp())
    ok, reasons = match_person(_row(), rules)
    assert ok is True
    assert reasons == ["icp_match"]


def test_reject_excluded_keyword():
    rules = rules_from_icp_dict(_spec_icp())
    ok, reasons = match_person(
        _row(
            title="Backend Engineer",
            headline="Backend Engineer · bootcamp mentor · Python",
            location="Spain",
        ),
        rules,
    )
    assert ok is False
    assert reasons[0].startswith("excluded_keyword:")


def test_reject_missing_title_signal():
    rules = rules_from_icp_dict(_spec_icp())
    ok, reasons = match_person(
        _row(title="Product Designer", headline="Figma lover", location="Spain"),
        rules,
    )
    assert ok is False
    assert "missing_title_signal" in reasons


def test_reject_location_mismatch():
    rules = rules_from_icp_dict(_spec_icp())
    ok, reasons = match_person(
        _row(location="Berlin, Germany"),
        rules,
    )
    assert ok is False
    assert "location_mismatch" in reasons


def test_reject_missing_keyword_hits():
    rules = rules_from_icp_dict(_spec_icp())
    ok, reasons = match_person(
        _row(headline="Backend Engineer", title="Backend Engineer", notes=""),
        rules,
    )
    # title has Backend Engineer but no Python/PostgreSQL/distributed systems
    assert ok is False
    assert reasons[0].startswith("missing_keyword_hits:")


def test_include_exclude_keyword_aliases():
    rules = rules_from_icp_dict(
        {
            "job_titles": ["Engineer"],
            "include_keywords": ["Rust"],
            "exclude_keywords": ["intern"],
            "contact_locations": ["Spain"],
            "person_match": {"require_title_signal": True, "min_keyword_hits": 1},
        }
    )
    ok, _ = match_person(
        _row(title="Engineer", headline="Rust Engineer", location="Spain"),
        rules,
    )
    assert ok is True
    ok2, reasons = match_person(
        _row(title="Engineer intern", headline="Rust", location="Spain"),
        rules,
    )
    assert ok2 is False
    assert "excluded_keyword:intern" in reasons[0]


def test_skills_as_title_signal_when_no_titles():
    rules = rules_from_icp_dict(
        {
            "person_match": {
                "skills": ["Kubernetes"],
                "require_title_signal": True,
                "min_keyword_hits": 0,
            },
            "contact_locations": ["Spain"],
            "require_location_match": True,
        }
    )
    ok, _ = match_person(
        _row(title="", headline="Platform | Kubernetes", location="Valencia, Spain"),
        rules,
    )
    assert ok is True


def test_recruiter_signal_rejected():
    rules = rules_from_icp_dict(
        {
            "job_titles": ["Engineer"],
            "person_match": {"require_title_signal": False, "min_keyword_hits": 0},
            "require_location_match": False,
        }
    )
    ok, reasons = match_person(
        _row(title="Engineer", company_name="Acme Staffing Partners"),
        rules,
    )
    assert ok is False
    assert "recruiter_or_staffing_signal" in reasons


def test_apply_and_stage_cli_artifacts(tmp_path: Path):
    camp = tmp_path / "talent_icp_camp"
    stages = camp / "stages"
    stages.mkdir(parents=True)
    (camp / "icp.json").write_text(json.dumps(_spec_icp()), encoding="utf-8")

    rows = [
        _row(
            person_id="p-ok",
            linkedin_url="https://www.linkedin.com/in/ok",
            title="Senior Backend Engineer",
            headline="Python · PostgreSQL",
            location="Barcelona, Spain",
        ),
        _row(
            person_id="p-bad-geo",
            linkedin_url="https://www.linkedin.com/in/bad-geo",
            title="Backend Engineer",
            headline="Python",
            location="Austin, TX",
        ),
        _row(
            person_id="p-boot",
            linkedin_url="https://www.linkedin.com/in/boot",
            title="Backend Engineer",
            headline="bootcamp mentor · Python",
            location="Spain",
        ),
    ]
    write_people_csv(stages / STAGE_T02_DEDUPED, rows)

    rules = load_talent_icp_rules(camp)
    matched, rejected = apply_talent_icp(rows, rules)
    assert len(matched) == 1
    assert matched[0]["person_id"] == "p-ok"
    assert {r["reject_reason"] for r in rejected} >= {
        "location_mismatch",
        "excluded_keyword:bootcamp",
    }

    m_path, r_path, n_ok, n_rej = run_talent_icp_stage(camp)
    assert m_path.name == STAGE_T03_ICP_MATCHED
    assert r_path.name == STAGE_T03_ICP_REJECTED
    assert n_ok == 1
    assert n_rej == 2

    with r_path.open(encoding="utf-8", newline="") as f:
        rej_rows = list(csv.DictReader(f))
    assert all(r.get("reject_reason") for r in rej_rows)
