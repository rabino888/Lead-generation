# Lead Generation Agent — Requirements

## Business Requirements

### Primary Use Case
Ravi (TBOmedia) speaks directly to prospective B2B clients, manually fills in their
ICP on the LeadGen Clients Google Sheet, and the system automatically produces
qualified lead lists delivered to a Google Sheet in their Drive folder.

### What a Good Lead Looks Like
A useful lead contains:
- Company name, website, industry, location, employee range
- A named decision-maker with at least one of: personal email / LinkedIn / direct phone
- Website intelligence: what services they offer, tech stack, content quality signal
- ICP match score (0–100) explaining why this company fits the client's profile
- A personalised outreach hook tailored to the company's visible pain points
- A recommended first service to pitch

### Lead Tiers

| Tier | Criteria | Delivered |
|------|----------|-----------|
| **Qualified** | Decision-maker found + personal email verified | Top of Google Sheet |
| **Partial** | Company enriched but no personal email (LinkedIn/generic email only) | Bottom section of sheet |

---

## Functional Requirements

### FR-1: Input Modes
The system must accept leads requests in three modes:

1. **Keyword mode** — natural language e.g. `"digital marketing agency Madrid"`
   - Agent extracts location from keyword automatically
   - Searches Apollo with industry inference
   - Falls back to Apify Google Maps for local/niche terms

2. **ICP mode** — structured object: `{industry, location, size_min, size_max, job_titles, keywords}`
   - Searches Apollo directly with structured filters
   - No keyword extraction needed

3. **Company list mode** — list of company names or URLs
   - Skips discovery; enriches the provided list directly
   - Uses Apify to find websites for companies that have none

### FR-2: Discovery
- Apollo must be queried first with industry + location + employee cap filters
- If Apollo returns fewer results than the target pool, Apify Google Maps scraper runs as fallback
- Duplicate companies (same domain) must be removed before pre-filtering
- Companies already delivered to this client (dedup registry) must be excluded

### FR-3: Pre-filtering
- Every discovered company must be scored 0–100 against the client's ICP before website crawling
- Scoring must use an LLM prompt with batch processing (10 companies per LLM call)
- Companies scoring below the client's threshold (default: 40) are dropped
- This stage must complete before any Firecrawl credits are spent

### FR-4: Website Enrichment
- Only crawl signal-rich pages: homepage, about, team, services, contact, news index, case studies
- English and Spanish URL path patterns must both be supported
- Crawl must respect a per-run page cap (`FIRECRAWL_MAX_PAGES_PER_RUN`)
- If a targeted crawl returns nothing, fall back to single-page homepage scrape
- Each page must be labelled with its source URL so the LLM knows what section it's reading
- Claude must extract: services offered, tech stack, content quality, SEO signals, blog presence, social proof

### FR-5: Contact Enrichment
- Apollo people search for decision-maker titles matching the client's `icp_job_titles`
- A lead is **qualified** when a verified personal email is found
- A lead is **partial** when only LinkedIn URL or generic company email is available
- Pipeline must loop through additional company batches (max 2 extra batches) until `max_leads` qualified leads are found
- If the quota cannot be met, the run still completes with whatever partial leads exist

### FR-6: Qualification Scoring
- Every lead (qualified and partial) receives an `icp_match_score` and a `lead_score`
- Claude must identify 2–3 specific pain points visible from the company's website
- Claude must identify 1–2 concrete opportunities (what TBOmedia/client service would solve the pain)
- Leads must be sorted by `lead_score` descending

### FR-7: Outreach Hook Generation
- Every lead gets a personalised first-line outreach hook (1–2 sentences)
- Hook must reference something specific from the company's website (not generic)
- A recommended first service must be identified per lead

### FR-8: Output Delivery
- A new Google Sheet must be created for every run inside the client's Drive folder
- Sheet must have two sections: Qualified Leads (top) and Partial Leads (bottom)
- An AI Recommendations section summarising patterns across the run must be appended
- A CSV must be saved locally at `data/clients/{client_id}/runs/{run_id}.csv`
- Both the sheet URL and CSV path must be returned in the run status response

### FR-9: Deduplication
- Every delivered lead's domain must be registered to prevent re-delivery in future runs
- Registry must be stored in Google Sheets ("Dedup" tab) so it survives Railway redeploys
- Admin endpoint must allow clearing the registry for a client

### FR-10: Client Authentication
- Every endpoint (except `/health`) must require a valid `X-API-Key` header
- Keys are stored in the LeadGen Clients sheet
- If a client row has no `api_key`, one must be auto-generated and written back to the sheet
- Clients may only access their own run data

### FR-11: Feedback Loop
- Clients must be able to submit outcomes per lead: `converted | not_interested | wrong_fit | no_response`
- Feedback must be stored per client
- After every 10 feedback entries, the ICP pre-filter threshold must be automatically adjusted:
  - Too many `wrong_fit` → raise threshold (be more selective)
  - High `converted` rate → lower threshold (we're being too picky)

---

## Technical Requirements

### TR-1: Performance
- `POST /run` must return a `run_id` within 2 seconds (pipeline runs as background task)
- A run for 3 leads should complete in under 30 minutes (primary bottleneck: Apollo rate limits)
- LLM calls must use batch processing where possible to reduce total API calls

### TR-2: Reliability
- Every LLM call must have a fallback chain: Claude → OpenAI → Gemini
- Every integration failure must be caught, logged, and treated as partial enrichment (not a crash)
- Pipeline failures must be recorded in run state with a clear error message

### TR-3: Credit/Cost Controls
- Firecrawl: hard cap per run via `FIRECRAWL_MAX_PAGES_PER_RUN` (default: 25)
- Apollo: employee cap `["1,5000"]` prevents Fortune 500 results from wasting credits
- Extra batching loop: capped at 2 additional batches (max 60 companies total per run)

### TR-4: Cloud Persistence
- No run-critical state may be stored only in local files (they are wiped on Railway redeploy)
- Dedup registry → Google Sheets Dedup tab ✅
- Run logs and CSVs → acceptable as local (ephemeral, for debugging only)
- Feedback log → currently local, needs migration to Sheets
- ICP profile/threshold → currently local, needs migration to Sheets

### TR-5: Security
- All secrets via environment variables, never hardcoded
- Admin endpoints protected by a separate `X-Admin-Secret` header
- Clients can only read their own runs (validated by `client_id` on run state)

### TR-6: Observability
- Every pipeline run gets its own structured log file at `data/clients/{client_id}/runs/{run_id}.log`
- All log lines carry the `run_id` for filtering
- Each stage logs entry, key decisions, and completion with counts
- Credit usage (Apollo, Firecrawl, LLM tokens) logged at pipeline end

### TR-7: Deployment
- Must run as a FastAPI + uvicorn app on Railway
- `GET /health` must always return `200 OK` for Railway health checks
- Config via Railway environment variables (no file-based config in production)
- Google credentials passed as `GOOGLE_SERVICE_ACCOUNT` JSON string (not file path)

---

## Known Limitations (as of current build)

| Limitation | Impact | Fix |
|------------|--------|-----|
| Apollo Basic plan has no verified personal emails | All leads are "partial" | Upgrade to Apollo Professional |
| Apollo rate throttling under heavy contact search | Runs take 30–90 min instead of 10 min | Add inter-request delays; upgrade plan |
| `feedback_log.json` stored locally | Lost on Railway redeploy | Migrate to Sheets |
| `icp_profile.json` stored locally | Threshold tuning lost on redeploy | Migrate to Sheets |
| Run tracker is in-memory | Run history lost on server restart | Persist to Sheets or SQLite |
| No completion webhook | Client must poll `/runs/{id}` | Add `callback_url` to RunRequest |
