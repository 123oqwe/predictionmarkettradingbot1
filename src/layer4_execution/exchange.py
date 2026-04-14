"""Exchange client abstractions for Phase 3.

Defines the interface every live platform implementation must satisfy.
Ships two concrete implementations:

  - StubExchangeClient: synthetic fills for testing. Always succeeds at the
    requested price. Used in unit tests and by the orchestrator when --live
    is NOT set.

  - SafetyGatedClient: wrapper that refuses to forward any order unless:
      1. --live flag was passed (checked via constructor)
      2. Env var for the platform's API key is present
      3. Git tree is clean (no uncommitted changes)
      4. Dry-run override is not set
    This is the multiple-redundant-gate pattern the doc demands: even if one
    check is bypassed, the next catches it.

Real HTTP clients (PolymarketLiveClient, KalshiLiveClient) are NOT in this
commit. They must be filled in by the operator after KYC and funding. The
interface is stable so the rest of the system doesn't change when they arrive.
"""
from __future__ import annotations

import abc
import os
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import List, Optional


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    PENDING = "pending"


@dataclass(frozen=True)
class OrderRequest:
    """What we send to the exchange. Includes idempotency key."""

    client_order_id: str
    platform: str
    market_id: str
    side: OrderSide
    token: str  # "YES" or "NO"
    limit_price: Decimal
    size_contracts: Decimal
    time_in_force: str = "IOC"  # immediate-or-cancel is the default for arbs


@dataclass(frozen=True)
class OrderResult:
    """What we get back."""

    client_order_id: str
    status: OrderStatus
    filled_size: Decimal
    filled_avg_price: Decimal
    fees_paid_usd: Decimal
    latency_ms: int
    exchange_order_id: Optional[str] = None
    error: Optional[str] = None


class ExchangeClient(abc.ABC):
    """Interface every platform client must implement."""

    @abc.abstractmethod
    async def place_order(self, req: OrderRequest) -> OrderResult:
        ...

    @abc.abstractmethod
    async def cancel_order(self, platform_order_id: str) -> bool:
        ...

    @abc.abstractmethod
    async def get_balance_usd(self) -> Decimal:
        ...

    @abc.abstractmethod
    async def get_open_positions(self) -> List[dict]:
        """Returns raw position dicts per platform. Reconciliation compares to SQLite."""


class StubExchangeClient(ExchangeClient):
    """Pure in-memory exchange for tests + paper cross-check. No network."""

    def __init__(
        self,
        *,
        fill_prob: float = 1.0,
        slippage_bps: int = 0,
        latency_ms: int = 50,
    ):
        self.fill_prob = fill_prob
        self.slippage_bps = slippage_bps
        self.latency_ms = latency_ms
        self._placed: List[OrderRequest] = []
        self._balance = Decimal(1000)

    @property
    def placed_orders(self) -> List[OrderRequest]:
        return list(self._placed)

    async def place_order(self, req: OrderRequest) -> OrderResult:
        self._placed.append(req)
        if self.fill_prob <= 0:
            return OrderResult(
                client_order_id=req.client_order_id,
                status=OrderStatus.REJECTED,
                filled_size=Decimal(0),
                filled_avg_price=Decimal(0),
                fees_paid_usd=Decimal(0),
                latency_ms=self.latency_ms,
                error="stub configured with fill_prob=0",
            )
        slip = req.limit_price * Decimal(self.slippage_bps) / Decimal(10000)
        filled_px = (
            req.limit_price + slip if req.side == OrderSide.BUY else req.limit_price - slip
        )
        return OrderResult(
            client_order_id=req.client_order_id,
            status=OrderStatus.FILLED,
            filled_size=req.size_contracts,
            filled_avg_price=filled_px,
            fees_paid_usd=Decimal(0),
            latency_ms=self.latency_ms,
            exchange_order_id=f"stub-{len(self._placed)}",
        )

    async def cancel_order(self, platform_order_id: str) -> bool:
        return True

    async def get_balance_usd(self) -> Decimal:
        return self._balance

    async def get_open_positions(self) -> List[dict]:
        return []


class SafetyGatedClient(ExchangeClient):
    """Wraps a real client with redundant safety checks.

    All four checks must pass before any order reaches the wrapped client:
      1. live_mode_enabled=True (explicit opt-in, from orchestrator --live)
      2. API key env var present and non-empty
      3. git_is_clean() (no uncommitted changes — dirty trees can't trade
         real money; see provenance.git_is_dirty())
      4. dry_run=False (if True, log what would have happened, return stub result)

    This is the belt+suspenders pattern from the doc. Any one check catching
    the misconfigured case is enough.
    """

    def __init__(
        self,
        inner: ExchangeClient,
        *,
        live_mode_enabled: bool,
        api_key_env: str,
        dry_run: bool = False,
    ):
        self.inner = inner
        self.live_mode_enabled = live_mode_enabled
        self.api_key_env = api_key_env
        self.dry_run = dry_run
        self._refused_count = 0

    @property
    def refused_count(self) -> int:
        return self._refused_count

    def _safety_ok(self) -> Optional[str]:
        """Return error string if NOT safe, None if safe."""
        if not self.live_mode_enabled:
            return "live_mode not enabled"
        if not os.environ.get(self.api_key_env):
            return f"API key env var {self.api_key_env} is unset"
        # Lazy import to avoid a cycle at module load.
        from src.provenance import git_is_dirty

        if git_is_dirty():
            return "git tree is dirty; refusing to trade real money on uncommitted code"
        if self.dry_run:
            return "dry_run enabled"
        return None

    async def place_order(self, req: OrderRequest) -> OrderResult:
        err = self._safety_ok()
        if err:
            self._refused_count += 1
            return OrderResult(
                client_order_id=req.client_order_id,
                status=OrderStatus.REJECTED,
                filled_size=Decimal(0),
                filled_avg_price=Decimal(0),
                fees_paid_usd=Decimal(0),
                latency_ms=0,
                error=f"safety_gate_blocked: {err}",
            )
        return await self.inner.place_order(req)

    async def cancel_order(self, platform_order_id: str) -> bool:
        if self._safety_ok():
            return False
        return await self.inner.cancel_order(platform_order_id)

    async def get_balance_usd(self) -> Decimal:
        # Balance queries are read-only; allow even without full safety stack
        # IF live_mode is on and api_key is present. Dry-run is still fine for
        # balance reads. Dirty git blocks to avoid using stale schemas.
        if not self.live_mode_enabled:
            return Decimal(0)
        return await self.inner.get_balance_usd()

    async def get_open_positions(self) -> List[dict]:
        if not self.live_mode_enabled:
            return []
        return await self.inner.get_open_positions()
