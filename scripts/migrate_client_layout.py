#!/usr/bin/env python3
"""Migrate data/campaigns/* into data/clients/{client}/campaigns/* + icps/*."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.client_store import (  # noqa: E402
    data_root,
    migrate_all_legacy_campaigns,
    migrate_legacy_library_icps,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Override DATA_ROOT (default: env or ./data)",
    )
    parser.add_argument(
        "--icps-only",
        action="store_true",
        help="Only move legacy clients/*/icps into company_outreach/icps (or talent itps)",
    )
    args = parser.parse_args()
    root = data_root(args.data_root)
    if args.icps_only:
        report = migrate_legacy_library_icps(root)
    else:
        report = migrate_all_legacy_campaigns(root)
        report["library_icps"] = migrate_legacy_library_icps(root)
    print(json.dumps(report, indent=2))
    errors = report.get("errors") or []
    lib_errs = (report.get("library_icps") or {}).get("errors") or []
    return 1 if errors or lib_errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
