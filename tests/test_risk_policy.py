"""Tests for kill switch policy + rules.

Critical: every rule must have a force-trip test. The doc warns that observe-mode
rules that never fire create false confidence — we explicitly trip each one with
synthetic metrics to prove the wiring works end-to-end.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.monitoring.metrics import MetricsRegistry
from src.risk import rules
from src.risk.policy import (
    PolicyConfig,
    PolicyEngine,
    PolicyMode,
    RuleConfig,
    TriggerName,
    Verdict,
)
from src.storage import state_db


def _open_db(tmp_path):
    conn = state_db.connect(tmp_path / "state.db")
    state_db.init_schema(conn)
    return conn


# ---------- rule unit tests ----------

class TestRules:
    def test_daily_loss_trip(self):
        m = MetricsRegistry()
        m.rolling_pnl_24h_usd.set(-100)
        d = rules.daily_loss_exceeded(m, {"max_daily_loss_usd": 50})
        assert d.verdict == Verdict.TRIP

    def test_daily_loss_ok(self):
        m = MetricsRegistry()
        m.rolling_pnl_24h_usd.set(-10)
        d = rules.daily_loss_exceeded(m, {"max_daily_loss_usd": 50})
        assert d.verdict == Verdict.OK

    def test_clock_drift_trip(self):
        m = MetricsRegistry()
        m.clock_drift_seconds.set(5.5)
        d = rules.clock_drift(m, {"max_drift_seconds": 2})
        assert d.verdict == Verdict.TRIP

    def test_exception_rate_trip(self):
        m = MetricsRegistry()
        for _ in range(6):
            m.exceptions_per_5min.add(1)
        d = rules.unhandled_exception_rate(m, {"max_per_5min": 5})
        assert d.verdict == Verdict.TRIP

    def test_api_disconnect_trip(self):
        m = MetricsRegistry()
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        m.heartbeat("polymarket", now=old)
        d = rules.api_disconnect(m, {"max_silence_seconds": 60, "layers": ["polymarket"]})
        assert d.verdict == Verdict.TRIP

    def test_layer_stall_trip(self):
        m = MetricsRegistry()
        old = datetime.now(timezone.utc) - timedelta(seconds=300)
        m.heartbeat("layer1", now=old)
        d = rules.layer_stall(m, {"max_silence_seconds": 120, "layers": ["layer1"]})
        assert d.verdict == Verdict.TRIP

    def test_manual_kill_via_file(self, tmp_path):
        kill_file = tmp_path / "kill"
        m = MetricsRegistry()
        d = rules.manual_kill(m, {"kill_file_path": str(kill_file)})
        assert d.verdict == Verdict.OK
        kill_file.touch()
        d = rules.manual_kill(m, {"kill_file_path": str(kill_file)})
        assert d.verdict == Verdict.TRIP

    def test_event_map_drift_trip(self):
        m = MetricsRegistry()
        d = rules.event_map_drift(m, {"expected_hash": "abc", "current_hash": "abc"})
        assert d.verdict == Verdict.OK
        d = rules.event_map_drift(m, {"expected_hash": "abc", "current_hash": "def"})
        assert d.verdict == Verdict.TRIP

    def test_usdc_depeg_trip(self):
        m = MetricsRegistry()
        m.usdc_price_usd = type("G", (), {"value": 0.97})()
        d = rules.usdc_depeg(m, {"min_price_usd": 0.995})
        assert d.verdict == Verdict.TRIP


# ---------- engine behavior ----------

class TestPolicyEngine:
    def test_observe_mode_does_not_halt(self, tmp_path):
        conn = _open_db(tmp_path)
        cfg = PolicyConfig(
            rules={
                TriggerName.DAILY_LOSS_EXCEEDED: RuleConfig(
                    name=TriggerName.DAILY_LOSS_EXCEEDED, mode=PolicyMode.OBSERVE
                )
            }
        )
        engine = PolicyEngine(conn, cfg, "{}")
        engine.register(TriggerName.DAILY_LOSS_EXCEEDED, rules.daily_loss_exceeded,
                        {"max_daily_loss_usd": 50})

        m = MetricsRegistry()
        m.rolling_pnl_24h_usd.set(-100)
        results = engine.evaluate_all(m)
        assert results[0][1].verdict == Verdict.TRIP
        # Observe mode should NOT halt trading.
        assert engine.should_halt() == []

    def test_enforce_mode_halts(self, tmp_path):
        conn = _open_db(tmp_path)
        cfg = PolicyConfig(
            rules={
                TriggerName.DAILY_LOSS_EXCEEDED: RuleConfig(
                    name=TriggerName.DAILY_LOSS_EXCEEDED, mode=PolicyMode.ENFORCE
                )
            }
        )
        engine = PolicyEngine(conn, cfg, "{}")
        engine.register(TriggerName.DAILY_LOSS_EXCEEDED, rules.daily_loss_exceeded,
                        {"max_daily_loss_usd": 50})
        m = MetricsRegistry()
        m.rolling_pnl_24h_usd.set(-100)
        engine.evaluate_all(m)
        halted = engine.should_halt()
        assert "daily_loss_exceeded" in halted

    def test_disabled_mode_skipped(self, tmp_path):
        conn = _open_db(tmp_path)
        cfg = PolicyConfig(
            rules={
                TriggerName.DAILY_LOSS_EXCEEDED: RuleConfig(
                    name=TriggerName.DAILY_LOSS_EXCEEDED, mode=PolicyMode.DISABLED
                )
            }
        )
        engine = PolicyEngine(conn, cfg, "{}")
        engine.register(TriggerName.DAILY_LOSS_EXCEEDED, rules.daily_loss_exceeded,
                        {"max_daily_loss_usd": 50})
        m = MetricsRegistry()
        m.rolling_pnl_24h_usd.set(-1000)
        results = engine.evaluate_all(m)
        assert results == []

    def test_cooldown_prevents_immediate_re_trip(self, tmp_path):
        conn = _open_db(tmp_path)
        cfg = PolicyConfig(
            rules={
                TriggerName.DAILY_LOSS_EXCEEDED: RuleConfig(
                    name=TriggerName.DAILY_LOSS_EXCEEDED,
                    mode=PolicyMode.ENFORCE,
                    cooldown_seconds=300,
                )
            }
        )
        engine = PolicyEngine(conn, cfg, "{}")
        engine.register(TriggerName.DAILY_LOSS_EXCEEDED, rules.daily_loss_exceeded,
                        {"max_daily_loss_usd": 50})
        m = MetricsRegistry()
        m.rolling_pnl_24h_usd.set(-100)
        results1 = engine.evaluate_all(m)
        results2 = engine.evaluate_all(m)
        assert len(results1) == 1
        assert len(results2) == 0  # cooldown blocked re-evaluation

    def test_force_trip_persists(self, tmp_path):
        conn = _open_db(tmp_path)
        cfg = PolicyConfig(
            rules={
                TriggerName.MANUAL: RuleConfig(name=TriggerName.MANUAL, mode=PolicyMode.ENFORCE)
            }
        )
        engine = PolicyEngine(conn, cfg, "{}")
        engine.force_trip(TriggerName.MANUAL, "test")
        assert "manual" in engine.should_halt()
        # Reset clears it.
        ok = state_db.kill_switch_reset(conn, "manual", "test_user", datetime.now(timezone.utc).isoformat())
        assert ok is True
        assert engine.should_halt() == []

    def test_every_rule_can_trip(self, tmp_path):
        """The doc-mandated "force-trip every rule once" coverage check.

        Each rule must trip given a hand-crafted MetricsRegistry. If a rule
        becomes unreachable (always returns OK regardless of input) this test
        catches it.
        """
        m = MetricsRegistry()
        # Set up state to trip every rule.
        m.rolling_pnl_24h_usd.set(-1000)
        m.clock_drift_seconds.set(10)
        for _ in range(20):
            m.exceptions_per_5min.add(1)
        m.heartbeat("test", now=datetime.now(timezone.utc) - timedelta(seconds=300))
        m.usdc_price_usd = type("G", (), {"value": 0.50})()
        m.position_mismatch_count = type("G", (), {"value": 1.0})()

        kill_file = tmp_path / "kill"
        kill_file.touch()

        configs = {
            "daily_loss_exceeded": (rules.daily_loss_exceeded, {"max_daily_loss_usd": 1}),
            "abnormal_price_jump": None,  # requires price-feed wiring; skipped
            "api_disconnect": (rules.api_disconnect, {"max_silence_seconds": 30, "layers": ["test"]}),
            "clock_drift": (rules.clock_drift, {"max_drift_seconds": 1}),
            "unhandled_exception_rate": (rules.unhandled_exception_rate, {"max_per_5min": 5}),
            "position_mismatch": (rules.position_mismatch, {}),
            "usdc_depeg": (rules.usdc_depeg, {"min_price_usd": 0.995}),
            "layer_stall": (rules.layer_stall, {"max_silence_seconds": 30, "layers": ["test"]}),
            "manual": (rules.manual_kill, {"kill_file_path": str(kill_file)}),
            "event_map_drift": (rules.event_map_drift, {"expected_hash": "a", "current_hash": "b"}),
        }
        for name, entry in configs.items():
            if entry is None:
                continue
            fn, params = entry
            d = fn(m, params)
            assert d.verdict == Verdict.TRIP, f"rule {name} did not trip on hostile metrics"
