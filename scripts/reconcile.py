"""CLI entry point for paper-mode reconciliation.

Run on a schedule (cron / systemd timer) every 5 minutes:
    python scripts/reconcile.py --config config.yaml
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.risk.reconcile import reconcile_paper_state  # noqa: E402
from src.storage import state_db  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    args = p.parse_args()
    cfg = load_config(args.config)
    conn = state_db.connect(cfg.storage.state_db_path)
    state_db.init_schema(conn)

    report = reconcile_paper_state(conn)
    out = {
        "checked_positions": report.checked_positions,
        "findings": [
            {"severity": f.severity, "category": f.category, "detail": f.detail}
            for f in report.findings
        ],
        "mismatch_count": report.mismatch_count,
    }
    print(json.dumps(out, indent=2))
    sys.exit(0 if report.mismatch_count == 0 else 1)


if __name__ == "__main__":
    main()
