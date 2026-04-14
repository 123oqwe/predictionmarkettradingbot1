"""Backtest metrics: Sharpe, max drawdown, capital efficiency, win rate.

Design notes:
- Money math stays Decimal.
- Statistics (Sharpe, stddev) unavoidably float — they're ratios of sums of
  many small numbers, and Decimal gains nothing here. Doc permits this.
- Sharpe uses `periods_per_year=365`. Prediction markets trade 24x7, not 252
  equity-market days. Using 252 would under-annualize by ~21% — a
  quietly-wrong answer worse than being explicit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import mean, pstdev
from typing import Sequence

PERIODS_PER_YEAR = 365  # prediction markets run 24x7


@dataclass(frozen=True)
class TradeRecord:
    """One completed (resolved) trade used for metrics."""

    opened_at: datetime
    resolved_at: datetime
    capital_locked_usd: Decimal
    realized_pnl_usd: Decimal

    @property
    def hold_seconds(self) -> float:
        return max(1.0, (self.resolved_at - self.opened_at).total_seconds())

    @property
    def return_pct(self) -> float:
        if self.capital_locked_usd <= 0:
            return 0.0
        return float(self.realized_pnl_usd / self.capital_locked_usd)


def win_rate(trades: Sequence[TradeRecord]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.realized_pnl_usd > 0)
    return wins / len(trades)


def total_pnl_usd(trades: Sequence[TradeRecord]) -> Decimal:
    return sum((t.realized_pnl_usd for t in trades), Decimal(0))


def capital_dollar_days(trades: Sequence[TradeRecord]) -> float:
    """Sum of (capital_locked * days_held). Denominator for capital efficiency."""
    return sum(
        float(t.capital_locked_usd) * (t.hold_seconds / 86400.0) for t in trades
    )


def pnl_per_dollar_day(trades: Sequence[TradeRecord]) -> float:
    dd = capital_dollar_days(trades)
    if dd <= 0:
        return 0.0
    return float(total_pnl_usd(trades)) / dd


def max_drawdown_usd(trades: Sequence[TradeRecord]) -> Decimal:
    """Walk trades in resolution order; track running PnL, find max peak-to-trough."""
    if not trades:
        return Decimal(0)
    ordered = sorted(trades, key=lambda t: t.resolved_at)
    running = Decimal(0)
    peak = Decimal(0)
    max_dd = Decimal(0)
    for t in ordered:
        running += t.realized_pnl_usd
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd
    return max_dd


def sharpe_ratio(
    trades: Sequence[TradeRecord],
    risk_free_rate_annual: float = 0.04,
    periods_per_year: int = PERIODS_PER_YEAR,
) -> float:
    """Per-trade sharpe annualized to `periods_per_year`.

    Treats each trade's return_pct as one period's return. This is approximate —
    a strict Sharpe would bin by time. For Phase 2.5 where we want a single
    comparable number across backtests, per-trade is fine as long as both sides
    of an A/B comparison use the same definition.
    """
    if len(trades) < 2:
        return 0.0
    returns = [t.return_pct for t in trades]
    # Per-trade risk-free rate: annual rate spread across avg holding period.
    avg_hold_days = mean(t.hold_seconds / 86400.0 for t in trades) or 1.0
    rf_per_trade = risk_free_rate_annual * (avg_hold_days / 365.0)
    excess = [r - rf_per_trade for r in returns]
    mu = mean(excess)
    sigma = pstdev(excess)
    if sigma == 0:
        return 0.0
    trades_per_year = 365.0 / avg_hold_days if avg_hold_days > 0 else periods_per_year
    return (mu / sigma) * math.sqrt(trades_per_year)


def average_annualized_return(trades: Sequence[TradeRecord]) -> float:
    """Mean of per-trade annualized returns. Weighted by capital dollar-days."""
    if not trades:
        return 0.0
    total_cap_days = 0.0
    total_weighted = 0.0
    for t in trades:
        days = t.hold_seconds / 86400.0
        if days <= 0 or t.capital_locked_usd <= 0:
            continue
        ann = (1.0 + t.return_pct) ** (365.0 / days) - 1.0
        weight = float(t.capital_locked_usd) * days
        total_weighted += ann * weight
        total_cap_days += weight
    if total_cap_days == 0:
        return 0.0
    return total_weighted / total_cap_days


@dataclass(frozen=True)
class BacktestMetrics:
    trades: int
    total_pnl_usd: Decimal
    win_rate: float
    avg_annualized_return: float
    sharpe: float
    max_drawdown_usd: Decimal
    pnl_per_dollar_day: float
    capital_dollar_days: float

    def to_dict(self) -> dict:
        return {
            "trades": self.trades,
            "total_pnl_usd": str(self.total_pnl_usd),
            "win_rate": self.win_rate,
            "avg_annualized_return": self.avg_annualized_return,
            "sharpe": self.sharpe,
            "max_drawdown_usd": str(self.max_drawdown_usd),
            "pnl_per_dollar_day": self.pnl_per_dollar_day,
            "capital_dollar_days": self.capital_dollar_days,
        }


def compute_metrics(
    trades: Sequence[TradeRecord], risk_free_rate_annual: float = 0.04
) -> BacktestMetrics:
    return BacktestMetrics(
        trades=len(trades),
        total_pnl_usd=total_pnl_usd(trades),
        win_rate=win_rate(trades),
        avg_annualized_return=average_annualized_return(trades),
        sharpe=sharpe_ratio(trades, risk_free_rate_annual=risk_free_rate_annual),
        max_drawdown_usd=max_drawdown_usd(trades),
        pnl_per_dollar_day=pnl_per_dollar_day(trades),
        capital_dollar_days=capital_dollar_days(trades),
    )
