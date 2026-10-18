"""Tests for outreach language inference."""
from __future__ import annotations

from agent.utils.outreach_language import (
    detect_language_from_text,
    infer_outreach_language,
    languages_from_profile_field,
)


def test_languages_native_first():
    codes = languages_from_profile_field(
        [
            {"name": "English", "proficiency": "Professional working"},
            {"name": "Spanish", "proficiency": "Native or bilingual"},
        ]
    )
    assert codes[0] == "es"
    assert "en" in codes


def test_languages_string_list():
    assert languages_from_profile_field(["French", "German"]) == ["fr", "de"]


def test_detect_spanish_about():
    text = (
        "Soy asesor inmobiliario en Marbella con más de diez años de experiencia "
        "en compraventa de viviendas de lujo en la Costa del Sol y gestión de "
        "carteras para clientes internacionales."
    )
    assert detect_language_from_text(text) == "es"


def test_detect_english_about():
    text = (
        "I am a luxury real estate advisor based in Marbella with over ten years "
        "of experience helping international buyers find exceptional properties "
        "along the Costa del Sol and managing off-market opportunities."
    )
    assert detect_language_from_text(text) == "en"


def test_short_text_inconclusive():
    assert detect_language_from_text("Real Estate Advisor") == ""


def test_english_titles_not_catalan():
    # Job-title soup + "en" often triggers ca; outreach should stay English.
    headline = "Terapeuta en Six Senses"
    experience = (
        "VIP Hostess | Luxury Hospitality & Guest Experience @ Puente Romano Beach Resort "
        "· Jun 2026 - Present · 4 mos; Luxury Real Estate Advisor @ KELLER WILLIAMS Marbella "
        "· Feb 2026 - Present · helping international clients along the Costa del Sol."
    )
    assert detect_language_from_text("", headline, experience, "") == "en"


def test_infer_prefers_linkedin_languages():
    lang = infer_outreach_language(
        about="I work in Marbella selling homes to international clients every day.",
        languages=[{"name": "Dutch", "proficiency": "Native or bilingual"}],
    )
    assert lang == "nl"
