# Lead Generation Agent — Implementation Plan
**Spec:** `docs/specs/2026-05-21-lead-gen-agent-design.md`
**Date:** 2026-05-21

---

## Phase 1: Project Scaffold

### Task 1.1 — Project structure & config files
- Create all directories: `agent/stages/`, `agent/integrations/`, `agent/utils/`, `data/clients/`, `outputs/`
- Create `railway.toml`
- Create `requirements.txt` with all dependencies
- Create `.env.example` (template, never the real .env)
- Create `.gitignore`
- Create `CLAUDE.md` with full agent context

### Task 1.2 — Git initialisation
- `git init` in project root
- Initial commit with scaffold

---

## Phase 2: Core Models & Utilities

### Task 2.1 — Pydantic models (`agent/models.py`)
Define all data models:
- `RunRequest` — input for POST /run (mode, keyword, icp, company_list, max_leads, output_sheet_name)
- `ICPProfile` — structured ICP (industry, location, size range, job titles, keywords, excluded keywords)
- `Lead` — full lead record (all fields from spec section 3)
- `RunStatus` — run state tracking (run_id, status, counts, sheet_url, csv_path)
- `FeedbackRequest` — POST /feedback body
- `ClientProfile` — loaded from Google Sheets (client_id, api_key, icp, active)

### Task 2.2 — Logger (`agent/utils/logger.py`)
- Timestamped structured logger
- Per-run log file saved to `data/clients/{client_id}/runs/{run_id}.log`
- Console output for Railway logs

### Task 2.3 — Run tracker (`agent/utils/run_tracker.py`)
- In-memory run state dict (run_id → RunStatus)
- Methods: `create_run()`, `update_run()`, `get_run()`
- Persists run summary to JSON on completion

### Task 2.4 — Auth (`agent/utils/auth.py`)
- `authenticate(api_key)` → reads "LeadGen Clients" Google Sheet → returns `ClientProfile` or raises 401
- Caches sheet data for 5 minutes to avoid hitting Sheets API on every call

---

## Phase 3: Integrations

### Task 3.1 — Google Sheets client (`agent/integrations/sheets.py`)
- `read_clients_sheet()` → returns list of ClientProfile
- `write_leads_to_sheet(sheet_name, qualified_leads, partial_leads, recommendations)` → creates new tab, writes leads, appends partial section, appends recommendations paragraph
- Uses Google service account credentials from `GOOGLE_SERVICE_ACCOUNT` env var

### Task 3.2 — Apollo client (`agent/integrations/apollo.py`)
- `search_companies(icp, max_results)` → company search with full signal extraction (intent, tech, funding, hiring, growth signals)
- `enrich_contacts(company, job_titles)` → find decision-makers, return name/title/email/LinkedIn/direct phone + company generic email
- Handles Apollo rate limits gracefully (retry with backoff)
- Logs credits used per call

### Task 3.3 — Firecrawl client (`agent/integrations/firecrawl.py`)
- `crawl_website(url)` → crawl company website, return clean Markdown
- `scrape_page(url)` → single page scrape fallback
- Handles failed URLs gracefully (returns None, logs reason)
- Respects Firecrawl rate limits

### Task 3.4 — Apify client (`agent/integrations/apify.py`)
- `google_maps_search(keyword, location, max_results)` → Google Maps actor
- `google_search(query, max_results)` → Google Search actor (find company websites)
- `linkedin_company_search(company_name)` → LinkedIn Company Scraper actor
- Uses `APIFY_TOKEN` env var
- Deduplicates results by domain before returning

### Task 3.5 — LLM client (`agent/integrations/llm.py`)
- `call_claude(prompt, system, max_tokens)` → Anthropic API call (primary)
- `call_llm(prompt, system, max_tokens)` → tries Claude first, falls back to OpenAI, then Gemini
- Logs token usage per call
- JSON response parsing with validation

---

## Phase 4: Pipeline Stages

### Task 4.1 — Stage 1: Discovery (`agent/stages/discovery.py`)
- `run(request, client_profile)` → returns raw company list
- Apollo search with full ICP + signal extraction
- If results < max_leads AND ICP targets local/niche: trigger Apify fallback
- Merge Apollo + Apify results, deduplicate by domain
- Tag each company with `lead_source: apollo | apify`
- For Mode 3 (company_list): use Apify to find missing websites/LinkedIn, skip Apollo discovery

### Task 4.2 — Stage 2: Pre-filter (`agent/stages/prefilter.py`)
- `run(companies, client_profile)` → returns filtered shortlist
- Batch-prompt Claude with all companies at once (one API call)
- Prompt includes: client ICP + Apollo signals per company
- Returns scored list, drops anything below threshold (default 40, tunable per client from icp_profile.json)
- Logs how many were filtered and why

### Task 4.3 — Stage 3: Website enrichment (`agent/stages/website.py`)
- `run(companies)` → returns companies with website analysis added
- Firecrawl crawls each company website → clean Markdown
- Claude extracts structured data from Markdown:
  - tech_stack_detected, services_offered, content_quality_score
  - seo_health, has_chatbot, has_blog + last post date
  - social_proof, website_summary
- If no website or Firecrawl fails → mark partial, continue

### Task 4.4 — Stage 4: Contact enrichment (`agent/stages/contacts.py`)
- `run(companies, client_profile)` → returns companies split into qualified/partial
- Apollo enrichment API → find decision-makers by job titles from ICP
- Returns: name, title, email, LinkedIn, direct_phone
- Also fetches company_generic_email as fallback
- Companies with no decision-maker email → routed to partial pile
- Agent tracks qualified count → continues looping until max_leads reached

### Task 4.5 — Stage 5: Qualification (`agent/stages/qualify.py`)
- `run(leads, client_profile)` → returns fully qualified leads
- Claude analyses: website content + Apollo signals + contact data together
- Structured output per lead:
  - icp_match_score (0-100), lead_score (0-100)
  - pain_points (list), opportunities (list), qualification_notes
- Single Claude call per lead (not batch — needs full context per company)

### Task 4.6 — Stage 6: Hook generation (`agent/stages/hooks.py`)
- `run(leads, client_profile)` → returns leads with outreach copy added
- Claude writes per lead:
  - personalized_hook: 2-3 sentence outreach opener referencing specific pain points
  - recommended_first_service: #1 service to pitch this company
- Can be batched (5-10 leads per Claude call) to save tokens

### Task 4.7 — Stage 7: Report generation (`agent/stages/report.py`)
- `run(qualified_leads, partial_leads, run_metadata)` → writes all outputs
- Sort qualified leads by lead_score descending
- Generate AI recommendations paragraph (one Claude call on the full batch)
- Write CSV: qualified leads first, partial leads section at end
  - Save to `data/clients/{client_id}/runs/{run_id}.csv`
- Write Google Sheet: new tab named "{date} - {keyword}"
  - Qualified leads with all columns
  - Partial leads section with visual separator
  - Recommendations paragraph at bottom
- Return sheet_url + csv_path

---

## Phase 5: Pipeline Orchestrator

### Task 5.1 — Pipeline (`agent/pipeline.py`)
- `run_pipeline(run_id, request, client_profile)` → full async pipeline
- Calls all 7 stages in order
- Updates run_tracker status after each stage
- Handles partial failures gracefully (logs, continues)
- On completion: saves run summary JSON
- On error: marks run as failed with error message

---

## Phase 6: FastAPI Application

### Task 6.1 — Main app (`main.py`)
Implement all 4 endpoints:

**POST /run**
- Validate X-API-Key header → authenticate → load client profile
- Parse RunRequest body
- Create run_id, register in run_tracker as "pending"
- Launch pipeline as background task (FastAPI BackgroundTasks)
- Return: `{ run_id, status: "started", message: "Run started. Poll /runs/{run_id} for results." }`

**GET /runs/{run_id}**
- Validate X-API-Key
- Fetch run status from run_tracker
- If complete: include sheet_url, csv_path, qualified_count, partial_count, recommendations summary
- Return RunStatus

**POST /feedback**
- Validate X-API-Key → get client_id
- Append feedback entry to `data/clients/{client_id}/feedback_log.json`
- If feedback crosses threshold: update `icp_profile.json` pre-filter threshold
- Return: `{ saved: true }`

**GET /health**
- Return: `{ status: "ok", timestamp: "..." }`

---

## Phase 7: Testing & Deployment

### Task 7.1 — Smoke test (local)
- Set up `.env` with real API keys from `C:\Users\ravi_\Desktop\TBOmedia\Credentials\.env`
- Run a small test: keyword "digital marketing agencies Madrid", max_leads=5
- Verify: pipeline runs end to end, CSV generated, Google Sheet populated

### Task 7.2 — Railway deployment
- Create `railway.toml` with correct start command
- `railway up` from project directory
- Verify `GET /health` returns 200
- Run test call against live Railway URL

---

## Dependencies (requirements.txt)

```
fastapi
uvicorn[standard]
pydantic
httpx
anthropic
openai
google-generativeai
apify-client
firecrawl-py
gspread
google-auth
python-dotenv
python-multipart
```

---

## Execution Order

1. Phase 1 (scaffold) → commit
2. Phase 2 (models + utils) → commit
3. Phase 3 (integrations) → commit
4. Phase 4 (pipeline stages) → commit
5. Phase 5 (orchestrator) → commit
6. Phase 6 (FastAPI app) → commit
7. Phase 7 (test + deploy) → final commit
