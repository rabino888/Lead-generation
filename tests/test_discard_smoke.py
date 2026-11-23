"""Discarding a smoke clears the gate and leaves the campaign cost log."""
from __future__ import annotations

import json

from agent.utils.list_run import discard_smoke_run, smoke_was_cancelled
from agent.utils.seed_sources import _record_seed_apify_cost


def test_discard_keeps_cost_log_and_resets_gate(tmp_path):
    (tmp_path / "cost_runs.json").write_text(
        json.dumps({"campaign_id": "demo", "runs": [{"run_id": "seed-1", "label": "seed"}]}),
        encoding="utf-8",
    )
    (tmp_path / "list_run.json").write_text(
        json.dumps(
            {
                "campaign_id": "demo",
                "phase": "smoking",
                "smoke_run_id": "run_missing",
                "counts": {"matched": 57},
                "smoke_preview": [{"company_name": "TULA"}],
            }
        ),
        encoding="utf-8",
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    smoke_csv = stages / "04_smoke_contactable.csv"
    smoke_csv.write_text("company_name\nTULA\n", encoding="utf-8")
    raw = stages / "01_raw_seeds.csv"
    raw.write_text("company_name\nTULA\n", encoding="utf-8")
    (tmp_path / "seeds.csv").write_text("company_name\nTULA\n", encoding="utf-8")

    state = discard_smoke_run(tmp_path, "demo")

    kept = json.loads((tmp_path / "cost_runs.json").read_text(encoding="utf-8"))
    assert kept["runs"] == [{"run_id": "seed-1", "label": "seed"}]
    assert state["phase"] == "idle"
    assert state["smoke_run_id"] == ""
    assert state["counts"] == {}
    assert state["cancel_generation"] == 1
    assert not smoke_csv.exists()
    assert not raw.exists()
    assert not (tmp_path / "seeds.csv").exists()
    assert smoke_was_cancelled(tmp_path, 0)
    assert not smoke_was_cancelled(tmp_path, 1)


def test_discard_refuses_a_running_full_batch(tmp_path):
    (tmp_path / "list_run.json").write_text(
        json.dumps({"campaign_id": "demo", "phase": "running_full"}),
        encoding="utf-8",
    )
    try:
        discard_smoke_run(tmp_path, "demo")
    except RuntimeError as exc:
        assert "full batch" in str(exc)
    else:
        raise AssertionError("expected the full batch to be left running")


def test_seed_spend_appends_without_replacing_earlier_rows(tmp_path):
    (tmp_path / "cost_runs.json").write_text(
        json.dumps({"campaign_id": "demo", "runs": [{"run_id": "older", "label": "older"}]}),
        encoding="utf-8",
    )

    class _Campaign:
        campaign_dir = tmp_path
        campaign_id = "demo"

    _record_seed_apify_cost(_Campaign(), 1.25)
    manifest = json.loads((tmp_path / "cost_runs.json").read_text(encoding="utf-8"))
    assert manifest["runs"][0]["run_id"] == "older"
    assert manifest["runs"][1]["label"] == "LinkedIn company search"
    assert manifest["runs"][1]["cost_tracker"]["total_usd"] == 1.25
