"""Phase 4 tests: prefilter + extractor (stub) + cache + matcher + tiers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.matching.extraction_cache import ExtractionCache
from src.matching.extractor import (
    Extractor,
    ExtractorConfig,
    ExtractorMode,
    hash_text,
)
from src.matching.matcher import compare
from src.matching.prefilter import (
    PrefilterConfig,
    keyword_overlap,
    prefilter_pair,
    prefilter_pairs,
)
from src.matching.schema import (
    ResolutionCriteria,
    required_edge_cases,
)
from src.risk.tiers import (
    PairTierState,
    Tier,
    effective_per_trade_cap,
    effective_threshold,
    live_trading_allowed,
)
from tests.conftest import make_book, make_market

# ---- prefilter ----


def _poly(title: str, liquidity: str = "1000", days: int = 30, market_id: str = "p1"):
    return make_market(
        yes_asks=make_book([("0.45", "500")]),
        no_asks=make_book([("0.48", "500")]),
        days_to_resolution=days,
        market_id=market_id,
    ).model_copy(update={"liquidity_usd": Decimal(liquidity), "title": title})


def _kalshi(title: str, liquidity: str = "1000", days: int = 30, market_id: str = "K1"):
    return _poly(title, liquidity, days, market_id).model_copy(
        update={"platform": "kalshi", "market_id": market_id}
    )


class TestPrefilter:
    def test_keyword_overlap_nonempty(self):
        a = _poly("Will the Fed cut rates in December 2026?")
        b = _kalshi("Will the Federal Reserve lower fed funds target rate December 2026?")
        assert keyword_overlap(a, b) >= 2

    def test_prefilter_accepts_compatible_pair(self):
        a = _poly("Will the Fed cut rates in December 2026?", days=30)
        b = _kalshi("Will the Federal Reserve lower fed funds target rate December 2026?", days=30)
        assert prefilter_pair(a, b, PrefilterConfig()) is True

    def test_prefilter_rejects_date_mismatch(self):
        a = _poly("Will Team X win?", days=30)
        b = _kalshi("Will Team X win?", days=60)
        assert prefilter_pair(a, b, PrefilterConfig(max_resolution_date_delta_days=7)) is False

    def test_prefilter_rejects_low_liquidity(self):
        a = _poly("Fed cut December?", liquidity="10")
        b = _kalshi("Fed cut December?", liquidity="10")
        assert prefilter_pair(a, b, PrefilterConfig(min_liquidity_usd=Decimal("100"))) is False

    def test_prefilter_rejects_disjoint_keywords(self):
        a = _poly("Will Taylor Swift announce a tour?")
        b = _kalshi("Will GDP growth exceed 3% in Q4?")
        assert prefilter_pair(a, b, PrefilterConfig()) is False

    def test_prefilter_pairs_deterministic(self):
        polys = [_poly(f"Fed {i}", market_id=f"p{i}") for i in range(3)]
        kals = [_kalshi(f"Federal Reserve {i}", market_id=f"K{i}") for i in range(3)]
        out1 = prefilter_pairs(polys, kals, PrefilterConfig())
        out2 = prefilter_pairs(polys, kals, PrefilterConfig())
        assert [(p.market_id, k.market_id) for p, k in out1] == [
            (p.market_id, k.market_id) for p, k in out2
        ]


# ---- extractor stub ----


class TestExtractorStub:
    @pytest.mark.asyncio
    async def test_stub_produces_valid_criteria(self):
        e = Extractor(ExtractorConfig(mode=ExtractorMode.STUB))
        crit = await e.extract(
            title="Will the Fed cut rates in December 2026?",
            description="FOMC meeting",
            rules_text="Resolves YES if fed funds rate is cut.",
        )
        assert crit.event_type == "fed_rate_decision"
        assert crit.schema_version == 1
        # All vocabulary keys populated.
        for k in required_edge_cases("fed_rate_decision"):
            assert k in crit.edge_cases
        assert crit.description_hash == hash_text("FOMC meeting")

    @pytest.mark.asyncio
    async def test_stub_offline_mode_raises(self):
        e = Extractor(ExtractorConfig(mode=ExtractorMode.OFFLINE))
        with pytest.raises(RuntimeError, match="OFFLINE"):
            await e.extract(title="x", description="y", rules_text="z")

    @pytest.mark.asyncio
    async def test_anthropic_mode_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        e = Extractor(ExtractorConfig(mode=ExtractorMode.ANTHROPIC))
        with pytest.raises(RuntimeError, match="Anthropic mode requires"):
            await e.extract(title="x", description="y", rules_text="z")


# ---- extraction cache ----


class TestExtractionCache:
    @pytest.mark.asyncio
    async def test_put_and_get_roundtrip(self, tmp_path):
        cache = ExtractionCache(base_dir=tmp_path, platform="polymarket")
        e = Extractor(ExtractorConfig(mode=ExtractorMode.STUB))
        crit = await e.extract(
            title="Fed cut?", description="desc", rules_text="rules"
        )
        cache.put(market_id="m1", criteria=crit)
        got = cache.get(
            market_id="m1",
            description_hash=crit.description_hash,
            rules_hash=crit.raw_rules_hash,
            llm_model_version=crit.llm_model_version,
        )
        assert got is not None
        assert got.event_type == crit.event_type

    @pytest.mark.asyncio
    async def test_cache_key_includes_description_hash(self, tmp_path):
        cache = ExtractionCache(base_dir=tmp_path, platform="polymarket")
        e = Extractor(ExtractorConfig(mode=ExtractorMode.STUB))
        c1 = await e.extract(title="Fed cut?", description="v1", rules_text="rules")
        c2 = await e.extract(title="Fed cut?", description="v2", rules_text="rules")
        cache.put(market_id="m1", criteria=c1)
        # Looking up under v2 description hash should NOT return v1.
        got = cache.get(
            market_id="m1",
            description_hash=c2.description_hash,
            rules_hash=c2.raw_rules_hash,
            llm_model_version=c2.llm_model_version,
        )
        assert got is None

    @pytest.mark.asyncio
    async def test_cache_persists_across_reopen(self, tmp_path):
        e = Extractor(ExtractorConfig(mode=ExtractorMode.STUB))
        crit = await e.extract(
            title="Fed cut?", description="desc", rules_text="rules"
        )
        c1 = ExtractionCache(base_dir=tmp_path, platform="polymarket")
        c1.put(market_id="m1", criteria=crit)
        c2 = ExtractionCache(base_dir=tmp_path, platform="polymarket")
        assert c2.size() >= 1


# ---- matcher ----


def _crit(
    *,
    event_type: str = "fed_rate_decision",
    predicate: str = "rate_cut",
    source: str = "fomc_statement",
    metric: str = "upper_bound_of_target_range",
    direction: str = "less_than_previous",
    edge_overrides: dict = None,
    confidence: float = 0.9,
) -> ResolutionCriteria:
    now = datetime(2026, 12, 17, 18, 0, 0, tzinfo=timezone.utc)
    edge_cases = dict.fromkeys(required_edge_cases(event_type), "not_applicable")
    if edge_overrides:
        edge_cases.update(edge_overrides)
    return ResolutionCriteria(
        event_type=event_type,
        event_date_start=now,
        event_date_end=now + timedelta(hours=6),
        primary_predicate=predicate,
        resolution_source=source,
        resolution_metric=metric,
        resolution_direction=direction,
        edge_cases=edge_cases,
        confidence_overall=confidence,
        confidence_per_field={},
        raw_rules_hash="r",
        description_hash="d",
        llm_model_version="test",
    )


class TestMatcher:
    def test_identical_criteria_match(self):
        r = compare(_crit(), _crit())
        assert r.match is True
        assert r.confidence > 0
        assert r.differences == []

    def test_event_type_mismatch_short_circuits(self):
        r = compare(
            _crit(event_type="fed_rate_decision"),
            _crit(event_type="sports_match"),
        )
        assert r.match is False
        assert len(r.differences) == 1
        assert "event_type" in r.differences[0]

    def test_source_incompatible_flagged(self):
        r = compare(
            _crit(source="cme_settlement"),
            _crit(source="coinbase_spot_close"),
        )
        assert r.match is False
        assert any("resolution_source" in d for d in r.differences)

    def test_whitelisted_source_equivalence(self):
        r = compare(
            _crit(source="fomc_statement"),
            _crit(source="federal_reserve_announcement"),
        )
        # These should be treated as compatible — not flagged.
        assert not any("resolution_source" in d for d in r.differences)

    def test_edge_case_divergence(self):
        r = compare(
            _crit(edge_overrides={"meeting_postponed": "ambiguous"}),
            _crit(edge_overrides={"meeting_postponed": "resolves_no"}),
        )
        assert r.match is False
        assert any("edge_case.meeting_postponed" in d for d in r.differences)

    def test_medium_confidence_routes_to_review(self):
        r = compare(
            _crit(confidence=0.85, edge_overrides={"meeting_postponed": "ambiguous"}),
            _crit(confidence=0.85, edge_overrides={"meeting_postponed": "resolves_no"}),
        )
        assert r.match is False
        # 1 diff * 0.1 penalty = 0.75 >= 0.6 threshold → review.
        assert r.requires_review is True


# ---- tiers ----


class TestTiers:
    def test_threshold_escalation(self):
        assert effective_threshold(Tier.A) == Decimal("0.28")
        assert effective_threshold(Tier.B) == Decimal("0.35")
        assert effective_threshold(Tier.C) == Decimal("0.40")

    def test_cap_escalation(self):
        base = Decimal("100")
        assert effective_per_trade_cap(Tier.A, base) == base
        assert effective_per_trade_cap(Tier.B, base) == base / 2
        assert effective_per_trade_cap(Tier.C, base) == Decimal(0)

    def test_live_trading_allowed(self):
        assert live_trading_allowed(Tier.A)
        assert live_trading_allowed(Tier.B)
        assert not live_trading_allowed(Tier.C)

    def test_c_to_b_promotion_after_cooling(self):
        entered = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        state = PairTierState(pair_id="p1", tier=Tier.C, entered_tier_at=entered)
        # 24h < cooling window — not eligible.
        assert state.eligible_for_c_to_b(entered + timedelta(hours=24)) is False
        # 48h >= cooling window — eligible.
        assert state.eligible_for_c_to_b(entered + timedelta(hours=48)) is True

    def test_incident_blocks_promotion(self):
        entered = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        state = PairTierState(pair_id="p1", tier=Tier.C, entered_tier_at=entered)
        state.record_incident()
        assert state.eligible_for_c_to_b(entered + timedelta(hours=72)) is False
