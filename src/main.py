"""Phase 0 + Phase 1 + Phase 2 orchestrator.

Wires everything: layers 1-4, Kalshi, cross-market detection, adverse selection,
and the full Phase 2 stack — monitoring, kill switch framework, reconciliation,
Telegram alerts (stub mode when not configured), health endpoint, crash
recovery with a state-loaded gate before the first scan cycle.

Run:
    python -m src.main --config config.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

import structlog

from src.alerts.telegram import AlertLevel, TelegramAlerter, TelegramConfig
from src.config import Config, load_config
from src.layer1_data_recording.kalshi_fetcher import KalshiFetcher
from src.layer1_data_recording.parquet_writer import DailyParquetWriter
from src.layer1_data_recording.polymarket_fetcher import PolymarketFetcher
from src.layer3_strategy.adverse_selection import (
    NewsWindow,
    OpportunityHistory,
    apply_filters,
)
from src.layer3_strategy.allocation import allocate_capital
from src.layer3_strategy.cross_market import (
    CrossMarketContext,
    find_cross_opportunities,
)
from src.layer3_strategy.intra_market import StrategyContext, find_opportunities
from src.layer3_strategy.models import Market, Opportunity
from src.layer4_execution.paper import PaperExecutor
from src.matching.event_map import EventMap, load_event_map
from src.monitoring.http_server import HealthServer
from src.monitoring.metrics import MetricsRegistry, persist_snapshot
from src.provenance import build_bundle
from src.reports import CycleReport, render_cycle
from src.risk import rules as risk_rules
from src.risk.policy import (
    PolicyConfig,
    PolicyEngine,
    PolicyMode,
    RuleConfig,
    TriggerName,
)
from src.risk.recovery import perform_recovery
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


def _build_news_windows(cfg: Config) -> List[NewsWindow]:
    if cfg.adverse_selection is None:
        return []
    return [
        NewsWindow(
            topic_tags=tuple(w.topic_tags),
            blackout_minutes_before=w.blackout_minutes_before,
            blackout_minutes_after=w.blackout_minutes_after,
        )
        for w in cfg.adverse_selection.news_windows
    ]


def _topic_tags_for(opp: Opportunity, event_map: EventMap) -> List[str]:
    """Resolve topic tags for an opportunity from the event map (cross only)."""
    if opp.strategy != "cross_market":
        return []
    p = event_map.by_id(opp.event_id)
    return list(p.topic_tags) if p else []


_NAME_TO_TRIGGER = {t.value: t for t in TriggerName}
_RULES_BY_NAME = risk_rules.ALL_RULES


def _build_policy_engine(
    conn, cfg: Config, provenance_json: str, event_map_hash: str
) -> Optional[PolicyEngine]:
    if cfg.risk is None:
        return None
    pc = PolicyConfig(
        rules={
            _NAME_TO_TRIGGER[name]: RuleConfig(
                name=_NAME_TO_TRIGGER[name],
                mode=PolicyMode(rc.mode),
                cooldown_seconds=cfg.risk.default_cooldown_seconds,
            )
            for name, rc in cfg.risk.rules.items()
            if name in _NAME_TO_TRIGGER
        },
        default_mode=PolicyMode(cfg.risk.default_mode),
        default_cooldown_seconds=cfg.risk.default_cooldown_seconds,
    )
    engine = PolicyEngine(conn, pc, provenance_json)
    for name, rc in cfg.risk.rules.items():
        if name not in _RULES_BY_NAME or name not in _NAME_TO_TRIGGER:
            continue
        params = dict(rc.params)
        if name == "event_map_drift":
            # Inject expected hash at startup; current_hash is refreshed each cycle.
            params.setdefault("expected_hash", event_map_hash)
            params.setdefault("current_hash", event_map_hash)
        engine.register(_NAME_TO_TRIGGER[name], _RULES_BY_NAME[name], params)
    return engine


async def run(config_path: str) -> None:
    _setup_logging()
    cfg = load_config(config_path)
    provenance = build_bundle(cfg.raw)
    provenance_json = provenance.serialize()

    event_map_path = cfg.event_map_path or "event_map.yaml"
    event_map = load_event_map(event_map_path)

    logger.info(
        "startup",
        mode=cfg.mode,
        git=provenance.git_commit,
        config_hash=provenance.config_hash,
        dirty=provenance.git_dirty,
        event_map_hash=event_map.content_hash,
        event_map_pairs=len(event_map.pairs),
        event_map_enabled=len(event_map.enabled()),
        kalshi_enabled=cfg.kalshi is not None,
    )

    conn = state_db.connect(cfg.storage.state_db_path)
    state_db.init_schema(conn)

    poly_fetcher = PolymarketFetcher(
        gamma_base_url=cfg.polymarket.gamma_base_url,
        clob_base_url=cfg.polymarket.clob_base_url,
        fee_bps=cfg.polymarket.fee_bps,
        timeout_seconds=cfg.polymarket.request_timeout_seconds,
        max_concurrency=cfg.polymarket.max_concurrent_requests,
    )
    poly_writer = DailyParquetWriter(
        base_dir=cfg.storage.snapshots_dir,
        platform="polymarket",
        flush_interval_seconds=cfg.storage.parquet_flush_interval_seconds,
    )

    kalshi_fetcher = None
    kalshi_writer = None
    if cfg.kalshi is not None:
        kalshi_fetcher = KalshiFetcher(
            base_url=cfg.kalshi.base_url,
            fee_bps=cfg.kalshi.fee_bps,
            api_key=os.environ.get(cfg.kalshi.api_key_env),
            timeout_seconds=cfg.kalshi.request_timeout_seconds,
            max_concurrency=cfg.kalshi.max_concurrent_requests,
            markets_limit=cfg.kalshi.markets_limit,
        )
        kalshi_writer = DailyParquetWriter(
            base_dir=cfg.storage.snapshots_dir,
            platform="kalshi",
            flush_interval_seconds=cfg.storage.parquet_flush_interval_seconds,
        )

    intra_ctx = StrategyContext(
        config=cfg.intra_market,
        gas_cost_usd=cfg.polymarket.gas_estimate_usd,
        config_hash=provenance.config_hash,
        git_hash=provenance.git_commit,
    )
    cross_ctx = None
    if cfg.cross_market is not None:
        cross_ctx = CrossMarketContext(
            intra=intra_ctx,
            cross_min_annualized_return=cfg.cross_market.min_annualized_return,
            polymarket_gas_usd=cfg.polymarket.gas_estimate_usd,
            kalshi_gas_usd=Decimal(0),
            config_hash=provenance.config_hash,
            git_hash=provenance.git_commit,
        )

    executor = PaperExecutor(conn=conn, provenance_json=provenance_json)

    intra_history = OpportunityHistory()
    cross_history = OpportunityHistory()
    news_windows = _build_news_windows(cfg)

    # Phase 2: monitoring + risk + alerts + health.
    metrics = MetricsRegistry()
    policy = _build_policy_engine(conn, cfg, provenance_json, event_map.content_hash)

    alerter = TelegramAlerter(
        TelegramConfig(
            bot_token=os.environ.get(cfg.telegram.bot_token_env) if cfg.telegram else None,
            chat_id=os.environ.get(cfg.telegram.chat_id_env) if cfg.telegram else None,
            max_per_hour_non_critical=(cfg.telegram.max_per_hour_non_critical if cfg.telegram else 5),
        )
    )

    # Startup safety: perform_recovery reads kill-switch state; if tripped and
    # enforced, refuse to scan until manually reset.
    recovery = perform_recovery(conn)
    logger.info(
        "recovery",
        safe_to_trade=recovery.safe_to_trade,
        tripped=recovery.tripped_triggers,
        reconcile_errors=recovery.reconcile_errors,
        open_positions=recovery.open_positions,
    )
    if not recovery.safe_to_trade:
        await alerter.send(
            AlertLevel.CRITICAL,
            f"Startup halted: tripped={recovery.tripped_triggers} reconcile_errors={recovery.reconcile_errors}. "
            f"Reset via scripts/kill_switch_reset.py before resuming.",
        )

    health: Optional[HealthServer] = None
    if cfg.monitoring is not None:
        health = HealthServer(
            metrics, conn, mode=cfg.mode, port=cfg.monitoring.health_port
        )
        try:
            await health.start()
            logger.info("health_endpoint_started", port=cfg.monitoring.health_port)
        except Exception as e:
            logger.warning("health_endpoint_failed", error=str(e))
            health = None

    stop = asyncio.Event()

    def _signal_handler(*_):
        logger.info("shutdown_signal_received")
        stop.set()

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    except NotImplementedError:
        pass

    loop_num = 0
    try:
        while not stop.is_set():
            loop_num += 1
            cycle_start = datetime.now(timezone.utc)
            cycle = CycleReport()

            fetch_tasks = [poly_fetcher.fetch_snapshot()]
            if kalshi_fetcher is not None:
                fetch_tasks.append(kalshi_fetcher.fetch_snapshot())
            results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

            poly_markets: List[Market] = (
                results[0] if (results and not isinstance(results[0], Exception)) else []
            )
            kalshi_markets: List[Market] = (
                results[1] if (
                    kalshi_fetcher is not None
                    and len(results) > 1
                    and not isinstance(results[1], Exception)
                ) else []
            )
            cycle.markets_polymarket = len(poly_markets)
            cycle.markets_kalshi = len(kalshi_markets)

            # Layer heartbeats — every successful fetch confirms the fetcher is alive.
            if poly_markets:
                metrics.heartbeat("polymarket")
            if kalshi_markets:
                metrics.heartbeat("kalshi")

            if poly_markets:
                try:
                    await poly_writer.write_many(poly_markets)
                except Exception as e:
                    logger.error("polymarket_parquet_write_failed", error=str(e))
                    metrics.record_api_error("polymarket")
            if kalshi_markets and kalshi_writer is not None:
                try:
                    await kalshi_writer.write_many(kalshi_markets)
                except Exception as e:
                    logger.error("kalshi_parquet_write_failed", error=str(e))
                    metrics.record_api_error("kalshi")

            intra_opps = find_opportunities(poly_markets, intra_ctx) if poly_markets else []
            cycle.intra_detected = intra_opps

            cross_opps: List[Opportunity] = []
            if cross_ctx and event_map.enabled():
                poly_by_id = {m.market_id: m for m in poly_markets}
                kalshi_by_ticker = {m.market_id: m for m in kalshi_markets}
                cross_opps = find_cross_opportunities(
                    event_map.enabled(),
                    poly_by_id,
                    kalshi_by_ticker,
                    cross_ctx,
                    cfg.intra_market,
                    cfg.cross_market.min_annualized_return,
                )
            cycle.cross_detected = cross_opps

            now = datetime.now(timezone.utc)
            if cfg.adverse_selection:
                cycle.intra_passed, cycle.intra_filter_stats = apply_filters(
                    intra_opps,
                    history=intra_history,
                    age_threshold_seconds=cfg.adverse_selection.age_threshold_seconds,
                    topic_tags_for=lambda o: [],
                    news_windows=news_windows,
                    upcoming_news=[],
                    market_listed_at_for=lambda o: None,
                    min_market_age_hours=cfg.adverse_selection.min_market_age_hours,
                    now=now,
                )
                cycle.cross_passed, cycle.cross_filter_stats = apply_filters(
                    cross_opps,
                    history=cross_history,
                    age_threshold_seconds=cfg.adverse_selection.age_threshold_seconds,
                    topic_tags_for=lambda o: _topic_tags_for(o, event_map),
                    news_windows=news_windows,
                    upcoming_news=[],
                    market_listed_at_for=lambda o: None,
                    min_market_age_hours=cfg.adverse_selection.min_market_age_hours,
                    now=now,
                )
            else:
                cycle.intra_passed = intra_opps
                cycle.cross_passed = cross_opps

            # Metrics aggregation.
            metrics.opportunities_detected_total.inc(len(intra_opps) + len(cross_opps))
            metrics.opportunities_passed_total.inc(
                len(cycle.intra_passed) + len(cycle.cross_passed)
            )
            metrics.opportunities_per_minute.add(len(intra_opps) + len(cross_opps))
            metrics.rolling_pnl_24h_usd.set(float(state_db.realized_pnl_total(conn)))
            metrics.capital_utilization_pct.set(
                float(state_db.total_capital_locked(conn) / cfg.allocation.total_capital_usd)
                if cfg.allocation.total_capital_usd > 0
                else 0.0
            )

            # Evaluate kill switches. Any tripped switch halts trading this cycle.
            if policy is not None:
                policy.evaluate_all(metrics)
            halted = state_db.any_kill_switch_tripped(conn)

            if halted:
                logger.warning("trading_halted", tripped=halted)
                # Skip allocation + fills but keep recording + reporting.
                allocations = []
            else:
                reserved = state_db.total_capital_locked(conn)
                markets_by_id: Dict[str, Market] = {m.market_id: m for m in poly_markets}
                allocations = allocate_capital(
                    cycle.intra_passed,
                    markets_by_id,
                    intra_ctx,
                    cfg.allocation,
                    reserved_capital_usd=reserved,
                )
            cycle.allocations_count = len(allocations)

            if not halted:
                for alloc in allocations:
                    market = markets_by_id.get(alloc.opportunity.market_id)
                    if market is None:
                        continue
                    try:
                        executor.fill_with_resolution(alloc, market.resolution_date)
                        cycle.capital_allocated_this_cycle += alloc.allocated_capital_usd
                        metrics.trades_executed_total.inc()
                    except Exception as e:
                        logger.error("paper_fill_failed", error=str(e))
                        metrics.exceptions_total.inc()
                        metrics.exceptions_per_5min.add(1)

            executor.resolve_due_positions()

            for opp in cycle.cross_passed:
                state_db.write_opportunity(conn, opp, provenance_json)

            # Persist a metrics snapshot each cycle.
            try:
                persist_snapshot(conn, metrics, now=now)
            except Exception as e:
                logger.warning("metrics_persist_failed", error=str(e))

            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            header = (
                f"[{now_str}] Loop #{loop_num:,}  git={provenance.git_commit}  "
                f"config={provenance.config_hash}  event_map={event_map.content_hash}"
            )
            print(render_cycle(cycle, header=header), flush=True)

            await poly_writer.flush()
            if kalshi_writer is not None:
                await kalshi_writer.flush()

            elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
            remaining = max(0.0, cfg.polymarket.poll_interval_seconds - elapsed)
            try:
                await asyncio.wait_for(stop.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass
    finally:
        if health is not None:
            try:
                await health.stop()
            except Exception:
                pass
        await poly_writer.close()
        if kalshi_writer is not None:
            await kalshi_writer.close()
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 0 + Phase 1 orchestrator")
    parser.add_argument("--config", default="config.yaml")
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
