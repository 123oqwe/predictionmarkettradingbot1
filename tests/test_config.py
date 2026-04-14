"""Tests for config loader — especially the float-rejection invariant."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.config import _d, load_config


def test_decimal_coercion_accepts_string():
    assert _d("0.20") == Decimal("0.20")


def test_decimal_coercion_accepts_int():
    assert _d(5) == Decimal("5")


def test_decimal_coercion_rejects_float():
    with pytest.raises(TypeError, match="Float value"):
        _d(0.20)


def test_load_config_from_repo_config(tmp_path):
    # Use the shipped config.yaml.
    repo_cfg = Path(__file__).resolve().parent.parent / "config.yaml"
    cfg = load_config(repo_cfg)
    assert cfg.mode == "paper"
    assert cfg.intra_market.min_annualized_return == Decimal("0.20")
    assert cfg.intra_market.min_days_to_resolution == Decimal("5")
    assert cfg.polymarket.gas_estimate_usd == Decimal("0.20")
    assert cfg.allocation.total_capital_usd == Decimal("1000")
