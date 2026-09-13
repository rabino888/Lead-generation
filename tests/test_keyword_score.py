"""Unit tests for deterministic keyword-overlap lead scoring."""
from __future__ import annotations

from datetime import datetime

from agent.models import Contact, InputMode, Lead, SenderProfile, WebsiteAnalysis
from agent.utils.keyword_score import (
    compute_keyword_score,
    extract_lead_corpus,
    normalize_text,
    score_lead,
    tokenize,
)


def _sample_sender() -> SenderProfile:
    return SenderProfile(
        name="Allyjob",
        core_offer="Multilingual candidate sourcing for Spain contact center hubs",
        services=[
            "Multilingual candidate sourcing",
            "Relocation-ready talent for Spain hubs",
        ],
        target_pain_points=[
            "Long time-to-fill for Nordic language seats",
            "Agent churn requiring constant backfill across Spain hubs",
        ],
    )


def _sample_lead(**overrides) -> Lead:
    lead = Lead(
        lead_id="lead_test_1",
        company_name="Acme BPO",
        run_id="run_test",
        client_id="allyjob",
        input_mode=InputMode.CURATED_SEEDS,
        scraped_at=datetime.utcnow(),
        company_linkedin_description=(
            "Leading contact center operator in Barcelona hiring multilingual "
            "agents for Nordic and German customer support."
        ),
        website_analysis=WebsiteAnalysis(
            website_summary=(
                "We operate Spain hubs with seasonal hiring spikes and focus on "
                "talent acquisition for relocation-ready multilingual teams."
            ),
            website_open_roles=["Hiring Nordic speakers in Málaga"],
        ),
        decision_maker=Contact(
            name="Jane Doe",
            title="Talent Acquisition Director",
            linkedin_headline="Head of TA at a Spain contact center",
            linkedin_post_summary="Discussing agent churn and backfill across our hubs.",
        ),
    )
    for key, value in overrides.items():
        setattr(lead, key, value)
    return lead


def test_normalize_and_tokenize_strips_accents_and_punctuation():
    assert normalize_text("  Málaga — España!!!  ") == "malaga espana"
    assert "malaga" in tokenize("Offices in Málaga and Madrid")


def test_extract_lead_corpus_includes_website_and_linkedin():
    corpus = extract_lead_corpus(_sample_lead())
    assert "contact center" in corpus.lower()
    assert "agent churn" in corpus.lower()
    assert "seasonal hiring" in corpus.lower()


def test_compute_keyword_score_strong_overlap():
    result = compute_keyword_score(_sample_lead(), _sample_sender())
    assert result.lead_score >= 40
    assert result.matched_terms
    assert result.score_evidence
    assert any("service" in r or "pain-point" in r for r in result.score_reasons)


def test_compute_keyword_score_no_corpus_returns_zero():
    lead = _sample_lead(website_analysis=None, decision_maker=None, company_linkedin_description=None)
    result = compute_keyword_score(lead, _sample_sender())
    assert result.lead_score == 0
    assert "No website or LinkedIn text" in result.score_reasons[0]


def test_score_lead_populates_model_fields():
    lead = _sample_lead()
    score_lead(lead, _sample_sender())
    assert lead.score_method == "keyword_overlap"
    assert isinstance(lead.lead_score, int)
    assert 0 <= lead.lead_score <= 100
    assert lead.matched_terms
    assert lead.score_evidence
    assert lead.score_reasons
    assert lead.pain_points == []


def test_compute_keyword_score_hiring_first_boosts_open_roles():
    lead = _sample_lead(
        website_analysis=WebsiteAnalysis(
            website_is_hiring="yes",
            website_open_roles=[
                "Multilingual customer support agent",
                "Nordic language contact center lead",
            ],
        ),
    )
    baseline = compute_keyword_score(lead, _sample_sender(), hiring_first=False)
    boosted = compute_keyword_score(lead, _sample_sender(), hiring_first=True)
    assert boosted.lead_score >= baseline.lead_score
    assert any("hiring" in r.lower() for r in boosted.score_reasons)


def test_dict_lead_input_supported():
    payload = {
        "company_description": "BPO contact center in Madrid with multilingual support teams.",
        "linkedin_posts": [{"text": "Struggling with Nordic language seat time-to-fill."}],
    }
    result = compute_keyword_score(payload, _sample_sender())
    assert result.lead_score > 0
    assert result.matched_terms
