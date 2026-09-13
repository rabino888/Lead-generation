# SPEC — Talent search workflow

**Status:** Spec complete (2026-09-07). Control board + estimate for `talent_search` are live in `/builder`. Stage CLIs (T01–T06) still to build (phases C–E).  
**Audience:** Operator-first (same as list-building control).  
**Companion docs:**
- Pre-run control UI + estimate: [`SPEC-dashboard.md`](SPEC-dashboard.md)
- Live board UI: `/builder` (`agent/dashboard/static/builder.html`)
- Company-path decisions that still apply to **email deliverability**: [`docs/DECISIONS.md`](docs/DECISIONS.md) D3 (personal email = contactable)

This SPEC defines the **people-first pipeline** behind `campaign_type: talent_search`. The dashboard already shows the six-step board and cost model; this file defines stages, schemas, reuse, and how plan JSON drives a run.

---

## 1. Purpose

Build **lists of people** who are strong matches for a **specific job role** (or tight title cluster), then eliminate weak candidates cheaply where possible, and only then spend Apollo on email (and optional phone).

Primary deliverable: a contactable person row (revealed personal email + LinkedIn) suitable for recruiter / talent outreach handoff — **not** company website intel or DM posts.

### Goals

- New campaign type, not a mode bolted onto company outreach.
- Elimination-first product narrative; fixed step order in v1 (matches the design).
- Reuse Apollo reveal, Apify person profile, Sheets dedup patterns, cost tracking, and the control dashboard plan/estimate contracts.
- Industry- and country-agnostic: role / geo / filters live in `data/campaigns/{id}/` only.

### Non-goals (v1)

- Website scrape, company LinkedIn profile/jobs, DM posts (company-outreach modules).
- LLM qualify / LLM prefilter.
- Client self-serve auth (same as dashboard SPEC).
- Visual redesign (live `/builder` UI is authoritative).
- Replacing LinkedIn people discovery with Apollo people search for **seeding** (Apollo is for reveal after qualification).

---

## 2. Locked product decisions

| Decision | Choice |
|----------|--------|
| Campaign type id | `talent_search` |
| Primary object | Person (candidate), keyed by LinkedIn `/in/` URL |
| Deliverable | Candidate with **Apollo-revealed personal email** (phone optional) |
| v1 step order | Seed scrape → **personal profile** → dedupe → ICP match → Apollo email → Apollo phone |
| Board toggles | Steps 1–5 fixed; **phone** is the only optional toggle (matches design) |
| Cost UI | Seed count × pass rates; every seed pays profile before elimination (warn in UI) |
| Plan file | Same `enrichment_plan.json` as company outreach (`campaign_type: talent_search`) |
| Designs | Implement UI in `/builder`, not freeform |

### Why profile before ICP (v1)

Operators need profile text (about, experience, headline, location) to eliminate bad fits. That means **Apify spend on the full seed** before ICP cuts the list. Documented tradeoff; a later optimization may add a cheap seed-field ICP **before** profile without changing the dashboard’s default order until explicitly versioned.

---

## 3. End-to-end workflow

```mermaid
flowchart TD
  Plan[enrichment_plan.json talent_search]
  Seed[T01 person seed scrape]
  Profile[T02 Apify person profile]
  Dedup[T03 dedupe LinkedIn URL]
  ICP[T04 person ICP match]
  Email[T05 Apollo email reveal]
  Phone[T06 Apollo phone optional]
  Sheet[CSV plus Sheets deliverable]
  CostPortal[Post-run cost dashboard]

  Plan -->|seed_count pass rates phone flag| Seed
  Seed --> Profile
  Profile --> Dedup
  Dedup --> ICP
  ICP --> Email
  Email --> Phone
  Email --> Sheet
  Phone --> Sheet
  Sheet --> CostPortal
```

### Step detail

| Step | Id | Vendor | Fail / drop behavior |
|------|-----|--------|----------------------|
| 1. Initial seed scrape | `talent_seed` | Apify (LinkedIn people or jobs→people) | No `/in/` URL → drop from raw seeds |
| 2. Personal profile scrape | `talent_profile` | Apify `apimaestro/linkedin-profile-detail` (default) | Actor fail → row kept with empty profile + QA flag; do not invent data |
| 3. Deduplication | `talent_dedupe` | Local + Sheets ContactDedup | Duplicate LinkedIn URL / email → drop or merge; essential locked |
| 4. ICP match | `talent_icp` | Local rules on profile + seed fields | Below threshold / hard exclusions → rejected CSV |
| 5. Apollo email reveal | `talent_apollo_email` | Apollo `people/match` | No personal email → not delivered |
| 6. Apollo phone reveal | `talent_apollo_phone` | Apollo + webhook | Optional; skip if plan phone off or webhook missing |

---

## 4. Connection to the control dashboard

Designs already encode the talent board (`Main.dc.html`) and campaign picker (`CampaignSelect.dc.html`). Implementation must keep these contracts.

### 4.1 Plan fields (talent)

Extends [`SPEC-dashboard.md`](SPEC-dashboard.md) §7.1:

```json
{
  "schema_version": 1,
  "campaign_id": "talent_backend_eu",
  "campaign_type": "talent_search",
  "updated_at_utc": "2026-09-07T00:00:00+00:00",
  "target_leads": null,
  "seed_count": 500,
  "website_crawler": null,
  "modules": {
    "icp_match": true,
    "dedupe": true,
    "apollo_email": true,
    "apify_dm_profile": true,
    "apify_dm_posts": false,
    "website_scrape": false,
    "apify_company_profile": false,
    "apify_company_jobs": false,
    "apollo_phone": true
  },
  "talent_pass_rates": {
    "after_dedupe": 0.90,
    "after_icp": 0.40,
    "email_success": 0.70
  },
  "talent_seed": {
    "source_type": "linkedin_people",
    "actor_id": null,
    "query_notes": "Senior Backend Engineer · Spain"
  },
  "notes": ""
}
```

Rules:

- `campaign_type` must be `talent_search`.
- Estimator **ignores** company-only modules even if true.
- `modules.apify_dm_profile` stays conceptually “on” for talent (profile step is fixed); UI does not offer turning it off in v1.
- `modules.apollo_phone` maps to the design’s phone toggle.
- `seed_count` + `talent_pass_rates` drive the estimate (defaults: dedupe 90%, ICP 40%, email success 70% — same as design).

### 4.2 Estimate model (must match design)

From the builder talent estimate model:

| Cost component | Formula |
|----------------|---------|
| Seed scrape | `seed_count × seed_scrape_usd` |
| Profile | `seed_count × profile_usd` (every seed) |
| Email | `survivors_after_icp × apollo_credits_per_lead × apollo_per_credit` |
| Phone | `email_successes × apollo_per_credit` if phone on else 0 |
| Dedupe / ICP | $0 |

Where:

- `after_dedupe = seed_count × after_dedupe_rate`
- `survivors_after_icp = after_dedupe × after_icp_rate`
- `email_successes = survivors_after_icp × email_success_rate`

Placeholder unit rates (calibrate from Apify truth): `seed_scrape_usd ≈ 0.01`, `profile_usd ≈ 0.25`, `apollo_per_credit` from `APOLLO_CREDIT_USD`, `apollo_credits_per_lead` default 1.5.

UI warnings (already in design) are required in product copy:

1. Elimination runs **after** profile spend.  
2. Pre-run numbers are `est` until post-run cost portal.

### 4.3 Builder API reuse

Same routes as dashboard SPEC (`/builder/api/campaigns/...`). Talent-specific:

- `GET` campaign returns `campaign_type`, `seed_count`, pass rates, phone flag.
- `POST .../estimate` uses talent model when `campaign_type === talent_search`.
- `PUT .../plan` persists the JSON above.
- Cost portal link: `/dashboard/campaigns/{id}` (actuals after runs).

### 4.4 Runtime mapping (talent)

| Plan / UI | Runtime |
|-----------|---------|
| `seed_count` | Informational for estimate; real volume = rows in T01 |
| `talent_seed.source_type` / actor | Seed CLI config in `seed_sources.json` |
| Phone off | Skip T06; `APOLLO_SKIP_PHONE=1` for any shared Apollo helper |
| Phone on | Require `APOLLO_PHONE_WEBHOOK_URL` (+ public base / secret) |
| Profile actor | `APIFY_LINKEDIN_PERSON_ACTOR` (default `apimaestro/linkedin-profile-detail`) |

---

## 5. Campaign folder layout

Under `data/campaigns/{campaign_id}/` (gitignored data; template later under `examples/`):

| Artifact | Role |
|----------|------|
| `campaign.json` | Display name, `campaign_type: talent_search`, segments |
| `icp.json` | **Person** ICP rules (titles, locations, keywords, exclusions, experience signals) |
| `seed_sources.json` | Enabled talent seed source(s) |
| `run_payload.json` | Sender / client handoff context (same shape as company; scoring services optional) |
| `enrichment_plan.json` | Builder plan (§4.1) |
| `stages/` | Talent stage CSVs (§6) |
| `cost_runs.json` | Appended by stage CLIs (reuse cost manifest) |

Do **not** reuse company `01_raw_seeds` company headers as the only schema — use person headers (§6.2). A shared `stages/` directory is fine if filenames are talent-prefixed or a `campaign_type` discriminator selects writers.

---

## 6. Stage artifacts

Recommended filenames (parallel to company `01`–`07`, talent-prefixed to avoid collisions if a folder is mis-typed):

| Stage | File | Contents |
|-------|------|----------|
| T01 | `stages/T01_raw_people.csv` | Seeded person URLs + lightweight seed fields |
| T02 | `stages/T02_profiles.csv` | Profile enrichment merged onto people |
| T02b | `stages/T02_profile_failed.csv` | Optional QA: actor failures |
| T03 | `stages/T03_deduped.csv` | Unique people after LinkedIn/email dedupe |
| T03b | `stages/T03_dedupe_rejected.csv` | Dupes dropped |
| T04 | `stages/T04_icp_matched.csv` | Survivors |
| T04b | `stages/T04_icp_rejected.csv` | Elimination reasons |
| T05 | `stages/T05_apollo_contactable.csv` | Email revealed |
| T05b | `stages/T05_apollo_rejected.csv` | No email / match fail |
| T06 | `stages/T06_phone_enriched.csv` | Optional phone merge |
| Out | Sheet + local CSV via report stage | Delivered contactable list |

### 6.1 Orchestrator

New entry (name TBD, e.g. `scripts/run_talent_campaign.py`) that:

1. Loads campaign + `enrichment_plan.json` (require `talent_search`).
2. Runs T01→T05 always; T06 if `modules.apollo_phone`.
3. Appends cost manifests; optionally rebuilds cost dashboard.

Company `run_deterministic_campaign.py` must **refuse** or no-op talent campaigns with a clear error (wrong orchestrator).

### 6.2 Person row schema (minimum)

Core identity:

- `person_id` — stable id (`{campaign_prefix}-{n}` or hash of LinkedIn URL)
- `linkedin_url` — normalized `https://www.linkedin.com/in/...` (no query junk)
- `full_name`, `first_name`, `last_name`
- `headline`, `title`, `location`
- `company_name`, `company_linkedin_url` (optional, from profile)
- `source_url`, `seed_source_id`, `notes`

After profile (T02):

- `about`, `experience_summary`, `education_summary` (or structured JSON column)
- `profile_raw_chars`, `profile_scraped_at_utc`
- `profile_status`: `ok` | `failed` | `skipped`

After Apollo (T05):

- `apollo_person_id`
- `email`, `email_status`
- `phone` / `mobile_phone` (T06)
- `apollo_reveal_status`

QA:

- `reject_reason`, `qa_flags`

Normalization: one canonical LinkedIn URL per person (lowercase host, strip trailing slash, strip `?…`).

---

## 7. Stage implementation notes (reuse vs build)

### T01 — Seed scrape

**Build:** register a new seed source type, e.g. `linkedin_people`, in `agent/utils/seed_sources.py` (company registry today only has `csv_ingest` + `linkedin_jobs`).

**Also support:** `csv_ingest` of a person CSV (LinkedIn URL column) for operator-curated lists — zero Apify seed cost.

**Actor:** pick one Apify people/search actor after smoke (cost + field quality). Until chosen, `talent_seed.actor_id` is required in plan/seed_sources config. Do **not** use blocked jobs actors (`afanasenko/linkedin-jobs-scraper`).

**Output:** `T01_raw_people.csv` only rows with parseable `/in/` URLs.

### T02 — Personal profile

**Reuse:** `get_linkedin_person_profile` / person branch of `enrich_decision_maker_linkedin` in [`agent/integrations/apify.py`](agent/integrations/apify.py) (`APIFY_LINKEDIN_PERSON_ACTOR`).

**Change:** run **before** Apollo, keyed by seed LinkedIn URL (today this runs post-Apollo on DM URL).

**Concurrency / cost:** respect `LEAD_COST_CAP` patterns; batch with clear CostTracker attribution per `person_id`. Default skip-posts (`APIFY_SKIP_LINKEDIN_POSTS=1` for talent runs).

### T03 — Deduplication

**Reuse patterns:** [`agent/utils/deduplication.py`](agent/utils/deduplication.py) ContactDedup (email + LinkedIn).

**Talent keys (in order):**

1. Normalized LinkedIn URL  
2. Apollo person id (if already present)  
3. Revealed email (late; mainly cross-run)

Cross-run: mark delivered LinkedIn URLs / emails on Sheets ContactDedup for the client so re-runs do not re-bill.

### T04 — Person ICP match

**Reuse idea:** deterministic rules like [`agent/utils/icp_match.py`](agent/utils/icp_match.py), **not** company-only fields.

**Person ICP (`icp.json` extensions or `person_icp` block):**

| Rule | Examples |
|------|----------|
| Title / headline allow | `job_titles`, regex on headline |
| Title exclude | Staffing, student, “seeking”, wrong seniorities |
| Location | `contact_locations` / person geo (strict: no match → reject) |
| Keywords in about/experience | Must-have skills / domain terms |
| Excluded keywords | Agency, bootcamp coach, etc. |
| Optional tenure / current role signals | From profile JSON when available |

No LLM in v1. Write reject reasons to `T04_icp_rejected.csv` for operator audit (elimination workflow).

### T05 — Apollo email reveal

**Reuse:** Apollo `people/match` helpers in [`agent/integrations/apollo.py`](agent/integrations/apollo.py).

**Build:** match by **LinkedIn URL** (and/or name + domain when URL match fails), reveal personal email. Do **not** run company people-search-from-org as the primary path.

**Gate:** no personal email → `T05_apollo_rejected.csv`, not delivered. Aligns with D3 for “contactable.”

**Credits:** track via CostTracker / stage cost manifest (`credits_consumed` when available).

### T06 — Apollo phone reveal

**Reuse:** [`scripts/request_apollo_phone_reveals.py`](scripts/request_apollo_phone_reveals.py) + webhook import path; Railway webhook service.

Only for T05 contactable rows; skip if plan phone off.

### Report / Sheets

**Reuse:** report stage patterns + Drive folder per client. Person-oriented columns (name, LinkedIn, email, phone, title, location, company, reject-free delivered set). Handoff JSON may omit company website fields.

---

## 8. ICP and seed_sources examples (illustrative)

`icp.json` (talent-oriented):

```json
{
  "campaign_type": "talent_search",
  "job_titles": ["Senior Backend Engineer", "Staff Backend Engineer", "Backend Engineer"],
  "contact_locations": ["Spain"],
  "keywords": ["Python", "distributed systems", "PostgreSQL"],
  "excluded_keywords": ["recruitment", "staffing", "bootcamp"],
  "person_match": {
    "require_title_signal": true,
    "min_keyword_hits": 1
  }
}
```

`seed_sources.json`:

```json
[
  {
    "id": "linkedin_people_backend_es",
    "type": "linkedin_people",
    "enabled": true,
    "config": {
      "actor_id": "REPLACE_AFTER_SMOKE",
      "target_count": 500,
      "query": "Senior Backend Engineer Spain"
    }
  }
]
```

---

## 9. Cost and QA

- Append `cost_runs.json` per stage CLI (same as company funnel).
- Prefer Apify API truth for profile/seed actors in post-run portal.
- QA flags: missing profile, ICP borderline, Apollo match without email, phone webhook timeout.
- Funnel metrics to log: seeds → profiles ok → deduped → ICP pass → email ok → phone ok (mirrors design funnel bars).

---

## 10. Phasing (connect everything)

| Phase | Deliverable |
|-------|-------------|
| **A** | This SPEC + dashboard SPEC aligned |
| **B** | Builder UI (company + talent boards) + plan/estimate APIs |
| **C** | Talent MVP: CSV person ingest → T02–T05 (skip live seed actor if needed) |
| **D** | Live `linkedin_people` seed actor after smoke + cost gate |
| **E** | T06 phone + Sheets delivery + cost portal rows for talent campaigns |
| **F** | Optional: cheap pre-profile ICP pass (new plan version) |

---

## 11. Acceptance criteria

- Operator can save a `talent_search` plan from the designed UI (seed volume, pass rates, phone) and get the same estimate math as `Main.dc.html`.
- A talent run produces person stage CSVs T01–T05 (T06 if phone on) without calling website crawl or company LinkedIn jobs.
- Contactable output requires Apollo personal email.
- Dedupe + ICP are always on; reject CSVs explain eliminations.
- Company orchestrator does not silently run talent campaigns.
- No industry/geo hardcoding in Python defaults.

---

## 12. Open items (smoke before locking actors)

1. Which Apify actor for **people seed** (field coverage, $/100 profiles, LinkedIn ToS/risk).  
2. Apollo `people/match` success rate when only LinkedIn URL is supplied (measure on a 20-person smoke).  
3. Whether failed profiles are ICP-eligible (recommend: **no** — require `profile_status=ok` unless operator force flag).  
4. Calibrate `seed_scrape_usd` / `profile_usd` from billed runs.  
5. ContactDedup sheet column conventions for person-first campaigns vs company DM rows.
