"""Fill model tests. Every edge case from phase-2.5-backtest.md."""
from __future__ import annotations

from decimal import Decimal

from src.backtest.fill_model import FillModelConfig, FillModelKind, fill_price
from tests.conftest import make_book


class TestOptimistic:
    def test_top_of_book(self):
        side = make_book([("0.45", "50"), ("0.50", "100")])
        price, filled = fill_price(
            side,
            Decimal("50"),
            FillModelConfig(kind=FillModelKind.OPTIMISTIC),
        )
        assert price == Decimal("0.45")
        assert filled == Decimal("50")

    def test_walks_book(self):
        side = make_book([("0.40", "20"), ("0.50", "80")])
        price, filled = fill_price(
            side,
            Decimal("100"),
            FillModelConfig(kind=FillModelKind.OPTIMISTIC),
        )
        # Weighted average of 20@0.40 + 80@0.50 = 0.48
        assert price == Decimal("0.48")
        assert filled == Decimal("100")


class TestRealistic:
    def test_adds_slippage(self):
        side = make_book([("0.50", "100")])
        price, filled = fill_price(
            side,
            Decimal("100"),
            FillModelConfig(kind=FillModelKind.REALISTIC, realistic_slippage_bps=20),
        )
        # 0.50 + 20 bps (0.20% of 0.50) = 0.501
        assert price == Decimal("0.501")
        assert filled == Decimal("100")

    def test_rejects_price_over_one(self):
        side = make_book([("0.999", "100")])
        price, filled = fill_price(
            side,
            Decimal("100"),
            FillModelConfig(kind=FillModelKind.REALISTIC, realistic_slippage_bps=1000),
        )
        # 0.999 * 1.1 > 1 → reject
        assert filled == Decimal("0")


class TestPessimistic:
    def test_drops_top_level_when_depth_available(self):
        # 30 at 0.40 (top), 40 at 0.45, 30 at 0.50.
        # Pessimistic skips top, walks from second level: 40@0.45 + 30@0.50 = 70 total.
        # Weighted for 50 contracts: 40@0.45 + 10@0.50 = 23.0 / 50 = 0.46
        side = make_book([("0.40", "30"), ("0.45", "40"), ("0.50", "30")])
        price, filled = fill_price(
            side, Decimal("50"), FillModelConfig(kind=FillModelKind.PESSIMISTIC)
        )
        assert filled == Decimal("50")
        assert price == Decimal("0.46")

    def test_size_capped_to_second_level_depth(self):
        side = make_book([("0.40", "100"), ("0.50", "30")])
        price, filled = fill_price(
            side, Decimal("100"), FillModelConfig(kind=FillModelKind.PESSIMISTIC)
        )
        # Only 30 at 0.50 remain after dropping top, so fill is capped.
        assert filled == Decimal("30")
        assert price == Decimal("0.50")

    def test_single_level_falls_back_with_extra_slippage(self):
        # Only one level — doc says "skip", but we apply extra slippage instead
        # to keep trade counts honest. Default 100 bps extra.
        side = make_book([("0.50", "100")])
        price, filled = fill_price(
            side,
            Decimal("100"),
            FillModelConfig(
                kind=FillModelKind.PESSIMISTIC, pessimistic_extra_slippage_bps=100
            ),
        )
        # 0.50 + 100 bps = 0.50 * 1.01 = 0.505
        assert price == Decimal("0.505")
        assert filled == Decimal("100")


class TestEdgeCases:
    def test_empty_book(self):
        from src.layer3_strategy.models import OrderBookSide

        side = OrderBookSide(levels=[])
        for kind in FillModelKind:
            p, f = fill_price(side, Decimal("100"), FillModelConfig(kind=kind))
            assert p == Decimal(0)
            assert f == Decimal(0)

    def test_zero_desired_returns_zeros(self):
        side = make_book([("0.50", "100")])
        for kind in FillModelKind:
            p, f = fill_price(side, Decimal("0"), FillModelConfig(kind=kind))
            assert p == Decimal(0)
            assert f == Decimal(0)

    def test_all_return_decimal_not_float(self):
        side = make_book([("0.45", "100")])
        for kind in FillModelKind:
            p, f = fill_price(side, Decimal("50"), FillModelConfig(kind=kind))
            assert isinstance(p, Decimal)
            assert isinstance(f, Decimal)
