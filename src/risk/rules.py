"""Concrete kill switch rules.

Each rule is a pure function: (metrics, params) → RuleDecision. Side effects
are PolicyEngine's job.

The doc lists 10 triggers. Rule params are documented inline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.monitoring.metrics import MetricsRegistry
from src.risk.policy import RuleDecision


# 1. Daily PnL loss exceeded.
def daily_loss_exceeded(metrics: MetricsRegistry, params: dict) -> RuleDecision:
    """params: {max_daily_loss_usd: float (positive number, threshold for loss)}"""
    max_loss = float(params.get("max_daily_loss_usd", 50.0))
    pnl = metrics.rolling_pnl_24h_usd.value
    if pnl < -max_loss:
        return RuleDecision.trip(f"rolling_pnl_24h={pnl:.2f} < -{max_loss}")
    return RuleDecision.ok()


# 2. Abnormal price jump (a market with > X% move in one tick is suspicious).
def abnormal_price_jump(metrics: MetricsRegistry, params: dict) -> RuleDecision:
    """Read the `last_price_jump_pct` gauge set by the PriceJumpTracker.

    The orchestrator calls `tracker.observe(markets)` each cycle and writes
    the max jump into this gauge. A jump > threshold for one tick is a
    strong sign of market disruption or data corruption.
    """
    threshold = float(params.get("max_price_jump_pct", 0.20))
    jump = float(metrics.last_price_jump_pct.value)
    if jump > threshold:
        return RuleDecision.trip(f"price_jump={jump:.3f} > {threshold}")
    return RuleDecision.ok()


# 3. API disconnect — heartbeat age over threshold for any platform.
def api_disconnect(metrics: MetricsRegistry, params: dict) -> RuleDecision:
    """params: {max_silence_seconds: int (per-layer heartbeat age),
                layers: list[str] (which heartbeats to check)}"""
    max_silence = int(params.get("max_silence_seconds", 60))
    layers = params.get("layers") or list(metrics._last_heartbeat.keys())
    for layer in layers:
        age = metrics.heartbeat_age_seconds(layer)
        if age is None:
            continue
        if age > max_silence:
            return RuleDecision.trip(f"layer={layer} silent for {age:.1f}s")
    return RuleDecision.ok()


# 4. Clock drift vs system time. Phase 2 doc says 5s, we tighten to 2s.
def clock_drift(metrics: MetricsRegistry, params: dict) -> RuleDecision:
    """params: {max_drift_seconds: int (absolute drift threshold)}.

    The orchestrator sets `metrics.clock_drift_seconds` from an NTP probe.
    """
    max_drift = float(params.get("max_drift_seconds", 2.0))
    drift = abs(metrics.clock_drift_seconds.value)
    if drift > max_drift:
        return RuleDecision.trip(f"clock_drift={drift:.2f}s > {max_drift}s")
    return RuleDecision.ok()


# 5. Unhandled exception rate.
def unhandled_exception_rate(metrics: MetricsRegistry, params: dict) -> RuleDecision:
    """params: {max_per_5min: int}. Reads metrics.exceptions_per_5min count."""
    threshold = int(params.get("max_per_5min", 5))
    n = metrics.exceptions_per_5min.count()
    if n > threshold:
        return RuleDecision.trip(f"{n} exceptions in last 5min > {threshold}")
    return RuleDecision.ok()


# 6. Position mismatch — set by reconcile job; we only read.
def position_mismatch(metrics: MetricsRegistry, params: dict) -> RuleDecision:
    """params: {} — reads metrics gauge `position_mismatch_count`."""
    g = getattr(metrics, "position_mismatch_count", None)
    n = float(g.value) if g else 0.0
    if n > 0:
        return RuleDecision.trip(f"reconcile mismatch count = {int(n)}")
    return RuleDecision.ok()


# 7. USDC depeg (Polymarket settles in USDC).
def usdc_depeg(metrics: MetricsRegistry, params: dict) -> RuleDecision:
    """params: {min_price_usd: float}. Reads gauge `usdc_price_usd`."""
    min_price = float(params.get("min_price_usd", 0.995))
    g = getattr(metrics, "usdc_price_usd", None)
    p = float(g.value) if g else 1.0
    if 0 < p < min_price:
        return RuleDecision.trip(f"usdc_price={p:.4f} < {min_price}")
    return RuleDecision.ok()


# 8. Layer stall — same idea as api_disconnect but layer-specific.
def layer_stall(metrics: MetricsRegistry, params: dict) -> RuleDecision:
    """params: {max_silence_seconds: int (default 120), layers: list[str]}"""
    max_silence = int(params.get("max_silence_seconds", 120))
    layers = params.get("layers") or list(metrics._last_heartbeat.keys())
    stalled = []
    for layer in layers:
        age = metrics.heartbeat_age_seconds(layer)
        if age is not None and age > max_silence:
            stalled.append(f"{layer}={age:.0f}s")
    if stalled:
        return RuleDecision.trip(f"stalled: {', '.join(stalled)}")
    return RuleDecision.ok()


# 9. Manual kill via kill file on disk.
def manual_kill(metrics: MetricsRegistry, params: dict) -> RuleDecision:
    """params: {kill_file_path: str (default /tmp/arb_agent.kill)}"""
    p = Path(params.get("kill_file_path", "/tmp/arb_agent.kill"))
    if p.exists():
        return RuleDecision.trip(f"kill file present at {p}")
    return RuleDecision.ok()


# 10. Event map drift — content hash changed since startup.
def event_map_drift(metrics: MetricsRegistry, params: dict) -> RuleDecision:
    """params: {expected_hash: str (the hash recorded at startup),
                current_hash: str (re-read each cycle)}"""
    expected: Optional[str] = params.get("expected_hash")
    current: Optional[str] = params.get("current_hash")
    if expected and current and expected != current:
        return RuleDecision.trip(f"event_map hash changed: {expected} → {current}")
    return RuleDecision.ok()


# 11 (Round A #17). Disk-free-low — prevent silent Parquet write failures.
def disk_free_low(metrics: MetricsRegistry, params: dict) -> RuleDecision:
    """params: {min_free_pct: float (default 5.0)}"""
    threshold = float(params.get("min_free_pct", 5.0))
    free = float(metrics.disk_free_pct.value)
    if 0 < free < threshold:
        return RuleDecision.trip(f"disk_free={free:.1f}% < {threshold}%")
    return RuleDecision.ok()


ALL_RULES = {
    "daily_loss_exceeded": daily_loss_exceeded,
    "abnormal_price_jump": abnormal_price_jump,
    "api_disconnect": api_disconnect,
    "clock_drift": clock_drift,
    "unhandled_exception_rate": unhandled_exception_rate,
    "position_mismatch": position_mismatch,
    "usdc_depeg": usdc_depeg,
    "layer_stall": layer_stall,
    "manual": manual_kill,
    "event_map_drift": event_map_drift,
    "disk_free_low": disk_free_low,
}
