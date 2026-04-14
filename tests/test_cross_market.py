"""Cross-market detection tests."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.layer3_strategy.cross_market import (
    CrossMarketContext,
    detect_cross_pair,
    find_cross_opportunities,
)
from src.matching.event_map import EdgeCase, Pair
from tests.conftest import make_book, make_market


def _pair(
    *,
    pair_id: str = "p1",
    poly_id: str = "poly-1",
    kal_id: str = "KAL-1",
    enabled: bool = True,
    override: str | None = None,
) -> Pair:
    return Pair(
        pair_id=pair_id,
        polymarket_market_id=poly_id,
        kalshi_market_ticker=kal_id,
        verified_by="t",
        verified_date=date(2026, 4, 1),
        trading_enabled=enabled,
        edge_cases_reviewed=[
            EdgeCase("c1", "YES", "YES", False),
            EdgeCase("c2", "NO", "NO", False),
            EdgeCase("c3", "YES", "YES", False),
            EdgeCase("c4", "YES", "YES", False),
            EdgeCase("c5", "ambiguous", "NO", True, "doc"),
        ],
        topic_tags=["test"],
        min_annualized_return_override=Decimal(override) if override else None,
    )


def _ctx(strategy_ctx) -> CrossMarketContext:
    return CrossMarketContext(
        intra=strategy_ctx,
        cross_min_annualized_return=Decimal("0.28"),
        polymarket_gas_usd=Decimal("0.20"),
        kalshi_gas_usd=Decimal("0"),
        config_hash="cross_test_hash",
        git_hash="cross_test_git",
    )


class TestSingleDirection:
    def test_detects_basic_cross_arbitrage(self, strategy_ctx, default_intra_config):
        # Polymarket YES at 0.40, Kalshi NO at 0.50 → 0.90 cost ($0.10 profit/pair)
        poly = make_market(
            yes_asks=make_book([("0.40", "500")]),
            no_asks=make_book([("0.55", "500")]),
            days_to_resolution=30,
            market_id="poly-1",
        )
        kal = make_market(
            yes_asks=make_book([("0.55", "500")]),
            no_asks=make_book([("0.50", "500")]),
            days_to_resolution=30,
            market_id="KAL-1",
        )
        # Override platform name on kalshi market — make_market hardcodes 'polymarket'.
        kal_kalshi = kal.model_copy(update={"platform": "kalshi"})

        opps = detect_cross_pair(
            _pair(),
            poly,
            kal_kalshi,
            _ctx(strategy_ctx),
            default_intra_config,
            Decimal("0.28"),
        )
        # Direction 1 (POLY_YES + KAL_NO) should hit. Direction 2 may or may not.
        assert len(opps) >= 1
        d1 = next((o for o in opps if "POLY_YES+KAL_NO" in o.title), None)
        assert d1 is not None
        assert d1.expected_profit_usd > 0
        assert d1.annualized_return >= Decimal("0.28")
        assert d1.strategy == "cross_market"
        assert d1.event_id == "p1"


class TestGating:
    def test_disabled_pair_yields_nothing(self, strategy_ctx, default_intra_config):
        poly = make_market(
            yes_asks=make_book([("0.40", "500")]),
            no_asks=make_book([("0.55", "500")]),
            days_to_resolution=30,
            market_id="poly-1",
        )
        kal = make_market(
            yes_asks=make_book([("0.55", "500")]),
            no_asks=make_book([("0.50", "500")]),
            days_to_resolution=30,
            market_id="KAL-1",
        ).model_copy(update={"platform": "kalshi"})
        opps = detect_cross_pair(
            _pair(enabled=False),
            poly,
            kal,
            _ctx(strategy_ctx),
            default_intra_config,
            Decimal("0.28"),
        )
        assert opps == []

    def test_missing_market_yields_nothing(self, strategy_ctx, default_intra_config):
        poly = make_market(
            yes_asks=make_book([("0.40", "500")]),
            no_asks=make_book([("0.55", "500")]),
            days_to_resolution=30,
            market_id="poly-1",
        )
        opps = detect_cross_pair(
            _pair(),
            poly,
            None,
            _ctx(strategy_ctx),
            default_intra_config,
            Decimal("0.28"),
        )
        assert opps == []

    def test_pair_specific_threshold_override(self, strategy_ctx, default_intra_config):
        # Same setup but tighten threshold to something it can't meet.
        poly = make_market(
            yes_asks=make_book([("0.45", "500")]),
            no_asks=make_book([("0.55", "500")]),
            days_to_resolution=30,
            market_id="poly-1",
        )
        kal = make_market(
            yes_asks=make_book([("0.55", "500")]),
            no_asks=make_book([("0.51", "500")]),
            days_to_resolution=30,
            market_id="KAL-1",
        ).model_copy(update={"platform": "kalshi"})

        # Default threshold passes.
        ok = detect_cross_pair(
            _pair(),
            poly,
            kal,
            _ctx(strategy_ctx),
            default_intra_config,
            Decimal("0.28"),
        )
        assert len(ok) >= 1

        # Aggressive override blocks it.
        blocked = detect_cross_pair(
            _pair(override="2.00"),  # 200% annualized — extreme
            poly,
            kal,
            _ctx(strategy_ctx),
            default_intra_config,
            Decimal("0.28"),
        )
        assert blocked == []


class TestSizeCapping:
    def test_size_capped_by_shallow_kalshi(self, strategy_ctx, default_intra_config):
        # Polymarket has deep liquidity; Kalshi has only 30 contracts.
        poly = make_market(
            yes_asks=make_book([("0.40", "1000")]),
            no_asks=make_book([("0.55", "1000")]),
            days_to_resolution=30,
            market_id="poly-1",
        )
        kal = make_market(
            yes_asks=make_book([("0.55", "30")]),
            no_asks=make_book([("0.50", "30")]),
            days_to_resolution=30,
            market_id="KAL-1",
        ).model_copy(update={"platform": "kalshi"})
        opps = detect_cross_pair(
            _pair(),
            poly,
            kal,
            _ctx(strategy_ctx),
            default_intra_config,
            Decimal("0.28"),
        )
        for o in opps:
            assert o.size_contracts <= Decimal("30")


class TestKalshiTickRounding:
    def test_kalshi_prices_quantized_to_cents(self, strategy_ctx, default_intra_config):
        # Force a Kalshi price that, if rounded UP to 1¢, kills the arb.
        poly = make_market(
            yes_asks=make_book([("0.40", "500")]),
            no_asks=make_book([("0.55", "500")]),
            days_to_resolution=30,
            market_id="poly-1",
        )
        # Kalshi NO ask at 0.555 ($0.555 — but Kalshi only supports cents).
        # After rounding up to 0.56, total cost = 0.40 + 0.56 = 0.96 (4¢ profit).
        kal = make_market(
            yes_asks=make_book([("0.50", "500")]),
            no_asks=make_book([("0.555", "500")]),
            days_to_resolution=30,
            market_id="KAL-1",
        ).model_copy(update={"platform": "kalshi"})
        opps = detect_cross_pair(
            _pair(),
            poly,
            kal,
            _ctx(strategy_ctx),
            default_intra_config,
            Decimal("0.28"),
        )
        # Prices on cross opps should be quantized to cents on the Kalshi leg.
        for o in opps:
            # The opportunity tags 'no_fill_price' as the leg-b (Kalshi) side
            # for direction POLY_YES+KAL_NO. Check it's at a cent boundary.
            if "POLY_YES+KAL_NO" in o.title:
                # Must be a multiple of 0.01.
                quantized = o.no_fill_price.quantize(Decimal("0.01"))
                assert o.no_fill_price == quantized, (
                    f"Kalshi leg price {o.no_fill_price} not at cent boundary"
                )


class TestFindCrossOpportunities:
    def test_iteration_order_is_deterministic(self, strategy_ctx, default_intra_config):
        poly = make_market(
            yes_asks=make_book([("0.40", "500")]),
            no_asks=make_book([("0.55", "500")]),
            days_to_resolution=30,
            market_id="poly-1",
        )
        kal = make_market(
            yes_asks=make_book([("0.55", "500")]),
            no_asks=make_book([("0.50", "500")]),
            days_to_resolution=30,
            market_id="KAL-1",
        ).model_copy(update={"platform": "kalshi"})

        pairs = [_pair(pair_id=f"p{i}") for i in range(3)]
        opps1 = find_cross_opportunities(
            pairs,
            {"poly-1": poly},
            {"KAL-1": kal},
            _ctx(strategy_ctx),
            default_intra_config,
            Decimal("0.28"),
        )
        opps2 = find_cross_opportunities(
            pairs,
            {"poly-1": poly},
            {"KAL-1": kal},
            _ctx(strategy_ctx),
            default_intra_config,
            Decimal("0.28"),
        )
        # Same inputs → same output (count and order of opportunity_ids).
        assert [o.opportunity_id for o in opps1] == [o.opportunity_id for o in opps2]
