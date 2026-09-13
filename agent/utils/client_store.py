"""
Client-first data layout:

  data/clients/{client_id}/
    client.json
    icps/{icp_id}/
      meta.json     — display_name, brief, campaign_type, cross-refs
      icp.json      — targeting rules (1 ICP → many campaigns)
    campaigns/{campaign_id}/
      campaign.json — includes icp_id
      enrichment_plan.json, seed_sources.json, stages/, …

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


def data_root(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get("DATA_ROOT", "data"))


def slug_id(value: str, *, fallback: str = "item") -> str:
    raw = re.sub(r"[^a-zA-Z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return (raw[:40] or fallback)


def client_dir(root: Path, client_id: str) -> Path:
    return Path(root) / "clients" / client_id


def icp_dir(root: Path, client_id: str, icp_id: str) -> Path:
    return client_dir(root, client_id) / "icps" / icp_id


def client_campaign_dir(root: Path, client_id: str, campaign_id: str) -> Path:
    return client_dir(root, client_id) / "campaigns" / campaign_id


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


def ensure_client(root: Path, client_id: str, client_name: str = "") -> Path:
    path = client_dir(root, client_id)
    path.mkdir(parents=True, exist_ok=True)
    (path / "icps").mkdir(exist_ok=True)
    (path / "campaigns").mkdir(exist_ok=True)
    meta_path = path / "client.json"
    meta = _read_json(meta_path, {})
    if not isinstance(meta, dict):
        meta = {}
    meta["client_id"] = client_id
    if client_name:
        meta["client_name"] = client_name
    elif not meta.get("client_name"):
        meta["client_name"] = client_id
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


def list_client_icps(root: Path, client_id: str) -> list[dict[str, Any]]:
    base = client_dir(root, client_id) / "icps"
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for d in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        summary = icp_summary(root, client_id, d.name)
        if summary:
            out.append(summary)
    return out


def icp_summary(root: Path, client_id: str, icp_id: str) -> Optional[dict[str, Any]]:
    path = icp_dir(root, client_id, icp_id)
    if not path.is_dir():
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
    return {
        "icp_id": icp_id,
        "client_id": client_id,
        "client_name": meta.get("client_name") or client_id,
        "display_name": meta.get("display_name") or meta.get("name") or icp_id,
        "brief": meta.get("brief") or "",
        "campaign_type": meta.get("campaign_type") or "company_outreach",
        "campaign_ids": campaign_ids,
        "disk_path": str(path.resolve()),
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
    path = icp_dir(root, client_id, icp_id) / "icp.json"
    data = _read_json(path, {})
    return data if isinstance(data, dict) else {}


def load_icp_meta(root: Path, client_id: str, icp_id: str) -> dict[str, Any]:
    path = icp_dir(root, client_id, icp_id) / "meta.json"
    data = _read_json(path, {})
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
) -> dict[str, Any]:
    ensure_client(root, client_id, client_name)
    path = icp_dir(root, client_id, icp_id)
    path.mkdir(parents=True, exist_ok=True)
    data = dict(icp_data or {})
    data["icp_id"] = icp_id
    data.pop("campaign_id", None)
    _write_json(path / "icp.json", data)
    meta = load_icp_meta(root, client_id, icp_id)
    meta.update(
        {
            "icp_id": icp_id,
            "client_id": client_id,
            "client_name": client_name or meta.get("client_name") or client_id,
            "display_name": (display_name or meta.get("display_name") or icp_id).strip() or icp_id,
            "name": (display_name or meta.get("name") or icp_id).strip() or icp_id,
            "brief": brief if brief is not None else meta.get("brief") or "",
            "campaign_type": campaign_type or meta.get("campaign_type") or "company_outreach",
        }
    )
    _write_json(path / "meta.json", meta)
    return icp_summary(root, client_id, icp_id) or {"icp_id": icp_id}


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


def list_client_campaign_dirs(root: Path, client_id: str) -> list[Path]:
    base = client_dir(root, client_id) / "campaigns"
    if not base.is_dir():
        return []
    return sorted(
        [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")],
        key=lambda p: p.name.lower(),
    )


def list_legacy_campaign_dirs(root: Path) -> list[Path]:
    base = Path(root) / "campaigns"
    if not base.is_dir():
        return []
    return sorted(
        [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")],
        key=lambda p: p.name.lower(),
    )


def resolve_campaign_dir(root: Path, campaign_id: str) -> Optional[Path]:
    """Prefer client-nested campaign; fall back to legacy data/campaigns/{id}."""
    clients = Path(root) / "clients"
    if clients.is_dir():
        for client in clients.iterdir():
            if not client.is_dir():
                continue
            cand = client / "campaigns" / campaign_id
            if cand.is_dir():
                return cand
    legacy = legacy_campaign_dir(root, campaign_id)
    if legacy.is_dir():
        return legacy
    return None


def list_all_campaign_dirs(root: Path) -> list[Path]:
    """Unique campaign folders (client path wins over legacy duplicate)."""
    seen: set[str] = set()
    out: list[Path] = []
    clients = Path(root) / "clients"
    if clients.is_dir():
        for client in sorted(clients.iterdir(), key=lambda p: p.name.lower()):
            if not client.is_dir():
                continue
            for camp in list_client_campaign_dirs(root, client.name):
                if camp.name in seen:
                    continue
                seen.add(camp.name)
                out.append(camp)
    for camp in list_legacy_campaign_dirs(root):
        if camp.name in seen:
            continue
        # Skip empty junctions that already moved — still list if dir exists and not seen
        seen.add(camp.name)
        out.append(camp)
    return sorted(out, key=lambda p: p.name.lower())


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
    # Already under clients/{cid}/campaigns/{id}
    try:
        if campaign_dir.parent.name == "campaigns" and campaign_dir.parent.parent.name == cid:
            return campaign_dir
    except Exception:
        pass
    root = campaign_dir.parent.parent  # …/data/campaigns/id → …/data
    # If parent is campaigns under clients, root differs
    if (campaign_dir.parent.parent.parent.name == "clients"):
        return campaign_dir
    ensure_client(root, cid, str(meta.get("client_name") or cid))
    dest = client_campaign_dir(root, cid, campaign_dir.name)
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
    existing = (meta.get("icp_id") or "").strip()
    if existing and icp_dir(root, client_id, existing).is_dir():
        return existing

    preferred = (icp_id or "").strip()
    if not preferred:
        preferred = slug_id(
            str(meta.get("display_name") or meta.get("name") or campaign_dir.name),
            fallback=campaign_dir.name,
        )
    ensure_client(root, client_id, client_name)
    new_id = unique_id(client_dir(root, client_id) / "icps", preferred)
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
        campaign_type=str(meta.get("campaign_type") or "company_outreach"),
        client_name=client_name,
    )
    meta["icp_id"] = new_id
    _write_json(campaign_dir / "campaign.json", meta)
    sync_campaign_config_to_client(campaign_dir, client_id=client_id)
    return new_id


def migrate_campaign_under_client(root: Path, campaign_dir: Path) -> Path:
    """
    Move legacy data/campaigns/{id} → data/clients/{cid}/campaigns/{id}
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
    ensure_client(root, client_id, str(meta.get("client_name") or client_id))
    icp_id = extract_icp_from_campaign(root, campaign_dir)
    dest = client_campaign_dir(root, client_id, campaign_dir.name)
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
