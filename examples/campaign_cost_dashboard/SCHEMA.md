# Cost dashboard schema (v4)

Machine-readable reports live in `cost_log.json`. The live SPA (`/dashboard`) and optional offline HTML render the same fields.

## Report root (`cost_log.json`)

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | `"4"` |
| `campaign_id` | string | Campaign folder name under `data/campaigns/` |
| `client_id` | string | From `run_payload.json` sender |
| `client_name` | string | Display name from sender |
| `logged_at` | ISO datetime | When the report was built |
| `unit_rates_usd` | object | `apollo_per_credit`, `firecrawl_per_page`, `llm_per_1k_tokens` |
| `firecrawl_plan` | object | Optional plan metadata (see below) |
| `campaign_context` | object | ICP + sender summary from `icp.json` + `run_payload.json` |
| `funnel_steps` | array | Full deterministic funnel labels (10 steps) |
| `by_workflow_step` | object | Spend buckets: `seeding`, `apollo_contact`, `enrichment` |
| `by_source` | object | Vendor totals: `apify`, `apollo`, `firecrawl`, `llm` |
| `by_run` | array | One row per manifest entry in `cost_runs.json` |
| `totals` | object | Campaign rollup |
| `note` | string | Operator notes on billing basis |

## Totals

| Field | Description |
|-------|-------------|
| `usd_total` | Sum of all spend (single headline number) |
| `contactable_leads` | Rows in `stages/04_apollo_contactable.csv` |
| `per_contactable_usd` | `usd_total / contactable_leads` |

There is **no** separate “estimated” column. Uncertain lines use `basis` and `display_label` (e.g. `LLM (est) — gemini`).

## By workflow step

Each key (`seeding`, `apollo_contact`, `enrichment`):

| Field | Description |
|-------|-------------|
| `usd` | Total USD for that funnel stage |
| `runs` | Number of manifest runs in this stage |

## By source

Each vendor key:

| Field | Description |
|-------|-------------|
| `usd` | Total USD attributed to vendor |
| `display_label` | UI label (`Apify`, `Apollo`, `Firecrawl`, `LLM (est) — gemini`) |
| `basis` | How USD was derived (see Billing basis) |
| `is_est` | `true` when USD is modeled, not vendor API |
| `credits` | Apollo or Firecrawl credits (when applicable) |
| `tokens` | LLM tokens (when applicable) |
| `providers` | LLM token split, e.g. `{ "gemini": 285283 }` |
| `plan_credits` | Firecrawl plan size (e.g. 1000 on free tier) |

## By run

| Field | Description |
|-------|-------------|
| `run_key` | Stable id in `cost_runs.json` |
| `label` | Human label |
| `workflow_step` | `seeding` \| `apollo_contact` \| `enrichment` |
| `run_id` | Pipeline run id when applicable |
| `run_date` | `YYYY-MM-DD` from `started_at_utc` |
| `started_at_utc` | ISO start |
| `ended_at_utc` | ISO end |
| `total_usd` | Run total |
| `outcome` | Stage outcomes (contactable count, leads enriched, etc.) |
| `sources` | Map of line items (see Source line item) |
| `apify_breakdown` | Per-actor Apify rows for this run |
| `apollo_detail` | Apollo attempt stats when present |

## Source line item (`sources.*`)

| Field | Description |
|-------|-------------|
| `usd` | Line USD |
| `display_label` | UI label |
| `basis` | Billing basis code |
| `is_est` | Modeled cost flag |
| `source` | Vendor: `apify`, `apollo`, `firecrawl`, `llm` |
| `workflow_step` | Stage attribution |
| `credits` | Apollo/Firecrawl credits |
| `tokens` | LLM tokens |
| `providers` | LLM provider token map |
| `primary_provider` | Dominant LLM for the run |
| `runs` | Apify run objects (seeding windows) |
| `by_actor` | Apify actor rollup |
| `actor` | Single actor name (e.g. afanasenko jobs line) |
| `optional` | `true` for optional LinkedIn jobs scrape |
| `warning` | Operator warning (broken/expensive actor) |
| `note` | Free text |

## Apify breakdown row (`apify_breakdown[]`)

| Field | Description |
|-------|-------------|
| `actor` | `username/name` from Apify API |
| `runs` | Run count in window |
| `usd` | `usageTotalUsd` sum |
| `run_ids` | Sample Apify run ids |
| `statuses` | e.g. `SUCCEEDED`, `FAILED` |

Afanasenko `linkedin-jobs-scraper` rows are highlighted in HTML when present — they count toward totals even when optional/broken.

## Billing basis (`basis`)

| Code | Vendor | Meaning |
|------|--------|---------|
| `apify_api` | Apify | `usageTotalUsd` from Apify API |
| `apollo_credits` | Apollo | Session `credits_consumed` × `apollo_per_credit` |
| `apollo_credit_estimate` | Apollo | Legacy: org enrich + reveal counts (marked est) |
| `firecrawl_plan_credits` | Firecrawl | Credits on plan; USD often $0 on free tier |
| `firecrawl_flat_rate_est` | Firecrawl | Pages × `firecrawl_per_page` when no plan |
| `llm_flat_rate_est` | LLM | Tokens × `llm_per_1k_tokens` — always shown as **LLM (est)** |

## Firecrawl plan (`firecrawl_plan` in manifest / report)

Set in `cost_runs.json` at campaign level:

```json
"firecrawl_plan": {
  "plan_name": "free",
  "plan_credits": 1000,
  "usd_per_credit": 0
}
```

Per-run `firecrawl.credits` overrides page count (1 scrape ≈ 1 credit on typical plans).

## SPA index (`data/cost_dashboard_index.json`)

Built with `python scripts/build_cost_dashboard.py` (or `--ledger` on the open/build scripts).

| Field | Description |
|-------|-------------|
| `campaigns[]` | Per-campaign rollup (all folders under `data/campaigns/`) |
| `by_client` | USD and lead totals grouped by client |
| `runs[]` | All runs across campaigns |
| `totals` | Global USD, campaign count, run count, client count |

Live UI: `GET /dashboard` (hash routes for client/campaign). Offline export: `data/cost_dashboard.html`.

## Manifest input (`cost_runs.json`)

Stage CLIs append rows via `agent/utils/cost_manifest.py`. Apollo rows should include:

```json
"apollo": {
  "credits_consumed": 60,
  "contactable": 25,
  "rejected": 8
}
```

Enrichment rows should include `apify.api_truth_usd`, optional `afanasenko_jobs_usd`, and `firecrawl.credits`.

## LLM providers

Website stage uses `WEBSITE_PROVIDERS = ("gemini", "openai")` — **not Anthropic**. Token usage is attributed per provider in `llm.providers`; dashboard shows `LLM (est) — gemini` when Gemini dominated.

## Commands

```powershell
# Single campaign offline report
python scripts/open_campaign_cost_dashboard.py --campaign automata_us_rnd

# Rebuild SPA index
python scripts/open_campaign_cost_dashboard.py --ledger

# Template preview
python scripts/open_campaign_cost_dashboard.py --preview-template
```
