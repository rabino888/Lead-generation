"""
Client-first data layout:

  data/clients/{client_id}/
    client.json
    icps/{icp_id}/              # company-outreach library ICPs
      meta.json
      icp.json
    itps/{itp_id}/              # talent-search library profiles
    campaigns/
      in_progress/{campaign_id}/
      saved_for_later/{campaign_id}/
      completed/
        company_outreach/{campaign_id}/
        talent/{campaign_id}/

Legacy trees are still read (not written for new clients):
  company_outreach/icps|campaigns, talent_outreach/itps|campaigns
  campaigns/{id} flat under the client (pre-status)

Legacy pipeline folders under data/campaigns/{id}/ are still resolved
(and may be junctions into the client tree).
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")

CAMPAIGN_SYNC_FILES = (
    "campaign.json",
    "icp.json",
    "seed_sources.json",
    "enrichment_plan.json",
)

# Legacy typed profile roots (still scanned for existing data)
_PROFILE_COMPANY = "company_outreach"
_PROFILE_TALENT = "talent_outreach"
_LIB_ICP = "icps"
_LIB_ITP = "itps"

CAMPAIGN_STATUS_IN_PROGRESS = "in_progress"
CAMPAIGN_STATUS_SAVED = "saved_for_later"
CAMPAIGN_STATUS_COMPLETED = "completed"
CAMPAIGN_STATUSES = (
    CAMPAIGN_STATUS_IN_PROGRESS,
    CAMPAIGN_STATUS_SAVED,
    CAMPAIGN_STATUS_COMPLETED,
)
_COMPLETED_TYPE_COMPANY = "company_outreach"
_COMPLETED_TYPE_TALENT = "talent"


def data_root(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get("DATA_ROOT", "data"))


def slug_id(value: str, *, fallback: str = "item") -> str:
    raw = re.sub(r"[^a-zA-Z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return (raw[:40] or fallback)


def profile_folder(campaign_type: str) -> str:
    """Legacy typed profile folder (company_outreach / talent_outreach)."""
    return _PROFILE_TALENT if (campaign_type or "").strip() == "talent_search" else _PROFILE_COMPANY


def library_folder(campaign_type: str) -> str:
    return _LIB_ITP if (campaign_type or "").strip() == "talent_search" else _LIB_ICP


def completed_type_folder(campaign_type: str) -> str:
    return (
        _COMPLETED_TYPE_TALENT
        if (campaign_type or "").strip() == "talent_search"
        else _COMPLETED_TYPE_COMPANY
    )


def client_dir(root: Path, client_id: str) -> Path:
    return Path(root) / "clients" / client_id


def icp_dir(
    root: Path,
    client_id: str,
    icp_id: str,
    *,
    campaign_type: str = "company_outreach",
) -> Path:
    """Canonical write path: flat icps/ or itps/ under the client."""
    lib = library_folder(campaign_type)
    return client_dir(root, client_id) / lib / icp_id


def find_icp_dir(root: Path, client_id: str, icp_id: str) -> Optional[Path]:
    """Locate a library ICP/ITP: flat first, then legacy typed folders."""
    base = client_dir(root, client_id)
    candidates = (
        base / _LIB_ICP / icp_id,
        base / _LIB_ITP / icp_id,
        base / _PROFILE_COMPANY / _LIB_ICP / icp_id,
        base / _PROFILE_TALENT / _LIB_ITP / icp_id,
        base / _PROFILE_COMPANY / _LIB_ITP / icp_id,
        base / _PROFILE_TALENT / _LIB_ICP / icp_id,
    )
    for cand in candidates:
        if cand.is_dir():
            return cand
    return None


def client_campaign_dir(
    root: Path,
    client_id: str,
    campaign_id: str,
    *,
    campaign_type: str = "company_outreach",
    status: str = CAMPAIGN_STATUS_IN_PROGRESS,
) -> Path:
    """Canonical write path under campaigns/{status}/…"""
    st = (status or CAMPAIGN_STATUS_IN_PROGRESS).strip() or CAMPAIGN_STATUS_IN_PROGRESS
    base = client_dir(root, client_id) / "campaigns"
    if st == CAMPAIGN_STATUS_COMPLETED:
        return base / CAMPAIGN_STATUS_COMPLETED / completed_type_folder(campaign_type) / campaign_id
    return base / st / campaign_id


def _campaign_dir_score(path: Path) -> int:
    """Prefer live trees (stages + artifacts) over config-only sync mirrors."""
    if not path.is_dir():
        return -1
    score = 0
    stages = path / "stages"
    if stages.is_dir():
        score += 20
        try:
            score += min(10, sum(1 for p in stages.iterdir() if p.is_file()))
        except OSError:
            pass
    for name in (
        "cost_runs.json",
        "enrichment_plan.json",
        "seed_sources.json",
        "icp.json",
        "campaign.json",
        "list_run.json",
    ):
        if (path / name).is_file():
            score += 2
    return score


def _pick_best_campaign_dir(candidates: list[Path]) -> Optional[Path]:
    best: Optional[Path] = None
    best_score = -1
    for cand in candidates:
        if not cand.is_dir():
            continue
        score = _campaign_dir_score(cand)
        if score > best_score:
            best = cand
            best_score = score
    return best


def find_client_campaign_dir(root: Path, client_id: str, campaign_id: str) -> Optional[Path]:
    base = client_dir(root, client_id)
    camps = base / "campaigns"
    candidates = [
        camps / CAMPAIGN_STATUS_IN_PROGRESS / campaign_id,
        camps / CAMPAIGN_STATUS_SAVED / campaign_id,
        camps / CAMPAIGN_STATUS_COMPLETED / _COMPLETED_TYPE_COMPANY / campaign_id,
        camps / CAMPAIGN_STATUS_COMPLETED / _COMPLETED_TYPE_TALENT / campaign_id,
        # Optional typed nesting under non-completed statuses
        camps / CAMPAIGN_STATUS_IN_PROGRESS / _COMPLETED_TYPE_COMPANY / campaign_id,
        camps / CAMPAIGN_STATUS_IN_PROGRESS / _COMPLETED_TYPE_TALENT / campaign_id,
        camps / CAMPAIGN_STATUS_SAVED / _COMPLETED_TYPE_COMPANY / campaign_id,
        camps / CAMPAIGN_STATUS_SAVED / _COMPLETED_TYPE_TALENT / campaign_id,
        # Legacy typed + flat
        base / _PROFILE_COMPANY / "campaigns" / campaign_id,
        base / _PROFILE_TALENT / "campaigns" / campaign_id,
        camps / campaign_id,
    ]
    return _pick_best_campaign_dir(candidates)


def legacy_campaign_dir(root: Path, campaign_id: str) -> Path:
    return Path(root) / "campaigns" / campaign_id


def _read_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = {}
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_client_meta(root: Path, client_id: str) -> dict[str, Any]:
    data = _read_json(client_dir(root, client_id) / "client.json", {})
    return data if isinstance(data, dict) else {}


def list_clients(root: Path) -> list[dict[str, Any]]:
    """All clients under data/clients/ with a usable slug id (incl. ICP-only)."""
    base = Path(root) / "clients"
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for d in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if not _ID_RE.match(d.name):
            continue
        if d.name.endswith("-smoketest"):
            continue
        meta = load_client_meta(root, d.name)
        out.append(
            {
                "client_id": d.name,
                "client_name": meta.get("client_name") or d.name,
                "brief": meta.get("brief") or "",
            }
        )
    return out


def ensure_client(
    root: Path,
    client_id: str,
    client_name: str = "",
    *,
    brief: Optional[str] = None,
) -> Path:
    path = client_dir(root, client_id)
    path.mkdir(parents=True, exist_ok=True)
    # Canonical layout
    (path / _LIB_ICP).mkdir(exist_ok=True)
    (path / _LIB_ITP).mkdir(exist_ok=True)
    camps = path / "campaigns"
    (camps / CAMPAIGN_STATUS_IN_PROGRESS).mkdir(parents=True, exist_ok=True)
    (camps / CAMPAIGN_STATUS_SAVED).mkdir(parents=True, exist_ok=True)
    (camps / CAMPAIGN_STATUS_COMPLETED / _COMPLETED_TYPE_COMPANY).mkdir(
        parents=True, exist_ok=True
    )
    (camps / CAMPAIGN_STATUS_COMPLETED / _COMPLETED_TYPE_TALENT).mkdir(
        parents=True, exist_ok=True
    )
    meta_path = path / "client.json"
    meta = _read_json(meta_path, {})
    if not isinstance(meta, dict):
        meta = {}
    meta["client_id"] = client_id
    if client_name:
        meta["client_name"] = client_name
    elif not meta.get("client_name"):
        meta["client_name"] = client_id
    if brief is not None:
        meta["brief"] = brief
    _write_json(meta_path, meta)
    return path


def unique_id(parent: Path, preferred: str) -> str:
    base = preferred[:64] or "item"
    if not (parent / base).exists():
        return base
    for n in range(2, 100):
        cand = f"{base}_{n}"[:64]
        if not (parent / cand).exists():
            return cand
    raise ValueError(f"Could not allocate unique id under {parent}")


def _iter_library_dirs(root: Path, client_id: str) -> list[tuple[Path, str]]:
    """Yield (dir, default_campaign_type) for each library folder that exists."""
    base = client_dir(root, client_id)
    out: list[tuple[Path, str]] = []
    typed = (
        (base / _LIB_ICP, "company_outreach"),
        (base / _LIB_ITP, "talent_search"),
        (base / _PROFILE_COMPANY / _LIB_ICP, "company_outreach"),
        (base / _PROFILE_TALENT / _LIB_ITP, "talent_search"),
    )
    seen: set[Path] = set()
    for folder, default_ctype in typed:
        try:
            resolved = folder.resolve()
        except OSError:
            resolved = folder
        if resolved in seen or not folder.is_dir():
            continue
        seen.add(resolved)
        out.append((folder, default_ctype))
    return out


def list_client_icps(root: Path, client_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for folder, default_ctype in _iter_library_dirs(root, client_id):
        for d in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if d.name in seen:
                continue
            summary = icp_summary(root, client_id, d.name)
            if summary:
                if not summary.get("campaign_type"):
                    summary["campaign_type"] = default_ctype
                seen.add(d.name)
                out.append(summary)
    out.sort(key=lambda x: str(x.get("display_name") or x.get("icp_id") or "").lower())
    return out


def delete_client_icp(root: Path, client_id: str, icp_id: str, *, force: bool = False) -> dict[str, Any]:
    """Delete a library ICP/ITP folder. Blocks if campaigns still reference it unless force=True."""
    path = find_icp_dir(root, client_id, icp_id)
    if path is None or not path.is_dir():
        raise FileNotFoundError(f"ICP not found: {icp_id}")
    summary = icp_summary(root, client_id, icp_id) or {}
    linked = list(summary.get("campaign_ids") or [])
    if linked and not force:
        raise ValueError(
            f"ICP {icp_id!r} is linked to {len(linked)} campaign(s): "
            + ", ".join(linked[:8])
            + ("" if len(linked) <= 8 else "…")
            + ". Re-point or delete those campaigns first, or pass force=1."
        )
    shutil.rmtree(path)
    return {
        "ok": True,
        "icp_id": icp_id,
        "client_id": client_id,
        "deleted_path": str(path),
        "unlinked_campaigns": linked,
    }


def icp_summary(root: Path, client_id: str, icp_id: str) -> Optional[dict[str, Any]]:
    path = find_icp_dir(root, client_id, icp_id)
    if path is None or not path.is_dir():
        return None
    meta = _read_json(path / "meta.json", {})
    icp = _read_json(path / "icp.json", {})
    if not isinstance(meta, dict):
        meta = {}
    if not isinstance(icp, dict):
        icp = {}
    titles = icp.get("job_titles") or []
    if isinstance(titles, str):
        titles = [titles]
    locations = icp.get("contact_locations") or []
    if not locations and icp.get("location"):
        locations = [icp.get("location")]
    campaign_ids = [
        c.name
        for c in list_client_campaign_dirs(root, client_id)
        if (_read_json(c / "campaign.json", {}) or {}).get("icp_id") == icp_id
    ]
    # Also count legacy campaigns that reference this icp
    for cdir in list_legacy_campaign_dirs(root):
        meta_c = _read_json(cdir / "campaign.json", {})
        if not isinstance(meta_c, dict):
            continue
        if meta_c.get("client_id") == client_id and meta_c.get("icp_id") == icp_id:
            if cdir.name not in campaign_ids:
                campaign_ids.append(cdir.name)

    from agent.utils.icp_readiness import assess_icp_depth

    ctype = meta.get("campaign_type") or "company_outreach"
    # Infer from path when meta is thin
    if path.parent.name == _LIB_ITP:
        ctype = meta.get("campaign_type") or "talent_search"
    depth = assess_icp_depth(icp, campaign_type=ctype)

    return {
        "icp_id": icp_id,
        "client_id": client_id,
        "client_name": meta.get("client_name") or client_id,
        "display_name": meta.get("display_name") or meta.get("name") or icp_id,
        "brief": meta.get("brief") or "",
        "campaign_type": ctype,
        "campaign_ids": campaign_ids,
        "disk_path": str(path.resolve()),
        "ready": depth["ready"],
        "missing": depth["missing"],
        "remedy": depth["remedy"],
        "icp": {
            "job_titles": titles[:12],
            "contact_locations": locations,
            "industries": list(icp.get("industries") or [])[:12],
            "company_size_min": icp.get("company_size_min") or icp.get("size_min"),
            "company_size_max": icp.get("company_size_max") or icp.get("size_max"),
            "website_analysis_mode": icp.get("website_analysis_mode") or "full",
        },
    }


def load_icp_doc(root: Path, client_id: str, icp_id: str) -> dict[str, Any]:
    path = find_icp_dir(root, client_id, icp_id)
    if path is None:
        return {}
    data = _read_json(path / "icp.json", {})
    return data if isinstance(data, dict) else {}


def load_icp_meta(root: Path, client_id: str, icp_id: str) -> dict[str, Any]:
    path = find_icp_dir(root, client_id, icp_id)
    if path is None:
        return {}
    data = _read_json(path / "meta.json", {})
    return data if isinstance(data, dict) else {}


def save_icp(
    root: Path,
    client_id: str,
    icp_id: str,
    *,
    icp_data: dict[str, Any],
    display_name: str = "",
    brief: str = "",
    campaign_type: str = "company_outreach",
    client_name: str = "",
    create_new: bool = False,
) -> dict[str, Any]:
    ensure_client(root, client_id, client_name)
    ctype = campaign_type or "company_outreach"
    if create_new:
        # Always write under the canonical flat library — never overwrite a legacy same-id folder.
        path = icp_dir(root, client_id, icp_id, campaign_type=ctype)
    else:
        existing = find_icp_dir(root, client_id, icp_id)
        path = existing if existing is not None else icp_dir(root, client_id, icp_id, campaign_type=ctype)
    path.mkdir(parents=True, exist_ok=True)
    data = dict(icp_data or {})
    data["icp_id"] = icp_id
    data.pop("campaign_id", None)
    _write_json(path / "icp.json", data)
    meta = _read_json(path / "meta.json", {})
    if not isinstance(meta, dict):
        meta = {}
    # When create_new, don't inherit a blank brief over nothing — still set provided brief.
    if create_new:
        brief_val = brief or ""
    else:
        brief_val = brief if brief is not None else meta.get("brief") or ""
    meta.update(
        {
            "icp_id": icp_id,
            "client_id": client_id,
            "client_name": client_name or meta.get("client_name") or client_id,
            "display_name": (display_name or meta.get("display_name") or icp_id).strip() or icp_id,
            "name": (display_name or meta.get("name") or icp_id).strip() or icp_id,
            "brief": brief_val,
            "campaign_type": ctype or meta.get("campaign_type") or "company_outreach",
        }
    )
    _write_json(path / "meta.json", meta)
    return icp_summary(root, client_id, icp_id) or {"icp_id": icp_id}


def unique_icp_id(
    root: Path,
    client_id: str,
    preferred: str,
    *,
    campaign_type: str = "company_outreach",
) -> str:
    """Allocate an ICP/ITP id unused in any library location (flat + legacy typed)."""
    base = (preferred or "item")[:64] or "item"
    for n in range(0, 100):
        cand = base if n == 0 else f"{base}_{n + 1}"[:64]
        if find_icp_dir(root, client_id, cand) is not None:
            continue
        typed = icp_dir(root, client_id, cand, campaign_type=campaign_type)
        if typed.exists():
            continue
        return cand
    raise ValueError(f"Could not allocate unique ICP id under {client_id}")


def snapshot_icp_into_campaign(
    root: Path,
    client_id: str,
    icp_id: str,
    campaign_dir: Path,
) -> None:
    """Copy library ICP into the campaign folder for pipeline tools."""
    doc = load_icp_doc(root, client_id, icp_id)
    if not doc:
        return
    snap = dict(doc)
    snap["icp_id"] = icp_id
    snap["campaign_id"] = campaign_dir.name
    _write_json(campaign_dir / "icp.json", snap)


def _looks_like_campaign_dir(path: Path) -> bool:
    if not path.is_dir() or path.name.startswith("."):
        return False
    if path.name in CAMPAIGN_STATUSES or path.name in (
        _COMPLETED_TYPE_COMPANY,
        _COMPLETED_TYPE_TALENT,
    ):
        return False
    return any(
        (path / name).exists()
        for name in (
            "campaign.json",
            "icp.json",
            "list_run.json",
            "enrichment_plan.json",
            "seed_sources.json",
            "stages",
        )
    )


def _collect_campaign_dirs_under(folder: Path, into: dict[str, list[Path]]) -> None:
    if not folder.is_dir():
        return
    for p in folder.iterdir():
        if not p.is_dir() or p.name.startswith("."):
            continue
        if p.name in (_COMPLETED_TYPE_COMPANY, _COMPLETED_TYPE_TALENT) and not _looks_like_campaign_dir(p):
            for child in p.iterdir():
                if _looks_like_campaign_dir(child):
                    into.setdefault(child.name, []).append(child)
            continue
        if _looks_like_campaign_dir(p):
            into.setdefault(p.name, []).append(p)


def list_client_campaign_dirs(root: Path, client_id: str) -> list[Path]:
    base = client_dir(root, client_id)
    by_id: dict[str, list[Path]] = {}
    camps = base / "campaigns"
    if camps.is_dir():
        for status in (CAMPAIGN_STATUS_IN_PROGRESS, CAMPAIGN_STATUS_SAVED):
            _collect_campaign_dirs_under(camps / status, by_id)
        completed = camps / CAMPAIGN_STATUS_COMPLETED
        if completed.is_dir():
            for type_name in (_COMPLETED_TYPE_COMPANY, _COMPLETED_TYPE_TALENT):
                _collect_campaign_dirs_under(completed / type_name, by_id)
            # stray campaign folders directly under completed/
            for p in completed.iterdir():
                if _looks_like_campaign_dir(p):
                    by_id.setdefault(p.name, []).append(p)
        # Legacy flat campaigns sitting next to status folders
        for p in camps.iterdir():
            if p.name in CAMPAIGN_STATUSES:
                continue
            if _looks_like_campaign_dir(p):
                by_id.setdefault(p.name, []).append(p)
    for folder in (
        base / _PROFILE_COMPANY / "campaigns",
        base / _PROFILE_TALENT / "campaigns",
    ):
        _collect_campaign_dirs_under(folder, by_id)
    out: list[Path] = []
    for cid, cands in by_id.items():
        picked = _pick_best_campaign_dir(cands)
        if picked is not None:
            out.append(picked)
    return sorted(out, key=lambda p: p.name.lower())


def list_legacy_campaign_dirs(root: Path) -> list[Path]:
    base = Path(root) / "campaigns"
    if not base.is_dir():
        return []
    return sorted(
        [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")],
        key=lambda p: p.name.lower(),
    )


def resolve_campaign_dir(root: Path, campaign_id: str) -> Optional[Path]:
    """Prefer the richest live campaign folder (stages over config-only mirrors)."""
    candidates: list[Path] = []
    clients = Path(root) / "clients"
    if clients.is_dir():
        for client in clients.iterdir():
            if not client.is_dir():
                continue
            found = find_client_campaign_dir(root, client.name, campaign_id)
            if found is not None:
                candidates.append(found)
    legacy = legacy_campaign_dir(root, campaign_id)
    if legacy.is_dir():
        candidates.append(legacy)
    return _pick_best_campaign_dir(candidates)


def list_all_campaign_dirs(root: Path) -> list[Path]:
    """Unique campaign folders; richest path wins on id clash."""
    by_id: dict[str, list[Path]] = {}
    clients = Path(root) / "clients"
    if clients.is_dir():
        for client in sorted(clients.iterdir(), key=lambda p: p.name.lower()):
            if not client.is_dir():
                continue
            for camp in list_client_campaign_dirs(root, client.name):
                by_id.setdefault(camp.name, []).append(camp)
    for camp in list_legacy_campaign_dirs(root):
        by_id.setdefault(camp.name, []).append(camp)
    out: list[Path] = []
    for cid, cands in by_id.items():
        picked = _pick_best_campaign_dir(cands)
        if picked is not None:
            out.append(picked)
    return sorted(out, key=lambda p: p.name.lower())


def _campaign_already_under_client(campaign_dir: Path, client_id: str) -> bool:
    """True when campaign_dir is under …/clients/{client_id}/…/campaigns/…"""
    try:
        parts = list(campaign_dir.resolve().parts)
    except OSError:
        parts = list(campaign_dir.parts)
    if "clients" not in parts or "campaigns" not in parts:
        return False
    try:
        idx = parts.index("clients")
    except ValueError:
        return False
    if idx + 1 >= len(parts) or parts[idx + 1] != client_id:
        return False
    try:
        cidx = parts.index("campaigns", idx + 1)
    except ValueError:
        return False
    return cidx < len(parts) - 1


def sync_campaign_config_to_client(
    campaign_dir: Path,
    *,
    client_id: Optional[str] = None,
) -> Optional[Path]:
    """
    Mirror config files into the client tree when the campaign still lives
    under legacy data/campaigns/{id}/.
    """
    campaign_dir = Path(campaign_dir)
    if not campaign_dir.is_dir():
        return None
    meta = _read_json(campaign_dir / "campaign.json", {})
    if not isinstance(meta, dict):
        meta = {}
    cid = (client_id or meta.get("client_id") or "").strip()
    if not cid:
        return None
    if _campaign_already_under_client(campaign_dir, cid):
        return campaign_dir
    root = campaign_dir.parent.parent  # …/data/campaigns/id → …/data
    # If already under clients via typed path, root detection differs — bail out
    if "clients" in campaign_dir.parts:
        return campaign_dir
    ctype = str(meta.get("campaign_type") or "company_outreach")
    ensure_client(root, cid, str(meta.get("client_name") or cid))
    dest = client_campaign_dir(root, cid, campaign_dir.name, campaign_type=ctype)
    if dest.resolve() == campaign_dir.resolve():
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    for name in CAMPAIGN_SYNC_FILES:
        src = campaign_dir / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    return dest


def set_campaign_display_name(campaign_dir: Path, display_name: str) -> dict[str, Any]:
    name = (display_name or "").strip()
    if not name:
        raise ValueError("display_name is required")
    meta = _read_json(campaign_dir / "campaign.json", {})
    if not isinstance(meta, dict):
        meta = {}
    meta["name"] = name
    meta["display_name"] = name
    _write_json(campaign_dir / "campaign.json", meta)
    sync_campaign_config_to_client(campaign_dir)
    return meta


def move_campaign_to_status(
    root: Path,
    client_id: str,
    campaign_id: str,
    *,
    status: str,
    campaign_type: Optional[str] = None,
) -> Path:
    """
    Move a campaign between status buckets (in_progress / saved_for_later / completed/…).

    Updates ``campaign.json`` status and refreshes the ``data/campaigns/{id}`` junction.
    """
    root = Path(root)
    st = (status or "").strip()
    if st not in CAMPAIGN_STATUSES:
        raise ValueError(f"status must be one of {CAMPAIGN_STATUSES}")

    src = find_client_campaign_dir(root, client_id, campaign_id)
    if src is None or not src.is_dir():
        raise FileNotFoundError(f"Campaign not found: {client_id}/{campaign_id}")

    meta = _read_json(src / "campaign.json", {})
    if not isinstance(meta, dict):
        meta = {}
    ctype = (
        (campaign_type or "").strip()
        or str(meta.get("campaign_type") or "company_outreach").strip()
        or "company_outreach"
    )
    dest = client_campaign_dir(
        root, client_id, campaign_id, campaign_type=ctype, status=st
    )
    if src.resolve() == dest.resolve():
        meta["status"] = st
        meta["campaign_type"] = ctype
        _write_json(src / "campaign.json", meta)
        return src

    if dest.exists():
        raise FileExistsError(f"Destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    meta = _read_json(dest / "campaign.json", {})
    if not isinstance(meta, dict):
        meta = {}
    meta["status"] = st
    meta["campaign_type"] = ctype
    meta["campaign_id"] = campaign_id
    meta["client_id"] = client_id
    _write_json(dest / "campaign.json", meta)

    # Refresh legacy junction under data/campaigns/{id}
    legacy = legacy_campaign_dir(root, campaign_id)
    try:
        if legacy.exists() or legacy.is_symlink():
            if legacy.is_dir() and not legacy.is_symlink():
                # Real folder should not be deleted; only junctions/links
                if os.name == "nt":
                    import subprocess

                    subprocess.run(
                        ["cmd", "/c", "rmdir", str(legacy)],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                else:
                    legacy.unlink(missing_ok=True)  # type: ignore[arg-type]
            else:
                legacy.unlink(missing_ok=True)
    except OSError:
        pass
    _try_junction(legacy, dest)
    return dest


def extract_icp_from_campaign(
    root: Path,
    campaign_dir: Path,
    *,
    icp_id: Optional[str] = None,
) -> Optional[str]:
    """
    Ensure a library ICP exists for this campaign. Returns icp_id.
    Does not move the campaign folder.
    """
    campaign_dir = Path(campaign_dir)
    meta = _read_json(campaign_dir / "campaign.json", {})
    if not isinstance(meta, dict):
        meta = {}
    client_id = (meta.get("client_id") or "").strip()
    if not client_id:
        return None
    client_name = str(meta.get("client_name") or client_id)
    ctype = str(meta.get("campaign_type") or "company_outreach")
    existing = (meta.get("icp_id") or "").strip()
    if existing and find_icp_dir(root, client_id, existing) is not None:
        return existing

    preferred = (icp_id or "").strip()
    if not preferred:
        preferred = slug_id(
            str(meta.get("display_name") or meta.get("name") or campaign_dir.name),
            fallback=campaign_dir.name,
        )
    ensure_client(root, client_id, client_name)
    lib_parent = client_dir(root, client_id) / library_folder(ctype)
    lib_parent.mkdir(parents=True, exist_ok=True)
    new_id = unique_id(lib_parent, preferred)
    icp_doc = _read_json(campaign_dir / "icp.json", {})
    if not isinstance(icp_doc, dict):
        icp_doc = {}
    save_icp(
        root,
        client_id,
        new_id,
        icp_data=icp_doc,
        display_name=str(meta.get("display_name") or meta.get("name") or new_id),
        brief=str(meta.get("brief") or ""),
        campaign_type=ctype,
        client_name=client_name,
    )
    meta["icp_id"] = new_id
    _write_json(campaign_dir / "campaign.json", meta)
    sync_campaign_config_to_client(campaign_dir, client_id=client_id)
    return new_id


def migrate_campaign_under_client(root: Path, campaign_dir: Path) -> Path:
    """
    Move legacy data/campaigns/{id} → data/clients/{cid}/campaigns/in_progress/{id}
    and extract/link ICP. Leaves a directory junction at the legacy path when possible.
    """
    root = Path(root)
    campaign_dir = Path(campaign_dir).resolve()
    meta = _read_json(campaign_dir / "campaign.json", {})
    if not isinstance(meta, dict):
        meta = {}
    client_id = (meta.get("client_id") or "").strip()
    if not client_id:
        raise ValueError(f"No client_id on {campaign_dir}")
    ctype = str(meta.get("campaign_type") or "company_outreach")
    ensure_client(root, client_id, str(meta.get("client_name") or client_id))
    icp_id = extract_icp_from_campaign(root, campaign_dir)
    dest = client_campaign_dir(root, client_id, campaign_dir.name, campaign_type=ctype)
    if dest.resolve() == campaign_dir.resolve():
        if icp_id:
            snapshot_icp_into_campaign(root, client_id, icp_id, dest)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        _merge_tree(campaign_dir, dest)
        shutil.rmtree(campaign_dir)
    else:
        shutil.move(str(campaign_dir), str(dest))

    meta = _read_json(dest / "campaign.json", {})
    if isinstance(meta, dict) and icp_id:
        meta["icp_id"] = icp_id
        _write_json(dest / "campaign.json", meta)
        snapshot_icp_into_campaign(root, client_id, icp_id, dest)

    legacy = legacy_campaign_dir(root, dest.name)
    if not legacy.exists():
        _try_junction(legacy, dest)
    return dest


def _merge_tree(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                _merge_tree(item, target)
            else:
                shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _try_junction(link: Path, target: Path) -> bool:
    """Create a Windows junction or POSIX symlink so legacy paths keep working."""
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            import subprocess

            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        link.symlink_to(target, target_is_directory=True)
        return True
    except Exception:
        # Fallback: write a tiny pointer file for operators
        try:
            _write_json(
                link.parent / f"{link.name}.relocated.json",
                {"relocated_to": str(target), "campaign_id": link.name},
            )
        except OSError:
            pass
        return False


def migrate_all_legacy_campaigns(root: Optional[Path] = None) -> dict[str, Any]:
    root = data_root(root)
    moved: list[str] = []
    linked_only: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    for camp in list_legacy_campaign_dirs(root):
        # Skip if this path is already a junction into clients
        try:
            resolved = camp.resolve()
            if "clients" in resolved.parts and "campaigns" in resolved.parts:
                # already nested via junction
                extract_icp_from_campaign(root, resolved)
                linked_only.append(camp.name)
                continue
        except OSError:
            pass
        meta = _read_json(camp / "campaign.json", {})
        if not isinstance(meta, dict) or not meta.get("client_id"):
            skipped.append(camp.name)
            continue
        try:
            migrate_campaign_under_client(root, camp)
            moved.append(camp.name)
        except Exception as exc:  # noqa: BLE001 — migration report
            errors.append(f"{camp.name}: {exc}")
    return {"moved": moved, "linked_only": linked_only, "skipped": skipped, "errors": errors}


def migrate_legacy_library_icps(root: Optional[Path] = None) -> dict[str, Any]:
    """
    Move typed library folders into flat client libs:
      company_outreach/icps/{id} → icps/{id}
      talent_outreach/itps/{id}  → itps/{id}

    Leaves nothing at the typed path once moved. Safe to re-run.
    """
    root = data_root(root)
    clients_root = Path(root) / "clients"
    moved: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    if not clients_root.is_dir():
        return {"moved": moved, "skipped": skipped, "errors": errors}

    typed_sources = (
        (_PROFILE_COMPANY, _LIB_ICP, "company_outreach"),
        (_PROFILE_TALENT, _LIB_ITP, "talent_search"),
        (_PROFILE_COMPANY, _LIB_ITP, "talent_search"),
        (_PROFILE_TALENT, _LIB_ICP, "company_outreach"),
    )

    for client_dir_path in sorted(clients_root.iterdir()):
        if not client_dir_path.is_dir() or client_dir_path.name.startswith("."):
            continue
        cid = client_dir_path.name
        for profile, lib, default_ctype in typed_sources:
            src_root = client_dir_path / profile / lib
            if not src_root.is_dir():
                continue
            for icp_folder in sorted(src_root.iterdir()):
                if not icp_folder.is_dir() or icp_folder.name.startswith("."):
                    continue
                icp_id = icp_folder.name
                meta = _read_json(icp_folder / "meta.json", {})
                if not isinstance(meta, dict):
                    meta = {}
                ctype = str(meta.get("campaign_type") or default_ctype)
                dest = icp_dir(root, cid, icp_id, campaign_type=ctype)
                key = f"{cid}/{profile}/{lib}/{icp_id}"
                try:
                    if dest.resolve() == icp_folder.resolve():
                        skipped.append(key)
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists():
                        # Prefer keeping flat copy; drop typed duplicate
                        shutil.rmtree(icp_folder)
                        skipped.append(f"{key} (flat exists)")
                    else:
                        shutil.move(str(icp_folder), str(dest))
                        meta2 = _read_json(dest / "meta.json", {})
                        if isinstance(meta2, dict) and not meta2.get("campaign_type"):
                            meta2["campaign_type"] = ctype
                            _write_json(dest / "meta.json", meta2)
                        moved.append(key)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{key}: {exc}")
            try:
                if src_root.is_dir() and not any(src_root.iterdir()):
                    src_root.rmdir()
            except OSError:
                pass

    return {"moved": moved, "skipped": skipped, "errors": errors}


def migrate_legacy_typed_campaigns(root: Optional[Path] = None) -> dict[str, Any]:
    """
    Move company_outreach|talent_outreach/campaigns/{id}
    → campaigns/in_progress/{id} (or completed/{type} when status is set).
    Safe to re-run.
    """
    root = data_root(root)
    clients_root = Path(root) / "clients"
    moved: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    if not clients_root.is_dir():
        return {"moved": moved, "skipped": skipped, "errors": errors}

    for client_dir_path in sorted(clients_root.iterdir()):
        if not client_dir_path.is_dir() or client_dir_path.name.startswith("."):
            continue
        cid = client_dir_path.name
        for profile, default_ctype in (
            (_PROFILE_COMPANY, "company_outreach"),
            (_PROFILE_TALENT, "talent_search"),
        ):
            src_root = client_dir_path / profile / "campaigns"
            if not src_root.is_dir():
                continue
            for camp in sorted(src_root.iterdir()):
                if not _looks_like_campaign_dir(camp):
                    continue
                meta = _read_json(camp / "campaign.json", {})
                if not isinstance(meta, dict):
                    meta = {}
                ctype = str(meta.get("campaign_type") or default_ctype)
                status = str(meta.get("status") or CAMPAIGN_STATUS_IN_PROGRESS)
                if status not in CAMPAIGN_STATUSES:
                    status = CAMPAIGN_STATUS_IN_PROGRESS
                dest = client_campaign_dir(
                    root, cid, camp.name, campaign_type=ctype, status=status
                )
                key = f"{cid}/{profile}/campaigns/{camp.name}"
                try:
                    if dest.resolve() == camp.resolve():
                        skipped.append(key)
                        continue
                    ensure_client(root, cid, str(meta.get("client_name") or cid))
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists():
                        _merge_tree(camp, dest)
                        shutil.rmtree(camp)
                        skipped.append(f"{key} (merged into status)")
                    else:
                        shutil.move(str(camp), str(dest))
                        moved.append(key)
                    # Refresh legacy junction if present
                    legacy = legacy_campaign_dir(root, dest.name)
                    if legacy.exists():
                        try:
                            if os.name == "nt":
                                import subprocess

                                # Remove junction / empty dir without deleting target
                                subprocess.run(
                                    ["cmd", "/c", "rmdir", str(legacy)],
                                    check=False,
                                    capture_output=True,
                                    text=True,
                                )
                            elif legacy.is_symlink():
                                legacy.unlink()
                        except OSError:
                            pass
                    if not legacy.exists():
                        _try_junction(legacy, dest)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{key}: {exc}")
            try:
                if src_root.is_dir() and not any(src_root.iterdir()):
                    src_root.rmdir()
            except OSError:
                pass

    return {"moved": moved, "skipped": skipped, "errors": errors}
