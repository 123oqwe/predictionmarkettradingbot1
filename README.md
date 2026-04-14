# predictionmarkettradingbot1 — Prediction Market Arbitrage Agent

Your owned prediction market trading bot.

Phase 0 MVP: four-layer architecture, intra-market arbitrage detection on Polymarket, paper execution only.

See [`docs/README.md`](docs/README.md) for the full 7-phase roadmap.

## What this ships (Phase 0)

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
- 48 unit tests, all passing, including replay determinism

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
