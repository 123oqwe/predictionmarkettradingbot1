"""Backtest metrics tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.backtest.metrics import (
    PERIODS_PER_YEAR,
    TradeRecord,
    average_annualized_return,
    compute_metrics,
    max_drawdown_usd,
    sharpe_ratio,
    total_pnl_usd,
    win_rate,
)


def _tr(
    profit: str,
    capital: str = "100",
    hold_days: float = 30.0,
    opened: datetime = None,
) -> TradeRecord:
    if opened is None:
        opened = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
    return TradeRecord(
        opened_at=opened,
        resolved_at=opened + timedelta(seconds=int(hold_days * 86400)),
        capital_locked_usd=Decimal(capital),
        realized_pnl_usd=Decimal(profit),
    )


class TestBasic:
    def test_win_rate_empty(self):
        assert win_rate([]) == 0.0

    def test_win_rate(self):
        trades = [_tr("10"), _tr("-5"), _tr("20"), _tr("-3")]
        assert win_rate(trades) == 0.5

    def test_total_pnl(self):
        trades = [_tr("10"), _tr("-3"), _tr("5")]
        assert total_pnl_usd(trades) == Decimal("12")


class TestMaxDrawdown:
    def test_no_drawdown_monotonic_up(self):
        # PnL: +10, +10, +10 (cumulative 10, 20, 30). No drawdown.
        trades = [
            _tr("10", opened=datetime(2026, 4, 1, tzinfo=timezone.utc), hold_days=1),
            _tr("10", opened=datetime(2026, 4, 2, tzinfo=timezone.utc), hold_days=1),
            _tr("10", opened=datetime(2026, 4, 3, tzinfo=timezone.utc), hold_days=1),
        ]
        assert max_drawdown_usd(trades) == Decimal(0)

    def test_peak_to_trough(self):
        # PnL: +10, +10, -15, +5 → cumulative 10, 20, 5, 10. Peak 20, trough 5 → DD = 15.
        trades = [
            _tr("10", opened=datetime(2026, 4, 1, tzinfo=timezone.utc), hold_days=1),
            _tr("10", opened=datetime(2026, 4, 2, tzinfo=timezone.utc), hold_days=1),
            _tr("-15", opened=datetime(2026, 4, 3, tzinfo=timezone.utc), hold_days=1),
            _tr("5", opened=datetime(2026, 4, 4, tzinfo=timezone.utc), hold_days=1),
        ]
        assert max_drawdown_usd(trades) == Decimal(15)


class TestAnnualizedReturn:
    def test_one_percent_on_thirty_day_trade(self):
        # 1% in 30 days → (1.01)^(365/30) - 1 ≈ 12.9% annualized.
        tr = _tr("1.00", "100", 30)
        ann = average_annualized_return([tr])
        assert 0.12 < ann < 0.14

    def test_weights_by_capital_days(self):
        # A tiny short-duration trade has absurd annualized return in isolation
        # ((1.01)^365 ≈ 37x). If we averaged unweighted, it would dominate a
        # long-duration trade on much bigger capital. Weighting by
        # capital × hold_time makes the long-duration trade carry most of the
        # weight, which reflects actual capital deployed.
        #
        # short: 1% profit on $10 capital in 1 day → ~3700% annualized, weight 10
        # long:  0.5% profit on $1000 capital in 100 days → ~1.8% annualized, weight 100,000
        # Weighted avg should be dominated by the long trade even though the
        # short trade has the higher raw annualized.
        short = _tr("0.10", "10", 1)            # 1% in 1 day
        long_trade = _tr("5.00", "1000", 100)   # 0.5% in 100 days
        weighted = average_annualized_return([short, long_trade])
        # Long-trade-dominated weighted average should be much closer to 1.8%
        # than to 3700%. A generous upper bound of 10% confirms the weighting
        # is pulling it down from the short-trade's raw value.
        assert weighted < 0.10, f"weighted avg {weighted} not dominated by long trade"


class TestSharpe:
    def test_empty_returns_zero(self):
        assert sharpe_ratio([]) == 0.0

    def test_positive_sharpe(self):
        # All winners with low variance → positive Sharpe.
        trades = [_tr("5.00", "100", 30) for _ in range(5)]
        # Vary opening dates to avoid identical timestamps (stdev would be 0).
        trades = [
            TradeRecord(
                opened_at=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(days=i),
                resolved_at=datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(days=i),
                capital_locked_usd=Decimal("100"),
                realized_pnl_usd=Decimal("5.00") + Decimal(f"0.{i}"),
            )
            for i in range(5)
        ]
        s = sharpe_ratio(trades)
        assert s > 0

    def test_zero_variance_returns_zero(self):
        # All identical returns → zero sigma → zero sharpe (avoid div-by-zero).
        trades = [
            TradeRecord(
                opened_at=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(days=i),
                resolved_at=datetime(2026, 4, 1, tzinfo=timezone.utc) + timedelta(days=i+30),
                capital_locked_usd=Decimal("100"),
                realized_pnl_usd=Decimal("5.00"),
            )
            for i in range(5)
        ]
        s = sharpe_ratio(trades)
        assert s == 0.0


class TestComputeMetrics:
    def test_roundtrip(self):
        trades = [_tr("5.00", "100", 30), _tr("-2.00", "50", 15)]
        m = compute_metrics(trades)
        assert m.trades == 2
        assert m.total_pnl_usd == Decimal("3.00")
        assert 0 < m.win_rate < 1
        # to_dict round-trip
        d = m.to_dict()
        assert d["trades"] == 2
        assert d["total_pnl_usd"] == "3.00"


def test_periods_per_year_is_365_for_247_markets():
    """Prediction markets trade 24x7, so annualization uses 365 not 252.

    This is the single most common quiet bug in crypto / prediction market
    backtests copy-pasted from equity code. Assert the constant explicitly.
    """
    assert PERIODS_PER_YEAR == 365
