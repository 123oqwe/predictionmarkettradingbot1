# Phase 3 — Paper to Live Transition

**Duration:** 2 weeks build + 4 weeks live running (realistically 2 months total)
**Goal:** Run real money at small size, calibrate paper predictions against live reality using uncertainty bounds, and make an evidence-based decision about the edge.
**Prerequisite:** Phase 2.5 exit criteria all met. You have a concrete baseline from the historical census.

---

## The core tension

Phase 3 is where most quant projects die emotionally, and where most quant projects that don't die learn to **stop measuring performance with point estimates**.

Here's the setup: you've built a system that says "this opportunity has expected profit $5.20." You go live. The actual profit is $3.10. Divergence: 40%. Is your model broken?

**You can't answer that with point estimates alone.** What you need is uncertainty bounds. If your paper model says "expected $5.20, 90% confidence interval [$2.50, $7.80]" and live returns $3.10, your model is well-calibrated — live fell within bounds. The point estimate diverged, but the probabilistic prediction was honest.

This is the core shift in Phase 3: paper stops being a point estimator and becomes a probabilistic model. Success is not "live matches paper exactly," it's "live falls within paper's predicted uncertainty most of the time." When that's true, you trust the model. When it's not, you fix the model — not by adjusting point estimates but by fixing whichever source of uncertainty you underestimated.

There's also a second tension: **the first version of "live" shouldn't be "let the agent trade at speed."** Prediction market arbitrage windows can be seconds. Manual approval is too slow. But unsupervised full auto is too risky for the first week. The gate system below threads that needle.

Phase 3 is emotionally brutal — you'll see live reality diverge from paper reality and feel like the project failed. It didn't. Your job is to measure the divergence and decide what it means, with data instead of feelings.

---

## Success criteria

1. Live execution module works end-to-end for real (small) trades
2. 2+ weeks of live PnL data at small size
3. Paper model outputs uncertainty bounds, not just point estimates
4. Calibration metric: live actual profit falls within paper's 90% CI at least 85% of the time (allowing some tail for unmodeled events)
5. You can make an informed SCALE/HOLD/REDESIGN/STOP decision with numbers, not vibes

Phase 3 output is **knowledge, not profit.** Breaking even or losing small amounts while learning is a valid outcome.

---

## The two questions, now with right framing

**Old question:** "Is paper PnL close to live PnL?"
**New question:** "When my paper model says 'expected $5, likely range $2-$8,' does live actually land in that range 90% of the time?"

The new question is better because:
- It acknowledges that trading is inherently probabilistic
- It gives you a clean pass/fail criterion (calibration percentage)
- It tells you exactly what to fix when it fails (which source of uncertainty was underestimated)

**How to output uncertainty bounds from the paper model:**

Each paper opportunity includes three numbers:

```python
class Opportunity(BaseModel):
    # ... existing fields ...
    expected_profit_usd: Decimal
    profit_p05_usd: Decimal  # 5th percentile — pessimistic
    profit_p95_usd: Decimal  # 95th percentile — optimistic
```

Sources of uncertainty to model:
1. **Slippage** — your fill price vs. the price you saw. Distribution based on historical backtest data.
2. **Partial fills** — sometimes you get less than requested. Modeled as a probability.
3. **Fees** — usually deterministic, but edge cases (volume tiers, promos) add variance.
4. **Time-to-resolution variance** — resolution can be early or late, affecting annualized.
5. **Rule divergence (cross-market only)** — the `p_divergence` from Phase 1.

For a first version, use conservative assumptions from Phase 2.5's historical data. Refine based on live observations.

---

## Capital plan

- **Total live capital:** $300–$500
- **Max per trade:** $25 (yes, twenty-five dollars)
- **Max concurrent exposure:** $150
- **Hard stop-out:** cumulative live loss > $75 → halt, review
- **Per-platform cap:** $250 (so you don't lock everything on one platform)

If these numbers feel small, they are. **The point is to generate calibration data, not profit.** A $75 loss here is cheap tuition. A $10k loss after skipping this stage is expensive tuition for the same lesson.

---

## Scope

- Live execution module
- Paper model upgraded to output uncertainty bounds
- Paired paper-vs-live logging (every live trade → shadow paper trade → pair comparison)
- Three-gate approval system (redesigned from the previous version)
- Postgres migration for transactional data
- Tax-aware PnL reporting
- Calibration dashboard

**Out of scope:**
- Scaling live capital
- Adding platforms or strategies
- Multi-user anything

---

## Gate 1 redesign: auto-execute with strict threshold + review window

The original version of this roadmap said "manually approve every trade." That's wrong for prediction market arbitrage — windows are too short for humans.

**The redesigned Gate 1:**

- **Threshold is temporarily extreme:** 60% annualized minimum (vs. your normal 20%)
- **Size cap is very small:** $10 max per trade
- **Auto-execute:** yes, immediately
- **Post-execution review pause:** after every fill, the orchestrator pauses for 10 seconds before accepting the next opportunity, and sends a Telegram alert with the fill details
- **Manual intervention window:** during the 10-second pause, you can hit a panic button to halt

This gives you speed where it matters (execution) and observation where it matters (between trades). In Gate 1 you'll probably do 0-3 trades per day, watch each one closely, and have time to inspect live-vs-paper for every single fill.

**Duration:** 3 days minimum, or 5 successful fills — whichever is longer.

**Graduation condition to Gate 2:** every Gate 1 fill was explainable; no surprises; paper predicted outcome falls within 2x the expected range (loose tolerance since sample is small).

### Gate 2: loosen threshold and size

- Threshold: 30% annualized
- Size cap: $20
- Pause: 5 seconds
- Alerts: still on every fill

**Duration:** 4 days minimum, 15+ fills.

**Graduation condition:** 85% of fills fall within paper's 90% CI. Divergence where it happens has identifiable explanation.

### Gate 3: normal operations at small size

- Threshold: 20% annualized (normal)
- Size cap: $25
- Pause: 0 (normal loop)
- Alerts: aggregated into hourly summary

**Duration:** 7+ days to accumulate enough data for robust calibration statistics.

**Final evaluation:** proper calibration analysis + decision document.

### What if you never graduate past Gate 1?

Legitimate outcome. It means either: (a) 60% annualized opportunities are too rare, so you spend a week with 3 trades and can't calibrate meaningfully, or (b) every Gate 1 trade produces a surprise, which is data itself. In case (a), lower Gate 1 threshold to 40% and try again. In case (b), go back to Phase 2.5 and figure out what the backtest missed.

---

## Postgres migration

Until now, SQLite has been enough. Phase 3 introduces real concurrency (live execution + reconciliation + monitoring + reporting all writing simultaneously), and SQLite's write serialization starts hurting.

**Migration plan:**

1. Set up local Postgres (Docker is fine for now)
2. Port the SQLite schema via SQLAlchemy / SQLModel (use whichever ORM you already have)
3. Write a migration script that copies historical SQLite data into Postgres
4. Switch transactional writes to Postgres
5. **Keep Parquet unchanged** — time-series data doesn't benefit from Postgres; it benefits from columnar storage.
6. Run the first week of Gate 1 on Postgres with SQLite still receiving shadow writes, compare nightly for sanity.

**Why now, not later:** Phase 4's matcher will write cache data, Phase 5 will add more strategies. SQLite concurrent writes get worse. Do the migration once, here, when the data volume is still small and the stakes are low.

**Why not a cloud Postgres:** unnecessary for Phase 3. Local is cheaper, faster, simpler. Cloud Postgres is a Phase 5 consideration if you're running remotely.

---

## Paired logging (the heart of Phase 3)

Every live trade triggers a shadow paper trade at the same moment. Both outcomes land in one row:

```python
class ExecutionRecord(BaseModel):
    opportunity_id: str
    detected_at: datetime
    executed_at: datetime

    # What paper predicted
    paper_expected_profit: Decimal
    paper_profit_p05: Decimal
    paper_profit_p95: Decimal
    paper_expected_fill_price_yes: Decimal
    paper_expected_fill_price_no: Decimal

    # What live actually did
    live_actual_profit: Decimal
    live_fill_price_yes: Decimal
    live_fill_price_no: Decimal
    live_fill_latency_ms: int
    live_partial_fill: bool
    live_slippage_bps: int

    # Calibration
    within_p5_p95: bool  # live fell inside paper's 90% CI
    divergence_bps: int  # signed difference, in basis points
    explanation: str | None  # filled in manually after review
```

**This is the data that answers the core question.** Every live trade produces one row. You'll stare at them a lot.

---

## Calibration dashboard

`scripts/calibration_report.py`:

```
=== Calibration Report (last 14 days) ===
Live trades executed: 27
Paper shadow predictions: 27

Calibration statistic: 22/27 = 81.5% of live fills fell within paper's 90% CI
  (target: ≥85%)
  STATUS: MARGINAL — needs investigation

Divergence by source (from manual review):
  Slippage (higher than model):  5 trades
  Fee tier surprise:              2 trades
  Resolution timing:              1 trade
  Unexplained:                    2 trades  <-- investigate these

Point-estimate comparison:
  Paper expected PnL: +$68.40
  Live actual PnL:    +$51.20
  Divergence:         -25.1% (point estimate)

Fill latency:
  p50:  410 ms
  p95: 1480 ms
  p99: 4200 ms

Slippage (live - paper expected, in bps):
  mean: +43
  p95:  +120
  worst: +230

"Opportunities that vanished before execution" rate: 24%
  (paper saw them; live couldn't catch them in time)
```

**The single most important metric is the calibration statistic.** If live falls in paper's 90% CI >= 85% of the time, the model is well-calibrated even if point estimates diverge. If calibration is below 85%, the model's uncertainty is too tight — it's claiming more confidence than it deserves.

**Second most important:** "opportunities that vanished before execution." This is the honest measurement of paper-vs-live latency. It tells you how much of your paper edge was a timing illusion.

**Third most important:** the unexplained bucket. Every live fill should have a post-hoc explanation. Unexplained rows are bugs.

---

## Tax reality check

Phase 3 also adds a sober PnL report that accounts for taxes.

**Realistic US tax assumptions** (check with your accountant; these are illustrative):

- **Kalshi** (CFTC-regulated swap contracts): typically 60/40 treatment under Section 1256. Marginal ~28–35% depending on bracket.
- **Polymarket** (offshore, on Polygon): likely ordinary income, treated as short-term capital gains or other income. In NYC, combined federal + state + city marginal can hit ~45-50%.
- **Wash sale rules, carryover losses, state nexus issues**: get an accountant before live capital exceeds $5k.

**Reporting:**

```python
class AfterTaxReport(BaseModel):
    gross_pnl: Decimal
    estimated_tax_polymarket: Decimal  # applied to polymarket trades
    estimated_tax_kalshi: Decimal      # applied at 60/40 blended rate
    net_pnl: Decimal
    effective_tax_rate: Decimal
    annualized_net: Decimal
    benchmark_rfr: Decimal              # risk-free rate, currently ~4%
    risk_premium_required: Decimal     # config value, default 5%
    excess_over_benchmark: Decimal
    # If negative, your strategy is losing to a T-bill after tax
```

**A 6% annualized strategy, after NYC tax of ~45%, is 3.3% net. Risk-free rate is ~4%. You're losing money to a T-bill.** Know this number before Phase 3 scaling.

Run this report weekly. It's a reality check against the natural tendency to celebrate gross PnL while ignoring after-tax reality.

---

## Deliverables

### 1. Live execution module

`src/layer4_execution/live.py`:
- Real API calls for order placement
- Handles partial fills (not rare — common)
- Handles order rejection and timeouts
- Writes every attempt (including failures) to Postgres
- Uses Phase 2 idempotency keys

Only enabled with `--live` flag AND `config.mode: live` AND valid API keys loaded from env. Three gates against accidental live execution.

### 2. Uncertainty-bounded paper model

Refactor `compute_opportunity` to output (expected, p5, p95) using the uncertainty sources listed above. Bootstrap the uncertainty distributions from Phase 2.5 historical data.

### 3. Three-gate approval system

Gate 1/2/3 as described, with config-driven thresholds and pauses.

### 4. Postgres migration

Local Postgres setup, schema port, migration script, cutover.

### 5. Paired logging

Every live fill → shadow paper trade → ExecutionRecord row.

### 6. Calibration dashboard

`scripts/calibration_report.py` with all the metrics above. Runs daily.

### 7. After-tax PnL report

`scripts/aftertax_report.py` with jurisdiction-aware calculations. Config:

```yaml
tax:
  jurisdiction: us_nyc
  brackets:
    federal_marginal: 0.35
    state: 0.065
    city: 0.04
  kalshi_treatment: section_1256  # 60/40 long/short blend
  polymarket_treatment: ordinary_income
  risk_free_rate: 0.04
  risk_premium_required: 0.05
```

### 8. Decision document template

`docs/phase3_decision_template.md`:

```markdown
## Phase 3 Decision — {date}

Duration: {days} live days
Trades: {n}

Gross PnL: ${gross}
After-tax PnL: ${net}
Annualized net: {pct}%
Excess over (risk-free + premium): {pct}%

Calibration statistic: {pct}% (target 85%)
Unexplained divergence count: {n}

Root causes identified:
  - ...

Fixes applied to paper model:
  - ...

Decision: [ SCALE | HOLD | REDESIGN | STOP ]

Reasoning:
```

Decision criteria (use literally, do not fudge):

- **SCALE (→ Phase 4):** calibration ≥ 85%, after-tax excess > 0, no unexplained divergence
- **HOLD:** calibration 75-85% OR excess marginal OR a few unexplained divergences. Run another 2 weeks at current gate with model updates.
- **REDESIGN:** calibration < 75% OR after-tax negative OR systemic unexplained issues. Go back and fix whatever Phase 2.5 missed. Re-run Phase 3 from scratch.
- **STOP:** can't explain divergence after 4+ weeks, or after-tax excess is structurally negative. The edge isn't real. Phase 3 ends; infrastructure is still valuable.

---

## Task breakdown

### Task 3.1 `[research]` — Exchange execution deep dive
Each platform: exact order types (IOC, FOK, limit, market, post-only), minimum sizes, funding/withdrawal flow, fee tiers and triggers, settlement timing, KYC requirements, rejection codes and meanings. Document thoroughly.

### Task 3.2 `[slog]` — Fund the accounts
Deposit $300–500 on each platform. Settle. Verify balances. Before trusting anything: withdraw $1 back successfully. Full round-trip test.

### Task 3.3 `[infra]` — Postgres setup and migration
Local Postgres via Docker. Schema port. Data migration script. Shadow-write period.

### Task 3.4 `[math]` — Uncertainty-bounded paper model
Bootstrap uncertainty distributions from Phase 2.5 data. Update Opportunity model. Update tests.

### Task 3.5 `[infra]` — Live execution module
Unit tests with mocked API for every code path. Integration test with a single real $5 order (to be cancelled immediately).

### Task 3.6 `[math]` — First live trade, manual
Bypass the agent. Use execution module directly to place one small order on a liquid market. Cancel immediately if unfilled. Verify SQLite, Postgres, and exchange state all agree.

### Task 3.7 `[infra]` — Three-gate system
Implement gates, thresholds, pauses, panic button.

### Task 3.8 `[slog]` — Run Gate 1 for 3+ days
Watch every fill closely.

### Task 3.9 `[infra]` — Paired logging
ExecutionRecord schema, logging on every fill.

### Task 3.10 `[infra]` — Calibration dashboard
Daily script with all metrics.

### Task 3.11 `[math]` — Tune uncertainty bounds after Gate 1
Review which divergences weren't covered by the 90% CI. Update the uncertainty model. Re-verify in Gate 2.

### Task 3.12 `[slog]` — Graduate through gates
Gate 2 after Gate 1 success. Gate 3 after Gate 2 success. Do not skip.

### Task 3.13 `[infra]` — After-tax reporting
Config, calculations, weekly report.

### Task 3.14 `[research]` — Write decision document
After 2+ weeks at Gate 3. Honest numbers.

---

## Gotchas

**First live trade exposes a bug.** Guaranteed. Some edge case — unexpected order type rejection, a field you serialized wrong, a rejection code you didn't handle. Size = $5 so the bug costs lunch money.

**KYC walls appear at withdrawal, not deposit.** You can fund, trade, and try to withdraw — and get blocked. Complete KYC **before** funding.

**First-withdrawal delays.** Platforms add extra verification. Budget days. Test this before you need the money.

**Fee surprises.** Published rate says 1%. Actual fills show 1.3%. Maker/taker distinction? Volume tier? API calculating differently? Reconcile fees against actual fills for every Gate 1 trade, not just totals.

**Partial fills breaking delta neutrality.** You want 100 contracts of YES and 100 of NO. YES fills all, NO fills 63. Now you have 37 unmatched YES contracts — a directional bet. Decide the policy: aggressively market-buy the remaining NO (costs slippage), cancel the YES difference (costs the fill you got), or hold to resolution (exposes you to single-market risk). **Decide now, implement now, test now.** Don't decide in the moment.

**Reconciliation on different settlement times.** Exchanges have clearing cycles. Your balance at 3pm might differ from your balance at 5pm because of pending settlements. Reconciliation logic has to know the settlement cycle of each platform.

**Stop-loss psychology.** When the $75 stop triggers at $73, you'll want to move it. Don't. The entire point is discipline. Moving the stop defeats the purpose.

**Survivorship bias in paper mode.** Paper "saw" opportunities that were already gone by the time live could have executed. The "vanished" rate measures this honestly. If it's > 40%, your latency is too high for the strategy to work at scale.

**Tax rate changes mid-year.** You're running Phase 3 in April. Cap gains rates could change in a future tax bill. The tax report uses current config; be aware that yearly tax liability is computed at year-end with whatever rates are in effect then.

**The "big win" trap.** You catch one exceptional opportunity and make $40 on a day. It's unlikely to repeat. Don't extrapolate from single events; your decision metric is 14+ days of data, not any single outlier.

---

## What to expect, realistically

**Week 1 (Gate 1):** 1-5 trades, probably small loss or break-even. At least one bug surfaces. Most of your time is debugging, not trading. Don't panic — this is what Gate 1 is for.

**Week 2 (Gate 2):** 10-25 trades. First calibration report lands. Almost certainly shows some divergence. You start understanding which sources of uncertainty you underestimated.

**Week 3-4 (Gate 3):** Enough trades to run real statistics. Calibration either lands at target or doesn't. Unexplained divergences either shrink or don't.

**Decision point:** most likely outcome is HOLD or REDESIGN, not SCALE. That's fine. Iterating on the model is the actual work of Phase 3, not a failure.

**If SCALE:** you're in a minority. Proceed to Phase 4 with lessons learned.

**If HOLD/REDESIGN:** normal, continue iterating.

**If STOP:** totally legitimate. Phase 2.5 infrastructure, backtest engine, and paper model updates are all generically valuable for future strategies. Calling it off after Phase 3 is much better than after Phase 5 with more lost capital.

---

## Failure modes and when to loop back

- **Bugs in Gate 1 should have caught them.** Slow down. More Gate 1, smaller sizes.
- **Calibration gets worse, not better, over time.** Something is degrading: fee tiers may be changing, market structure may be shifting. Investigate before blaming model.
- **Unexplained trades.** Hard stop. Root cause the mystery before accumulating more data. Unexplained is always worse than expected.
- **Tempted to increase size before calibration hits target.** Don't. Whatever's true at $25 is true at $250, except 10x more expensive to learn.
- **Tempted to cherry-pick a "good period" for the decision.** The decision is on the full Phase 3 dataset, not a subset. Cherry-picking is how you fool yourself.

---

## Cost budget

Phase 3 should cost **< $150/month plus capital**:
- Postgres local: free
- Exchange fees: variable based on trade volume, but small at Gate 1-3 sizes
- Trading capital: $500 (one-time)
- Maybe a cheap accountant consultation: $150 once

Watch the ratio of fees to PnL. If fees are eating >30% of gross profit at Gate 3, that's a critical finding for the decision doc.

---

## Exit criteria → Phase 4

- [ ] 2+ weeks of Gate 3 live data
- [ ] Calibration statistic ≥ 85% OR a documented and tested fix
- [ ] After-tax net excess over risk-free + premium is positive
- [ ] Decision document written with honest numbers
- [ ] At least one full deposit → trade → withdrawal cycle on every live platform
- [ ] Paper model has been updated based on live observations and re-verified
- [ ] Postgres migration complete, no SQLite data loss
- [ ] Every fill has an explanation; unexplained count is zero

If any fail, loop back. Do not advance on hope.
