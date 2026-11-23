"""Small helper so execute can mention the matcher without a cycle."""
from __future__ import annotations

from typing import Any

from agent.autopilot.gates import rules_from_icp
from agent.utils.icp_match import match_company


def split_match(rows: list[dict[str, Any]], icp: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rules = rules_from_icp(icp)
    matched: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        ok, reasons = match_company(row, rules)
        if ok:
            matched.append(row)
        else:
            rejected.append({**row, "icp_match_reasons": ";".join(reasons)})
    return matched, rejected
