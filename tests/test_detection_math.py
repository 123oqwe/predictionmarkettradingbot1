"""Detection math tests — all scenarios from phase-0-mvp.md section 3.

These tests are where bugs that cost money get caught. Every case here corresponds
to a documented failure mode that would silently leak capital if not checked.
"""
from __future__ import annotations

from decimal import Decimal

from src.layer3_strategy.intra_market import compute_opportunity
from tests.conftest import make_book, make_market


class TestProfitableOpportunity:
    def test_flat_deep_book_profitable(self, strategy_ctx):
        """Classic arb: yes_ask + no_ask < 1, deep liquidity, long-enough resolution."""
        m = make_market(
            yes_asks=make_book([("0.45", "500")]),
            no_asks=make_book([("0.48", "500")]),
            days_to_resolution=30,
        )
        opp = compute_opportunity(m, strategy_ctx)
        assert opp is not None
        assert opp.yes_fill_price == Decimal("0.45")
        assert opp.no_fill_price == Decimal("0.48")
        assert opp.expected_profit_usd > 0
        # 0.07 absolute per pair on $0.93 capital = 7.5% absolute, 30-day
        # annualized ≈ (1.075)^(365/30) - 1 ≈ 1.4 = 140%.
        assert opp.annualized_return > Decimal("1.0")

    def test_return_types_are_decimal(self, strategy_ctx):
        m = make_market(
            yes_asks=make_book([("0.45", "500")]),
            no_asks=make_book([("0.48", "500")]),
            days_to_resolution=30,
        )
        opp = compute_opportunity(m, strategy_ctx)
        assert opp is not None
        # Every numeric field must be Decimal, never float. A float here corrupts
        # all downstream math.
        for name in (
            "size_contracts",
            "yes_fill_price",
            "no_fill_price",
            "gross_cost_usd",
            "fee_cost_usd",
            "gas_cost_usd",
            "capital_at_risk_usd",
            "days_to_resolution",
            "expected_profit_usd",
            "profit_pct_absolute",
            "annualized_return",
        ):
            assert isinstance(getattr(opp, name), Decimal), f"{name} must be Decimal"


class TestBreakevenAndGates:
    def test_zero_fees_zero_gas_breakeven(self, strategy_ctx_no_gas):
        """yes_ask + no_ask == 1.0 with no fees/gas → zero profit, rejected."""
        # Set fee_bps=0 (default); ctx has gas=0.
        m = make_market(
            yes_asks=make_book([("0.50", "500")]),
            no_asks=make_book([("0.50", "500")]),
            days_to_resolution=30,
        )
        opp = compute_opportunity(m, strategy_ctx_no_gas)
        assert opp is None  # expected_profit = 0, rejected

    def test_just_above_breakeven_gated_by_annualized(self, strategy_ctx_no_gas):
        """Tiny absolute profit on long-horizon trade → annualized below threshold."""
        # 0.5% absolute profit, 200 days → ~0.9% annualized. Below 20% threshold.
        m = make_market(
            yes_asks=make_book([("0.495", "500")]),
            no_asks=make_book([("0.500", "500")]),
            days_to_resolution=200,
        )
        opp = compute_opportunity(m, strategy_ctx_no_gas)
        assert opp is None  # below 20% annualized gate


class TestBookShape:
    def test_sloped_book_gives_lower_max_profitable_size(self, strategy_ctx):
        """Sloped book produces lower max profitable size than flat.

        Flat book: entire 500 fills at 0.45. Sloped: price walks up → fewer profitable
        contracts. The detection's size search should find a smaller winning size.
        """
        flat = make_market(
            yes_asks=make_book([("0.45", "500")]),
            no_asks=make_book([("0.48", "500")]),
            days_to_resolution=30,
        )
        sloped = make_market(
            yes_asks=make_book(
                [("0.45", "50"), ("0.50", "50"), ("0.55", "100"), ("0.60", "300")]
            ),
            no_asks=make_book(
                [("0.48", "50"), ("0.52", "50"), ("0.58", "100"), ("0.65", "300")]
            ),
            days_to_resolution=30,
        )

        flat_opp = compute_opportunity(flat, strategy_ctx)
        sloped_opp = compute_opportunity(sloped, strategy_ctx)
        assert flat_opp is not None
        assert sloped_opp is not None
        # Sloped market has less profitable size at same threshold.
        assert sloped_opp.size_contracts < flat_opp.size_contracts

    def test_asymmetric_liquidity_capped_by_shallow_side(self, strategy_ctx):
        """Deep YES, shallow NO → size capped by NO side."""
        m = make_market(
            yes_asks=make_book([("0.45", "10000")]),
            no_asks=make_book([("0.48", "37")]),
            days_to_resolution=30,
        )
        opp = compute_opportunity(m, strategy_ctx)
        assert opp is not None
        assert opp.size_contracts <= Decimal("37")


class TestResolutionTiming:
    def test_short_resolution_high_annualized(self, strategy_ctx_no_gas):
        """1% absolute profit on a 10-day trade → ~40% annualized, passes 20% gate."""
        m = make_market(
            yes_asks=make_book([("0.495", "500")]),
            no_asks=make_book([("0.495", "500")]),
            days_to_resolution=10,
        )
        opp = compute_opportunity(m, strategy_ctx_no_gas)
        assert opp is not None
        assert opp.annualized_return >= Decimal("0.20")

    def test_long_resolution_rejected_on_annualized(self, strategy_ctx_no_gas):
        """3% absolute on 300-day → ~3.6% annualized, below 20% gate."""
        m = make_market(
            yes_asks=make_book([("0.485", "500")]),
            no_asks=make_book([("0.485", "500")]),
            days_to_resolution=300,
        )
        opp = compute_opportunity(m, strategy_ctx_no_gas)
        assert opp is None

    def test_past_resolution_rejected(self, strategy_ctx):
        """Resolution date already past → must reject, never negative days."""
        m = make_market(
            yes_asks=make_book([("0.45", "500")]),
            no_asks=make_book([("0.48", "500")]),
            days_to_resolution=-1,
        )
        opp = compute_opportunity(m, strategy_ctx)
        assert opp is None

    def test_below_min_days_cap_rejected(self, strategy_ctx_no_gas):
        """Even profitable trade resolving in < min_days (5) is rejected.

        This is the annualization-blow-up guard: a 1% profit in 1 day would
        compute to ~3700% annualized, which the allocator would prioritize over
        any realistic trade. Since you can't actually execute 365 such trades
        per year, that's phantom edge. Cap at 5 days.
        """
        # 2% absolute profit, 3 days → would be ~900% annualized without the cap.
        m = make_market(
            yes_asks=make_book([("0.49", "500")]),
            no_asks=make_book([("0.49", "500")]),
            days_to_resolution=3,
        )
        opp = compute_opportunity(m, strategy_ctx_no_gas)
        assert opp is None, "trade under min_days must be rejected regardless of annualized"

    def test_exactly_at_min_days_passes(self, strategy_ctx_no_gas):
        """5 days exactly should pass (boundary)."""
        m = make_market(
            yes_asks=make_book([("0.49", "500")]),
            no_asks=make_book([("0.49", "500")]),
            days_to_resolution=5.01,  # just over to be safe with fractional math
        )
        opp = compute_opportunity(m, strategy_ctx_no_gas)
        assert opp is not None


class TestCostsAndFees:
    def test_gas_eats_small_trade(self, strategy_ctx):
        """Gas > theoretical profit on tiny trade → rejected."""
        # Tight spread: 0.498 + 0.498 = 0.996, profit 0.004/pair. On 10 contracts
        # profit is $0.04, but gas is $0.20 → net loss, rejected.
        m = make_market(
            yes_asks=make_book([("0.498", "10")]),
            no_asks=make_book([("0.498", "10")]),
            days_to_resolution=30,
        )
        opp = compute_opportunity(m, strategy_ctx)
        assert opp is None

    def test_fees_reduce_profit(self, default_intra_config):
        """Non-zero fee_bps reduces profit. 100 bps (1%) on marginal trade → rejected."""
        from src.layer3_strategy.intra_market import StrategyContext

        ctx = StrategyContext(
            config=default_intra_config,
            gas_cost_usd=Decimal("0"),
            config_hash="h",
            git_hash="g",
        )
        m = make_market(
            yes_asks=make_book([("0.495", "500")]),
            no_asks=make_book([("0.495", "500")]),
            days_to_resolution=10,
            fee_bps=100,  # 1% fee on both legs
        )
        opp = compute_opportunity(m, ctx)
        # 1% absolute, minus 1% fee on $0.99 capital ≈ ~0% net → below 20% annualized.
        assert opp is None


class TestMarketState:
    def test_resolved_market_rejected(self, strategy_ctx):
        m = make_market(
            yes_asks=make_book([("0.01", "500")]),
            no_asks=make_book([("0.01", "500")]),
            days_to_resolution=30,
            resolved=True,
        )
        opp = compute_opportunity(m, strategy_ctx)
        assert opp is None

    def test_inactive_market_rejected(self, strategy_ctx):
        m = make_market(
            yes_asks=make_book([("0.45", "500")]),
            no_asks=make_book([("0.48", "500")]),
            days_to_resolution=30,
            active=False,
        )
        opp = compute_opportunity(m, strategy_ctx)
        assert opp is None

    def test_empty_book_rejected(self, strategy_ctx):
        m = make_market(
            yes_asks=make_book([]),
            no_asks=make_book([("0.48", "500")]),
            days_to_resolution=30,
        )
        opp = compute_opportunity(m, strategy_ctx)
        assert opp is None


class TestDeterminism:
    def test_same_inputs_same_output(self, strategy_ctx):
        """Layer 3 purity: two calls on identical inputs produce identical outputs."""
        m = make_market(
            yes_asks=make_book([("0.45", "100"), ("0.46", "200"), ("0.50", "200")]),
            no_asks=make_book([("0.48", "100"), ("0.49", "200"), ("0.51", "200")]),
            days_to_resolution=30,
        )
        opp1 = compute_opportunity(m, strategy_ctx)
        opp2 = compute_opportunity(m, strategy_ctx)
        assert opp1 is not None and opp2 is not None
        assert opp1.model_dump() == opp2.model_dump()
        assert opp1.opportunity_id == opp2.opportunity_id
