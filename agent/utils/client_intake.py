"""
Local client intake from uploaded docs or public URLs.

No paid LLM / Firecrawl — PDF via pypdf, HTML via simple tag strip, httpx fetch.
Writes client.json + CLIENT.md + sources/ under data/clients/{id}/.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_URL_BYTES = 5 * 1024 * 1024
MAX_BRIEF_CHARS = 12_000
ALLOWED_UPLOAD_SUFFIXES = {".pdf", ".md", ".txt", ".markdown", ".text"}

_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in {"script", "style", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = (data or "").strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return "\n".join(self._chunks)


def slugify_client_id(name: str, *, fallback: str = "client") -> str:
    raw = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip().lower()).strip("_")
    slug = (raw[:64] or fallback)
    if not _SLUG_RE.match(slug):
        slug = fallback
    return slug


def allocate_client_id(data_root: Path, preferred: str) -> str:
    base = slugify_client_id(preferred)
    clients = Path(data_root) / "clients"
    if not (clients / base).exists():
        return base
    for n in range(2, 100):
        cand = f"{base}_{n}"[:64]
        if not (clients / cand).exists():
            return cand
    raise ValueError(f"Could not allocate unique client id for {preferred!r}")


def extract_text_from_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def extract_text_from_bytes(data: bytes, *, content_type: str = "", filename: str = "") -> str:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".pdf") or "pdf" in ctype:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            return extract_text_from_pdf(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    if name.endswith((".md", ".markdown", ".txt", ".text")) or ctype.startswith("text/"):
        return data.decode("utf-8", errors="replace").strip()
    # Heuristic: treat as HTML/text
    text = data.decode("utf-8", errors="replace")
    if "<html" in text[:500].lower() or "<body" in text[:500].lower():
        return html_to_text(text)
    return text.strip()


def html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html or "")
    return parser.text().strip()


def extract_text_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(path)
    if suffix in {".md", ".markdown", ".txt", ".text"}:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    raw = path.read_bytes()
    return extract_text_from_bytes(raw, filename=path.name)


def fetch_url_bytes(url: str) -> tuple[bytes, str]:
    """Return (body, content_type). Rejects non-http(s) and oversized bodies."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be http(s) with a host")
    import httpx

    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > MAX_URL_BYTES:
                    raise ValueError(f"URL body exceeds {MAX_URL_BYTES} bytes")
                chunks.append(chunk)
            return b"".join(chunks), ctype


def truncate_brief(text: str, *, max_chars: int = MAX_BRIEF_CHARS) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 20].rstrip() + "\n\n…[truncated]"


def build_client_brief_markdown(
    *,
    client_id: str,
    client_name: str,
    body: str,
    source_doc: str = "",
    source_url: str = "",
    notes: str = "",
) -> str:
    lines = [
        f"# {client_name} — client brief",
        "",
        f"**Client id:** `{client_id}`  ",
    ]
    if source_doc:
        lines.append(f"**Source file:** `{source_doc}`  ")
    if source_url:
        lines.append(f"**Source URL:** {source_url}  ")
    lines.append("")
    if (notes or "").strip():
        lines.extend(["## Operator notes", "", notes.strip(), ""])
    lines.extend(["## Source extract", "", truncate_brief(body) or "(no extractable text)", ""])
    return "\n".join(lines)


def create_client_from_source(
    data_root: Path,
    *,
    client_name: str,
    upload_bytes: Optional[bytes] = None,
    upload_filename: str = "",
    source_url: str = "",
    notes: str = "",
    client_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create/update a client folder from an optional upload and/or URL.

    Returns summary dict with client_id, paths, brief_preview.
    """
    from agent.utils.client_store import ensure_client, load_client_meta

    name = (client_name or "").strip()
    if not name:
        raise ValueError("client_name is required")
    url = (source_url or "").strip()
    if not upload_bytes and not url and not (notes or "").strip():
        raise ValueError("Provide a file upload, a URL, or notes")

    cid = (client_id or "").strip() or allocate_client_id(data_root, name)
    if not _SLUG_RE.match(cid):
        raise ValueError(f"Invalid client id: {cid}")

    ensure_client(data_root, cid, name)
    client_path = Path(data_root) / "clients" / cid
    sources = client_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)

    extracts: list[str] = []
    source_doc = ""
    saved_url = ""

    if upload_bytes is not None:
        if len(upload_bytes) > MAX_UPLOAD_BYTES:
            raise ValueError(f"Upload exceeds {MAX_UPLOAD_BYTES} bytes")
        fname = Path(upload_filename or "upload.bin").name
        suffix = Path(fname).suffix.lower()
        if suffix and suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise ValueError(
                f"Unsupported file type {suffix}; allowed: {sorted(ALLOWED_UPLOAD_SUFFIXES)}"
            )
        if not suffix:
            fname = f"{fname}.txt"
            suffix = ".txt"
        dest = sources / fname
        dest.write_bytes(upload_bytes)
        source_doc = f"sources/{fname}"
        extracts.append(extract_text_from_path(dest))

    if url:
        body, ctype = fetch_url_bytes(url)
        # Prefer filename from path; default by content type
        path_name = Path(urlparse(url).path).name or "page"
        if "pdf" in ctype and not path_name.lower().endswith(".pdf"):
            path_name = f"{path_name}.pdf"
        elif not Path(path_name).suffix:
            path_name = f"{path_name}.html"
        # Sanitize filename
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", path_name)[:120] or "page.html"
        dest = sources / safe
        dest.write_bytes(body)
        saved_url = url
        if not source_doc:
            source_doc = f"sources/{safe}"
        extracts.append(extract_text_from_bytes(body, content_type=ctype, filename=safe))

    body_text = "\n\n".join(x for x in extracts if x).strip()
    if (notes or "").strip() and not body_text:
        body_text = notes.strip()

    brief_md = build_client_brief_markdown(
        client_id=cid,
        client_name=name,
        body=body_text,
        source_doc=source_doc,
        source_url=saved_url,
        notes=notes,
    )
    (client_path / "CLIENT.md").write_text(brief_md, encoding="utf-8")

    # Short brief for client.json (first ~2k of extract)
    short = truncate_brief(body_text or notes, max_chars=2000)
    meta = load_client_meta(data_root, cid)
    meta.update(
        {
            "client_id": cid,
            "client_name": name,
            "brief": short,
            "brief_path": "CLIENT.md",
        }
    )
    if source_doc:
        meta["source_doc"] = source_doc
    if saved_url:
        meta["source_url"] = saved_url
    (client_path / "client.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "client_id": cid,
        "client_name": name,
        "brief_path": "CLIENT.md",
        "source_doc": source_doc or None,
        "source_url": saved_url or None,
        "brief_preview": short[:400],
        "path": str(client_path),
    }
