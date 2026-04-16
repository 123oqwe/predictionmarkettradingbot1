"""Real Polymarket CLOB client — production-ready except for signing.

Polymarket auth is two-layer:
  1. L1 (wallet): your Polygon-deployed wallet signs an EIP-712 typed message
     to derive an L2 API key bundle (api_key, secret, passphrase).
  2. L2 (API key): every order signs a separate EIP-712 message with that
     bundle. CLOB validates and forwards to the on-chain matching engine.

We implement everything above the signing layer. The two `_sign_*` methods
are the seam — they raise NotImplementedError unless the operator provides
a `signer` callable. For testing/dry-run we ship a pure-Python signer that
returns deterministic placeholders (rejected by the CLOB but useful for
verifying request shapes).

Read endpoints (book, market info) are public; we use them without auth.

Rate limit: defaults to 8 req/s with burst 16. Polymarket's CLOB rejects
sustained > 10 req/s with HTTP 429.

Reference: https://docs.polymarket.com/#clob-api
"""
from __future__ import annotations

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
class PolymarketAuth:
    """L2 auth bundle returned from CLOB after wallet signs the derive-key message.

    Operator obtains this once via `clob/derive-api-key` (POST signed by wallet),
    then loads from a secrets store. Never hardcode.
    """

    api_key: str
    secret: str
    passphrase: str
    funder_address: str  # the wallet address holding USDC


# Operator-supplied signer signature: (l2_payload_dict) -> base64 signature.
# We don't ship a real implementation; web3.py + eth-account can build it but
# requires the operator's private key, which is out of scope for this repo.
Signer = Callable[[dict], str]


def stub_signer(payload: dict) -> str:
    """Deterministic stub signer for tests. CLOB rejects but request shape is correct."""
    payload_json = json.dumps(payload, sort_keys=True).encode()
    return "stub_" + hashlib.sha256(payload_json).hexdigest()[:32]


class PolymarketLiveClient(ExchangeClient):
    """Concrete Polymarket CLOB client.

    Args:
      clob_base_url: typically https://clob.polymarket.com
      auth: L2 auth bundle (or None for read-only mode)
      signer: callable that produces signatures; operator provides a real one.
      rate_per_sec: token bucket refill rate (default 8/s)
      burst: max in-flight requests (default 16)
    """

    def __init__(
        self,
        clob_base_url: str = "https://clob.polymarket.com",
        auth: Optional[PolymarketAuth] = None,
        signer: Optional[Signer] = None,
        rate_per_sec: float = 8.0,
        burst: int = 16,
        timeout_seconds: int = 10,
    ):
        self.base = clob_base_url.rstrip("/")
        self.auth = auth
        self.signer = signer
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._bucket = TokenBucket(rate_per_sec=rate_per_sec, burst=burst)

    # -------- Read endpoints (no auth) --------

    async def get_book(self, token_id: str) -> dict:
        await self._bucket.acquire()
        async with aiohttp.ClientSession(timeout=self._timeout) as s:
            async with s.get(f"{self.base}/book", params={"token_id": token_id}) as r:
                r.raise_for_status()
                return await r.json()

    async def get_market(self, condition_id: str) -> dict:
        await self._bucket.acquire()
        async with aiohttp.ClientSession(timeout=self._timeout) as s:
            async with s.get(f"{self.base}/markets/{condition_id}") as r:
                r.raise_for_status()
                return await r.json()

    # -------- Write endpoints (auth required) --------

    def _ensure_auth(self) -> None:
        if self.auth is None:
            raise RuntimeError("PolymarketLiveClient: no auth bundle configured")
        if self.signer is None:
            raise RuntimeError("PolymarketLiveClient: no signer configured")

    def _build_l2_payload(self, req: OrderRequest) -> dict:
        """Construct the EIP-712 order payload (without the signature)."""
        # Polymarket CLOB order schema. Field names are exact; do not rename.
        side_int = 0 if req.side == OrderSide.BUY else 1
        # Convert price/size to integer cents/contracts where Polymarket expects.
        # CLOB uses 6-decimal precision for USDC and 6-decimal for outcome shares.
        price_units = int((req.limit_price * Decimal(1_000_000)).to_integral_value())
        size_units = int((req.size_contracts * Decimal(1_000_000)).to_integral_value())
        return {
            "salt": int(time.time() * 1000),
            "maker": self.auth.funder_address,  # type: ignore
            "signer": self.auth.funder_address,  # type: ignore
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": req.market_id,  # YES or NO token id
            "makerAmount": price_units * size_units // 1_000_000,
            "takerAmount": size_units,
            "expiration": 0,  # 0 means until cancelled
            "nonce": 0,
            "feeRateBps": 0,
            "side": side_int,
            "signatureType": 0,  # EOA
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
        payload = self._build_l2_payload(req)
        signature = self.signer(payload)  # type: ignore
        signed_order = {**payload, "signature": signature}

        headers = self._auth_headers("POST", "/order", json.dumps(signed_order))
        body = {"order": signed_order, "owner": self.auth.api_key, "orderType": req.time_in_force}  # type: ignore

        t0 = time.monotonic()
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as s:
                async with s.post(f"{self.base}/order", json=body, headers=headers) as r:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    text = await r.text()
                    if r.status != 200:
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

        # Parse CLOB response.
        success = bool(data.get("success", False))
        if not success:
            return OrderResult(
                client_order_id=req.client_order_id,
                status=OrderStatus.REJECTED,
                filled_size=Decimal(0),
                filled_avg_price=Decimal(0),
                fees_paid_usd=Decimal(0),
                latency_ms=elapsed_ms,
                error=str(data.get("errorMsg", "unknown_error")),
            )

        order_id = str(data.get("orderID") or data.get("orderId") or "")
        # CLOB returns IOC matching info. For limit orders that rest on the
        # book, filled_size can be zero (PENDING). For IOC, we get fills.
        matches = data.get("makingAmount") or data.get("matched") or []
        filled_size = Decimal(0)
        filled_quote = Decimal(0)
        if isinstance(matches, list):
            for m in matches:
                try:
                    sz = Decimal(str(m.get("size", 0)))
                    px = Decimal(str(m.get("price", 0)))
                    filled_size += sz
                    filled_quote += sz * px
                except Exception:
                    continue

        if filled_size == 0:
            status = OrderStatus.PENDING
            avg = Decimal(0)
        elif filled_size < req.size_contracts:
            status = OrderStatus.PARTIAL
            avg = filled_quote / filled_size
        else:
            status = OrderStatus.FILLED
            avg = filled_quote / filled_size

        return OrderResult(
            client_order_id=req.client_order_id,
            status=status,
            filled_size=filled_size,
            filled_avg_price=avg,
            fees_paid_usd=Decimal(0),  # CLOB currently 0 maker/taker fees
            latency_ms=elapsed_ms,
            exchange_order_id=order_id,
        )

    async def cancel_order(self, platform_order_id: str) -> bool:
        try:
            self._ensure_auth()
        except RuntimeError:
            return False
        await self._bucket.acquire()
        body = {"orderID": platform_order_id}
        headers = self._auth_headers("DELETE", "/order", json.dumps(body))
        async with aiohttp.ClientSession(timeout=self._timeout) as s:
            async with s.delete(f"{self.base}/order", json=body, headers=headers) as r:
                return r.status == 200

    async def get_balance_usd(self) -> Decimal:
        if self.auth is None:
            return Decimal(0)
        await self._bucket.acquire()
        # /collateral returns the operator's USDC balance held by the protocol.
        headers = self._auth_headers("GET", "/collateral", "")
        async with aiohttp.ClientSession(timeout=self._timeout) as s:
            async with s.get(f"{self.base}/collateral", headers=headers) as r:
                if r.status != 200:
                    return Decimal(0)
                data = await r.json()
        return Decimal(str(data.get("balance", 0)))

    async def get_open_positions(self) -> List[dict]:
        if self.auth is None:
            return []
        await self._bucket.acquire()
        headers = self._auth_headers("GET", "/positions", "")
        async with aiohttp.ClientSession(timeout=self._timeout) as s:
            async with s.get(f"{self.base}/positions", headers=headers) as r:
                if r.status != 200:
                    return []
                data = await r.json()
        return data if isinstance(data, list) else data.get("positions", []) or []

    # -------- HMAC-SHA256 over the L2 secret for header auth --------

    def _auth_headers(self, method: str, path: str, body: str) -> Dict[str, str]:
        """Polymarket CLOB L2 auth: HMAC over (timestamp + method + path + body)."""
        if self.auth is None:
            return {}
        timestamp = str(int(time.time()))
        message = timestamp + method.upper() + path + body
        import hmac as _hmac

        sig = _hmac.new(
            self.auth.secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "POLY_ADDRESS": self.auth.funder_address,
            "POLY_SIGNATURE": sig,
            "POLY_TIMESTAMP": timestamp,
            "POLY_API_KEY": self.auth.api_key,
            "POLY_PASSPHRASE": self.auth.passphrase,
            "Content-Type": "application/json",
        }
