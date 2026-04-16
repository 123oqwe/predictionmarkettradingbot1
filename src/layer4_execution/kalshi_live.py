"""Real Kalshi trade-api v2 client — production-ready except for RSA signing.

Kalshi v2 auth: each authenticated request includes:
  KALSHI-ACCESS-KEY:        the operator's API key id
  KALSHI-ACCESS-TIMESTAMP:  current epoch milliseconds
  KALSHI-ACCESS-SIGNATURE:  base64 RSA-PSS signature of (timestamp + method + path)

Operator generates an RSA keypair in Kalshi's web UI, downloads the private
key, and provides it to this client. Read endpoints (/markets, /events,
/orderbook) work without auth.

Rate limit: defaults to 5 req/s burst 10. Kalshi documents 100 req/s but
returns 429 on bursts well below that on the v2 endpoints; conservative
defaults stay safely below.

Reference: https://trading-api.readme.io/reference/getting-started
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Dict, List, Optional

import aiohttp
import structlog

from src.layer4_execution.exchange import (
    ExchangeClient,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
)
from src.layer4_execution.rate_limit import TokenBucket

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class KalshiAuth:
    """Kalshi v2 auth bundle.

    `private_key_pem` is the operator's downloaded RSA private key (PEM-encoded).
    Never log it; never commit it. Treat as wallet-level secret.
    """

    api_key_id: str
    private_key_pem: str  # PEM-encoded PKCS8 RSA private key


# Operator-supplied signer signature: (message_bytes) -> base64 signature.
RsaSigner = Callable[[bytes], str]


def make_rsa_signer(private_key_pem: str) -> RsaSigner:
    """Return a signer using the cryptography library.

    The cryptography package is a peer dep — already required for HTTPS via
    aiohttp, so we don't add it. Operator must have it installed.
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as e:
        raise RuntimeError(
            "Kalshi RSA signing needs `cryptography` package. "
            "Install with `pip install cryptography`."
        ) from e

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode(), password=None
    )

    def sign(message: bytes) -> str:
        sig = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode()

    return sign


def stub_signer(message: bytes) -> str:
    """Deterministic stub for tests; Kalshi rejects but request shape verifies."""
    return base64.b64encode(hashlib.sha256(message).digest()).decode()


class KalshiLiveClient(ExchangeClient):
    """Concrete Kalshi v2 client.

    Args:
      base_url: typically https://api.elections.kalshi.com/trade-api/v2
      auth: KalshiAuth bundle (or None for read-only)
      signer: callable that produces RSA-PSS signatures
      rate_per_sec / burst: token bucket
    """

    def __init__(
        self,
        base_url: str = "https://api.elections.kalshi.com/trade-api/v2",
        auth: Optional[KalshiAuth] = None,
        signer: Optional[RsaSigner] = None,
        rate_per_sec: float = 5.0,
        burst: int = 10,
        timeout_seconds: int = 10,
    ):
        self.base = base_url.rstrip("/")
        self.auth = auth
        self._signer = signer or (
            make_rsa_signer(auth.private_key_pem) if auth else None
        )
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._bucket = TokenBucket(rate_per_sec=rate_per_sec, burst=burst)

    # -------- Read endpoints --------

    async def get_markets(self, status: str = "open", limit: int = 100) -> List[dict]:
        await self._bucket.acquire()
        async with aiohttp.ClientSession(timeout=self._timeout) as s:
            async with s.get(
                f"{self.base}/markets",
                params={"status": status, "limit": str(limit)},
            ) as r:
                r.raise_for_status()
                data = await r.json()
        return data.get("markets", [])

    async def get_orderbook(self, ticker: str) -> dict:
        await self._bucket.acquire()
        async with aiohttp.ClientSession(timeout=self._timeout) as s:
            async with s.get(f"{self.base}/markets/{ticker}/orderbook") as r:
                r.raise_for_status()
                return await r.json()

    # -------- Write endpoints --------

    def _ensure_auth(self) -> None:
        if self.auth is None:
            raise RuntimeError("KalshiLiveClient: no auth bundle configured")
        if self._signer is None:
            raise RuntimeError("KalshiLiveClient: no signer configured")

    def _auth_headers(self, method: str, path: str) -> Dict[str, str]:
        ts = str(int(time.time() * 1000))
        message = (ts + method.upper() + path).encode()
        sig = self._signer(message)  # type: ignore
        return {
            "KALSHI-ACCESS-KEY": self.auth.api_key_id,  # type: ignore
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "Content-Type": "application/json",
        }

    async def place_order(self, req: OrderRequest) -> OrderResult:
        try:
            self._ensure_auth()
        except RuntimeError as e:
            return OrderResult(
                client_order_id=req.client_order_id,
                status=OrderStatus.REJECTED,
                filled_size=Decimal(0),
                filled_avg_price=Decimal(0),
                fees_paid_usd=Decimal(0),
                latency_ms=0,
                error=f"auth_missing: {e}",
            )

        await self._bucket.acquire()
        # Kalshi prices are integers in cents.
        price_cents = int((req.limit_price * Decimal(100)).to_integral_value())
        body = {
            "ticker": req.market_id,
            "client_order_id": req.client_order_id,
            "side": "yes" if req.token == "YES" else "no",
            "action": "buy" if req.side == OrderSide.BUY else "sell",
            "type": "limit",
            "yes_price": price_cents if req.token == "YES" else None,
            "no_price": price_cents if req.token == "NO" else None,
            "count": int(req.size_contracts),
            "time_in_force": "IOC" if req.time_in_force == "IOC" else "GTC",
        }
        body = {k: v for k, v in body.items() if v is not None}
        headers = self._auth_headers("POST", "/portfolio/orders")
        t0 = time.monotonic()
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as s:
                async with s.post(
                    f"{self.base}/portfolio/orders", json=body, headers=headers
                ) as r:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    text = await r.text()
                    if r.status not in (200, 201):
                        return OrderResult(
                            client_order_id=req.client_order_id,
                            status=OrderStatus.REJECTED,
                            filled_size=Decimal(0),
                            filled_avg_price=Decimal(0),
                            fees_paid_usd=Decimal(0),
                            latency_ms=elapsed_ms,
                            error=f"http_{r.status}: {text[:200]}",
                        )
                    data = json.loads(text)
        except Exception as e:
            return OrderResult(
                client_order_id=req.client_order_id,
                status=OrderStatus.REJECTED,
                filled_size=Decimal(0),
                filled_avg_price=Decimal(0),
                fees_paid_usd=Decimal(0),
                latency_ms=int((time.monotonic() - t0) * 1000),
                error=f"http_exception: {e}",
            )

        # Kalshi response shape: { "order": { "order_id": "...", "status": "...",
        #   "remaining_count": N, "fill_count": N, "yes_price"/"no_price": cents } }
        order = data.get("order") or {}
        order_id = str(order.get("order_id") or "")
        fill_count = int(order.get("fill_count", 0) or 0)
        remaining = int(order.get("remaining_count", req.size_contracts) or 0)
        ks_status = (order.get("status") or "").lower()

        if fill_count == 0 and ks_status in ("resting", "open"):
            status = OrderStatus.PENDING
        elif fill_count >= int(req.size_contracts):
            status = OrderStatus.FILLED
        elif fill_count > 0:
            status = OrderStatus.PARTIAL
        elif ks_status in ("rejected", "cancelled", "canceled"):
            status = OrderStatus.REJECTED
        else:
            status = OrderStatus.PENDING

        # Effective avg price: Kalshi gives the limit_price; we trust it for
        # limit IOC fills since matching can't fill above limit.
        avg = Decimal(price_cents) / Decimal(100)

        return OrderResult(
            client_order_id=req.client_order_id,
            status=status,
            filled_size=Decimal(fill_count),
            filled_avg_price=avg,
            fees_paid_usd=Decimal(0),  # Kalshi extracts fees on settle
            latency_ms=elapsed_ms,
            exchange_order_id=order_id,
        )

    async def cancel_order(self, platform_order_id: str) -> bool:
        try:
            self._ensure_auth()
        except RuntimeError:
            return False
        await self._bucket.acquire()
        headers = self._auth_headers("DELETE", f"/portfolio/orders/{platform_order_id}")
        async with aiohttp.ClientSession(timeout=self._timeout) as s:
            async with s.delete(
                f"{self.base}/portfolio/orders/{platform_order_id}", headers=headers
            ) as r:
                return r.status == 200

    async def get_balance_usd(self) -> Decimal:
        if self.auth is None:
            return Decimal(0)
        await self._bucket.acquire()
        headers = self._auth_headers("GET", "/portfolio/balance")
        async with aiohttp.ClientSession(timeout=self._timeout) as s:
            async with s.get(f"{self.base}/portfolio/balance", headers=headers) as r:
                if r.status != 200:
                    return Decimal(0)
                data = await r.json()
        # Kalshi reports in cents.
        cents = data.get("balance", 0)
        return Decimal(str(cents)) / Decimal(100)

    async def get_open_positions(self) -> List[dict]:
        if self.auth is None:
            return []
        await self._bucket.acquire()
        headers = self._auth_headers("GET", "/portfolio/positions")
        async with aiohttp.ClientSession(timeout=self._timeout) as s:
            async with s.get(f"{self.base}/portfolio/positions", headers=headers) as r:
                if r.status != 200:
                    return []
                data = await r.json()
        return data.get("market_positions", [])
