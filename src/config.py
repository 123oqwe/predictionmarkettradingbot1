"""Config loader. Every numeric field becomes Decimal; float never enters the system."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def _d(v: Any) -> Decimal:
    """Coerce a YAML scalar to Decimal. Forbids float inputs to prevent contamination."""
    if isinstance(v, Decimal):
        return v
    if isinstance(v, float):
        raise TypeError(
            f"Float value {v!r} in config — always quote numeric fields as strings "
            f"so they parse as Decimal, never float."
        )
    return Decimal(str(v))


@dataclass(frozen=True)
class PolymarketConfig:
    clob_base_url: str
    gamma_base_url: str
    poll_interval_seconds: int
    request_timeout_seconds: int
    max_concurrent_requests: int
    gas_estimate_usd: Decimal
    fee_bps: int


@dataclass(frozen=True)
class StorageConfig:
    snapshots_dir: str
    state_db_path: str
    parquet_flush_interval_seconds: int


@dataclass(frozen=True)
class IntraMarketConfig:
    min_annualized_return: Decimal
    min_days_to_resolution: Decimal
    min_trade_size_contracts: Decimal
    max_trade_size_contracts: Decimal
    min_market_liquidity_usd: Decimal
    stale_snapshot_threshold_seconds: int


@dataclass(frozen=True)
class AllocationConfig:
    total_capital_usd: Decimal
    max_capital_per_trade_usd: Decimal
    max_capital_per_event_usd: Decimal


@dataclass(frozen=True)
class PaperExecutionConfig:
    resolution_poll_interval_seconds: int


@dataclass(frozen=True)
class CrossMarketConfig:
    min_annualized_return: Decimal
    assumed_rule_divergence_prob: Decimal


@dataclass(frozen=True)
class KalshiConfig:
    base_url: str
    poll_interval_seconds: int
    request_timeout_seconds: int
    max_concurrent_requests: int
    fee_bps: int
    markets_limit: int
    api_key_env: str


@dataclass(frozen=True)
class NewsWindowConfig:
    topic_tags: tuple
    blackout_minutes_before: int
    blackout_minutes_after: int


@dataclass(frozen=True)
class AdverseSelectionConfig:
    age_threshold_seconds: int
    min_market_age_hours: int
    news_windows: tuple


@dataclass(frozen=True)
class Config:
    mode: str
    polymarket: PolymarketConfig
    storage: StorageConfig
    intra_market: IntraMarketConfig
    allocation: AllocationConfig
    paper_execution: PaperExecutionConfig
    # Phase 1 additions; optional so Phase 0 configs still load.
    cross_market: Optional[CrossMarketConfig] = None
    kalshi: Optional[KalshiConfig] = None
    adverse_selection: Optional[AdverseSelectionConfig] = None
    event_map_path: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)  # for hashing / provenance


def load_config(path: str | Path) -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())

    pm = raw["polymarket"]
    st = raw["storage"]
    im = raw["strategy"]["intra_market"]
    al = raw["allocation"]
    pe = raw["execution"]["paper"]

    cross = None
    if "cross_market" in raw.get("strategy", {}):
        cm = raw["strategy"]["cross_market"]
        cross = CrossMarketConfig(
            min_annualized_return=_d(cm["min_annualized_return"]),
            assumed_rule_divergence_prob=_d(cm["assumed_rule_divergence_prob"]),
        )

    kalshi_cfg = None
    if "kalshi" in raw:
        k = raw["kalshi"]
        kalshi_cfg = KalshiConfig(
            base_url=k["base_url"],
            poll_interval_seconds=int(k["poll_interval_seconds"]),
            request_timeout_seconds=int(k["request_timeout_seconds"]),
            max_concurrent_requests=int(k["max_concurrent_requests"]),
            fee_bps=int(k["fee_bps"]),
            markets_limit=int(k.get("markets_limit", 100)),
            api_key_env=k.get("api_key_env", "KALSHI_API_KEY"),
        )

    adv = None
    if "adverse_selection" in raw:
        a = raw["adverse_selection"]
        windows = tuple(
            NewsWindowConfig(
                topic_tags=tuple(w["topic_tags"]),
                blackout_minutes_before=int(w["blackout_minutes_before"]),
                blackout_minutes_after=int(w["blackout_minutes_after"]),
            )
            for w in (a.get("news_windows") or [])
        )
        adv = AdverseSelectionConfig(
            age_threshold_seconds=int(a["age_threshold_seconds"]),
            min_market_age_hours=int(a["min_market_age_hours"]),
            news_windows=windows,
        )

    return Config(
        mode=raw["mode"],
        polymarket=PolymarketConfig(
            clob_base_url=pm["clob_base_url"],
            gamma_base_url=pm["gamma_base_url"],
            poll_interval_seconds=int(pm["poll_interval_seconds"]),
            request_timeout_seconds=int(pm["request_timeout_seconds"]),
            max_concurrent_requests=int(pm["max_concurrent_requests"]),
            gas_estimate_usd=_d(pm["gas_estimate_usd"]),
            fee_bps=int(pm["fee_bps"]),
        ),
        storage=StorageConfig(
            snapshots_dir=st["snapshots_dir"],
            state_db_path=st["state_db_path"],
            parquet_flush_interval_seconds=int(st["parquet_flush_interval_seconds"]),
        ),
        intra_market=IntraMarketConfig(
            min_annualized_return=_d(im["min_annualized_return"]),
            min_days_to_resolution=_d(im["min_days_to_resolution"]),
            min_trade_size_contracts=_d(im["min_trade_size_contracts"]),
            max_trade_size_contracts=_d(im["max_trade_size_contracts"]),
            min_market_liquidity_usd=_d(im["min_market_liquidity_usd"]),
            stale_snapshot_threshold_seconds=int(im["stale_snapshot_threshold_seconds"]),
        ),
        allocation=AllocationConfig(
            total_capital_usd=_d(al["total_capital_usd"]),
            max_capital_per_trade_usd=_d(al["max_capital_per_trade_usd"]),
            max_capital_per_event_usd=_d(al["max_capital_per_event_usd"]),
        ),
        paper_execution=PaperExecutionConfig(
            resolution_poll_interval_seconds=int(pe["resolution_poll_interval_seconds"]),
        ),
        cross_market=cross,
        kalshi=kalshi_cfg,
        adverse_selection=adv,
        event_map_path=raw.get("event_map_path"),
        raw=raw,
    )
