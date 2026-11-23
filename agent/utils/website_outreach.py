"""
Outreach-oriented website page selection and deterministic extractors.

Crawl priority (most → least):
  1. Careers / jobs index
  2. Blog / news index + latest articles, and services
  3. About / meet the team

Social profile URLs (Facebook, Instagram, TikTok, X) are parsed from crawled
text and from homepage HTML footers, rather than inferred by the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, urlunparse

from agent.models import BlogPost, WebsiteAnalysis

DEFAULT_OUTREACH_MAX_PAGES = 8

_CAREERS_INDEX = re.compile(
    r"(career|job|vacanc|join[-_]?us|work[-_]?(with|for)[-_]?us|empleo|"
    r"trabaja|vacante|unete|únete|opportunit)",
    re.I,
)
_BLOG_SEGMENT = (
    r"blog|news|insights|noticias|actualidad|press|prensa|presse|"
    r"newsroom|resources|articles|journal|media|updates"
)
_BLOG_INDEX_ONLY = re.compile(
    r"/(blog|insights|articles|resources|journal|media|updates)/?$", re.I
)
# News/press hubs — exact index paths plus common compounds (newsroom, press-releases).
_NEWS_INDEX_ONLY = re.compile(
    r"/(?:news|noticias|actualidad|press|prensa|presse|newsroom)"
    r"(?:[-_]?(?:releases?|room|centre|center|and[-_]?media))?/?$",
    re.I,
)
_BLOG_ARTICLE = re.compile(rf"/({_BLOG_SEGMENT})/.+", re.I)
# When sitemap/map misses hubs, probe these paths (cheap HTTP) before selection.
_CONTENT_HUB_PATHS = (
    "/blog",
    "/news",
    "/press",
    "/newsroom",
    "/press-releases",
    "/press-release",
    "/insights",
    "/noticias",
    "/actualidad",
    "/prensa",
    "/media",
    "/articles",
    "/resources",
    "/en/news",
    "/en/press",
    "/en/blog",
    "/es/noticias",
    "/es/prensa",
    "/es/blog",
)
_SERVICES = re.compile(
    r"/(services|what-we-do|solutions|capabilities|expertise|servicios|"
    r"soluciones|que-hacemos|offerings)(/|$)",
    re.I,
)
_ABOUT = re.compile(
    r"/(about|about-us|who-we-are|our-story|company|nosotros|"
    r"quienes-somos|sobre-nosotros|acerca)(/|$)",
    re.I,
)
_TEAM = re.compile(
    r"/(team|our-team|people|meet-the-team|equipo|nuestro-equipo|leadership|"
    r"founders)(/|$)",
    re.I,
)
_SKIP_ASSET = re.compile(
    r"\.(pdf|docx?|xlsx?|png|jpe?g|gif|svg|webp|zip)(?:\?|$)", re.I
)
_DATE_IN_PATH = re.compile(r"(20\d{2})(?:[-/](\d{1,2}))?")

_SHARE_SKIP = re.compile(
    r"sharer|share\.php|/intent/|/dialog/|facebook\.com/share|"
    r"twitter\.com/share|/i/web|/hashtag/|/search\?",
    re.I,
)
_SOCIAL_RE = {
    "facebook": re.compile(
        r"https?://(?:www\.)?(?:facebook\.com|fb\.com)/"
        r"(?!sharer|share|dialog|tr\b|plugins)[A-Za-z0-9.\-]+/?",
        re.I,
    ),
    "instagram": re.compile(
        r"https?://(?:www\.)?instagram\.com/"
        r"(?!p/|reel/|stories/|explore)[A-Za-z0-9._]+/?",
        re.I,
    ),
    "tiktok": re.compile(
        r"https?://(?:www\.)?tiktok\.com/@[A-Za-z0-9._]+/?",
        re.I,
    ),
    "x": re.compile(
        r"https?://(?:www\.)?(?:twitter\.com|x\.com)/"
        r"(?!intent|share|i/|search|hashtag|home)[A-Za-z0-9_]+/?",
        re.I,
    ),
    "youtube": re.compile(
        r"https?://(?:www\.)?youtube\.com/(?:c/|channel/|user/|@)?[A-Za-z0-9._\-]+/?",
        re.I,
    ),
    "pinterest": re.compile(
        r"https?://(?:www\.)?pinterest\.com/[A-Za-z0-9._\-]+/?",
        re.I,
    ),
    "reddit": re.compile(
        r"https?://(?:www\.)?reddit\.com/(?:user|u|r)/[A-Za-z0-9._\-]+/?",
        re.I,
    ),
    "threads": re.compile(
        r"https?://(?:www\.)?threads\.net/@?[A-Za-z0-9._]+/?",
        re.I,
    ),
    "snapchat": re.compile(
        r"https?://(?:www\.)?snapchat\.com/add/[A-Za-z0-9._\-]+/?",
        re.I,
    ),
    "linkedin": re.compile(
        r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9._\-]+/?",
        re.I,
    ),
}

WEBSITE_CSV_FIELDS = [
    "website_is_hiring",
    "website_open_roles",
    "website_careers_url",
    "services_offered",
    "about_summary",
    "has_blog",
    "blog_url",
    "news_url",
    "blog_posts",
    "social_facebook",
    "social_instagram",
    "social_tiktok",
    "social_x",
    "social_youtube",
    "social_pinterest",
    "social_reddit",
    "social_threads",
    "social_snapchat",
    "social_linkedin",
    "other_social_profiles",
    "facebook_post_summary",
    "instagram_post_summary",
    "meta_ads_active",
    "meta_ads_count",
    "meta_ads_summary",
    "decision_maker_mentions",
    "website_summary",
]


@dataclass
class OutreachPagePlan:
    urls: list[str] = field(default_factory=list)
    blog_url: Optional[str] = None
    news_url: Optional[str] = None
    careers_url: Optional[str] = None


def _norm_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), path, "", "", ""))


def _path(url: str) -> str:
    return urlparse(url if "://" in url else f"https://{url}").path.lower()


def _is_asset(url: str) -> bool:
    return bool(_SKIP_ASSET.search(url))


def _is_careers_index(url: str) -> bool:
    path = _path(url).rstrip("/")
    if not _CAREERS_INDEX.search(path):
        return False
    # Skip individual job postings — the careers index scrape covers listings.
    tail = path.split("/")[-1]
    return tail in {
        "careers", "career", "jobs", "job", "join-us", "joinus",
        "work-with-us", "vacancies", "vacantes", "empleo",
        "trabaja-con-nosotros", "opportunities", "hiring",
    } or path.count("/") <= 2


def _is_blog_index(url: str) -> bool:
    return bool(_BLOG_INDEX_ONLY.search(_path(url).rstrip("/")))


def _is_news_index(url: str) -> bool:
    return bool(_NEWS_INDEX_ONLY.search(_path(url).rstrip("/")))


def _is_blog_article(url: str) -> bool:
    path = _path(url)
    if re.search(r"/page/\d+|/tag/|/category/|/author/", path):
        return False
    return bool(_BLOG_ARTICLE.search(path))


def _is_services(url: str) -> bool:
    return bool(_SERVICES.search(_path(url)))


def _is_about(url: str) -> bool:
    return bool(_ABOUT.search(_path(url)))


def _is_team(url: str) -> bool:
    return bool(_TEAM.search(_path(url)))


def _article_rank(url: str, index: int) -> tuple:
    """Newer dated paths first; undated keep sitemap order."""
    path = _path(url)
    match = _DATE_IN_PATH.search(path)
    if match:
        year = int(match.group(1))
        month = int(match.group(2) or 12)
        return (-year, -month, index)
    return (0, 0, index)


def _pick_unique(candidates: list[str], seen: set[str], limit: int) -> list[str]:
    if limit <= 0:
        return []
    picked: list[str] = []
    for url in candidates:
        key = _norm_url(url)
        if not key or key in seen or _is_asset(url):
            continue
        seen.add(key)
        picked.append(url)
        if len(picked) >= limit:
            break
    return picked


def _site_base(website: str) -> str:
    home = (website or "").strip()
    if not home:
        return ""
    if "://" not in home:
        home = f"https://{home}"
    parsed = urlparse(home)
    return urlunparse((parsed.scheme or "https", parsed.netloc, "", "", "", "")).rstrip("/")


def _probe_live_url(url: str) -> Optional[str]:
    """Return final URL if the path resolves to a live HTML-ish page."""
    try:
        import httpx
    except ImportError:
        return None
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LeadGenBot/1.0)"},
        ) as client:
            resp = client.get(url)
    except Exception:
        return None
    if resp.status_code >= 400:
        return None
    final = str(resp.url).rstrip("/")
    # Soft-404 / homepage bounce: reject when we left the content-hub path.
    if not (_is_blog_index(final) or _is_news_index(final) or _is_blog_article(final)):
        ctype = (resp.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text/" not in ctype:
            return None
        # Accept only if final path still looks like a hub we asked for.
        if not (_is_blog_index(url) or _is_news_index(url)):
            return None
        if _norm_url(final) == _norm_url(_site_base(url)):
            return None
    return final


def augment_mapped_with_content_hubs(
    mapped: list[str],
    website: str,
    *,
    probe: bool = True,
) -> list[str]:
    """
    Ensure blog/news/press hubs are candidates even when sitemap/map omits them.

    When neither a blog nor news index is already in ``mapped``, probe common
    paths and append any that resolve. Always a no-op when hubs are already
    present (avoids extra HTTP).
    """
    usable = [u for u in (mapped or []) if u and not _is_asset(u)]
    has_blog = any(_is_blog_index(u) for u in usable)
    has_news = any(_is_news_index(u) for u in usable)
    if has_blog and has_news:
        return list(mapped or [])

    base = _site_base(website)
    if not base:
        return list(mapped or [])

    seen = {_norm_url(u) for u in usable}
    out = list(mapped or [])
    candidates = [f"{base}{path}" for path in _CONTENT_HUB_PATHS]
    # Prefer probing missing class only (news when no news, blog when no blog).
    for cand in candidates:
        key = _norm_url(cand)
        if not key or key in seen:
            continue
        want_news = _is_news_index(cand) and not has_news
        want_blog = _is_blog_index(cand) and not has_blog
        if not (want_news or want_blog):
            continue
        if not probe:
            out.append(cand)
            seen.add(key)
            if want_news:
                has_news = True
            if want_blog:
                has_blog = True
            if has_blog and has_news:
                break
            continue
        live = _probe_live_url(cand)
        if not live:
            continue
        live_key = _norm_url(live)
        if live_key in seen:
            continue
        out.append(live)
        seen.add(live_key)
        if _is_news_index(live):
            has_news = True
        if _is_blog_index(live):
            has_blog = True
        if has_blog and has_news:
            break
    return out


def select_outreach_urls(
    mapped: list[str],
    website: str,
    *,
    max_pages: int = DEFAULT_OUTREACH_MAX_PAGES,
) -> OutreachPagePlan:
    """
    Choose which mapped URLs to scrape for outreach intel.
    Homepage is always first. Remaining slots follow careers → blog/news →
    articles → services → about/team.

    News/press indexes are first-class with blog: when a site has /news or
    /press but no /blog, those hubs still get a scrape slot.
    """
    max_pages = max(1, min(int(max_pages), 12))
    home = website.strip() if website else ""
    if home and "://" not in home:
        home = f"https://{home}"

    usable = [u for u in mapped if u and not _is_asset(u)]
    careers = [u for u in usable if _is_careers_index(u)]
    careers.sort(key=len)
    blog_indexes = [u for u in usable if _is_blog_index(u)]
    blog_indexes.sort(key=len)
    news_indexes = [u for u in usable if _is_news_index(u)]
    news_indexes.sort(key=len)
    articles = [
        u
        for u in usable
        if _is_blog_article(u) and not _is_blog_index(u) and not _is_news_index(u)
    ]
    articles = [
        u for _, u in sorted(
            (( _article_rank(u, i), u) for i, u in enumerate(articles)),
            key=lambda pair: pair[0],
        )
    ]
    services = [u for u in usable if _is_services(u)]
    services.sort(key=len)
    about = [u for u in usable if _is_about(u)]
    about.sort(key=len)
    team = [u for u in usable if _is_team(u)]
    team.sort(key=len)

    seen: set[str] = set()
    urls: list[str] = []
    if home:
        urls.extend(_pick_unique([home], seen, 1))

    remaining = lambda: max_pages - len(urls)

    careers_picked = _pick_unique(careers, seen, min(1, remaining()))
    urls.extend(careers_picked)

    blog_picked = _pick_unique(blog_indexes, seen, min(1, remaining()))
    urls.extend(blog_picked)
    # News/press is first-class with blog — always take a slot when present.
    news_picked: list[str] = []
    if remaining() > 0 and news_indexes:
        news_picked = _pick_unique(news_indexes, seen, 1)
        urls.extend(news_picked)

    urls.extend(_pick_unique(articles, seen, min(3, remaining())))
    urls.extend(_pick_unique(services, seen, min(1, remaining())))
    urls.extend(_pick_unique(about, seen, min(1, remaining())))
    urls.extend(_pick_unique(team, seen, min(1, remaining())))

    return OutreachPagePlan(
        urls=urls[:max_pages],
        blog_url=blog_picked[0] if blog_picked else None,
        news_url=news_picked[0] if news_picked else None,
        careers_url=careers_picked[0] if careers_picked else None,
    )


def prefer_brief_relevant_urls(
    mapped: list[str],
    website: str,
    brief_terms: list[str],
    *,
    base_urls: list[str] | None = None,
    max_pages: int = DEFAULT_OUTREACH_MAX_PAGES,
    max_brief_slots: int = 4,
) -> list[str]:
    """
    Merge outreach plan URLs with sitemap pages whose path matches brief terms.

    Brief terms come from CLIENT.md / ICP keywords (e.g. property, alquiler).
    Homepage and existing plan URLs stay first; brief-matching pages fill remaining slots.
    """
    max_pages = max(1, min(int(max_pages), 12))
    plan = select_outreach_urls(mapped, website, max_pages=max_pages)
    ordered = list(base_urls or plan.urls)
    seen = {_norm_url(u) for u in ordered}
    terms = [t.strip().lower() for t in (brief_terms or []) if t and len(t.strip()) >= 3]
    if not terms:
        return ordered[:max_pages]

    scored: list[tuple[int, str]] = []
    for url in mapped or []:
        if not url or _is_asset(url):
            continue
        key = _norm_url(url)
        if not key or key in seen:
            continue
        path = _path(url).lower()
        hits = sum(1 for t in terms if t.replace(" ", "-") in path or t.replace(" ", "") in path or t in path)
        if hits:
            scored.append((hits, url))
    scored.sort(key=lambda pair: (-pair[0], len(pair[1])))
    for _, url in scored[: max(0, int(max_brief_slots))]:
        if len(ordered) >= max_pages:
            break
        key = _norm_url(url)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(url)
    return ordered[:max_pages]


_SOCIAL_SLUG_SKIP = {
    "facebook": {
        "sharer",
        "share",
        "dialog",
        "plugins",
        "tr",
        "privacy",
        "policy",
        "policies",
        "login",
        "help",
        "watch",
        "settings",
        "legal",
        "recover",
        "lite",
        "pages",
        "groups",
        "events",
        "hashtag",
    },
    "instagram": {
        "p",
        "reel",
        "reels",
        "stories",
        "explore",
        "accounts",
        "directory",
        "about",
        "legal",
    },
    "tiktok": {"share", "legal", "login"},
    "x": {"intent", "share", "i", "search", "hashtag", "home", "privacy", "login", "settings"},
    "youtube": {"watch", "playlist", "results", "feed", "embed", "redirect", "premium", "shorts"},
    "pinterest": {"pin", "ideas", "search", "today"},
    "reddit": set(),
    "threads": {"login", "intent"},
    "snapchat": set(),
    "linkedin": {"share", "feed", "login", "jobs", "school"},
}


def _social_slug(network: str, url: str) -> str:
    parts = [part for part in urlparse(url).path.strip("/").split("/") if part]
    if not parts:
        return ""
    if network == "tiktok":
        return parts[0].lstrip("@").lower()
    if network == "linkedin":
        if parts[0].lower() not in {"company", "in"} or len(parts) < 2:
            return ""
        return parts[1].lower()
    if network == "reddit":
        return parts[1].lower() if len(parts) > 1 else ""
    if network == "youtube" and parts[0].lower() in {"c", "channel", "user"}:
        return parts[1].lower() if len(parts) > 1 else ""
    if network == "snapchat":
        return parts[1].lower() if len(parts) > 1 else ""
    return parts[0].lstrip("@").lower()


_OTHER_SOCIAL_LABELS = (
    ("tiktok", "TikTok"),
    ("x", "X"),
    ("youtube", "YouTube"),
    ("pinterest", "Pinterest"),
    ("reddit", "Reddit"),
    ("threads", "Threads"),
    ("snapchat", "Snapchat"),
    ("linkedin", "LinkedIn"),
)


def other_social_profiles(found: dict[str, Optional[str]]) -> str:
    """Profiles other than the Facebook and Instagram pages we fetch posts for."""
    parts: list[str] = []
    for key, label in _OTHER_SOCIAL_LABELS:
        url = (found.get(key) or "").strip()
        if url:
            parts.append(f"{label}: {url}")
    return "; ".join(parts)


def extract_social_links(markdown: str) -> dict[str, Optional[str]]:
    """First profile URL per network from crawled text or footer HTML.

    Footer icons are plain links such as https://www.facebook.com/ofracosmetics/
    and https://www.instagram.com/slmdskincare/. Share buttons and post URLs
    are skipped so the company profile is the one that is kept.
    """
    found = {key: None for key in _SOCIAL_RE}
    if not markdown:
        return found
    linkedin_urls: list[str] = []
    for network, pattern in _SOCIAL_RE.items():
        for match in pattern.finditer(markdown):
            url = match.group(0).rstrip(").,;\"'?>")
            url = url.split("?")[0].split("#")[0].rstrip("/")
            if _SHARE_SKIP.search(url):
                continue
            slug = _social_slug(network, url)
            if (
                not slug
                or slug.isdigit()
                or slug in {"profile.php", "photo.php", "story.php"}
                or slug in _SOCIAL_SLUG_SKIP.get(network, set())
            ):
                continue
            if network == "linkedin":
                linkedin_urls.append(url)
                continue
            found[network] = url
            break
    if linkedin_urls:
        company = [url for url in linkedin_urls if "/company/" in url.lower()]
        found["linkedin"] = (company or linkedin_urls)[0]
    return found


def fill_social_links_from_homepage(
    website: str,
    found: dict[str, Optional[str]],
) -> dict[str, Optional[str]]:
    """Fill profile URLs from the live homepage footer when the crawl missed them."""
    html = _fetch_homepage_html(website)
    if not html:
        return found
    extra = extract_social_links(html)
    merged = dict(found)
    for key, url in extra.items():
        if url and not merged.get(key):
            merged[key] = url
    return merged


def _fetch_homepage_html(website: str) -> str:
    base = _site_base(website)
    if not base:
        return ""
    try:
        import httpx
    except ImportError:
        return ""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html",
    }
    try:
        import time

        with httpx.Client(follow_redirects=True, timeout=12, headers=headers) as client:
            resp = None
            for attempt in range(3):
                resp = client.get(base + "/")
                if resp.status_code != 429 or attempt == 2:
                    break
                time.sleep(3 * (attempt + 1))
    except Exception:
        return ""
    if resp is None or resp.status_code >= 400:
        return ""
    ctype = (resp.headers.get("content-type") or "").lower()
    if ctype and "html" not in ctype and "text/" not in ctype:
        return ""
    return resp.text[:500_000]


def extract_decision_maker_mentions(
    markdown: str,
    name: Optional[str],
    *,
    max_mentions: int = 5,
) -> list[str]:
    """Verbatim snippets where the decision-maker's name appears on the site."""
    if not markdown or not (name or "").strip():
        return []
    parts = [p for p in re.split(r"\s+", name.strip()) if p and p.lower() not in {"de", "del", "van", "von"}]
    if len(parts) >= 2:
        pattern = re.compile(
            rf"\b{re.escape(parts[0])}\s+{re.escape(parts[-1])}\b",
            re.I,
        )
    else:
        pattern = re.compile(rf"\b{re.escape(name.strip())}\b", re.I)

    text = re.sub(r"\s+", " ", markdown)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    mentions: list[str] = []
    seen: set[str] = set()
    for sentence in sentences:
        if not pattern.search(sentence):
            continue
        snippet = sentence.strip()
        if len(snippet) > 420:
            match = pattern.search(snippet)
            if match:
                start = max(0, match.start() - 160)
                end = min(len(snippet), match.end() + 160)
                snippet = snippet[start:end].strip()
        key = snippet.lower()
        if key in seen:
            continue
        seen.add(key)
        mentions.append(snippet)
        if len(mentions) >= max_mentions:
            break
    return mentions


def format_blog_posts(posts: list[BlogPost] | None) -> str:
    lines: list[str] = []
    for post in posts or []:
        title = (post.title or "").replace("|", "/").strip()
        if not title:
            continue
        date = (post.published_at or "").strip()
        desc = (post.description or "").replace("|", "/").strip()
        url = (post.url or "").strip()
        parts = [date or "", title]
        if desc:
            parts.append(desc)
        if url:
            parts.append(url)
        lines.append(" | ".join(p for p in parts if p))
    return "; ".join(lines)


def parse_blog_posts(value: str | None) -> list[BlogPost]:
    if not (value or "").strip():
        return []
    posts: list[BlogPost] = []
    for chunk in str(value).split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split("|")]
        date = title = desc = url = ""
        if len(parts) == 1:
            title = parts[0]
        elif re.match(r"^\d{4}", parts[0] or ""):
            date = parts[0]
            title = parts[1] if len(parts) > 1 else ""
            rest = parts[2:]
            urls = [p for p in rest if p.startswith("http")]
            desc_parts = [p for p in rest if p not in urls]
            desc = " ".join(desc_parts)
            url = urls[0] if urls else ""
        else:
            title = parts[0]
            rest = parts[1:]
            urls = [p for p in rest if p.startswith("http")]
            desc_parts = [p for p in rest if p not in urls]
            desc = " ".join(desc_parts)
            url = urls[0] if urls else ""
        if title:
            posts.append(
                BlogPost(
                    title=title,
                    published_at=date or None,
                    description=desc or None,
                    url=url or None,
                )
            )
    return posts


def _join(values: list[str] | None) -> str:
    return "; ".join(v for v in (values or []) if str(v).strip())


def website_analysis_to_csv(wa: Optional[WebsiteAnalysis]) -> dict[str, str]:
    empty = {k: "" for k in WEBSITE_CSV_FIELDS}
    if not wa:
        return empty
    social = {
        "social_facebook": wa.social_facebook or "",
        "social_instagram": wa.social_instagram or "",
        "social_tiktok": wa.social_tiktok or "",
        "social_x": wa.social_x or "",
        "social_youtube": wa.social_youtube or "",
        "social_pinterest": wa.social_pinterest or "",
        "social_reddit": wa.social_reddit or "",
        "social_threads": wa.social_threads or "",
        "social_snapchat": wa.social_snapchat or "",
        "social_linkedin": wa.social_linkedin or "",
        "other_social_profiles": wa.other_social_profiles or "",
        "facebook_post_summary": wa.facebook_post_summary or "",
        "instagram_post_summary": wa.instagram_post_summary or "",
        "meta_ads_active": wa.meta_ads_active or "",
        "meta_ads_count": "" if wa.meta_ads_count is None else str(wa.meta_ads_count),
        "meta_ads_summary": wa.meta_ads_summary or "",
    }
    has_blog = ""
    if wa.has_blog is not None:
        has_blog = str(wa.has_blog)
    row = {
        "website_is_hiring": wa.website_is_hiring or "",
        "website_open_roles": _join(wa.website_open_roles),
        "website_careers_url": wa.website_careers_url or "",
        "services_offered": _join(wa.services_offered),
        "about_summary": wa.about_summary or "",
        "has_blog": has_blog,
        "blog_url": wa.blog_url or "",
        "news_url": wa.news_url or "",
        "blog_posts": format_blog_posts(wa.blog_posts),
        **social,
        "decision_maker_mentions": _join(wa.decision_maker_mentions),
        "website_summary": wa.website_summary or "",
    }
    for key, val in getattr(wa, "collector_extracts", None) or {}:
        if (
            isinstance(key, str)
            and key not in row
            and re.fullmatch(r"[a-z][a-z0-9_]{0,32}", key)
        ):
            row[key] = "" if val is None else str(val)
    return row


def website_csv_row(wa: Optional[WebsiteAnalysis]) -> list[str]:
    data = website_analysis_to_csv(wa)
    return [data[k] for k in WEBSITE_CSV_FIELDS]


def _split(value) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in str(value).split(";") if p.strip()]


def _parse_bool(value) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def _parse_count(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def website_analysis_from_csv(row: dict) -> Optional[WebsiteAnalysis]:
    keys = WEBSITE_CSV_FIELDS + ["last_blog_post", "tech_stack", "has_chatbot"]
    if not any(str(row.get(k) or "").strip() for k in keys):
        return None

    posts = parse_blog_posts(row.get("blog_posts"))
    if not posts:
        legacy_date = (row.get("last_blog_post") or "").strip()
        if legacy_date:
            posts = [BlogPost(title="Untitled", published_at=legacy_date)]

    return WebsiteAnalysis(
        services_offered=_split(row.get("services_offered")),
        about_summary=(row.get("about_summary") or "").strip() or None,
        has_blog=_parse_bool(row.get("has_blog")),
        blog_url=(row.get("blog_url") or "").strip() or None,
        news_url=(row.get("news_url") or "").strip() or None,
        blog_posts=posts,
        social_facebook=(row.get("social_facebook") or "").strip() or None,
        social_instagram=(row.get("social_instagram") or "").strip() or None,
        social_tiktok=(row.get("social_tiktok") or "").strip() or None,
        social_x=(row.get("social_x") or "").strip() or None,
        social_youtube=(row.get("social_youtube") or "").strip() or None,
        social_pinterest=(row.get("social_pinterest") or "").strip() or None,
        social_reddit=(row.get("social_reddit") or "").strip() or None,
        social_threads=(row.get("social_threads") or "").strip() or None,
        social_snapchat=(row.get("social_snapchat") or "").strip() or None,
        social_linkedin=(row.get("social_linkedin") or "").strip() or None,
        other_social_profiles=(row.get("other_social_profiles") or "").strip() or None,
        facebook_post_summary=(row.get("facebook_post_summary") or "").strip() or None,
        instagram_post_summary=(row.get("instagram_post_summary") or "").strip() or None,
        meta_ads_active=(row.get("meta_ads_active") or "").strip() or None,
        meta_ads_count=_parse_count(row.get("meta_ads_count")),
        meta_ads_summary=(row.get("meta_ads_summary") or "").strip() or None,
        decision_maker_mentions=_split(row.get("decision_maker_mentions")),
        website_summary=(row.get("website_summary") or "").strip() or None,
        website_is_hiring=(row.get("website_is_hiring") or "").strip() or None,
        website_open_roles=_split(row.get("website_open_roles")),
        website_careers_url=(row.get("website_careers_url") or "").strip() or None,
    )


def merge_mentions(*groups: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for item in group or []:
            text = str(item).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
    return out[:8]


def parse_blog_posts_from_llm(raw) -> list[BlogPost]:
    if not isinstance(raw, list):
        return []
    posts: list[BlogPost] = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        posts.append(
            BlogPost(
                title=title[:240],
                published_at=str(item.get("published_at") or item.get("date") or "").strip() or None,
                description=str(item.get("description") or item.get("summary") or "").strip() or None,
                url=str(item.get("url") or "").strip() or None,
            )
        )
    return posts
