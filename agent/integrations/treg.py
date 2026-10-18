"""
treg.to catalog client — LinkedIn enrich without Apify actors.

Auth: ``TREG_TOKEN`` (org-scoped or identity with org baked in).
Optional ``TREG_ORG`` → ``X-Treg-Org`` for unpinned identity tokens.
Base: ``TREG_BASE_URL`` (default https://treg.to).

Person enrich uses routed/prepaid catalog endpoints so we do not burn Apify
run minutes for LinkedIn profile + posts. Website crawl stays on Apify.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from agent.utils.logger import log

TREG_BASE_DEFAULT = "https://treg.to"

# Routed LinkedIn person / company
PROFILE_ENDPOINT = "treg.linkedin.user.profile"
FULL_PROFILE_ENDPOINT = "anyapi.linkedin.user.profile.full"
POSTS_ENDPOINT = "harvestapi.linkedin.user.posts"
COMPANY_ENDPOINT = "treg.linkedin.company.profile"
# Company open jobs (prefer domain index; LinkedIn search as fallback)
COMPANY_JOBS_DOMAIN_ENDPOINT = "treg.companies.jobs.search"
COMPANY_JOBS_LI_ENDPOINT = "apify.linkedin.search.jobs"


def treg_token() -> str:
    return (os.environ.get("TREG_TOKEN") or "").strip()


def treg_configured() -> bool:
    return bool(treg_token())


def linkedin_enrich_provider() -> str:
    """
    ``LINKEDIN_ENRICH_PROVIDER=treg|apify`` (default apify).
    Falls back to apify when treg is selected but TREG_TOKEN is missing.
    """
    raw = (os.environ.get("LINKEDIN_ENRICH_PROVIDER") or "apify").strip().lower()
    if raw not in ("treg", "apify"):
        return "apify"
    if raw == "treg" and not treg_configured():
        log.warning(
            "LINKEDIN_ENRICH_PROVIDER=treg but TREG_TOKEN is unset — using apify"
        )
        return "apify"
    return raw


def _base_url() -> str:
    return (os.environ.get("TREG_BASE_URL") or TREG_BASE_DEFAULT).rstrip("/")


def _headers(*, idempotency_key: str = "") -> dict[str, str]:
    token = treg_token()
    if not token:
        raise RuntimeError("TREG_TOKEN is not configured")
    headers = {
        "Accept": "application/json",
        "X-Treg-Token": token,
    }
    org = (os.environ.get("TREG_ORG") or "").strip()
    if org:
        headers["X-Treg-Org"] = org
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _settle_treg_usd(usd: float) -> float:
    if usd <= 0:
        return 0.0
    try:
        from agent.utils.cost_tracker import get_cost_tracker

        tracker = get_cost_tracker()
        if tracker:
            tracker.add_usage(treg_usd=usd)
    except Exception:
        pass
    return usd


def _record_cost_from_response(resp: httpx.Response) -> float:
    """Settle from X-Treg-Cost-Micro (micro-USD). Returns USD charged."""
    raw = resp.headers.get("X-Treg-Cost-Micro") or resp.headers.get("x-treg-cost-micro")
    if raw is None or str(raw).strip() == "":
        return 0.0
    try:
        usd = float(raw) / 1_000_000.0
    except (TypeError, ValueError):
        return 0.0
    return _settle_treg_usd(usd)


def _record_cost_from_payload(data: Any) -> float:
    """Fallback when headers omit cost but body carries costUsd (AnyAPI)."""
    if not isinstance(data, dict):
        return 0.0
    raw = data.get("costUsd")
    if raw is None:
        raw = data.get("cost_usd")
    if raw is None or str(raw).strip() == "":
        return 0.0
    try:
        usd = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return _settle_treg_usd(usd)


def call_catalog(
    endpoint_id: str,
    *,
    method: str = "GET",
    query: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
    timeout: float = 90.0,
    idempotency_key: str = "",
    headers: Optional[dict[str, str]] = None,
) -> tuple[Any, float]:
    """
    Call ``/call/{endpoint_id}``. Returns (parsed JSON body, usd_charged).
    Raises httpx.HTTPStatusError on non-2xx after treg saw the request.
    """
    path = f"{_base_url()}/call/{endpoint_id}"
    q = {k: v for k, v in (query or {}).items() if v is not None and v != ""}
    if q and method.upper() == "GET":
        path = f"{path}?{urlencode(q, doseq=True)}"
    headers_out = _headers(idempotency_key=idempotency_key)
    for key, value in (headers or {}).items():
        if value is not None and str(value) != "":
            headers_out[str(key)] = str(value)
    method_u = method.upper()
    with httpx.Client(timeout=timeout) as client:
        if method_u == "GET":
            resp = client.get(path, headers=headers_out)
        elif method_u == "POST":
            headers_out = {**headers_out, "Content-Type": "application/json"}
            resp = client.post(path, headers=headers_out, json=body or {}, params=q or None)
        else:
            raise ValueError(f"Unsupported method {method}")
    cost = _record_cost_from_response(resp)
    call_id = resp.headers.get("X-Treg-Call-Id") or resp.headers.get("x-treg-call-id") or ""
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = str(resp.json().get("detail") or resp.text[:300])
        except Exception:
            detail = resp.text[:300]
        log.warning(
            "treg %s %s → %s cost=$%.4f call_id=%s detail=%s",
            method_u,
            endpoint_id,
            resp.status_code,
            cost,
            call_id,
            detail,
        )
        resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        data = None
    if cost <= 0:
        cost = _record_cost_from_payload(data)
    log.info(
        "treg %s %s → %s cost=$%.4f call_id=%s",
        method_u,
        endpoint_id,
        resp.status_code,
        cost,
        call_id or "—",
    )
    return data, cost


def _linkedin_handle(profile_url: str) -> str:
    from urllib.parse import unquote

    m = re.search(r"linkedin\.com/in/([^/?#]+)", profile_url or "", re.I)
    if not m:
        return ""
    return unquote(m.group(1).strip().strip("/"))


def _unwrap_routed(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    out = data.get("output")
    if isinstance(out, dict):
        return out
    return data


def treg_served_by(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    meta = data.get("_treg")
    if isinstance(meta, dict) and meta.get("served_by"):
        return str(meta.get("served_by"))
    return ""


def people_from_search_payload(data: Any) -> list[dict[str, Any]]:
    out = _unwrap_routed(data)
    people = out.get("people") if isinstance(out, dict) else None
    if not isinstance(people, list) and isinstance(data, dict):
        raw = data.get("people")
        people = raw if isinstance(raw, list) else None
    if not isinstance(people, list):
        return []
    return [p for p in people if isinstance(p, dict)]


def email_from_find_payload(data: Any) -> str:
    out = _unwrap_routed(data)
    if not isinstance(out, dict):
        return ""
    for key in ("email", "work_email"):
        val = out.get(key)
        if isinstance(val, str) and "@" in val:
            return val.strip()
    return ""


def person_contact_fields(person: dict[str, Any]) -> dict[str, str]:
    first = str(person.get("first_name") or "").strip()
    last = str(person.get("last_name") or "").strip()
    name = str(person.get("full_name") or person.get("name") or "").strip()
    if not name:
        name = " ".join(part for part in (first, last) if part)
    title = str(
        person.get("title") or person.get("job_title") or person.get("headline") or ""
    ).strip()
    email = str(person.get("email") or person.get("work_email") or "").strip()
    linkedin = str(
        person.get("linkedin_url")
        or person.get("linkedin")
        or person.get("profile_url")
        or ""
    ).strip()
    return {
        "name": name,
        "title": title,
        "email": email,
        "linkedin_url": linkedin,
        "first_name": first,
        "last_name": last,
    }


def pick_decision_maker(
    people: list[dict[str, Any]],
    job_titles: list[str],
) -> dict[str, Any] | None:
    """Prefer an ICP title, then a person who already has an email."""
    if not people:
        return None
    from agent.utils.job_titles import title_priority

    def _rank(person: dict[str, Any]) -> tuple[int, int]:
        fields = person_contact_fields(person)
        return (
            title_priority(fields["title"], job_titles),
            0 if fields["email"] else 1,
        )

    return sorted(people, key=_rank)[0]


def _profile_has_work_history(profile: dict[str, Any]) -> bool:
    """True when real experience text exists (not headline-as-current-position)."""
    if not profile:
        return False
    exp = profile.get("experience_summary")
    return isinstance(exp, str) and bool(exp.strip())


def get_linkedin_person_profile(profile_url: Optional[str]) -> dict:
    """Same shape as ``apify.get_linkedin_person_profile`` (incl. work history)."""
    from agent.integrations.apify import (
        _EDUCATION_KEYS,
        _EXPERIENCE_KEYS,
        _normalize_linkedin_url,
        _summarize_profile_sections,
        current_position_from_experience,
        get_linkedin_person_profile_apify,
    )

    profile_url = _normalize_linkedin_url(profile_url)
    if not profile_url:
        return {}
    if not treg_configured():
        return get_linkedin_person_profile_apify(profile_url)

    handle = _linkedin_handle(profile_url)
    body: dict[str, Any] = {"linkedin_url": profile_url}
    if handle:
        body["linkedin_handle"] = handle
    data: Any = None
    try:
        data, _ = call_catalog(
            PROFILE_ENDPOINT,
            method="POST",
            body=body,
            idempotency_key=f"li-profile:{handle or profile_url}",
        )
    except Exception as e:
        log.warning("treg LinkedIn profile failed for %s: %s", profile_url, e)
        return get_linkedin_person_profile_apify(profile_url)

    out = _unwrap_routed(data)
    if not out:
        # Some providers return the profile at top level / in raw
        raw = data.get("raw") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            out = raw
        elif isinstance(raw, list) and raw and isinstance(raw[0], dict):
            out = raw[0]

    name = (
        (out.get("full_name") or out.get("name") or "").strip()
        or " ".join(
            p for p in [out.get("first_name"), out.get("last_name")] if isinstance(p, str)
        ).strip()
    )
    about = (out.get("about") or out.get("summary") or "").strip()
    headline = (out.get("headline") or "").strip()
    location = out.get("location")
    if isinstance(location, dict):
        location = (
            location.get("linkedinText")
            or location.get("full")
            or location.get("city")
            or None
        )
    li_url = (out.get("linkedin_url") or profile_url).strip()

    experience = (
        out.get("experience")
        or out.get("experiences")
        or out.get("positions")
        or out.get("work_experience")
    )
    education = out.get("education") or out.get("educations")
    experience_summary = (out.get("experience_summary") or "").strip() or None
    if not experience_summary:
        experience_summary = _summarize_profile_sections(experience, _EXPERIENCE_KEYS)
    education_summary = (out.get("education_summary") or "").strip() or None
    if not education_summary:
        education_summary = _summarize_profile_sections(education, _EDUCATION_KEYS)
    # Role from experience only — do not treat headline as work history or the
    # Apify fallback never runs on thin treg payloads.
    current_position = (out.get("current_position") or "").strip() or None
    if not current_position:
        current_position = current_position_from_experience(
            experience, headline="", title=""
        )

    result = {
        "name": name or None,
        "linkedin_url": li_url,
        "headline": headline[:500] if headline else None,
        "about": about[:2000] if about else None,
        "location": location if isinstance(location, str) else None,
        "follower_count": out.get("followers") or out.get("follower_count"),
        "experience_summary": experience_summary,
        "education_summary": education_summary,
        "current_position": current_position,
        "_treg_served_by": (
            (data.get("_treg") or {}).get("served_by")
            if isinstance(data, dict)
            else None
        ),
    }

    # Thin treg payload (headline-only) → one Apify pass for work history.
    # Talent T04 sets TREG_SKIP_APIFY_FALLBACK=1 so Apify only runs on T05
    # contactables (see enrich_contactable_work_history) — not all ICP survivors.
    skip_apify = (os.environ.get("TREG_SKIP_APIFY_FALLBACK") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not _profile_has_work_history(result) and not skip_apify:
        log.info(
            "treg LinkedIn profile thin (no experience_summary) for %s — falling back to Apify",
            profile_url,
        )
        apify_prof = get_linkedin_person_profile_apify(profile_url)
    elif not _profile_has_work_history(result) and skip_apify:
        log.info(
            "treg LinkedIn profile thin for %s — Apify fallback deferred (TREG_SKIP_APIFY_FALLBACK)",
            profile_url,
        )
        apify_prof = None
    else:
        apify_prof = None

    if apify_prof:
        # Prefer Apify experience fields; keep treg identity when richer
        for key in (
            "experience_summary",
            "education_summary",
            "current_position",
            "about",
            "headline",
            "location",
            "name",
            "linkedin_url",
            "follower_count",
        ):
            aval = apify_prof.get(key)
            if aval and not result.get(key):
                result[key] = aval
            elif key in (
                "experience_summary",
                "education_summary",
                "current_position",
            ) and aval:
                result[key] = aval
        result["_apify_fallback"] = True

    # Display fallback only after enrichment — headline is not experience.
    if not (result.get("current_position") or "").strip():
        result["current_position"] = current_position_from_experience(
            None,
            headline=headline,
            title=str(out.get("title") or ""),
        )

    return result


def _anyapi_profile_payload(data: Any) -> dict[str, Any]:
    """Unwrap anyapi.linkedin.user.profile.full → profile dict."""
    if not isinstance(data, dict):
        return {}
    out = data.get("output")
    if isinstance(out, dict):
        if out.get("found") is False:
            return {}
        nested = out.get("data")
        if isinstance(nested, dict):
            return nested
        if out.get("firstName") or out.get("experience") or out.get("url"):
            return out
    if data.get("firstName") or data.get("experience"):
        return data
    raw = data.get("raw")
    if isinstance(raw, dict):
        return raw
    return {}


def get_linkedin_person_profile_full(profile_url: Optional[str]) -> dict:
    """
    Full LinkedIn profile via ``anyapi.linkedin.user.profile.full`` (~$0.003–0.0042).

    Prefer this for talent work history. Falls back to Apify when thin/missing.
    """
    from agent.integrations.apify import (
        _EDUCATION_KEYS,
        _EXPERIENCE_KEYS,
        _normalize_linkedin_url,
        _summarize_profile_sections,
        current_position_from_experience,
        get_linkedin_person_profile_apify,
    )

    profile_url = _normalize_linkedin_url(profile_url)
    if not profile_url:
        return {}
    if not treg_configured():
        return get_linkedin_person_profile_apify(profile_url)

    handle = _linkedin_handle(profile_url)
    data: Any = None
    try:
        data, _ = call_catalog(
            FULL_PROFILE_ENDPOINT,
            method="POST",
            body={"url": profile_url},
            idempotency_key=f"li-profile-full:{handle or profile_url}",
            timeout=120.0,
        )
    except Exception as e:
        log.warning("anyapi full LinkedIn profile failed for %s: %s", profile_url, e)
        return get_linkedin_person_profile_apify(profile_url)

    item = _anyapi_profile_payload(data)
    if not item:
        log.info("anyapi full profile empty for %s — Apify fallback", profile_url)
        return get_linkedin_person_profile_apify(profile_url)

    first = (item.get("firstName") or "").strip()
    last = (item.get("lastName") or "").strip()
    name = (
        f"{first} {last}".strip()
        or (item.get("full_name") or item.get("name") or "").strip()
    )
    about = (item.get("about") or item.get("summary") or "").strip()
    headline = (item.get("headline") or "").strip()
    location = item.get("location")
    if isinstance(location, dict):
        location = (
            location.get("linkedinText")
            or location.get("full")
            or location.get("city")
            or None
        )
    public_id = (item.get("publicIdentifier") or "").strip()
    li_url = (item.get("url") or item.get("linkedin_url") or "").strip()
    if not li_url and public_id:
        li_url = f"https://www.linkedin.com/in/{public_id}"
    if not li_url:
        li_url = profile_url

    experience = item.get("experience") or item.get("experiences") or item.get("positions")
    education = item.get("education") or item.get("educations")
    experience_summary = _summarize_profile_sections(experience, _EXPERIENCE_KEYS)
    education_summary = _summarize_profile_sections(education, _EDUCATION_KEYS)

    current_position = current_position_from_experience(
        experience, headline="", title=""
    )
    if not current_position:
        cur = item.get("currentPosition") or item.get("current_position")
        if isinstance(cur, list) and cur and isinstance(cur[0], dict):
            role = (cur[0].get("position") or cur[0].get("title") or "").strip()
            company = (cur[0].get("companyName") or cur[0].get("company") or "").strip()
            if role and company:
                current_position = f"{role} @ {company}"[:300]
            elif role:
                current_position = role[:300]
        elif isinstance(cur, str) and cur.strip():
            current_position = cur.strip()[:300]

    languages = item.get("languages") or item.get("language")

    result = {
        "name": name or None,
        "linkedin_url": li_url,
        "headline": headline[:500] if headline else None,
        "about": about[:2000] if about else None,
        "location": location if isinstance(location, str) else None,
        "follower_count": item.get("followerCount") or item.get("follower_count"),
        "experience_summary": experience_summary,
        "education_summary": education_summary,
        "current_position": current_position,
        "languages": languages,
        "_treg_served_by": FULL_PROFILE_ENDPOINT,
    }

    if not _profile_has_work_history(result):
        log.info(
            "anyapi full profile thin (no experience) for %s — Apify fallback",
            profile_url,
        )
        apify_prof = get_linkedin_person_profile_apify(profile_url)
        if apify_prof:
            for key in (
                "experience_summary",
                "education_summary",
                "current_position",
                "about",
                "headline",
                "location",
                "name",
                "linkedin_url",
                "follower_count",
                "languages",
            ):
                aval = apify_prof.get(key)
                if aval and (
                    key
                    in (
                        "experience_summary",
                        "education_summary",
                        "current_position",
                    )
                    or not result.get(key)
                ):
                    result[key] = aval
            result["_apify_fallback"] = True

    if not (result.get("current_position") or "").strip():
        result["current_position"] = current_position_from_experience(
            None, headline=headline, title=""
        )
    return result


def get_linkedin_profile_posts(
    profile_url: Optional[str],
    max_posts: Optional[int] = None,
) -> dict:
    """Same shape as ``apify.get_linkedin_profile_posts``."""
    from agent.integrations.apify import (
        _default_max_posts,
        _extract_interests,
        _normalize_linkedin_url,
        _normalize_post,
        _summarize_posts,
        detect_hiring_from_posts,
    )

    if max_posts is None:
        max_posts = _default_max_posts()
    profile_url = _normalize_linkedin_url(profile_url)
    if not profile_url:
        return {"posts": [], "interests": [], "summary": None}
    if not treg_configured():
        return {"posts": [], "interests": [], "summary": None}

    handle = _linkedin_handle(profile_url)
    query: dict[str, Any] = {"page": 1}
    if handle:
        query["profilePublicIdentifier"] = handle
    else:
        query["profile"] = profile_url

    try:
        data, _ = call_catalog(
            POSTS_ENDPOINT,
            method="GET",
            query=query,
            idempotency_key=f"li-posts:{handle or profile_url}:{max_posts}",
        )
    except Exception as e:
        log.warning("treg LinkedIn posts failed for %s: %s", profile_url, e)
        return {"posts": [], "interests": [], "summary": None}

    items: list[dict] = []
    if isinstance(data, dict):
        for key in ("elements", "items", "posts", "data"):
            val = data.get(key)
            if isinstance(val, list):
                items = [x for x in val if isinstance(x, dict)]
                break
        if not items and isinstance(data.get("element"), dict):
            items = [data["element"]]
    elif isinstance(data, list):
        items = [x for x in data if isinstance(x, dict)]

    normalized: list[dict] = []
    for item in items[: max(1, max_posts)]:
        # Harvest nests postedAt.date / engagement.likes
        posted = item.get("postedAt")
        if isinstance(posted, dict):
            item = {**item, "postedAt": posted.get("date") or posted.get("timestamp")}
        eng = item.get("engagement")
        if isinstance(eng, dict):
            item = {
                **item,
                "likes": eng.get("likes") or item.get("likes"),
                "comments": eng.get("comments") or item.get("comments"),
            }
        if not item.get("content") and item.get("text"):
            pass
        elif item.get("content") and not item.get("text"):
            item = {**item, "text": item.get("content")}
        post = _normalize_post(item)
        if post.get("text") or post.get("url"):
            normalized.append(post)

    interests = _extract_interests(normalized)
    summary = _summarize_posts(normalized, interests)
    hiring = detect_hiring_from_posts(normalized)
    log.info("treg LinkedIn posts returned %d posts for %s", len(normalized), profile_url)
    return {
        "posts": normalized,
        "interests": interests,
        "summary": summary,
        "is_hiring": hiring,
    }


def get_linkedin_company_profile(company_linkedin_url: Optional[str]) -> dict:
    """Same shape as ``apify.get_linkedin_company_profile`` (best-effort)."""
    from agent.integrations.apify import _normalize_linkedin_url

    company_linkedin_url = _normalize_linkedin_url(company_linkedin_url)
    if not company_linkedin_url or not treg_configured():
        return {}
    try:
        data, _ = call_catalog(
            COMPANY_ENDPOINT,
            method="POST",
            body={"linkedin_url": company_linkedin_url},
            idempotency_key=f"li-co:{company_linkedin_url}",
        )
    except Exception as e:
        log.warning("treg LinkedIn company failed for %s: %s", company_linkedin_url, e)
        return {}
    out = _unwrap_routed(data)
    description = (out.get("description") or out.get("about") or "").strip()
    return {
        "name": out.get("name") or out.get("company_name"),
        "linkedin_url": out.get("linkedin_url") or company_linkedin_url,
        "description": description[:2000] if description else None,
        "industry": out.get("industry"),
        "employee_count": out.get("employee_count") or out.get("staff_count"),
        "website": out.get("website") or out.get("website_url") or out.get("websiteUrl"),
    }


def _domain_from_website(website: Optional[str]) -> str:
    if not website:
        return ""
    raw = website.strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        from urllib.parse import urlparse

        host = (urlparse(raw).netloc or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _job_items(data: Any) -> list:
    """Normalize treg/Apify job payloads.

    Routed company-jobs returns ``{output: {jobs: [...]}}``.
    LinkedIn job search returns the dataset as ``{output: [{title: ...}, ...]}``.
    Treating a list ``output`` as the wrapper dict drops every title.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    output = data.get("output")
    if isinstance(output, list):
        return output
    if isinstance(output, dict):
        data = output
    jobs = data.get("jobs")
    if isinstance(jobs, list):
        return jobs
    for key in ("items", "elements", "data"):
        nested = data.get(key)
        if isinstance(nested, list):
            return nested
        if isinstance(nested, dict):
            inner = nested.get("items") or nested.get("jobs") or nested.get("elements")
            if isinstance(inner, list):
                return inner
    return []


def _job_titles_from_payload(data: Any) -> list[str]:
    titles: list[str] = []
    items = _job_items(data)
    for item in items:
        if not isinstance(item, dict):
            continue
        title = (
            item.get("title")
            or item.get("jobTitle")
            or item.get("job_title")
            or item.get("position")
            or ""
        )
        if isinstance(title, str) and title.strip():
            titles.append(title.strip())
        details = item.get("job_details")
        if isinstance(details, dict):
            t2 = details.get("title")
            if isinstance(t2, str) and t2.strip():
                titles.append(t2.strip())
    # de-dupe preserve order
    seen: set[str] = set()
    out_titles: list[str] = []
    for t in titles:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out_titles.append(t)
    return out_titles


def get_company_open_jobs(
    *,
    company_linkedin_url: Optional[str] = None,
    website: Optional[str] = None,
    company_name: Optional[str] = None,
    max_jobs: int = 5,
) -> dict:
    """
    Open roles for a company via treg (~$0.001–0.009).

    LinkedIn company-page search first, then the company-name job index, then
    the domain index. Returns the same shape as the Apify jobs helpers.
    """
    empty = {"is_hiring": None, "open_jobs_count": 0, "job_titles": [], "summary": None}
    if not treg_configured():
        return dict(empty)

    titles: list[str] = []
    # LinkedIn company page first. Domain job indexes only see careers-site
    # scrapes and miss openings that exist only on the company's LinkedIn tab.
    if company_linkedin_url:
        from agent.integrations.apify import _normalize_linkedin_url

        li = _normalize_linkedin_url(company_linkedin_url) or company_linkedin_url
        if li.endswith("/jobs"):
            li = li[: -len("/jobs")]
        try:
            data, _ = call_catalog(
                COMPANY_JOBS_LI_ENDPOINT,
                method="POST",
                query={
                    "maxItems": max_jobs,
                    "maxTotalChargeUsd": 0.05,
                    "timeout": 90,
                },
                body={
                    # Broad token so the actor returns company-filtered openings
                    "jobTitles": ["a OR e OR the"],
                    "company": [li],
                    "maxItems": max_jobs,
                },
                idempotency_key=f"co-jobs-li:v2:{li}",
                timeout=120.0,
            )
            titles = _job_titles_from_payload(data)[:max_jobs]
        except Exception as exc:
            log.info("treg LinkedIn company jobs miss for %s: %s", li, exc)

    # The LinkedIn jobs actor often returns an empty dataset for a company URL
    # even when openings exist. The company job index matches the name more
    # reliably for small firms, so try that before the domain.
    if not titles and (company_name or "").strip():
        try:
            data, _ = call_catalog(
                COMPANY_JOBS_DOMAIN_ENDPOINT,
                method="POST",
                body={"name": company_name.strip(), "limit": max_jobs},
                idempotency_key=f"co-jobs-name:{company_name.strip().lower()}",
                timeout=90.0,
            )
            titles = _job_titles_from_payload(data)[:max_jobs]
        except Exception as exc:
            log.info("treg company jobs (name) miss for %s: %s", company_name, exc)

    domain = _domain_from_website(website)
    if not titles and domain:
        try:
            data, _ = call_catalog(
                COMPANY_JOBS_DOMAIN_ENDPOINT,
                method="POST",
                body={
                    "domain": domain,
                    "name": (company_name or "").strip() or None,
                    "limit": max_jobs,
                },
                idempotency_key=f"co-jobs-dom:{domain}",
                timeout=90.0,
            )
            titles = _job_titles_from_payload(data)[:max_jobs]
        except Exception as exc:
            log.info("treg company jobs (domain) miss for %s: %s", domain, exc)

    count = len(titles)
    return {
        "is_hiring": "yes" if count > 0 else "no",
        "open_jobs_count": count,
        "job_titles": titles,
        "summary": "; ".join(titles[:5]) if titles else None,
    }


def enrich_decision_maker_linkedin(
    profile_url: Optional[str],
    max_posts: Optional[int] = None,
) -> dict:
    """Combined person enrichment via treg (profile + posts)."""
    from agent.integrations.apify import (
        _default_max_posts,
        _skip_linkedin_posts,
        _skip_person_profile,
        detect_hiring_from_posts,
    )

    if max_posts is None:
        max_posts = _default_max_posts()
    if _skip_person_profile():
        profile: dict = {}
    else:
        profile = get_linkedin_person_profile(profile_url)
    if _skip_linkedin_posts():
        return {
            **profile,
            "posts": [],
            "interests": [],
            "post_summary": None,
            "is_hiring": None,
        }
    posts_data = get_linkedin_profile_posts(profile_url, max_posts=max_posts)
    posts = posts_data.get("posts") or []
    return {
        **profile,
        "posts": posts,
        "interests": posts_data.get("interests") or [],
        "post_summary": posts_data.get("summary"),
        "is_hiring": posts_data.get("is_hiring")
        if posts_data.get("is_hiring") is not None
        else detect_hiring_from_posts(posts),
    }
