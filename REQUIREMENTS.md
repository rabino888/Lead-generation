# Lead Generation Agent — Requirements

## Business Requirements

### Primary Use Case
Operators speak directly to prospective B2B clients, collect their ICP and offer
context, and pass that context in the `/run` request or via a **campaign profile**.
The system produces contactable lead lists delivered to CSV and Google Sheets.
Multiple clients, industries, and geographies must be supported without hard-coded
campaign logic in scripts.

### What a Good Lead Looks Like
A useful lead contains:
- Company name, website, industry, location, employee range
- A named decision-maker with at least one of: personal email / LinkedIn / direct phone
- Website intelligence: what services they offer, tech stack, content quality signal
- ICP match score (0–100) explaining why this company fits the client's profile
- Pain points, evidence, and qualification notes from collected signals
- Recent decision-maker LinkedIn post context when publicly available

### Lead Tiers

| Tier | Criteria | Delivered |
|------|----------|-----------|
| **Delivered / Contactable** | Decision-maker found + personal email verified | Top of Google Sheet |
| **Rejected / Not delivered** | No Apollo-revealed personal email | Counted/logged, not enriched further in normal flow |

---

## Functional Requirements

### FR-1: Input Modes
The system must accept lead requests in five modes:

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

4. **Curated seeds mode** — structured `company_name + website` objects
   - Skips Apollo company discovery and Apify website lookup
   - Deduplicates seeds by domain/name before Apollo contact enrichment
   - Intended for web-directory workflows where prospect companies are collected first

5. **Apollo CSV mode** — path to a manually exported Apollo CSV
   - Imports only rows that already contain a contact email
   - Runs website enrichment, qualification, deduplication, and reporting

Every request must include a `sender` profile with client/offer context:
`client_id`, `name`, `business_description`, `core_offer`, `services`,
`proof_points`, `target_pain_points`, and optional `positioning_notes`.

### FR-2: Discovery
- Apollo must be queried first with industry + location + employee cap filters
- If Apollo returns fewer results than the target pool, Apify Google Maps scraper runs as fallback
- Duplicate companies (same domain) must be removed before pre-filtering
- Companies already delivered to this client (dedup registry) must be excluded

### FR-3: Pre-filtering
- Every discovered company must be scored 0–100 against the client's ICP before website crawling
- Scoring must use an LLM prompt with batch processing (10 companies per LLM call)
- Companies scoring below the client's threshold (default: 40) are dropped
- This stage must complete before Apollo reveal, Firecrawl, or downstream LLM credits are spent

### FR-4: Website Enrichment
- Only crawl signal-rich pages: homepage, about, team, services, contact, news index, case studies
- English and Spanish URL path patterns must both be supported
- Crawl must be capped at 5 pages per company by default
- Total pages crawled must be tracked for usage reporting, but not used as a run-wide blocker
- If a targeted crawl returns nothing, fall back to single-page homepage scrape
- Each page must be labelled with its source URL so the LLM knows what section it's reading
- Claude must extract: services offered, tech stack, content quality, SEO signals, blog presence, social proof

### FR-5: Contact Enrichment
- Apollo people search for decision-maker titles matching the client's `icp_job_titles`
- Apollo `people/match` must reveal the selected person's email before the lead is delivered
- Pipeline must loop through additional company batches (max 2 extra batches) until `max_leads` contactable leads are found
- Leads without a revealed email are rejected/not delivered and should not consume Firecrawl/qualification work
- If the quota cannot be met, the run completes with the contactable leads found or fails clearly if none are found

### FR-6: LinkedIn Decision-Maker Context
- After Apollo reveals a contactable decision-maker, Apify should fetch up to 5 recent public LinkedIn posts for that person
- Revealed contacts should be deduplicated by email/LinkedIn before expensive enrichment and report output
- Post enrichment must be best effort and fail gracefully if there is no LinkedIn URL, no public posts, no Apify token, or actor failure
- Normalized output should include post text, date, engagement fields, URL, inferred interests, and a short summary

### FR-7: Qualification Scoring
- Every delivered/contactable lead receives an `icp_match_score` and a `lead_score`
- Claude must identify specific pain points relevant to the sender's offer
- Claude must label evidence sources for pain points: `website`, `apollo`, `linkedin_post`, or `inferred`
- Claude must identify concrete opportunities that map to the sender's services or close matches
- Claude must not recommend web design, SEO, generic marketing, or IT support unless those are part of the sender's offer
- Leads must be sorted by `lead_score` descending

### FR-8: Output Delivery
- A new Google Sheet must be created for every run inside the client's Drive folder
- Sheet must include delivered/contactable leads and optional not-delivered/rejected rows when passed to report generation
- A CSV must be saved locally at `data/clients/{client_id}/runs/{run_id}.csv`
- Both the sheet URL and CSV path must be returned in the run status response
- Output must include LinkedIn interests/post summary and pain point evidence fields

### FR-10: Deduplication
- Every delivered lead's domain must be registered to prevent re-delivery in future runs
- Registry must be stored in Google Sheets ("Dedup" tab) so it survives Railway redeploys
- Admin endpoint must allow clearing the registry for a client

### FR-11: Request-Driven Run Access
- For the current local/request-driven build, `/run`, `/runs/{run_id}`, and `/feedback` must not depend on LeadGen Clients Sheet auth
- `ClientProfile` must be constructed from `RunRequest.sender` and `RunRequest.icp`
- Admin endpoints may keep `X-Admin-Secret` protection and legacy Clients Sheet utilities

### FR-12: Campaign Profiles (Multi-Client)
Campaign-specific configuration must live under `data/campaigns/{campaign_id}/`, not
in script source code. Each campaign folder must support:

| File | Purpose |
|------|---------|
| `campaign.json` | Discovery config: geo segments, Apollo queries, seed targets, exclusion rules |
| `run_payload.json` | `sender` + `icp` passed to `/run` for enrichment batches |
| `seeds.csv` | Curated company seeds (`company_name`, `website`, `geo_segment`, …) |
| `curated_supplement.json` | Optional manual seed additions |
| `campaign_sheet.json` | Generated — master Google Sheet metadata |
| `batch_state.json` | Generated — per-segment enrichment progress |

Campaign scripts (`build_seed_list.py`, `publish_campaign_sheet.py`,
`run_deterministic_campaign.py`, `upload_campaign_enriched.py`) must accept
`--campaign {campaign_id}` and load all client name, geography, segment order,
Apollo queries, and exclusion rules from `campaign.json` + `run_payload.json`.

No script may hard-code a specific `client_id`, geography (e.g. LATAM), or
segment list. Example campaigns: `tbomedia_bpo`, `allyjob_spain`.

Shared loader: `agent/utils/campaign_config.py`.

### FR-13: Feedback Loop
- Clients must be able to submit outcomes per lead: `converted | not_interested | wrong_fit | no_response`
- Feedback must be stored per client
- After every 10 feedback entries, the ICP pre-filter threshold must be automatically adjusted:
  - Too many `wrong_fit` → raise threshold (be more selective)
  - High `converted` rate → lower threshold (we're being too picky)

---

## Technical Requirements

### TR-1: Performance
- `POST /run` must return a `run_id` within 2 seconds (pipeline runs as background task)
- A run for 3 contactable leads should complete in under 20 minutes
- LLM calls must use batch processing where possible to reduce total API calls

### TR-2: Reliability
- Every LLM call must have a fallback chain: Claude → OpenAI → Gemini
- Every integration failure must be caught, logged, and treated as partial enrichment (not a crash)
- Pipeline failures must be recorded in run state with a clear error message

### TR-3: Credit/Cost Controls
- Firecrawl: max 5 pages per company by default; total page count tracked for usage
- Apollo: employee cap `["1,5000"]` prevents Fortune 500 results from wasting credits
- Extra batching loop: capped at 2 additional batches (max 60 companies total per run)

### TR-4: Cloud Persistence
- No run-critical state may be stored only in local files (they are wiped on Railway redeploy)
- Dedup registry -> Google Sheets Dedup tab
- Run logs and CSVs → acceptable as local (ephemeral, for debugging only)
- Feedback log → currently local, needs migration to Sheets
- ICP profile/threshold → currently local, needs migration to Sheets

### TR-5: Security
- All secrets via environment variables, never hardcoded
- Admin endpoints protected by a separate `X-Admin-Secret` header
- `/run`, `/runs/{run_id}`, and `/feedback` are temporarily unauthenticated for request-driven local operation
- Before external production use, reintroduce auth around request-driven client identity

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
- Apollo phone webhook may deploy as a separate lightweight Railway service

### TR-8: Apollo async phone reveal
- Phone numbers require `people/match` with `reveal_phone_number=true` and a **public HTTPS webhook**
- Apollo POSTs results asynchronously; sync response does not include the phone
- Production webhook: standalone [apollo-phone-webhook](https://github.com/rabino888/apollo-phone-webhook) on Railway
- Import: `scripts/import_apollo_phone_webhooks.py --fetch-railway` (uses `PUBLIC_BASE_URL` + `ADMIN_SECRET`)
- Request: `scripts/request_apollo_phone_reveals.py` (skips CSV rows that already have a phone)
- Do not use free webhook.site inboxes (50-request lifetime cap)

---

## Known Limitations (as of current build)

| # | Limitation | Confirmed | Impact | Fix |
|---|------------|-----------|--------|-----|
| 1 | Request-driven flow needs a clean live smoke test | Done (Allyjob) | — | Allyjob 52-lead campaign verified end-to-end |
| 2 | Apollo Basic may still return few verified personal emails | Yes | Contactable volume may be low | Use paid reveal carefully, Apollo CSV fallback, or add another email provider |
| 3 | Apify LinkedIn posts actor input/output may vary | Pending | Post context may be empty or need actor-specific mapping | Test configured actor and adjust `get_linkedin_profile_posts()` normalization |
| 4 | `/run`, `/runs`, `/feedback` are temporarily unauthenticated | Yes | Not production-safe for external clients | Add request-driven auth before public deployment |
| 5 | `feedback_log.json` stored locally | Yes | Lost on Railway redeploy | Migrate to Sheets "Feedback" tab |
| 6 | `icp_profile.json` stored locally | Yes | Threshold tuning lost on redeploy | Migrate to Sheets "Thresholds" column |
| 7 | Run tracker is in-memory | Yes | Run history lost on server restart | Persist to Sheets or SQLite |
| 8 | Campaign scripts previously hard-coded tbomedia/LATAM | Fixed | Could not onboard new clients without code edits | Use `data/campaigns/{id}/campaign.json` + `--campaign` flag |
| 9 | Apollo phone webhook JSONL on Railway is ephemeral | Yes | Payloads lost on webhook service redeploy | Import to CSV/sheets promptly; optional Railway volume on webhook service |
| 10 | ~13% of Allyjob contacts have no phone in Apollo | Yes | 7/52 leads phone-less | No code fix; Apollo data gap |
