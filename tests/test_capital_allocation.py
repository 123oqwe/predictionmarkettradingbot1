"""Capital allocator tests. Every case from phase-0-mvp.md Task 0.5."""
from __future__ import annotations

from decimal import Decimal

from src.config import AllocationConfig
from src.layer3_strategy.allocation import allocate_capital
from src.layer3_strategy.intra_market import compute_opportunity
from tests.conftest import make_book, make_market


def _alloc_cfg(
    total: str = "1000",
    per_trade: str = "200",
    per_event: str = "300",
) -> AllocationConfig:
    return AllocationConfig(
        total_capital_usd=Decimal(total),
        max_capital_per_trade_usd=Decimal(per_trade),
        max_capital_per_event_usd=Decimal(per_event),
    )


def _make_opp_and_market(
    strategy_ctx,
    *,
    yes_price: str,
    no_price: str,
    depth: str,
    days: float,
    market_id: str,
    event_id: str,
):
    """Helper: produce (opportunity, market) pair for allocation tests."""
    m = make_market(
        yes_asks=make_book([(yes_price, depth)]),
        no_asks=make_book([(no_price, depth)]),
        days_to_resolution=days,
        market_id=market_id,
        event_id=event_id,
    )
    opp = compute_opportunity(m, strategy_ctx)
    assert opp is not None, f"test helper expected opportunity on {market_id}"
    return opp, m


class TestBasicAllocation:
    def test_empty_input(self, strategy_ctx):
        out = allocate_capital([], {}, strategy_ctx, _alloc_cfg())
        assert out == []

    def test_single_opportunity_full_take(self, strategy_ctx):
        opp, m = _make_opp_and_market(
            strategy_ctx,
            yes_price="0.45",
            no_price="0.48",
            depth="50",  # at max_trade_size=1000, liquidity caps it here
            days=30,
            market_id="mkt-1",
            event_id="evt-1",
        )
        allocations = allocate_capital(
            [opp], {"mkt-1": m}, strategy_ctx, _alloc_cfg()
        )
        assert len(allocations) == 1
        assert allocations[0].allocation_reason == "full"
        assert allocations[0].opportunity.opportunity_id == opp.opportunity_id

    def test_ranks_by_annualized(self, strategy_ctx):
        """With 2 opportunities and capital for only one, pick higher-annualized."""
        # Both small, only one can fit under per_event=300 total=350.
        high_opp, m_high = _make_opp_and_market(
            strategy_ctx,
            yes_price="0.40",  # 12% per-pair profit, very high annualized
            no_price="0.46",
            depth="500",
            days=30,
            market_id="mkt-high",
            event_id="evt-high",
        )
        low_opp, m_low = _make_opp_and_market(
            strategy_ctx,
            yes_price="0.47",  # 4% per-pair
            no_price="0.48",
            depth="500",
            days=30,
            market_id="mkt-low",
            event_id="evt-low",
        )
        # Total only covers one full trade.
        cfg = _alloc_cfg(total="200", per_trade="200", per_event="200")
        markets = {"mkt-high": m_high, "mkt-low": m_low}

        allocations = allocate_capital([low_opp, high_opp], markets, strategy_ctx, cfg)
        assert len(allocations) >= 1
        # First allocation must be the high-annualized one regardless of input order.
        assert allocations[0].opportunity.market_id == "mkt-high"


class TestCaps:
    def test_reserved_capital_reduces_pool(self, strategy_ctx):
        opp, m = _make_opp_and_market(
            strategy_ctx,
            yes_price="0.45",
            no_price="0.48",
            depth="50",
            days=30,
            market_id="mkt-1",
            event_id="evt-1",
        )
        # Reserve almost all capital; only $5 free → below min_trade_size, no allocation.
        cfg = _alloc_cfg(total="50", per_trade="50", per_event="50")
        allocations = allocate_capital(
            [opp], {"mkt-1": m}, strategy_ctx, cfg, reserved_capital_usd=Decimal("45")
        )
        # $5 remaining is below the ~$46 capital_at_risk for this opp → resize or skip.
        # We either resize to a passing smaller size or skip; in either case total
        # allocated must not exceed 5.
        total_alloc = sum(
            (a.allocated_capital_usd for a in allocations), Decimal(0)
        )
        assert total_alloc <= Decimal("5")

    def test_per_event_cap_prevents_concentration(self, strategy_ctx):
        """Two opportunities on same event_id should be capped by per_event."""
        opp1, m1 = _make_opp_and_market(
            strategy_ctx,
            yes_price="0.40",
            no_price="0.46",
            depth="500",
            days=30,
            market_id="mkt-1a",
            event_id="evt-shared",
        )
        opp2, m2 = _make_opp_and_market(
            strategy_ctx,
            yes_price="0.41",
            no_price="0.46",
            depth="500",
            days=30,
            market_id="mkt-1b",
            event_id="evt-shared",
        )
        cfg = _alloc_cfg(total="1000", per_trade="300", per_event="300")
        markets = {"mkt-1a": m1, "mkt-1b": m2}
        allocations = allocate_capital([opp1, opp2], markets, strategy_ctx, cfg)

        total_on_event = sum(
            (a.allocated_capital_usd for a in allocations if a.opportunity.event_id == "evt-shared"),
            Decimal(0),
        )
        assert total_on_event <= Decimal("300")


class TestResizeLogic:
    def test_resize_when_per_trade_cap_binds(self, strategy_ctx):
        """When per-trade cap < detected capital-at-risk, resize down and re-check math."""
        opp, m = _make_opp_and_market(
            strategy_ctx,
            yes_price="0.40",
            no_price="0.46",
            depth="10000",
            days=30,
            market_id="mkt-1",
            event_id="evt-1",
        )
        # Big detection, small per-trade cap forces resize.
        assert opp.capital_at_risk_usd > Decimal("100"), "test setup expects big opp"
        cfg = _alloc_cfg(total="1000", per_trade="50", per_event="200")
        allocations = allocate_capital([opp], {"mkt-1": m}, strategy_ctx, cfg)

        assert len(allocations) == 1
        a = allocations[0]
        # Resize should have shrunk it under the cap.
        assert a.allocated_capital_usd <= Decimal("50")
        assert a.allocation_reason in ("resized_by_trade_cap", "resized_by_event_cap")
        # The resized opportunity's size_contracts is smaller than the original.
        assert a.allocated_size_contracts < opp.size_contracts
        # The resized trade still must have passed the annualized gate — the
        # annualized_return on the Allocation's opportunity must meet threshold.
        assert a.opportunity.annualized_return >= strategy_ctx.config.min_annualized_return

    def test_resize_skipped_when_below_min_size(self, strategy_ctx):
        """If resize would produce size < min_trade_size_contracts, skip entirely."""
        opp, m = _make_opp_and_market(
            strategy_ctx,
            yes_price="0.45",
            no_price="0.48",
            depth="500",
            days=30,
            market_id="mkt-1",
            event_id="evt-1",
        )
        # Tiny per-trade cap ($2) cannot support any multi-contract trade.
        cfg = _alloc_cfg(total="100", per_trade="2", per_event="100")
        allocations = allocate_capital([opp], {"mkt-1": m}, strategy_ctx, cfg)
        # Either empty, or the opp was skipped because target_size dropped below min.
        total_alloc = sum((a.allocated_capital_usd for a in allocations), Decimal(0))
        assert total_alloc <= Decimal("2")
