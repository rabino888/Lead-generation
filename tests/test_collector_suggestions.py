"""Collector suggestion validation, accept merge, and the default website prompt."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from types import SimpleNamespace

from agent.models import WebsiteAnalysis
from agent.stages.website import build_outreach_prompt
from agent.utils.collector_suggestions import (
    accept_suggestions,
    apply_jobs_extracts,
    build_collector_prompt,
    label_hiring_function,
    load_collector_context,
    merge_generated_suggestions,
    reject_suggestions,
    validate_suggestion_document,
    why_drop_reason,
    write_suggestions,
)
from agent.utils.enrichment_plan import default_plan, load_plan, save_plan
from agent.utils.website_outreach import website_analysis_to_csv

ROOT = Path(__file__).resolve().parents[1]
GOLEM = ROOT / "data" / "clients" / "golemui" / "campaigns" / "in_progress" / "golemui_insurance_uk"
PITCH = (
    "I rebuilt [Insurer]'s [product] quote form as a single JSON file — "
    "open it, change a question, tell me what you think."
)


def _row(**overrides):
    base = {
        "id": "public_quote_form",
        "kind": "website_extract",
        "parent_module": "website_scrape",
        "columns": ["quote_form_url"],
        "page_hints": ["quote"],
        "already_collected": False,
        "existing_fields": [],
        "why": "The offer builds one public quote journey, so the crawl should record the form URL.",
        "cost_note": "No extra vendor call.",
        "paid_extra": False,
    }
    base.update(overrides)
    return base


def test_validation_drops_unknown_and_unsafe_rows():
    hints = [f"hint{i}" for i in range(7)]
    suggestions = [
        _row(),
        _row(id="bad_kind", kind="email_copy"),
        _row(id="bad_parent", parent_module="apollo_email"),
        _row(id="bad_column", columns=["Quote-URL"]),
        _row(id="paid_missing", kind="module", parent_module="apollo_phone", columns=[], paid_extra=True, cost_note=""),
        _row(id="subject_line", why="Re: your quote form"),
        _row(id="short_subject", why="Your quote form rebuilt"),
        _row(id="pitch_copy", why=PITCH),
        _row(id="many_hints", page_hints=hints),
    ]
    suggestions.extend(
        _row(
            id=f"extra_{i}",
            columns=[f"col_{i}"],
            why=f"The offer needs column {i} recorded from the public site.",
        )
        for i in range(6)
    )
    leave_off = [
        {"module": name, "reason": "Not used by this offer.", "cost_note": "Left off."}
        for name in (
            "apollo_phone",
            "facebook_company_posts",
            "instagram_company_posts",
            "meta_ads",
            "linkedin_company_profile",
        )
    ]
    cleaned = validate_suggestion_document(
        {"suggestions": suggestions, "leave_off": leave_off},
        pitch=PITCH,
    )
    ids = [row["id"] for row in cleaned["suggestions"]]
    assert "bad_kind" not in ids
    assert "bad_parent" not in ids
    assert "bad_column" not in ids
    assert "paid_missing" not in ids
    assert "subject_line" not in ids
    assert "short_subject" not in ids
    assert "pitch_copy" not in ids
    assert len(ids) == 6
    hinted = next(row for row in cleaned["suggestions"] if row["id"] == "many_hints")
    assert len(hinted["page_hints"]) == 5
    assert len(cleaned["leave_off"]) == 4
    assert why_drop_reason(
        "The offer builds one public quote journey, so the crawl should record the form URL.",
        PITCH,
    ) is None


def test_merge_keeps_accepted_and_rejected():
    existing = {
        "suggestions": [
            _row(id="kept_yes", status="accepted", why="The offer needs the accepted column."),
            _row(id="kept_no", status="rejected", why="The offer does not need this rejected column."),
            _row(id="still_open", status="proposed", why="Old proposed sentence about the offer."),
        ]
    }
    fresh = validate_suggestion_document(
        {
            "suggestions": [
                _row(id="kept_yes", why="A regenerated sentence that must not replace the accepted row."),
                _row(id="still_open", why="The offer still needs this proposed column from the new pass."),
                _row(id="brand_new", why="The magnet needs one new column from the public page."),
            ],
            "leave_off": [
                {"module": "apollo_phone", "reason": "The magnet is a link.", "cost_note": "About one credit."}
            ],
        }
    )
    merged = merge_generated_suggestions(existing, fresh)
    by_id = {row["id"]: row for row in merged["suggestions"]}
    assert by_id["kept_yes"]["status"] == "accepted"
    assert "accepted column" in by_id["kept_yes"]["why"]
    assert by_id["kept_no"]["status"] == "rejected"
    assert by_id["still_open"]["status"] == "proposed"
    assert "new pass" in by_id["still_open"]["why"]
    assert by_id["brand_new"]["status"] == "proposed"
    assert merged["leave_off"][0]["module"] == "apollo_phone"


def test_accept_merges_extracts_and_reject_leaves_plan(tmp_path: Path):
    camp = tmp_path / "camp"
    camp.mkdir()
    plan = default_plan("camp")
    plan["modules"]["website_scrape"] = False
    plan["modules"]["linkedin_company_jobs"] = False
    plan["modules"]["apollo_phone"] = False
    saved = save_plan(camp, plan)
    assert "extracts" not in saved
    write_suggestions(
        camp,
        {
            "campaign_id": "camp",
            "suggestions": [
                _row(id="turn_phone", kind="module", parent_module="apollo_phone", columns=[]),
                _row(),
                _row(
                    id="hiring_function",
                    kind="jobs_extract",
                    parent_module="linkedin_company_jobs",
                    columns=["hiring_function"],
                    why="The offer is for in-house digital teams, so titles already stored should be labeled.",
                ),
                _row(
                    id="open_role_titles",
                    kind="jobs_extract",
                    parent_module="linkedin_company_jobs",
                    columns=[],
                    already_collected=True,
                    existing_fields=["website_open_roles", "company_open_jobs_summary"],
                    why="Open role titles are already stored in website_open_roles and company_open_jobs_summary.",
                ),
            ],
            "leave_off": [],
        },
    )

    accept_suggestions(camp, ["open_role_titles"])
    after_noop = load_plan(camp, "camp")
    assert after_noop["updated_at_utc"] == saved["updated_at_utc"]
    assert "extracts" not in after_noop
    assert after_noop["modules"]["website_scrape"] is False
    assert after_noop["modules"]["linkedin_company_jobs"] is False

    reject_suggestions(camp, ["hiring_function"])
    after_reject = load_plan(camp, "camp")
    assert after_reject["updated_at_utc"] == saved["updated_at_utc"]
    assert "extracts" not in after_reject
    doc = json.loads((camp / "collector_suggestions.json").read_text(encoding="utf-8"))
    statuses = {row["id"]: row.get("status") for row in doc["suggestions"]}
    assert statuses["hiring_function"] == "rejected"
    assert statuses["open_role_titles"] == "accepted"
    assert statuses["public_quote_form"] is None

    accept_suggestions(camp, ["public_quote_form", "turn_phone"])
    merged = load_plan(camp, "camp")
    assert merged["modules"]["website_scrape"] is True
    assert merged["modules"]["apollo_phone"] is True
    assert merged["modules"]["linkedin_company_jobs"] is False
    assert [row["id"] for row in merged["extracts"]] == ["public_quote_form"]
    assert merged["extracts"][0]["columns"] == ["quote_form_url"]
    assert merged["extracts"][0]["parent_module"] == "website_scrape"

    accept_suggestions(camp, ["public_quote_form"])
    again = load_plan(camp, "camp")
    assert [row["id"] for row in again["extracts"]] == ["public_quote_form"]


def test_plan_without_extracts_is_unchanged(tmp_path: Path):
    camp = tmp_path / "plain"
    camp.mkdir()
    saved = save_plan(camp, default_plan("plain"))
    assert "extracts" not in saved
    raw = json.loads((camp / "enrichment_plan.json").read_text(encoding="utf-8"))
    assert "extracts" not in raw
    loaded = load_plan(camp, "plain")
    assert "extracts" not in loaded


def test_unknown_extract_ids_are_ignored(tmp_path: Path):
    camp = tmp_path / "extracts"
    camp.mkdir()
    plan = default_plan("extracts")
    plan["extracts"] = [
        {"id": "Quote-Form", "kind": "website_extract", "parent_module": "website_scrape", "columns": ["quote_form_url"]},
        {"kind": "website_extract", "parent_module": "website_scrape", "columns": ["quote_form_url"]},
        {"id": "nope", "kind": "module", "parent_module": "website_scrape", "columns": ["quote_form_url"]},
        {"id": "wrong_parent", "kind": "website_extract", "parent_module": "apollo_email", "columns": ["quote_form_url"]},
        {
            "id": "public_quote_form",
            "kind": "website_extract",
            "parent_module": "website_scrape",
            "columns": ["quote_form_url", "Bad"],
            "page_hints": ["quote", "h2", "h3", "h4", "h5", "h6"],
            "instruction": "Record the public quote form URL.",
        },
    ]
    saved = save_plan(camp, plan)
    assert [row["id"] for row in saved["extracts"]] == ["public_quote_form"]
    assert saved["extracts"][0]["columns"] == ["quote_form_url"]
    assert saved["extracts"][0]["page_hints"] == ["quote", "h2", "h3", "h4", "h5"]


def test_default_website_prompt_unchanged_without_extracts():
    plain = build_outreach_prompt(company_name="Acme", content="Hello", dm_name=None, extracts=None)
    empty = build_outreach_prompt(company_name="Acme", content="Hello", dm_name=None, extracts=[])
    jobs_only = build_outreach_prompt(
        company_name="Acme",
        content="Hello",
        extracts=[
            {
                "kind": "jobs_extract",
                "columns": ["hiring_function"],
                "instruction": "Bucket titles.",
            }
        ],
    )
    assert plain == empty == jobs_only
    assert plain.rstrip().endswith(
        "- about_summary should come from About / Team pages, not the homepage nav."
    )
    assert "- Do not extract tech stack, SEO, chatbot, or testimonials." in plain
    assert "quote_form_url" not in plain
    assert '"tech_stack"' not in plain

    with_extract = build_outreach_prompt(
        company_name="Acme",
        content="Hello",
        extracts=[
            {
                "kind": "website_extract",
                "columns": ["quote_form_url", "tech_stack"],
                "page_hints": ["quote"],
                "instruction": "Record the public quote form URL and any named framework.",
            }
        ],
    )
    assert with_extract != plain
    assert '"quote_form_url": null' in with_extract
    assert "Record the public quote form URL and any named framework." in with_extract
    assert "Pages to check: quote." in with_extract
    assert "- Do not extract tech stack, SEO, chatbot, or testimonials." in with_extract


def test_hiring_function_buckets_existing_titles():
    assert label_hiring_function(["Senior Frontend Engineer"]) == "frontend"
    assert label_hiring_function(["Claims Handler", "Backend Engineer"]) == (
        "engineering; claims_or_underwriting"
    )
    assert label_hiring_function(["Head of Digital", "Office Manager"]) == "digital; other"
    assert label_hiring_function([]) == ""
    analysis = SimpleNamespace(
        website_open_roles=["React Engineer"],
        collector_extracts={},
    )
    apply_jobs_extracts(
        analysis,
        website_open_roles=analysis.website_open_roles,
        company_open_jobs_summary="Claims Handler",
        extracts=[{"kind": "jobs_extract", "columns": ["hiring_function"]}],
    )
    assert analysis.collector_extracts["hiring_function"] == "frontend; claims_or_underwriting"
    untouched = WebsiteAnalysis(website_summary="Acme builds robots.")
    row = website_analysis_to_csv(untouched)
    assert "hiring_function" not in row
    assert "tech_stack" not in row


@pytest.mark.skipif(not GOLEM.is_dir(), reason="local client folder is not in the repo")
def test_golem_prompt_omits_pitch_and_proof_line():
    context = load_collector_context(GOLEM)
    prompt = build_collector_prompt(context)
    assert context["pitch"]
    assert context["proof_line"]
    assert context["pitch"] not in prompt
    assert "I rebuilt" not in prompt
    assert context["proof_line"] not in prompt
    assert "product_launch_speed" in prompt
    assert any("differs from campaign icp" in warning for warning in context["warnings"])
    assert any("proposed" in warning for warning in context["warnings"])


@pytest.mark.skipif(not GOLEM.is_dir(), reason="local client folder is not in the repo")
def test_golem_suggestions_file_stays_proposed():
    doc = json.loads((GOLEM / "collector_suggestions.json").read_text(encoding="utf-8"))
    campaign = json.loads((GOLEM / "campaign.json").read_text(encoding="utf-8"))
    magnet = json.loads((ROOT / "data" / "clients" / "golemui" / "lead_magnets" / "1.json").read_text(encoding="utf-8"))
    assert campaign["offer_id"] == 1
    assert campaign["lead_magnet_id"] == 1
    assert doc["generator"] == "cursor"
    assert doc["magnet_status"] == "proposed"
    assert doc["offer_id"] == 1
    assert doc["lead_magnet_id"] == 1
    assert [row["id"] for row in doc["suggestions"]] == [
        "public_quote_form",
        "tech_stack",
        "systems_named",
        "journey_access",
        "hiring_function",
        "open_role_titles",
    ]
    assert all(row["status"] == "proposed" for row in doc["suggestions"])
    assert all(row["paid_extra"] is False for row in doc["suggestions"])
    quote = doc["suggestions"][0]
    assert quote["page_hints"] == ["quote", "get-a-quote", "claims", "fnol"]
    assert doc["suggestions"][-1]["already_collected"] is True
    for row in doc["suggestions"]:
        assert why_drop_reason(row["why"], magnet["pitch"]) is None
        assert magnet["pitch"] not in row["why"]
    assert {row["module"] for row in doc["leave_off"]} == {
        "apollo_phone",
        "facebook_company_posts",
        "instagram_company_posts",
        "meta_ads",
        "linkedin_company_profile",
    }
    plan = json.loads((GOLEM / "enrichment_plan.json").read_text(encoding="utf-8"))
    assert "extracts" not in plan


def test_print_prompt_does_not_call_model_or_write(tmp_path: Path):
    client = tmp_path / "clients" / "acme"
    camp = client / "campaigns" / "in_progress" / "acme_widgets"
    (client / "offers").mkdir(parents=True)
    (client / "lead_magnets").mkdir()
    (client / "proofs").mkdir()
    (client / "icps" / "widgets").mkdir(parents=True)
    camp.mkdir(parents=True)
    (client / "company_brief.md").write_text(
        "# Brief\n\n## What they do\n\nThey build forms.\n\n## Core paid offer\n\nThey wire forms to a CRM.\n",
        encoding="utf-8",
    )
    (client / "offers" / "1.json").write_text(
        json.dumps({"id": 1, "title": "Forms", "description": "One form wired to the CRM."}),
        encoding="utf-8",
    )
    (client / "lead_magnets" / "1.json").write_text(
        json.dumps(
            {
                "id": 1,
                "offer_id": 1,
                "icp_id": "other_icp",
                "status": "proposed",
                "title": "A public form",
                "description": "A copy of the public form.",
                "pitch": "I rebuilt the form as a single JSON file — open it, change a question, tell me what you think.",
            }
        ),
        encoding="utf-8",
    )
    (client / "proofs" / "1.json").write_text(
        json.dumps(
            {
                "pain_themes": ["launch_speed"],
                "proof_line": "They change the journey within a matter of days.",
            }
        ),
        encoding="utf-8",
    )
    (client / "icps" / "widgets" / "icp.json").write_text(
        json.dumps({"icp_id": "widgets", "job_titles": ["CTO"], "industries": ["software"]}),
        encoding="utf-8",
    )
    (camp / "campaign.json").write_text(
        json.dumps(
            {
                "campaign_id": "acme_widgets",
                "client_id": "acme",
                "icp_id": "widgets",
                "offer_id": 1,
                "lead_magnet_id": 1,
            }
        ),
        encoding="utf-8",
    )
    (camp / "enrichment_plan.json").write_text(
        json.dumps(default_plan("acme_widgets")),
        encoding="utf-8",
    )
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["DATA_ROOT"] = str(tmp_path)
    script = ROOT / "scripts" / "suggest_collectors.py"
    missing = subprocess.run(
        [sys.executable, str(script), "--campaign", "acme_widgets"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 2
    assert "Pass --generate" in missing.stderr
    assert not (camp / "collector_suggestions.json").exists()

    printed = subprocess.run(
        [sys.executable, str(script), "--campaign", "acme_widgets", "--print-prompt"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert printed.returncode == 0, printed.stderr
    assert "I rebuilt" not in printed.stdout
    assert "within a matter of days" not in printed.stdout
    assert "launch_speed" in printed.stdout
    assert "WARNING" in printed.stderr
    assert not (camp / "collector_suggestions.json").exists()
