"""Backtest engine. Feeds Parquet → Layer 3 detection → backtest fill → metrics.

Deliberately does NOT use the production Layer 4 — production writes to the
main state.db and includes live semantics. Backtest Layer 4 is in-memory and
writes to a separate SQLite file.

Design: keep Layer 3 untouched. We call `find_opportunities` with the same
purity guarantees, then simulate fills with the chosen fill model, resolve
at market.resolution_date using the recorded snapshot's data.

A key honesty mechanism: the backtester uses per-snapshot capital-at-risk
computed under the chosen fill model, NOT the production detection's
capital_at_risk which used the real book directly. This closes the
"fill-model cheating" loophole where backtests report numbers based on
optimistic fills that live can't achieve.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from src.backtest.fill_model import FillModelConfig, fill_price
from src.backtest.metrics import BacktestMetrics, TradeRecord, compute_metrics
from src.layer2_data_serving.replay_stream import ReplayStream
from src.layer3_strategy.intra_market import StrategyContext, find_opportunities
from src.layer3_strategy.models import (
    Market,
    Opportunity,
    OrderBookLevel,
    OrderBookSide,
)


@dataclass
class BacktestResult:
    """Everything the report needs to render."""

    trades: List[TradeRecord] = field(default_factory=list)
    opportunities_detected: int = 0
    opportunities_above_threshold: int = 0
    snapshots_processed: int = 0
    fill_model: str = "realistic"
    config_hash: str = ""
    determinism_hash: str = ""

    @property
    def metrics(self) -> BacktestMetrics:
        return compute_metrics(self.trades)


def _consume_side(side: OrderBookSide, consumed: Decimal) -> OrderBookSide:
    """Return a new OrderBookSide with `consumed` contracts removed from the top.

    Walks levels from best onward, subtracting until `consumed` is exhausted.
    If more than the book has is consumed, returns an empty side.
    """
    if consumed <= 0:
        return side
    remaining_to_consume = consumed
    new_levels: list = []
    for lv in side.levels:
        if remaining_to_consume >= lv.size_contracts:
            remaining_to_consume -= lv.size_contracts
            continue
        if remaining_to_consume > 0:
            new_levels.append(
                OrderBookLevel(
                    price=lv.price,
                    size_contracts=lv.size_contracts - remaining_to_consume,
                )
            )
            remaining_to_consume = Decimal(0)
        else:
            new_levels.append(lv)
    return OrderBookSide(levels=new_levels)


def _market_minus_consumed(
    market: Market, yes_consumed: Decimal, no_consumed: Decimal
) -> Market:
    """Return a copy of the market with `consumed` contracts removed from each side.

    Used to honor the fact that two opportunities on the same market within
    one tick can't both consume the full book — the first fill reduces
    available depth for the second.
    """
    return market.model_copy(
        update={
            "yes_asks": _consume_side(market.yes_asks, yes_consumed),
            "no_asks": _consume_side(market.no_asks, no_consumed),
        }
    )


def _recompute_fill(
    market: Market,
    opp: Opportunity,
    fill_cfg: FillModelConfig,
) -> Optional[TradeRecord]:
    """Given an opportunity that production detection accepted, recompute what
    the backtest fill model would actually have paid. Returns None if the
    backtest fill makes the trade unprofitable.
    """
    yes_price, yes_filled = fill_price(market.yes_asks, opp.size_contracts, fill_cfg)
    no_price, no_filled = fill_price(market.no_asks, opp.size_contracts, fill_cfg)
    size = min(yes_filled, no_filled)
    if size <= 0:
        return None

    gross = size * (yes_price + no_price)
    fee = gross * Decimal(market.fee_bps) / Decimal(10000)
    gas = opp.gas_cost_usd  # same per-trade gas
    capital = gross + fee + gas
    payout = size * Decimal(1)
    profit = payout - capital
    if profit <= 0:
        return None

    return TradeRecord(
        opened_at=opp.detected_at,
        resolved_at=market.resolution_date,
        capital_locked_usd=capital,
        realized_pnl_usd=profit,
    )


async def run_backtest(
    *,
    snapshots_dir: str,
    platform: str,
    start: datetime,
    end: datetime,
    strategy_ctx: StrategyContext,
    fill_cfg: FillModelConfig,
) -> BacktestResult:
    """Run the full pipeline. Deterministic given inputs.

    Returns a BacktestResult whose `determinism_hash` can be compared across runs.
    """
    stream = ReplayStream(
        base_dir=snapshots_dir, platform=platform, start=start, end=end
    )

    result = BacktestResult(fill_model=fill_cfg.kind.value)
    hasher = hashlib.sha256()

    async for tick in stream.ticks():
        result.snapshots_processed += len(tick)
        markets_by_id = {m.market_id: m for m in tick}
        opps = find_opportunities(tick, strategy_ctx)
        result.opportunities_detected += len(opps)

        # Fix #4: track per-market consumed liquidity within this tick. Two
        # opportunities on the same market both get bookable size from the
        # SAME snapshot; the first "fill" reduces what's left for the second.
        # Deterministic iteration order (the sort key) keeps replay stable.
        opps_sorted = sorted(opps, key=lambda o: (-o.annualized_return, o.market_id))
        consumed: dict = {}  # market_id -> (yes_consumed, no_consumed)

        for opp in opps_sorted:
            market = markets_by_id.get(opp.market_id)
            if market is None:
                continue
            yes_c, no_c = consumed.get(opp.market_id, (Decimal(0), Decimal(0)))
            adj_market = _market_minus_consumed(market, yes_c, no_c)
            trade = _recompute_fill(adj_market, opp, fill_cfg)
            if trade is None:
                continue
            result.opportunities_above_threshold += 1
            result.trades.append(trade)
            # Charge this fill to the consumed budget.
            consumed[opp.market_id] = (
                yes_c + opp.size_contracts,
                no_c + opp.size_contracts,
            )
            # Canonical hash over (detected_at, market_id, size, realized_pnl).
            hasher.update(
                (
                    f"{opp.detected_at.isoformat()}|{opp.market_id}|"
                    f"{trade.capital_locked_usd}|{trade.realized_pnl_usd}"
                ).encode()
            )

    result.determinism_hash = hasher.hexdigest()
    return result


def format_report_markdown(result: BacktestResult) -> str:
    m = result.metrics
    lines = [
        "# Backtest Report",
        "",
        f"- Fill model: `{result.fill_model}`",
        f"- Snapshots processed: {result.snapshots_processed:,}",
        f"- Opportunities detected: {result.opportunities_detected:,}",
        f"- Opportunities that survived fill model: {result.opportunities_above_threshold:,}",
        f"- Determinism hash: `{result.determinism_hash}`",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Trades | {m.trades} |",
        f"| Total PnL (USD) | {m.total_pnl_usd} |",
        f"| Win rate | {m.win_rate:.2%} |",
        f"| Avg annualized (cap-weighted) | {m.avg_annualized_return:.2%} |",
        f"| Sharpe (per-trade, 365d-annualized) | {m.sharpe:.2f} |",
        f"| Max drawdown (USD) | {m.max_drawdown_usd} |",
        f"| PnL per $-day | {m.pnl_per_dollar_day:.6f} |",
        f"| Capital $-days | {m.capital_dollar_days:,.1f} |",
        "",
    ]
    return "\n".join(lines) + "\n"
