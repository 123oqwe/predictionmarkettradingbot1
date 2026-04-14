"""External probes that feed metrics each cycle.

Each probe returns a value or None. If None, the metric is NOT updated — this
way a failing probe doesn't mask the last-known-good value, and the operator
gets to see the `*_age_seconds` heartbeat metric go stale.

All probes are defensively implemented: any exception returns None, plus
`logger.warning(...)`. The kill switch rules tolerate `None` gracefully.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import aiohttp
import structlog

from src.layer3_strategy.models import Market

logger = structlog.get_logger(__name__)


# ---------------- Clock drift (NTP) ----------------

# We use Cloudflare's HTTP time endpoint as a simple alternative to ntplib
# (avoids adding a dep; HTTP is accurate to a few hundred ms which is plenty
# for a 2-second drift threshold).
_CLOCK_PROBE_URL = "https://cloudflare.com/cdn-cgi/trace"


async def probe_clock_drift_seconds(
    timeout: int = 5,
) -> Optional[float]:
    """Return absolute drift between local clock and an external reference.

    Uses Cloudflare's trace endpoint which includes `ts=<unix>` in the body.
    Network-free fallback: returns 0 (no drift claim) if the endpoint fails.
    A None return means "probe failed, don't update the gauge".
    """
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            local_before = time.time()
            async with s.get(_CLOCK_PROBE_URL) as resp:
                text = await resp.text()
            local_after = time.time()
    except Exception as e:
        logger.warning("clock_probe_failed", error=str(e))
        return None

    # Parse `ts=1234567890.123` from the trace body.
    remote_ts: Optional[float] = None
    for line in text.splitlines():
        if line.startswith("ts="):
            try:
                remote_ts = float(line.split("=", 1)[1])
                break
            except ValueError:
                continue
    if remote_ts is None:
        logger.warning("clock_probe_no_ts", body=text[:120])
        return None

    local_mid = (local_before + local_after) / 2
    return abs(local_mid - remote_ts)


# ---------------- USDC spot price ----------------

# Coinbase spot: simplest public endpoint. Stable, rate-limit-friendly.
_USDC_PROBE_URL = "https://api.coinbase.com/v2/prices/USDC-USD/spot"


async def probe_usdc_price_usd(timeout: int = 5) -> Optional[float]:
    """Return the current USDC/USD spot price as a float.

    Kill switch threshold is 0.995 by default; any trip here halts Polymarket
    trading (which settles in USDC) until manually reset.
    """
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.get(_USDC_PROBE_URL) as resp:
                if resp.status != 200:
                    logger.warning("usdc_probe_bad_status", status=resp.status)
                    return None
                data = await resp.json()
    except Exception as e:
        logger.warning("usdc_probe_failed", error=str(e))
        return None

    try:
        return float(data["data"]["amount"])
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("usdc_probe_parse_failed", error=str(e))
        return None


# ---------------- Price jump tracker ----------------


class PriceJumpTracker:
    """Tracks mid-price per market_id across cycles. Returns max observed jump.

    "Mid" = (best_yes_ask + (1 - best_no_ask)) / 2, which under no-arb should
    equal the implied YES probability. Big moves of the mid across one tick
    are what `abnormal_price_jump` watches for.

    In-memory only. Intentional: we don't want old prices from yesterday
    influencing today's jump calc; a cold start means one cycle of warmup.
    """

    def __init__(self):
        self._prev_mid: Dict[str, float] = {}

    def _mid(self, market: Market) -> Optional[float]:
        if not market.yes_asks.levels or not market.no_asks.levels:
            return None
        yes = float(market.yes_asks.levels[0].price)
        no = float(market.no_asks.levels[0].price)
        # Two independent estimates of YES probability; average.
        return (yes + (1 - no)) / 2

    def observe(self, markets: List[Market]) -> float:
        """Update internal state from this tick's markets; return the largest
        relative price jump observed across all known markets.

        Returns 0.0 if no prior data (warmup cycle).
        """
        max_jump = 0.0
        for m in markets:
            mid = self._mid(m)
            if mid is None:
                continue
            prev = self._prev_mid.get(m.market_id)
            if prev is not None and prev > 0:
                jump = abs(mid - prev) / prev
                if jump > max_jump:
                    max_jump = jump
            self._prev_mid[m.market_id] = mid
        return max_jump

    def forget(self, market_id: str) -> None:
        """Drop a market from tracking (e.g., resolved)."""
        self._prev_mid.pop(market_id, None)


# ---------------- Reconcile → metrics bridge ----------------


def position_mismatch_count_from_reconcile(reconcile_report) -> int:
    """Count the mismatch findings from a ReconcileReport."""
    return int(reconcile_report.mismatch_count)
