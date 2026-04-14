"""Chaos tests — deliberate failure injection per phase-2 doc.

Each test causes a specific failure condition and asserts the system responds
correctly. These are the gating tests for any production deploy.

Real-world chaos (network outages, full disk, etc.) can't be perfectly simulated
in unit tests — the orchestrator-level integration tests in CI cover the
software-injectable failure paths.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.layer3_strategy.intra_market import compute_opportunity
from src.layer3_strategy.models import Allocation
from src.layer4_execution.paper import PaperExecutor
from src.matching.event_map import load_event_map
from src.monitoring.metrics import MetricsRegistry
from src.risk import rules
from src.risk.policy import (
    PolicyConfig,
    PolicyEngine,
    PolicyMode,
    RuleConfig,
    TriggerName,
)
from src.risk.reconcile import reconcile_paper_state
from src.risk.recovery import perform_recovery
from src.storage import state_db
from tests.conftest import make_book, make_market


def _open_db(tmp_path):
    conn = state_db.connect(tmp_path / "state.db")
    state_db.init_schema(conn)
    return conn


# Chaos 1: SIGKILL during fill — re-running the same allocation must not duplicate.
def test_chaos_duplicate_fill_after_simulated_crash(tmp_path, strategy_ctx):
    conn = _open_db(tmp_path)
    m = make_market(
        yes_asks=make_book([("0.40", "500")]),
        no_asks=make_book([("0.46", "500")]),
        days_to_resolution=30,
    )
    opp = compute_opportunity(m, strategy_ctx)
    assert opp is not None
    alloc = Allocation(
        opportunity=opp,
        allocated_capital_usd=opp.capital_at_risk_usd,
        allocated_size_contracts=opp.size_contracts,
        allocation_reason="full",
    )
    ex = PaperExecutor(conn=conn, provenance_json="{}")
    ex.fill_with_resolution(alloc, m.resolution_date)

    # Simulate crash + restart: open new connection, replay fill → idempotent.
    conn.close()
    conn2 = _open_db(tmp_path)
    ex2 = PaperExecutor(conn=conn2, provenance_json="{}")
    ex2.fill_with_resolution(alloc, m.resolution_date)
    assert len(state_db.open_positions(conn2)) == 1


# Chaos 2: DB integrity — corrupt a paper_trades row, ensure reconcile catches it.
def test_chaos_capital_mismatch_caught(tmp_path, strategy_ctx):
    conn = _open_db(tmp_path)
    m = make_market(
        yes_asks=make_book([("0.40", "500")]),
        no_asks=make_book([("0.46", "500")]),
        days_to_resolution=30,
    )
    opp = compute_opportunity(m, strategy_ctx)
    alloc = Allocation(
        opportunity=opp,
        allocated_capital_usd=opp.capital_at_risk_usd,
        allocated_size_contracts=opp.size_contracts,
        allocation_reason="full",
    )
    ex = PaperExecutor(conn=conn, provenance_json="{}")
    pos = ex.fill_with_resolution(alloc, m.resolution_date)
    # Inject corruption: tamper with capital_locked.
    conn.execute(
        "UPDATE paper_trades SET capital_locked_usd = '999999' WHERE client_order_id = ?",
        (pos.client_order_id,),
    )
    rep = reconcile_paper_state(conn)
    assert rep.mismatch_count >= 1
    assert any("capital_mismatch" in f.category for f in rep.findings)


# Chaos 3: Modify event_map mid-run — the drift rule must fire.
def test_chaos_event_map_drift_detection(tmp_path):
    em_path = tmp_path / "event_map.yaml"
    em_path.write_text("schema_version: 1\npairs: []\n")
    em1 = load_event_map(em_path)
    em_path.write_text(
        """
schema_version: 1
pairs:
  - pair_id: new
    polymarket_market_id: "0xabc"
    kalshi_market_ticker: "T"
    verified_by: "x"
    verified_date: "2026-04-01"
    trading_enabled: false
    edge_cases_reviewed:
      - { scenario: "1", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "2", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "3", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "4", polymarket: "YES", kalshi: "YES", divergent: false }
      - { scenario: "5", polymarket: "YES", kalshi: "YES", divergent: true }
"""
    )
    em2 = load_event_map(em_path)
    assert em1.content_hash != em2.content_hash

    m = MetricsRegistry()
    decision = rules.event_map_drift(
        m, {"expected_hash": em1.content_hash, "current_hash": em2.content_hash}
    )
    from src.risk.policy import Verdict

    assert decision.verdict == Verdict.TRIP


# Chaos 4: Layer stall — heartbeat ages, layer_stall trips.
def test_chaos_layer_stall(tmp_path):
    conn = _open_db(tmp_path)
    cfg = PolicyConfig(
        rules={
            TriggerName.LAYER_STALL: RuleConfig(
                name=TriggerName.LAYER_STALL, mode=PolicyMode.ENFORCE
            )
        }
    )
    engine = PolicyEngine(conn, cfg, "{}")
    engine.register(
        TriggerName.LAYER_STALL,
        rules.layer_stall,
        {"max_silence_seconds": 60, "layers": ["layer1"]},
    )
    m = MetricsRegistry()
    m.heartbeat("layer1", now=datetime.now(timezone.utc) - timedelta(seconds=180))
    engine.evaluate_all(m)
    assert "layer_stall" in engine.should_halt()


# Chaos 5: Manual kill via touch file.
def test_chaos_manual_kill_file(tmp_path):
    conn = _open_db(tmp_path)
    kill_path = tmp_path / "killfile"

    cfg = PolicyConfig(
        rules={TriggerName.MANUAL: RuleConfig(name=TriggerName.MANUAL, mode=PolicyMode.ENFORCE)}
    )
    engine = PolicyEngine(conn, cfg, "{}")
    engine.register(TriggerName.MANUAL, rules.manual_kill, {"kill_file_path": str(kill_path)})

    m = MetricsRegistry()
    engine.evaluate_all(m)
    assert engine.should_halt() == []  # no kill file yet

    kill_path.touch()
    # Reset cooldown for test by clearing internal state.
    engine._last_trip_at.clear()
    engine.evaluate_all(m)
    assert "manual" in engine.should_halt()


# Chaos 6: Restart with kill switch tripped — recovery must report not-safe-to-trade.
def test_chaos_restart_with_tripped_switch(tmp_path):
    conn = _open_db(tmp_path)
    state_db.kill_switch_enforce(
        conn, "manual", "test", datetime.now(timezone.utc).isoformat(), "{}"
    )
    conn.close()
    # Simulate restart.
    conn2 = state_db.connect(tmp_path / "state.db")
    rep = perform_recovery(conn2)
    assert not rep.safe_to_trade
    assert "manual" in rep.tripped_triggers


# Chaos 7: Clock drift — drift gauge above threshold trips.
def test_chaos_clock_drift(tmp_path):
    conn = _open_db(tmp_path)
    cfg = PolicyConfig(
        rules={
            TriggerName.CLOCK_DRIFT: RuleConfig(
                name=TriggerName.CLOCK_DRIFT, mode=PolicyMode.ENFORCE
            )
        }
    )
    engine = PolicyEngine(conn, cfg, "{}")
    engine.register(TriggerName.CLOCK_DRIFT, rules.clock_drift, {"max_drift_seconds": 2})
    m = MetricsRegistry()
    m.clock_drift_seconds.set(10.0)
    engine.evaluate_all(m)
    assert "clock_drift" in engine.should_halt()


# Chaos 8: Exception flood — rate trigger fires.
def test_chaos_exception_flood(tmp_path):
    conn = _open_db(tmp_path)
    cfg = PolicyConfig(
        rules={
            TriggerName.UNHANDLED_EXCEPTION_RATE: RuleConfig(
                name=TriggerName.UNHANDLED_EXCEPTION_RATE, mode=PolicyMode.ENFORCE
            )
        }
    )
    engine = PolicyEngine(conn, cfg, "{}")
    engine.register(
        TriggerName.UNHANDLED_EXCEPTION_RATE,
        rules.unhandled_exception_rate,
        {"max_per_5min": 5},
    )
    m = MetricsRegistry()
    for _ in range(20):
        m.exceptions_per_5min.add(1)
    engine.evaluate_all(m)
    assert "unhandled_exception_rate" in engine.should_halt()


# Chaos 9: USDC depeg — gauge below threshold trips.
def test_chaos_usdc_depeg(tmp_path):
    conn = _open_db(tmp_path)
    cfg = PolicyConfig(
        rules={TriggerName.USDC_DEPEG: RuleConfig(name=TriggerName.USDC_DEPEG, mode=PolicyMode.ENFORCE)}
    )
    engine = PolicyEngine(conn, cfg, "{}")
    engine.register(TriggerName.USDC_DEPEG, rules.usdc_depeg, {"min_price_usd": 0.995})
    m = MetricsRegistry()
    m.usdc_price_usd = type("G", (), {"value": 0.95})()
    engine.evaluate_all(m)
    assert "usdc_depeg" in engine.should_halt()
