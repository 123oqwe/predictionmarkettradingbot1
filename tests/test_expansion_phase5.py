"""Phase 5 expansion tests: feature flags + convergence detection.

Everything here is FROZEN. Tests verify the code works; deployment is
blocked behind feature_flags until Phase 3 SCALE decision.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.expansion.feature_flags import (
    clear_all,
    disable,
    enable,
    is_enabled,
)
from src.expansion.resolution_convergence import (
    ConvergenceConfig,
    find_convergence_opportunities,
)
from tests.conftest import make_book, make_market

# ---- feature flags ----


class TestFeatureFlags:
    def test_default_off(self, tmp_path):
        clear_all(tmp_path)
        assert is_enabled("option_e_convergence", flag_dir=tmp_path) is False

    def test_config_override_wins_over_default(self, tmp_path):
        clear_all(tmp_path)
        assert is_enabled("option_e_convergence", config_override=True, flag_dir=tmp_path) is True

    def test_disable_file_overrides_everything(self, tmp_path):
        disable("option_e_convergence", flag_dir=tmp_path)
        # Even with config on, disable file kills it.
        assert (
            is_enabled("option_e_convergence", config_override=True, flag_dir=tmp_path)
            is False
        )

    def test_enable_file_with_expiry_expires(self, tmp_path):
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=1)
        p = enable("option_e_convergence", expires_in_hours=None, flag_dir=tmp_path)
        p.write_text(past.isoformat())
        assert is_enabled("option_e_convergence", flag_dir=tmp_path, now=now) is False

    def test_enable_file_with_future_expiry_works(self, tmp_path):
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=2)
        p = enable("option_e_convergence", expires_in_hours=None, flag_dir=tmp_path)
        p.write_text(future.isoformat())
        assert is_enabled("option_e_convergence", flag_dir=tmp_path, now=now) is True

    def test_enable_clears_existing_disable(self, tmp_path):
        disable("option_e_convergence", flag_dir=tmp_path)
        enable("option_e_convergence", expires_in_hours=24, flag_dir=tmp_path)
        # enable flag clears the disable flag.
        assert is_enabled("option_e_convergence", flag_dir=tmp_path) is True


# ---- convergence detection ----


def _market_near_resolution(
    *,
    yes_price: str,
    yes_depth: str = "500",
    hours_to_resolution: float = 3.0,
    market_id: str = "m1",
):
    m = make_market(
        yes_asks=make_book([(yes_price, yes_depth)]),
        no_asks=make_book([("0.50", "500")]),
        market_id=market_id,
    )
    return m.model_copy(
        update={
            "resolution_date": m.fetched_at
            + timedelta(seconds=int(hours_to_resolution * 3600))
        }
    )


class TestConvergenceDetection:
    def test_high_confidence_near_resolution_detected(self, strategy_ctx):
        m = _market_near_resolution(yes_price="0.97", hours_to_resolution=3.0)
        opps = find_convergence_opportunities([m], strategy_ctx, ConvergenceConfig())
        # Expect one YES-side convergence hit.
        yes_opps = [o for o in opps if "[conv/yes]" in o.title]
        assert len(yes_opps) == 1
        opp = yes_opps[0]
        assert opp.yes_fill_price == Decimal("0.97")
        assert opp.size_contracts >= Decimal("50")
        assert opp.expected_profit_usd >= Decimal("1")

    def test_below_confidence_threshold_rejected(self, strategy_ctx):
        m = _market_near_resolution(yes_price="0.80", hours_to_resolution=3.0)
        opps = find_convergence_opportunities([m], strategy_ctx, ConvergenceConfig())
        # 0.80 isn't "convergence" — it's a regular mid-market bet.
        yes_opps = [o for o in opps if "[conv/yes]" in o.title]
        assert yes_opps == []

    def test_too_close_to_resolution_rejected(self, strategy_ctx):
        m = _market_near_resolution(yes_price="0.97", hours_to_resolution=0.5)
        opps = find_convergence_opportunities([m], strategy_ctx, ConvergenceConfig())
        assert opps == []  # we can't execute in time

    def test_too_far_from_resolution_rejected(self, strategy_ctx):
        m = _market_near_resolution(yes_price="0.97", hours_to_resolution=48.0)
        opps = find_convergence_opportunities([m], strategy_ctx, ConvergenceConfig())
        assert opps == []  # not "convergence" regime — just regular arb

    def test_thin_book_rejected(self, strategy_ctx):
        # Top-of-book high price but total depth below threshold.
        m = _market_near_resolution(yes_price="0.97", yes_depth="30", hours_to_resolution=3.0)
        opps = find_convergence_opportunities([m], strategy_ctx, ConvergenceConfig())
        assert opps == []

    def test_resolved_market_skipped(self, strategy_ctx):
        m = _market_near_resolution(yes_price="0.97", hours_to_resolution=3.0)
        m = m.model_copy(update={"resolved": True})
        opps = find_convergence_opportunities([m], strategy_ctx, ConvergenceConfig())
        assert opps == []

    def test_both_sides_can_trigger(self, strategy_ctx):
        """A market with high prices on BOTH sides (unusual but possible near
        resolution) can produce two convergence opportunities."""
        m = make_market(
            yes_asks=make_book([("0.97", "500")]),
            no_asks=make_book([("0.96", "500")]),
            market_id="dual",
        )
        m = m.model_copy(
            update={"resolution_date": m.fetched_at + timedelta(hours=3)}
        )
        opps = find_convergence_opportunities([m], strategy_ctx, ConvergenceConfig())
        # Note: two trades on the same market would be contradictory in real
        # life — the allocator would pick one. We're just verifying detection
        # runs independently on each side.
        assert len(opps) == 2
