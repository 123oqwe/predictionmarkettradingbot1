"""Adverse selection filter tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.layer3_strategy.adverse_selection import (
    NewsEvent,
    NewsWindow,
    OpportunityHistory,
    age_filter,
    apply_filters,
    news_window_filter,
    young_market_filter,
)
from src.layer3_strategy.intra_market import compute_opportunity
from tests.conftest import make_book, make_market


def _opp_at(ts: datetime, strategy_ctx, market_id="m1", event_id="e1"):
    m = make_market(
        yes_asks=make_book([("0.45", "500")]),
        no_asks=make_book([("0.48", "500")]),
        days_to_resolution=30,
        market_id=market_id,
        event_id=event_id,
        fetched_at=ts,
    )
    opp = compute_opportunity(m, strategy_ctx)
    assert opp is not None
    return opp


class TestAgeFilter:
    def test_first_observation_accepted(self, strategy_ctx):
        h = OpportunityHistory()
        opp = _opp_at(datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc), strategy_ctx)
        d = age_filter(opp, h, threshold_seconds=60)
        assert d.accepted

    def test_repeated_within_threshold_accepted(self, strategy_ctx):
        h = OpportunityHistory()
        t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        opp1 = _opp_at(t0, strategy_ctx)
        age_filter(opp1, h, threshold_seconds=60)
        opp2 = _opp_at(t0 + timedelta(seconds=30), strategy_ctx)
        d = age_filter(opp2, h, threshold_seconds=60)
        assert d.accepted

    def test_repeated_past_threshold_rejected(self, strategy_ctx):
        h = OpportunityHistory()
        t0 = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        age_filter(_opp_at(t0, strategy_ctx), h, threshold_seconds=60)
        opp = _opp_at(t0 + timedelta(seconds=120), strategy_ctx)
        d = age_filter(opp, h, threshold_seconds=60)
        assert not d.accepted
        assert "too_old" in (d.reason or "")


class TestNewsWindow:
    def test_no_tags_accepted(self, strategy_ctx):
        opp = _opp_at(datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc), strategy_ctx)
        d = news_window_filter(
            opp,
            topic_tags_for_opp=[],
            windows=[NewsWindow(("fed",), 15, 60)],
            upcoming_events=[
                NewsEvent("fed", datetime(2026, 4, 14, 12, 5, 0, tzinfo=timezone.utc))
            ],
            now=datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert d.accepted

    def test_blackout_rejects(self, strategy_ctx):
        ev_time = datetime(2026, 4, 14, 12, 30, 0, tzinfo=timezone.utc)
        d = news_window_filter(
            _opp_at(ev_time - timedelta(minutes=10), strategy_ctx),
            topic_tags_for_opp=["fed"],
            windows=[NewsWindow(("fed",), 15, 60)],
            upcoming_events=[NewsEvent("fed", ev_time)],
            now=ev_time - timedelta(minutes=10),  # within 15-min before window
        )
        assert not d.accepted
        assert "news_window" in (d.reason or "")

    def test_outside_window_accepted(self, strategy_ctx):
        ev_time = datetime(2026, 4, 14, 12, 30, 0, tzinfo=timezone.utc)
        d = news_window_filter(
            _opp_at(ev_time - timedelta(hours=2), strategy_ctx),
            topic_tags_for_opp=["fed"],
            windows=[NewsWindow(("fed",), 15, 60)],
            upcoming_events=[NewsEvent("fed", ev_time)],
            now=ev_time - timedelta(hours=2),
        )
        assert d.accepted


class TestYoungMarket:
    def test_no_listing_time_accepted(self, strategy_ctx):
        opp = _opp_at(datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc), strategy_ctx)
        d = young_market_filter(
            opp,
            market_listed_at=None,
            min_age_hours=24,
            now=opp.detected_at,
        )
        assert d.accepted

    def test_too_young_rejected(self, strategy_ctx):
        now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        opp = _opp_at(now, strategy_ctx)
        d = young_market_filter(
            opp,
            market_listed_at=now - timedelta(hours=2),
            min_age_hours=24,
            now=now,
        )
        assert not d.accepted

    def test_old_enough_accepted(self, strategy_ctx):
        now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        opp = _opp_at(now, strategy_ctx)
        d = young_market_filter(
            opp,
            market_listed_at=now - timedelta(days=3),
            min_age_hours=24,
            now=now,
        )
        assert d.accepted


class TestApplyFilters:
    def test_pipeline_aggregates_stats(self, strategy_ctx):
        now = datetime(2026, 4, 14, 12, 30, 0, tzinfo=timezone.utc)
        h = OpportunityHistory()
        # Pre-populate history so the second observation registers as old.
        old_opp = _opp_at(now - timedelta(seconds=120), strategy_ctx, market_id="m-old")
        h.observe(old_opp)

        opps = [
            _opp_at(now, strategy_ctx, market_id="m-fresh"),
            _opp_at(now, strategy_ctx, market_id="m-old"),  # will be rejected as stale
        ]

        accepted, stats = apply_filters(
            opps,
            history=h,
            age_threshold_seconds=60,
            topic_tags_for=lambda o: [],
            news_windows=[],
            upcoming_news=[],
            market_listed_at_for=lambda o: None,
            min_market_age_hours=24,
            now=now,
        )
        assert len(accepted) == 1
        assert accepted[0].market_id == "m-fresh"
        assert stats.accepted >= 1
        assert any("too_old" in k for k in stats.rejected)

    def test_history_gc_drops_old_entries(self, strategy_ctx):
        h = OpportunityHistory(max_age_seconds=60)
        old_ts = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        h.first_seen_by_fingerprint["fp-old"] = old_ts
        now = old_ts + timedelta(minutes=10)
        h.gc(now)
        assert "fp-old" not in h.first_seen_by_fingerprint
