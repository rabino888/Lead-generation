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
