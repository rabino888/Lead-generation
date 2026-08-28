"""
Optional deterministic weak-seed checks.

Campaigns opt in via icp.json:
  apply_weak_seed_checks: true
  weak_seed_rule_pack: "generic" | "none" | ""   (default none when checks off)
  weak_seed_extra_patterns: ["regex|reason", ...]  optional campaign-specific rules

No campaign-tuned rules run unless explicitly enabled.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Named packs — only used when campaign sets weak_seed_rule_pack.
_RULE_PACKS: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "generic": [
        (re.compile(r"^//|headcase", re.I), "non-company placeholder name"),
        (re.compile(r"aave|crypto|blockchain|web3|defi|bitcoin", re.I), "crypto/web3"),
        (re.compile(r"spectate|888|gambling|casino|poker|\bbet\b", re.I), "gambling-adjacent"),
    ],
}

_SUSPICIOUS_DOMAINS = (
    "web.app",
    "firebaseapp.com",
    "blogspot.com",
    "wordpress.com",
    "medium.com",
)


def _parse_extra_patterns(raw: list[str] | None) -> list[tuple[re.Pattern[str], str]]:
    out: list[tuple[re.Pattern[str], str]] = []
    for item in raw or []:
        if not item or "|" not in item:
            continue
        pattern_s, reason = item.split("|", 1)
        try:
            out.append((re.compile(pattern_s.strip(), re.I), reason.strip() or "weak_seed"))
        except re.error:
            continue
    return out


def weak_seed_reason(
    name: str,
    website: str,
    industry: str = "",
    *,
    enabled: bool = False,
    rule_pack: str = "",
    extra_patterns: list[str] | None = None,
    check_suspicious_domains: bool = True,
) -> str | None:
    """
    Return a rejection reason, or None if the seed passes.

    When enabled=False (default), always returns None — campaigns must opt in.
    """
    if not enabled:
        return None

    blob = f"{name} {industry}"
    rules: list[tuple[re.Pattern[str], str]] = []
    pack = (rule_pack or "").strip().lower()
    if pack and pack != "none":
        rules.extend(_RULE_PACKS.get(pack, []))
    rules.extend(_parse_extra_patterns(extra_patterns))

    for pattern, reason in rules:
        if pattern.search(blob):
            return reason

    if check_suspicious_domains and website:
        domain = urlparse(
            website if website.startswith("http") else f"https://{website}"
        ).netloc.lower().removeprefix("www.")
        for bad in _SUSPICIOUS_DOMAINS:
            if domain == bad or domain.endswith(f".{bad}"):
                return f"suspicious domain ({bad})"

    return None


def is_strong_icp_seed(
    name: str,
    website: str,
    industry: str = "",
    *,
    enabled: bool = False,
    rule_pack: str = "",
    extra_patterns: list[str] | None = None,
) -> bool:
    return (
        weak_seed_reason(
            name,
            website,
            industry,
            enabled=enabled,
            rule_pack=rule_pack,
            extra_patterns=extra_patterns,
        )
        is None
    )
