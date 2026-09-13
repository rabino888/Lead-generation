#!/usr/bin/env python3
"""Migrate data/campaigns/* into data/clients/{client}/campaigns/* + icps/*."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.utils.client_store import data_root, migrate_all_legacy_campaigns  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Override DATA_ROOT (default: env or ./data)",
    )
    args = parser.parse_args()
    root = data_root(args.data_root)
    report = migrate_all_legacy_campaigns(root)
    print(json.dumps(report, indent=2))
    return 1 if report.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
