# Campaign template

Copy this folder to `data/campaigns/{your_campaign_id}/` (create `data/campaigns/` locally — it is gitignored).

1. Rename `campaign_id` in all three JSON files.
2. Fill `icp.json` (industries, geo, job titles, `contact_locations`, `website_analysis_mode`).
3. Fill `run_payload.json` `sender` block — required for keyword scoring.
4. Add `seeds.csv` with at least `company_name` and `website` columns.
5. Run the stage scripts listed in the root `README.md`, or `run_deterministic_campaign.py`.

Create an empty `stages/` directory; stage scripts write `01_*` … `07_*` CSVs there.
