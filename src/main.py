"""Phase 0 orchestrator. Wires Layers 1 → 2 → 3 → 4 and runs a CLI dashboard.

Run:
    python -m src.main --config config.yaml

The orchestrator is intentionally small. Everything non-trivial lives in the
layer modules. The main loop is:

    1. Fetch a snapshot (Layer 1 → Parquet + in-memory)
    2. Run detection on it (Layer 3)
    3. Allocate capital (Layer 3)
    4. Fill allocations (Layer 4 → SQLite)
    5. Resolve any due positions
    6. Print dashboard
    7. Sleep to next cycle
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import structlog

from src.config import load_config
from src.layer1_data_recording.parquet_writer import DailyParquetWriter
from src.layer1_data_recording.polymarket_fetcher import PolymarketFetcher
from src.layer3_strategy.allocation import allocate_capital
from src.layer3_strategy.intra_market import StrategyContext, find_opportunities
from src.layer3_strategy.models import Market
from src.layer4_execution.paper import PaperExecutor
from src.provenance import build_bundle
from src.storage import state_db

logger = structlog.get_logger(__name__)


def _setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
    )


async def run(config_path: str) -> None:
    _setup_logging()
    cfg = load_config(config_path)

    provenance = build_bundle(cfg.raw)
    provenance_json = provenance.serialize()

    logger.info(
        "startup",
        mode=cfg.mode,
        git=provenance.git_commit,
        config_hash=provenance.config_hash,
        dirty=provenance.git_dirty,
    )

    # Storage.
    conn = state_db.connect(cfg.storage.state_db_path)
    state_db.init_schema(conn)

    # Layer 1.
    fetcher = PolymarketFetcher(
        gamma_base_url=cfg.polymarket.gamma_base_url,
        clob_base_url=cfg.polymarket.clob_base_url,
        fee_bps=cfg.polymarket.fee_bps,
        timeout_seconds=cfg.polymarket.request_timeout_seconds,
        max_concurrency=cfg.polymarket.max_concurrent_requests,
    )
    writer = DailyParquetWriter(
        base_dir=cfg.storage.snapshots_dir,
        platform="polymarket",
        flush_interval_seconds=cfg.storage.parquet_flush_interval_seconds,
    )

    # Layer 3 ctx.
    ctx = StrategyContext(
        config=cfg.intra_market,
        gas_cost_usd=cfg.polymarket.gas_estimate_usd,
        config_hash=provenance.config_hash,
        git_hash=provenance.git_commit,
    )

    # Layer 4.
    executor = PaperExecutor(conn=conn, provenance_json=provenance_json)

    stop = asyncio.Event()

    def _signal_handler(*_):
        logger.info("shutdown_signal_received")
        stop.set()

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    except NotImplementedError:
        # Windows / non-unix — signal handlers not available. Skip gracefully.
        pass

    loop_num = 0
    try:
        while not stop.is_set():
            loop_num += 1
            cycle_start = datetime.now(timezone.utc)

            # 1. Fetch.
            try:
                markets = await fetcher.fetch_snapshot()
            except Exception as e:
                logger.error("fetch_failed", error=str(e))
                state_db.log_error(
                    conn,
                    category="fetch",
                    message=str(e),
                    context=None,
                    provenance_json=provenance_json,
                    occurred_at_iso=datetime.now(timezone.utc).isoformat(),
                )
                markets = []

            # 2. Persist snapshot.
            if markets:
                try:
                    await writer.write_many(markets)
                except Exception as e:
                    logger.error("parquet_write_failed", error=str(e))

            # 3. Detect.
            opportunities = find_opportunities(markets, ctx) if markets else []

            # 4. Allocate.
            reserved = state_db.total_capital_locked(conn)
            markets_by_id: Dict[str, Market] = {m.market_id: m for m in markets}
            allocations = allocate_capital(
                opportunities,
                markets_by_id,
                ctx,
                cfg.allocation,
                reserved_capital_usd=reserved,
            )

            # 5. Fill paper trades.
            for alloc in allocations:
                market = markets_by_id.get(alloc.opportunity.market_id)
                if market is None:
                    continue
                try:
                    executor.fill_with_resolution(alloc, market.resolution_date)
                except Exception as e:
                    logger.error(
                        "paper_fill_failed",
                        opportunity_id=alloc.opportunity.opportunity_id,
                        error=str(e),
                    )

            # 6. Resolve any due positions.
            executor.resolve_due_positions()

            # 7. Dashboard.
            _print_dashboard(
                loop_num=loop_num,
                markets=markets,
                opportunities=opportunities,
                allocations=allocations,
                conn=conn,
                provenance=provenance,
            )

            # 8. Flush Parquet periodically.
            await writer.flush()

            # 9. Sleep until next cycle.
            elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
            remaining = max(0.0, cfg.polymarket.poll_interval_seconds - elapsed)
            try:
                await asyncio.wait_for(stop.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass
    finally:
        await writer.close()
        conn.close()


def _print_dashboard(
    *,
    loop_num: int,
    markets,
    opportunities,
    allocations,
    conn,
    provenance,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    realized = state_db.realized_pnl_total(conn)
    locked = state_db.total_capital_locked(conn)
    lines = [
        f"[{now}] Loop #{loop_num:,}",
        f"Markets scanned: {len(markets)}",
        f"Opportunities detected: {len(opportunities)}",
        f"Allocations this cycle: {len(allocations)}",
        f"Capital locked: ${locked:.2f}  |  Realized PnL: ${realized:.2f}",
        f"Git: {provenance.git_commit}  |  Config: {provenance.config_hash}",
    ]
    if opportunities:
        top = sorted(opportunities, key=lambda o: -o.annualized_return)[:3]
        for i, o in enumerate(top, 1):
            lines.append(
                f"  #{i} {o.title[:50]:<50} size={o.size_contracts} "
                f"ann={o.annualized_return:.2%} days={o.days_to_resolution:.1f}"
            )
    print("\n".join(lines), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 0 orchestrator")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    args = parser.parse_args()

    if not Path(args.config).exists():
        print(f"config not found: {args.config}", file=sys.stderr)
        sys.exit(2)

    try:
        asyncio.run(run(args.config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
