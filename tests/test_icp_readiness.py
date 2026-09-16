"""ICP Automata-depth readiness gates."""
from __future__ import annotations

import json
from pathlib import Path

from agent.utils.brief_icp import parse_brief_to_icp
from agent.utils.icp_readiness import assess_icp_depth, assert_icp_ready_for_run


def test_brief_stub_is_thin():
    stub = parse_brief_to_icp("TA managers Spain 50-200", campaign_id="x")
    a = assess_icp_depth(stub)
    assert a["ready"] is False
    assert any("industries" in m for m in a["missing"])
    assert "thin" in a["remedy"].lower() or "Automata" in a["remedy"]


def test_decoracel_icp_is_ready():
    path = Path("data/clients/decoracel/icps/real_estate_espa_a/icp.json")
    if not path.is_file():
        return
    icp = json.loads(path.read_text(encoding="utf-8"))
    a = assess_icp_depth(icp)
    assert a["ready"] is True
    assert a["missing"] == []
    assert_icp_ready_for_run(icp)


def test_assert_raises_on_thin():
    try:
        assert_icp_ready_for_run({"job_titles": ["CEO"]})
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "thin" in str(exc).lower() or "Missing" in str(exc)
