"""Shared pytest fixtures."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.config import IntraMarketConfig
from src.layer3_strategy.intra_market import StrategyContext
from src.layer3_strategy.models import Market, OrderBookLevel, OrderBookSide


@pytest.fixture
def default_intra_config() -> IntraMarketConfig:
    return IntraMarketConfig(
        min_annualized_return=Decimal("0.20"),
        min_days_to_resolution=Decimal("5"),
        min_trade_size_contracts=Decimal("5"),
        max_trade_size_contracts=Decimal("1000"),
        min_market_liquidity_usd=Decimal("100"),
        stale_snapshot_threshold_seconds=10,
    )


@pytest.fixture
def strategy_ctx(default_intra_config) -> StrategyContext:
    return StrategyContext(
        config=default_intra_config,
        gas_cost_usd=Decimal("0.20"),
        config_hash="test_config_hash",
        git_hash="test_git_hash",
    )


@pytest.fixture
def strategy_ctx_no_gas(default_intra_config) -> StrategyContext:
    return StrategyContext(
        config=default_intra_config,
        gas_cost_usd=Decimal("0"),
        config_hash="test_config_hash",
        git_hash="test_git_hash",
    )


def make_book(levels):
    """Helper: build an OrderBookSide from a list of (price_str, size_str) tuples."""
    return OrderBookSide(
        levels=[OrderBookLevel(price=Decimal(p), size_contracts=Decimal(s)) for p, s in levels]
    )


def make_market(
    *,
    yes_asks=None,
    no_asks=None,
    yes_bids=None,
    no_bids=None,
    days_to_resolution: float = 30.0,
    fee_bps: int = 0,
    market_id: str = "mkt-1",
    event_id: str = "evt-1",
    active: bool = True,
    resolved: bool = False,
    fetched_at: datetime | None = None,
) -> Market:
    """Helper: build a Market snapshot from minimal inputs.

    All time-sensitive fields derive from `fetched_at`, so callers control the clock.
    """
    if fetched_at is None:
        # Anchor to a fixed timestamp so tests are deterministic.
        fetched_at = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)

    resolution_date = fetched_at + timedelta(
        seconds=int(days_to_resolution * 86400)
    )

    return Market(
        platform="polymarket",
        market_id=market_id,
        event_id=event_id,
        title="Test market",
        yes_bids=yes_bids or OrderBookSide(levels=[]),
        yes_asks=yes_asks or OrderBookSide(levels=[]),
        no_bids=no_bids or OrderBookSide(levels=[]),
        no_asks=no_asks or OrderBookSide(levels=[]),
        fee_bps=fee_bps,
        resolution_date=resolution_date,
        resolution_source="test",
        fetched_at=fetched_at,
        active=active,
        resolved=resolved,
        liquidity_usd=Decimal("1000"),
    )
