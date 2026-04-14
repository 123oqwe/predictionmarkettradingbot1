# predictionmarkettradingbot1 — Prediction Market Arbitrage Agent

Your owned prediction market trading bot.

**Phases 0–5 shipped in code. Phases 3–5 frozen pending operator validation.**

| Phase | Status | Gate before enabling |
|---|---|---|
| 0 MVP | Ready | — |
| 1 Dual platform | Ready; event_map must be hand-built | Write `event_map.yaml`, soak 1 week |
| 2 Risk + reliability | Ready; kill switches in observe mode | 14-day soak with a macro event, flip rules to enforce |
| 2.5 Backtest | Ready | Read the first census report |
| 3 Live transition | **Frozen** — needs KYC + funding | Complete Phase 3 decision doc with SCALE |
| 4 Auto matching | Ready; stub extractor works offline | Validate extractor vs Phase 1 ground truth |
| 5 Expansion | **Frozen** — all feature flags default OFF | Phase 3 must conclude SCALE first |

See [`docs/README.md`](docs/README.md) for the full 7-phase roadmap.

## What this ships

**Phase 0 — foundations:**
- Four-layer architecture: data recording, data serving, strategy, execution
- Polymarket CLOB fetcher (read-only, no auth)
- Append-only Parquet log with daily UTC rotation
- Live + replay streams with identical interfaces
- Intra-market arbitrage detection (YES_ask + NO_ask < 1 after fees/gas, size-weighted)
- Annualized return gate + minimum-days-to-resolution cap (prevents same-day annualization blow-up)
- Greedy capital allocator with per-trade + per-event caps, including resize-down logic
- SQLite state DB (WAL + synchronous=FULL) with idempotent inserts
- Paper execution with deterministic client_order_id
- Provenance (git hash + config hash) attached to every record

**Phase 1 — dual platform + matching:**
- Kalshi fetcher with cent-tick price quantization (a 0.505 detection becomes 0.51 actual)
- Strict `event_map.yaml` loader: enforces ≥5 edge cases per pair, ≥1 marked divergent, content hash for provenance
- Cross-market detection (both directions per pair, same purity guarantees as intra)
- Cross-market threshold derivation: `r_cross >= (r_intra + p_div) / (1 - p_div)` (calculator at `scripts/threshold_calc.py`)
- Adverse selection filters: age-of-opportunity, news-window blackouts, young-market gate
- Daily report v2 with annualized-return histograms split by strategy

**Phase 2 — reliability:**
- Monitoring layer (MetricsRegistry + SQLite metrics table + Prometheus endpoint)
- Kill switch framework with observe→enforce modes, per-rule cooldowns
- 10 rules implemented, every one force-tripped by a test
- Paper-mode reconciliation (`scripts/reconcile.py`)
- Telegram alerter with secret redaction + rate limiting
- Crash recovery + state-loaded gate before first scan
- Runbook (`docs/runbook.md`) answering all 8 doc questions

**Phase 2.5 — backtesting:**
- Three fill models (optimistic/realistic/pessimistic) with pessimistic extra-slippage tweak
- Sharpe using `periods_per_year=365` (asserted by test — prediction markets are 24×7)
- Backtest runner + A/B comparator + census script
- Integration test: synthetic Parquet → known PnL, fill-model invariant `opt ≥ real ≥ pes`

**Phase 3 — paper-to-live (FROZEN):**
- Exchange client ABC + 4-check SafetyGatedClient (live flag + API key env + clean git + not-dry-run)
- Live executor with two-leg parallel submission + deterministic per-leg client_order_id
- Partial fill policy: imbalance > 5 → marketable-limit retry @ +50bps × 3 → trip kill switch
- Uncertainty-bounded model (p05/p95) + calibration coverage stat
- Three-gate graduation (code-enforced, no silent downgrade)
- Tax reporting (Section 1256 + ordinary income) with benchmark check

**Phase 4 — auto matching:**
- Prefilter kills ~99% of cartesian product before LLM
- ResolutionCriteria schema + controlled edge-case vocabularies per event_type
- Extractor with STUB/ANTHROPIC/OFFLINE modes; XML-tagged prompt injection guard
- Parquet cache keyed on (market_id, description_hash, rules_hash, llm_model_version)
- Deterministic matcher returning explicit `differences` list
- Tier A/B/C rules in code (C paper-only 48h, auto-promote to B)

**Phase 5 — expansion (FROZEN):**
- Feature flag system (`/tmp/arb_agent_flags/disable_<name>.flag`) — hot kill, no restart
- Option E: resolution convergence trading (≥0.95 near-resolution prices, ≥200-contract depth)
- Capacity diagnostic (`scripts/capacity_report.py`) — capital/edge/latency/attention classifier
- Expansion proposal template in `docs/`

**204 unit tests, all passing**, including replay determinism, chaos injection, and Phase 5 frozen-by-default. CI on Python 3.9 + 3.11.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/                            # 48 tests
python -m src.main --config config.yaml  # run the orchestrator
```

## Architecture

```
┌─────────────────────────────────────────────┐
│ Layer 1: Data Recording                      │
│ polymarket_fetcher.py + parquet_writer.py   │
│ Writes only, never reads the log.           │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│ Layer 2: Data Serving                        │
│ live_stream.py  + replay_stream.py          │
│ Identical async-iterator interface.         │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│ Layer 3: Strategy (PURE)                     │
│ models.py + intra_market.py + allocation.py │
│ No I/O. No datetime.now(). Replay-safe.     │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│ Layer 4: Execution                           │
│ paper.py (live trading in Phase 3)          │
│ Idempotent via deterministic order IDs.     │
└─────────────────────────────────────────────┘
```

Layer 3 is sacred. No I/O there, ever. The replay determinism test in `tests/test_replay_determinism.py` enforces this: any hidden state (datetime.now(), dict iteration order, env reads) causes a hash mismatch and the test fails.

## Key design decisions

### Decimal everywhere
Every monetary or size quantity is `Decimal`, never `float`. The config loader rejects float values in YAML. The model validators reject float at construction. `tests/test_detection_math.py::TestProfitableOpportunity::test_return_types_are_decimal` enforces it at the output boundary. Python's `Decimal * float = float` silent conversion is one of the most insidious bugs in financial code — we treat it as an error.

### Annualized return gate with min-days cap
Thresholds are on annualized return, not absolute profit. A 2% absolute profit on a 30-day trade is 27% annualized (good); the same on a 300-day trade is 2.4% (worse than T-bills). But we also cap trades at `min_days_to_resolution = 5` — a 2% profit in 1 day computes to ~3700% annualized, which the allocator would prioritize over any realistic trade. Since you can't actually execute 365 such trades per year, that's phantom edge, not real.

### Size-weighted fill + size search
`OrderBookSide.weighted_fill_price()` walks the book level by level to compute the true blended cost. Detection binary-searches for the largest size where the annualized gate still passes — because bigger = more absolute profit, but the book walks up as size grows.

### Capital allocator resize math
When a per-trade or per-event cap forces a smaller size, the allocator does NOT naively scale down — gas is a fixed per-trade cost, so linear scaling overshoots the cap. Instead it binary-searches for the largest integer size whose recomputed `capital_at_risk_usd` fits under the cap AND still passes the annualized gate.

### Idempotent fills from Phase 0 (not Phase 2)
The roadmap introduces idempotency keys in Phase 2. We added them in Phase 0 instead: the cost is trivial, and it makes crash recovery tractable from day one. `client_order_id = sha256(opportunity_id + detected_at + market_id + size + "paper")` — repeated fills on the same allocation are no-ops.

## Configuration

See [`config.yaml`](config.yaml). All numeric values are strings (YAML) so they parse as `Decimal`:

```yaml
strategy:
  intra_market:
    min_annualized_return: "0.20"       # 20% annualized
    min_days_to_resolution: "5"         # reject trades <5 days
    ...
```

## Running replay

After you've collected some snapshots (run the orchestrator for a few minutes):

```bash
python scripts/replay.py --from 2026-04-14 --to 2026-04-15
```

The determinism hash printed at the end must match across runs.

## Tests

```bash
pytest tests/ -v
```

Included tests:
- `test_models.py` — OrderBook fill math, float rejection
- `test_detection_math.py` — every failure mode from phase-0-mvp.md section 3
- `test_capital_allocation.py` — greedy ranking, caps, resize logic
- `test_replay_determinism.py` — two replays → identical hash
- `test_state_db_and_paper.py` — SQLite schema + paper fill flow
- `test_provenance.py` — config hash stability
- `test_config.py` — float rejection in loader

## What's not here yet

Phase 0 exit criteria that require running, not just writing code:

- 24-hour soak test (run the orchestrator overnight, check for memory leaks, crashes, UTC rollover)
- Manual verification of 3+ detected opportunities against spreadsheet math
- Polymarket CLOB fee/gas reconnaissance (see `docs/phase-0-mvp.md` Task 0.1)

Phases 1–6 are in `docs/`. They require sustained real-world work — reading platform rules, live trading, calibration — that can't be shortcutted.

## License

Private repository. No redistribution implied.
