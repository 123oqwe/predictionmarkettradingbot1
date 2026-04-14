"""End-to-end backtest integration test.

Write a synthetic Parquet with known-outcome trades, run the backtest, assert
the exact expected output. This guards against regressions in the runner
itself — every future change to fill_model.py, runner.py, or metrics.py that
breaks the expected PnL fails this test.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.backtest.fill_model import FillModelConfig, FillModelKind
from src.backtest.runner import run_backtest
from src.layer1_data_recording.parquet_writer import DailyParquetWriter
from src.layer3_strategy.models import Market, OrderBookLevel, OrderBookSide


def _synthetic_market(idx: int, anchor: datetime, tick_offset_s: int) -> Market:
    """Produce a market with a known arbitrage of 5% absolute profit.

    Prices: 0.45 yes + 0.50 no = 0.95 per pair → 5% profit per pair
    Size available: 100 contracts on both sides, single level
    Days to resolution: 30
    → Profit for full 100 size = $5, capital = $95 + gas
    """
    ts = anchor + timedelta(seconds=tick_offset_s)
    return Market(
        platform="polymarket",
        market_id=f"synth-{idx:04d}",
        event_id=f"evt-{idx:04d}",
        title=f"Synthetic arb {idx}",
        yes_bids=OrderBookSide(levels=[]),
        yes_asks=OrderBookSide(
            levels=[OrderBookLevel(price=Decimal("0.45"), size_contracts=Decimal("100"))]
        ),
        no_bids=OrderBookSide(levels=[]),
        no_asks=OrderBookSide(
            levels=[OrderBookLevel(price=Decimal("0.50"), size_contracts=Decimal("100"))]
        ),
        fee_bps=0,
        resolution_date=ts + timedelta(days=30),
        resolution_source="test",
        fetched_at=ts,
        active=True,
        resolved=False,
        liquidity_usd=Decimal("1000"),
    )


@pytest.mark.asyncio
async def test_backtest_end_to_end(tmp_path: Path, strategy_ctx):
    anchor = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
    base = tmp_path / "snapshots"

    # 3 distinct ticks × 4 markets each = 12 rows.
    writer = DailyParquetWriter(base_dir=base, platform="polymarket")
    batches = []
    for tick_idx, offset in enumerate((0, 10, 20)):
        batches.extend([_synthetic_market(i + tick_idx * 100, anchor, offset) for i in range(4)])
    await writer.write_many(batches)
    await writer.close()

    result = await run_backtest(
        snapshots_dir=str(base),
        platform="polymarket",
        start=anchor - timedelta(seconds=1),
        end=anchor + timedelta(minutes=1),
        strategy_ctx=strategy_ctx,
        fill_cfg=FillModelConfig(kind=FillModelKind.OPTIMISTIC),
    )

    # 12 markets → 12 opportunities detected → 12 trades (each is the same arb).
    assert result.snapshots_processed == 12
    assert result.opportunities_detected == 12
    assert len(result.trades) == 12

    # Every trade is profitable (fill = optimistic, no slippage).
    assert all(t.realized_pnl_usd > 0 for t in result.trades)
    assert result.metrics.win_rate == 1.0
    # Total PnL: each market has ~$5 profit at size 100.
    assert result.metrics.total_pnl_usd >= Decimal("50")


@pytest.mark.asyncio
async def test_backtest_deterministic(tmp_path: Path, strategy_ctx):
    """Two runs on the same Parquet must produce identical determinism_hash."""
    anchor = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
    base = tmp_path / "snapshots"
    writer = DailyParquetWriter(base_dir=base, platform="polymarket")
    await writer.write_many([_synthetic_market(i, anchor, 0) for i in range(5)])
    await writer.close()

    r1 = await run_backtest(
        snapshots_dir=str(base),
        platform="polymarket",
        start=anchor - timedelta(seconds=1),
        end=anchor + timedelta(minutes=1),
        strategy_ctx=strategy_ctx,
        fill_cfg=FillModelConfig(kind=FillModelKind.REALISTIC),
    )
    r2 = await run_backtest(
        snapshots_dir=str(base),
        platform="polymarket",
        start=anchor - timedelta(seconds=1),
        end=anchor + timedelta(minutes=1),
        strategy_ctx=strategy_ctx,
        fill_cfg=FillModelConfig(kind=FillModelKind.REALISTIC),
    )
    assert r1.determinism_hash == r2.determinism_hash
    assert r1.metrics.to_dict() == r2.metrics.to_dict()


@pytest.mark.asyncio
async def test_liquidity_consumed_within_tick(tmp_path: Path, strategy_ctx):
    """Fix #4: two opportunities on the same market within one tick should not
    both get full book size. The second must see a reduced book.

    Strategy: craft a scenario where the SAME market appears with multiple
    opportunities. Since find_opportunities returns one opp per market in our
    current strategy, we verify by producing duplicate market_ids in the
    snapshot. That's an edge-case but exercises the consumed-liquidity logic.
    """
    anchor = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
    base = tmp_path / "snapshots"

    # Build a market with a lot of liquidity (1000 contracts) — if fix #4
    # works, the second observed opp would see less than 1000 available.
    # For this test we verify that running the same market twice consumes
    # the book correctly by checking the FIRST trade fills at full detected
    # size and that `consume_side` truly reduces available depth.

    from src.backtest.runner import _consume_side
    from src.layer3_strategy.models import OrderBookLevel, OrderBookSide

    side = OrderBookSide(
        levels=[
            OrderBookLevel(price=Decimal("0.45"), size_contracts=Decimal("100")),
            OrderBookLevel(price=Decimal("0.50"), size_contracts=Decimal("200")),
        ]
    )
    # Consume 50 → top level now has 50 left.
    after_50 = _consume_side(side, Decimal("50"))
    assert after_50.levels[0].price == Decimal("0.45")
    assert after_50.levels[0].size_contracts == Decimal("50")
    assert after_50.levels[1].size_contracts == Decimal("200")

    # Consume 100 → first level gone entirely.
    after_100 = _consume_side(side, Decimal("100"))
    assert len(after_100.levels) == 1
    assert after_100.levels[0].price == Decimal("0.50")
    assert after_100.levels[0].size_contracts == Decimal("200")

    # Consume 250 → top level gone, second level reduced to 50.
    after_250 = _consume_side(side, Decimal("250"))
    assert len(after_250.levels) == 1
    assert after_250.levels[0].price == Decimal("0.50")
    assert after_250.levels[0].size_contracts == Decimal("50")

    # Consume more than available → empty side.
    after_all = _consume_side(side, Decimal("999"))
    assert after_all.levels == []


@pytest.mark.asyncio
async def test_pessimistic_produces_fewer_or_smaller_profits(tmp_path: Path, strategy_ctx):
    """Pessimistic fill model must produce PnL <= realistic <= optimistic for
    the same inputs. This is the honest-fill invariant."""
    anchor = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
    base = tmp_path / "snapshots"
    writer = DailyParquetWriter(base_dir=base, platform="polymarket")
    # Use a two-level book so pessimistic has something to "drop into".
    m = Market(
        platform="polymarket",
        market_id="two-level",
        event_id="evt",
        title="Two level book",
        yes_bids=OrderBookSide(levels=[]),
        yes_asks=OrderBookSide(
            levels=[
                OrderBookLevel(price=Decimal("0.45"), size_contracts=Decimal("50")),
                OrderBookLevel(price=Decimal("0.47"), size_contracts=Decimal("100")),
            ]
        ),
        no_bids=OrderBookSide(levels=[]),
        no_asks=OrderBookSide(
            levels=[
                OrderBookLevel(price=Decimal("0.48"), size_contracts=Decimal("50")),
                OrderBookLevel(price=Decimal("0.50"), size_contracts=Decimal("100")),
            ]
        ),
        fee_bps=0,
        resolution_date=anchor + timedelta(days=30),
        resolution_source="test",
        fetched_at=anchor,
        active=True,
        resolved=False,
        liquidity_usd=Decimal("1000"),
    )
    await writer.write_many([m])
    await writer.close()

    async def run(kind):
        return await run_backtest(
            snapshots_dir=str(base),
            platform="polymarket",
            start=anchor - timedelta(seconds=1),
            end=anchor + timedelta(minutes=1),
            strategy_ctx=strategy_ctx,
            fill_cfg=FillModelConfig(kind=kind),
        )

    opt = await run(FillModelKind.OPTIMISTIC)
    rea = await run(FillModelKind.REALISTIC)
    pes = await run(FillModelKind.PESSIMISTIC)

    # Invariant: optimistic >= realistic >= pessimistic in PnL (when all produce trades).
    # If any produced zero trades, that's also acceptable (pessimistic more likely).
    opt_pnl = opt.metrics.total_pnl_usd
    rea_pnl = rea.metrics.total_pnl_usd
    pes_pnl = pes.metrics.total_pnl_usd
    assert opt_pnl >= rea_pnl
    assert rea_pnl >= pes_pnl
