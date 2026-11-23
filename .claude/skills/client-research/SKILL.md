---
name: client-research
description: Research a client company from its website and write its modular outreach library — one master company_brief.md plus one offer, lead magnet(s) and proof file per ICP, and ICP drafts in this repo's icps/{icp_id}/ format. Use when Ravi says "research this company", "what does X do", "find their ICP", "lead magnets for X", "proof statements", or starts a new client or a new ICP for an existing client.
---

# Client research

Turns a client's URL into a modular client library under
`data/clients/{client_id}/`, so each email campaign can pick one ICP and the
offer, lead magnet and proof that belong to it.

Content only: this skill never sends, posts, or contacts anyone. It never
overwrites an existing file without showing Ravi the before/after and getting
an explicit yes.

This skill exists in two repos — `Lead-generation/.claude/skills/client-research/`
and `outreach-content-agent/.claude/skills/client-research/`. Keep the two
`SKILL.md` files identical: when you edit one, copy it over the other. Whichever
repo it runs in, the client library is written to that repo's
`data/clients/{client_id}/` and then offered for sync to the other.

## Output layout

```
data/clients/{client_id}/
  company_brief.md              # MASTER — shared facts + index of everything below
  offers/{n}.json               # one paid offer per ICP
  lead_magnets/{n}.json         # free "use this" magnets — each belongs to ONE offer (offer_id)
  proofs/{n}.json               # one named proof company per offer (main + backup)
  icps/{icp_id}/icp.json        # targeting rules (this repo's schema)
  icps/{icp_id}/meta.json       # display_name, brief, campaign_type
  research/{YYYY-MM-DD}.md      # raw findings + every source link (evidence)
```

**The chain is ICP → offer → lead magnet(s) + proof.** Each offer carries an
`icp_id`. Each lead magnet and each proof carries the `offer_id` it supports
(plus `icp_id`, copied from that offer for easy filtering). The magnet is the
free demo of that offer's paid work, so a magnet never stands alone and never
serves two offers — if two offers need the same demo, write two magnet files.
The link lives on the child file only (no `lead_magnet_ids` list on the offer),
so there is one place to change it.

A campaign later picks `{icp_id, offer_id, lead_magnet_id}`; the magnet's
`offer_id` must equal the campaign's `offer_id`. Nothing from another ICP leaks
into its emails. Ids are integers per folder; continue from the highest
existing id.

Shared facts (sender, product, pricing, voice, compliance) live **once**, in
`company_brief.md`. Anything that changes by audience lives in the per-ICP files.

### File shapes

`offers/{n}.json` — the outreach agent's `FreeValueOffer` shape plus `icp_id`:

```json
{
  "id": 1,
  "icp_id": "insurance_agencies_us",
  "title": "Short name",
  "description": "What it is, what the prospect gets, how it leads to paid work. Include hard limits: what NOT to promise or claim.",
  "deliverability_note": "One line: can the client actually deliver this, and how cheaply?"
}
```

`lead_magnets/{n}.json` — same shape plus the link to its offer and the
"use this" fields:

```json
{
  "id": 1,
  "offer_id": 1,
  "icp_id": "insurance_agencies_us",
  "status": "existing | proposed | approved",
  "title": "Short name",
  "description": "What the prospect gets to use, and what the email must not promise.",
  "deliverability_note": "Template reuse, per-prospect effort, cap per month.",
  "pitch": "One-line 'use this, tell me what you think' ask.",
  "upsell": "The paid implementation inside the linked offer that this demo leads to.",
  "effort": "Per-prospect build time and marginal cost.",
  "compliance": "Vertical-specific flags, or 'none noted'."
}
```

`status`: `existing` = the client already offers it publicly (cite the page);
`proposed` = designed in this research, not yet offered; `approved` = the
client has agreed to deliver it. Only `existing` or `approved` magnets may go
into a campaign. In Stage 1, list the client's real free offers in the brief
even when they fail the "use this" test, so nobody mistakes a proposal for one.

`proofs/{n}.json`:

```json
{
  "id": 1,
  "offer_id": 1,
  "icp_id": "insurance_agencies_us",
  "pain_themes": ["after_hours_intake", "speed_to_lead"],
  "main": {"company": "", "person": "", "title": "", "claim": "exact claim as published", "source_url": "", "source_type": "trade_press | vendor_named_person | vendor_named_company | client_own"},
  "backup": {"company": "", "person": "", "title": "", "claim": "", "source_url": "", "source_type": ""},
  "fit_caveat": "Mismatch to frame around, or 'none'",
  "proof_line": "Draft one-sentence proof line for the opener",
  "verified_on": "YYYY-MM-DD"
}
```

`icps/{icp_id}/icp.json` — copy the field set of an existing ICP in this repo
(e.g. another client's `icp.json`); do not invent fields. `meta.json` holds
`icp_id`, `client_id`, `client_name`, `display_name`, `name`, `brief`,
`campaign_type: "company_outreach"`.

### `company_brief.md` sections (in order)

```
# Company brief — {client_id}
## Sender                 name, site, email, signature block ([PLACEHOLDER] if unknown)
## What they do           product, delivery model, pricing, entry offers (stated)
## Claims (company-stated) unverified numbers from their site
## Core paid offer        the shared offer every ICP builds on
## ICPs                   table: icp_id | audience | offer id | lead magnet ids | proof id  (the chain at a glance)
## Offer by ICP           2–3 lines per ICP + pointer to offers/{n}.json
## Free lead magnets      table: id | offer_id | icp_id | name | what they get | pain themes
## Related companies      table: id | offer_id | icp_id | company | issue solved | result | pain themes  (from proofs/)
## Voice
## Compliance
## Example email          only if the client provided one
## Sources                pointer to research/{date}.md
```

The index tables are summaries — the JSON files are the source of truth. When
a JSON file changes, update its row in the brief in the same edit.

## Inputs — ask once, one question at a time, only for what is missing

1. **Client URL** (required).
2. **`client_id`** — if `data/clients/{client_id}/` exists, read
   `company_brief.md`, `CLIENT.md`, `offers/`, `lead_magnets/`, `proofs/` and
   `icps/*/meta.json` first. Research only the gaps; reuse existing ICP ids.
   Clients can have aliases (a public brand vs the name in this repo) — ask if
   the URL's brand does not match the folder name.
3. **Target ICPs** — which audiences/verticals to build. If not given, run
   Stage 1–2 and ask before Stage 3. Group verticals into one ICP only when they
   share the same buyer, offer and magnet; otherwise split them.

## Rules for every stage

- **Label every claim:** `stated` (on the client's site — quote it),
  `inferred` (your reasoning — say from what), or `third-party` (named source).
- **Disambiguate the name.** Search results for common names mix unrelated
  companies. Filter on the exact domain and record which look-alikes you excluded.
- **Fetch, don't guess, URLs.** Only cite pages you fetched or a search returned.
- **Client-agnostic.** Nothing from one client goes into code, shared scripts,
  or another client's folder (CLAUDE.md).

## Stage 1 — What the company does → `company_brief.md` shared sections

Fetch the homepage, then the linked pages that exist: `/about`, `/pricing`,
`/case-studies`, `/industries`, the product/service pages, and the
diagnostic/booking page (fit statements often live there). One web search on
`"<name>" <domain>` for third-party mentions.

Capture: product/service, pricing and entry offers, delivery model,
integrations, industries listed, value props, claims, named clients and case
studies, founder/team (only names the site publishes), location and markets,
CTAs and existing free offers.

## Stage 2 — ICP → `icps/{icp_id}/` drafts

1. **Stated fit** — quote every explicit "for / not for" line (revenue bands,
   team size, "owners of…", tools). Usually on pricing, diagnostic and FAQ pages.
2. **Firmographics per ICP** — revenue, headcount, ownership, geography, tech
   stack, ops profile, exclusions. Derive headcount from revenue at
   ~$100–200K revenue per employee for service businesses, and state that.
   Narrow to a realistic sweet spot when delivery capacity (solo founder,
   entry price, few case studies) suggests one.
3. **Titles per ICP** — economic buyer + champion titles, with regional
   variants (Managing Director / Principal in AU-UK, President in US, Managing
   Broker for mortgage, Agency Principal for insurance). These go in
   `job_titles`.
4. **Buying signals** — hiring for roles the product replaces, review
   complaints it fixes, tool stack, growth events → `include_keywords` and
   notes in `meta.json` `brief`.
5. Map to `icp.json` fields: `industries`, `excluded_industries`, `geo`,
   `company_size_min/max`, `employee_ranges`, `include_keywords`,
   `exclude_keywords`, `job_titles`, `contact_locations`.

## Stage 3 — Offer and lead magnets per ICP → `offers/`, `lead_magnets/`

**Offer:** the core paid offer cut for this ICP — the wedge (the one pain to
lead with), what gets built first, what expands later. Not a module catalog.

**Lead magnet:** a **working thing the prospect uses**, pitched as "use this,
tell me what you think". It is the free demo of the linked offer's wedge — write
the offer first, then derive the magnet from it and set its `offer_id`. If a
magnet doesn't lead naturally into its offer's paid build, fix one of the two.
Implementation is paid (integrations, live number, monthly plan).

**Reject:** audits, scorecards, "how much you're losing" calculators, PDFs,
checklists, script packs, mystery-shop reports. Audits get ignored and loss
framing is off-brand. Customer-facing tools are different and allowed: a
branded calculator or quote/enquiry form (e.g. mortgage payment calculator,
insurance premium estimator) that the prospect embeds on their own site and
connects to their own CRM themselves.

**No-access rule (hard):** a free magnet must be deliverable with zero access
to the prospect's systems — built only from public information (website,
listings). No logins, CRM/ATS/AMS connections, webhooks on their forms, phone
number porting, or their files/candidate data. The moment the client needs
access, it is paid implementation and the prospect has become a client — put
that step in the offer's `upsell`, not in the magnet.

**Keep only magnets that pass all three:**

| Test | Pass when |
|---|---|
| No access needed | Delivered as a demo number, hosted link or embed code the prospect installs themselves |
| Value to prospect | They can call it, use it, or put it on their site within days |
| Effort for client | Built from a reusable template in ≲30 min per prospect, marginal cost a few dollars |

For clients with agent/automation products, check the default pattern first:
**website → auto-generated knowledge base → deployed voice/text agent** with a
demo number, personalised per prospect. One template usually serves several
ICPs — note the reuse in each magnet's `deliverability_note`.

Per magnet record the pitch, upsell, effort, delivery cap (pre-build for the
top 10–20 prospects/week; cap anything with runtime cost at N/month) and
compliance flags — e.g. no rate quotes or advice for licensed financial or
insurance agents, consent for outbound SMS/calls (TCPA / Spam Act), AI-in-hiring
rules for candidate screening (NYC LL 144, Illinois). Flag; no legal advice.

## Stage 4 — Proof per offer → `proofs/`

Use the client's own case study when it fits the ICP. Otherwise find
third-party proof that the same kind of thing worked for a **named** company in
that vertical:

1. Search `<vertical> AI <magnet type> case study results`. Prefer, in order:
   trade press citing named results → vendor case study with named person and
   title → vendor case study with named company only. Reject unnamed ("a
   regional agency") and vendor-blog revenue projections.
2. **Fetch the source page** and copy the exact claim, person, title, date.
   Never cite a number you did not see on the page.
3. Record main + backup, `source_type`, `fit_caveat` (e.g. "large lender, not a
   broker — frame as 'the big players already run this'") and a draft
   `proof_line`.
4. If no named proof exists, say so in `fit_caveat`, leave `main` empty, and
   propose the workaround (borrow a named end-client result, or make the
   client's first install the proof).

Proof-line rules:
- Market proof — never implied as the client's own work unless it is.
- Never name the vendor in the email ("with an AI receptionist", not the brand).
- One stat or one quote per line, never stacked.

## Write and hand off

1. Write `research/{date}.md` first (all findings + sources), then the JSON
   files, then `company_brief.md`. New files: write directly. Existing files:
   show before/after and wait for a yes.
2. Validate every JSON file, and check every magnet/proof `offer_id` points at
   an existing offer with the same `icp_id`:
   `python -c "import json,sys; [json.load(open(p,encoding='utf-8')) for p in sys.argv[1:]]" <files>`
3. Show Ravi a short summary: ICPs built, offer/magnet/proof ids per ICP, open
   gaps (`[PLACEHOLDER]`s, ICPs without named proof). Not the whole file.
4. Offer — do not do — the sync to the other repo's
   `data/clients/{client_id}/`: copy `company_brief.md`, `research/`,
   `offers/`, `lead_magnets/`, `proofs/` as-is. ICP files differ in shape:
   Lead-generation uses `icps/{icp_id}/icp.json` + `meta.json`; the outreach
   agent uses a flat `icps/{icp_id}.json` (the `icp.json` content). Outreach
   campaigns then point `run_payload.json` at `{icp_id, offer_id, lead_magnet_id}`.
   Client ids must match `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` (no dots — `nebula_ai`,
   not `nebula.ai`).
5. If paid enrichment (treg, Apollo) would sharpen headcount/title data, say so
   and ask before spending.
