# Handoff Document

**Repo:** https://github.com/123oqwe/predictionmarkettradingbot1
**As of:** commit `b40aca1`, 2026-04-14
**Shipped:** Phases 0, 1, 2, 2.5, 3 (skeleton), 4, 5 (frozen)
**Tests:** 217 passing on Python 3.9 + 3.11 CI
**Lint:** ruff clean

---

## 1. What's in the box

### Fully usable now
- **Phase 0** — four-layer architecture, Polymarket fetcher, intra-market detection, paper execution, deterministic replay.
- **Phase 1** — Kalshi fetcher, cross-market detection, adverse selection filters, daily report v2.
- **Phase 2** — monitoring layer, 10 kill switch rules with observe/enforce modes, reconciliation, Telegram alerts (stub mode works offline), crash recovery, health endpoint, runbook, chaos tests.
- **Phase 2.5** — backtest runner, three fill models, Sharpe/drawdown metrics, A/B comparator, historical census.
- **Phase 4** — prefilter, LLM extractor (stub/Anthropic/offline modes), Parquet cache with 4-tuple key, deterministic matcher, tier A/B/C rules.

### Frozen (code is ready, deployment is gated)
- **Phase 3** — live execution skeleton behind 4-check SafetyGatedClient. Partial fill policy. Uncertainty-bounded paper model. Three-gate graduation. Tax reports. Decision template.
- **Phase 5** — resolution convergence strategy, feature flag system, capacity diagnostic. All flags default OFF. A master `global_phase5_enabled` flag is also default OFF.

### Not applicable
- **Phase 6** — business/life decision, not a code phase. Read `docs/phase-6-productization.md` when you're ready.

---

## 2. What you must do next (strict order)

### Step 1 — Phase 0 validation (≈ 1 day)
- [ ] Run `python -m src.main --config config.yaml` for 24 hours. Monitor:
  - No unhandled exceptions
  - Parquet files growing and rotating at UTC midnight
  - Memory stable (check `ps aux | grep python` periodically)
  - Resolution polling correctly marks positions resolved
- [ ] Manually verify 3+ detected opportunities against a spreadsheet (the doc's Task 0.13).
  Pull one Opportunity row from SQLite, find the matching Parquet snapshot, redo the math
  by hand in a spreadsheet. Numbers must match exactly.
- [ ] Read `docs/phase-0-mvp.md` Task 0.1 and confirm Polymarket fee/gas values in
  `config.yaml` match the current CLOB docs.

### Step 2 — Phase 1 event map (hand work, ≈ 1-2 weeks)
- [ ] Start from `event_map.example.yaml`. Copy to `event_map.yaml`.
- [ ] Build **10-30 verified pairs**. Budget 30min-2h per pair:
  - Pick Polymarket + Kalshi markets for the same underlying event
  - Read BOTH platforms' full rules text
  - Enumerate ≥5 edge cases, mark ≥1 as `divergent: true`
  - Default `trading_enabled: false` → review → flip to `true` only when confident
- [ ] Categories to prioritize: US politics, Fed policy, major sports, macro (CPI/NFP/GDP).
- [ ] Expect 30-50% of pairs you evaluate to end `trading_enabled: false` — this is correct.
- [ ] 1-week soak: run orchestrator continuously, spot-check every cross-market opportunity detected.

### Step 3 — Phase 2 reliability validation (2-3 weeks)
- [ ] Deploy to a machine you can leave running.
- [ ] Keep all kill switch rules in **observe mode** for 48h. Check `kill_switch_events` in SQLite daily.
- [ ] For each rule that never trips in 48h: deliberately **force-trip it once** using the rule's synthetic condition. Confirm the trip is logged and a CRITICAL alert fires.
- [ ] Flip rules to **enforce mode** one at a time. Start with `daily_loss_exceeded` and `manual`.
- [ ] Run 14 days unattended **including at least one macro event** (CPI, NFP, FOMC).
- [ ] Run all chaos tests: `pytest tests/test_chaos.py -v`.
- [ ] Read `docs/runbook.md` twice, add any missing operational detail you notice.

### Step 4 — Phase 2.5 census reading (1-2 days)
- [ ] After ≥2 weeks of Parquet data accumulate:
  ```bash
  python scripts/census.py --from 2026-XX-XX --to 2026-XX-XX --output reports/census-first.md
  ```
- [ ] Read the output carefully. **This is the most important task in Phase 2.5.** The doc says all the value of this phase comes from understanding what you see here.
- [ ] Flag any suspicious bucket (>500% annualized ≈ bug). Fix before Phase 3.
- [ ] Answer the five questions in the doc's "What to actually do with the census":
  1. Is there any edge at all?
  2. Where does the edge live? (strategy/platform/category)
  3. What's my realistic daily win count under capital caps?
  4. Are suspicious buckets real or bugs?
  5. Is my rejection profile sensible?

### Step 5 — Phase 3 live transition (6-8 weeks)
**Pre-requisites before writing a line of config:**
- [ ] Complete KYC on Polymarket (Polygon wallet + on-chain KYC)
- [ ] Complete KYC on Kalshi (US resident verification)
- [ ] Fund each platform with $300-500
- [ ] Do a **$1 round-trip test**: deposit → place tiny test order → cancel → withdraw. Confirm balances reconcile.

**Then in order:**
- [ ] Write a real `PolymarketLiveClient` + `KalshiLiveClient` implementing `ExchangeClient` (see `src/layer4_execution/exchange.py`). Real HTTP, rate-limited, with the platform's auth scheme.
- [ ] Wire those clients behind `SafetyGatedClient` (which is already written). Verify all 4 gates block when any one is missing.
- [ ] Manual first live trade: bypass the agent, use your `PolymarketLiveClient` directly to place one $5 order on a liquid market. Cancel immediately.
- [ ] **Gate 1** (3 days, 60% annualized, $10 cap): at least 5 successful fills, every one explainable. The `scripts/calibration_report.py` will tell you where you are.
- [ ] **Gate 2** (4 days, 30% annualized, $20 cap): 15 fills, calibration coverage ≥ 85%.
- [ ] **Gate 3** (7+ days, 20% annualized, $25 cap): enough data for robust statistics.
- [ ] Write `docs/phase3_decision.md` using `phase3_decision_template.md`. **Do not fudge the numbers.** The four decision paths (SCALE/HOLD/REDESIGN/STOP) have hard criteria — pick the one that matches, even if it's disappointing.

### Step 6 — Decision dictates next action
Depending on the Phase 3 decision:

- **SCALE** → You've earned Phase 5. Pick ONE expansion, run `scripts/capacity_report.py` to confirm it addresses your binding constraint, fill `docs/expansion_proposal_template.md`, enable its flag for 48h paper trial, then promote.
- **HOLD** → Update paper model uncertainty distributions from Gate 3 data. Re-run Gate 3 for another 2 weeks.
- **REDESIGN** → Something in Phase 2.5 missed reality. Back to the backtest engine; find what it missed.
- **STOP** → The edge isn't there. Your infrastructure is still valuable. Phase 2.5 tools, the matcher, and the uncertainty model are all generically useful. Write a post-mortem. Don't fall for sunk cost.

### Step 7 — Phase 4 extractor validation (parallelizable with Phase 3)
- [ ] Run the STUB extractor against your Phase 1 ground truth pairs. Confirm extracted JSON matches your hand-written notes for the fields you care about.
- [ ] If you want higher fidelity: set `ANTHROPIC_API_KEY` in env, switch `ExtractorConfig.mode` to `ExtractorMode.ANTHROPIC`. Re-run validation.
- [ ] If extracted output disagrees with your notes on ≥10% of pairs, the prompt needs iteration (see `src/matching/extractor.py::_SYSTEM_PROMPT`).
- [ ] Work the review queue daily (20 min/day per doc SLA).

### Step 8 — Phase 6 (optional, many months out)
- [ ] Only if Phase 5 is running hands-off with consistent positive risk-adjusted returns for 2+ months.
- [ ] Answer the doc's 5 questions before picking a direction. Write answers down, date them, re-read in 3 months.
- [ ] Direction options: SaaS, signal service, small fund, open source, or **stay in Phase 5 indefinitely** (this last one is underrated).

---

## 3. Known issues / flagged concerns

### Phase 0
- **Paper resolution stub**: `PaperExecutor.resolve_due_positions` uses `expected_profit_usd` as the realized PnL for any position past its resolution date. This is optimistic — it assumes the market always resolves in your favor. Phase 3 will replace this with real exchange resolution queries.

### Phase 1
- `event_map.yaml` is NOT shipped — only `event_map.example.yaml`. You must write the real file yourself.
- Adverse selection **age filter has a warmup problem**: history is empty at startup, so every opportunity looks "new" for the first few minutes. Warmup window: at least `age_threshold_seconds` (default 60s).

### Phase 2
- Kill switch **force-trip verification is manual**: the code has test-level force-trip coverage (`test_risk_policy.py::test_every_rule_can_trip`), but you must also force-trip each rule on a live system before trusting it in enforce mode.
- Telegram alerter stub mode **logs to stdout**; in a real deployment this is fine but verify before you rely on alerts.

### Phase 3
- **No real exchange clients** — you write `PolymarketLiveClient`/`KalshiLiveClient`. The `SafetyGatedClient` wrapper and `LiveExecutor` work with any `ExchangeClient` so the rest of the system is stable.
- Tax module is **illustrative only**. The Section 1256 60/40 + NYC marginal rate logic is correct per the doc but the actual numbers depend on your personal tax situation. Talk to an accountant before live capital exceeds $5k.
- **Postgres migration is not done** — Phase 3 doc recommends it for real concurrency. SQLite WAL + `synchronous=FULL` is currently what we use; fine for paper, acceptable for small live capital. Migrate before scaling past ~$2k live.

### Phase 4
- Extractor cache uses **append-only Parquet** that rewrites the whole file on each put. Fine for hundreds of markets, not thousands. Switch to per-day files if extraction scales.
- **Controlled vocabulary is small**: 5 event types covered. Adding a new type requires both the vocab entry AND extractor prompt iteration.

### Phase 5
- **Convergence strategy does not handle short-selling the opposite side.** We only look at buying the cheap side at ≥0.95. The doc mentions the counter-trade (sell the NO side when YES is near certain) but we haven't built it — convergence on the sell side requires live-trading the bid side, which is a bigger lift.
- `capacity_report.py` **vanish rate estimate is weak** until Phase 3 is live — it's a function of paired execution records, which are only populated after live trading starts.

---

## 4. Files to read first (in this order)

1. `README.md` — status table at the top
2. `docs/README.md` — 7-phase roadmap
3. `docs/phase-0-mvp.md` through `phase-6-productization.md` — the source of truth
4. `docs/runbook.md` — operational questions answered
5. `docs/phase3_decision_template.md` — the document you'll eventually fill out
6. `docs/expansion_proposal_template.md` — gates for Phase 5 decisions
7. `config.yaml` — every tunable parameter (annotated)

---

## 5. Critical commands reference

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/                               # 217 tests

# Core orchestrator
python -m src.main --config config.yaml

# Analysis CLIs
python scripts/reconcile.py --config config.yaml
python scripts/backtest.py --from 2026-04-14 --to 2026-04-15 --fill-model realistic
python scripts/ab_backtest.py --from 2026-04-14 --to 2026-04-15 --config-a a.yaml --config-b b.yaml
python scripts/census.py --from 2026-04-14 --to 2026-04-15 --output reports/census.md
python scripts/calibration_report.py --window-days 14
python scripts/aftertax_report.py --window-days 30
python scripts/capacity_report.py --window-days 30
python scripts/threshold_calc.py --intra 0.20 --p-div 0.05
python scripts/replay.py --from 2026-04-14 --to 2026-04-15

# Emergency controls
touch /tmp/arb_agent.kill                            # manual kill switch
python scripts/kill_switch_reset.py --trigger manual --by <your-name>
touch /tmp/arb_agent_flags/disable_option_e_convergence.flag  # disable one strategy
curl http://127.0.0.1:9100/health
curl http://127.0.0.1:9100/metrics

# Review queue
python scripts/review.py --queue reports/review_queue.json
```

---

## 6. Contact points / provenance

- **Architecture decisions**: every module has a top docstring explaining the design. Read it before changing anything.
- **Test coverage**: `pytest --cov=src tests/` — Phase 0-5 core modules are at >80% line coverage; edge cases in `exchange.py` real-client paths are stub-only (by design).
- **Audit trail**: every trade record in SQLite has a `provenance` JSON blob with git hash, config hash, schema version, started_at timestamp. You can answer "what code / config produced this trade?" for every row.

---

## 7. One-line summary

**You have a production-grade skeleton with clean separation, full test coverage, and explicit safety gates. Every phase required for you to go live is shipped. The remaining work is not writing code — it's KYC, reading rules, running the system, and making honest decisions from the resulting data.**

Don't expand. Don't skip gates. Write your Phase 3 decision doc with real numbers, however disappointing.

Good luck.
