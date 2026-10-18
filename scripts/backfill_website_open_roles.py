"""Fill empty website_open_roles from company job boards.

Uses the careers-page hop plus public Ashby / Greenhouse / Lever listings.
Falls back to the cached careers page, then a fresh scrape, for boards
without a listing API.

Usage:
  python scripts/backfill_website_open_roles.py --campaign automata_us_rnd \\
      --sheet-id 14hfRa-UKxgIU2aS92Ic-oekoYJ3LIxI7y5M_hsblPxY
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from agent.integrations.careers import (
    _is_rejected_careers_url,
    canonical_ats_board_url,
    careers_index_needs_portal_hop,
    clean_role_titles,
    fetch_icims_open_roles,
    find_careers_page,
    icims_board_url,
    open_roles_from_ats,
    pick_careers_portal_hop,
    preferred_ats_board_url,
)
from agent.stages.website import _detect_website_hiring
from agent.utils.campaign_config import load_campaign
from agent.utils.website_markdown_cache import load_markdown

log = logging.getLogger("open_roles")

_YC_COMPANY = re.compile(
    r"https?://(?:www\.)?ycombinator\.com/companies/[A-Za-z0-9_-]+",
    re.I,
)
_ICIMS_URL = re.compile(r"https?://[A-Za-z0-9.-]+\.icims\.com/[^\s)\]\"']+", re.I)


def _careers_section(markdown: str, careers_url: str) -> str:
    if not markdown or not careers_url:
        return ""
    key = careers_url.rstrip("/")
    if key not in markdown:
        return ""
    for section in markdown.split("\n\n---\n\n"):
        if key in section:
            return section
    return ""


def _yc_jobs_url(markdown: str) -> str:
    if not markdown:
        return ""
    match = _YC_COMPANY.search(markdown)
    if not match:
        return ""
    base = match.group(0).rstrip("/")
    if base.lower().endswith("/jobs"):
        return base
    return base + "/jobs"


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script\b.*?>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style\b.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()[:14000]


def _fetch_plain(url: str) -> str:
    import httpx

    try:
        resp = httpx.get(
            url,
            timeout=25,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
    except Exception as exc:
        log.warning("Page fetch failed for %s: %s", url, exc)
        return ""
    if resp.status_code != 200:
        return ""
    return _html_to_text(resp.text)


def _roles_from_hiring(hiring: object) -> list[str]:
    if not isinstance(hiring, dict):
        return []
    roles = hiring.get("open_roles") or []
    if isinstance(roles, str):
        roles = [part.strip() for part in re.split(r"[;\n]", roles)]
    return clean_role_titles(list(roles))


def _pack(roles: list[str], careers_url: str, source: str) -> dict[str, str]:
    return {
        "website_is_hiring": "yes",
        "website_open_roles": "; ".join(roles),
        "website_careers_url": careers_url,
        "source": source,
    }


def _ats(url: str) -> dict[str, str] | None:
    got = open_roles_from_ats(url)
    if not got:
        return None
    provider = "ats"
    canonical = got["careers_url"]
    if "ashbyhq.com" in canonical:
        provider = "ashby"
    elif "greenhouse.io" in canonical:
        provider = "greenhouse"
    elif "lever.co" in canonical:
        provider = "lever"
    return _pack(list(got["open_roles"]), canonical, provider)


def _llm_roles(
    text: str,
    *,
    company_name: str,
    website: str,
    careers_url: str,
) -> dict[str, str] | None:
    if len((text or "").strip()) < 200:
        return None
    hiring = _detect_website_hiring(
        text,
        company_name,
        website,
        log,
        careers_url=careers_url or None,
        careers_markdown=text,
        hiring_first=True,
    )
    roles = _roles_from_hiring(hiring)
    if not roles:
        return None
    url = ""
    if isinstance(hiring, dict):
        url = str(hiring.get("careers_url") or "").strip()
    if not url or url.lower() == "null" or _is_rejected_careers_url(url):
        url = careers_url
    board = canonical_ats_board_url(url)
    return _pack(roles, board or url, "llm")


def fill_from_cache(row: dict[str, str], markdown: str) -> dict[str, str] | None:
    """ATS listing APIs and cached careers text. No new website crawl."""
    website = (row.get("website") or "").strip()
    name = (row.get("company_name") or "").strip() or website
    careers = (row.get("website_careers_url") or "").strip()
    if careers and not _is_rejected_careers_url(careers):
        got = _ats(careers)
        if got:
            return got
    board = preferred_ats_board_url(markdown, website) if markdown else None
    if board:
        got = _ats(board)
        if got:
            return got

    icims_match = _ICIMS_URL.search(markdown or "") or _ICIMS_URL.search(careers or "")
    if icims_match:
        titles = fetch_icims_open_roles(icims_match.group(0))
        board_url = icims_board_url(icims_match.group(0)) or ""
        if titles and board_url:
            return _pack(titles, board_url, "icims")

    yc = _yc_jobs_url(markdown)
    if yc:
        got = _llm_roles(
            _fetch_plain(yc),
            company_name=name,
            website=website,
            careers_url=yc,
        )
        if got:
            got["source"] = "yc"
            return got

    section = _careers_section(markdown, careers)
    if len(section) >= 400 and careers and not _is_rejected_careers_url(careers):
        got = _llm_roles(
            section,
            company_name=name,
            website=website,
            careers_url=careers,
        )
        if got:
            got["source"] = "cache"
            return got
    return None


def _scrape(url: str) -> str:
    from agent.integrations.apify import scrape_website_apify
    from agent.integrations.firecrawl import scrape_page

    markdown = scrape_page(url) or ""
    if len(markdown) >= 500:
        return markdown
    rendered = scrape_website_apify(
        url, max_pages=1, max_depth=0, crawler_type="playwright:firefox"
    )
    return rendered or markdown


def fill_from_live_page(row: dict[str, str], markdown: str) -> dict[str, str] | None:
    """Probe a careers URL, hop to the job board, then read open roles."""
    website = (row.get("website") or "").strip()
    name = (row.get("company_name") or "").strip() or website
    if not website:
        return None
    found = find_careers_page(website, markdown or None)
    if not found or _is_rejected_careers_url(found):
        return None
    got = _ats(found)
    if got:
        return got
    if "icims.com" in found:
        titles = fetch_icims_open_roles(found)
        board_url = icims_board_url(found) or found
        if titles:
            return _pack(titles, board_url, "icims")
    if "ycombinator.com/companies/" in found:
        yc = found if found.rstrip("/").endswith("/jobs") else found.rstrip("/") + "/jobs"
        got = _llm_roles(
            _fetch_plain(yc),
            company_name=name,
            website=website,
            careers_url=yc,
        )
        if got:
            got["source"] = "yc"
            return got

    page = _scrape(found)
    target = found
    if page and careers_index_needs_portal_hop(page, careers_url=found):
        hop = pick_careers_portal_hop(page, website, current_url=found)
        if hop and hop.rstrip("/") != found.rstrip("/"):
            log.info("Careers portal hop for %s: %s → %s", name, found, hop)
            got = _ats(hop)
            if got:
                return got
            hopped = _scrape(hop)
            if hopped:
                page = hopped
                target = hop
    return _llm_roles(
        page,
        company_name=name,
        website=website,
        careers_url=target,
    )


def _patch_csv(path: Path, updates: dict[str, dict[str, str]]) -> int:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    if "lead_id" not in fields or "website_open_roles" not in fields:
        return 0
    changed = 0
    for row in rows:
        upd = updates.get((row.get("lead_id") or "").strip())
        if not upd:
            continue
        if (row.get("website_open_roles") or "").strip():
            continue
        for key in ("website_is_hiring", "website_open_roles", "website_careers_url"):
            row[key] = upd[key]
        changed += 1
    if not changed:
        return 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, path.with_suffix(path.suffix + f".bak_{stamp}"))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})
    return changed


def _col_letter(index_1based: int) -> str:
    letters = ""
    n = index_1based
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _patch_sheet(spreadsheet_id: str, updates: dict[str, dict[str, str]]) -> int:
    from agent.integrations.sheets import _get_gspread_client

    gc = _get_gspread_client()
    spreadsheet = gc.open_by_key(spreadsheet_id)
    try:
        worksheet = spreadsheet.worksheet("Leads")
    except Exception:
        worksheet = spreadsheet.sheet1
    values = worksheet.get_all_values()
    if not values:
        return 0
    headers = values[0]
    index = {name: pos for pos, name in enumerate(headers)}
    for required in ("lead_id", "website_is_hiring", "website_open_roles", "website_careers_url"):
        if required not in index:
            raise RuntimeError(f"Sheet is missing column {required}")
    batch: list[dict] = []
    matched = 0
    for row_number, row in enumerate(values[1:], start=2):
        lead_id = row[index["lead_id"]] if index["lead_id"] < len(row) else ""
        upd = updates.get(lead_id.strip())
        if not upd:
            continue
        current = ""
        col = index["website_open_roles"]
        if col < len(row):
            current = (row[col] or "").strip()
        if current:
            continue
        matched += 1
        for field in ("website_is_hiring", "website_open_roles", "website_careers_url"):
            letter = _col_letter(index[field] + 1)
            batch.append({"range": f"{letter}{row_number}", "values": [[upd[field]]]})
    if batch:
        worksheet.batch_update(batch, value_input_option="USER_ENTERED")
    return matched


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Backfill website open roles")
    parser.add_argument("--campaign", default="automata_us_rnd")
    parser.add_argument(
        "--sheet-id",
        default="14hfRa-UKxgIU2aS92Ic-oekoYJ3LIxI7y5M_hsblPxY",
    )
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Do not scrape companies that are still empty after the cache/API pass",
    )
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated company name fragments to limit the run",
    )
    args = parser.parse_args()

    camp = load_campaign(args.campaign).campaign_dir
    scored = camp / "stages" / "06_scored.csv"
    with scored.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    only = [part.strip().casefold() for part in args.only.split(",") if part.strip()]
    missing = [row for row in rows if not (row.get("website_open_roles") or "").strip()]
    if only:
        missing = [
            row for row in missing
            if any(part in (row.get("company_name") or "").casefold() for part in only)
        ]
    log.info("%d companies missing open roles", len(missing))

    updates: dict[str, dict[str, str]] = {}
    pending: list[tuple[dict[str, str], str]] = []
    for row in missing:
        cached = load_markdown(camp, row.get("website") or "")
        markdown = cached.markdown if cached else ""
        if cached and cached.careers_url and not (row.get("website_careers_url") or "").strip():
            row = dict(row)
            row["website_careers_url"] = cached.careers_url
        got = fill_from_cache(row, markdown)
        lead_id = (row.get("lead_id") or "").strip()
        if got:
            updates[lead_id] = got
            log.info(
                "FILLED %s via %s (%d chars of roles) %s",
                row.get("company_name"),
                got["source"],
                len(got["website_open_roles"]),
                got["website_careers_url"],
            )
        else:
            pending.append((row, markdown))

    log.info("Cache/API filled %d; %d still empty", len(updates), len(pending))
    if pending and not args.skip_live:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(fill_from_live_page, row, markdown): row
                for row, markdown in pending
            }
            for future in as_completed(futures):
                row = futures[future]
                lead_id = (row.get("lead_id") or "").strip()
                try:
                    got = future.result()
                except Exception as exc:
                    log.warning("Live careers read failed for %s: %s", row.get("company_name"), exc)
                    continue
                if not got:
                    log.info("STILL EMPTY %s", row.get("company_name"))
                    continue
                updates[lead_id] = got
                log.info(
                    "FILLED %s via %s %s",
                    row.get("company_name"),
                    got["source"],
                    got["website_careers_url"],
                )

    if not updates:
        log.info("No open roles to write")
        return 0

    for path in sorted((camp / "stages").glob("*.csv")):
        if ".bak" in path.name or "partial" in path.name:
            continue
        changed = _patch_csv(path, updates)
        if changed:
            log.info("Updated %s (%d rows)", path.name, changed)

    written = _patch_sheet(args.sheet_id, updates)
    log.info("Sheet updated for %d companies (%d with roles)", written, len(updates))
    for lead_id, upd in sorted(updates.items(), key=lambda item: item[0]):
        print(f"{lead_id}\t{upd['source']}\t{upd['website_careers_url']}\t{upd['website_open_roles'][:180]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
