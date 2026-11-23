"""Company rows must not inherit the ICP's first industry."""

from agent.models import ICPProfile
from agent.utils.stage_runner import rows_to_raw_companies


def test_blank_industry_is_not_filled_from_the_icp():
    icp = ICPProfile(industry="restaurants", location="Spain")
    rows = [{"company_name": "Vithas", "website": "https://vithas.es", "industry": ""}]
    out = rows_to_raw_companies(rows, icp)
    assert out[0].industry in ("", None)
    assert out[0].location == "Spain"
