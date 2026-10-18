# Data layout (client → ICP/ITP → campaign)

Local runtime data lives under `data/` (gitignored). Canonical structure:

```
data/clients/{client_id}/
  client.json                 # client_name, optional brief (new-client intake)
  icps/{icp_id}/              # company-outreach library ICPs
    meta.json                 # display_name, brief, campaign_type
    icp.json                  # targeting rules (1 ICP → many campaigns)
  itps/{itp_id}/              # talent-search library profiles (same shape)
  campaigns/
    in_progress/{campaign_id}/
      campaign.json           # includes icp_id reference
      enrichment_plan.json
      seed_sources.json
      icp.json                # snapshot of the linked ICP for pipeline tools
      stages/                 # 01_raw_seeds … 07_qa_flags
    saved_for_later/{campaign_id}/
    completed/
      company_outreach/{campaign_id}/
      talent/{campaign_id}/
  runs/                       # historical delivery CSVs / logs

# Legacy trees (still read; migrate with scripts/migrate_client_layout.py):
  company_outreach/icps|campaigns
  talent_outreach/itps|campaigns
  campaigns/{campaign_id}/    # flat under the client (pre-status)
```

`data/campaigns/{campaign_id}/` is a **compat junction** into the client
status-bucket campaign folder so stage CLIs and `campaign_config.CAMPAIGNS_ROOT`
keep working.

Migrate leftover legacy folders:

```powershell
python scripts/migrate_client_layout.py
```

Gold ICP shape: `data/clients/automata/icps/` (or a campaign snapshot under
`campaigns/in_progress/…/icp.json`).
