"""Tests for the monitoring probes + PriceJumpTracker.

Network probes are not exercised in unit tests (offline CI); instead we test
the parsing / fallback paths by constructing synthetic inputs.
"""
from __future__ import annotations

import pytest

from src.monitoring.probes import (
    PriceJumpTracker,
    position_mismatch_count_from_reconcile,
    probe_clock_drift_seconds,
    probe_usdc_price_usd,
)
from src.risk.reconcile import ReconcileFinding, ReconcileReport
from tests.conftest import make_book, make_market


class TestPriceJumpTracker:
    def test_empty_markets_returns_zero(self):
        t = PriceJumpTracker()
        assert t.observe([]) == 0.0

    def test_cold_start_no_jump(self):
        t = PriceJumpTracker()
        m = make_market(
            yes_asks=make_book([("0.50", "100")]),
            no_asks=make_book([("0.50", "100")]),
        )
        # First observation sets baseline; no jump claim possible.
        assert t.observe([m]) == 0.0

    def test_large_jump_detected(self):
        t = PriceJumpTracker()
        m1 = make_market(
            yes_asks=make_book([("0.50", "100")]),
            no_asks=make_book([("0.50", "100")]),
        )
        t.observe([m1])
        # Next tick: YES price jumps to 0.70 (40% move in implied probability).
        m2 = make_market(
            yes_asks=make_book([("0.70", "100")]),
            no_asks=make_book([("0.30", "100")]),
        )
        jump = t.observe([m2])
        assert jump > 0.1

    def test_no_jump_when_quiet(self):
        t = PriceJumpTracker()
        m1 = make_market(yes_asks=make_book([("0.50", "100")]), no_asks=make_book([("0.50", "100")]))
        t.observe([m1])
        m2 = make_market(
            yes_asks=make_book([("0.501", "100")]),
            no_asks=make_book([("0.499", "100")]),
        )
        jump = t.observe([m2])
        assert jump < 0.01

    def test_empty_book_skipped(self):
        t = PriceJumpTracker()
        m = make_market(yes_asks=make_book([]), no_asks=make_book([]))
        assert t.observe([m]) == 0.0

    def test_forget_market(self):
        t = PriceJumpTracker()
        m = make_market(yes_asks=make_book([("0.50", "100")]), no_asks=make_book([("0.50", "100")]))
        t.observe([m])
        assert "mkt-1" in t._prev_mid
        t.forget("mkt-1")
        assert "mkt-1" not in t._prev_mid


class TestReconcileBridge:
    def test_zero_mismatches(self):
        report = ReconcileReport(checked_positions=5, findings=[])
        assert position_mismatch_count_from_reconcile(report) == 0

    def test_counts_error_findings(self):
        report = ReconcileReport(
            checked_positions=5,
            findings=[
                ReconcileFinding(severity="error", category="capital_mismatch", detail="x"),
                ReconcileFinding(severity="warn", category="overdue", detail="y"),
                ReconcileFinding(severity="error", category="missing_opportunity", detail="z"),
            ],
        )
        # warn doesn't count, only error severity
        assert position_mismatch_count_from_reconcile(report) == 2


# ---- Network probes: exercise the failure path (offline) ----

@pytest.mark.asyncio
async def test_clock_drift_probe_returns_none_on_network_failure(monkeypatch):
    """Offline: exception → None. Does not crash the orchestrator."""
    import aiohttp

    class BadSession:
        def __init__(self, *a, **kw):
            raise aiohttp.ClientError("simulated")

        async def __aenter__(self): ...
        async def __aexit__(self, *a): ...

    monkeypatch.setattr("src.monitoring.probes.aiohttp.ClientSession", BadSession)
    result = await probe_clock_drift_seconds(timeout=1)
    assert result is None


@pytest.mark.asyncio
async def test_usdc_probe_returns_none_on_network_failure(monkeypatch):
    import aiohttp

    class BadSession:
        def __init__(self, *a, **kw):
            raise aiohttp.ClientError("simulated")

        async def __aenter__(self): ...
        async def __aexit__(self, *a): ...

    monkeypatch.setattr("src.monitoring.probes.aiohttp.ClientSession", BadSession)
    result = await probe_usdc_price_usd(timeout=1)
    assert result is None


class TestMetricsGaugesExist:
    """Regression: the 4 previously-dead gauges must exist on MetricsRegistry.

    If a future refactor renames one, this test catches it before it silently
    neuters a kill switch.
    """

    def test_required_gauges_present(self):
        from src.monitoring.metrics import MetricsRegistry

        m = MetricsRegistry()
        for name in (
            "clock_drift_seconds",
            "usdc_price_usd",
            "position_mismatch_count",
            "last_price_jump_pct",
        ):
            assert hasattr(m, name), f"MetricsRegistry must expose {name}"
            # Each gauge has `value` attribute (from _Gauge dataclass).
            assert hasattr(getattr(m, name), "value")
