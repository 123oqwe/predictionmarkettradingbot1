"""Tests for data models, especially the size-weighted fill price math."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.layer3_strategy.models import OrderBookLevel, OrderBookSide
from tests.conftest import make_book


class TestOrderBookLevelFloatRejection:
    def test_rejects_float_price(self):
        with pytest.raises(TypeError, match="float value"):
            OrderBookLevel(price=0.45, size_contracts=Decimal("10"))

    def test_rejects_float_size(self):
        with pytest.raises(TypeError, match="float value"):
            OrderBookLevel(price=Decimal("0.45"), size_contracts=10.0)

    def test_accepts_string_numeric(self):
        lv = OrderBookLevel(price="0.45", size_contracts="10")
        assert lv.price == Decimal("0.45")
        assert lv.size_contracts == Decimal("10")


class TestWeightedFillPrice:
    def test_single_level_full_fill(self):
        side = make_book([("0.50", "100")])
        price, filled = side.weighted_fill_price(Decimal("50"))
        assert price == Decimal("0.50")
        assert filled == Decimal("50")

    def test_walks_book_across_levels(self):
        # 30 at 0.45, 40 at 0.46, 30 at 0.47. Buy 100.
        # Cost = 30*0.45 + 40*0.46 + 30*0.47 = 13.50 + 18.40 + 14.10 = 46.00
        # Avg = 0.46
        side = make_book([("0.45", "30"), ("0.46", "40"), ("0.47", "30")])
        price, filled = side.weighted_fill_price(Decimal("100"))
        assert filled == Decimal("100")
        assert price == Decimal("0.46")

    def test_partial_fill_when_book_shallow(self):
        side = make_book([("0.50", "20")])
        price, filled = side.weighted_fill_price(Decimal("100"))
        assert filled == Decimal("20")
        assert price == Decimal("0.50")

    def test_empty_book(self):
        side = OrderBookSide(levels=[])
        price, filled = side.weighted_fill_price(Decimal("100"))
        assert price == Decimal("0")
        assert filled == Decimal("0")

    def test_zero_desired(self):
        side = make_book([("0.50", "100")])
        price, filled = side.weighted_fill_price(Decimal("0"))
        assert price == Decimal("0")
        assert filled == Decimal("0")

    def test_exact_level_boundary(self):
        # Exactly consumes first level.
        side = make_book([("0.45", "30"), ("0.50", "100")])
        price, filled = side.weighted_fill_price(Decimal("30"))
        assert price == Decimal("0.45")
        assert filled == Decimal("30")

    def test_returns_decimal_not_float(self):
        side = make_book([("0.45", "100")])
        price, filled = side.weighted_fill_price(Decimal("50"))
        assert isinstance(price, Decimal)
        assert isinstance(filled, Decimal)
