"""
Outreach segments for any company-outreach campaign.

After enrichment, contacts are grouped so outreach can follow the account,
the open roles, and a few firmographic cuts. Industry labels and role buckets
come from the lead row and, optionally, icp.json `outreach_role_groups`.
Nothing here is specific to one client.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from agent.utils.job_titles import primary_title

SEGMENT_COLUMNS: tuple[str, ...] = (
    "segment_company_key",
    "segment_industry",
    "segment_open_roles",
    "segment_role_group",
    "segment_role_source",
    "segment_opening_status",
    "segment_slice",
    "segment_buyer_tier",
    "segment_size_band",
    "segment_contact_use",
    "segment_priority",
    "segment_reason",
)

_HIRING_YES = frozenset({"yes", "true", "1", "y"})


def segment_outreach_rows(
    rows: list[dict[str, Any]],
    *,
    company_size_min: int | None = None,
    company_size_max: int | None = None,
    role_groups: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Return a new row list with segment columns first.

    Priority A: the account has an economic buyer, and headcount is inside
    the ICP range when the ICP sets one. Priority B: size is outside that
    range, or nobody on the account is an economic buyer. Open roles do not
    change priority; they are their own segment.
    """
    groups = _clean_groups(role_groups or [])
    prepared: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        prepared.append(_prepare(row, index, company_size_min, company_size_max, groups))

    by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in prepared:
        by_company[item["company_key"]].append(item)

    for members in by_company.values():
        _assign_contacts(members)

    out: list[dict[str, Any]] = []
    for item in prepared:
        segments = {key: item[key] for key in SEGMENT_COLUMNS}
        merged: dict[str, Any] = dict(segments)
        for key, value in item["row"].items():
            if key not in merged:
                merged[key] = value
        out.append(merged)
    return out


def _prepare(
    row: dict[str, Any],
    index: int,
    size_min: int | None,
    size_max: int | None,
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    company = str(row.get("company_name") or "").strip()
    website = str(row.get("website") or "").strip()
    company_key = _root_domain(website) or _norm_name(company) or f"row-{index}"
    industry = str(row.get("industry") or "").strip() or "Unspecified"
    roles = _role_list(row)
    opening = _opening_status(row, roles)
    role_group, role_source = _role_group(row, roles, opening, groups)
    size_band = _size_band(row.get("company_size"), size_min, size_max)
    title = str(row.get("decision_maker_title") or "")
    return {
        "row": row,
        "index": index,
        "company_key": company_key,
        "segment_company_key": company_key,
        "segment_industry": industry,
        "segment_open_roles": "; ".join(roles),
        "segment_role_group": role_group,
        "segment_role_source": role_source,
        "segment_opening_status": opening,
        "segment_slice": f"{industry} · {role_group}",
        "segment_buyer_tier": _buyer_tier(title),
        "segment_size_band": size_band,
        "person_key": _person_key(row),
        "name_key": _norm_name(str(row.get("decision_maker_name") or "")),
    }


def _assign_contacts(members: list[dict[str, Any]]) -> None:
    size_band = members[0]["segment_size_band"]
    has_economic = any(m["segment_buyer_tier"] == "economic_buyer" for m in members)
    priority, reason = _priority(size_band, has_economic)
    rank = {"economic_buyer": 0, "operator": 1, "other": 2}
    order = sorted(members, key=lambda m: (rank[m["segment_buyer_tier"]], m["index"]))

    seen_people: set[str] = set()
    seen_names: set[str] = set()
    first_assigned = False
    for member in order:
        person = member["person_key"]
        name = member["name_key"]
        duplicate = (person and person in seen_people) or (name and name in seen_names)
        if duplicate:
            use = "same_person"
        elif not first_assigned:
            use = "first_touch"
            first_assigned = True
        else:
            use = "second_thread"
        if person:
            seen_people.add(person)
        if name:
            seen_names.add(name)
        member["segment_contact_use"] = use
        member["segment_priority"] = priority
        member["segment_reason"] = reason


def _priority(size_band: str, has_economic: bool) -> tuple[str, str]:
    outside = size_band in {"below_icp", "above_icp"}
    if outside and not has_economic:
        return "B", "Size outside ICP range, and no economic buyer on the account"
    if outside:
        return "B", "Size outside ICP range"
    if not has_economic:
        return "B", "No economic buyer on the account"
    if size_band == "unknown":
        return "A", "Economic buyer on the account; headcount was not captured"
    if size_band == "in_range":
        return "A", "Economic buyer on the account, size inside ICP range"
    return "A", "Economic buyer on the account"


def _buyer_tier(title: str) -> str:
    raw = primary_title(title).lower().replace("co-founder", "founder").replace("cofounder", "founder")
    if not raw:
        return "other"
    without_vp = re.sub(r"\bvice president\b", " ", raw)
    without_vp = re.sub(r"\bvp\b", " ", without_vp)
    if re.search(
        r"\b(owner|founder|ceo|chief executive|proprietor|managing director|managing partner)\b",
        without_vp,
    ):
        return "economic_buyer"
    if re.search(r"\bpresident\b", without_vp):
        return "economic_buyer"
    if re.search(
        r"\b(cfo|cto|coo|cmo|chro|chief financial|chief operating|chief technology|"
        r"chief marketing|chief people|chief revenue)\b",
        raw,
    ):
        return "economic_buyer"
    if re.search(r"\bpartner\b", raw) and "consultant" not in raw and "associate" not in raw:
        return "economic_buyer"
    if re.search(r"\b(vice president|vp|director|head of|head|manager|partner)\b", raw):
        return "operator"
    return "other"


def _opening_status(row: dict[str, Any], roles: list[str]) -> str:
    if roles:
        return "named_roles"
    hiring = " ".join(
        str(row.get(key) or "")
        for key in ("website_is_hiring", "company_is_hiring", "decision_maker_is_hiring")
    ).lower()
    if any(token in _HIRING_YES for token in re.split(r"[\s,;]+", hiring) if token):
        return "hiring_uncaptured"
    return "none"


def _role_list(row: dict[str, Any]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for key in ("website_open_roles", "company_open_jobs_summary"):
        for part in re.split(r"[;|]", str(row.get(key) or "")):
            text = re.sub(r"\s+", " ", part).strip(" .")
            marker = text.casefold()
            if not text or marker in seen:
                continue
            seen.add(marker)
            found.append(text)
    return found


def _role_group(
    row: dict[str, Any],
    roles: list[str],
    opening: str,
    groups: list[dict[str, Any]],
) -> tuple[str, str]:
    role_text = " ".join(roles)
    matched = _match_group(role_text, groups) if role_text else ""
    if matched:
        return matched, "open_roles"
    if roles:
        return "; ".join(roles), "open_roles"
    services = str(row.get("services_offered") or "")
    matched = _match_group(services, groups)
    if matched:
        return matched, "services"
    if opening == "hiring_uncaptured":
        return "hiring_uncaptured", "none"
    return "no_open_roles", "none"


def _match_group(text: str, groups: list[dict[str, Any]]) -> str:
    haystack = text.casefold()
    if not haystack.strip():
        return ""
    for group in groups:
        for pattern in group["patterns"]:
            if pattern.casefold() in haystack:
                return str(group["label"])
    return ""


def _clean_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for group in groups:
        label = str(group.get("label") or group.get("id") or "").strip()
        patterns = [str(p).strip() for p in (group.get("patterns") or []) if str(p).strip()]
        if label and patterns:
            cleaned.append({"label": label, "patterns": patterns})
    return cleaned


def _size_band(raw: Any, size_min: int | None, size_max: int | None) -> str:
    size = _parse_size(raw)
    if size is None:
        return "unknown"
    if size_min is not None and size < size_min:
        return "below_icp"
    if size_max is not None and size > size_max:
        return "above_icp"
    if size_min is None and size_max is None:
        if size <= 10:
            return "1-10"
        if size <= 50:
            return "11-50"
        if size <= 200:
            return "51-200"
        return "201+"
    return "in_range"


def _parse_size(raw: Any) -> int | None:
    numbers = [int(n) for n in re.findall(r"\d+", str(raw or ""))]
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0]
    return (numbers[0] + numbers[1]) // 2


def _person_key(row: dict[str, Any]) -> str:
    linkedin = str(row.get("decision_maker_linkedin") or "").strip().lower()
    if not linkedin:
        return ""
    linkedin = linkedin.split("?", 1)[0].rstrip("/")
    linkedin = re.sub(r"^https?://", "", linkedin)
    linkedin = linkedin.removeprefix("www.")
    return linkedin


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _root_domain(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if url.startswith("http") else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")
