"""Manually reset a tripped kill switch.

The doc warns: write and test the reset script BEFORE any rules. This is that.

Usage:
    python scripts/kill_switch_reset.py --trigger daily_loss_exceeded --by alice
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.storage import state_db  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--trigger", required=True)
    p.add_argument("--by", required=True, help="who is performing the reset (audit trail)")
    args = p.parse_args()
    cfg = load_config(args.config)
    conn = state_db.connect(cfg.storage.state_db_path)
    state_db.init_schema(conn)
    iso = datetime.now(timezone.utc).isoformat()
    cleared = state_db.kill_switch_reset(conn, args.trigger, args.by, iso)
    if cleared:
        print(f"reset trigger={args.trigger} by={args.by} at={iso}")
        sys.exit(0)
    print(f"trigger={args.trigger} was not tripped (or unknown). Nothing to reset.")
    sys.exit(2)


if __name__ == "__main__":
    main()
