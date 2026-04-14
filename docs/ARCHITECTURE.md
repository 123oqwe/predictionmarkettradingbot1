# System Architecture Diagrams

Six views of the same system. Read in order.

---

## 1. The four-layer architecture (Phase 0 foundation)

This is the single most important structural decision. It doesn't change
across Phases 1-5. Everything plugs into this skeleton.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  LAYER 1 — DATA RECORDING                                                │
│  ┌────────────────────┐  ┌────────────────────┐  ┌─────────────────┐    │
│  │ polymarket_fetcher │  │  kalshi_fetcher    │  │ (future Manifold │    │
│  │  (CLOB + gamma)    │  │  (trade-api v2)    │  │   Option B)     │    │
│  └─────────┬──────────┘  └─────────┬──────────┘  └────────┬────────┘    │
│            │                        │                      │             │
│            └────────┬───────────────┴──────────────────────┘             │
│                     ▼                                                    │
│           ┌──────────────────────┐                                       │
│           │ DailyParquetWriter   │  append-only, UTC midnight rotation   │
│           │ data/snapshots/...   │  NEVER reads, only writes             │
│           └──────────────────────┘                                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  LAYER 2 — DATA SERVING                                                  │
│  ┌──────────────────┐                   ┌──────────────────┐             │
│  │   LiveStream     │                   │   ReplayStream   │             │
│  │  (polls fetcher) │                   │ (reads Parquet)  │             │
│  └────────┬─────────┘                   └────────┬─────────┘             │
│           │                                      │                       │
│           └─────────┐            ┌───────────────┘                       │
│                     ▼            ▼                                       │
│              ┌─────────────────────────┐                                 │
│              │ AsyncIterator[Market]   │  IDENTICAL INTERFACE            │
│              │ (Layer 3 can't tell     │  live vs replay from above      │
│              │  which is which)        │                                 │
│              └─────────────────────────┘                                 │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  LAYER 3 — STRATEGY (PURE, NO I/O, NO datetime.now())                    │
│                                                                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────────┐     │
│  │  intra_market   │  │  cross_market   │  │ resolution_convergence│    │
│  │  (Phase 0)      │  │  (Phase 1)      │  │    (Phase 5, FROZEN) │     │
│  └────────┬────────┘  └────────┬────────┘  └─────────┬────────────┘     │
│           │                    │                      │                  │
│           └──────────┬─────────┴──────────────────────┘                  │
│                      ▼                                                   │
│           ┌──────────────────────┐                                       │
│           │ find_opportunities() │  pure function                        │
│           │ → List[Opportunity]  │  same inputs → same outputs (tested)  │
│           └──────────┬───────────┘                                       │
│                      ▼                                                   │
│           ┌──────────────────────┐                                       │
│           │ adverse_selection    │  age / news / young-market filters    │
│           │ (Phase 1)            │                                       │
│           └──────────┬───────────┘                                       │
│                      ▼                                                   │
│           ┌──────────────────────┐                                       │
│           │  allocate_capital()  │  greedy by annualized + resize        │
│           │  with per-trade,     │  logic that accounts for fixed gas    │
│           │  per-event caps      │                                       │
│           └──────────┬───────────┘                                       │
│                      │                                                   │
└──────────────────────┼───────────────────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  LAYER 4 — EXECUTION                                                     │
│                                                                          │
│  ┌────────────────┐                    ┌─────────────────────────┐       │
│  │  PaperExecutor │                    │  LiveExecutor (Phase 3) │       │
│  │  (all phases)  │                    │  + SafetyGatedClient    │       │
│  │                │                    │  + partial_fill policy  │       │
│  └───────┬────────┘                    └──────────┬──────────────┘       │
│          │                                        │                      │
│          └────────────┬───────────────────────────┘                      │
│                       ▼                                                  │
│           ┌──────────────────────────┐                                   │
│           │ SQLite state.db          │  WAL + synchronous=FULL            │
│           │  opportunities           │  idempotent inserts                │
│           │  paper_trades            │                                   │
│           │  execution_records       │  (Phase 3, paired live/paper)    │
│           │  metrics / kill_switch_* │  (Phase 2)                       │
│           └──────────────────────────┘                                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**The rule nobody breaks**: Layer 3 never imports `aiohttp`, never calls
`datetime.now()`. Time flows from `market.fetched_at`. This is what makes
replay deterministic and tests byte-reproducible.

---

## 2. One cycle of the orchestrator (Phase 0-2 + 4)

```
Every poll_interval_seconds (default 5s):

   ┌──────────────────────────┐
   │  asyncio.gather:         │
   │  - Polymarket.fetch()    │◀── parallel fetches
   │  - Kalshi.fetch()        │
   └───────────┬──────────────┘
               │ markets[]
               ▼
   ┌──────────────────────────┐       ┌─────────────────────────┐
   │ Layer 1 Parquet writers  │──────▶│  data/snapshots/*.parquet│
   │ (per platform)           │       │  (persisted immediately) │
   └───────────┬──────────────┘       └─────────────────────────┘
               ▼
   ┌──────────────────────────┐
   │ find_opportunities()     │
   │ + find_cross_opps()      │
   └───────────┬──────────────┘
               │ opportunities[]
               ▼
   ┌──────────────────────────┐
   │ apply_filters()          │  age / news / young-market
   └───────────┬──────────────┘
               │ passed[]
               ▼
   ┌──────────────────────────┐       ┌─────────────────────────┐
   │ metrics.update()         │──────▶│  MetricsRegistry        │
   │ (Phase 2)                │       │  (in-memory)            │
   └───────────┬──────────────┘       └──────┬──────────────────┘
               │                              ▼
               ▼                     ┌──────────────────┐
   ┌──────────────────────────┐     │ HealthServer     │
   │ policy.evaluate_all()    │     │  /health /metrics│
   │ → kill_switch trips?     │     └──────────────────┘
   └───────────┬──────────────┘
               │
        ┌──────┴──────┐
        │             │
      halted?       OK
        │             │
        │             ▼
        │   ┌──────────────────────────┐
        │   │ allocate_capital()       │
        │   └───────────┬──────────────┘
        │               │ allocations[]
        │               ▼
        │   ┌──────────────────────────┐
        │   │ PaperExecutor.fill()     │
        │   │   or LiveExecutor (P3)   │
        │   └───────────┬──────────────┘
        │               ▼
        │   ┌──────────────────────────┐
        │   │ executor.resolve_due()   │
        │   └───────────┬──────────────┘
        │               │
        └───────┬───────┘
                ▼
      ┌─────────────────────────────┐
      │ print(render_cycle(report)) │◀── scannable dashboard
      └──────────┬──────────────────┘
                 ▼
      ┌─────────────────────────────┐
      │ persist_snapshot(metrics)   │◀── writes to metrics table
      └──────────┬──────────────────┘
                 ▼
             sleep to next tick
```

---

## 3. Safety gates before a real trade hits the exchange

Phase 3 wires FOUR checks before any order reaches the real network.
Each independent; any one blocks.

```
                  ┌─────────────────────┐
                  │  Allocation (P3)    │
                  └──────────┬──────────┘
                             ▼
                  ┌─────────────────────┐
                  │   LiveExecutor      │
                  │   .execute(alloc)   │
                  └──────────┬──────────┘
                             ▼
                  ┌─────────────────────┐
                  │ SafetyGatedClient   │
                  │  ┌───────────────┐  │
   fail → REJECT  │  │ 1. --live     │  │
        ◀─────────┼──│    flag set?  │  │
                  │  └───────┬───────┘  │
                  │          │ yes      │
                  │          ▼          │
                  │  ┌───────────────┐  │
   fail → REJECT  │  │ 2. API_KEY    │  │
        ◀─────────┼──│    env var    │  │
                  │  │    present?   │  │
                  │  └───────┬───────┘  │
                  │          │ yes      │
                  │          ▼          │
                  │  ┌───────────────┐  │
   fail → REJECT  │  │ 3. git tree   │  │
        ◀─────────┼──│    clean?     │  │
                  │  └───────┬───────┘  │
                  │          │ yes      │
                  │          ▼          │
                  │  ┌───────────────┐  │
   fail → LOG     │  │ 4. dry_run    │  │
        ◀─────────┼──│    == False?  │  │
                  │  └───────┬───────┘  │
                  │          │ yes      │
                  └──────────┼──────────┘
                             ▼
                  ┌─────────────────────┐
                  │  PolymarketLive…    │   YOU write this file.
                  │  KalshiLive…        │   Interface is stable.
                  │  (real HTTP)        │
                  └──────────┬──────────┘
                             ▼
                      ┌───────────┐
                      │ Exchange  │
                      └─────┬─────┘
                            ▼
                      ┌────────────────────┐
                      │ OrderResult        │
                      │  FILLED / PARTIAL  │
                      └─────┬──────────────┘
                            ▼
                     ┌──────────────────┐
                     │ partial_fill     │ imbalance > 5?
                     │ .resolve()       │   → retry × 3 @ +50bps
                     │                  │   → else trip kill switch
                     └──────────────────┘
```

---

## 4. Phase 3 gate progression (code-enforced, no shortcuts)

```
                   START (paper only)
                          │
                          ▼
              ┌─────────────────────────┐
              │  Gate 1                 │
              │  threshold: 60% ann     │  extreme — only highest-quality
              │  size cap:  $10         │  opportunities qualify
              │  pause:     10s/fill    │
              │  alert:     every fill  │
              │                         │
              │  graduation:            │
              │   - ≥ 3 days            │
              │   - ≥ 5 successful fills│
              │   - every fill          │
              │     explainable         │
              └──────────┬──────────────┘
                         │
          FAIL           │  PASS
          ──────▶ loop ◀─┤
                         │
                         ▼
              ┌─────────────────────────┐
              │  Gate 2                 │
              │  threshold: 30% ann     │  looser but still conservative
              │  size cap:  $20         │
              │  pause:     5s/fill     │
              │  alert:     every fill  │
              │                         │
              │  graduation:            │
              │   - ≥ 4 days            │
              │   - ≥ 15 successful     │
              │   - calibration ≥ 85%   │  ← 90% CI coverage check
              └──────────┬──────────────┘
                         │
          FAIL           │  PASS
          ──────▶ loop ◀─┤
                         │
                         ▼
              ┌─────────────────────────┐
              │  Gate 3 (terminal)      │
              │  threshold: 20% ann     │  normal operations
              │  size cap:  $25         │
              │  pause:     0s          │
              │  alert:     hourly      │
              │                         │
              │  run ≥ 7 days to        │
              │  accumulate statistics  │
              └──────────┬──────────────┘
                         │
                         ▼
              ┌─────────────────────────┐
              │ Write decision doc      │
              │ (phase3_decision_       │
              │  template.md)           │
              └──────────┬──────────────┘
                         │
          ┌──────────┬───┴──────┬────────────┐
          ▼          ▼          ▼            ▼
       SCALE       HOLD    REDESIGN        STOP
        │           │          │             │
   → Phase 4+5   → repeat   → back to    → take the
     expansion     Gate 3      Phase 2.5    lessons, do
                               backtest     something
                                            else
```

---

## 5. Kill switch state machine

```
                        [clean startup]
                              │
                              ▼
                     ┌─────────────────┐
                     │     OK (idle)   │
                     └────────┬────────┘
                              │
     policy.evaluate_all()    │
     sees rule tripped        │
                              ▼
            ┌─────────────────────────────────┐
            │   is rule in OBSERVE mode?      │
            └────┬────────────────────────┬───┘
             yes │                        │ no
                 ▼                        ▼
    ┌──────────────────────┐  ┌──────────────────────┐
    │ log to               │  │ log to               │
    │ kill_switch_events   │  │ kill_switch_events   │
    │ (mode='observe')     │  │ (mode='enforce')     │
    │ last_observe_at = T  │  │ mark tripped=1       │
    │                      │  │ tripped_at = T       │
    │ TRADING CONTINUES    │  │ CRITICAL alert sent  │
    └──────────┬───────────┘  └──────────┬───────────┘
               │                          │
         cooldown 300s                    ▼
         before same rule        ┌──────────────────┐
         can trip again          │ any_kill_switch_ │
               │                 │ tripped() > []   │
               ▼                 │                  │
       [back to evaluate]        │ orchestrator     │
                                 │ skips alloc+fill │
                                 │ this cycle       │
                                 └────────┬─────────┘
                                          │
                                          │ operator intervenes:
                                          │
                                          │   python scripts/
                                          │     kill_switch_reset.py
                                          │     --trigger <name>
                                          │     --by <you>
                                          │
                                          ▼
                                ┌──────────────────────┐
                                │ tripped=0            │
                                │ reset_at = T         │
                                │ reset_by = <name>    │
                                │                      │
                                │ (audit trail in      │
                                │  kill_switch_events) │
                                └──────────┬───────────┘
                                           │
                                           ▼
                                   ┌────────────────┐
                                   │   OK (idle)    │
                                   └────────────────┘
```

---

## 6. Overall phase status + unlock conditions

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                                                                      │
 │   Phase 0  MVP                              ✅ READY                 │
 │     ↓                                       (24h soak recommended)   │
 │                                                                      │
 │   Phase 1  Dual Platform                    ✅ READY                 │
 │     ↓                                       (needs event_map.yaml)   │
 │                                                                      │
 │   Phase 2  Risk + Reliability               ✅ READY (observe mode)  │
 │     ↓                                       (14-day soak + macro)    │
 │                                                                      │
 │   Phase 2.5  Backtest                       ✅ READY                 │
 │     ↓                                       (read first census!)     │
 │                                                                      │
 │   Phase 3  Paper→Live                       🔒 FROZEN — skeleton     │
 │     ↓                                                                │
 │     ├── requires KYC on both platforms                               │
 │     ├── requires funded accounts ($300-500)                          │
 │     ├── requires you to write real ExchangeClient                    │
 │     ├── Gate 1 (3d) → Gate 2 (4d) → Gate 3 (7d+)                     │
 │     └── write decision doc: SCALE | HOLD | REDESIGN | STOP           │
 │                                                                      │
 │   Phase 4  Auto Matching                    ✅ READY (stub + LLM)    │
 │                                             (validate vs Phase 1)    │
 │                                                                      │
 │   Phase 5  Expansion                        🔒 FROZEN                │
 │     ↓                                                                │
 │     ├── global_phase5_enabled default False                          │
 │     ├── all strategy flags default False                             │
 │     └── unlock: Phase 3 decision == SCALE                            │
 │                                                                      │
 │   Phase 6  Productization                   💭 LIFE DECISION         │
 │                                             (not a code phase)       │
 │                                                                      │
 └─────────────────────────────────────────────────────────────────────┘

                      Current working path:
                  ─────────────────────────────
           Phase 0/1/2 running ── you ── Phase 3 prep
                                    │
                                    │ (code won't help here —
                                    │  this is KYC / funding /
                                    │  rules reading / soak)
```

---

## Summary in 3 sentences

- **Architecture**: four layers separated by explicit pure/impure boundaries; Layer 3 is sacred.
- **Safety**: every risky action has redundant checks — observe→enforce for kill switches, 4-check gating for live execution, file-based hot kills for strategies, git-clean check for real money.
- **Discipline**: code doesn't let you skip phases. Gates are enforced in code, not docs. If you want to move fast, the system stops you — which is the only way it eventually becomes something worth running.
