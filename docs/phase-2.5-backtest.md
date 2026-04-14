# Phase 2.5 — Historical Data, Replay, and Backtest Engine

**Duration:** 2 weeks (realistically 2–3)
**Goal:** Turn the Parquet log you've been recording since Phase 0 into a deterministic backtest and replay engine that makes every subsequent decision evidence-based instead of vibe-based.
**Prerequisite:** Phase 2 exit criteria all met.

---

## The core tension

This phase exists because of a painful realization most quant projects have around Phase 3: **you cannot make Phase 3 / 4 / 5 decisions without historical data analysis.** The calibration in Phase 3 needs a baseline. The matcher in Phase 4 needs training data. Every new strategy in Phase 5 needs backtest evidence.

In the previous version of this roadmap, backtesting was casually mentioned as a "Phase 5 refactor." That was wrong. Running Phase 3 without a backtest engine is like running an A/B test without a control group. You end up arguing about whether divergence is "normal" with no way to look at the actual history.

So Phase 2.5 exists. It's short, it's satisfying, and it's entirely leverage — everything you built in Phases 0–2 is already compatible because the four-layer architecture was designed with replay in mind. This phase is mostly about adding the tools that sit on top of the layers and let you ask historical questions.

The core tension: **resist the urge to optimize strategies during backtesting.** The goal of Phase 2.5 is to build the tooling, not to discover edge. Optimization belongs in Phase 5, once live results exist to anchor backtests to reality.

---

## Why backtests lie, and what to do about it

Before building anything, understand the three lies backtests tell:

**Lie 1: Fill optimism.** Backtests assume you got the price you saw. Live execution competes with other bots, slips, or misses entirely. Your backtester must model conservative fills — e.g., assume 1-tick slippage on every trade, or assume you get the next level down, not the top.

**Lie 2: Survivorship and lookahead.** If you backtest only on markets that exist today, you exclude markets that delisted, resolved badly, or had data issues. Backtest with the raw recorded log, including markets that no longer exist.

**Lie 3: Strategy-data cross-contamination.** If you tune strategy parameters on the same data you use to validate them, you're fitting to noise. The standard defense is train/validate split: tune on one time window, validate on another.

The backtest engine has to be built with these lies in mind, not "add realism later."

---

## Success criteria

1. Replay script reproduces any historical time window deterministically — byte-identical output when run twice
2. Backtest engine runs a complete strategy configuration against historical data and produces a PnL report
3. A/B comparator runs two strategy configs on the same historical window and shows the difference
4. Historical opportunity census report tells you, concretely, "how many opportunities existed in the last month, at what annualized returns, with what capital-at-risk"
5. At least one insight from backtesting changes a config value or surfaces a bug you didn't know about

---

## Scope

**In scope:**
- Replay script (wraps the Layer 2 replay stream you already have)
- Backtest engine that runs end-to-end (Layer 2 → Layer 3 → Layer 4 paper) against historical Parquet
- Conservative fill model for backtesting
- Train/validate time splits
- A/B strategy comparator
- Historical opportunity census report
- Basic backtest UI (CLI, not web)

**Out of scope:**
- Cloud infrastructure for scaling backtests
- Automatic parameter optimization (Phase 5 at earliest)
- Live-vs-backtest calibration (Phase 3's job, not this phase)
- ML model training (Phase 4 at earliest)
- Real-time strategy recalibration

---

## The four tools Phase 2.5 delivers

### Tool 1: Deterministic replay (`scripts/replay.py`)

Simple in principle: feed historical Parquet through Layers 2-3-4, produce the same output as running live would have.

```bash
# Replay a specific time window
python scripts/replay.py --from '2026-04-10 14:00' --to '2026-04-10 18:00' \
    --strategy intra_market --config config.yaml

# Output:
# Replayed 14,412 market snapshots
# Detected 187 opportunities
# After filters: 42
# Above threshold: 9
# Paper trades: 9
# Simulated PnL: +$47.30
# Determinism check: PASS (hash=abc123)
```

The determinism check hashes the full output and compares against a previous run on the same inputs. Any difference → Layer 3 has hidden state, find it.

**Why this matters beyond determinism:** replay is the primary debugging tool for Phase 3. When live produces an unexpected trade, replay that moment in paper, see what the strategy layer would have done, compare.

### Tool 2: Backtest engine (`scripts/backtest.py`)

A step up from replay. Runs a full time range with more sophisticated controls:

```bash
python scripts/backtest.py \
    --from '2026-03-01' \
    --to '2026-04-01' \
    --config configs/conservative.yaml \
    --fill-model pessimistic \
    --output reports/backtest-2026-03.html
```

Key features:

**Conservative fill model.** When the strategy says "fill at 0.45," the backtester fills at the next level down (or skips the trade entirely if no second level exists). This under-counts profit, which is what you want — any strategy that shows edge under pessimistic fills has some chance of real edge.

Three fill models, configurable:
- `optimistic` — top of book (for upper-bound estimates; rarely used)
- `realistic` — top of book with 2-tick slippage (default)
- `pessimistic` — next level down, full size only if available (for validation)

**Time-range specification.** Dates, not just offsets. Lets you deliberately backtest over specific events ("how did the strategy behave around the April FOMC?").

**Event map snapshotting.** If `event_map.yaml` changed during the backtest window, the backtester must use the version that was active at each moment, not the current one. Phase 2 provenance (event map hashes) makes this possible. If you can't find the historical event map, print a warning and use an approximation.

**Report output.** An HTML report (or markdown; HTML is nicer for charts) with:
- Cumulative PnL chart
- Trade list with fills
- Annualized return distribution (passed vs rejected)
- Capital utilization over time
- Breakdown by strategy, by platform, by pair
- Key numbers at top: total trades, win rate, Sharpe, max drawdown, avg annualized return

### Tool 3: A/B comparator (`scripts/ab_backtest.py`)

Runs two configs on the same historical window, prints the differences:

```bash
python scripts/ab_backtest.py \
    --from '2026-03-01' --to '2026-04-01' \
    --config-a configs/threshold_20.yaml \
    --config-b configs/threshold_25.yaml \
    --output reports/ab-threshold.html
```

The report answers questions like:
- Which config made more trades?
- Which made more absolute PnL?
- Which had better Sharpe?
- Which had better capital efficiency (PnL per dollar-day of capital locked)?
- Which had a worse max drawdown?
- Which exposed you to more tail risk?

**Don't read the A/B result and immediately change config.** Backtests lie (see above). The A/B comparator tells you "here's what would have happened in the past," which is useful context but not a decision. Decisions come after Phase 3 live calibration.

### Tool 4: Historical opportunity census (`scripts/census.py`)

This one is diagnostic, not trading. It answers: **what does my opportunity landscape actually look like?**

```bash
python scripts/census.py --from '2026-03-01' --to '2026-04-01' --output reports/census-march.md
```

Sample output:

```
=== Opportunity Census: 2026-03-01 to 2026-04-01 ===

Total snapshots processed: 412,814
Markets observed (unique): 638
Opportunities detected (any ann_return): 3,428

Distribution by annualized return:
  0-10%:     ██████████████████ 1,842
  10-20%:    ███████ 721
  20-30%:    ███ 312
  30-50%:    ██ 219
  50-100%:   █ 142
  100-500%:  ▌ 98      <-- suspicious, check why
  >500%:     ▏ 94      <-- very suspicious, likely data/math errors

Distribution by days-to-resolution (above 20% ann):
  <5 days:   ████████ 412      <-- may be capacity-limited
  5-30 days: ██████ 287
  30-90 days: ███ 145
  90+ days:  █ 53

Distribution by strategy:
  intra_market: 798 above 20%
  cross_market: 173 above 28%

Distribution by platform pair (cross-market only):
  polymarket-kalshi: 173 above 28%

Median opportunity lifetime (time from first visible to disappearance):
  intra_market: 14 seconds
  cross_market: 47 seconds

Adverse selection filter rejection reasons:
  too_old:        28% of rejections
  news_window:    41%
  young_market:   19%
  min_liquidity:  12%
```

**What to look for in a census:**

- **A big bucket at >100% annualized** is almost always a bug. Investigate every opportunity in that bucket by hand. Probably resolution dates are wrong, or fees are being underestimated, or the fill model is too optimistic.
- **Skew toward very short resolution times** means your strategy is finding day-of-event opportunities. These are capacity-limited in live — you can't actually execute 30 of them in a day.
- **Very short opportunity lifetimes** (median < 10 seconds) mean you're competing with HFT bots and your live fill rate will be poor.
- **Filter rejection dominated by one reason** is a signal your filters are lopsided. Maybe too aggressive on news windows, too permissive on age.

This report is where the backtest engine earns its keep. You'll learn more from reading your first census than from all the implementation work combined.

---

## Deliverables

### 1. Replay script (as above)

### 2. Backtest engine

`src/backtest/` as a new subdirectory:
- `runner.py` — coordinates Layers 2 → 3 → 4, with backtest-specific Layer 4
- `fill_model.py` — three fill models
- `report.py` — generates HTML/markdown reports
- `metrics.py` — Sharpe, max drawdown, PnL-per-dollar-day, etc.

### 3. Historical event map loader

A helper that, given a timestamp, loads the event map version that was active at that moment. Uses the hashes recorded in Phase 2 provenance. If no matching version can be found, falls back to the oldest known version (with warning).

### 4. A/B comparator

`scripts/ab_backtest.py` — runs two configs, diffs results.

### 5. Census report

`scripts/census.py` — descriptive statistics of the opportunity landscape.

### 6. Backtest integration tests

Tests that run the backtest engine against a tiny synthetic Parquet file with known outcomes, verify exact expected results. Guards against regressions in the backtest logic itself.

---

## Task breakdown

### Task 2.5.1 `[infra]` — Replay script
Wrap Layer 2 replay stream. Add determinism check. Test against 1 hour of recorded data — two runs must produce identical output.

### Task 2.5.2 `[infra]` — Backtest Layer 4 (paper filler with fill model)
Fill model selectable. Applies slippage rules consistently. Writes results to a backtest-specific SQLite file (not the production state db).

### Task 2.5.3 `[math]` — Fill models
Optimistic, realistic, pessimistic. Unit tests for each, with synthetic order books that exercise edge cases (empty second level, asymmetric books, book that fills partially).

### Task 2.5.4 `[infra]` — Historical event map loader
Given a timestamp, find the active event map version. Requires querying Phase 2 provenance records.

### Task 2.5.5 `[infra]` — Metrics module
Sharpe, max drawdown, annualized return, capital efficiency, win rate. Unit tests with known inputs.

### Task 2.5.6 `[infra]` — Backtest runner + report generator
End-to-end. Produces HTML or markdown report.

### Task 2.5.7 `[infra]` — A/B comparator
Runs two configs, diffs. Report highlights the differences.

### Task 2.5.8 `[math]` — Census report
Descriptive statistics only — no decisions, no trading logic. Just "what did the opportunity landscape look like?"

### Task 2.5.9 `[research]` — Read the first census
This is the most important task in the phase. Sit down with the first real census report and **understand what you're seeing.** Flag suspicious buckets. Investigate by hand. This is the moment you start knowing your market.

### Task 2.5.10 `[infra]` — Backtest integration test
Synthetic Parquet with known-outcome trades. Backtester must produce the exact expected PnL.

---

## Gotchas

**Determinism failures surface now, not in Phase 0.** Even with a clean Phase 0 determinism test, adding Kalshi + cross-market + filters may have introduced non-determinism (ordering, iteration order, datetime.now() leaks). Run the determinism check again as the first test of the new replay script. Fix immediately if it fails.

**Fill model too pessimistic → backtest shows no edge.** If the pessimistic model says your strategy is unprofitable but realistic says it is, you have a genuine question: which model is closer to reality? You can't know until Phase 3 live calibration. Run both, keep the results side by side, wait.

**Fill model too optimistic → false confidence.** More dangerous. If backtest shows a 40% annualized strategy but live can't produce 10%, you'll fund live trading based on false hope. **Default to pessimistic for any new strategy decision.**

**Floating point in Sharpe calculations.** Sharpe is `mean / std`, which involves many intermediate floats. Your Decimal discipline from Phase 0 is harder to maintain here. Accept float for metrics that are inherently statistical, but keep Decimal for money.

**Memory usage on long backtests.** Backtesting a month of data with 300+ markets × 5-second snapshots = millions of rows. If you naively load everything into memory, you OOM. Use Parquet's streaming reads with pyarrow/polars; process chronologically; only keep state that's necessary for the current time window.

**Event map versioning gaps.** If you don't have a complete history of event map versions, some historical windows can't be backtested faithfully. Document the gap. Use a closest-approximation with a warning. Don't silently substitute the current event map — that's a form of lookahead bias.

**Resolution data is not historical.** At time T in the past, a market's resolution might not have happened yet. The backtester needs to know what happened in the future to compute realized PnL for backtested trades. Solution: rely on current state of resolved positions (which you've been tracking since Phase 0). For markets still unresolved, compute unrealized PnL only.

**Configuration drift.** The config used when you recorded data is not the current config. Your backtest is evaluating "what would current config have done on historical data," which is a valid question but not the only one. Also run "what did actual config do on historical data" using historical config hashes.

---

## What to actually do with the census

Once the first census lands, spend a day with it. Ask:

1. **Is there any edge at all?** How many opportunities passed filters and thresholds, in absolute terms? Less than 10/month = very hard to make this strategy work. 10-30/month = plausible. >30/month = likely viable. Capital-adjusted, of course.

2. **Where does the edge live?** By strategy, by platform pair, by event category. Concentration tells you where to invest Phase 5 effort and where to ignore.

3. **What's my realistic daily win count?** With capital caps, how many trades can I actually execute per day? If the census shows 50 opportunities/day but my capital only supports 5, the excess is phantom edge.

4. **Are the suspicious buckets real or bugs?** The >100% annualized bucket is usually bugs. Investigate a sample by hand.

5. **Is my rejection profile sensible?** If 80% of rejections are from one filter, maybe that filter is miscalibrated. If rejection rates are 10%, maybe your filters are too loose.

The answers become the factual basis for Phase 3 decisions. Without this phase, Phase 3 is running blind.

---

## Failure modes and when to loop back

- **Determinism test fails on new replay script.** Find and fix before proceeding. Probably a datetime leak or an iteration order issue in newly-added strategy code.
- **Backtest says strategy is unprofitable under any fill model.** Not necessarily fatal — live might still work if the model is too conservative. But flag it: you're entering Phase 3 with low confidence, so Phase 3 capital and expectations must be lower.
- **Census reveals an obvious bug** (e.g., hundreds of opportunities at 1000% annualized). Fix before Phase 3. This is why the census exists.
- **Historical event map gaps.** Accept them, document them, work around them. Phase 3 onward will have complete event map history.
- **Memory issues on month-long backtests.** Switch to streaming reads. Don't paper over with "just use a smaller window" — you need to backtest long windows in Phase 3+.

---

## Cost budget

Phase 2.5 should cost **< $100/month**:
- No new infrastructure; backtests run locally
- Possibly more Parquet storage as log grows: still small
- LLM calls for report formatting: optional, minor
- You might want to spend on a decent CSV/parquet viewer, like tad: under $50 one-time

---

## Exit criteria → Phase 3

- [ ] Replay script produces deterministic output (verified via hash comparison)
- [ ] Backtest engine runs end-to-end with all three fill models
- [ ] A/B comparator runs and produces clear diffs
- [ ] Historical census report generated and read carefully
- [ ] At least one actionable insight from the census changed a config value or fixed a bug
- [ ] Integration test for backtest engine passes (synthetic data → expected output)
- [ ] Historical event map loader handles the case of missing versions gracefully
- [ ] You have a concrete answer to "how many opportunities per day can my capital actually support?"
- [ ] You have a concrete answer to "what's my realistic annualized return based on the last 30 days of data?"

That last number is the baseline you'll compare live results against in Phase 3. It's the single most important number in the whole project.
