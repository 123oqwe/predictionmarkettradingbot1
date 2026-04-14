"""A/B backtest: run two configs on the same historical window, diff the results.

Usage:
    python scripts/ab_backtest.py \
        --from 2026-04-10 --to 2026-04-15 \
        --config-a configs/threshold_20.yaml \
        --config-b configs/threshold_25.yaml \
        [--platform polymarket] [--fill-model realistic]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.fill_model import FillModelConfig, FillModelKind  # noqa: E402
from src.backtest.runner import BacktestResult, run_backtest  # noqa: E402
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


def _diff_line(label: str, a, b, fmt: str = "{}") -> str:
    sa = fmt.format(a)
    sb = fmt.format(b)
    return f"| {label} | {sa} | {sb} |"


async def one_run(cfg_path: str, start, end, platform, fill_kind) -> BacktestResult:
    cfg = load_config(cfg_path)
    provenance = build_bundle(cfg.raw)
    ctx = StrategyContext(
        config=cfg.intra_market,
        gas_cost_usd=cfg.polymarket.gas_estimate_usd,
        config_hash=provenance.config_hash,
        git_hash=provenance.git_commit,
    )
    return await run_backtest(
        snapshots_dir=cfg.storage.snapshots_dir,
        platform=platform,
        start=start,
        end=end,
        strategy_ctx=ctx,
        fill_cfg=FillModelConfig(kind=FillModelKind(fill_kind)),
    )


async def main_async(args) -> None:
    start, end = _parse(args.start), _parse(args.end)
    a_task = one_run(args.config_a, start, end, args.platform, args.fill_model)
    b_task = one_run(args.config_b, start, end, args.platform, args.fill_model)
    ra, rb = await asyncio.gather(a_task, b_task)
    ma, mb = ra.metrics, rb.metrics
    print("# A/B Backtest Report")
    print("")
    print(f"- Window: {args.start} → {args.end}")
    print(f"- Platform: {args.platform}")
    print(f"- Fill model: {args.fill_model}")
    print(f"- Config A: {args.config_a}")
    print(f"- Config B: {args.config_b}")
    print("")
    print("| Metric | A | B |")
    print("|---|---|---|")
    print(_diff_line("Trades", ma.trades, mb.trades))
    print(_diff_line("Total PnL ($)", ma.total_pnl_usd, mb.total_pnl_usd))
    print(_diff_line("Win rate", ma.win_rate, mb.win_rate, "{:.2%}"))
    print(_diff_line("Avg annualized", ma.avg_annualized_return, mb.avg_annualized_return, "{:.2%}"))
    print(_diff_line("Sharpe", ma.sharpe, mb.sharpe, "{:.2f}"))
    print(_diff_line("Max drawdown ($)", ma.max_drawdown_usd, mb.max_drawdown_usd))
    print(_diff_line("PnL / $-day", ma.pnl_per_dollar_day, mb.pnl_per_dollar_day, "{:.6f}"))
    print("")
    print("**Reading rules:** the doc warns backtests lie in several ways "
          "(fill optimism, survivorship, cross-contamination). An A/B diff "
          "that favors B by ~10% in point estimates but matches within 1 "
          "Sharpe is NOT a reason to ship B. Wait for Phase 3 live calibration.")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    p.add_argument("--config-a", required=True)
    p.add_argument("--config-b", required=True)
    p.add_argument("--platform", default="polymarket")
    p.add_argument("--fill-model", default="realistic")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
