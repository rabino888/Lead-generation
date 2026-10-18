"""Tests for humanize_company_name."""
from __future__ import annotations

from agent.utils.company_name import humanize_company_name


def test_noda_ai():
    assert humanize_company_name("Noda AI") == "Noda"


def test_legal_suffixes():
    assert humanize_company_name("Acme Inc.") == "Acme"
    assert humanize_company_name("Foo Bar GmbH") == "Foo Bar"
    assert humanize_company_name("Decoracel S.L.") == "Decoracel"
    assert humanize_company_name("Widgets Ltd") == "Widgets"


def test_keeps_core_when_no_suffix():
    assert humanize_company_name("OpenAI") == "OpenAI"
    assert humanize_company_name("Thomson Reuters") == "Thomson Reuters"


def test_strips_tech_descriptors():
    assert humanize_company_name("Bright Labs") == "Bright"
    assert humanize_company_name("Noda AI Technologies") == "Noda"


def test_empty_and_whitespace():
    assert humanize_company_name("") == ""
    assert humanize_company_name("   ") == ""
    assert humanize_company_name(None) == ""


def test_leading_the():
    assert humanize_company_name("The Acme Company") == "Acme"


def test_real_estate_living_and_alias():
    assert humanize_company_name("Tribeca Living") == "Tribeca"
    assert humanize_company_name("Tribeca Living Real Estate") == "Tribeca"
    assert (
        humanize_company_name("Homy Marbella @ Homy Marbella Real Estate") == "Homy"
    )
    assert humanize_company_name("Homy Marbella Real Estate") == "Homy"


def test_geo_and_realty_trailing():
    assert humanize_company_name("Grande Realty Marbella") == "Grande"
    assert humanize_company_name("Checkmates Marbella") == "Checkmates"
    assert humanize_company_name("The Agency Marbella") == "The Agency"
    assert humanize_company_name("Engel & Völkers Spain") == "Engel & Völkers"


def test_dash_wrapped_descriptor():
    assert humanize_company_name("PROMORA -Consultora Inmobiliaria-") == "PROMORA"


def test_property_management_phrases():
    assert humanize_company_name("Marbella Property Care") == "Marbella"
    assert humanize_company_name("LM Group Real Estate") == "LM"


def test_avoids_useless_stub():
    # Strip industry, but keep geo when the leftover brand would be tiny.
    assert humanize_company_name("Buy Marbella Property") == "Buy Marbella"
