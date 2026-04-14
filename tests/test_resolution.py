"""Tests for resolution probes + strategy-aware PnL realization."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.layer3_strategy.models import PaperPosition
from src.layer4_execution.resolution import (
    Resolution,
    ResolutionOutcome,
    realize_pnl,
)


def _pos(
    *,
    yes_price: str = "0.45",
    no_price: str = "0.48",
    size: str = "100",
    capital: str = "95",
    expected: str = "5",
) -> PaperPosition:
    return PaperPosition(
        client_order_id="co_test",
        opportunity_id="opp_test",
        platform="polymarket",
        market_id="m1",
        event_id="e1",
        size_contracts=Decimal(size),
        yes_fill_price=Decimal(yes_price),
        no_fill_price=Decimal(no_price),
        capital_locked_usd=Decimal(capital),
        expected_profit_usd=Decimal(expected),
        opened_at=datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc),
        resolution_date=datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestIntraMarketRealize:
    def test_yes_outcome_yields_expected(self):
        pos = _pos()
        r = Resolution(ResolutionOutcome.YES, "test")
        assert realize_pnl(pos, "intra_market", r) == Decimal("5")

    def test_no_outcome_yields_expected(self):
        """Delta-neutral: either outcome pays $1/pair."""
        pos = _pos()
        r = Resolution(ResolutionOutcome.NO, "test")
        assert realize_pnl(pos, "intra_market", r) == Decimal("5")

    def test_void_yields_full_capital_loss(self):
        pos = _pos()
        r = Resolution(ResolutionOutcome.VOID, "test")
        assert realize_pnl(pos, "intra_market", r) == -Decimal("95")

    def test_unresolved_returns_none(self):
        pos = _pos()
        r = Resolution(ResolutionOutcome.UNRESOLVED, "probing")
        assert realize_pnl(pos, "intra_market", r) is None


class TestCrossMarketRealize:
    def test_both_agree_yields_expected(self):
        pos = _pos()
        yes = Resolution(ResolutionOutcome.YES, "a")
        also_yes = Resolution(ResolutionOutcome.YES, "b")
        assert realize_pnl(pos, "cross_market", yes, also_yes) == Decimal("5")

    def test_rule_divergence_yields_total_loss(self):
        pos = _pos()
        yes = Resolution(ResolutionOutcome.YES, "a")
        no = Resolution(ResolutionOutcome.NO, "b")
        assert realize_pnl(pos, "cross_market", yes, no) == -Decimal("95")

    def test_void_on_either_side_is_loss(self):
        pos = _pos()
        yes = Resolution(ResolutionOutcome.YES, "a")
        void = Resolution(ResolutionOutcome.VOID, "b")
        assert realize_pnl(pos, "cross_market", yes, void) == -Decimal("95")

    def test_missing_secondary_leaves_unresolved(self):
        pos = _pos()
        yes = Resolution(ResolutionOutcome.YES, "a")
        # No secondary → can't decide.
        assert realize_pnl(pos, "cross_market", yes, None) is None

    def test_secondary_unresolved_leaves_unresolved(self):
        pos = _pos()
        yes = Resolution(ResolutionOutcome.YES, "a")
        unk = Resolution(ResolutionOutcome.UNRESOLVED, "b")
        assert realize_pnl(pos, "cross_market", yes, unk) is None


class TestConvergenceRealize:
    def test_bought_yes_and_yes_won(self):
        """yes_fill_price > 0 means we bought YES."""
        pos = _pos(yes_price="0.97", no_price="0", expected="3", capital="97")
        yes = Resolution(ResolutionOutcome.YES, "test")
        assert realize_pnl(pos, "resolution_convergence", yes) == Decimal("3")

    def test_bought_yes_but_no_won(self):
        pos = _pos(yes_price="0.97", no_price="0", expected="3", capital="97")
        no = Resolution(ResolutionOutcome.NO, "test")
        assert realize_pnl(pos, "resolution_convergence", no) == -Decimal("97")

    def test_bought_no_and_no_won(self):
        """yes_fill_price == 0 means we bought NO."""
        pos = _pos(yes_price="0", no_price="0.97", expected="3", capital="97")
        no = Resolution(ResolutionOutcome.NO, "test")
        assert realize_pnl(pos, "resolution_convergence", no) == Decimal("3")

    def test_bought_no_but_yes_won(self):
        pos = _pos(yes_price="0", no_price="0.97", expected="3", capital="97")
        yes = Resolution(ResolutionOutcome.YES, "test")
        assert realize_pnl(pos, "resolution_convergence", yes) == -Decimal("97")

    def test_void_is_loss(self):
        pos = _pos(yes_price="0.97", no_price="0", expected="3", capital="97")
        void = Resolution(ResolutionOutcome.VOID, "test")
        assert realize_pnl(pos, "resolution_convergence", void) == -Decimal("97")


class TestUnknownStrategyFallback:
    def test_unknown_strategy_falls_back_to_intra_like(self):
        pos = _pos()
        r = Resolution(ResolutionOutcome.YES, "test")
        # Fallback: treat as delta-neutral intra_market.
        assert realize_pnl(pos, "some_future_strategy", r) == Decimal("5")
