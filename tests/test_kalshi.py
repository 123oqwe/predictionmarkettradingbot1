"""Kalshi-specific tests. Tick rounding + book parsing."""
from __future__ import annotations

from decimal import Decimal

from src.layer1_data_recording.kalshi_fetcher import (
    _parse_book_side,
    quantize_to_kalshi_tick,
)


class TestTickQuantization:
    def test_half_cent_rounds(self):
        # Kalshi doesn't support half-cent. 0.505 must round to a valid tick.
        q = quantize_to_kalshi_tick(Decimal("0.505"))
        assert q in (Decimal("0.50"), Decimal("0.51"))

    def test_exact_cent_unchanged(self):
        assert quantize_to_kalshi_tick(Decimal("0.45")) == Decimal("0.45")

    def test_returns_decimal(self):
        assert isinstance(quantize_to_kalshi_tick(Decimal("0.45")), Decimal)


class TestBookParsing:
    def test_parses_cents_to_dollars(self):
        # Kalshi format: [[price_cents, size], ...]
        side = _parse_book_side([[45, "30"], [46, "40"]], sort_ascending=True)
        assert len(side.levels) == 2
        assert side.levels[0].price == Decimal("0.45")
        assert side.levels[0].size_contracts == Decimal("30")
        assert side.levels[1].price == Decimal("0.46")

    def test_drops_invalid_rows(self):
        side = _parse_book_side(
            [[45, "30"], "garbage", [150, "10"], [20, "0"]], sort_ascending=True
        )
        # Valid: 45/30. Dropped: garbage, 150 (price >= 1), 20/0 (zero size).
        assert len(side.levels) == 1
        assert side.levels[0].price == Decimal("0.45")

    def test_empty_payload(self):
        side = _parse_book_side([], sort_ascending=True)
        assert side.levels == []

    def test_none_payload(self):
        side = _parse_book_side(None, sort_ascending=True)
        assert side.levels == []

    def test_asks_sorted_ascending(self):
        side = _parse_book_side(
            [[50, "10"], [45, "10"], [48, "10"]], sort_ascending=True
        )
        prices = [lv.price for lv in side.levels]
        assert prices == sorted(prices)

    def test_bids_sorted_descending(self):
        side = _parse_book_side(
            [[50, "10"], [45, "10"], [48, "10"]], sort_ascending=False
        )
        prices = [lv.price for lv in side.levels]
        assert prices == sorted(prices, reverse=True)
