# Cost log — example_campaign

- Logged: `2026-08-28T12:00:00+00:00`
- Contactable leads (current): **25**

## Campaign ICP (Step 1 intake)

- Campaign: `example_campaign`
- Client: **Example Client**
- Core offer: One-sentence value proposition for the outreach pitch.
- Primary geo: United States
- Company size: 5–200 employees
- Industries: computer software, information technology
- Job titles: CEO, Founder, CTO, VP Engineering
- Contact locations: United States
- Website analysis mode: `full`
- Outreach payload: `data/campaigns/example_campaign/run_payload.json`

## By workflow step

| Workflow step | Billed USD | Estimated USD | Total |
|---------------|----------:|-------------:|------:|
| Seeding (Apify LinkedIn jobs search) | $0.1500 | $0.0000 | **$0.1500** |
| Apollo contact (org enrich + email reveal) | $1.2000 | $2.0000 | **$3.2000** |
| Enrichment (Firecrawl + Apify LinkedIn + LLM) | $4.5000 | $1.0000 | **$5.5000** |
| **Campaign total** | **$5.6500** | **$3.0000** | **~$8.65** |

## By source

| Source | Billed USD | Estimated USD | Total |
|--------|----------:|-------------:|------:|
| apify | $3.0000 | $0.0000 | **$3.0000** |
| apollo | $1.2000 | $2.0000 | **$3.2000** |
| firecrawl | $0.0000 | $0.8000 | **$0.8000** |
| llm | $0.0000 | $0.6500 | **$0.6500** |

**Per contactable lead (est.):** ~$0.346

## By run

| Run | Step | USD | Outcome |
|-----|------|----:|---------|
| LinkedIn jobs scrape (batch 1) `seed_batch_1` | seeding | $0.1000 | raw_seeds=50 · run_id=— |
| Apollo contact (batch 1) `apollo_batch_1` | apollo_contact | $1.6000 | contactable=25, rejected=8 · run_id=— |
| Firecrawl + Apify enrich (25 leads) `enrich_batch_1` | enrichment | $5.5000 | leads=25 · run_id=— |

## Run detail (sources)

### LinkedIn jobs scrape (batch 1) (`seed_batch_1`)
- Workflow step: `seeding`
- **apify_linkedin_jobs_seed** (apify_linkedin_jobs_seed): $0.1000 (?)

### Apollo contact (batch 1) (`apollo_batch_1`)
- Workflow step: `apollo_contact`
- **apollo_org_enrich** (apollo_org_enrich): ~$0.9000 (~? credits, estimate)
- **apollo_email_reveal** (apollo_email_reveal): ~$0.7000 (~? credits, estimate)

### Firecrawl + Apify enrich (25 leads) (`enrich_batch_1`)
- Workflow step: `enrichment`
- **apify_linkedin_company_person** (apify_linkedin_company_person): $2.9000 (?)
- **firecrawl** (firecrawl): $0.8000 (?)
- **llm** (llm): $0.6500 (?)

_Note: Template data — replace by running open_campaign_cost_dashboard.py --campaign {id}_