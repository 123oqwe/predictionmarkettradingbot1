"""Historical opportunity census.

Answers: "what did the opportunity landscape actually look like?"
Diagnostic, not trading. No fill decisions — just descriptive statistics.

Key checks the report should surface:
  - A bucket at >100% annualized is almost always a bug. Investigate by hand.
  - Heavy skew to short days_to_resolution means capacity constraints.
  - Median opportunity lifetime < 10s means HFT competition.

Usage:
    python scripts/census.py --from 2026-04-01 --to 2026-05-01 \
        [--output reports/census-april.md]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.layer2_data_serving.replay_stream import ReplayStream  # noqa: E402
from src.layer3_strategy.intra_market import (  # noqa: E402
    StrategyContext,
    find_opportunities,
)
from src.provenance import build_bundle  # noqa: E402

_BUCKETS = [
    (Decimal("0"), Decimal("0.10"), "0-10%"),
    (Decimal("0.10"), Decimal("0.20"), "10-20%"),
    (Decimal("0.20"), Decimal("0.30"), "20-30%"),
    (Decimal("0.30"), Decimal("0.50"), "30-50%"),
    (Decimal("0.50"), Decimal("1.00"), "50-100%"),
    (Decimal("1.00"), Decimal("5.00"), "100-500%"),
    (Decimal("5.00"), Decimal("10000"), ">500%"),
]

_DAY_BUCKETS = [
    (0, 5, "<5 days"),
    (5, 30, "5-30 days"),
    (30, 90, "30-90 days"),
    (90, 10000, "90+ days"),
]


def _bar(n: int) -> str:
    return "█" * min(30, n // max(1, n // 30 if n > 30 else 1))


def _parse(dt: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(dt, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise SystemExit(f"invalid date: {dt}")


def _bucket_ann(ann: Decimal) -> str:
    for lo, hi, label in _BUCKETS:
        if lo <= ann < hi:
            return label
    return ">500%"


def _bucket_days(days: Decimal) -> str:
    d = float(days)
    for lo, hi, label in _DAY_BUCKETS:
        if lo <= d < hi:
            return label
    return "90+ days"


async def main_async(args) -> None:
    cfg = load_config(args.config)
    provenance = build_bundle(cfg.raw)
    ctx = StrategyContext(
        config=cfg.intra_market,
        gas_cost_usd=cfg.polymarket.gas_estimate_usd,
        config_hash=provenance.config_hash,
        git_hash=provenance.git_commit,
    )
    start, end = _parse(args.start), _parse(args.end)

    stream = ReplayStream(
        base_dir=cfg.storage.snapshots_dir,
        platform=args.platform,
        start=start,
        end=end,
    )

    total_snapshots = 0
    total_opportunities = 0
    ann_buckets: dict = defaultdict(int)
    day_buckets_above_20: dict = defaultdict(int)
    markets_seen: set = set()
    suspicious = 0  # > 500% annualized — doc says almost always a bug

    # Approximate opportunity lifetime tracking: first-seen → last-seen timestamp
    # per (strategy, market_id). Lifetime is last - first.
    first_seen: dict = {}
    last_seen: dict = {}

    async for tick in stream.ticks():
        total_snapshots += len(tick)
        for m in tick:
            markets_seen.add(m.market_id)
        opps = find_opportunities(tick, ctx)
        total_opportunities += len(opps)
        for o in opps:
            ann_buckets[_bucket_ann(o.annualized_return)] += 1
            if o.annualized_return > Decimal(5):
                suspicious += 1
            if o.annualized_return >= Decimal("0.20"):
                day_buckets_above_20[_bucket_days(o.days_to_resolution)] += 1
            key = (o.strategy, o.market_id)
            if key not in first_seen:
                first_seen[key] = o.detected_at
            last_seen[key] = o.detected_at

    # Lifetimes in seconds.
    lifetimes = [
        (last_seen[k] - first_seen[k]).total_seconds() for k in first_seen
    ]
    median_lifetime = 0
    if lifetimes:
        srt = sorted(lifetimes)
        median_lifetime = srt[len(srt) // 2]

    lines = [
        f"# Opportunity Census: {args.start} to {args.end}",
        "",
        f"Total snapshots processed: {total_snapshots:,}",
        f"Unique markets observed: {len(markets_seen):,}",
        f"Opportunities detected (any ann_return): {total_opportunities:,}",
        "",
        "## Distribution by annualized return",
        "",
    ]
    for _, _, label in _BUCKETS:
        n = ann_buckets.get(label, 0)
        lines.append(f"  {label:>10}: {_bar(n)} {n}")
    if suspicious:
        lines.append(
            f"\n⚠ **Suspicious: {suspicious} opportunities above 500% annualized.** "
            f"The doc warns these are almost always bugs (wrong resolution dates, "
            f"fee underestimates, optimistic fill model). Investigate by hand."
        )

    lines.extend([
        "",
        "## Distribution by days-to-resolution (above 20% annualized)",
        "",
    ])
    for _, _, label in _DAY_BUCKETS:
        n = day_buckets_above_20.get(label, 0)
        lines.append(f"  {label:>12}: {_bar(n)} {n}")
    if day_buckets_above_20.get("<5 days", 0) > 0:
        lines.append(
            "\n⚠ **Short-duration trades detected above threshold.** "
            "Our min_days_to_resolution config should already gate these in "
            "production, but the census may include older data written under "
            "a different config. Double-check."
        )

    lines.extend([
        "",
        "## Opportunity lifetimes",
        "",
        f"Median observed lifetime: {median_lifetime:.1f}s "
        f"(resolution = poll_interval_seconds ≈ {cfg.polymarket.poll_interval_seconds}s)",
        "",
    ])
    if median_lifetime < 10 and lifetimes:
        lines.append(
            "⚠ **Short median lifetime implies HFT competition.** Live fill rate "
            "will be materially worse than backtest. Plan Phase 3 capital accordingly."
        )

    report = "\n".join(lines) + "\n"

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report)
        print(f"wrote {out}")
    else:
        print(report)

    if args.json:
        data = {
            "total_snapshots": total_snapshots,
            "total_opportunities": total_opportunities,
            "unique_markets": len(markets_seen),
            "ann_buckets": ann_buckets,
            "day_buckets_above_20": day_buckets_above_20,
            "suspicious_above_500pct": suspicious,
            "median_lifetime_seconds": median_lifetime,
        }
        Path(args.json).write_text(json.dumps(data, indent=2, default=str))
        print(f"wrote {args.json}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    p.add_argument("--platform", default="polymarket")
    p.add_argument("--output", default=None)
    p.add_argument("--json", default=None, help="also write raw counts as JSON for pipelining")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
