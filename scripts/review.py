"""Review queue CLI — operator disambiguates medium-confidence matches.

Reads pending candidates from a local JSON queue file (populated by the
discovery pipeline). Presents each pair, asks for a decision, writes the
result to `event_map.yaml` and a review log.

Commands per candidate:
  [a] approve as-is
  [m] approve conditionally (requires manual monitoring)
  [r] reject
  [s] skip for now
  [q] quit

This is a scaffold — the discovery pipeline itself (which populates the
queue) is a shell over prefilter + extractor + matcher. We ship it as a
script that an operator runs daily.

Usage:
    python scripts/review.py --queue reports/review_queue.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--queue", required=True, help="path to a JSON queue file")
    p.add_argument("--log", default="reports/review_log.jsonl")
    args = p.parse_args()

    queue_path = Path(args.queue)
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not queue_path.exists():
        print(f"no queue file at {queue_path}")
        return

    items = json.loads(queue_path.read_text())
    if not items:
        print("queue is empty")
        return

    remaining = list(items)
    decisions = []
    print(f"Pending: {len(remaining)} pair candidates\n")

    for i, item in enumerate(remaining, 1):
        print(f"\n[{i}/{len(remaining)}] {item.get('pair_id', '?')}  "
              f"confidence={item.get('confidence', 'n/a')}")
        print(f"  A ({item.get('platform_a', '?')}): {item.get('title_a', '')}")
        print(f"  B ({item.get('platform_b', '?')}): {item.get('title_b', '')}")
        diffs = item.get("differences", [])
        if diffs:
            print("  Differences:")
            for d in diffs:
                print(f"    - {d}")
        choice = input("  [a]pprove  [m]anual-monitor  [r]eject  [s]kip  [q]uit > ").strip().lower()
        if choice == "q":
            break
        decisions.append({
            "pair_id": item.get("pair_id"),
            "decision": choice,
            "decided_at": datetime.now(timezone.utc).isoformat(),
        })
        # Append to log immediately so a crash doesn't lose work.
        with log_path.open("a") as f:
            f.write(json.dumps(decisions[-1]) + "\n")

    print(f"\nlogged {len(decisions)} decisions to {log_path}")
    print("To apply approved pairs: manually copy them into event_map.yaml "
          "with trading_enabled: false, review edge cases, then flip to true.")


if __name__ == "__main__":
    main()
