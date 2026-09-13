---
name: icp-draft
description: >-
  Draft or refine a campaign icp.json through a back-and-forth planning chat.
  Use when the operator asks to build an ICP, fill icp.json, plan targeting from
  a client brief, or match Automata-style ICP depth. Writes local campaign files
  only — never call paid Gemini/OpenAI/Apollo/Apify for ICP construction.
---

# ICP draft (Cursor planning)

## Goal

Produce a **complete** deterministic ICP under
`data/clients/{client_id}/icps/{icp_id}/icp.json` (and the linked campaign snapshot).
Gold shape: `data/clients/automata/icps/` (legacy reference also at
`data/campaigns/automata_us_rnd/icp.json` via junction).

## Hard rules

- **No paid vendor/LLM APIs** for this step (Cursor chat only).
- Industry- and country-agnostic: all specifics live in the campaign folder.
- Prefer questions over guessing when the brief is thin.
- After writing, tell the operator to click **Reload ICP from disk** in `/builder` (once).
- Prefer staying in Cursor for the whole Q&A loop — see `SOP-ICP-PLANNING.md`.
- Do not send the operator back to the browser between every clarifying question.

## Reference

1. Gold shape: `data/clients/automata/campaigns/automata_us_rnd/icp.json` (or library ICP under `data/clients/automata/icps/`)
2. Field meanings: `docs/ICP-INTAKE-TEMPLATE.md`
3. Pipeline rules: company gate = deterministic rules only (no LLM prefilter)
4. Layout: `data/clients/{client_id}/icps/{icp_id}/` + `data/clients/{client_id}/campaigns/{campaign_id}/` (campaign.json references `icp_id`)

## Required fields (company outreach)

| Field | Notes |
|-------|--------|
| `schema_version` | `"1"` |
| `campaign_id` | Folder name |
| `industries` | Target sectors (UI label: Industry) |
| `excluded_industries` | Hard rejects |
| `geo` | `primary_location`, `country_codes`, `location_hints`, `segments` |
| `company_size_min` / `company_size_max` | Headcount window |
| `employee_ranges` | Apollo-style buckets e.g. `"11,50"` |
| `include_keywords` | Company text signals (not the same as industry) |
| `exclude_keywords` | Noise phrases |
| `excluded_name_patterns` | Regex for name rejects |
| `excluded_domains` | Mega / off-ICP domains |
| `job_titles` | Decision-maker titles (include common variants) |
| `contact_locations` | Person geo for Apollo `person_locations` |
| `website_analysis_mode` | `hiring_first` or `full` |

## Workflow

1. Read campaign `campaign.json` brief (if any) and current `icp.json`.
2. Ask clarifying questions (industry list, geo precision, size, title variants, excludes).
3. Write/update `icp.json` on disk.
4. Optionally draft a starter `seed_sources.json` with a `csv_ingest` primary source.
5. Summarize decisions + open questions.

## Anti-patterns

- Do not invent a thin ICP with only titles + size when Automata-level depth is expected.
- Do not put industry names only in `include_keywords` — use `industries`.
- Do not call website-scrape LLM providers or enrichment APIs while drafting ICP.
