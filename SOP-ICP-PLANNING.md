# SOP — ICP planning without paid APIs

**Audience:** Public / self-hosted operators using this repo in Cursor (or another local IDE agent).  
**Why:** Your **local harness** (chat + repo context) knows the client better than any in-app Gemini/OpenAI call. ICP drafting must not use paid vendor LLM keys the way website scrape does.

**Gold file shape:** [`data/campaigns/automata_us_rnd/icp.json`](data/campaigns/automata_us_rnd/icp.json)  
**Skill (Cursor):** [`.cursor/skills/icp-draft/SKILL.md`](.cursor/skills/icp-draft/SKILL.md)

---

## Rule (non-negotiable)

| Allowed for ICP | Not allowed for ICP |
|-----------------|---------------------|
| Cursor / IDE agent chat (your subscription) | Product calls to Gemini, OpenAI, Anthropic, etc. |
| Editing `data/campaigns/{id}/icp.json` on disk | “Generate ICP” buttons that hit paid APIs |
| Local builder save/reload of that file | Apollo / Apify / Firecrawl during ICP drafting |

Enrichment modules (Apollo, Apify, website LLM) come **after** ICP is saved. They are separate spend.

---

## Recommended workflow (lowest friction)

Do **not** bounce browser → terminal → browser for every edit. Treat Cursor as the ICP workbench; treat `/builder` as folder create + review + cost board.

### Step A — Create the campaign stub (browser, once)

1. Start the local portal: `python scripts/serve_cost_dashboard.py`
2. Open http://127.0.0.1:8765/builder?new=1
3. Pick/register client, outreach type, optional short brief
4. Click **Create & open ICP setup**  
   → Creates `data/campaigns/{client}_outreach/` (or `_talent`) with stub `icp.json`

Leave the builder tab open or close it. You will not need it until review.

### Step B — Draft ICP in Cursor (primary work)

1. Stay in **this repo** in Cursor
2. Open or @-mention: `data/campaigns/{campaign_id}/` and the gold example `automata_us_rnd/icp.json`
3. Say something like:  
   > Draft a full Automata-depth `icp.json` for `{campaign_id}` from this brief: …  
   > Ask clarifying questions first. Use the `icp-draft` skill. No paid APIs.
4. Answer the agent’s questions in the **same chat** (industry list, geo, size, title variants, excludes)
5. Let the agent **write** `data/campaigns/{campaign_id}/icp.json` (and optionally `seed_sources.json`)

You never leave Cursor for the planning loop. That is intentional.

### Step C — Review in builder (optional but useful)

1. Return to `/builder?campaign={campaign_id}` (or the open tab)
2. Click **Reload ICP from disk** so the form matches the file
3. Spot-check **Industry**, titles, geo, size, excludes
4. Save only if you edited in the form
5. Click **Continue to enrichment modules** when targeting is good enough

### Step D — Enrichment / run (paid APIs OK here)

Module board + estimates, then stage CLIs / `run_deterministic_campaign.py`.  
Website scrape may use Gemini/OpenAI — that is **enrichment spend**, not ICP construction.

---

## What each surface is for

| Surface | Job |
|---------|-----|
| **Cursor chat** | Plan + write full `icp.json` (back-and-forth) |
| **`/builder` create** | Allocate campaign folder + client + type |
| **`/builder` ICP form** | Review / small edits after Cursor wrote the file |
| **Copy planning prompt** | Optional starter paste if you open a fresh chat — not the preferred loop |
| **Reload ICP from disk** | Sync form after Cursor saved the file |

---

## Anti-patterns (avoid these)

1. Asking the **product** to “AI-fill ICP” with OpenAI/Gemini keys — not supported; do not add it.
2. Drafting a thin ICP (titles + size only) when campaigns need Automata-level `industries`, `geo`, excludes, title variants.
3. Putting industry names only in `include_keywords` — use **`industries`** (UI label: **Industry**).
4. Round-tripping every clarification through the browser. Stay in Cursor for Q&A; reload once.

---

## Checklist before first paid stage

- [ ] `icp.json` exists under `data/campaigns/{id}/`
- [ ] `industries` and/or clear exclude lists set
- [ ] `job_titles` include common variants
- [ ] `contact_locations` set for person geo (Apollo)
- [ ] `company_size_min` / `company_size_max` (or `employee_ranges`) set
- [ ] `website_analysis_mode` chosen (`hiring_first` vs `full`)
- [ ] Operator reviewed file (Cursor and/or builder Reload)

---

## Public operators: one-sentence version

**Create the folder in `/builder`, build `icp.json` entirely in Cursor against that folder (like editing code), reload once in the builder, then turn on enrichment modules.**
