# Lead Generation Agent — Roadmap

## Current Status: First Complete Run Done — Pre-Production

The full 7-stage pipeline has completed a real end-to-end run. All integrations verified.
The system is not yet deployed to Railway — currently running locally for testing.

### First Completed Run — `run_20260524_203709_73b3ef`
| Field | Value |
|-------|-------|
| Keyword | `digital marketing agency Madrid` |
| Duration | ~2 hours (22:37 → 00:31) |
| Qualified leads | 0 (Apollo Basic plan has no verified emails) |
| Partial leads | 60 (website intel + scores + hooks on all) |
| Firecrawl pages | 50 |
| LLM tokens | 108,061 |
| Google Sheet | https://docs.google.com/spreadsheets/d/1wPXOF6XnfFJsEcMm06F9hr5MRzM3u7ph6NhPzFeUChw |
| Drive folder | https://drive.google.com/drive/folders/1V3XsGQHJ-eXly4P-N8gWlOzQ_HUs1LBJ |
| Dedup domains registered | 60 (written to Sheets Dedup tab ✅) |

---

## What Is Built and Working

### Core Pipeline (Stages 1–7) — All Verified End-to-End ✅
- **Stage 1 — Discovery:** Apollo search with industry filter + employee cap + progressive
  fallback (full → no-industry → no-location). Apify Google Maps fallback for local searches.
  Keyword/location splitting from natural language. Deduplication vs previous runs.
  Verified: 87 real companies found for "digital marketing agency Madrid".
- **Stage 2 — Pre-filter:** Claude batch scoring (10 companies/call, 2000 tokens). Configurable
  threshold per client (default 40/100). Reliably drops non-ICP companies before Firecrawl.
  Verified: 82/87 passed (correctly dropped Ministerio de Educación, UNED, etc.).
- **Stage 3 — Website Enrichment:** Firecrawl crawl with per-run page cap. Page URLs labelled
  in output for LLM context. Falls back to single-page scrape.
  NOTE: Targeted crawl (`includePaths`) code written but not yet tested in a complete run.
  The verified run used the old (untargeted) crawl. 50 pages used across 40 companies.
- **Stage 4 — Contact Enrichment:** Apollo people search by job title. Qualified vs partial
  classification. Loop with max 2 extra batches of 20 companies to hit `max_leads` quota.
  Verified working — but stalls severely under Apollo Basic rate limits (see Issues).
- **Stage 5 — Qualification:** Claude scores each lead (icp_match_score + lead_score).
  Extracts pain points, opportunities, qualification notes. Leads sorted by score.
  Verified: scores and analysis generated for all 60 partial leads.
- **Stage 6 — Hook Generation:** Claude writes personalised first-line outreach hook per lead.
  Verified: hooks generated for all 60 partial leads.
- **Stage 7 — Report:** CSV saved locally. New Google Sheet created in client's Drive folder.
  Verified: Sheet and CSV both written. AI recommendations block appended (1,463 chars).

### Infrastructure
- FastAPI async server with `POST /run`, `GET /runs/{id}`, `POST /feedback`, `GET /health`
- Admin endpoints: create client, check/reset dedup registry
- Per-client API key auth with 5-minute cache (reads from Google Sheets)
- API key auto-generation: if a client row has no key, one is written back to the sheet
- Structured per-run logging to `data/clients/{client_id}/runs/{run_id}.log`
- Apollo + Firecrawl + LLM token usage tracked per run
- LLM fallback chain: Claude → OpenAI → Gemini (any provider failure is silent)

### Infrastructure — Verified
- Apollo company search: ✅ responsive (11,548 results on direct API test)
- Apollo people search: ✅ works in pipeline (returns empty on Basic plan — expected)
- Google Sheet creation per run: ✅ verified
- Google Drive folder creation per client: ✅ verified
- LLM fallback chain: ✅ Claude primary confirmed working (`claude-sonnet-4-5`)
- API key auth: ✅ working
- API key auto-generation from blank sheet rows: implemented, not yet tested

### Persistence (Cloud-Ready)
- Dedup registry: Google Sheets "Dedup" tab — survives Railway redeploys ✅ (60 domains written)
- Client management: Google Sheets "LeadGen Clients" — source of truth for ICP + keys ✅
- feedback_log.json: ⚠️ still local — lost on Railway redeploy (not yet migrated)
- icp_profile.json: ⚠️ still local — lost on Railway redeploy (not yet migrated)
- run_tracker: ⚠️ in-memory — run history lost on server restart (not yet persisted)

---

## Immediate Next Steps

### 1. Fix Apollo Contact Search Stalling [CRITICAL — blocks production]
**Confirmed in run `203709`:** 87-minute gap between company 9 and company 10 during
contact enrichment. The total run took ~2 hours for 60 leads — unacceptable for production.

Root cause: Apollo Basic plan aggressively rate-limits `mixed_people/search`. Tenacity
retries silently wait and retry, burning time with no visible progress.

Fixes needed:
- Add a 2–3 second inter-request delay between consecutive contact searches
- Surface the HTTP status code in the log (currently swallowed) to confirm it's 429
- Consider skipping contact search entirely for companies where Apollo previously returned
  nothing (the pipeline already labels them partial — don't keep trying)
- Target: <20 minutes for a 3-lead run

### 2. Verify Targeted Crawl in Production
The `includePaths` targeted crawl was implemented but the completed run used old server code.
Needs a fresh complete run to confirm:
- Firecrawl respects path filters (about, team, services, contact, news)
- Credit consumption stays within 25-page cap for a 3-lead run
- LLM receives enough signal from targeted pages

### 3. Migrate Local Files to Google Sheets
Two files still live locally and will be lost on Railway redeploy:
- `feedback_log.json` → new "Feedback" tab in Clients spreadsheet
- `icp_profile.json` → new "Thresholds" column on the Clients sheet

### 4. Deploy to Railway
Pre-requisites: fix Apollo stalling first (a 2-hour run will time out on Railway).
Required steps:
- Set all env vars in Railway dashboard (use `GOOGLE_SERVICE_ACCOUNT` JSON string)
- Push repo to GitHub via Fenix-create936 bot account
- `railway up` from the project directory
- Verify `/health` passes Railway health check
- Run one smoke test from the Railway URL

---

## Short-Term Improvements (Next 1–2 Weeks)

### Better Discovery Quality
- The pre-filter currently drops good agencies with low Apollo data (low score = unknown, not bad)
- Consider treating `score < threshold AND data_completeness < 30%` as "unknown, crawl anyway"
  rather than "bad, drop"
- Add Hunter.io as an email enrichment fallback when Apollo returns no personal email

### Apollo Contact Search Hardening
- Current: one Apollo people search per company, one result used
- Improve: try multiple job title variants if first search returns nothing
- Add rate-limit retry with exponential backoff specifically for Apollo 429 responses

### Run Completion Notification
- Add optional `callback_url` field to `RunRequest`
- On pipeline completion, POST the result JSON to the callback URL
- Enables integration with Zapier/Make/n8n workflows for client notification

### Persist Run History
- `run_tracker` is in-memory — history lost on server restart
- Option A: write each run's summary JSON to a "Runs" tab in the Clients spreadsheet
- Option B: use Railway's attached SQLite volume
- Impact: enables run history endpoint, analytics, billing tracking

---

## Medium-Term (Next Month)

### Email Finding Fallback
Apollo Basic returns verified personal emails for <10% of contacts.
Add a secondary enrichment step after Apollo comes back empty:
1. Find the decision-maker's name from Apollo or website
2. Try Hunter.io `email-finder` API to construct the likely email
3. Verify with Hunter.io `email-verifier`
4. If confidence > 80%, classify as qualified

### Simple Admin Dashboard
A minimal read-only web page (or Notion/Airtable view) showing:
- All client runs with status and link to Google Sheet
- Credit usage per run
- Dedup count per client

### Smarter ICP Inference
Currently uses keyword → fixed industry string mapping.
Improve to have Claude infer the best Apollo industry filters from the keyword + ICP context.
This reduces the need for the no-industry fallback search.

---

## Long-Term Vision

### Multi-Tenant Billing
- Track Apollo credits and Firecrawl pages consumed per client per month
- Add a `billing_tier` to the Clients sheet (free / starter / pro)
- Block runs when a client exceeds their monthly quota

### Automated Client Onboarding
- Webhook from Stripe/payment provider triggers client row creation
- Welcome email with API key sent automatically
- First run auto-triggered with default settings

### Feedback-Trained Scoring
- Accumulate feedback entries across runs
- Use them as few-shot examples in the qualification prompt
- Over time the scoring model learns what "converted" looks like for each client

### Alternative Discovery Sources
- LinkedIn Sales Navigator (when Apollo has gaps)
- Crunchbase for funding-signal discovery
- G2/Capterra for intent data (companies actively evaluating tools)
