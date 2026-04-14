"""Tests for the live executor + partial fill policy.

These are pure-Python tests using the StubExchangeClient — no network.
They verify the policy behavior the doc demands: imbalance triggers retry,
retries can resolve, failures surface so a kill switch can fire.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.layer3_strategy.intra_market import compute_opportunity
from src.layer3_strategy.models import Allocation
from src.layer4_execution.exchange import (
    ExchangeClient,
    OrderRequest,
    OrderResult,
    OrderStatus,
    SafetyGatedClient,
    StubExchangeClient,
)
from src.layer4_execution.live import LiveExecutor
from src.layer4_execution.partial_fill import (
    ImbalanceResolution,
    PartialFillConfig,
    resolve_imbalance,
)
from tests.conftest import make_book, make_market


def _alloc(strategy_ctx, size: int = 50):
    m = make_market(
        yes_asks=make_book([("0.40", "500")]),
        no_asks=make_book([("0.46", "500")]),
        days_to_resolution=30,
    )
    opp = compute_opportunity(m, strategy_ctx)
    assert opp is not None
    return Allocation(
        opportunity=opp,
        allocated_capital_usd=opp.capital_at_risk_usd,
        allocated_size_contracts=Decimal(size),
        allocation_reason="full",
    )


class TestLiveExecutorWithStub:
    @pytest.mark.asyncio
    async def test_both_legs_fill(self, strategy_ctx):
        alloc = _alloc(strategy_ctx)
        stub = StubExchangeClient(fill_prob=1.0)
        executor = LiveExecutor(stub, platform="polymarket")
        out = await executor.execute(alloc)
        assert out.both_filled
        assert out.leg_imbalance_contracts == Decimal(0)
        assert len(stub.placed_orders) == 2

    @pytest.mark.asyncio
    async def test_rejection_surfaces(self, strategy_ctx):
        alloc = _alloc(strategy_ctx)
        stub = StubExchangeClient(fill_prob=0.0)
        executor = LiveExecutor(stub, platform="polymarket")
        out = await executor.execute(alloc)
        assert not out.both_filled
        assert out.yes_leg.result.status == OrderStatus.REJECTED


class TestSafetyGatedClient:
    @pytest.mark.asyncio
    async def test_refuses_when_not_live(self, strategy_ctx):
        stub = StubExchangeClient()
        gated = SafetyGatedClient(stub, live_mode_enabled=False, api_key_env="DOES_NOT_EXIST")
        alloc = _alloc(strategy_ctx)
        executor = LiveExecutor(gated, platform="polymarket")
        out = await executor.execute(alloc)
        assert out.yes_leg.result.status == OrderStatus.REJECTED
        assert "live_mode" in (out.yes_leg.result.error or "")
        assert gated.refused_count >= 1

    @pytest.mark.asyncio
    async def test_refuses_when_api_key_missing(self, strategy_ctx, monkeypatch):
        monkeypatch.delenv("TEST_FAKE_KEY", raising=False)
        stub = StubExchangeClient()
        gated = SafetyGatedClient(stub, live_mode_enabled=True, api_key_env="TEST_FAKE_KEY")
        alloc = _alloc(strategy_ctx)
        executor = LiveExecutor(gated, platform="polymarket")
        out = await executor.execute(alloc)
        assert out.yes_leg.result.status == OrderStatus.REJECTED
        assert "API key" in (out.yes_leg.result.error or "")

    @pytest.mark.asyncio
    async def test_forwards_when_safe_and_clean(self, strategy_ctx, monkeypatch):
        monkeypatch.setenv("TEST_FAKE_KEY", "x")
        # Force git clean by monkey-patching the checker.
        import src.provenance as prov

        monkeypatch.setattr(prov, "git_is_dirty", lambda: False)
        stub = StubExchangeClient()
        gated = SafetyGatedClient(stub, live_mode_enabled=True, api_key_env="TEST_FAKE_KEY")
        alloc = _alloc(strategy_ctx)
        executor = LiveExecutor(gated, platform="polymarket")
        out = await executor.execute(alloc)
        assert out.both_filled

    @pytest.mark.asyncio
    async def test_blocks_when_dirty_tree(self, strategy_ctx, monkeypatch):
        monkeypatch.setenv("TEST_FAKE_KEY", "x")
        import src.provenance as prov

        monkeypatch.setattr(prov, "git_is_dirty", lambda: True)
        stub = StubExchangeClient()
        gated = SafetyGatedClient(stub, live_mode_enabled=True, api_key_env="TEST_FAKE_KEY")
        alloc = _alloc(strategy_ctx)
        executor = LiveExecutor(gated, platform="polymarket")
        out = await executor.execute(alloc)
        assert out.yes_leg.result.status == OrderStatus.REJECTED
        assert "dirty" in (out.yes_leg.result.error or "")


# -------- partial fill policy --------


class ImbalancedStubClient(ExchangeClient):
    """Fills YES in full but only partial NO on the first call.

    Used to simulate the leg-imbalance that the partial_fill policy must resolve.
    """

    def __init__(self, yes_fill_size: Decimal, no_fill_size: Decimal):
        self.yes_fill_size = yes_fill_size
        self.no_fill_size = no_fill_size
        self.calls: list = []

    async def place_order(self, req: OrderRequest) -> OrderResult:
        self.calls.append(req)
        if "rebalance" in req.client_order_id:
            # Retry — fill it fully.
            return OrderResult(
                client_order_id=req.client_order_id,
                status=OrderStatus.FILLED,
                filled_size=req.size_contracts,
                filled_avg_price=req.limit_price,
                fees_paid_usd=Decimal(0),
                latency_ms=10,
                exchange_order_id="retry-ok",
            )
        size = self.yes_fill_size if req.token == "YES" else self.no_fill_size
        status = OrderStatus.FILLED if size >= req.size_contracts else OrderStatus.PARTIAL
        return OrderResult(
            client_order_id=req.client_order_id,
            status=status,
            filled_size=size,
            filled_avg_price=req.limit_price,
            fees_paid_usd=Decimal(0),
            latency_ms=10,
            exchange_order_id="original",
        )

    async def cancel_order(self, *_):
        return True

    async def get_balance_usd(self):
        return Decimal(1000)

    async def get_open_positions(self):
        return []


class StubRejectAllRetries(ImbalancedStubClient):
    async def place_order(self, req: OrderRequest) -> OrderResult:
        self.calls.append(req)
        if "rebalance" in req.client_order_id:
            return OrderResult(
                client_order_id=req.client_order_id,
                status=OrderStatus.REJECTED,
                filled_size=Decimal(0),
                filled_avg_price=Decimal(0),
                fees_paid_usd=Decimal(0),
                latency_ms=10,
                error="liquidity gone",
            )
        return await super().place_order(req)


class TestPartialFillPolicy:
    @pytest.mark.asyncio
    async def test_small_imbalance_accepted(self, strategy_ctx):
        alloc = _alloc(strategy_ctx, size=100)
        client = ImbalancedStubClient(yes_fill_size=Decimal(100), no_fill_size=Decimal(98))
        executor = LiveExecutor(client, platform="polymarket")
        out = await executor.execute(alloc)
        # 2-contract imbalance is within default 5 tolerance.
        report = await resolve_imbalance(out, client, PartialFillConfig())
        assert report.resolution == ImbalanceResolution.BALANCED
        assert report.retries_used == 0

    @pytest.mark.asyncio
    async def test_large_imbalance_resolved_on_retry(self, strategy_ctx):
        alloc = _alloc(strategy_ctx, size=100)
        client = ImbalancedStubClient(yes_fill_size=Decimal(100), no_fill_size=Decimal(63))
        executor = LiveExecutor(client, platform="polymarket")
        out = await executor.execute(alloc)
        report = await resolve_imbalance(out, client, PartialFillConfig())
        assert report.resolution == ImbalanceResolution.RESOLVED_BY_RETRY
        assert report.retries_used == 1
        assert report.final_imbalance == Decimal(0)

    @pytest.mark.asyncio
    async def test_retries_exhausted_trips_kill_switch(self, strategy_ctx):
        alloc = _alloc(strategy_ctx, size=100)
        client = StubRejectAllRetries(
            yes_fill_size=Decimal(100), no_fill_size=Decimal(50)
        )
        executor = LiveExecutor(client, platform="polymarket")
        out = await executor.execute(alloc)
        report = await resolve_imbalance(out, client, PartialFillConfig(max_retries=3))
        assert report.resolution == ImbalanceResolution.FAILED_TRIP_KILL_SWITCH
        assert report.retries_used == 3
        assert report.final_imbalance > 0
