"""
Per-client lead deduplication.
Tracks every company domain ever delivered to a client.
A lead is skipped if its domain is already in the client's seen_leads registry.

Registry stored in: Google Sheets "Dedup" tab on the Clients spreadsheet.
Columns: client_id | domain | company_name | date_delivered

This persists across Railway deploys (no local files wiped on redeploy).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from agent.utils.logger import log


def _extract_domain(url: Optional[str], company_name: str) -> str:
    """Extract domain from URL, fallback to slugified company name."""
    if url:
        try:
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

    try:
        from agent.integrations.sheets import get_seen_domains
        seen = get_seen_domains(client_id)
    except Exception as e:
        _log.warning("Dedup: could not load seen domains from Sheets (%s) — treating all as new", e)
        seen = set()

    if not seen:
        _log.info(
            "Dedup: no previous leads for client %s — all %d companies are new",
            client_id, len(companies),
        )
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
    After a successful run, add all delivered lead domains to the Dedup tab.
    Call this for BOTH qualified and partial leads — we don't want to re-surface
    a partial lead in a future run either.
    """
    _log = logger or log

    if not leads:
        return

    try:
        from agent.integrations.sheets import get_seen_domains, add_seen_domains
        existing = get_seen_domains(client_id)
    except Exception as e:
        _log.warning("Dedup: could not load existing domains (%s) — writing all as new", e)
        existing = set()

    new_entries = []
    for lead in leads:
        domain = _extract_domain(lead.website, lead.company_name)
        if domain not in existing:
            new_entries.append({"domain": domain, "company_name": lead.company_name})

    if new_entries:
        try:
            from agent.integrations.sheets import add_seen_domains
            add_seen_domains(client_id, new_entries)
            _log.info(
                "Dedup: marked %d new domains as delivered for client %s",
                len(new_entries), client_id,
            )
        except Exception as e:
            _log.error("Dedup: failed to write delivered domains to Sheets: %s", e)
    else:
        _log.info("Dedup: all %d leads were already in the registry", len(leads))


def get_seen_count(client_id: str) -> int:
    """Return how many unique companies have been delivered to this client."""
    try:
        from agent.integrations.sheets import get_seen_domains
        return len(get_seen_domains(client_id))
    except Exception:
        return 0


def clear_registry(client_id: str) -> None:
    """
    Delete all Dedup tab entries for a client.
    Use with care — this allows re-delivery of previously seen companies.
    """
    try:
        import gspread
        import os
        from agent.integrations.sheets import _get_gspread_client

        gc = _get_gspread_client()
        sheet_id = os.environ["CLIENTS_SHEET_ID"]
        wb = gc.open_by_key(sheet_id)

        try:
            ws = wb.worksheet("Dedup")
        except gspread.WorksheetNotFound:
            log.info("Dedup: no Dedup tab found for client %s — nothing to clear", client_id)
            return

        all_rows = ws.get_all_values()
        if len(all_rows) <= 1:
            log.info("Dedup: Dedup tab is empty for client %s", client_id)
            return

        header = all_rows[0]
        try:
            client_col = header.index("client_id")
        except ValueError:
            client_col = 0

        # Collect row indices to delete (1-indexed; row 1 is header)
        to_delete = [
            i + 2  # 1-indexed sheet row
            for i, row in enumerate(all_rows[1:])
            if len(row) > client_col and row[client_col] == client_id
        ]

        # Delete from bottom to top so row indices stay valid
        for row_idx in reversed(to_delete):
            ws.delete_rows(row_idx)

        log.info(
            "Dedup: cleared %d entries from Dedup tab for client %s",
            len(to_delete), client_id,
        )

    except Exception as e:
        log.error("Dedup: could not clear registry for client %s: %s", client_id, e)
