"""Config loader. Every numeric field becomes Decimal; float never enters the system."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

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
class Config:
    mode: str
    polymarket: PolymarketConfig
    storage: StorageConfig
    intra_market: IntraMarketConfig
    allocation: AllocationConfig
    paper_execution: PaperExecutionConfig
    raw: Dict[str, Any]  # kept for hashing / provenance


def load_config(path: str | Path) -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())

    pm = raw["polymarket"]
    st = raw["storage"]
    im = raw["strategy"]["intra_market"]
    al = raw["allocation"]
    pe = raw["execution"]["paper"]

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
        raw=raw,
    )
