"""
Quality check after a list is generated.

Compares each delivered company with that campaign's ICP. A marketing agency
fails an ecommerce-brand ICP. The same agency passes an ICP that targets agencies.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping

from agent.utils.icp_match import match_company
from agent.utils.icp_rules import IcpRules


def _reason_text(reason: str) -> str:
    if reason == "service_provider":
        return "agency or service firm, not an operator in this ICP"
    if reason == "industry_mismatch":
        return "industry is outside this ICP"
    if reason.startswith("excluded_industry:"):
        return f"industry is excluded ({reason.split(':', 1)[1]})"
    if reason.startswith("excluded_keyword:"):
        return f"matches an excluded keyword ({reason.split(':', 1)[1]})"
    return reason.replace("_", " ")


def classify_row(row: Mapping[str, Any], rules: IcpRules) -> dict[str, Any]:
    ok, reasons = match_company(row, rules)
    reason = reasons[0] if reasons and not ok else "icp_match"
    instagram = str(row.get("social_instagram") or row.get("instagram_url") or "").strip()
    return {
        "company_name": str(row.get("company_name") or "").strip(),
        "industry": str(row.get("industry") or "").strip(),
        "website": str(row.get("website") or "").strip(),
        "fit": bool(ok),
        "reason": reason,
        "reason_text": "matches this ICP" if ok else _reason_text(reason),
        "instagram_url": instagram,
        "instagram_posts": bool(str(row.get("instagram_post_summary") or "").strip()),
    }


def review_rows(
    rows: list[Mapping[str, Any]],
    rules: IcpRules,
    *,
    instagram_on: bool = False,
) -> dict[str, Any]:
    companies = [classify_row(row, rules) for row in rows]
    failed = [row for row in companies if not row["fit"]]
    checked = len(companies)
    if checked and len(failed) == checked:
        summary = (
            f"Company fit failed — {len(failed)}/{checked} delivered companies "
            "are outside this ICP."
        )
    elif failed:
        summary = (
            f"Company fit failed — {len(failed)}/{checked} delivered companies "
            "are outside this ICP."
        )
    else:
        summary = f"Company fit passed — {checked}/{checked} delivered companies match this ICP."

    notes: list[str] = []
    if instagram_on and checked:
        missing_url = sum(1 for row in companies if not row["instagram_url"])
        missing_posts = sum(
            1 for row in companies if row["instagram_url"] and not row["instagram_posts"]
        )
        if missing_url:
            notes.append(
                f"Instagram was requested, but {missing_url}/{checked} companies had no "
                "Instagram URL on the crawled site, so no Instagram posts were requested."
            )
        if missing_posts:
            notes.append(
                f"Instagram URL was found for {missing_posts} companies and the posts were not stored."
            )
    if notes:
        summary = summary + " " + " ".join(notes)
    return {
        "ok": not failed,
        "checked": checked,
        "failed": len(failed),
        "summary": summary,
        "companies": companies,
    }


def _read_rows(csv_path: Path | None) -> list[dict[str, str]]:
    if csv_path is None or not csv_path.is_file():
        return []
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def review_delivery(
    campaign_dir: Path,
    campaign_id: str,
    csv_path: Path | None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from agent.utils.campaign_config import load_campaign
    from agent.utils.icp_rules import load_icp_rules

    campaign = load_campaign(campaign_id)
    rules = load_icp_rules(campaign)
    modules = (plan or {}).get("modules") or {}
    instagram_on = bool(modules.get("instagram_company_posts"))
    report = review_rows(_read_rows(csv_path), rules, instagram_on=instagram_on)
    report["campaign_id"] = campaign_id
    path = Path(campaign_dir) / "list_qa.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
