"""Call outreach-content-agent through its CLI. This repo does not import it.

The sequence is drafted locally. ``deliver`` is never invoked, and ``generate``
always runs with ``--dry-run``, so nothing is written to a sending sheet.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
DRY_RUN_SHEET = "https://example.com/autopilot-dry-run"

_QA_PASS = re.compile(r"qa pass\s*:\s*(\d+)", re.I)
_NEEDS = re.compile(r"needs_review\s*:\s*(\d+)", re.I)
_FAILED = re.compile(r"^\s*failed\s*:\s*(\d+)", re.I | re.M)
_VERIFY_PASSED = re.compile(r"Passed:\s*(\d+)")
_VERIFY_REVIEW = re.compile(r"Needs review:\s*(\d+)")
_COST = re.compile(r"total=\$([0-9]*\.?[0-9]+)")


CommandRunner = Callable[[list[str], Path], dict[str, Any]]


def find_outreach_repo() -> Path | None:
    raw = (os.environ.get("OUTREACH_REPO") or "").strip()
    candidates = []
    if raw:
        candidates.append(Path(raw))
    candidates.append(REPO_ROOT.parent / "outreach-content-agent")
    for path in candidates:
        if (path / "scripts" / "cli.py").is_file():
            return path
    return None


def _strict_offer(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(doc.get("id") or 1),
        "title": str(doc.get("title") or "Offer"),
        "description": str(doc.get("description") or ""),
        "deliverability_note": str(doc.get("deliverability_note") or "Confirm before sending."),
    }


def sync_library(leadgen_root: Path, outreach_root: Path, plan: dict[str, Any]) -> Path:
    """Copy the client library into the outreach repo's data/clients tree.

    Outreach resolves ``icps/{id}.json`` as a file. This repo stores
    ``icps/{id}/icp.json``. The copy writes the file shape outreach already reads.
    """
    client_id = plan["client_id"]
    campaign_id = plan["campaign_id"]
    icp_id = f"{client_id}_buyers"
    src = Path(leadgen_root) / "clients" / client_id
    dest = Path(outreach_root) / "data" / "clients" / client_id
    dest.mkdir(parents=True, exist_ok=True)
    brief = src / "company_brief.md"
    if brief.is_file():
        text = brief.read_text(encoding="utf-8")
    else:
        text = f"# Company brief — {client_id}\n"
    constraints = [str(item).strip() for item in (plan.get("outreach_constraints") or []) if str(item).strip()]
    if constraints:
        text = text.rstrip() + "\n\n## Autopilot constraints\n\n" + "\n".join(f"- {item}" for item in constraints) + "\n"
    (dest / "company_brief.md").write_text(text, encoding="utf-8")

    offer_path = src / "offers" / "1.json"
    offer = json.loads(offer_path.read_text(encoding="utf-8")) if offer_path.is_file() else (plan.get("offer") or {})
    (dest / "offers").mkdir(exist_ok=True)
    (dest / "offers" / "1.json").write_text(
        json.dumps(_strict_offer(offer), indent=2) + "\n", encoding="utf-8"
    )
    magnet_path = src / "lead_magnets" / "1.json"
    magnet = json.loads(magnet_path.read_text(encoding="utf-8")) if magnet_path.is_file() else {}
    if magnet:
        (dest / "lead_magnets").mkdir(exist_ok=True)
        (dest / "lead_magnets" / "1.json").write_text(
            json.dumps(_strict_offer(magnet), indent=2) + "\n", encoding="utf-8"
        )
    icp_src = src / "icps" / icp_id / "icp.json"
    icp = json.loads(icp_src.read_text(encoding="utf-8")) if icp_src.is_file() else dict(plan.get("icp") or {})
    (dest / "icps").mkdir(exist_ok=True)
    (dest / "icps" / f"{icp_id}.json").write_text(json.dumps(icp, indent=2) + "\n", encoding="utf-8")

    campaign = dest / "campaigns" / campaign_id
    campaign.mkdir(parents=True, exist_ok=True)
    pointer = {"offer_id": 1, "lead_magnet_id": 1, "icp_id": icp_id}
    payload = campaign / "run_payload.json"
    payload.write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")
    _write_constraints(campaign, constraints)
    return payload


def _write_constraints(campaign_dir: Path, constraints: list[str]) -> None:
    if not constraints:
        return
    block = "\n".join(f"- {item}" for item in constraints)
    section = f"\n\n## Autopilot constraints\n\n{block}\n"
    written = False
    examples = campaign_dir / "examples"
    if examples.is_dir():
        for path in examples.rglob("instructions.md"):
            text = path.read_text(encoding="utf-8")
            if "## Autopilot constraints" in text:
                continue
            path.write_text(text.rstrip() + section, encoding="utf-8")
            written = True
        spec = campaign_dir / "sequence_spec.md"
        if spec.is_file() and "## Autopilot constraints" not in spec.read_text(encoding="utf-8"):
            spec.write_text(spec.read_text(encoding="utf-8").rstrip() + section, encoding="utf-8")
            written = True
    if not written:
        examples.mkdir(parents=True, exist_ok=True)
        (examples / "autopilot_constraints.md").write_text(section.lstrip(), encoding="utf-8")


def spin_ready(outreach_root: Path, client_id: str, campaign_id: str) -> bool:
    spin = outreach_root / "data" / "clients" / client_id / "campaigns" / campaign_id / "spin"
    return spin.is_dir() and any(spin.glob("*.spin.md"))


def build_commands(
    *,
    client_id: str,
    campaign_id: str,
    csv_path: str,
    payload_path: str,
    outreach_root: Path,
    fresh: bool = False,
    retry: bool = False,
) -> list[list[str]]:
    """Argv lists for ``scripts/cli.py``. ``deliver`` is intentionally absent."""
    cli = str(outreach_root / "scripts" / "cli.py")
    base = [sys.executable, cli]
    if spin_ready(outreach_root, client_id, campaign_id):
        commands = [
            base + ["render", "--client", client_id, "--campaign", campaign_id, "--csv", csv_path],
            base + ["verify", "--client", client_id, "--campaign", campaign_id, "--csv", csv_path],
        ]
        return commands
    commands: list[list[str]] = []
    if not retry:
        commands.append(base + ["sequence", "init", "--client", client_id, "--campaign", campaign_id])
        commands.append(
            base
            + [
                "kit",
                "propose",
                "--client",
                client_id,
                "--campaign",
                campaign_id,
                "--csv",
                csv_path,
                "--payload",
                payload_path,
            ]
        )
        commands.append(base + ["kit", "approve", "--client", client_id, "--campaign", campaign_id])
    else:
        commands.append(base + ["sequence", "sync", "--client", client_id, "--campaign", campaign_id])
    generate = [
        *base,
        "generate",
        "--client",
        client_id,
        "--campaign",
        campaign_id,
        "--csv",
        csv_path,
        "--sheet-url",
        DRY_RUN_SHEET,
        "--dry-run",
    ]
    if fresh:
        generate.append("--fresh")
    commands.append(generate)
    return commands


def parse_outreach_output(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    text = f"{stdout}\n{stderr}"
    qa_pass = _last_int(_QA_PASS, text)
    if qa_pass == 0:
        qa_pass = _last_int(_VERIFY_PASSED, text)
    needs = _last_int(_NEEDS, text)
    if "Needs review:" in text and _VERIFY_REVIEW.search(text):
        needs = max(needs, _last_int(_VERIFY_REVIEW, text))
    failed = _last_int(_FAILED, text)
    usd = 0.0
    for match in _COST.finditer(text):
        usd += float(match.group(1))
    reasons = []
    if returncode != 0:
        tail = (stderr or stdout or "").strip().splitlines()
        reasons.append(tail[-1][:500] if tail else f"exit {returncode}")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and ("gate" in stripped.lower() or "needs_review" in stripped.lower()):
            reasons.append(stripped[2:][:300])
    ok = returncode == 0 and needs == 0 and failed == 0 and qa_pass > 0
    return {
        "ok": ok,
        "qa_pass": qa_pass,
        "needs_review": needs,
        "failed": failed,
        "usd": round(usd, 4),
        "reasons": reasons,
        "summary": f"qa_pass={qa_pass} needs_review={needs} failed={failed} exit={returncode}",
    }


def _last_int(pattern: re.Pattern[str], text: str) -> int:
    found = list(pattern.finditer(text))
    if not found:
        return 0
    return int(found[-1].group(1))


def default_command_runner(argv: list[str], cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
    }


def run_outreach(
    *,
    data_root: Path,
    plan: dict[str, Any],
    csv_path: str,
    command_runner: CommandRunner | None = None,
    retry: bool = False,
) -> dict[str, Any]:
    repo = find_outreach_repo()
    if repo is None:
        return {
            "ok": False,
            "qa_pass": 0,
            "needs_review": 0,
            "failed": 0,
            "usd": 0.0,
            "reasons": [
                "outreach repo not found. Set OUTREACH_REPO to a checkout of outreach-content-agent."
            ],
            "summary": "outreach repo not found",
            "commands": [],
        }
    payload = sync_library(data_root, repo, plan)
    commands = build_commands(
        client_id=plan["client_id"],
        campaign_id=plan["campaign_id"],
        csv_path=csv_path,
        payload_path=str(payload),
        outreach_root=repo,
        fresh=bool(plan.get("outreach_fresh") or retry),
        retry=retry,
    )
    runner = command_runner or default_command_runner
    combined_out = []
    combined_err = []
    last_code = 0
    ran: list[list[str]] = []
    for argv in commands:
        if "deliver" in argv:
            raise RuntimeError("autopilot must not call outreach deliver")
        ran.append(argv)
        result = runner(argv, repo)
        last_code = int(result.get("returncode") or 0)
        combined_out.append(result.get("stdout") or "")
        combined_err.append(result.get("stderr") or "")
        # sequence init exits in error when the folder already exists. Keep going.
        if last_code != 0 and "sequence" in argv and "init" in argv:
            last_code = 0
            continue
        if last_code != 0:
            break
    parsed = parse_outreach_output("\n".join(combined_out), "\n".join(combined_err), last_code)
    parsed["commands"] = ran
    parsed["stdout"] = "\n".join(combined_out)[-8000:]
    return parsed


def handoff_csv(campaign_dir: Path) -> Path | None:
    stages = campaign_dir / "stages"
    for name in ("05_enriched.csv", "06_scored.csv", "04_apollo_contactable.csv", "03_deduped.csv"):
        path = stages / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None
