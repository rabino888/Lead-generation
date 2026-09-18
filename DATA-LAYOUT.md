# Data layout (client → ICP/ITP → campaign)

Local runtime data lives under `data/` (gitignored). Canonical structure:

```
data/clients/{client_id}/
  client.json                 # client_name, optional brief (new-client intake)
  company_outreach/
    icps/{icp_id}/
      meta.json               # display_name, brief (ICP description), campaign_type
      icp.json                # targeting rules (1 ICP → many campaigns)
    campaigns/{campaign_id}/
      campaign.json           # includes icp_id reference
      enrichment_plan.json
      seed_sources.json
      icp.json                # snapshot of the linked ICP for pipeline tools
      stages/                 # 01_raw_seeds … 07_qa_flags
  talent_outreach/
    itps/{itp_id}/            # same meta.json + icp.json shape as ICPs
    campaigns/{campaign_id}/
  runs/                       # historical delivery CSVs / logs

# Legacy flat trees (still read for existing clients):
  icps/{icp_id}/
  campaigns/{campaign_id}/
```

`data/campaigns/{campaign_id}/` is a **compat junction** into the client
typed campaign folder so stage CLIs and `campaign_config.CAMPAIGNS_ROOT`
keep working.

Migrate leftover legacy folders:

```powershell
python scripts/migrate_client_layout.py
```

Gold ICP shape: `data/clients/automata/campaigns/automata_us_rnd/icp.json`
(or the library copy under `data/clients/automata/icps/` / typed
`company_outreach/icps/`).
