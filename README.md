# Lead Generation Agent

B2B **lead intelligence pipeline**: curated company seeds → deterministic ICP match → Apollo contact reveal → website + LinkedIn enrichment → keyword scoring → Google Sheets / CSV delivery.

Built for operators running **geo- and industry-specific campaigns** where every client’s ICP, offer, and seed sources live in campaign config — not in shared code defaults.

**Not included:** outreach copy, email sequences, or CRM sync. The agent gathers pitch intelligence (hiring signals, open roles, website content, LinkedIn activity).

## Workflow (deterministic only)

| Step | Tool |
|------|------|
| Seeds → ICP → dedup | `scripts/stage_*.py` or `run_deterministic_campaign.py` |
| Company gate | `icp.json` rules — no LLM prefilter |
| Contact gate | Apollo email + DM `linkedin_url` |
| Score | Keyword overlap — no LLM qualify |
| API | `POST /run` (curated seeds or Apollo CSV) uses the same pipeline |

Contactable leads require Apollo **personal email** and **decision-maker LinkedIn URL**. No Google-search LinkedIn fallback.

## Local-only (not published)

These stay on your machine via `.gitignore` — **the pipeline does not read them at runtime**:

- `data/` — campaigns, stage CSVs, cost logs, run outputs
- `outputs/`
- `docs/` — operator playbooks, intake templates, client ICP notes (optional locally)
- `server*` — local dev server logs/scripts
- `.env` and Google credentials

Use `examples/campaign_template/` as the scaffold; copy to `data/campaigns/{id}/` locally.
For architecture detail, see root `PROJECT.md`, `REQUIREMENTS.md`, and `ROADMAP.md`.

## Quick start

### 1. Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest
cp .env.example .env                  # fill API keys
```

### 2. Scaffold a campaign

Copy `examples/campaign_template/` to `data/campaigns/{your_campaign_id}/` and fill:

- `icp.json` — industries, geo, size, job titles, `contact_locations`, `website_analysis_mode`
- `run_payload.json` — `sender` (offer, services, pain points) for scoring
- `seed_sources.json` — how seeds enter the funnel (CSV ingest, LinkedIn jobs, etc.)

Fill `icp.json` fields per `examples/campaign_template/` and your campaign brief.

### 3. Run the deterministic funnel

```powershell
python scripts/run_seed_source.py --campaign {campaign_id}
python scripts/match_campaign_icp.py --campaign {campaign_id}
python scripts/stage_dedupe.py --campaign {campaign_id}
python scripts/stage_pre_apollo.py --campaign {campaign_id}
python scripts/stage_apollo.py --campaign {campaign_id}
python scripts/stage_enrich.py --campaign {campaign_id}
python scripts/stage_score.py --campaign {campaign_id}
python scripts/stage_qa_flags.py --campaign {campaign_id}
```

Or orchestrate stages via `scripts/run_deterministic_campaign.py`.

### 4. Run tests

```powershell
python -m pytest tests/ -q
```

### 5. Start the API (optional)

```powershell
uvicorn main:app --reload
```

`POST /run` accepts a full `RunRequest` body. Production auth is not enabled yet — run locally or behind your own gateway.

## Contactability rules

A lead is **contactable** only when Apollo returns:

1. A revealed **personal email** (not generic inbox only)
2. A **decision-maker LinkedIn URL** on the same person (`linkedin_url` from Apollo)

If Apollo reveals email but no LinkedIn URL, the company is **rejected** at the contacts stage. Set `APOLLO_ALLOW_MISSING_DM_LINKEDIN=1` only for legacy/debug runs.

Stage CSV `04_apollo_contactable.csv` is guarded: merges preserve person fields and refuse email-only or missing-LinkedIn rows.

## Required API keys

| Service | Purpose |
|---------|---------|
| Apollo | People search, email reveal, org enrich |
| Firecrawl | Website map/crawl + careers pages |
| Apify | LinkedIn company/person/posts (optional jobs) |
| Anthropic / OpenAI / Gemini | Website LLM analysis (careers/site JSON) |
| Google (service account) | Sheets output + dedup tabs |

Copy `.env.example` and set keys in `.env` (local) or your host environment (Railway, etc.). **Never commit `.env` or service account JSON.**

## Project layout

```
agent/
  integrations/     # Apollo, Firecrawl, Apify, LLM, Sheets
  stages/           # Pipeline stage implementations
  utils/            # ICP rules, scoring, cost tracking, gates
scripts/            # Operator CLIs (stage_*, run_*, diagnostics)
examples/           # Campaign template (published)
data/campaigns/     # Per-campaign config + stage CSVs (local, gitignored)
tests/              # pytest unit tests
```

## Documentation (in repo)

| File | Contents |
|------|----------|
| [PROJECT.md](PROJECT.md) | Architecture, endpoints, env vars |
| [REQUIREMENTS.md](REQUIREMENTS.md) | Functional requirements |
| [ROADMAP.md](ROADMAP.md) | Built vs planned |
| [SECURITY.md](SECURITY.md) | Secrets and reporting |

Optional local `docs/` folder (gitignored) can hold operator playbooks and client ICP notes.

## Security

See [SECURITY.md](SECURITY.md). Report vulnerabilities privately — do not open public issues for secrets or live client data.

## License

MIT — see [LICENSE](LICENSE).
