"""Tests for state_db schema + paper executor end-to-end flow."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from src.layer3_strategy.intra_market import compute_opportunity
from src.layer3_strategy.models import Allocation
from src.layer4_execution.paper import PaperExecutor, client_order_id
from src.storage import state_db
from tests.conftest import make_book, make_market


def _open_db(tmp_path: Path):
    conn = state_db.connect(tmp_path / "state.db")
    state_db.init_schema(conn)
    return conn


def test_schema_init_and_version(tmp_path):
    conn = _open_db(tmp_path)
    assert state_db.current_schema_version(conn) == state_db.CURRENT_SCHEMA_VERSION


def test_write_opportunity_is_idempotent(tmp_path, strategy_ctx):
    conn = _open_db(tmp_path)
    m = make_market(
        yes_asks=make_book([("0.45", "500")]),
        no_asks=make_book([("0.48", "500")]),
        days_to_resolution=30,
    )
    opp = compute_opportunity(m, strategy_ctx)
    assert opp is not None
    state_db.write_opportunity(conn, opp, "{}")
    state_db.write_opportunity(conn, opp, "{}")  # duplicate, should no-op
    row = conn.execute("SELECT COUNT(*) AS c FROM opportunities").fetchone()
    assert row["c"] == 1


def test_paper_executor_fills_and_resolves(tmp_path, strategy_ctx):
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

    executor = PaperExecutor(conn=conn, provenance_json="{}")
    pos = executor.fill_with_resolution(alloc, m.resolution_date)

    # Position is recorded, unresolved.
    open_pos = state_db.open_positions(conn)
    assert len(open_pos) == 1
    assert open_pos[0].client_order_id == pos.client_order_id
    assert open_pos[0].resolved is False

    # Double-fill same allocation → idempotent: no second row.
    executor.fill_with_resolution(alloc, m.resolution_date)
    assert len(state_db.open_positions(conn)) == 1

    # Force resolution by advancing the clock.
    future = m.resolution_date + timedelta(hours=1)
    resolved = executor.resolve_due_positions(now=future)
    assert len(resolved) == 1

    assert state_db.realized_pnl_total(conn) == pos.expected_profit_usd
    assert state_db.open_positions(conn) == []


def test_client_order_id_is_deterministic(strategy_ctx):
    m = make_market(
        yes_asks=make_book([("0.45", "500")]),
        no_asks=make_book([("0.48", "500")]),
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
    assert client_order_id(alloc) == client_order_id(alloc)
