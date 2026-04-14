"""SQLite state DB with WAL + synchronous=FULL.

Why synchronous=FULL on WAL: default WAL is synchronous=NORMAL, which can lose
the last few seconds of commits on crash. synchronous=FULL trades a bit of write
throughput for durability — the right call when the data is trading positions.

Tables:
  - schema_version: one-row table tracking migration state
  - opportunities: every detected opportunity (pre-allocation)
  - paper_trades: every paper fill event
  - paper_positions: open paper positions, updated on resolution
  - errors: structured error log
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from src.layer3_strategy.models import Opportunity, PaperPosition

CURRENT_SCHEMA_VERSION = 5


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- Phase 2: monitoring metrics (time-series, append-only).
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    labels TEXT  -- JSON string
);
CREATE INDEX IF NOT EXISTS ix_metrics_name_time ON metrics(name, recorded_at);

-- Phase 2: kill switch state. One row per trip event.
CREATE TABLE IF NOT EXISTS kill_switch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    trigger TEXT NOT NULL,
    mode TEXT NOT NULL,           -- 'observe' or 'enforce'
    enforced INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    provenance TEXT NOT NULL,
    reset_at TEXT,                -- when manually reset (NULL until then)
    reset_by TEXT
);
CREATE INDEX IF NOT EXISTS ix_kill_switch_occurred ON kill_switch_events(occurred_at);

-- Phase 3: paired (paper_expected vs live_actual) execution records for
-- calibration analysis. One row per live fill.
CREATE TABLE IF NOT EXISTS execution_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    paper_expected_profit_usd TEXT NOT NULL,
    paper_profit_p05_usd TEXT NOT NULL,
    paper_profit_p95_usd TEXT NOT NULL,
    paper_expected_yes_px TEXT NOT NULL,
    paper_expected_no_px TEXT NOT NULL,
    live_actual_profit_usd TEXT,       -- NULL until resolved
    live_fill_yes_px TEXT,
    live_fill_no_px TEXT,
    live_fill_latency_ms INTEGER,
    live_partial_fill INTEGER,         -- bool
    live_slippage_bps INTEGER,
    within_p5_p95 INTEGER,             -- bool; NULL until resolved
    divergence_bps INTEGER,
    explanation TEXT,
    gate TEXT NOT NULL,
    provenance TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_execution_records_opp ON execution_records(opportunity_id);

-- Phase 3 fix: persist gate state across restarts. Single-row table; upserts
-- each time the orchestrator advances a gate or records a fill.
CREATE TABLE IF NOT EXISTS gate_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
    current_gate TEXT NOT NULL,
    gate_entered_at TEXT NOT NULL,
    successful_fills_at_gate INTEGER NOT NULL DEFAULT 0,
    calibration_coverage_recent REAL,
    updated_at TEXT NOT NULL
);

-- Round A (#7): persist telegram alert timestamps so restart doesn't reset
-- the hourly rate limiter. Stored as sent_at ISO strings.
CREATE TABLE IF NOT EXISTS telegram_alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT NOT NULL,
    level TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_telegram_sent_at ON telegram_alert_log(sent_at);

-- Phase 2: kill switch active state (one row per trigger). Persisted across restarts.
CREATE TABLE IF NOT EXISTS kill_switch_state (
    trigger TEXT PRIMARY KEY,
    tripped INTEGER NOT NULL DEFAULT 0,  -- 1 = currently halting trading
    tripped_at TEXT,
    reason TEXT,
    last_observe_at TEXT,
    last_enforce_at TEXT
);

CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id TEXT PRIMARY KEY,
    strategy TEXT NOT NULL,
    platform TEXT NOT NULL,
    market_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    title TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    size_contracts TEXT NOT NULL,
    yes_fill_price TEXT NOT NULL,
    no_fill_price TEXT NOT NULL,
    gross_cost_usd TEXT NOT NULL,
    fee_cost_usd TEXT NOT NULL,
    gas_cost_usd TEXT NOT NULL,
    capital_at_risk_usd TEXT NOT NULL,
    days_to_resolution TEXT NOT NULL,
    expected_profit_usd TEXT NOT NULL,
    profit_pct_absolute TEXT NOT NULL,
    annualized_return TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    git_hash TEXT NOT NULL,
    provenance TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_opportunities_detected_at ON opportunities(detected_at);
CREATE INDEX IF NOT EXISTS ix_opportunities_market ON opportunities(market_id);

CREATE TABLE IF NOT EXISTS paper_trades (
    client_order_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    market_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    size_contracts TEXT NOT NULL,
    yes_fill_price TEXT NOT NULL,
    no_fill_price TEXT NOT NULL,
    capital_locked_usd TEXT NOT NULL,
    expected_profit_usd TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    resolution_date TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    realized_pnl_usd TEXT,
    resolved_at TEXT,
    provenance TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(opportunity_id)
);

CREATE INDEX IF NOT EXISTS ix_paper_trades_resolution ON paper_trades(resolved, resolution_date);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    context TEXT,
    provenance TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_errors_occurred_at ON errors(occurred_at);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with WAL + synchronous=FULL. Creates the file + parent dir."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)  # autocommit; explicit tx below
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables if absent, advance schema_version monotonically.

    Migration is purely additive in Phase 0→2: we only added new tables, no
    column changes. CREATE TABLE IF NOT EXISTS handles that. The version row is
    updated to CURRENT_SCHEMA_VERSION on first init or if it lags behind.
    """
    conn.executescript(SCHEMA_SQL)
    row = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
    if row is None or row["version"] < CURRENT_SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES(?, datetime('now'))",
            (CURRENT_SCHEMA_VERSION,),
        )


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
    return row["version"] if row else 0


def write_opportunity(
    conn: sqlite3.Connection, opp: Opportunity, provenance_json: str
) -> None:
    """Idempotent insert (ON CONFLICT DO NOTHING). Decimals stored as strings for
    byte-for-byte fidelity — SQLite has no Decimal type, and float storage would
    erase precision."""
    conn.execute(
        """
        INSERT OR IGNORE INTO opportunities (
            opportunity_id, strategy, platform, market_id, event_id, title,
            detected_at, size_contracts, yes_fill_price, no_fill_price,
            gross_cost_usd, fee_cost_usd, gas_cost_usd, capital_at_risk_usd,
            days_to_resolution, expected_profit_usd, profit_pct_absolute,
            annualized_return, config_hash, git_hash, provenance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            opp.opportunity_id,
            opp.strategy,
            opp.platform,
            opp.market_id,
            opp.event_id,
            opp.title,
            opp.detected_at.isoformat(),
            str(opp.size_contracts),
            str(opp.yes_fill_price),
            str(opp.no_fill_price),
            str(opp.gross_cost_usd),
            str(opp.fee_cost_usd),
            str(opp.gas_cost_usd),
            str(opp.capital_at_risk_usd),
            str(opp.days_to_resolution),
            str(opp.expected_profit_usd),
            str(opp.profit_pct_absolute),
            str(opp.annualized_return),
            opp.config_hash,
            opp.git_hash,
            provenance_json,
        ),
    )


def write_paper_trade(
    conn: sqlite3.Connection, pos: PaperPosition, provenance_json: str
) -> None:
    """Idempotent by client_order_id — repeat calls with same ID are no-ops."""
    conn.execute(
        """
        INSERT OR IGNORE INTO paper_trades (
            client_order_id, opportunity_id, platform, market_id, event_id,
            size_contracts, yes_fill_price, no_fill_price, capital_locked_usd,
            expected_profit_usd, opened_at, resolution_date, resolved,
            realized_pnl_usd, resolved_at, provenance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pos.client_order_id,
            pos.opportunity_id,
            pos.platform,
            pos.market_id,
            pos.event_id,
            str(pos.size_contracts),
            str(pos.yes_fill_price),
            str(pos.no_fill_price),
            str(pos.capital_locked_usd),
            str(pos.expected_profit_usd),
            pos.opened_at.isoformat(),
            pos.resolution_date.isoformat(),
            1 if pos.resolved else 0,
            str(pos.realized_pnl_usd) if pos.realized_pnl_usd is not None else None,
            pos.resolved_at.isoformat() if pos.resolved_at else None,
            provenance_json,
        ),
    )


def open_positions(conn: sqlite3.Connection) -> List[PaperPosition]:
    rows = conn.execute(
        "SELECT * FROM paper_trades WHERE resolved = 0"
    ).fetchall()
    return [_row_to_position(r) for r in rows]


def due_for_resolution(conn: sqlite3.Connection, now_iso: str) -> List[PaperPosition]:
    rows = conn.execute(
        "SELECT * FROM paper_trades WHERE resolved = 0 AND resolution_date <= ?",
        (now_iso,),
    ).fetchall()
    return [_row_to_position(r) for r in rows]


def mark_resolved(
    conn: sqlite3.Connection,
    client_order_id: str,
    realized_pnl_usd: Decimal,
    resolved_at_iso: str,
) -> None:
    conn.execute(
        """
        UPDATE paper_trades
        SET resolved = 1,
            realized_pnl_usd = ?,
            resolved_at = ?
        WHERE client_order_id = ?
        """,
        (str(realized_pnl_usd), resolved_at_iso, client_order_id),
    )


def log_error(
    conn: sqlite3.Connection,
    category: str,
    message: str,
    context: Optional[str],
    provenance_json: str,
    occurred_at_iso: str,
) -> None:
    conn.execute(
        "INSERT INTO errors(occurred_at, category, message, context, provenance) VALUES(?, ?, ?, ?, ?)",
        (occurred_at_iso, category, message, context, provenance_json),
    )


def total_capital_locked(conn: sqlite3.Connection) -> Decimal:
    row = conn.execute(
        "SELECT COALESCE(SUM(CAST(capital_locked_usd AS REAL)), 0) AS s "
        "FROM paper_trades WHERE resolved = 0"
    ).fetchone()
    # Store-as-string + sum-as-real is the pragmatic choice for aggregation.
    # Callers should treat this as an approximation; per-row Decimal is exact.
    return Decimal(str(row["s"]))


def realized_pnl_total(conn: sqlite3.Connection) -> Decimal:
    row = conn.execute(
        "SELECT COALESCE(SUM(CAST(realized_pnl_usd AS REAL)), 0) AS s "
        "FROM paper_trades WHERE resolved = 1"
    ).fetchone()
    return Decimal(str(row["s"]))


def _row_to_position(row) -> PaperPosition:
    from datetime import datetime

    realized = row["realized_pnl_usd"]
    resolved_at = row["resolved_at"]
    return PaperPosition(
        client_order_id=row["client_order_id"],
        opportunity_id=row["opportunity_id"],
        platform=row["platform"],
        market_id=row["market_id"],
        event_id=row["event_id"],
        size_contracts=Decimal(row["size_contracts"]),
        yes_fill_price=Decimal(row["yes_fill_price"]),
        no_fill_price=Decimal(row["no_fill_price"]),
        capital_locked_usd=Decimal(row["capital_locked_usd"]),
        expected_profit_usd=Decimal(row["expected_profit_usd"]),
        opened_at=datetime.fromisoformat(row["opened_at"]),
        resolution_date=datetime.fromisoformat(row["resolution_date"]),
        resolved=bool(row["resolved"]),
        realized_pnl_usd=Decimal(realized) if realized is not None else None,
        resolved_at=datetime.fromisoformat(resolved_at) if resolved_at else None,
    )


# ---------------- Phase 2: monitoring metrics ----------------

def write_metric(
    conn: sqlite3.Connection,
    name: str,
    value: float,
    recorded_at_iso: str,
    labels_json: Optional[str] = None,
) -> None:
    conn.execute(
        "INSERT INTO metrics(recorded_at, name, value, labels) VALUES(?, ?, ?, ?)",
        (recorded_at_iso, name, float(value), labels_json),
    )


def latest_metric(conn: sqlite3.Connection, name: str) -> Optional[float]:
    row = conn.execute(
        "SELECT value FROM metrics WHERE name = ? ORDER BY id DESC LIMIT 1",
        (name,),
    ).fetchone()
    return float(row["value"]) if row else None


def metrics_in_window(
    conn: sqlite3.Connection, name: str, since_iso: str
) -> list:
    rows = conn.execute(
        "SELECT recorded_at, value FROM metrics WHERE name = ? AND recorded_at >= ? ORDER BY id",
        (name, since_iso),
    ).fetchall()
    return [(r["recorded_at"], float(r["value"])) for r in rows]


def gc_metrics_older_than(conn: sqlite3.Connection, before_iso: str) -> int:
    """Drop metrics older than `before_iso`. Returns rows deleted."""
    cur = conn.execute("DELETE FROM metrics WHERE recorded_at < ?", (before_iso,))
    return cur.rowcount


# ---------------- Phase 2: kill switch state ----------------

def kill_switch_get_state(conn: sqlite3.Connection, trigger: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM kill_switch_state WHERE trigger = ?", (trigger,)
    ).fetchone()
    return dict(row) if row else None


def kill_switch_record_observation(
    conn: sqlite3.Connection, trigger: str, reason: str, occurred_at_iso: str, provenance_json: str
) -> None:
    """Record an observe-mode trip (would-have-fired event). Does NOT halt trading."""
    conn.execute(
        """
        INSERT INTO kill_switch_events(occurred_at, trigger, mode, enforced, reason, provenance)
        VALUES(?, ?, 'observe', 0, ?, ?)
        """,
        (occurred_at_iso, trigger, reason, provenance_json),
    )
    conn.execute(
        """
        INSERT INTO kill_switch_state(trigger, tripped, last_observe_at)
        VALUES(?, 0, ?)
        ON CONFLICT(trigger) DO UPDATE SET last_observe_at = excluded.last_observe_at
        """,
        (trigger, occurred_at_iso),
    )


def kill_switch_enforce(
    conn: sqlite3.Connection, trigger: str, reason: str, occurred_at_iso: str, provenance_json: str
) -> None:
    """Record enforcement event AND mark the trigger active. Halts trading."""
    conn.execute(
        """
        INSERT INTO kill_switch_events(occurred_at, trigger, mode, enforced, reason, provenance)
        VALUES(?, ?, 'enforce', 1, ?, ?)
        """,
        (occurred_at_iso, trigger, reason, provenance_json),
    )
    conn.execute(
        """
        INSERT INTO kill_switch_state(trigger, tripped, tripped_at, reason, last_enforce_at)
        VALUES(?, 1, ?, ?, ?)
        ON CONFLICT(trigger) DO UPDATE SET
            tripped = 1,
            tripped_at = excluded.tripped_at,
            reason = excluded.reason,
            last_enforce_at = excluded.last_enforce_at
        """,
        (trigger, occurred_at_iso, reason, occurred_at_iso),
    )


def kill_switch_reset(
    conn: sqlite3.Connection, trigger: str, reset_by: str, reset_at_iso: str
) -> bool:
    """Manual reset. Returns True if a tripped trigger was cleared, False if it
    was already clear (or unknown)."""
    state = kill_switch_get_state(conn, trigger)
    if not state or not state.get("tripped"):
        return False
    conn.execute(
        "UPDATE kill_switch_state SET tripped = 0, reason = NULL, tripped_at = NULL WHERE trigger = ?",
        (trigger,),
    )
    conn.execute(
        """
        UPDATE kill_switch_events
        SET reset_at = ?, reset_by = ?
        WHERE trigger = ? AND enforced = 1 AND reset_at IS NULL
        """,
        (reset_at_iso, reset_by, trigger),
    )
    return True


def any_kill_switch_tripped(conn: sqlite3.Connection) -> List[str]:
    """Return list of trigger names currently halting trading."""
    rows = conn.execute(
        "SELECT trigger FROM kill_switch_state WHERE tripped = 1"
    ).fetchall()
    return [r["trigger"] for r in rows]


# ---------------- Phase 3: paired execution records ----------------

def insert_execution_record(
    conn: sqlite3.Connection,
    *,
    opportunity_id: str,
    detected_at_iso: str,
    executed_at_iso: str,
    paper_expected: Decimal,
    paper_p05: Decimal,
    paper_p95: Decimal,
    paper_yes_px: Decimal,
    paper_no_px: Decimal,
    live_profit: Optional[Decimal],
    live_yes_px: Optional[Decimal],
    live_no_px: Optional[Decimal],
    live_latency_ms: Optional[int],
    live_partial_fill: Optional[bool],
    live_slippage_bps: Optional[int],
    within_ci: Optional[bool],
    divergence_bps: Optional[int],
    gate: str,
    provenance_json: str,
) -> int:
    """Write one paired record. Returns the new row id."""
    cur = conn.execute(
        """
        INSERT INTO execution_records (
            opportunity_id, detected_at, executed_at,
            paper_expected_profit_usd, paper_profit_p05_usd, paper_profit_p95_usd,
            paper_expected_yes_px, paper_expected_no_px,
            live_actual_profit_usd, live_fill_yes_px, live_fill_no_px,
            live_fill_latency_ms, live_partial_fill, live_slippage_bps,
            within_p5_p95, divergence_bps,
            gate, provenance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            opportunity_id,
            detected_at_iso,
            executed_at_iso,
            str(paper_expected),
            str(paper_p05),
            str(paper_p95),
            str(paper_yes_px),
            str(paper_no_px),
            str(live_profit) if live_profit is not None else None,
            str(live_yes_px) if live_yes_px is not None else None,
            str(live_no_px) if live_no_px is not None else None,
            live_latency_ms,
            1 if live_partial_fill else 0 if live_partial_fill is not None else None,
            live_slippage_bps,
            1 if within_ci else 0 if within_ci is not None else None,
            divergence_bps,
            gate,
            provenance_json,
        ),
    )
    return cur.lastrowid


def execution_records_since(conn: sqlite3.Connection, since_iso: str) -> List[dict]:
    rows = conn.execute(
        "SELECT * FROM execution_records WHERE executed_at >= ? ORDER BY executed_at",
        (since_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------- Round A fix #7: telegram alert log ----------------

def record_telegram_alert(conn, sent_at_iso: str, level: str) -> None:
    conn.execute(
        "INSERT INTO telegram_alert_log(sent_at, level) VALUES(?, ?)",
        (sent_at_iso, level),
    )


def count_non_critical_alerts_since(conn, since_iso: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM telegram_alert_log WHERE sent_at >= ? AND level != 'CRITICAL'",
        (since_iso,),
    ).fetchone()
    return int(row["c"] or 0)


def gc_telegram_alerts_older_than(conn, before_iso: str) -> int:
    cur = conn.execute("DELETE FROM telegram_alert_log WHERE sent_at < ?", (before_iso,))
    return cur.rowcount


# ---------------- Phase 3 fix: gate state persistence ----------------

def save_gate_state(
    conn: sqlite3.Connection,
    *,
    current_gate: str,
    gate_entered_at_iso: str,
    successful_fills: int,
    calibration_coverage: Optional[float],
    updated_at_iso: str,
) -> None:
    conn.execute(
        """
        INSERT INTO gate_state(
            id, current_gate, gate_entered_at, successful_fills_at_gate,
            calibration_coverage_recent, updated_at
        ) VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            current_gate = excluded.current_gate,
            gate_entered_at = excluded.gate_entered_at,
            successful_fills_at_gate = excluded.successful_fills_at_gate,
            calibration_coverage_recent = excluded.calibration_coverage_recent,
            updated_at = excluded.updated_at
        """,
        (
            current_gate,
            gate_entered_at_iso,
            int(successful_fills),
            float(calibration_coverage) if calibration_coverage is not None else None,
            updated_at_iso,
        ),
    )


def load_gate_state(conn: sqlite3.Connection) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM gate_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def calibration_coverage_in_window(
    conn: sqlite3.Connection, since_iso: str
) -> Optional[float]:
    """Fraction of resolved execution records whose live_pnl fell in [p05, p95].

    Returns None if there are no resolved records in the window.
    """
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN within_p5_p95 = 1 THEN 1 ELSE 0 END) AS within,
            SUM(CASE WHEN within_p5_p95 IS NOT NULL THEN 1 ELSE 0 END) AS resolved
        FROM execution_records
        WHERE executed_at >= ?
        """,
        (since_iso,),
    ).fetchone()
    resolved = (row["resolved"] or 0)
    if resolved == 0:
        return None
    return (row["within"] or 0) / resolved
