"""
Outreach-oriented website page selection and deterministic extractors.

Crawl priority (most → least):
  1. Careers / jobs index
  2. Blog / news index + latest articles, and services
  3. About / meet the team

Social profile URLs (Facebook, Instagram, TikTok, X) are parsed from markdown
rather than inferred by the LLM.
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
    r"blog|news|insights|noticias|actualidad|press|resources|articles|"
    r"journal|media|updates"
)
_BLOG_INDEX_ONLY = re.compile(
    r"/(blog|insights|articles|resources|journal|media|updates)/?$", re.I
)
_NEWS_INDEX_ONLY = re.compile(r"/(news|noticias|actualidad|press)/?$", re.I)
_BLOG_ARTICLE = re.compile(rf"/({_BLOG_SEGMENT})/.+", re.I)
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


def select_outreach_urls(
    mapped: list[str],
    website: str,
    *,
    max_pages: int = DEFAULT_OUTREACH_MAX_PAGES,
) -> OutreachPagePlan:
    """
    Choose which mapped URLs to scrape for outreach intel.
    Homepage is always first. Remaining slots follow careers → blog/news/services → about/team.
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
    articles = [u for u in usable if _is_blog_article(u) and not _is_blog_index(u)]
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
    news_picked = []
    if remaining() > 0:
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


def extract_social_links(markdown: str) -> dict[str, Optional[str]]:
    """First clean profile URL per network from crawled markdown."""
    found = {key: None for key in _SOCIAL_RE}
    if not markdown:
        return found
    for network, pattern in _SOCIAL_RE.items():
        for match in pattern.finditer(markdown):
            url = match.group(0).rstrip(").,;\"'")
            if _SHARE_SKIP.search(url):
                continue
            if network == "instagram" and re.search(r"/p/|/reel/", url, re.I):
                continue
            found[network] = url.rstrip("/")
            break
    return found


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
    }
    has_blog = ""
    if wa.has_blog is not None:
        has_blog = str(wa.has_blog)
    return {
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
