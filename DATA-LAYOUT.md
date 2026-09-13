# Data layout (client → ICP → campaign)

Local runtime data lives under `data/` (gitignored). Canonical structure:

```
data/clients/{client_id}/
  client.json
  icps/{icp_id}/
    meta.json       # display_name, brief, campaign_type
    icp.json        # targeting rules (1 ICP → many campaigns)
  campaigns/{campaign_id}/
    campaign.json   # includes icp_id reference
    enrichment_plan.json
    seed_sources.json
    icp.json        # snapshot of the linked ICP for pipeline tools
    stages/         # 01_raw_seeds … 07_qa_flags
  runs/             # historical delivery CSVs / logs
```

`data/campaigns/{campaign_id}/` is a **compat junction** into
`data/clients/{client}/campaigns/{campaign_id}/` so stage CLIs and
`campaign_config.CAMPAIGNS_ROOT` keep working.

Migrate leftover legacy folders:

```powershell
python scripts/migrate_client_layout.py
```

Gold ICP shape: `data/clients/automata/campaigns/automata_us_rnd/icp.json`
(or the library copy under `data/clients/automata/icps/`).
