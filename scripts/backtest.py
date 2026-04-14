"""Backtest CLI entry point.

Usage:
    python scripts/backtest.py --from 2026-04-14 --to 2026-04-15 \
        --fill-model realistic --output reports/backtest-2026-04.md
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.fill_model import FillModelConfig, FillModelKind  # noqa: E402
from src.backtest.runner import format_report_markdown, run_backtest  # noqa: E402
from src.config import load_config  # noqa: E402
from src.layer3_strategy.intra_market import StrategyContext  # noqa: E402
from src.provenance import build_bundle  # noqa: E402


def _parse(dt: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(dt, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise SystemExit(f"invalid date: {dt}")


async def main_async(args) -> None:
    cfg = load_config(args.config)
    provenance = build_bundle(cfg.raw)
    ctx = StrategyContext(
        config=cfg.intra_market,
        gas_cost_usd=cfg.polymarket.gas_estimate_usd,
        config_hash=provenance.config_hash,
        git_hash=provenance.git_commit,
    )
    fill_cfg = FillModelConfig(kind=FillModelKind(args.fill_model))
    result = await run_backtest(
        snapshots_dir=cfg.storage.snapshots_dir,
        platform=args.platform,
        start=_parse(args.start),
        end=_parse(args.end),
        strategy_ctx=ctx,
        fill_cfg=fill_cfg,
    )
    report = format_report_markdown(result)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report)
        print(f"wrote {out_path}")
    else:
        print(report)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    p.add_argument("--platform", default="polymarket")
    p.add_argument(
        "--fill-model",
        choices=[k.value for k in FillModelKind],
        default="realistic",
    )
    p.add_argument("--output", default=None, help="output path for markdown report")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
