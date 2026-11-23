"""Vendor payloads are inventoried, then matched onto the fields the list stores."""
from __future__ import annotations

from agent.utils.inbound_payload import describe_payload, map_inbound, map_object, map_record


def test_post_map_reads_harvest_content_inside_output_elements():
    payload = {
        "output": {
            "elements": [
                {
                    "content": "Spring drop is live",
                    "linkedinUrl": "https://www.linkedin.com/feed/update/urn:li:activity:9",
                    "postedAt": {"date": "2026-10-01T00:00:00Z"},
                    "engagement": {"likes": 4, "comments": 1},
                }
            ]
        }
    }
    inbound = map_inbound(payload, "post", source="harvestapi.linkedin.user.posts")
    record = inbound["records"][0]
    assert record["text"] == "Spring drop is live"
    assert record["url"].endswith("activity:9")
    assert record["likes"] == 4
    assert record["posted_at"] == "2026-10-01T00:00:00Z"
    assert record["_sources"]["text"] == "content"
    assert "output.elements[].content" in describe_payload(payload)


def test_job_map_reads_a_bare_output_list():
    payload = {"output": [{"jobTitle": "Ecommerce Manager"}, {"title": "Ecommerce Manager"}]}
    inbound = map_inbound(payload, "job", source="apify.linkedin.search.jobs")
    assert [row["title"] for row in inbound["records"]] == [
        "Ecommerce Manager",
        "Ecommerce Manager",
    ]
    assert inbound["records"][0]["_sources"]["title"] == "jobTitle"


def test_job_map_reads_nested_attributes_title():
    payload = {
        "output": {
            "jobs": [
                {"id": "1", "attributes": {"title": "Enfermero/a", "location": "Madrid"}},
                {"id": "2", "attributes": {"translated_title": "Camarero/a"}},
            ]
        }
    }
    inbound = map_inbound(payload, "job", source="company_jobs")
    assert [row["title"] for row in inbound["records"]] == ["Enfermero/a", "Camarero/a"]
    assert inbound["matched"] == 2


def test_company_map_reads_about_when_description_is_absent():
    payload = {"output": {"name": "Avex", "about": "Unified commerce for brands"}}
    mapped = map_object(payload, "company", source="treg.linkedin.company.profile")
    assert mapped["description"] == "Unified commerce for brands"
    assert mapped["name"] == "Avex"
    assert mapped["_sources"]["description"] == "about"


def test_unmatched_post_keeps_the_received_paths():
    payload = {"output": {"elements": [{"id": "1", "author": {"name": "Ada"}}]}}
    inbound = map_inbound(payload, "post", source="harvestapi.linkedin.user.posts")
    assert inbound["matched"] == 0
    assert "output.elements[].id" in inbound["received_paths"]
    assert map_record(payload["output"]["elements"][0], "post")["text"] is None
