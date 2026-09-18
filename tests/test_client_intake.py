"""Client intake from file/URL + cheap dashboard client sync."""
from __future__ import annotations

import json
from pathlib import Path

from agent.utils.client_intake import (
    allocate_client_id,
    create_client_from_source,
    html_to_text,
    slugify_client_id,
    truncate_brief,
)
from agent.utils.cost_dashboard import load_and_sync_dashboard_index, sync_clients_into_index


def test_slugify_and_allocate(tmp_path: Path):
    assert slugify_client_id("Vista Business Brain") == "vista_business_brain"
    (tmp_path / "clients" / "vista_business_brain").mkdir(parents=True)
    assert allocate_client_id(tmp_path, "Vista Business Brain") == "vista_business_brain_2"


def test_html_to_text_strips_scripts():
    html = "<html><head><script>evil()</script></head><body><h1>Hi</h1><p>There</p></body></html>"
    text = html_to_text(html)
    assert "Hi" in text
    assert "There" in text
    assert "evil" not in text


def test_create_client_from_txt_upload(tmp_path: Path):
    result = create_client_from_source(
        tmp_path,
        client_name="Acme Robotics",
        upload_bytes=b"Acme sells industrial robots to factories in Texas.\n",
        upload_filename="brief.txt",
        notes="Priority geo: US",
    )
    assert result["client_id"] == "acme_robotics"
    client = tmp_path / "clients" / "acme_robotics"
    assert (client / "client.json").is_file()
    assert (client / "CLIENT.md").is_file()
    assert (client / "sources" / "brief.txt").is_file()
    meta = json.loads((client / "client.json").read_text(encoding="utf-8"))
    assert meta["client_name"] == "Acme Robotics"
    assert "industrial robots" in meta["brief"]
    assert meta["source_doc"] == "sources/brief.txt"
    md = (client / "CLIENT.md").read_text(encoding="utf-8")
    assert "Priority geo" in md


def test_sync_clients_into_index_picks_up_disk_client(tmp_path: Path):
    create_client_from_source(
        tmp_path,
        client_name="Fresh Co",
        upload_bytes=b"Fresh Co makes yogurt.\n",
        upload_filename="notes.txt",
    )
    index = {
        "by_client": {},
        "campaigns": [],
        "runs": [],
        "totals": {"client_count": 0, "usd_total": 0},
    }
    synced, changed = sync_clients_into_index(index, tmp_path)
    assert changed
    assert "fresh_co" in synced["by_client"]
    assert synced["by_client"]["fresh_co"]["client_name"] == "Fresh Co"
    assert synced["totals"]["client_count"] >= 1


def test_load_and_sync_persists(tmp_path: Path):
    create_client_from_source(
        tmp_path,
        client_name="Beta Labs",
        notes="Only notes, no file",
    )
    # Minimal empty index file
    (tmp_path / "cost_dashboard_index.json").write_text(
        json.dumps({"by_client": {}, "campaigns": [], "runs": [], "totals": {}}),
        encoding="utf-8",
    )
    index = load_and_sync_dashboard_index(tmp_path)
    assert "beta_labs" in index["by_client"]
    disk = json.loads((tmp_path / "cost_dashboard_index.json").read_text(encoding="utf-8"))
    assert "beta_labs" in disk["by_client"]


def test_truncate_brief():
    assert len(truncate_brief("x" * 100, max_chars=50)) <= 50
