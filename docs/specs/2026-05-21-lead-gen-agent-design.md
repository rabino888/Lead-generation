# Lead Generation Agent — Design Spec
**Date:** 2026-05-21
**Project:** `C:\Users\ravi_\TBO-agents\Lead generation\`
**Author:** Ravi (TBOmedia)

---

## 1. Purpose

A fully qualified B2B lead generation agent built as a FastAPI service on Railway. Dual-purpose:

1. **Internal use** — TBOmedia uses it to find its own clients (agencies, SMEs, startups needing marketing, AI, automation)
2. **External product** — Sold as a paid service to other B2B businesses, each with their own isolated client profile and feedback loop

The agent emphasises **quality over quantity**. It only counts a lead as qualified if a named decision-maker with a direct email has been found. Everything else is collected but treated as partial.

---

## 2. Input Modes

The agent accepts three input modes, selectable per run:

| Mode | Input | Discovery |
|------|-------|-----------|
| **Keyword** | `"marketing agencies Madrid"` | Apollo searches by keyword |
| **Structured ICP** | Industry, location, size, job titles, keywords | Apollo searches by ICP parameters |
| **Company List** | Uploaded CSV of company names ± URLs | Skips discovery; Apify finds missing websites/LinkedIn |

`max_leads` is always configurable. The quota counts **qualified leads only** (named decision-maker + direct email found). Partial leads do not count toward the quota.

---

## 3. Lead Data Model

A complete lead record contains the following fields:

### Discovery
- `company_name`
- `website`
- `industry`
- `location` (city, country)
- `company_size` (employee range)
- `founded_year`
- `company_linkedin_url`

### Apollo Intelligence Signals
- `intent_topics` — topics the company is actively researching
- `intent_strength` — low / medium / high buying intent
- `technologies_used` — Apollo technographic data (current tech stack)
- `funding_round` — recent funding signals
- `hiring_signals` — roles currently being hired (reveals pain points)
- `growth_signals` — headcount growth trend
- `job_change_alert` — decision-maker recently changed role (hot signal)
- `apollo_lead_score` — Apollo's own proprietary score

### Contacts (Apollo Enrichment)
- `decision_maker_name`
- `decision_maker_title`
- `decision_maker_email` ← **required for qualified status**
- `decision_maker_linkedin_url`
- `decision_maker_direct_phone` (personal/mobile, where Apollo has it)
- `company_phone`
- `company_generic_email` (fallback for partial leads)

### Website Analysis (Firecrawl + Claude)
- `tech_stack_detected`
- `services_offered`
- `content_quality_score` (1–10)
- `seo_health` (basic signals: missing meta, thin content, etc.)
- `has_chatbot` (boolean)
- `has_blog` (boolean + date of last post)
- `social_proof` (testimonials / case studies present?)
- `website_summary` (2–3 sentence Claude summary)

### Qualification (Claude)
- `icp_match_score` (0–100)
- `lead_score` (0–100 overall)
- `pain_points` (list: what they're missing or doing poorly)
- `opportunities` (list: specific services to pitch)
- `qualification_notes` (Claude's reasoning)

### Outreach
- `personalized_hook` (2–3 sentence cold outreach opening, references specific pain points)
- `recommended_first_service` (the #1 thing to pitch)

### Meta
- `run_id`
- `client_id`
- `input_mode` (keyword / icp / company_list)
- `lead_source` (apollo / apify)
- `enrichment_status` (complete / partial + reason)
- `scraped_at`

---

## 4. Pipeline — Option C (Apollo-First with Smart Pre-Filter)

Apollo is the primary discovery source. Apify is a fallback only. Claude pre-scores leads cheaply before any expensive scraping occurs.

```
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 1 — DISCOVERY                                            │
│  Apollo searches by keyword / structured ICP                    │
│  Extracts: company list + full Apollo intelligence signals      │
│  ↓                                                              │
│  IF Apollo results < max_leads AND ICP targets local/niche:     │
│  Apify fallback (Google Maps actor / Google Search actor)       │
│  Merge + deduplicate by domain                                  │
└──────────────────────────────┬──────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 2 — PRE-FILTER (Claude, fast + cheap)                    │
│  Claude batch-scores all companies against client ICP           │
│  Layers YOUR ICP logic on top of Apollo's signals               │
│  Drops anything below threshold (default: score < 40)           │
│  Only promising leads proceed — saves Firecrawl credits         │
└──────────────────────────────┬──────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 3 — WEBSITE ENRICHMENT (Firecrawl)                       │
│  Crawls each company website → returns clean Markdown           │
│  Extracts: services, tech stack, blog, SEO signals,             │
│  chatbot presence, social proof, content quality                │
│  If no website found → mark partial, skip to Stage 4            │
└──────────────────────────────┬──────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 4 — CONTACT ENRICHMENT (Apollo)                          │
│  Apollo enrichment API → finds decision-makers by job title     │
│  Returns: name, title, email, LinkedIn, direct phone            │
│  No decision-maker email found → route to partial leads pile    │
│  Agent continues running until max_leads QUALIFIED reached      │
└──────────────────────────────┬──────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 5 — FULL QUALIFICATION (Claude)                          │
│  Analyses: website content + Apollo signals + contact data      │
│  Outputs: lead score, ICP match score, pain points,             │
│  opportunities, qualification notes                             │
└──────────────────────────────┬──────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 6 — HOOK GENERATION (Claude)                             │
│  Writes personalised 2–3 sentence outreach hook                 │
│  References specific pain points found on their website         │
│  Recommends the #1 service to pitch this company                │
└──────────────────────────────┬──────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 7 — REPORT GENERATION                                    │
│  Qualified leads first (sorted by lead_score desc)              │
│  Partial leads appended at end (company generic email)          │
│  → CSV saved to data/clients/{client_id}/runs/                  │
│  → Google Sheet: new tab per run (named: date + keyword)        │
│  → AI recommendations paragraph (batch-level insights)          │
│  → Run summary log (count, partial count, time, est. costs)     │
└─────────────────────────────────────────────────────────────────┘
```

### Company List Mode (Mode 3) — Modified Entry

```
Upload CSV of company names
       ↓
Apify Google Search actor → finds missing website URLs
Apify LinkedIn Company Scraper → finds LinkedIn + employee count
       ↓
Enters pipeline at Stage 2 (pre-filter)
Continues as normal from there
```

---

## 5. Output Format

### Qualified Leads (counts toward quota)
All fields populated. Decision-maker name + direct email present. Sorted by `lead_score` descending.

### Partial Leads (end of document, does not count toward quota)
All fields populated except decision-maker contact. Uses `company_generic_email`. Appended after all qualified leads in both CSV and Google Sheet.

### AI Recommendations Paragraph
One paragraph per run, written by Claude, appended to the bottom of the sheet. Highlights patterns across the batch — e.g. "8 of 20 leads had no chatbot and thin blog content — these are strong candidates for TBOmedia's content + automation package."

### Run Summary Log (logged, not exported)
```
run_id, client_id, started_at, completed_at, input_mode,
keyword/icp_used, qualified_leads, partial_leads,
apollo_credits_used, firecrawl_pages_crawled, llm_tokens_used
```

---

## 6. Client Management

Clients are managed via a Google Sheet ("LeadGen Clients"). The agent reads this sheet on every `/run` call to authenticate the API key and load the client's ICP profile.

### Sheet Structure
| client_id | name | api_key | icp_industry | icp_location | icp_size | active |
|-----------|------|---------|--------------|--------------|----------|--------|
| tbomedia | TBOmedia | sk-xxx | Any | Spain | 1–50 | TRUE |
| client_01 | Acme Corp | sk-abc | E-commerce | UK | 10–200 | TRUE |

Adding a new client = adding a row. Deactivating = set `active` to FALSE.

---

## 7. Per-Client Feedback Loop

Each client has fully isolated data and a feedback loop that tunes their scoring over time.

### Feedback Submission
```
POST /feedback
{
  "run_id": "...",
  "lead_id": "...",
  "outcome": "converted" | "not_interested" | "wrong_fit" | "no_response",
  "notes": "..." (optional)
}
```

### Per-Client Data Isolation
```
data/clients/{client_id}/
├── runs/                    # All run CSVs for this client
├── feedback_log.json        # Their outcome history
└── icp_profile.json         # Their ICP + tuned thresholds
```

### How the Loop Works
1. Client runs agent → gets leads → works them
2. Client submits outcomes via `/feedback`
3. System logs: which signals / industries / sizes converted
4. Next run: pre-filter threshold auto-adjusts based on conversion patterns
5. Over time: agent gets progressively better at finding leads that convert for THIS client

---

## 8. API Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/run` | X-API-Key | Start a lead gen run. Returns `run_id` immediately (async). |
| `GET` | `/runs/{run_id}` | X-API-Key | Check run status + results when complete. |
| `POST` | `/feedback` | X-API-Key | Submit lead outcomes after working them. |
| `GET` | `/health` | None | Railway health check. |

### POST /run — Request Body
```json
{
  "mode": "keyword | icp | company_list",
  "keyword": "marketing agencies Madrid",
  "icp": {
    "industry": "Marketing",
    "location": "Spain",
    "company_size_min": 5,
    "company_size_max": 50,
    "job_titles": ["CEO", "Founder", "Marketing Director"],
    "keywords": ["digital marketing", "SEO"],
    "excluded_keywords": ["recruitment", "freelance"]
  },
  "company_list": ["Company A", "Company B"],
  "max_leads": 50,
  "output_sheet_name": "Madrid Agencies May 2026"
}
```

---

## 9. Project Structure

```
C:\Users\ravi_\TBO-agents\Lead generation\
│
├── main.py                        # FastAPI app + all endpoints
├── railway.toml                   # Railway deployment config
├── requirements.txt               # Python dependencies
├── .env                           # Local secrets (never committed)
├── .gitignore
├── CLAUDE.md                      # Agent context for Claude Code
│
├── agent/
│   ├── pipeline.py                # Orchestrates all 7 stages
│   ├── models.py                  # Pydantic models: RunRequest, Lead, Feedback
│   │
│   ├── stages/
│   │   ├── discovery.py           # Stage 1: Apollo search + signals + Apify fallback
│   │   ├── prefilter.py           # Stage 2: Claude ICP pre-filter
│   │   ├── website.py             # Stage 3: Firecrawl website crawl
│   │   ├── contacts.py            # Stage 4: Apollo contact enrichment
│   │   ├── qualify.py             # Stage 5: Claude deep qualification
│   │   ├── hooks.py               # Stage 6: Claude outreach hook generation
│   │   └── report.py              # Stage 7: CSV + Sheets + recommendations
│   │
│   ├── integrations/
│   │   ├── apollo.py              # Apollo API wrapper
│   │   ├── firecrawl.py           # Firecrawl API wrapper
│   │   ├── apify.py               # Apify actor runner (Maps, LinkedIn, Search)
│   │   ├── sheets.py              # Google Sheets read/write
│   │   └── llm.py                 # Claude primary, OpenAI/Gemini fallback
│   │
│   └── utils/
│       ├── auth.py                # API key → client_id lookup via Sheets
│       ├── logger.py              # Timestamped run logging
│       └── run_tracker.py         # Run state: pending/running/complete/failed
│
├── data/
│   └── clients/
│       └── {client_id}/
│           ├── runs/              # Per-run CSV outputs
│           ├── feedback_log.json  # Outcome history for this client
│           └── icp_profile.json   # Tuned thresholds from feedback
│
└── docs/
    └── specs/
        └── 2026-05-21-lead-gen-agent-design.md   # This document
```

---

## 10. Environment Variables

```bash
# LLMs
ANTHROPIC_API_KEY        # Primary — Claude for all reasoning tasks
OPENAI_API_KEY           # Fallback LLM
GEMINI_API_KEY           # Fallback LLM

# Data sources
APOLLO_API_KEY           # Discovery + contact enrichment + signals
FIRECRAWL_API_KEY        # Company website deep crawl
APIFY_TOKEN              # Fallback discovery (Google Maps, LinkedIn, Search)

# Google
GOOGLE_SERVICE_ACCOUNT   # JSON credentials (service account)
CLIENTS_SHEET_ID         # "LeadGen Clients" management sheet
OUTPUTS_SHEET_ID         # Lead results output sheet

# Agent
AGENT_ENV                # "development" | "production"
```

---

## 11. Error Handling Principles

- Runs are **async** — `/run` returns `run_id` immediately; client polls `/runs/{run_id}` for status
- Each lead is processed independently — one failed URL does not kill the run
- Failed enrichment at any stage is logged with reason; lead is marked `partial` not discarded
- If Firecrawl fails on a URL → lead continues without website analysis (flagged in output)
- If Apollo contact enrichment returns nothing → lead routed to partial pile
- All errors logged with timestamps per run

---

## 12. Future Enhancements (Out of Scope for v1)

- **Auto-ingestion from Registro Mercantil / Companies House** — automatic newly-registered company discovery for Mode 3 (currently: manual CSV upload)
- **Scheduled runs** — per-client cron schedule (currently: on-demand only)
- **Custom scoring model** — train on accumulated feedback data per client
- **CRM webhook push** — POST leads directly to HubSpot, Pipedrive, etc.
- **Multi-contact per company** — find 2–3 decision-makers per company, not just one
