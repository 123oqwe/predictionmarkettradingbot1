"""Tax computation tests."""
from __future__ import annotations

from decimal import Decimal

from src.tax import compute_after_tax, default_us_nyc


def test_ordinary_income_on_polymarket_pnl():
    cfg = default_us_nyc()
    # $100 Polymarket profit, no Kalshi.
    r = compute_after_tax(
        polymarket_pnl_usd=Decimal("100"),
        kalshi_pnl_usd=Decimal("0"),
        period_days=30,
        cfg=cfg,
        capital_deployed_usd=Decimal("1000"),
    )
    # Ordinary marginal = 0.35 + 0.065 + 0.04 = 0.455
    assert r.estimated_tax_polymarket == Decimal("100") * Decimal("0.455")
    assert r.estimated_tax_kalshi == Decimal(0)
    assert r.net_pnl == Decimal("100") - Decimal("45.5")


def test_kalshi_60_40_split():
    cfg = default_us_nyc()
    r = compute_after_tax(
        polymarket_pnl_usd=Decimal("0"),
        kalshi_pnl_usd=Decimal("100"),
        period_days=30,
        cfg=cfg,
        capital_deployed_usd=Decimal("1000"),
    )
    # 60% long-term at 15%: 60 * 0.15 = 9
    # 40% short-term at ordinary 0.455: 40 * 0.455 = 18.2
    # Total: 27.2
    assert r.estimated_tax_kalshi == Decimal("27.2")


def test_excess_over_benchmark():
    cfg = default_us_nyc()
    # $100 gross PnL on $1000 deployed for 30 days.
    # Polymarket-only for simple math: net PnL = 100 * (1 - 0.455) = 54.5
    # net_pct = 0.0545, annualized = (1.0545)^(365/30) - 1 ≈ 0.932 (93.2%)
    # benchmark = 0.04 + 0.05 = 0.09
    r = compute_after_tax(
        polymarket_pnl_usd=Decimal("100"),
        kalshi_pnl_usd=Decimal("0"),
        period_days=30,
        cfg=cfg,
        capital_deployed_usd=Decimal("1000"),
    )
    assert r.annualized_net > Decimal("0.5")  # way above benchmark at this scale
    assert r.excess_over_benchmark == r.annualized_net - r.benchmark_rate


def test_losing_period_has_zero_tax():
    cfg = default_us_nyc()
    r = compute_after_tax(
        polymarket_pnl_usd=Decimal("-50"),
        kalshi_pnl_usd=Decimal("-20"),
        period_days=30,
        cfg=cfg,
        capital_deployed_usd=Decimal("1000"),
    )
    # No tax on losses in this simplified model.
    assert r.estimated_tax_polymarket == Decimal(0)
    assert r.estimated_tax_kalshi == Decimal(0)
    assert r.net_pnl == Decimal("-70")
