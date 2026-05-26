# Lead Generation Agent — Claude Context

See the three reference documents for full context:

- **PROJECT.md** — architecture, file structure, tech stack, endpoints, environment variables, how to run
- **REQUIREMENTS.md** — functional and technical requirements, known limitations
- **ROADMAP.md** — what is built and working, what needs fixing, what comes next

## Quick orientation for Claude

- FastAPI + uvicorn on Railway. Entry point: `main.py`.
- 7-stage async pipeline in `agent/pipeline.py` — stages are in `agent/stages/`.
- All external API clients are in `agent/integrations/`.
- Client auth and ICP come from the "LeadGen Clients" Google Sheet (not a local config file).
- Deduplication registry is in a "Dedup" tab on the same Google Sheet.
- LLM primary: `claude-sonnet-4-5`. Fallback chain: Claude → OpenAI → Gemini.
- Apollo Basic plan — expect all leads to be "partial" (no verified personal emails).
- `load_dotenv(override=True)` is required in main.py to override empty system env vars.
- Secrets in `.env` (local dev) or Railway dashboard environment variables (production).

## Current Known Issues (fix before Railway deploy)

1. **Apollo contact search stalls** — `mixed_people/search` rate-limits heavily on Basic plan.
   First complete run took ~2 hours (87-min gap between contact 9 and 10). Need inter-request
   delay + better 429 handling in `agent/integrations/apollo.py` `enrich_contacts()`.

2. **Targeted crawl not yet verified** — `includePaths` added to `firecrawl.py` but the only
   completed run used old server code. First clean run with new code still needed.

3. **feedback_log.json + icp_profile.json are local files** — wiped on Railway redeploy.
   Must be migrated to Google Sheets before production use.

## First Completed Run
`run_20260524_203709_73b3ef` — 60 partial leads, Google Sheet and CSV written, 60 domains
registered in Sheets Dedup tab. All 7 stages ran successfully end-to-end.
