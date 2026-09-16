"""Portal-thin campaign.json must load as CampaignConfig."""
from __future__ import annotations

from agent.utils.campaign_config import CampaignConfig, normalize_campaign_config_data


def test_normalize_portal_thin_campaign_json():
    thin = {"name": "Real estate España — Málaga 1-50 (new list)"}
    data = normalize_campaign_config_data(thin, "decoracel_real_estate_espa_a_m_laga_1_50_new_list")
    cfg = CampaignConfig(**data)
    assert cfg.campaign_id.endswith("new_list")
    assert cfg.display_name.startswith("Real estate")
    assert cfg.seed_id_prefix
    assert cfg.segment_order == ["default"]
    assert "default" in cfg.segments


def test_normalize_preserves_automata_style_segments():
    data = normalize_campaign_config_data(
        {
            "campaign_id": "automata_us_rnd",
            "client_id": "automata",
            "client_name": "Automata",
            "display_name": "Automata US",
            "seed_id_prefix": "autous",
            "segment_order": ["US"],
            "segments": {"US": {"target_count": 40, "country_codes": {"United States": "US"}}},
        },
        "automata_us_rnd",
    )
    cfg = CampaignConfig(**data)
    assert cfg.segment_order == ["US"]
    assert cfg.segments["US"].target_count == 40
