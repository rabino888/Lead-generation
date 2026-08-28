"""
Apollo contactability rules — shared by contacts stage and tests.
"""
from __future__ import annotations

import os
from typing import Optional

from agent.models import Contact


def apollo_requires_dm_linkedin() -> bool:
    """
    When True, a revealed email alone is not enough — Apollo must return linkedin_url
    on the decision-maker. Set APOLLO_ALLOW_MISSING_DM_LINKEDIN=1 to opt out (legacy).
    """
    return os.environ.get("APOLLO_ALLOW_MISSING_DM_LINKEDIN", "").lower() not in (
        "1",
        "true",
        "yes",
    )


def apollo_contactable(
    decision_maker: Optional[Contact],
    *,
    require_dm_linkedin: Optional[bool] = None,
) -> tuple[bool, str]:
    """
    Return (contactable, reject_reason). Contactable = email (+ DM LinkedIn when required).
    """
    if not decision_maker or not decision_maker.email:
        return False, "Apollo did not reveal a contactable decision-maker email"
    require_li = (
        require_dm_linkedin if require_dm_linkedin is not None else apollo_requires_dm_linkedin()
    )
    if require_li and not (decision_maker.linkedin_url or "").strip():
        return False, "Apollo revealed email but no decision_maker LinkedIn URL"
    return True, ""
