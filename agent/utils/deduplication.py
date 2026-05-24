"""
Per-client lead deduplication.
Tracks every company domain ever delivered to a client.
A lead is skipped if its domain is already in the client's seen_leads registry.

Registry stored at: data/clients/{client_id}/seen_leads.json
Format: {"domains": ["example.com", "acme.com", ...], "last_updated": "..."}
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent.utils.logger import log


def _registry_path(client_id: str) -> Path:
    return Path("data") / "clients" / client_id / "seen_leads.json"


def _load_registry(client_id: str) -> set[str]:
    """Load the seen domains set for a client. Returns empty set if none exists."""
    path = _registry_path(client_id)
    if not path.exists():
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("domains", []))
    except Exception as e:
        log.warning("Could not load seen_leads for %s: %s — starting fresh", client_id, e)
        return set()


def _save_registry(client_id: str, domains: set[str]) -> None:
    """Persist the updated seen domains set."""
    path = _registry_path(client_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"domains": sorted(domains), "last_updated": datetime.utcnow().isoformat()},
            f,
            indent=2,
        )


def _extract_domain(url: Optional[str], company_name: str) -> str:
    """Extract domain from URL, fallback to slugified company name."""
    if url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url if url.startswith("http") else f"https://{url}")
            domain = (parsed.netloc or parsed.path).replace("www.", "").lower().strip("/")
            if domain:
                return domain
        except Exception:
            pass
    # Fallback: slugify company name
    return company_name.lower().strip().replace(" ", "-").replace(",", "").replace(".", "")


def filter_seen_leads(
    client_id: str,
    companies: list,  # list[RawCompany]
    logger=None,
) -> list:
    """
    Remove companies that have already been delivered to this client.
    Returns only unseen companies.
    """
    _log = logger or log
    seen = _load_registry(client_id)

    if not seen:
        _log.info("Dedup: no previous leads for client %s — all %d companies are new", client_id, len(companies))
        return companies

    unseen = []
    skipped = []
    for company in companies:
        domain = _extract_domain(company.website, company.company_name)
        if domain in seen:
            skipped.append(company.company_name)
        else:
            unseen.append(company)

    if skipped:
        _log.info(
            "Dedup: filtered %d already-delivered companies for client %s: %s%s",
            len(skipped),
            client_id,
            ", ".join(skipped[:5]),
            f" ... (+{len(skipped) - 5} more)" if len(skipped) > 5 else "",
        )

    _log.info("Dedup: %d/%d companies are new", len(unseen), len(companies))
    return unseen


def mark_leads_as_delivered(
    client_id: str,
    leads: list,  # list[Lead]
    logger=None,
) -> None:
    """
    After a successful run, add all delivered lead domains to the registry.
    Call this for BOTH qualified and partial leads — we don't want to re-surface
    a partial lead in a future run either.
    """
    _log = logger or log
    seen = _load_registry(client_id)
    new_domains: set[str] = set()

    for lead in leads:
        domain = _extract_domain(lead.website, lead.company_name)
        new_domains.add(domain)

    added = new_domains - seen
    if added:
        seen.update(added)
        _save_registry(client_id, seen)
        _log.info(
            "Dedup: marked %d new domains as delivered for client %s (total: %d)",
            len(added), client_id, len(seen),
        )


def get_seen_count(client_id: str) -> int:
    """Return how many unique companies have been delivered to this client."""
    return len(_load_registry(client_id))


def clear_registry(client_id: str) -> None:
    """
    Wipe the seen_leads registry for a client.
    Use with care — this allows re-delivery of previously seen companies.
    """
    path = _registry_path(client_id)
    if path.exists():
        path.unlink()
        log.info("Dedup: cleared seen_leads registry for client %s", client_id)
