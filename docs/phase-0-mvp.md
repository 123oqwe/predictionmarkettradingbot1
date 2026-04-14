# Phase 0 — MVP Slice

**Duration:** 2–3 weeks (realistically 3–4)
**Goal:** Build the layered architecture, prove the math on one platform, with fake money.

---

## The core tension

Every beginner tutorial tells you to "start small and add complexity later." For a trading system, this advice is subtly wrong. **The things you must get right in Phase 0 are not the features — it's the architecture and the math.** Features can be added later. Architecture cannot be retrofitted without pain, and math bugs compound into losses at every subsequent phase.

So Phase 0 is deliberately narrow on features (one platform, one strategy, paper only) and deliberately ambitious on foundations (four-layer architecture, annualized returns, order book depth, deterministic replay from day one). If this feels like over-engineering for an MVP — it isn't. It's front-loading the things that hurt to retrofit, while keeping the feature surface small enough to actually ship.

The temptation to build "a simple script that just finds arbs" is strong. Resist it. A simple script is how you end up in Phase 5 doing a ground-up rewrite.

---

## Success criteria

At the end of Phase 0, you can:

1. Fetch live market data from Polymarket every 5 seconds for 24+ hours, and every snapshot lands in an append-only Parquet log
2. Detect at least one intra-market `YES_ask + NO_ask < 1` opportunity (after fees, gas, and size-weighted fill price) with a correctly-computed **annualized return on capital-at-risk**
3. Run the detection logic deterministically against recorded data — two runs on the same log must produce identical output
4. Show a paper execution with full PnL breakdown, including capital lock-up time and annualized return
5. Manually verify the math on 3+ opportunities and confirm they match a spreadsheet calculation exactly

Profit is not a Phase 0 metric. **Correctness of the architecture and the math is the Phase 0 metric.**

---

## Scope

**In scope:**
- Polymarket only
- Intra-market YES + NO arbitrage only
- Paper trading only
- Four-layer architecture (data recording / data serving / strategy / execution)
- Annualized return calculation with order book depth
- Parquet-based append-only market snapshot log
- SQLite for transactional state (positions, trades)
- Basic capital allocation (greedy by annualized return)

**Out of scope (resist adding these):**
- Kalshi or any second platform
- Cross-market detection
- Event matching
- Risk controls beyond basic liquidity and capital floors
- Live execution
- A web UI
- Anything in Phase 2+

---

## The four-layer architecture

This is the single most important structural decision in the project. Get it right now and you save months later.

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Data Recording                                     │
│  - Polls exchange APIs                                       │
│  - Writes raw snapshots to append-only Parquet log          │
│  - Never makes strategy decisions                            │
│  - Never reads from the log (only writes)                    │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Data Serving                                       │
│  - Reads Parquet log (live or historical)                    │
│  - Presents unified Market snapshots to upstream layers      │
│  - In "live" mode: tails the current day's Parquet file      │
│  - In "replay" mode: reads a specified date range            │
│  - Same interface in both modes (this is the key)            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: Strategy                                           │
│  - Pure function: (snapshot, config, state) -> opportunities │
│  - No I/O. No API calls. No DB writes.                       │
│  - Unit-testable in total isolation                          │
│  - Same code runs in live and replay modes                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: Execution                                          │
│  - Receives opportunities                                    │
│  - Applies capital allocation                                │
│  - "Fills" in paper mode, submits orders in live mode        │
│  - Writes results to SQLite                                  │
└─────────────────────────────────────────────────────────────┘
```

**Why this matters:**

- **Deterministic replay for free.** Because Layer 3 is pure, feeding it the same historical snapshots produces the same opportunities every time. You can replay any moment to debug a bug or A/B a strategy change.
- **Paper and live share code.** The strategy layer doesn't know or care whether Layer 4 is paper or live. Calibration in Phase 3 becomes clean because both modes run identical logic.
- **Backtesting is nearly free.** Layer 2 serves historical snapshots the same way it serves live ones. A backtest is just a replay with a paper Layer 4 and different time range.
- **Platforms are pluggable.** Adding Kalshi in Phase 1 means adding another Layer 1 fetcher, not touching Layer 3 or 4.

**The architectural rule:** Layer 3 is sacred. It never imports from `httpx`, `aiohttp`, or anything that does I/O. Reviewing a PR that adds I/O to Layer 3 is an automatic rejection.

---

## Storage architecture

Two different kinds of data, two different stores:

**Time-series data (market snapshots, order books):** Append-only Parquet files, partitioned by day and platform.

```
data/snapshots/
├── polymarket/
│   ├── 2026-04-14.parquet
│   ├── 2026-04-15.parquet
│   └── ...
```

Why Parquet, not SQLite: columnar format for fast time-range scans, efficient compression, and DuckDB can query it without importing anywhere. A month of 5-second snapshots on a few hundred markets is maybe 2 GB. Querying "show me all `yes_ask < 0.5` moments on market X in the last week" takes under a second.

**Transactional data (positions, trades, PnL, opportunities):** SQLite with WAL mode.

```
data/state.db
  ├── opportunities
  ├── paper_trades
  ├── paper_positions
  ├── resolved_positions
  ├── errors
  └── schema_version
```

Why SQLite here: real transactions, real constraints, real atomic writes. This is the stuff where "two writes get interleaved and corrupt state" would actually matter.

**Migration planning:** SQLite in Phase 0–2 is fine. Plan to migrate transactional data to Postgres in Phase 3 (when you go live and want real concurrency). The Parquet log stays forever.

---

## Deliverables

### 1. Repo structure

```
arb-agent/
├── README.md
├── pyproject.toml
├── config.yaml
├── src/
│   ├── layer1_data_recording/
│   │   ├── __init__.py
│   │   ├── polymarket_fetcher.py
│   │   └── parquet_writer.py
│   ├── layer2_data_serving/
│   │   ├── __init__.py
│   │   ├── live_stream.py
│   │   └── replay_stream.py
│   ├── layer3_strategy/
│   │   ├── __init__.py
│   │   ├── models.py              # Market, OrderBook, Opportunity
│   │   ├── intra_market.py        # pure detection function
│   │   └── allocation.py          # capital allocator
│   ├── layer4_execution/
│   │   ├── __init__.py
│   │   └── paper.py
│   ├── storage/
│   │   ├── __init__.py
│   │   └── state_db.py
│   ├── provenance.py              # config hash, git hash tracking
│   └── main.py                    # orchestrator
├── tests/
│   ├── test_detection_math.py
│   ├── test_annualized_return.py
│   ├── test_market_impact.py
│   ├── test_capital_allocation.py
│   └── test_replay_determinism.py
├── scripts/
│   └── replay.py                  # deterministic replay from Parquet
└── data/
    ├── snapshots/
    └── state.db
```

### 2. Data model

```python
from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel

class OrderBookLevel(BaseModel):
    price: Decimal
    size_contracts: Decimal

class OrderBookSide(BaseModel):
    levels: list[OrderBookLevel]  # sorted best-first

    def weighted_fill_price(self, desired_contracts: Decimal) -> tuple[Decimal, Decimal]:
        """Returns (weighted_avg_price, actually_fillable_contracts)."""
        ...

class Market(BaseModel):
    platform: str                    # "polymarket"
    market_id: str
    title: str
    yes_bids: OrderBookSide
    yes_asks: OrderBookSide          # NOT a single ask — full depth
    no_bids: OrderBookSide
    no_asks: OrderBookSide
    fee_bps: int
    resolution_date: datetime        # CRITICAL for annualized return
    resolution_source: str
    fetched_at: datetime

class Opportunity(BaseModel):
    strategy: str                    # "intra_market"
    market_id: str
    detected_at: datetime
    size_contracts: Decimal
    yes_fill_price: Decimal          # size-weighted
    no_fill_price: Decimal
    gross_cost: Decimal
    fee_cost: Decimal
    gas_cost_usd: Decimal
    capital_at_risk_usd: Decimal
    days_to_resolution: Decimal
    expected_profit_usd: Decimal
    profit_pct_absolute: Decimal
    annualized_return: Decimal       # THIS is what thresholds compare against
    config_hash: str                 # provenance
    git_hash: str
```

**Use `Decimal`, never `float`.** Every price, every fee, every PnL number. Multiplying a Decimal by a float silently returns a float in Python, which eventually corrupts your accounting. Add a unit test that checks the return type of every profit calculation.

### 3. Detection math (⚠️ math-critical — this is the heart of the project)

The profit calculation must account for:

1. **Size-weighted fill price.** If you want to buy 100 contracts of YES and the order book has 30 at 0.45, 40 at 0.46, 30 at 0.47, your actual cost is `(30*0.45 + 40*0.46 + 30*0.47) / 100 = 0.459`, not 0.45.
2. **Fees on both sides.** YES buy and NO buy each pay fees.
3. **Gas cost (Polymarket on Polygon).** Per-trade, not per-contract. A $5 trade can be eaten by gas.
4. **Capital-at-risk.** The actual dollars that will be locked up until resolution. This is `size_contracts * (yes_fill_price + no_fill_price) + gas_cost`.
5. **Time to resolution.** Needed for annualization.

```python
def compute_opportunity(
    market: Market,
    desired_size_contracts: Decimal,
    config: Config,
) -> Opportunity | None:
    # Step 1: Find actually fillable size (may be less than desired)
    yes_price, yes_filled = market.yes_asks.weighted_fill_price(desired_size_contracts)
    no_price, no_filled = market.no_asks.weighted_fill_price(desired_size_contracts)
    size = min(yes_filled, no_filled)
    if size < config.min_trade_size_contracts:
        return None

    # Step 2: Gross cost
    gross_cost = size * (yes_price + no_price)

    # Step 3: Fees
    fee_cost = gross_cost * Decimal(market.fee_bps) / Decimal(10000)

    # Step 4: Gas (per-trade, amortized over the size)
    gas_cost_usd = config.polymarket_gas_estimate_usd

    # Step 5: Capital at risk
    capital_at_risk = gross_cost + fee_cost + gas_cost_usd

    # Step 6: Expected payout at resolution
    payout_at_resolution = size * Decimal("1.0")  # each pair pays $1

    # Step 7: Absolute profit
    expected_profit = payout_at_resolution - capital_at_risk
    if expected_profit <= 0:
        return None

    # Step 8: Annualized return on capital-at-risk
    days_to_resolution = Decimal((market.resolution_date - datetime.utcnow()).days)
    if days_to_resolution <= 0:
        return None  # market already past resolution, reject
    profit_pct = expected_profit / capital_at_risk
    # Simple annualization: (1 + r)^(365/days) - 1
    annualized_return = (Decimal("1") + profit_pct) ** (Decimal("365") / days_to_resolution) - Decimal("1")

    # Step 9: Threshold check — on annualized, not absolute
    if annualized_return < config.min_annualized_return:
        return None

    return Opportunity(
        size_contracts=size,
        yes_fill_price=yes_price,
        no_fill_price=no_price,
        gross_cost=gross_cost,
        fee_cost=fee_cost,
        gas_cost_usd=gas_cost_usd,
        capital_at_risk_usd=capital_at_risk,
        days_to_resolution=days_to_resolution,
        expected_profit_usd=expected_profit,
        profit_pct_absolute=profit_pct,
        annualized_return=annualized_return,
        ...
    )
```

**Why this matters:** A 2% absolute profit on a trade resolving in 30 days is 27% annualized. A 2% absolute profit on a trade resolving in 300 days is 2.4% annualized — worse than a T-bill after tax. Your threshold **must** be on the annualized number. Default config: `min_annualized_return = 0.20` (20% annualized).

**Size search:** In practice, `desired_size_contracts` should not be a fixed number. It should be the result of a small search: find the largest size where `annualized_return` stays above threshold. As you try to fill more contracts, you walk up the order book, your fill price rises, and annualized return drops. The optimal trade is at the edge where threshold is just met.

Required unit tests:

- Zero fees, zero gas, flat order book → break-even gives exactly 0 profit
- Flat book vs sloped book → sloped book produces lower max profitable size
- Short resolution (2 days) at 1% absolute → high annualized, passes threshold
- Long resolution (200 days) at 3% absolute → low annualized, rejected
- Gas cost larger than expected profit on small size → rejected
- Asymmetric liquidity (deep YES, shallow NO) → size capped by shallow side
- Resolution date in the past → rejected, never returns a negative `days_to_resolution`
- Return type is `Decimal` throughout (enforced by test)

### 4. Capital allocation (⚠️ math-critical)

When the scanner finds 5 opportunities simultaneously and you have $300 of paper capital, you don't get to do all 5. You have to pick.

```python
def allocate_capital(
    opportunities: list[Opportunity],
    available_capital_usd: Decimal,
    reserved_capital_usd: Decimal,
    max_capital_per_trade: Decimal,
    max_capital_per_event: Decimal,
) -> list[Allocation]:
    """
    Greedy allocation by annualized return.
    Opportunities are ranked highest-annualized first.
    Capital is committed in order until exhausted.
    `reserved_capital_usd` = capital already locked in open positions.
    """
    remaining = available_capital_usd - reserved_capital_usd
    allocations = []
    committed_per_event: dict[str, Decimal] = defaultdict(Decimal)

    for opp in sorted(opportunities, key=lambda o: o.annualized_return, reverse=True):
        if remaining <= 0:
            break

        # Apply per-trade cap
        trade_cap = min(max_capital_per_trade, remaining)

        # Apply per-event cap
        event_committed = committed_per_event[opp.event_id]
        event_headroom = max_capital_per_event - event_committed
        trade_cap = min(trade_cap, event_headroom)

        if trade_cap < opp.capital_at_risk_usd:
            # Resize the opportunity down (this requires re-running the math)
            ...

        allocations.append(Allocation(opp, size=trade_cap))
        remaining -= trade_cap
        committed_per_event[opp.event_id] += trade_cap

    return allocations
```

**Why this matters:** Naive "first come first served" allocation systematically puts capital into the lowest-annualized opportunities, because those are the ones that have been sitting there the longest waiting to be seen. Greedy-by-annualized is the minimum correct approach.

### 5. Layer 1 — Data recording

`src/layer1_data_recording/parquet_writer.py`:
- Opens a daily Parquet file per platform
- Appends every snapshot with full order book depth
- Flushes every N seconds
- On day rollover (UTC), closes current file and opens new one

Never reads from the log. Never makes decisions. Its only job is to faithfully record reality.

### 6. Layer 2 — Data serving

`src/layer2_data_serving/`:
- `live_stream.py`: tails the current day's Parquet file, yields new snapshots as they're appended
- `replay_stream.py`: reads a specified date range, yields snapshots in chronological order, at configurable speed (1x for wall-clock replay, 0x for instant)

Both expose the same async iterator interface. Layer 3 doesn't know which is which.

### 7. Layer 3 — Strategy

Pure function, as described above. Unit-tested in isolation with mocked snapshots. Zero dependencies on layers 1, 2, or 4.

### 8. Layer 4 — Paper execution

When an opportunity is allocated capital:
- "Fill" at the computed weighted prices
- Record intended trade, client order ID (idempotency from day one), capital locked
- Store position in SQLite with resolution date
- On every cycle, check if any positions' resolution dates have passed, query actual resolution, compute realized PnL

### 9. Provenance tracking

`src/provenance.py`:
- On every orchestrator startup, compute and record:
  - Git commit hash of current code
  - SHA-256 of config file
  - Schema version
  - Start timestamp
- Every trade record gets tagged with this bundle

This makes post-mortem analysis possible. Three weeks from now when you ask "why did we suddenly do so many trades on April 14," the answer will be "commit abc123 lowered the threshold to 15% annualized, then def456 raised it back."

### 10. CLI dashboard

```
[2026-04-14 10:23:45] Loop #8,421
Recording: polymarket/2026-04-14.parquet (342 markets, 1.2 GB)
Strategy: scanned 342 markets, 3 opportunities found
  → Best: "Will X?" size=$87 ann_return=34.2% days_to_res=18
  → 2nd:  "Will Y?" size=$50 ann_return=22.1% days_to_res=45
Capital: $1000 total, $137 allocated, $863 free
Paper PnL: +$4.23 unrealized, +$0.00 realized
Errors (1h): 0
Config: abc123 | Git: def456
```

No TUI, no colors. Scannable in two seconds.

---

## Task breakdown

Tasks tagged `[math]` (bugs here cost money), `[arch]` (architectural — affects all later phases), `[infra]` (plumbing), `[research]` (reading).

### Task 0.1 `[research]` — Polymarket CLOB API reconnaissance

Fetch current Polymarket CLOB API docs. Confirm:
- Listing endpoint for active markets
- Full order book endpoint (depth, not just best bid/ask)
- Authentication (read = no auth)
- Current fee schedule (exact basis points, where they apply)
- Rate limits
- How YES and NO tokens are structured
- Resolution date and resolution source fields
- **Polygon gas cost** — typical per-trade in USD equivalent

Output: `docs/polymarket-api-notes.md` with findings and source URLs.

### Task 0.2 `[arch]` — Four-layer architecture skeleton

Create the directory structure. Define the abstract interfaces between layers (pydantic models + ABCs). No logic yet — just the shape. This is the most important architectural commitment in the project.

### Task 0.3 `[infra]` — Data models

Fill in the pydantic models from section 2. Includes `OrderBookSide.weighted_fill_price()` with unit tests.

### Task 0.4 `[math]` — Detection math with tests (write tests first)

Every test case from section 3, written before any implementation. Then implement `compute_opportunity` and `find_opportunities` as pure functions. Test must pass before moving on. This is the most math-critical code in the project.

### Task 0.5 `[math]` — Capital allocator with tests

Greedy allocator with per-trade and per-event caps. Unit tests include:
- 5 opportunities, enough capital for 3 → top 3 by annualized taken
- Reserved capital reduces available pool correctly
- Per-event cap prevents concentration
- Allocation that would require resizing downward produces correct size

### Task 0.6 `[infra]` — Layer 1 fetcher + Parquet writer

Polymarket fetcher as async coroutine. Parquet writer with daily rotation. Integration test: fetch for 10 minutes, verify the Parquet file has the expected row count.

### Task 0.7 `[infra]` — Layer 2 data serving

Live stream and replay stream with identical async iterator interfaces. Test: replay a recorded file and verify the output matches what was recorded.

### Task 0.8 `[infra]` — SQLite state schema

Tables for opportunities, paper_trades, paper_positions, resolved_positions, errors. Schema version from day one. WAL mode.

### Task 0.9 `[infra]` — Layer 4 paper execution

Paper filler + position tracker + resolution poller. Writes to SQLite.

### Task 0.10 `[infra]` — Provenance module

Git hash, config hash, attach to every trade record.

### Task 0.11 `[infra]` — Orchestrator + dashboard

Wire layers 1-4 together. CLI output. Run for 1 hour, verify recordings, detections, and paper trades all work.

### Task 0.12 `[math]` — Replay determinism test

The test that validates the architecture: record 30 minutes of live data, then replay it twice. Both replays must produce byte-identical opportunity output. If not, Layer 3 has hidden state or nondeterminism — find and fix it.

### Task 0.13 `[math]` — Manual verification

Pick 3 detected opportunities. For each, pull the raw order book snapshot from the Parquet log, compute profit by hand in a spreadsheet including size-weighted fills and annualization, compare to the agent's recorded `Opportunity`. Exact match required.

### Task 0.14 `[infra]` — 24-hour soak test

Let it run for a full day. Check:
- No memory growth (monitor RSS)
- No uncaught exceptions
- Parquet files grow correctly, rotate at UTC midnight
- Resolution polling correctly handles markets that resolve mid-run
- Total capital allocated never exceeds configured maximum

---

## Gotchas (things that will actually bite you)

**The bid/ask flip.** Every time you think "buy," price is `ask`. Every time you think "sell," price is `bid`. When arb'ing, you're always buying — always `ask`. If detection finds "profitable" trades that all disappear on manual check, check this first.

**Already-resolved markets still in the feed.** "Will Biden win 2024?" shows `yes_ask = 0.01, no_ask = 0.01` and looks like 98% arbitrage. It resolved. Filter by `active` state AND verify `resolution_date > now`.

**YES and NO as separate tokens.** Polymarket's CLOB may treat YES and NO as two independent order books. You must fetch both and reason about liquidity separately. Naive fetching gives fake liquidity numbers.

**Top-of-book vs depth.** Your detection sees `yes_ask = 0.45` with `size = 1000`. You try to buy 1000 contracts. Actual fill prices walk up the book to 0.50 because the 1000 size was split across levels. If you don't model depth, you'll "detect" opportunities that don't survive realistic fills. This is why the Market model stores full book depth, not a single ask.

**Gas eating small trades.** $5 trade at 3% theoretical profit is 15¢. Polygon gas during congestion can be 30¢. Always include gas; always apply per-trade, not per-contract; always reject trades where gas > threshold fraction of profit.

**Zero-liquidity markets showing absurd spreads.** A dead market with `yes_ask = 0.10, no_ask = 0.10` looks like 80% arbitrage. It isn't — the orders get pulled the moment anyone touches them. Hard-floor total liquidity at $100 before even considering a market.

**Stale quotes.** API returns whatever it has. Check timestamps. Skip snapshots older than 10 seconds.

**Decimal/float contamination.** Python silently converts `Decimal * float` to `float`. One sloppy line corrupts your accounting. Enforce with a type-check unit test that runs a computation and asserts the result is `Decimal`.

**Annualization of trades resolving tomorrow.** A 1% profit on a trade resolving in 1 day gives an annualized return of ~3700%, which is mathematically correct but practically meaningless — you can't reinvest 365 times a year because opportunities don't show up that often. **Cap annualization at trades of duration ≥ 5 days**, or use a capacity-adjusted metric. Otherwise your allocator will prioritize same-day trades that you can't actually execute at scale.

**Missing `resolution_date`.** Some markets may not expose a resolution date in the API. Without it, you can't annualize. Either find it through another endpoint, estimate from market title (risky), or exclude the market. Don't use a fake "default" like 90 days.

**Nondeterminism in the strategy layer.** The replay determinism test catches this. Common causes: use of `datetime.now()` inside detection (use the snapshot timestamp), iterating over a `dict` or `set` (order-dependent in older Python), reading environment state. Layer 3 must be pure.

**Parquet schema drift.** If your Market model evolves mid-phase, old Parquet files won't load with the new schema. Version the schema in the Parquet metadata, write a migration script, or accept that old recordings are for debugging only, not replay.

---

## Failure modes and when to loop back

- **Detection math tests keep failing in subtle ways.** Slow down. Write the math on paper. Compute a specific example by hand. Match it to code. The bug is almost always that you misunderstood something.
- **Replay determinism test fails.** Layer 3 has I/O or hidden state. Find it. This test is non-negotiable.
- **24-hour soak test crashes repeatedly.** Reliability issue with Polymarket client or retry logic. Fix now; worse in Phase 2.
- **Zero opportunities above 20% annualized.** Try 15%, then 10%. If zero at 10%, your order book depth modeling might be too conservative (or the market is efficient, which is legitimate info). Verify code works at 5% temporarily, then set back. The real edge question is answered in Phase 3, not here.
- **Bored, want to add Kalshi.** Stop. Finish Phase 0. Adding a second platform now doubles debugging surface before you've validated the single-platform architecture.

---

## Cost budget

Phase 0 should cost **< $20/month**:
- Polymarket API: free
- Polygon RPC for gas estimates: free tier sufficient
- Local storage for Parquet: free
- Running on your own machine: electricity

If you find yourself provisioning cloud infra in Phase 0, you're over-engineering in the wrong direction.

---

## Exit criteria → Phase 1

- [ ] Four-layer architecture is real and respected — Layer 3 has zero I/O
- [ ] 24-hour soak test passes with zero unhandled exceptions
- [ ] All detection math unit tests pass, including the type-safety test
- [ ] Replay determinism test passes (two replays of same data = identical output)
- [ ] Manually verified ≥3 opportunities matching spreadsheet calculation exactly
- [ ] Capital allocator correctly respects per-trade and per-event caps
- [ ] Parquet recording runs cleanly through a UTC day rollover
- [ ] You can explain the Polymarket fee schedule and typical gas cost without looking them up
- [ ] You know your rough daily opportunity count above 20% annualized threshold
- [ ] Adding a second platform feels like adding a Layer 1 fetcher, not a rewrite

That last criterion is the test of whether the architecture actually works.
