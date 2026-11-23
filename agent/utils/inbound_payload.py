"""
First step for a vendor payload: record the object we received, then fill our fields.

treg, Harvest, and Apify use different wrappers and key names for the same fact
(`content` vs `text`, `output.elements` vs a bare list). Callers match through
this map instead of reading one hardcoded key.
"""
from __future__ import annotations

from typing import Any, Optional

from agent.utils.logger import log

# Collection keys, in the order we prefer when a payload holds several lists.
_LIST_KEYS = ("elements", "items", "posts", "jobs", "results", "records", "data")
_OBJECT_KEYS = ("output", "raw", "result")

# Canonical field → dotted paths on the vendor object. First non-empty value wins.
FIELD_PATHS: dict[str, dict[str, tuple[str, ...]]] = {
    "post": {
        "text": ("text", "content", "postText", "commentary", "caption", "message"),
        "url": (
            "url",
            "postUrl",
            "shareUrl",
            "linkedinUrl",
            "shareLinkedinUrl",
            "socialContent.shareUrl",
            "link",
        ),
        "posted_at": (
            "postedAt.date",
            "postedAt.timestamp",
            "postedAt",
            "date",
            "publishedAt",
            "time",
            "datePublished",
            "created_at",
        ),
        "likes": ("engagement.likes", "likes", "likeCount", "numLikes"),
        "comments": ("engagement.comments", "commentCount", "numComments", "comments"),
    },
    "job": {
        "title": (
            "title",
            "jobTitle",
            "job_title",
            "position",
            "job_details.title",
            "attributes.title",
            "attributes.translated_title",
            "attributes.normalized_title",
        ),
        "company_url": (
            "companyLinkedinUrl",
            "companyUrl",
            "inputUrl",
            "company_linkedin_url",
            "companyLinkedin",
        ),
    },
    "company": {
        "name": ("name", "company_name", "companyName"),
        "linkedin_url": ("linkedin_url", "linkedinUrl", "url"),
        "description": ("description", "about", "summary", "tagline", "overview"),
        "industry": ("industry",),
        "employee_count": ("employee_count", "staff_count", "employeeCount", "staffCount"),
        "website": ("website", "website_url", "websiteUrl"),
    },
}

_PRIMARY = {"post": "text", "job": "title", "company": "description"}


def describe_payload(data: Any, *, limit: int = 40) -> list[str]:
    """Dot-paths of the object we actually received, capped for the run log."""
    paths: list[str] = []

    def walk(node: Any, prefix: str, depth: int) -> None:
        if len(paths) >= limit or depth > 4:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).startswith("_"):
                    continue
                path = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(value, (dict, list)):
                    walk(value, path, depth + 1)
                else:
                    paths.append(path)
        elif isinstance(node, list):
            label = f"{prefix}[{len(node)}]" if prefix else f"[{len(node)}]"
            paths.append(label)
            if node and isinstance(node[0], dict):
                walk(node[0], f"{prefix}[]" if prefix else "[]", depth + 1)

    walk(data, "", 0)
    return paths


def _dig(record: dict[str, Any], path: str) -> Any:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    if current is None:
        return None
    if isinstance(current, str):
        text = current.strip()
        return text or None
    if isinstance(current, dict):
        return None
    if isinstance(current, list):
        if path.endswith("comments") and current and not isinstance(current[0], (str, int, float)):
            return len(current)
        return None
    return current


def map_record(record: dict[str, Any], kind: str) -> dict[str, Any]:
    """Fill one canonical record from the first matching path on the vendor object."""
    spec = FIELD_PATHS[kind]
    out: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for field, paths in spec.items():
        for path in paths:
            value = _dig(record, path)
            if value is None or value == "":
                continue
            out[field] = value
            sources[field] = path
            break
        else:
            out[field] = None
    out["_sources"] = sources
    return out


def _dict_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _lists_in(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in _LIST_KEYS:
        found = _dict_records(data.get(key))
        if found:
            return found
        nested = data.get(key)
        if isinstance(nested, dict):
            for inner_key in _LIST_KEYS:
                found = _dict_records(nested.get(inner_key))
                if found:
                    return found
    element = data.get("element")
    if isinstance(element, dict):
        return [element]
    return []


def iter_records(data: Any) -> list[dict[str, Any]]:
    """The list of row objects inside a vendor payload, whatever wrapper it used."""
    if isinstance(data, list):
        return _dict_records(data)
    if not isinstance(data, dict):
        return []
    for key in _OBJECT_KEYS:
        inner = data.get(key)
        if isinstance(inner, list):
            found = _dict_records(inner)
            if found:
                return found
        if isinstance(inner, dict):
            found = _lists_in(inner)
            if found:
                return found
    return _lists_in(data)


def unwrap_object(data: Any) -> dict[str, Any]:
    """One profile-style object, unwrapping output/raw when that layer is a dict."""
    if not isinstance(data, dict):
        return {}
    for key in _OBJECT_KEYS:
        inner = data.get(key)
        if isinstance(inner, dict):
            return inner
    return data


def _useful(kind: str, record: dict[str, Any]) -> bool:
    if kind == "post":
        return bool(record.get("text") or record.get("url"))
    field = _PRIMARY.get(kind, "")
    return bool(record.get(field))


def map_inbound(data: Any, kind: str, *, source: str) -> dict[str, Any]:
    """
    Inventory a list payload and map each row onto our fields.

    Logs the received paths when rows arrived but none filled the field we need.
    """
    raw_records = iter_records(data)
    mapped = [map_record(row, kind) for row in raw_records]
    received = describe_payload(data)
    matched = sum(1 for row in mapped if _useful(kind, row))
    if raw_records and matched == 0:
        log.warning(
            "inbound %s from %s: %d record(s), none matched %s. received: %s",
            kind,
            source,
            len(raw_records),
            _PRIMARY.get(kind, kind),
            ", ".join(received[:25]) or "(empty object)",
        )
    elif not raw_records and received:
        log.info(
            "inbound %s from %s: no records. received: %s",
            kind,
            source,
            ", ".join(received[:25]),
        )
    return {
        "kind": kind,
        "source": source,
        "records": mapped,
        "received_paths": received,
        "matched": matched,
    }


def map_object(data: Any, kind: str, *, source: str) -> dict[str, Any]:
    """Inventory one object payload and map it onto our fields."""
    raw = unwrap_object(data)
    mapped = map_record(raw, kind) if raw else {field: None for field in FIELD_PATHS[kind]}
    received = describe_payload(data)
    if raw and not _useful(kind, mapped):
        log.warning(
            "inbound %s from %s: object did not match %s. received: %s",
            kind,
            source,
            _PRIMARY.get(kind, kind),
            ", ".join(received[:25]) or "(empty object)",
        )
    mapped["_received_paths"] = received
    return mapped


def mapped_text(record: dict[str, Any]) -> str:
    value = record.get("text")
    if value is None:
        return ""
    return str(value).strip()
