# Phase 2 — Risk Controls and Reliability

**Duration:** 3 weeks (realistically 3–4)
**Goal:** Make the system robust enough to run unattended for two weeks including a macro event, without producing bad state.
**Prerequisite:** Phase 1 exit criteria all met.

---

## The core tension

Phase 2 is where side projects die from boredom, not bugs.

You just spent Phase 1 building something visibly interesting — cross-platform scanning, annualized return math, an event map growing by the day. Phase 2 now asks for two weeks of work that add exactly zero new features. No better detection. No new platforms. No bigger opportunity pool. Just defensive plumbing.

**The boring part is the point.** Every control in this phase exists because someone, somewhere, lost real money without it. A kill-switch that fires three times a year is ~30 bad trades you didn't make, which is worth all the setup work.

But there's a subtler tension. A naive approach to risk controls is to build them and turn them on. This is wrong. **Risk controls that trip unexpectedly are worse than no risk controls**, because you lose trust in them. The right pattern is monitoring → observation-only policy → enforced policy, over days or weeks, so you understand the system's behavior before you give any rule the authority to halt trading.

Phase 2 is also where you formalize provenance: every trade becomes traceable back to the code version, config version, and event map version that produced it. This is boring until three weeks later when you need it, at which point it's worth more than the rest of the phase combined.

---

## Success criteria

1. The system runs 14+ consecutive days unattended including at least one major macro event (CPI, NFP, or FOMC)
2. Every category of failure has a handled path, tested by deliberately causing the condition
3. Monitoring is continuous; policy runs first in observe-only mode, then with enforcement
4. You get notifications that are informative without being noise
5. You can `kill -9` the process at any moment and recover cleanly — same paper positions, same cumulative PnL, no duplicates
6. You can trace any individual trade back to the exact code commit, config hash, and event map version that produced it
7. You trust the system enough to hand it real money (even if you don't yet)

---

## Monitoring / policy / enforcement separation

This is the architectural pattern that makes risk controls safe to deploy.

```
┌─────────────────────────────────────────────────────────────┐
│  Monitoring layer                                            │
│  - Always on, from day one of Phase 2                        │
│  - Records metrics: PnL, exceptions, latency, drift, etc.    │
│  - Never makes decisions                                     │
│  - Emits structured events to a metrics store                │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Policy layer                                                │
│  - Reads monitoring metrics                                  │
│  - Evaluates rules ("daily loss > threshold?")               │
│  - Has two modes: OBSERVE and ENFORCE                        │
│  - In OBSERVE mode: logs what it would have done             │
│  - In ENFORCE mode: can trip the kill switch                 │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Enforcement layer                                           │
│  - Actually halts trading when told to                       │
│  - Persists kill-switch state to SQLite                      │
│  - Refuses to restart trading without manual reset           │
└─────────────────────────────────────────────────────────────┘
```

**Deployment sequence for every new kill-switch:**
1. Implement in monitoring — confirm the metric is being computed correctly
2. Add policy rule in OBSERVE mode — let it run for 3+ days, compare "would have tripped" events to your expectations
3. Tune thresholds based on observation
4. Flip to ENFORCE mode — and even then, only after you've seen it behave correctly in observe mode

This sequence is how you avoid the classic failure: "I added a kill switch, it fired at 3am on false signal, my system halted for 6 hours, I missed real opportunities."

---

## Deliverables

### 1. Monitoring layer

`src/monitoring/` — a standalone subsystem that:
- Subscribes to events from all four architectural layers
- Computes derived metrics (rolling PnL, rolling exception count, latency percentiles, clock drift, API health)
- Writes metrics to a time-series table in SQLite
- Exposes metrics via the `/metrics` endpoint (next deliverable)

Key metrics:
- `opportunities_detected_per_minute` (by strategy)
- `opportunities_passed_filters_per_minute`
- `trades_executed_per_minute`
- `rolling_pnl_24h` (for paper, per strategy)
- `api_error_rate_per_minute` (by platform)
- `api_latency_p50`, `p95`, `p99` (by platform)
- `clock_drift_seconds` (vs NTP)
- `layer_heartbeat_age_seconds` (each of the 4 layers emits heartbeats)
- `capital_utilization_pct`
- `event_map_content_hash` (detects changes)

### 2. Kill-switch framework (policy + enforcement)

`src/risk/policy.py` defines rules. `src/risk/enforcement.py` acts on them.

Triggers implemented in sequence (monitoring first, then observe, then enforce):

```python
class KillSwitchTrigger(Enum):
    DAILY_LOSS_EXCEEDED = "daily_loss"
    ABNORMAL_PRICE_JUMP = "price_jump"           # >20% move in one tick
    API_DISCONNECT = "api_disconnect"            # no data for 60s
    CLOCK_DRIFT = "clock_drift"                  # local vs NTP > 5s
    UNHANDLED_EXCEPTION_RATE = "exception_rate"  # >5 exceptions in 5 min
    POSITION_MISMATCH = "position_mismatch"      # reconciliation failed
    USDC_DEPEG = "usdc_depeg"                    # USDC < 0.995
    LAYER_STALL = "layer_stall"                  # heartbeat > 2 minutes old
    MANUAL = "manual"                             # kill file detected
    EVENT_MAP_DRIFT = "event_map_drift"          # content hash changed mid-run
```

Each trigger has a policy definition:
```python
@policy_rule
def daily_loss_exceeded(metrics: Metrics, config: Config) -> PolicyDecision:
    loss = metrics.rolling_pnl_24h
    if loss < -config.max_daily_loss_usd:
        return PolicyDecision.TRIP(
            reason=f"daily_loss={loss} exceeds limit {config.max_daily_loss_usd}"
        )
    return PolicyDecision.OK
```

**Mode control:**
```yaml
risk:
  policy_mode: observe  # or "enforce"
  rules:
    daily_loss_exceeded:
      enabled: true
      mode: enforce  # per-rule override
    usdc_depeg:
      enabled: true
      mode: observe  # new rule, still learning
```

The kill-switch state table in SQLite records every OBSERVE event alongside every ENFORCE event. This gives you "would have tripped" history to tune thresholds with actual data.

### 3. Reconciliation

`scripts/reconcile.py` — runs every 5 minutes and at end of day:

**In paper mode:** re-computes what each open position *should* be worth given current market prices, compares to the agent's internal state. Any mismatch = bug or race condition. Worth investigating immediately.

**In live mode (Phase 3 onward):** fetches actual exchange positions, compares to SQLite. Mismatch trips `position_mismatch`.

**Reconciliation is the single most important defense** against silent accounting bugs. Do not skip it.

### 4. Alerting

`src/alerts/` — Telegram webhook integration.

**Alert levels:**
- INFO: daily summary, notable opportunity detected
- WARN: retry succeeded, slow API response, adverse selection filter heavily tripped
- ERROR: unhandled exception, reconciliation mismatch, policy rule would have tripped (in observe mode)
- CRITICAL: kill switch ENFORCED, system halted

**Rate limiting on alerts:**
No more than 5 non-CRITICAL alerts per hour. Excess are batched and included in the next summary. Otherwise alert fatigue kills you.

**Alert template:**
```
[ERROR] Phase 2 — Observe-mode policy trip
Rule: daily_loss_exceeded
Current: -$47.20 (limit: -$50)
Would enforce in: 1 more hour of current rate
Metrics: rolling_pnl_24h=-47.20, trades_today=14
Git: abc123 | Config: def456
```

Useful alerts tell you **what, why now, how to react**.

### 5. Crash recovery

System must survive being killed at any moment:

- All state in SQLite (transactional) or Parquet (time-series)
- No in-memory state that matters beyond a single orchestrator loop cycle
- On startup:
  1. Load last known state
  2. Check kill-switch state — refuse to start trading if tripped
  3. Run reconciliation immediately
  4. Log startup event with git hash, config hash, schema version
  5. Resume

Test: `kill -9` during an active loop cycle, during reconciliation, during a trade fill, during a Parquet write. Each must recover cleanly.

### 6. Idempotency

Every trade attempt carries a deterministic `client_order_id`:

```python
def compute_client_order_id(opp: Opportunity) -> str:
    content = f"{opp.opportunity_id}|{opp.detected_at.isoformat()}|{opp.market_id}|{opp.strategy}|{opp.size_contracts}"
    return "co_" + hashlib.sha256(content.encode()).hexdigest()[:16]
```

Before submitting, check SQLite for an existing record with that ID. Found → skip. This prevents duplicates from retries, restarts, or race conditions.

### 7. Provenance bundle (every trade record tagged)

`src/provenance.py`:

```python
class ProvenanceBundle(BaseModel):
    git_commit: str           # Short hash
    git_dirty: bool           # Uncommitted changes?
    config_hash: str          # SHA-256 of config.yaml
    event_map_hash: str       # SHA-256 of event_map.yaml
    schema_version: int
    started_at: datetime

    def serialize(self) -> str:
        return json.dumps(self.dict(), default=str)
```

Every `paper_trade` row includes this bundle. Every `error` log includes it. Every `kill_switch_event` includes it.

**Enforcement:** the orchestrator refuses to start if `git_dirty == True` and mode is live. Uncommitted code cannot trade real money. In paper mode, dirty is allowed but logged.

### 8. Structured logging

Replace every `print()` with `structlog`.

```python
log.info(
    "opportunity_detected",
    strategy="cross_market",
    pair_id="fed-dec-2026-cut",
    annualized_return=0.32,
    size_contracts=87,
    provenance=provenance.serialize(),
)
```

Logs go to stdout (for tailing) and a rotating file (for grep). JSON only — no human-readable prose — because you'll query them later.

**Secret redaction layer:** wrap the log formatter so API keys, Telegram bot tokens, and private keys never appear in output even if they sneak into an exception. Test the redaction layer explicitly.

### 9. Health endpoint

Tiny HTTP server on localhost only:

```
GET /health
  → 200 OK with {mode, uptime, kill_switch_state, last_reconcile}
  → 503 with reason if not healthy

GET /metrics
  → Plain text, Prometheus format
  → Include all metrics from section 1
```

Bind to 127.0.0.1 only. Never 0.0.0.0. No auth, because no external exposure.

### 10. Runbook

`docs/runbook.md` — written while the system is calm, because that's when you have patience.

Answer these:
1. What do I do when `daily_loss_exceeded` trips?
2. What do I do when reconciliation fails?
3. What do I do when an exchange API is down?
4. How do I manually close all open paper positions?
5. How do I safely roll back to a previous code version?
6. How do I reset the kill switch, and what reasons are acceptable?
7. Where do alerts go, and who's on-call? (Usually: you.)
8. What do I do if I come back from vacation and the system has been halted for 3 days?

---

## Task breakdown

All `[infra]` or `[research]` — no new math in Phase 2.

### Task 2.1 `[infra]` — Structured logging migration
Replace all prints. Add redaction layer. Unit test that secrets in a sample exception don't appear in output.

### Task 2.2 `[infra]` — Monitoring layer
All metrics from section 1, written to SQLite time-series table.

### Task 2.3 `[infra]` — Health endpoint
`/health` and `/metrics`. Localhost bind only.

### Task 2.4 `[infra]` — Kill switch framework (policy layer)
Define rule interface, load from config, default every rule to OBSERVE mode. No enforcement yet.

### Task 2.5 `[infra]` — First kill switch rule in observe mode
`daily_loss_exceeded`. Watch it in observe mode for 2 days. Verify the metric is sane. Tune the threshold.

### Task 2.6 `[infra]` — Remaining kill switch rules in observe mode
All triggers from section 2. Each runs in observe mode for at least 48 hours, with observation logged.

### Task 2.7 `[infra]` — Reconciliation (paper)
Scheduled job, compares internal position state to recomputed from Parquet.

### Task 2.8 `[infra]` — Telegram alerts
Bot setup, levels, rate limiting, alert templates, end-of-day summary.

### Task 2.9 `[infra]` — Idempotency
Client order IDs, dedup check, tests with forced retries.

### Task 2.10 `[infra]` — Provenance bundle
Git hash, config hash, event map hash. Attach to every trade and error.

### Task 2.11 `[infra]` — Crash recovery test suite
`kill -9` in 5+ different states. Each must recover cleanly.

### Task 2.12 `[research]` — Write the runbook
Answer all questions from section 10.

### Task 2.13 `[infra]` — Chaos tests
Deliberately break things (see list below). Each should produce a correct response.

### Task 2.14 `[infra]` — Flip selected rules to ENFORCE mode
Only after observe-mode data looks good. Do one at a time.

### Task 2.15 `[infra]` — 14-day soak test with macro event
Run continuously through a scheduled FOMC or CPI release. Review all alerts.

---

## Chaos testing

Before declaring Phase 2 done, deliberately break each of these and verify correct response:

- [ ] Disconnect network for 2 minutes → API disconnect observed, alert fires, reconnects cleanly
- [ ] Corrupt a SQLite row → detected on next read, alert, manual fix required
- [ ] `kill -9` during active trade → recovery with no duplicates
- [ ] Shift system clock forward 10 seconds → clock drift policy triggers
- [ ] Point Polymarket client at fake endpoint returning malformed JSON → logged, skipped, retried
- [ ] Fake endpoint hangs forever → timeout and retry with backoff
- [ ] Fill data directory to 95% capacity → disk space alert
- [ ] Modify `event_map.yaml` mid-run → `event_map_drift` policy triggers
- [ ] Kill the Layer 1 recorder but leave Layer 3-4 running → `layer_stall` policy triggers

Chaos tests are not optional. **They are the phase.** The deliverables above are just prerequisites for being able to run these tests.

---

## Gotchas

**Alert fatigue.** First day, you'll get 40 alerts and mute the channel. Then a real issue will fire and you'll ignore it. Solution: tune thresholds aggressively in first 48 hours. Target 1-5 alerts/day steady state. Noisier → tune more. Silent → probably broken.

**"Day" boundaries.** Daily loss, daily reports, daily metrics — all need a consistent definition. Use UTC midnight for everything. Rolling 24h sounds nicer but makes everything harder to reason about.

**SQLite concurrent writes.** Async coroutines stepping on each other. Use WAL mode and serialize writes through a single async-locked writer coroutine. Don't let multiple places write to SQLite directly.

**Kill-switch reset script bugs.** You'll trip the switch during testing, try to reset it, discover the reset script is broken. **Write and test the reset script before implementing any triggers.**

**"Benign restart" race.** System restarts, Layer 1 starts recording, Layer 3 starts scanning, but the paper position state hasn't loaded yet. Scanner sees "no open positions" and happily allocates capital twice. Fix: state loading happens before the first scan cycle, and the loop refuses to execute until state is confirmed loaded.

**Retry loops without backoff.** Tight retry on a failing API can hit rate limits in seconds. Always exponential backoff. Always max retries. Always log every retry with the reason.

**Exceptions leaking secrets.** Python tracebacks can include local variable values. An exception near a line holding `api_key = "..."` serializes the key into the traceback. Redaction layer must handle formatted tracebacks, not just log messages.

**Observe-mode policy that never trips.** You set it up, it never fires for 2 weeks, you conclude it's working. But maybe the threshold is so loose it would never fire in enforcement either. **Explicitly force each policy to trip once in test conditions** before accepting that it "works in observe mode."

**Timezone drift in scheduled jobs.** Daily summary at 23:00 UTC. Your server is US Eastern. DST changes, now it runs at 22:00 UTC for 6 months and you don't notice. Schedule using UTC cron expressions, never "11pm server time."

**The macro event that doesn't happen.** Your 14-day soak test window doesn't happen to include a CPI or FOMC date. Extend the window — the soak test isn't done until a macro event is in it, because **macro events are where weird liquidity and latency effects happen**, and you need to see how your system behaves under them.

---

## Failure modes and when to loop back

- **Reliability bugs surfacing in earlier code.** Good — that's chaos testing working. Fix in place, don't retroactively "blame" Phase 0.

- **Can't pass 14-day soak test.** Something is deeply wrong. Do not paper over. Root cause it, even if it costs an extra week. Going to Phase 2.5 with an unstable system wastes Phase 2.5.

- **Unexplained reconciliation mismatches.** Scariest failure mode. Internal accounting is wrong somewhere. Find the root cause before advancing. Do not advance hoping the mismatch was "probably a race condition."

- **Observe-mode policies trip constantly.** Thresholds are too tight, or there's a genuine pattern you didn't expect. Investigate — this is Phase 2 teaching you about your system's real behavior.

- **Bored, want to skip to Phase 2.5.** Most common failure mode. A week of boredom now saves a month of stress later.

---

## Cost budget

Phase 2 should cost **< $75/month**:
- Everything from Phase 1
- Telegram bot: free
- Additional storage for metrics + alerts: small
- Still local-run, still free compute

If you're tempted to move to cloud in Phase 2, wait until Phase 3 at minimum. Phase 2 is about correctness, not infrastructure ambition.

---

## Exit criteria → Phase 2.5

- [ ] 14-day unattended soak test passed, including at least one macro event
- [ ] All chaos tests pass
- [ ] Monitoring layer producing all required metrics
- [ ] Every kill-switch rule has run in observe mode for ≥48 hours before enforcing
- [ ] At least one kill switch has actually fired (in testing, on real data) and been reset
- [ ] Reconciliation running on schedule with zero unexplained mismatches in the soak window
- [ ] `docs/runbook.md` complete and re-read once
- [ ] Provenance bundle attached to every trade — you can pick any trade and answer "what code/config produced this?"
- [ ] Every alert in the soak window was either actionable or useless noise that got tuned out
- [ ] You genuinely trust the system enough to hand it real money

That last criterion is subjective and critical. If you don't trust it, don't advance. Trust is earned by watching correct behavior over time, not by checking boxes.
