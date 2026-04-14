"""Replay a historical date range deterministically.

Feeds Parquet → Layer 2 replay stream → Layer 3 detection → prints opportunity
output + a determinism hash. Running this twice on the same window must produce
byte-identical output. Any divergence means Layer 3 has hidden state.

Usage:
    python scripts/replay.py --from '2026-04-14' --to '2026-04-15'
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Put repo root on path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.layer2_data_serving.replay_stream import ReplayStream  # noqa: E402
from src.layer3_strategy.intra_market import StrategyContext, find_opportunities  # noqa: E402
from src.provenance import build_bundle  # noqa: E402


def _parse(dt: str) -> datetime:
    # Accept YYYY-MM-DD or YYYY-MM-DD HH:MM.
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(dt, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise SystemExit(f"invalid date: {dt}")


async def main_async(config_path: str, start: datetime, end: datetime) -> None:
    cfg = load_config(config_path)
    provenance = build_bundle(cfg.raw)

    ctx = StrategyContext(
        config=cfg.intra_market,
        gas_cost_usd=cfg.polymarket.gas_estimate_usd,
        config_hash=provenance.config_hash,
        git_hash=provenance.git_commit,
    )

    stream = ReplayStream(
        base_dir=cfg.storage.snapshots_dir,
        platform="polymarket",
        start=start,
        end=end,
    )

    total_snapshots = 0
    total_opportunities = 0
    hasher = hashlib.sha256()

    async for tick in stream.ticks():
        total_snapshots += len(tick)
        opps = find_opportunities(tick, ctx)
        total_opportunities += len(opps)
        for opp in opps:
            # Canonical JSON per opportunity → bytes into hash.
            payload = json.dumps(
                opp.model_dump(mode="json"), sort_keys=True, default=str
            )
            hasher.update(payload.encode())

    print(f"Replayed {total_snapshots:,} market snapshots")
    print(f"Detected {total_opportunities:,} opportunities")
    print(f"Determinism hash: {hasher.hexdigest()}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    args = p.parse_args()
    start, end = _parse(args.start), _parse(args.end)
    asyncio.run(main_async(args.config, start, end))


if __name__ == "__main__":
    main()
