"""Outreach segmentation is campaign-agnostic: company, roles, buyer, size."""
from __future__ import annotations

from agent.utils.outreach_segments import segment_outreach_rows


def _row(**overrides):
    base = {
        "company_name": "Northwind",
        "website": "https://www.northwind.example",
        "industry": "Manufacturing",
        "company_size": "40",
        "decision_maker_name": "Ada Lovelace",
        "decision_maker_title": "Founder",
        "decision_maker_email": "ada@northwind.example",
        "decision_maker_linkedin": "https://www.linkedin.com/in/ada",
        "website_open_roles": "",
        "website_is_hiring": "no",
        "company_is_hiring": "",
        "company_open_jobs_summary": "",
        "services_offered": "",
    }
    base.update(overrides)
    return base


def test_same_company_picks_economic_buyer_as_first_touch():
    rows = segment_outreach_rows(
        [
            _row(
                decision_maker_name="Grace Hopper",
                decision_maker_title="Vice President of Operations",
                decision_maker_email="grace@northwind.example",
                decision_maker_linkedin="https://www.linkedin.com/in/grace",
            ),
            _row(),
        ],
        company_size_min=10,
        company_size_max=50,
    )
    by_email = {row["decision_maker_email"]: row for row in rows}
    assert by_email["ada@northwind.example"]["segment_contact_use"] == "first_touch"
    assert by_email["grace@northwind.example"]["segment_contact_use"] == "second_thread"
    assert by_email["ada@northwind.example"]["segment_buyer_tier"] == "economic_buyer"
    assert by_email["grace@northwind.example"]["segment_buyer_tier"] == "operator"
    assert by_email["ada@northwind.example"]["segment_priority"] == "A"
    assert by_email["ada@northwind.example"]["segment_company_key"] == "northwind.example"


def test_second_email_for_the_same_person_is_not_a_second_contact():
    rows = segment_outreach_rows(
        [
            _row(decision_maker_email="ada@northwind.example"),
            _row(
                decision_maker_email="ada.lovelace@northwind.example",
                decision_maker_linkedin="http://linkedin.com/in/ada/",
            ),
        ]
    )
    uses = {row["decision_maker_email"]: row["segment_contact_use"] for row in rows}
    assert uses["ada@northwind.example"] == "first_touch"
    assert uses["ada.lovelace@northwind.example"] == "same_person"


def test_size_outside_icp_is_priority_b():
    rows = segment_outreach_rows(
        [_row(company_size="400")],
        company_size_min=10,
        company_size_max=200,
    )
    assert rows[0]["segment_size_band"] == "above_icp"
    assert rows[0]["segment_priority"] == "B"


def test_open_roles_set_the_slice_without_a_role_taxonomy():
    rows = segment_outreach_rows(
        [_row(website_open_roles="Plant manager; Maintenance lead", website_is_hiring="yes")]
    )
    assert rows[0]["segment_opening_status"] == "named_roles"
    assert rows[0]["segment_open_roles"] == "Plant manager; Maintenance lead"
    assert rows[0]["segment_role_group"] == "Plant manager; Maintenance lead"
    assert rows[0]["segment_role_source"] == "open_roles"
    assert rows[0]["segment_slice"] == "Manufacturing · Plant manager; Maintenance lead"


def test_hiring_flag_without_titles_stays_uncaptured():
    rows = segment_outreach_rows([_row(website_is_hiring="yes")])
    assert rows[0]["segment_opening_status"] == "hiring_uncaptured"
    assert rows[0]["segment_role_group"] == "hiring_uncaptured"


def test_icp_role_group_matches_open_roles_before_services():
    groups = [
        {"id": "plant", "label": "Plant operations", "patterns": ["plant manager"]},
        {"id": "finance", "label": "Finance", "patterns": ["accounting"]},
    ]
    rows = segment_outreach_rows(
        [
            _row(
                website_open_roles="Plant manager",
                services_offered="Accounting support",
            )
        ],
        role_groups=groups,
    )
    assert rows[0]["segment_role_group"] == "Plant operations"
    assert rows[0]["segment_role_source"] == "open_roles"


def test_role_group_falls_back_to_services_when_no_roles_were_captured():
    rows = segment_outreach_rows(
        [_row(services_offered="Monthly accounting and audit")],
        role_groups=[{"id": "finance", "label": "Finance", "patterns": ["accounting"]}],
    )
    assert rows[0]["segment_opening_status"] == "none"
    assert rows[0]["segment_role_group"] == "Finance"
    assert rows[0]["segment_role_source"] == "services"


def test_two_companies_each_get_a_first_touch():
    rows = segment_outreach_rows(
        [
            _row(),
            _row(
                company_name="Contoso",
                website="https://contoso.example",
                industry="Software",
                decision_maker_name="Ken",
                decision_maker_title="Director of Sales",
                decision_maker_email="ken@contoso.example",
                decision_maker_linkedin="https://www.linkedin.com/in/ken",
            ),
        ],
        company_size_min=10,
        company_size_max=50,
    )
    uses = {row["segment_company_key"]: row["segment_contact_use"] for row in rows}
    assert uses == {"northwind.example": "first_touch", "contoso.example": "first_touch"}
    priorities = {row["segment_company_key"]: row["segment_priority"] for row in rows}
    assert priorities["northwind.example"] == "A"
    assert priorities["contoso.example"] == "B"


def test_vice_president_is_not_treated_as_president():
    rows = segment_outreach_rows(
        [_row(decision_maker_title="Executive Vice President")]
    )
    assert rows[0]["segment_buyer_tier"] == "operator"
    assert rows[0]["segment_priority"] == "B"
