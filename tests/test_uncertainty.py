"""Uncertainty-bounded paper model tests."""
from __future__ import annotations

from decimal import Decimal

from src.layer3_strategy.intra_market import compute_opportunity
from src.layer3_strategy.uncertainty import (
    UncertaintyInputs,
    in_confidence_interval,
    model_uncertainty,
    update_inputs_from_paired,
)
from tests.conftest import make_book, make_market


def _opp(strategy_ctx):
    m = make_market(
        yes_asks=make_book([("0.40", "500")]),
        no_asks=make_book([("0.46", "500")]),
        days_to_resolution=30,
    )
    opp = compute_opportunity(m, strategy_ctx)
    assert opp is not None
    return opp


class TestModelUncertainty:
    def test_produces_three_values(self, strategy_ctx):
        opp = _opp(strategy_ctx)
        u = model_uncertainty(opp, UncertaintyInputs.default_pre_live())
        assert u.expected_profit_usd == opp.expected_profit_usd
        assert u.profit_p05_usd <= u.expected_profit_usd
        assert u.profit_p95_usd >= u.profit_p05_usd

    def test_wider_slippage_widens_interval(self, strategy_ctx):
        opp = _opp(strategy_ctx)
        narrow = UncertaintyInputs(
            slippage_bps_samples=[0, 5, 10],
            fill_rate_samples=[1.0, 1.0, 1.0],
            fee_overrun_bps_samples=[0, 0, 0],
        )
        wide = UncertaintyInputs(
            slippage_bps_samples=[0, 50, 150],
            fill_rate_samples=[1.0, 1.0, 1.0],
            fee_overrun_bps_samples=[0, 0, 0],
        )
        u_narrow = model_uncertainty(opp, narrow)
        u_wide = model_uncertainty(opp, wide)
        narrow_width = u_narrow.profit_p95_usd - u_narrow.profit_p05_usd
        wide_width = u_wide.profit_p95_usd - u_wide.profit_p05_usd
        assert wide_width >= narrow_width

    def test_low_fill_rate_drags_p05_down(self, strategy_ctx):
        opp = _opp(strategy_ctx)
        reliable = UncertaintyInputs(
            slippage_bps_samples=[0, 0, 0],
            fill_rate_samples=[1.0, 1.0, 1.0],
            fee_overrun_bps_samples=[0, 0, 0],
        )
        unreliable = UncertaintyInputs(
            slippage_bps_samples=[0, 0, 0],
            fill_rate_samples=[0.3, 0.5, 0.7],
            fee_overrun_bps_samples=[0, 0, 0],
        )
        u_r = model_uncertainty(opp, reliable)
        u_u = model_uncertainty(opp, unreliable)
        assert u_u.profit_p05_usd <= u_r.profit_p05_usd


class TestCalibration:
    def test_realized_in_interval(self, strategy_ctx):
        opp = _opp(strategy_ctx)
        u = model_uncertainty(opp, UncertaintyInputs.default_pre_live())
        mid = (u.profit_p05_usd + u.profit_p95_usd) / 2
        assert in_confidence_interval(u, mid) is True

    def test_realized_outside_interval(self, strategy_ctx):
        opp = _opp(strategy_ctx)
        u = model_uncertainty(opp, UncertaintyInputs.default_pre_live())
        below = u.profit_p05_usd - Decimal(10)
        above = u.profit_p95_usd + Decimal(10)
        assert in_confidence_interval(u, below) is False
        assert in_confidence_interval(u, above) is False


class TestUpdateInputs:
    def test_append_trims_to_retention(self):
        base = UncertaintyInputs(
            slippage_bps_samples=list(range(200)),
            fill_rate_samples=[1.0] * 10,
            fee_overrun_bps_samples=[0] * 10,
        )
        updated = update_inputs_from_paired(
            base,
            new_slippage_bps=[999, 1000, 1001],
            new_fill_rates=[0.8],
            new_fee_overrun_bps=[5],
            retain_samples=50,
        )
        assert len(updated.slippage_bps_samples) == 50
        # The newest values must be at the tail (most recent data).
        assert updated.slippage_bps_samples[-1] == 1001
        assert updated.slippage_bps_samples[-2] == 1000
