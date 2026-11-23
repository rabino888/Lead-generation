"""
Careers page discovery.

Many companies host open positions on a dedicated subdomain
(https://jobs.concentrix.com) or an external ATS (Workday, Greenhouse,
Ashby…). A same-domain crawl often stops on a marketing /careers page whose
"See open roles" CTA points at the real portal.

Strategy, cheapest first:
  1. Prefer ATS / job-board links already present in crawled markdown.
  2. Read Careers / Jobs anchors in the homepage HTML. Insurers often
     link the real portal only from the footer (careers.aviva.co.uk,
     admiraljobs.co.uk), and a marketing sitemap will not include it.
  3. Rank sitemap / map careers URLs (ATS first, then real /careers paths).
  4. Probe common careers hosts, including jobsportal.{domain}, with plain HTTP.
  5. After scraping a marketing careers index, hop +1 page when a CTA or
     ATS link is present but role listings are not. A server-rendered
     "See all 151 roles" button is one httpx hop; vacancy-card headings and
     talent.community.com/projects/{slug} links are read from the HTML.
"""
from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import httpx

from agent.utils.logger import log

_URL_RE = re.compile(r"https?://[^\s)\]\"'<>]+", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

# Words that mark a URL as careers-related (English + Spanish).
_CAREERS_HINT = re.compile(
    r"(career|job|vacanc|join[-_]?us|work[-_]?(with|for)[-_]?us|talent|hiring"
    r"|empleo|trabaja|vacante|unete|únete|recruit|positions)",
    re.IGNORECASE,
)

# Well-known ATS / external job hosts — prefer these over marketing /careers.
_ATS_HOSTS = (
    "myworkdayjobs.com", "workday.com", "greenhouse.io", "lever.co",
    "smartrecruiters.com", "successfactors.com", "successfactors.eu",
    "teamtailor.com", "recruitee.com", "personio.", "bamboohr.com",
    "icims.com", "taleo.net", "factorialhr.", "jobvite.com", "ashbyhq.com",
    "workable.com", "breezy.hr", "join.com", "avature.net", "eightfold.ai",
    "phenom.com", "oraclecloud.com", "gem.com", "ycombinator.com",
)

_HIRING_TEXT = re.compile(
    r"(open positions|open roles|current openings|job openings|we'?re hiring"
    r"|join (our|the) team|apply now|vacantes|ofertas de empleo|trabaja con nosotros"
    r"|únete al equipo|unete al equipo|posiciones abiertas|see open roles|"
    r"view (all )?(openings|jobs|positions)|explore (open )?roles)",
    re.IGNORECASE,
)

_CTA_ANCHOR = re.compile(
    r"(see\s+(all\s+)?open\s+roles|see\s+all(?:\s+\d+)?\s+roles|see\s+(all\s+)?jobs|"
    r"view\s+open\s+roles|view\s+(all\s+)?(openings|jobs|positions|roles)|"
    r"open\s+roles|browse\s+(open\s+)?(jobs|roles|vacancies)|"
    r"explore\s+(open\s+)?roles|current\s+openings|current\s+opportunities|"
    r"check\s+out\s+open\s+positions|"
    r"job\s+openings|we'?re\s+hiring|apply\s+(now|here)|view\s+openings)",
    re.IGNORECASE,
)

# Listing-page buttons. "See all 151 roles" is a hop, never a job title.
_LISTING_CTA = re.compile(
    r"^(?:"
    r"see\s+all(?:\s+\d+)?\s+(?:roles|jobs|vacancies|positions|openings)"
    r"|see\s+open\s+(?:roles|positions|jobs|vacancies)"
    r"|check\s+out\s+open\s+positions"
    r"|view\s+open\s+roles"
    r"|view\s+all(?:\s+open)?\s+(?:roles|jobs|positions|openings|vacancies)"
    r"|current\s+opportunities"
    r"|current\s+openings"
    r"|browse\s+(?:open\s+)?(?:vacancies|jobs|roles|positions)"
    r"|explore\s+(?:open\s+)?roles"
    r")$",
    re.IGNORECASE,
)

# False positives: API/docs "jobs" endpoints, auth walls, asset paths.
_REJECT_CAREERS = re.compile(
    r"/api/|/sdk-api/|/reference/|/rest-api/|docs\.|/docs/|"
    r"login\.|/login\b|/signin\b|set-defjob|/oauth|"
    r"\.(pdf|docx?|xlsx?|png|jpe?g|gif|svg)(?:\?|$)",
    re.IGNORECASE,
)

_ROLE_SIGNAL = re.compile(
    r"(engineer|scientist|designer|manager|director|founder|"
    r"internship|full[- ]?time|part[- ]?time|remote|apply\s+for|"
    r"job\s+title|open\s+position|positions?\s+open|"
    r"senior\s+\w+|staff\s+\w+|principal\s+\w+)",
    re.IGNORECASE,
)


# Last two labels are not the registrable domain for these suffixes.
# Treating aviva.co.uk as "co.uk" made the probe request https://jobs.co.uk.
_MULTI_PART_PUBLIC_SUFFIXES = (
    "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "net.uk", "me.uk",
    "com.au", "net.au", "org.au", "co.nz", "org.nz", "co.za", "com.br",
    "com.mx", "co.jp", "com.sg", "co.in", "com.tr", "co.kr",
)

# Boards that match a "jobs" hint but are not the company's own portal.
_GENERIC_JOB_BOARDS = frozenset({
    "jobs.co.uk",
    "indeed.com",
    "indeed.co.uk",
    "reed.co.uk",
    "totaljobs.com",
    "cv-library.co.uk",
    "glassdoor.com",
    "glassdoor.co.uk",
    "monster.com",
    "monster.co.uk",
    "simplyhired.com",
    "ziprecruiter.com",
})

_CAREERS_SEGMENTS = frozenset({
    "career", "careers", "job", "jobs", "vacancy", "vacancies",
    "join-us", "joinus", "work-with-us", "empleo",
    "trabaja-con-nosotros", "vacantes",
})

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_ANCHOR_RE = re.compile(
    r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
    re.I | re.S,
)
_FOOTER_LABEL = re.compile(
    r"^(?:careers?|jobs?|vacancies|join us|work with us|we'?re hiring)\b",
    re.I,
)
_TILE_TITLE = re.compile(
    r'class="[^"]*attrax-vacancy-tile__title[^"]*"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_HEADING = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.I | re.S)
_UI_HEADING = re.compile(
    r"^(log in.*|register.*|search jobs|refine.*|cookie.*|\d+\s+vacancies?|"
    r"can'?t find.*|saved jobs|vacancies|our vacancies|"
    r"see all(?:\s+\d+)?\s+(?:roles|jobs|vacancies|positions|openings)|"
    r"current opportunities|check out open positions|view open roles|"
    r"browse vacancies|see open roles)$",
    re.I,
)
# Class / id fragments that wrap a rendered job list or a single title.
_VACANCY_REGION = re.compile(
    r"vacancies|vacancy|open[-_]roles|job-card|job_card|job-title|job_title",
    re.I,
)
_ATTR_CLASS_ID = re.compile(
    r"""(?:\b(?:class|id)\s*=\s*)(?:"([^"]*)"|'([^']*)')""",
    re.I,
)
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][\w:-]*)([^>]*)>", re.S)
_PROJECT_HREF = re.compile(
    r"""href\s*=\s*["']([^"']*/projects/[^"']+)["']""",
    re.I,
)
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})
_GENERIC_PROJECT_SLUGS = frozenset({
    "search", "apply", "login", "register", "careers", "jobs", "vacancies",
    "vacancy", "projects", "all", "open", "roles", "list", "listing",
})
_JOB_SITEMAP_LOC = re.compile(r"<loc>\s*(https?://[^<\s]*?/job/[^<\s]+)\s*</loc>", re.I)
_JOB_SLUG_TITLE = re.compile(r"/job/(.+)-in-[a-z0-9-]+-jid-\d+/?$", re.I)


def _registered_domain(website: str) -> str:
    """aviva.co.uk from https://www.aviva.co.uk/foo, not the public suffix alone."""
    host = urlparse(website if "://" in website else f"https://{website}").netloc.lower()
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    labels = [p for p in host.split(".") if p]
    suffix_len = 2
    joined = ".".join(labels)
    for suffix in _MULTI_PART_PUBLIC_SUFFIXES:
        if joined == suffix or joined.endswith("." + suffix):
            suffix_len = suffix.count(".") + 1
            break
    if len(labels) > suffix_len + 1 and labels[0] in {"es", "en", "web"}:
        labels = labels[1:]
    if len(labels) > suffix_len:
        return ".".join(labels[-(suffix_len + 1):])
    return ".".join(labels)


def _clean_url(url: str) -> str:
    return url.strip().rstrip(".,;:!?)]}\"'" )


def _norm_key(url: str) -> str:
    return _clean_url(url).lower().rstrip("/")


def _is_ats_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    if "ycombinator.com" in host and "/jobs" not in path and "/companies/" not in path:
        return False
    return any(ats in host for ats in _ATS_HOSTS)


def _is_rejected_careers_url(url: str) -> bool:
    return bool(_REJECT_CAREERS.search(url))


def is_generic_job_board(url: str) -> bool:
    """True for jobs.co.uk and other boards that are not the company's own portal."""
    return _is_generic_job_board(url)


def without_generic_job_boards(markdown: Optional[str]) -> str:
    """Drop crawled sections whose page URL is a generic job board.

    A `.co.uk` company must not be researched from `https://jobs.co.uk`.
    """
    if not markdown:
        return ""
    kept: list[str] = []
    for section in re.split(r"\n\n---\n\n", markdown):
        match = re.match(r"### \[(https?://[^\]\s]+)\]", section.strip())
        if match and _is_generic_job_board(match.group(1)):
            continue
        if section.strip():
            kept.append(section.strip())
    return "\n\n---\n\n".join(kept)


def _is_generic_job_board(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host in _GENERIC_JOB_BOARDS or _registered_domain(url) in _GENERIC_JOB_BOARDS


def _careers_host(host: str) -> bool:
    """careers.aviva.co.uk, jobsportal.lv.com, admiraljobs.co.uk."""
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith(("careers.", "jobs.", "jobsportal.", "talent.", "empleo.")):
        return True
    label = host.split(".")[0] if host else ""
    return bool(label) and label not in {"jobs", "careers"} and (
        label.endswith("jobs") or label.endswith("careers")
    )


def _path_segments(url: str) -> list[str]:
    return [p.lower() for p in urlparse(url).path.split("/") if p]


def _is_careers_target(url: str) -> bool:
    """True for a portal or a /careers path, false for article slugs that merely contain 'job'."""
    if _is_rejected_careers_url(url) or _is_generic_job_board(url):
        return False
    if _is_ats_url(url):
        return True
    if _careers_host(urlparse(url).netloc):
        return True
    return any(part in _CAREERS_SEGMENTS for part in _path_segments(url))


def _looks_like_careers_url(url: str, domain: str) -> bool:
    if not _is_careers_target(url):
        return False
    if _is_ats_url(url) or _careers_host(urlparse(url).netloc):
        return True
    host = urlparse(url).netloc.lower()
    return bool(domain) and domain in host


def _rank_careers_url(url: str, domain: str) -> int:
    """Lower score = better. ATS/job boards beat marketing /careers pages."""
    low = url.lower()
    score = len(url)
    if _is_ats_url(url):
        score -= 500
    host = urlparse(url).netloc.lower()
    if _careers_host(host):
        score -= 120
    if low.rstrip("/").endswith(("/careers", "/jobs", "/join-us", "/empleo")):
        score -= 40
    if domain and domain in host and not _is_ats_url(url):
        score += 30  # demote same-domain marketing relative to ATS
    if _is_rejected_careers_url(url):
        score += 10_000
    return score


def extract_careers_links(markdown: Optional[str], website: str) -> list[str]:
    """Careers-looking URLs already present in crawled site content, best first."""
    if not markdown:
        return []
    domain = _registered_domain(website)
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []
    for raw in _URL_RE.findall(markdown):
        url = _clean_url(raw)
        key = _norm_key(url)
        if key in seen:
            continue
        seen.add(key)
        if not _looks_like_careers_url(url, domain):
            continue
        low = url.lower()
        if any(x in low for x in ("wp-content/", "/blog/", "/news/", "/noticias/")):
            continue
        scored.append((_rank_careers_url(url, domain), url))
    scored.sort(key=lambda x: x[0])
    return [u for _, u in scored]


def filter_careers_urls(urls: list[str], website: str) -> list[str]:
    """Rank sitemap / map URLs that look like careers boards."""
    if not urls:
        return []
    domain = _registered_domain(website)
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for raw in urls:
        url = _clean_url(raw)
        if not url.startswith("http"):
            continue
        key = _norm_key(url)
        if key in seen:
            continue
        seen.add(key)
        low = url.lower()
        if any(x in low for x in ("/blog/", "/news/", "/noticias/")):
            continue
        if not (_looks_like_careers_url(url, domain) or _is_ats_url(url)):
            continue
        scored.append((_rank_careers_url(url, domain), url))
    scored.sort(key=lambda x: x[0])
    return [u for _, u in scored]


def _probe_candidates(website: str) -> list[str]:
    domain = _registered_domain(website)
    base = website.rstrip("/")
    if "://" not in base:
        base = f"https://{base}"
    return [
        f"https://careers.{domain}",
        f"https://jobs.{domain}",
        f"https://jobsportal.{domain}/vacancies/vacancy-search-results.aspx",
        f"https://jobsportal.{domain}",
        f"{base}/careers",
        f"{base}/jobs",
        f"{base}/join-us",
        f"{base}/work-with-us",
        f"{base}/vacancies",
        f"{base}/empleo",
        f"{base}/trabaja-con-nosotros",
        f"{base}/es/empleo",
        f"https://talent.{domain}",
        f"https://empleo.{domain}",
    ]


def _probe(url: str) -> Optional[str]:
    """Return the final URL if `url` resolves to a live careers-looking page."""
    if _is_rejected_careers_url(url):
        return None
    try:
        with httpx.Client(follow_redirects=True, timeout=6, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        }) as client:
            resp = client.get(url)
    except Exception:
        return None
    if resp.status_code >= 400:
        return None
    final = str(resp.url)
    if _is_rejected_careers_url(final):
        return None
    if _is_careers_target(final):
        return final
    # Redirected somewhere without a careers hint (often the homepage or a
    # soft-404) — only accept if the page content clearly shows hiring.
    return final if _HIRING_TEXT.search(resp.text[:20000]) else None


def _html_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    text = html.unescape(text)
    text = re.sub(r"\s+learn more$", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_response(url: str, *, timeout: float = 12) -> tuple[int, str]:
    """(status, body). Status 0 on transport error. Body empty when blocked or not HTML."""
    if not url:
        return 0, ""
    if "://" not in url:
        url = "https://" + url
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            },
        ) as client:
            resp = client.get(url)
    except Exception:
        return 0, ""
    status = int(resp.status_code)
    if status >= 400:
        return status, ""
    body = resp.text[:500_000]
    ctype = (resp.headers.get("content-type") or "").lower()
    if not ctype or "html" in ctype or "xml" in ctype or ctype.startswith("text/"):
        return status, body
    head = body[:2000].lower()
    if "<html" in head or "<div" in head or "<loc>" in head:
        return status, body
    return status, ""


def _fetch_html(url: str, *, timeout: float = 12) -> str:
    """Homepage or careers HTML. Empty on block, timeout, or non-HTML."""
    _status, body = _fetch_response(url, timeout=timeout)
    return body


def footer_careers_links(html: Optional[str], website: str) -> list[str]:
    """
    Careers / Jobs anchors from homepage HTML, best first.

    The visible label is enough. Admiral's footer says "Jobs" and points at
    admiraljobs.co.uk, which does not contain admiral.com.
    """
    if not html or not website:
        return []
    base = website if "://" in website else f"https://{website}"
    domain = _registered_domain(website)
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for href, inner in _ANCHOR_RE.findall(html):
        label = _html_text(inner)
        if href.lower().startswith(("javascript:", "mailto:", "#")):
            continue
        url = _clean_url(urljoin(base, href.strip()))
        if not url.startswith("http"):
            continue
        ats_label = _is_ats_url(url) and 0 < len(label.split()) <= 8
        if not (_FOOTER_LABEL.match(label) or ats_label):
            continue
        if _is_generic_job_board(url) or _is_rejected_careers_url(url):
            continue
        if not _is_careers_target(url):
            continue
        key = _norm_key(url)
        if key in seen:
            continue
        seen.add(key)
        scored.append((_rank_careers_url(url, domain), url))
    scored.sort(key=lambda x: x[0])
    return [u for _, u in scored]


_ROLE_WORD = re.compile(
    r"\b(manager|lead|director|head|engineer|analyst|underwriter|handler|"
    r"officer|advisor|adviser|partner|coordinator|executive|specialist|"
    r"assistant|consultant|developer|architect|actuary|lawyer|accountant|"
    r"associate|representative|assessor|broker|technician|designer|"
    r"recruiter|counsel)\b",
    re.I,
)
_JOB_LINK = re.compile(
    r"<a\b[^>]*href=[\"']([^\"']*(?:/job/|/jobs/|/vacanc)[^\"']*)[\"'][^>]*>(.*?)</a>",
    re.I | re.S,
)


def _looks_like_role_title(label: str) -> bool:
    """A job title, not a nav item or a marketing heading."""
    label = _html_text(label)
    if not label or len(label) > 80 or len(label.split()) > 14:
        return False
    if re.search(r"[.!?]|cookie|customers|employees|website uses|&amp;", label, re.I):
        return False
    if _is_rejected_title(label):
        return False
    return _ROLE_WORD.search(label) is not None


def _prepare_html(raw: str) -> str:
    """Drop comments, scripts, styles, and templates so class names in JS are not titles."""
    text = raw or ""
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<template\b[^>]*>.*?</template>", " ", text)
    return text


def _normalize_label(label: str) -> str:
    text = _html_text(label)
    text = re.sub(r"[\s›»→>]+$", "", text).strip()
    return re.sub(r"\s+", " ", text).strip()


_UI_LINK_LABELS = frozenset({
    "learn more", "read more", "find out more", "see more", "view details",
    "more info", "more information", "apply", "apply now", "details",
    "view job", "see details", "find out", "click here", "here",
})


def _is_rejected_title(label: str) -> bool:
    """Nav, listing buttons, and section headings that are not a job."""
    text = _normalize_label(label)
    if not text:
        return True
    if _UI_HEADING.match(text) or _LISTING_CTA.match(text):
        return True
    folded = text.casefold()
    if folded in _GENERIC_ROLE_TITLES or folded in _UI_LINK_LABELS:
        return True
    return False


def _accept_listed_title(label: str) -> bool:
    """A title taken from a vacancy card or job slug, without the role-word dictionary."""
    text = _normalize_label(label)
    if not text or len(text) > 80 or len(text.split()) > 14 or len(text) < 3:
        return False
    if _is_rejected_title(text):
        return False
    if re.fullmatch(r"[A-Z]{2,4}", text):
        return False
    if re.search(r"[.!?]|cookie|customers|employees|website uses", text, re.I):
        return False
    return True


def _class_tokens(marker: str) -> set[str]:
    return {tok for tok in re.split(r"[\s_]+", (marker or "").lower()) if tok}


def _marker_text(attrs: str) -> str:
    parts = [a or b for a, b in _ATTR_CLASS_ID.findall(attrs or "")]
    return " ".join(parts)


def _is_vacancy_card(marker: str) -> bool:
    tokens = _class_tokens(marker)
    if "vacancy-card" in tokens or "job-card" in tokens or "job_card" in tokens:
        return True
    # class="vacancy card" — not vacancy-card-container, which only wraps the list.
    return "vacancy" in tokens and "card" in tokens


def _is_job_title_marker(marker: str) -> bool:
    raw = (marker or "").lower().replace("_", "-")
    return "job-title" in raw


def _iter_marked_regions(page_html: str) -> list[tuple[str, str, str]]:
    """(class/id text, inner html, tag) for vacancy / job-card / job-title elements."""
    regions: list[tuple[str, str, str]] = []
    stack: list[tuple[str, int, str]] = []
    for match in _TAG_RE.finditer(page_html or ""):
        closing = match.group(1) == "/"
        tag = match.group(2).lower()
        attrs = match.group(3) or ""
        if tag in _VOID_TAGS or attrs.rstrip().endswith("/"):
            continue
        if not closing:
            marker = _marker_text(attrs)
            interesting = marker if marker and _VACANCY_REGION.search(marker) else ""
            stack.append((tag, match.end(), interesting))
            continue
        for index in range(len(stack) - 1, -1, -1):
            if stack[index][0] != tag:
                continue
            _open_tag, inner_at, interesting = stack[index]
            inner = page_html[inner_at:match.start()]
            del stack[index:]
            if interesting:
                regions.append((interesting, inner, tag))
            break
    return regions


def _first_card_heading(inner: str) -> str:
    for raw in _HEADING.findall(inner or ""):
        if _accept_listed_title(raw):
            return _normalize_label(raw)
    return ""


def _title_from_project_path(path: str) -> str:
    """Title-case /projects/{slug}, ignoring the query string and a trailing numeric id."""
    parts = [unquote(part) for part in (path or "").split("/") if part]
    lowered = [part.casefold() for part in parts]
    if "projects" not in lowered:
        return ""
    index = lowered.index("projects")
    if index + 1 >= len(parts):
        return ""
    slug = re.sub(r"-\d{4,}$", "", parts[index + 1]).strip("-_")
    if not slug or slug.isdigit() or not re.search(r"[A-Za-z]", slug):
        return ""
    if slug.casefold() in _GENERIC_PROJECT_SLUGS:
        return ""
    title = _title_from_slug(slug)
    return title if _accept_listed_title(title) else ""


def _project_titles(fragment: str, *, talent_only: bool = False) -> list[str]:
    found: list[str] = []
    for href in _PROJECT_HREF.findall(fragment or ""):
        href = html.unescape(href.strip())
        parsed = urlparse(href)
        if talent_only and "talent.community" not in parsed.netloc.lower():
            continue
        title = _title_from_project_path(parsed.path)
        if title:
            found.append(title)
    return found


def _titles_from_marked_regions(page_html: str) -> list[str]:
    """
    Titles from vacancy cards, job-title elements, and /projects/{slug} links.

    Card headings skip the role-word dictionary. Page headings such as
    "About AXA" or "Our vacancies" are not cards, so they stay out.
    """
    titles: list[str] = []
    titles.extend(_project_titles(page_html, talent_only=True))
    for marker, inner, _tag in _iter_marked_regions(page_html):
        if _is_job_title_marker(marker) or _is_vacancy_card(marker):
            heading = _first_card_heading(inner)
            if heading:
                titles.append(heading)
            else:
                label = _normalize_label(inner)
                if _accept_listed_title(label):
                    titles.append(label)
        titles.extend(_project_titles(inner))
    return titles


def titles_from_vacancy_html(page_html: Optional[str]) -> list[str]:
    """Role titles from a server-rendered vacancy list.

    Attrax tiles, /job/ links, and Eploy "N Vacancies" headings still require a
    role word. Vacancy-card headings, job-title elements, and
    talent.community.com/projects/{slug} links do not.
    """
    if not page_html:
        return []
    page = _prepare_html(page_html)
    strict: list[str] = []
    strict.extend(_html_text(raw) for raw in _TILE_TITLE.findall(page))
    for _href, inner in _JOB_LINK.findall(page):
        strict.append(_html_text(inner))
    # Eploy prints a "11 Vacancies" heading and then one heading per role.
    if re.search(r"\b\d+\s+vacancies\b", page, re.I):
        for raw in _HEADING.findall(page):
            label = _html_text(raw)
            if label and not _is_rejected_title(label):
                strict.append(label)
    trusted = _titles_from_marked_regions(page)
    kept = [title for title in strict if _looks_like_role_title(title)]
    kept.extend(title for title in trusted if _accept_listed_title(title))
    return clean_role_titles(kept)


def titles_from_job_sitemap(xml: Optional[str]) -> list[str]:
    """Role titles from a vacancies sitemap of /job/{title}-in-{place}-jid-{id} URLs."""
    titles: list[str] = []
    for loc in _JOB_SITEMAP_LOC.findall(xml or ""):
        path = urlparse(loc).path
        match = _JOB_SLUG_TITLE.search(path)
        slug = match.group(1) if match else path.rstrip("/").split("/")[-1]
        slug = re.sub(r"-jid-\d+$", "", slug, flags=re.I)
        titles.append(_title_from_slug(slug))
    return clean_role_titles([t for t in titles if _looks_like_role_title(t)])


def find_careers_page(
    website: Optional[str],
    crawled_markdown: Optional[str] = None,
    sitemap_urls: Optional[list[str]] = None,
) -> Optional[str]:
    """
    Best careers/jobs URL for a company, or None.
    Order: markdown ATS links → homepage footer anchors → sitemap → other
    site links → HTTP probe (including jobsportal.{domain}).
    """
    if not website:
        return None

    # Prefer real portals already linked from crawled content (no HTTP needed when ATS).
    md_links = extract_careers_links(crawled_markdown, website)
    for candidate in md_links[:5]:
        if _is_ats_url(candidate):
            log.info("Careers portal from site links (ATS): %s", candidate)
            return candidate.split("?")[0].rstrip("/") or candidate

    footer = footer_careers_links(_fetch_html(website), website)
    if footer:
        chosen = footer[0].rstrip("/") or footer[0]
        log.info("Careers page from homepage footer: %s", chosen)
        return chosen

    if sitemap_urls:
        for candidate in filter_careers_urls(sitemap_urls, website):
            resolved = _probe(candidate)
            if resolved:
                log.info("Careers page from sitemap: %s", resolved)
                return resolved

    if md_links:
        for candidate in md_links[:5]:
            if _is_ats_url(candidate):
                continue  # already tried above
            resolved = _probe(candidate)
            if resolved:
                log.info("Careers page from site links: %s", resolved)
                return resolved

    candidates = _probe_candidates(website)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_probe, candidates))
    for resolved in results:  # keep candidate priority order
        if resolved:
            log.info("Careers page from probe: %s", resolved)
            return resolved
    return None


def careers_index_needs_portal_hop(
    markdown: Optional[str],
    *,
    careers_url: Optional[str] = None,
) -> bool:
    """
    True when the scraped careers page looks like a marketing landing page
    (CTA / hiring copy) rather than an actual job board with listings.
    """
    if careers_url and _is_ats_url(careers_url):
        return False
    text = (markdown or "").strip()
    if not text:
        return True
    if careers_url and _is_rejected_careers_url(careers_url):
        return True
    if _is_ats_url(text):  # ATS URL already present in body
        # Still hop if current page itself is not the ATS — caller decides target.
        pass
    role_hits = len(_ROLE_SIGNAL.findall(text))
    hiring_hits = len(_HIRING_TEXT.findall(text))
    # Short marketing page with hiring CTA, or hiring language without role signals.
    if len(text) < 2500 and hiring_hits >= 1 and role_hits < 3:
        return True
    if hiring_hits >= 1 and role_hits < 2:
        return True
    if _CTA_ANCHOR.search(text) and role_hits < 3:
        return True
    return False


def pick_careers_portal_hop(
    markdown: Optional[str],
    website: str,
    *,
    current_url: Optional[str] = None,
) -> Optional[str]:
    """
    Pick one deeper careers/portal URL from a marketing careers page.
    Prefer ATS hosts, then CTA-anchored markdown links, then other careers links.
    """
    if not markdown or not website:
        return None
    domain = _registered_domain(website)
    current_key = _norm_key(current_url) if current_url else ""

    def _usable(url: str) -> Optional[str]:
        url = _clean_url(url)
        if not url.startswith("http"):
            return None
        if _norm_key(url) == current_key:
            return None
        if _is_rejected_careers_url(url):
            return None
        if not (_looks_like_careers_url(url, domain) or _is_ats_url(url)):
            return None
        # Drop same-path hash-only "links" like /company#careers when we already
        # scraped a better index — still allow if clearly ATS.
        if current_url and not _is_ats_url(url):
            cur = urlparse(current_url)
            nxt = urlparse(url)
            if (
                cur.netloc.lower() == nxt.netloc.lower()
                and cur.path.rstrip("/") == nxt.path.rstrip("/")
            ):
                return None
        return url

    # 1) Markdown links whose anchor is a "see open roles" CTA.
    cta_hits: list[tuple[int, str]] = []
    for anchor, href in _MD_LINK_RE.findall(markdown):
        if not _CTA_ANCHOR.search(anchor):
            continue
        url = _usable(href)
        if url:
            cta_hits.append((_rank_careers_url(url, domain), url))
    if cta_hits:
        cta_hits.sort(key=lambda x: x[0])
        return cta_hits[0][1]

    # 2) Any ATS / ranked careers URL in the page body.
    for url in extract_careers_links(markdown, website):
        picked = _usable(url)
        if picked and _is_ats_url(picked):
            return picked

    # 3) Fallback: best non-current careers link that is a jobs subdomain.
    for url in extract_careers_links(markdown, website):
        picked = _usable(url)
        if not picked:
            continue
        host = urlparse(picked).netloc.lower()
        if _careers_host(host):
            return picked
    return None


# Public job-board JSON. Used when a careers page only links out to Ashby,
# Greenhouse, or Lever — scraping those apps often misses the listings.
_ATS_SKIP_SLUGS = frozenset(
    {"jobs", "job", "embed", "job_board", "job_app", "v1", "postings"}
)
_GENERIC_ROLE_TITLES = frozenset(
    {
        "careers",
        "jobs",
        "open roles",
        "open positions",
        "join us",
        "we're hiring",
        "we are hiring",
        "our vacancies",
        "see all roles",
        "see open roles",
        "view open roles",
        "current opportunities",
        "browse vacancies",
        "check out open positions",
        "vacancies",
    }
)
_MAX_ATS_ROLES = 40


def ats_board_identity(url: str) -> Optional[tuple[str, str]]:
    """
    Return ('ashby'|'greenhouse'|'lever', board slug) for a job-board URL.

    Single-posting URLs collapse to the board so the listing API returns
    every open role, not one job.
    """
    raw = _clean_url(url or "")
    if not raw or _is_rejected_careers_url(raw):
        return None
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]
    if "ashbyhq.com" in host:
        if not parts or parts[0].lower() in _ATS_SKIP_SLUGS:
            return None
        return ("ashby", parts[0])
    if "greenhouse.io" in host:
        token = (parse_qs(parsed.query).get("for") or [""])[0].strip()
        if token:
            return ("greenhouse", token)
        for part in parts:
            if part.lower() in _ATS_SKIP_SLUGS or part.isdigit():
                continue
            return ("greenhouse", part)
        return None
    if "lever.co" in host:
        for part in parts:
            if part.lower() in _ATS_SKIP_SLUGS:
                continue
            return ("lever", part)
        return None
    return None


def canonical_ats_board_url(url: str) -> Optional[str]:
    ident = ats_board_identity(url)
    if not ident:
        return None
    provider, slug = ident
    safe = quote(slug, safe="-_.~")
    if provider == "ashby":
        return f"https://jobs.ashbyhq.com/{safe}"
    if provider == "greenhouse":
        return f"https://job-boards.greenhouse.io/{safe}"
    if provider == "lever":
        return f"https://jobs.lever.co/{safe}"
    return None


def clean_role_titles(titles: list[Any], *, limit: int = _MAX_ATS_ROLES) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in titles:
        title = " ".join(str(raw or "").split())
        if not title or len(title) > 140:
            continue
        if title.casefold() in _GENERIC_ROLE_TITLES:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(title)
        if len(out) >= limit:
            break
    return out


def titles_from_ats_payload(provider: str, payload: Any) -> list[str]:
    titles: list[str] = []
    if provider == "ashby" and isinstance(payload, dict):
        for job in payload.get("jobs") or []:
            if not isinstance(job, dict) or job.get("isListed") is False:
                continue
            titles.append(job.get("title") or "")
    elif provider == "greenhouse" and isinstance(payload, dict):
        for job in payload.get("jobs") or []:
            if isinstance(job, dict):
                titles.append(job.get("title") or "")
    elif provider == "lever" and isinstance(payload, list):
        for job in payload:
            if isinstance(job, dict):
                titles.append(job.get("text") or job.get("title") or "")
    return clean_role_titles(titles)


def fetch_ats_open_roles(url: str) -> list[str]:
    """Open-role titles from a public Ashby, Greenhouse, or Lever board. Empty on failure."""
    ident = ats_board_identity(url)
    if not ident:
        return []
    provider, slug = ident
    safe = quote(slug, safe="-_.~")
    try:
        if provider == "ashby":
            resp = httpx.get(
                f"https://api.ashbyhq.com/posting-api/job-board/{safe}",
                timeout=25,
                follow_redirects=True,
            )
        elif provider == "greenhouse":
            resp = httpx.get(
                f"https://boards-api.greenhouse.io/v1/boards/{safe}/jobs",
                timeout=25,
                follow_redirects=True,
            )
        else:
            resp = httpx.get(
                f"https://api.lever.co/v0/postings/{safe}",
                params={"mode": "json"},
                timeout=25,
                follow_redirects=True,
            )
    except Exception as exc:
        log.warning("ATS job board fetch failed for %s: %s", url, exc)
        return []
    if resp.status_code != 200:
        log.info("ATS job board %s returned HTTP %s for %s", provider, resp.status_code, slug)
        return []
    try:
        payload = resp.json()
    except Exception:
        return []
    titles = titles_from_ats_payload(provider, payload)
    if titles:
        log.info("ATS %s/%s: %d open roles", provider, slug, len(titles))
    return titles


def open_roles_from_ats(url: Optional[str]) -> Optional[dict[str, Any]]:
    """
    Structured listings for an ATS URL.

    Returns careers_url, open_roles, and a short markdown list — or None.
    """
    if not url:
        return None
    canonical = canonical_ats_board_url(url)
    if not canonical:
        return None
    titles = fetch_ats_open_roles(canonical)
    if not titles:
        return None
    return {
        "careers_url": canonical,
        "open_roles": titles,
        "markdown": "\n".join(f"- {title}" for title in titles),
    }


_ICIMS_JOB = re.compile(r"/jobs/\d+/([^/?#\"']+)/job", re.I)
_SMALL_WORDS = frozenset({"i", "ii", "iii", "iv", "qc", "qa"})


def _title_from_slug(slug: str) -> str:
    text = unquote(slug).replace("&amp;", " and ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    words: list[str] = []
    for word in text.split():
        if word.casefold() in _SMALL_WORDS:
            words.append(word.upper())
        else:
            words.append(word[:1].upper() + word[1:].lower())
    return " ".join(words)


def titles_from_icims_html(html: str) -> list[str]:
    return clean_role_titles([_title_from_slug(slug) for slug in _ICIMS_JOB.findall(html or "")])


def icims_board_url(url: str) -> Optional[str]:
    raw = _clean_url(url or "")
    if "://" not in raw:
        raw = "https://" + raw
    host = urlparse(raw).netloc.lower()
    if "icims.com" not in host:
        return None
    return f"https://{host}/jobs/search?ss=1&in_iframe=1"


def fetch_icims_open_roles(url: str) -> list[str]:
    """Open-role titles from an iCIMS board search page (titles are in the job URLs)."""
    board = icims_board_url(url)
    if not board:
        return []
    titles: list[str] = []
    for page in range(4):
        page_url = board if page == 0 else f"{board}&pr={page}"
        try:
            resp = httpx.get(
                page_url,
                timeout=25,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            )
        except Exception as exc:
            log.warning("iCIMS fetch failed for %s: %s", page_url, exc)
            break
        if resp.status_code != 200:
            break
        found = titles_from_icims_html(resp.text)
        if not found:
            break
        before = len(titles)
        titles = clean_role_titles(titles + found)
        if len(titles) == before:
            break
    if titles:
        log.info("iCIMS %s: %d open roles", urlparse(board).netloc, len(titles))
    return titles


def _titles_from_vacancy_sitemap(careers_url: str) -> list[str]:
    origin = urlparse(careers_url)
    if not origin.netloc:
        return []
    sitemap = f"{origin.scheme or 'https'}://{origin.netloc}/vacanciessitemap.xml"
    return titles_from_job_sitemap(_fetch_html(sitemap, timeout=12))


def _same_document(url: str, page_url: str) -> bool:
    left, right = urlparse(url), urlparse(page_url)
    return (
        left.netloc.lower() == right.netloc.lower()
        and left.path.rstrip("/").lower() == right.path.rstrip("/").lower()
    )


def _is_listing_destination(url: str, page_url: str) -> bool:
    """A careers, ATS, or same-company apply/vacancies URL. Not a generic board."""
    if _is_ats_url(url) or _is_careers_target(url):
        return True
    host = urlparse(url).netloc.lower()
    if "talent.community" in host or host.startswith("talent."):
        return True
    domain = _registered_domain(page_url)
    if domain and domain in host:
        path = urlparse(url).path.lower()
        if re.search(r"apply|vacanc|job|career|role|opportunit|position", path):
            return True
    return False


def listing_cta_url(page_html: Optional[str], page_url: str) -> Optional[str]:
    """
    First "See all 151 roles" / "Browse vacancies" anchor that leaves this page.

    jobs.co.uk and other generic boards are not followed. One caller hop only.
    """
    if not page_html or not page_url:
        return None
    base = page_url if "://" in page_url else f"https://{page_url}"
    prepared = _prepare_html(page_html)
    for href, inner in _ANCHOR_RE.findall(prepared):
        label = _normalize_label(inner)
        if not label or not _LISTING_CTA.match(label):
            continue
        if href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        url = _clean_url(urljoin(base, html.unescape(href.strip())))
        if not url.startswith("http"):
            continue
        if _same_document(url, page_url):
            continue
        if is_generic_job_board(url) or _is_rejected_careers_url(url):
            continue
        if not _is_listing_destination(url, page_url):
            continue
        return url
    return None


def _generic_board_cta(page_html: str, page_url: str) -> bool:
    """True when a listing button exists and every target is a generic job board."""
    if not page_html or not page_url:
        return False
    base = page_url if "://" in page_url else f"https://{page_url}"
    saw_cta = False
    for href, inner in _ANCHOR_RE.findall(_prepare_html(page_html)):
        label = _normalize_label(inner)
        if not label or not _LISTING_CTA.match(label):
            continue
        saw_cta = True
        url = _clean_url(urljoin(base, html.unescape(href.strip())))
        if url.startswith("http") and not is_generic_job_board(url):
            return False
    return saw_cta


def _looks_js_only(page_html: str) -> bool:
    if not page_html:
        return False
    visible = _html_text(_prepare_html(page_html))
    if re.search(
        r"enable javascript|requires javascript|just a moment|cf-browser-verification|"
        r"challenge-platform",
        page_html,
        re.I,
    ):
        return len(visible) < 800
    scripts = len(re.findall(r"<script\b", page_html, re.I))
    if len(visible) < 280 and scripts >= 2:
        return True
    prepared = _prepare_html(page_html)
    marker = r"vacanc|job-card|latestVacancies|open[-_]roles"
    if re.search(marker, page_html, re.I) and not re.search(marker, prepared, re.I):
        return True
    return False


def _icims_listing_score(url: str) -> int:
    """1 = a real iCIMS listings URL, 2 = a careers-*.icims.com jobs link. 0 = ignore."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host.endswith(".icims.com") or host == "icims.com":
        return 0
    if host.startswith(("cookie", "cdn", "login")):
        return 0
    if not icims_board_url(url):
        return 0
    path = parsed.path.lower()
    if "/jobs/search" in path or re.search(r"/jobs/\d+/", path):
        return 1
    if host.startswith("careers-") and "/jobs" in path:
        return 2
    return 0


def _embedded_board_url(page_html: str) -> Optional[str]:
    """Ashby, Greenhouse, Lever, or a company iCIMS board linked from the careers HTML.

    www.icims.com privacy pages and cookie-policy script hosts are not boards.
    """
    if not page_html:
        return None
    best: Optional[tuple[int, str]] = None
    for raw in _URL_RE.findall(page_html):
        url = _clean_url(html.unescape(raw)).strip("\\")
        if not url.startswith("http") or is_generic_job_board(url):
            continue
        if canonical_ats_board_url(url):
            return url
        score = _icims_listing_score(url)
        if score and (best is None or score < best[0]):
            best = (score, url)
    return best[1] if best else None


def _empty_careers_reason(status: int, page_html: str, page_url: str) -> str:
    if status == 0:
        return "network"
    if status >= 400:
        return f"http_{status}"
    host = urlparse(page_url or "").netloc.lower()
    if "myworkdayjobs.com" in host:
        return "js_only"
    if page_html and re.search(
        r"text/x-handlebars-template|\{\{this\.title\}\}|RetrieveVacancies\s*\(",
        page_html,
        re.I,
    ):
        return "js_only"
    if page_html and _generic_board_cta(page_html, page_url):
        return "generic_board"
    if _looks_js_only(page_html):
        return "js_only"
    return "no_cta"


def open_roles_report(
    url: Optional[str],
    *,
    allow_cta_hop: bool = True,
) -> dict[str, Any]:
    """
    Open roles for a careers URL, plus why the list is empty.

    reason is "ok", "http_403", "js_only", "no_cta", "generic_board", "network",
    or "cta_" plus the destination's reason when the one allowed hop failed.
    """
    empty: dict[str, Any] = {
        "open_roles": [],
        "careers_url": url or "",
        "reason": "missing_url",
        "status": 0,
        "hop_url": "",
        "hop_status": 0,
    }
    if not url:
        return empty
    if is_generic_job_board(url):
        return {**empty, "reason": "generic_board"}

    ats = open_roles_from_ats(url)
    if ats:
        return {
            "open_roles": list(ats["open_roles"]),
            "careers_url": ats["careers_url"],
            "reason": "ok",
            "status": 200,
            "hop_url": "",
            "hop_status": 0,
        }
    icims_titles = fetch_icims_open_roles(url)
    if icims_titles:
        board = icims_board_url(url) or url
        return {
            "open_roles": icims_titles,
            "careers_url": board,
            "reason": "ok",
            "status": 200,
            "hop_url": "",
            "hop_status": 0,
        }

    status, page_html = _fetch_response(url, timeout=20)
    titles = titles_from_vacancy_html(page_html) if page_html else []
    if not titles:
        titles = _titles_from_vacancy_sitemap(url)
    canonical = url.split("?")[0].rstrip("/") or url
    if titles:
        if page_html:
            log.info("Careers HTML open roles for %s: %d", canonical, len(titles))
        return {
            "open_roles": titles,
            "careers_url": canonical,
            "reason": "ok",
            "status": status or 200,
            "hop_url": "",
            "hop_status": 0,
        }

    if allow_cta_hop and page_html:
        hop = _embedded_board_url(page_html)
        if hop and _same_document(hop, url):
            hop = None
        if not hop:
            hop = listing_cta_url(page_html, url)
        if hop:
            log.info("Careers listing CTA hop: %s → %s", canonical, hop)
            hopped = open_roles_report(hop, allow_cta_hop=False)
            if hopped.get("open_roles"):
                hopped["hop_url"] = hop
                return hopped
            hop_reason = str(hopped.get("reason") or "no_cta")
            return {
                "open_roles": [],
                "careers_url": canonical,
                "reason": f"cta_{hop_reason}",
                "status": status,
                "hop_url": hop,
                "hop_status": int(hopped.get("status") or 0),
            }

    return {
        "open_roles": [],
        "careers_url": canonical,
        "reason": _empty_careers_reason(status, page_html, url),
        "status": status,
        "hop_url": "",
        "hop_status": 0,
    }


def open_roles_from_careers_page(
    url: Optional[str],
    *,
    allow_cta_hop: bool = True,
) -> Optional[dict[str, Any]]:
    """
    Structured open roles for a careers URL.

    Ashby, Greenhouse, Lever, and iCIMS use their listing APIs. Other portals
    expose titles in the HTML (Attrax, Eploy, vacancy cards, /projects/{slug}
    links) or a vacancies sitemap. When that HTML has no titles, one listing
    CTA ("See all 151 roles", "Browse vacancies", …) is followed.
    """
    found = open_roles_report(url, allow_cta_hop=allow_cta_hop)
    titles = list(found.get("open_roles") or [])
    if not titles:
        return None
    careers_url = str(found.get("careers_url") or url or "")
    if is_generic_job_board(careers_url):
        return None
    return {
        "careers_url": careers_url,
        "open_roles": titles,
        "markdown": "\n".join(f"- {title}" for title in titles),
    }


def preferred_ats_board_url(markdown: Optional[str], website: str) -> Optional[str]:
    """Highest-ranked Ashby/Greenhouse/Lever board linked from crawled markdown."""
    if not markdown or not website:
        return None
    for url in extract_careers_links(markdown, website):
        canonical = canonical_ats_board_url(url)
        if canonical:
            return canonical
    return None
