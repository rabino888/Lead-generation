"""Tests for lead summary / CSV export helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.utils.lead_export import export_leads_csv, lead_summary, resolve_stage_file


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def test_resolve_prefers_scored(tmp_path: Path):
    stages = tmp_path / "stages"
    _write_csv(
        stages / "04_apollo_contactable.csv",
        "company_name,decision_maker_email",
        ["A,a@x.com"],
    )
    _write_csv(
        stages / "06_scored.csv",
        "company_name,decision_maker_email,lead_score",
        ["A,a@x.com,80"],
    )
    name, path = resolve_stage_file(tmp_path, "auto")
    assert name == "06_scored.csv"
    assert path.name == "06_scored.csv"


def test_lead_summary_preview(tmp_path: Path):
    camp = tmp_path / "demo"
    stages = camp / "stages"
    _write_csv(
        stages / "04_apollo_contactable.csv",
        "company_name,decision_maker_name,decision_maker_title,decision_maker_email,decision_maker_linkedin,lead_score,run_id",
        [
            "Acme,Jane,CEO,j@acme.com,https://li/j,90,run1",
            "Beta,Bob,CTO,b@beta.com,https://li/b,70,run2",
        ],
    )
    summary = lead_summary(camp, preview=1)
    assert summary["total_rows"] == 2
    assert len(summary["preview"]) == 1
    assert summary["preview"][0]["company_name"] == "Acme"

    filtered = lead_summary(camp, run_id="run2")
    assert filtered["total_rows"] == 1
    assert filtered["preview"][0]["decision_maker_name"] == "Bob"


def test_export_writes_under_exports(tmp_path: Path):
    camp = tmp_path / "demo"
    stages = camp / "stages"
    _write_csv(
        stages / "04_apollo_contactable.csv",
        "company_name,decision_maker_email",
        ["A,a@x.com"],
    )
    dest = export_leads_csv(camp)
    assert dest.parent.name == "exports"
    assert dest.is_file()
    assert "a@x.com" in dest.read_text(encoding="utf-8")
