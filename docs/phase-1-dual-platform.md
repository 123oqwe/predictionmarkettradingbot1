# Phase 1 — Dual Platform + Event Matching

**Duration:** 4–5 weeks (realistically 5–6)
**Goal:** Detect cross-platform arbitrage on a hand-curated whitelist, with adverse selection awareness baked in.
**Prerequisite:** Phase 0 exit criteria all met. Layer architecture is working.

---

## The core tension

Phase 0 was a coding problem. **Phase 1 is partly a reading problem and partly a disciplined skepticism problem.**

The reading problem is obvious: most of your time will be spent comparing market rules on two platforms, trying to figure out whether "Will the Fed cut rates in December?" on Polymarket resolves identically to its Kalshi equivalent. Answer is often "almost, except for this edge case" — and that edge case is where money gets lost.

The skepticism problem is less obvious but more important. When you see `YES_A + NO_B < 1` on two different platforms, **the first question is not "how do I trade this?" — it's "why is it still here?"** If it were truly free money, faster bots would have taken it already. What remains in front of you has a reason: latency loss, rule divergence, or information asymmetry. The first two you can sometimes work with; the third is adverse selection, and it's how you lose money while feeling like you're winning.

So Phase 1 is about building two kinds of discipline: the discipline to read rules carefully, and the discipline to **systematically suspect every opportunity** before trading it.

---

## Success criteria

1. Kalshi data flows through Layer 1 into the same Parquet format as Polymarket, cleanly
2. `event_map.yaml` contains 10–30 hand-verified pairs, each with documented edge case review
3. Cross-market detection finds opportunities on those pairs, with adverse selection filters applied
4. Daily report split by strategy type with annualized return distributions
5. You can quantify: "On my whitelist, how many cross-market opportunities per day clear my filters, at what annualized return?"

---

## Scope

**In scope:**
- Kalshi API client as a Layer 1 fetcher (architecture stays intact)
- Manual `event_map.yaml` with edge case notes
- Cross-market detection with mathematically-derived threshold (not a magic number)
- Adverse selection filters (age-of-opportunity, news-context, counterparty hints)
- Daily report with annualized return histograms
- Cost budget for Kalshi API

**Out of scope:**
- Automated event matching (Phase 4)
- Live trading (Phase 3)
- Any platform beyond Polymarket + Kalshi
- Fancy risk controls (Phase 2)

---

## Deriving the cross-market threshold (not a magic number)

A common mistake: picking a threshold like "5% absolute profit" out of thin air. Let's derive it properly.

**Setup.** Your intra-market threshold is 20% annualized return. You're considering cross-market trades, which carry an extra risk that the two markets' rules diverge at resolution — even on pairs you've verified.

**Model the rule-divergence loss:**
- Let `p_divergence` = probability a verified pair resolves differently (~1–3% realistically, even with careful review)
- When divergence happens, you don't get $1 from a matched pair — you lose the whole position. Loss-given-divergence ≈ 100% of capital-at-risk.

**Expected value of a cross-market trade at annualized return `r`:**
```
EV = (1 - p_divergence) * r + p_divergence * (-1.0 annualized)
```

For the trade to be worth doing at the same EV as an intra-market trade at 20%:
```
r_cross * (1 - p_divergence) - p_divergence ≥ 0.20
r_cross ≥ (0.20 + p_divergence) / (1 - p_divergence)
```

With `p_divergence = 0.02`:
```
r_cross ≥ 0.224 / 0.98 ≈ 22.9% annualized
```

With `p_divergence = 0.05`:
```
r_cross ≥ 0.263 / 0.95 ≈ 27.7% annualized
```

**Conclusion:** Your cross-market threshold should be in the 23–28% annualized range, with the exact number depending on how confident you are in your verification process. Start conservative (28%) and relax only as you accumulate a clean track record.

Notice how this derivation also tells you something important: **the better your edge case review, the more trades become viable.** Sloppy review means `p_divergence` is high, which raises the threshold, which kills most opportunities. Rigorous review is directly worth money.

Put this in your config:
```yaml
strategy:
  intra_market:
    min_annualized_return: 0.20
  cross_market:
    min_annualized_return: 0.28
    assumed_rule_divergence_prob: 0.05
    # Can be tightened as track record improves
```

---

## The event matching problem, with a worked example

**Polymarket:** *"Will the Fed cut rates in December 2026?"*
Rules (hypothetical): Resolves YES if the Federal Reserve decreases the federal funds rate target range at the December 2026 FOMC meeting. Resolves NO otherwise. Resolution based on the FOMC statement released at the meeting.

**Kalshi:** *"Will the Federal Reserve lower the federal funds rate target at the December FOMC meeting?"*
Rules: Resolves YES if the upper bound of the federal funds rate target range is lower after the December FOMC meeting than before. Resolves NO otherwise.

Look identical. Now think through edge cases:

| Scenario | Polymarket | Kalshi | Match? |
|---|---|---|---|
| Standard 25bps cut | YES | YES | ✓ |
| Standard 50bps cut | YES | YES | ✓ |
| Inter-meeting cut Dec 20 (after meeting) | NO (not "at" meeting) | NO | ✓ |
| FOMC meeting postponed to January | Ambiguous | NO (no Dec meeting) | ⚠ divergent |
| Rate range change where only lower bound drops | Ambiguous (uses "range") | NO (uses "upper bound") | ⚠ divergent |
| Fed announces cut effective in January | Likely YES (announcement date) | Likely YES | ✓ |
| Decision delayed past midnight | YES | YES (same day) | ✓ |
| Fed dissolved | Unaddressed | Unaddressed | ⚠ both undefined |

**Two divergent scenarios identified.** This pair is not unconditionally safe. Options:

- **Block the pair entirely.** Safest, simplest.
- **Conditionally enable.** Trade only when none of the divergent scenarios are plausible (no rumors of meeting postponement, no asymmetric range changes in recent Fed history). Requires ongoing news monitoring.
- **Accept the risk with a buffer.** Bump this pair's threshold from 28% to 40% annualized, reflecting the extra ambiguity.

Document whichever you choose. No silent decisions.

**This is what verifying a pair looks like.** 30 minutes to 2 hours of reading and thinking. If a pair takes 5 minutes, you're not doing it right.

---

## Adverse selection filters

When detection finds a cross-market opportunity, it passes several skepticism gates before becoming a tradable candidate:

### Filter 1: Age of opportunity

If a cross-market gap has been visible for > N seconds, it's either (a) too small for other bots to care about or (b) too risky for them to take. Either way, you should hesitate.

```python
def age_filter(opp: Opportunity, market_history: MarketHistory, threshold_s: int = 60) -> bool:
    """Reject opportunities that have been visible for too long."""
    first_seen = market_history.first_time_opportunity_existed(opp)
    age_s = (opp.detected_at - first_seen).total_seconds()
    if age_s > threshold_s:
        log.info("opportunity_too_old", age_s=age_s, opp_id=opp.id)
        return False
    return True
```

**Why it matters:** Old-and-persistent opportunities are a warning sign, not a gift. Something is keeping them there.

### Filter 2: News context

Near a major news event for the underlying topic (e.g., within 60 minutes of a Fed speech for a rate market), reject the opportunity.

```yaml
adverse_selection:
  news_windows:
    - topic_tags: [fed, interest_rates]
      blackout_minutes_before: 15
      blackout_minutes_after: 60
    - topic_tags: [crypto]
      blackout_minutes_before: 5
      blackout_minutes_after: 30
```

**Why it matters:** During/after news, one side of a cross-market pair updates faster than the other. The "arbitrage" you see is one platform being slow to absorb new information — and you're betting the slow side is right.

### Filter 3: Minimum history

Don't trade a newly-listed market (< 24 hours old) until it has had time to be priced by the normal participants. Early prices are noisy and often have massive spreads that evaporate once real trading starts.

### Filter 4: Counterparty hint (advanced, skip if data unavailable)

If either platform exposes trade tape, check recent trades on both sides. If the side you'd be buying has seen large sells recently from accounts that trade well, you might be the patsy. Concretely: if someone just sold 500 YES at 0.46, and you're trying to buy YES at 0.46, you're now in the seat they vacated.

Most platforms don't expose enough trade tape data for this filter. Skip if unavailable; enable it as platforms add transparency.

### Filter integration

Filters run between detection and execution. Rejected opportunities are still **logged to Parquet** with the rejection reason, so you can later analyze whether your filters are too strict or too loose.

---

## Deliverables

### 1. Kalshi Layer 1 fetcher

`src/layer1_data_recording/kalshi_fetcher.py` — same interface as the Polymarket fetcher, writes to `data/snapshots/kalshi/YYYY-MM-DD.parquet`. Auth via env var. Zero changes to Layers 2, 3, or 4 — this is the test of whether the architecture from Phase 0 holds up.

### 2. Event map loader

`src/matching/event_map.py` — loads and strictly validates `event_map.yaml`. Rejects pairs with missing fields or incomplete edge case notes. Computes a content hash of the file, stores it in provenance on every scan.

### 3. Event map format

```yaml
pairs:
  - pair_id: fed-dec-2026-cut
    polymarket_market_id: "0xabc..."
    kalshi_market_ticker: "FED-26DEC-CUT"
    verified_by: "your-name"
    verified_date: "2026-04-20"
    trading_enabled: true
    confidence: 0.95
    topic_tags: [fed, interest_rates]

    # Explicit threshold override (optional; falls back to config default)
    min_annualized_return_override: null

    edge_cases_reviewed:
      - scenario: "standard 25bps cut"
        polymarket: "YES"
        kalshi: "YES"
        divergent: false
      - scenario: "inter-meeting cut after December meeting"
        polymarket: "NO"
        kalshi: "NO"
        divergent: false
      - scenario: "December meeting postponed to January"
        polymarket: "ambiguous"
        kalshi: "NO"
        divergent: true
        mitigation: "disable pair if postponement is announced; not applicable otherwise"
      - scenario: "asymmetric range change"
        polymarket: "ambiguous"
        kalshi: "NO (uses upper bound)"
        divergent: true
        mitigation: "historical Fed behavior makes this <1% likely; accept residual risk"

    notes: |
      Rule divergence probability estimated at ~2% based on the two documented
      scenarios. Both mitigations are monitored manually.

  - pair_id: btc-100k-eoy-2026
    polymarket_market_id: "0xdef..."
    kalshi_market_ticker: "BTC-26DEC-100K"
    verified_by: "your-name"
    verified_date: "2026-04-20"
    trading_enabled: false
    confidence: 0.6
    topic_tags: [crypto, btc]
    edge_cases_reviewed:
      - scenario: "BTC near $100k at expiry"
        polymarket: "Coinbase spot close"
        kalshi: "CME futures settlement"
        divergent: true
        mitigation: "BLOCKED — can diverge by several hundred dollars"
    notes: |
      BLOCKED. Settlement source difference is too large and too common.
```

**Rule:** `trading_enabled` defaults to false. `edge_cases_reviewed` must have at least 5 entries, with at least one marked `divergent: true` addressed explicitly (if you can't find any divergent scenarios at all, you probably didn't look hard enough, which is itself a warning).

### 4. Cross-market detection (⚠️ math-critical)

`src/layer3_strategy/cross_market.py`, still a pure function:

- Takes enabled pairs and current market snapshots from both platforms
- For each pair, computes both directions:
  - `yes_ask_A + no_ask_B + fees + gas` (buy YES on A, NO on B)
  - `no_ask_A + yes_ask_B + fees + gas` (the reverse)
- Uses the same size-weighted fill pricing and annualized return math from Phase 0
- Applies the pair-specific or config-default cross-market threshold (typically 28% annualized)
- Returns `Opportunity` objects tagged with `strategy="cross_market"`, `pair_id`, and `topic_tags`

Unit tests:
- Both directions of a pair checked independently
- Size capped by smallest of four order books
- Disabled pairs never evaluated
- Fees calculated correctly with different rates on different platforms
- Threshold override on a specific pair works

### 5. Adverse selection layer

`src/layer3_strategy/adverse_selection.py`:
- Age filter: uses a rolling window of recent opportunities stored in-memory
- News filter: reads `adverse_selection.yaml` config with news windows, checks `topic_tags`
- Minimum history filter: rejects markets less than 24h old

This layer sits between detection and allocation. Rejected opportunities logged with reason.

### 6. Unified scanner

The orchestrator now runs both intra and cross detection in every cycle. Same loop, same pure-function strategy layer, just with more markets and more pairs.

### 7. Daily report with annualized distributions

```
=== Daily Report 2026-04-14 ===
Markets scanned: 342 Polymarket + 87 Kalshi = 429
Event pairs tracked: 18 (15 enabled)
Event map version: hash=abc123, verified pairs=18, blocked=3

Opportunities detected (raw):
  intra_market:  42 (ann_return range: 2% - 87%)
  cross_market:  12 (ann_return range: 5% - 62%)

After adverse selection filters:
  intra_market:  28 (rejected: 14, reasons: [stale:6, news_window:5, young_market:3])
  cross_market:   5 (rejected: 7, reasons: [stale:4, news_window:2, young_market:1])

Above threshold (annualized):
  intra_market (20%): 9
  cross_market (28%): 2

Ann return distribution (intra, passed filter):
  20-30%:  ████ 4
  30-50%:  ███  3
  50-100%: ██   2

Paper trades executed: 6
  total capital deployed: $412
  average ann return of trades taken: 41.3%

Paper PnL today: +$14.20 unrealized
Paper PnL cumulative: +$87.15 unrealized, +$12.40 realized (3 positions settled)

Capital utilization: $412 / $1000 = 41%
Errors (1x Kalshi rate limit, 2x Polymarket timeout)
```

### 8. Initial event map (the real work)

Hand-build 10–30 pairs following the verification discipline. Prioritize by liquidity:

- US politics — approval, elections, appointments (watch for resolution source: AP vs Reuters)
- Fed policy — rate decisions, dot plot moves
- Major sports — Super Bowl, World Series, World Cup
- Crypto price thresholds (expect many to be blocked due to settlement differences)
- Macro — CPI, NFP, GDP, unemployment

Expect roughly 30–50% of pairs you evaluate to end up blocked (`trading_enabled: false`). That's a feature, not a failure.

---

## Task breakdown

### Task 1.1 `[research]` — Kalshi API reconnaissance
Endpoints, auth, rate limits, fee schedule, market state enum, ticker format. Document in `docs/kalshi-api-notes.md`.

### Task 1.2 `[infra]` — Kalshi Layer 1 fetcher
Writes to Parquet following same format. Integration test: fetch for 10 min, verify file contents.

### Task 1.3 `[infra]` — Event map schema + loader
Strict validation including edge_cases_reviewed minimum length. Content hash for provenance. Unit test with malformed YAML.

### Task 1.4 `[math]` — Cross-market threshold derivation in config
Encode the threshold derivation in config with assumed `p_divergence`. Write a small calculator script `scripts/threshold_calc.py` that prints the derived threshold given inputs.

### Task 1.5 `[slog]` — Hand-build first 5 pairs
Actual rules reading. Budget 1–2 hours per pair for the first few. Document every edge case considered.

### Task 1.6 `[math]` — Cross-market detection with tests
Pure function in Layer 3. Tests for both directions, threshold override, size sizing, disabled pair rejection.

### Task 1.7 `[infra]` — Adverse selection filter layer
Age, news, minimum history filters. Unit tests for each.

### Task 1.8 `[infra]` — News window config
`adverse_selection.yaml` with event windows for common topic tags.

### Task 1.9 `[infra]` — Orchestrator extension
Add Kalshi fetcher to Layer 1. Add cross-market detection call. Add adverse selection pass. No changes to Layer 4.

### Task 1.10 `[infra]` — Daily report v2
Query SQLite + Parquet, format annualized distributions, include rejection reasons.

### Task 1.11 `[slog]` — Expand event map to 10–30 pairs
More rules reading. Block aggressively; better to have 15 verified pairs than 30 sloppy ones.

### Task 1.12 `[infra]` — 1-week soak test across Kalshi + Polymarket
Run continuously. Review daily reports. Spot-check every cross-market opportunity detected.

---

## Gotchas

**Currency mismatch during USDC depeg.** Kalshi = USD, Polymarket = USDC. Usually 1:1, but USDC has depegged in the past. Monitor USDC price; halt cross-market trading if it drops below 0.995. Cheap insurance.

**Contract size assumption.** Both platforms claim "$1 payout per contract" but handle fractional shares and rounding differently. Verify on a small manual test before trusting your size math.

**Kalshi 1-cent tick size.** Kalshi rounds to whole cents; Polymarket is finer. If detection computes `no_ask = 0.505`, the Kalshi price is actually 0.51. Use the nearest achievable tick when computing fills.

**Timezone hell.** Platforms display resolution times in different zones. Store UTC internally; display whatever you want. A surprising number of bugs come from "resolves at 5pm" meaning different things.

**Market state enums.** Kalshi has `active`, `settled`, `expired`, `determining`, etc. Polymarket has its own. Filter to only the states you understand; reject unknown states with a warning log.

**Rules that reference data sources you don't have.** "Resolves based on Bloomberg Terminal" — do you have a Bloomberg Terminal? Either find another verification source or treat the pair as un-verifiable.

**News filter false positives.** "Fed" appears in many news articles; some are trivial. Start with broad windows, then tighten based on observed false positives (`daily_report` shows which filter rejected which opportunity).

**Lookahead bias in age filter.** You can't know an opportunity's age unless you've been recording it. The age filter requires the Parquet log from Layer 1 to be running. If you enable it before you've been recording for at least a few hours, every opportunity will look "young" (actually just new-to-your-log).

**Event map drift.** Rules can change after you verify a pair. Phase 2 will add an automated rules watcher, but in Phase 1 you have to re-check pairs manually if you suspect changes. Any pair untouched for > 30 days deserves a quick re-verification.

---

## Failure modes and when to loop back

- **Can't verify any pair with confidence.** Rules on the two platforms are always just different enough. This is a legitimate finding: cross-platform arb is harder than expected, and you should invest in intra-market depth (maybe more platforms for intra-market arb) rather than forcing cross-market pairs.

- **All 10 verified pairs are in one category (say, sports).** Strategy is now concentrated. Try to diversify; a single category's structural risk can wipe the whole book.

- **A verified pair turns out to have different rules.** Disable immediately. Post-mortem in the pair's notes. Add the missed edge case category to your review checklist. This is how you improve.

- **Zero cross-market opportunities above 28% annualized in a full week.** Try 23%. If nothing at 23%, either cross-market arb is currently not viable or your filters are too aggressive. Temporarily disable filters to see raw opportunities and diagnose.

- **Adverse selection filters rejecting > 80% of detected opportunities.** Either filters are too strict (loosen them one at a time, measure) or your detection is finding many false positives. Look at what's being rejected and why.

- **Tempted to automate event matching.** Stop. Phase 4. Without the hand-verified ground truth you build here, you can't calibrate an automated matcher.

---

## Cost budget

Phase 1 should cost **< $50/month**:
- Kalshi API: free for read
- Polymarket: free
- Storage growing (now two platforms): still < 5 GB/month
- Running locally: still free
- News/event data for adverse selection: optional; free tier feeds work for now

---

## Exit criteria → Phase 2

- [ ] Kalshi Layer 1 fetcher running stably for 1 week
- [ ] Architecture still clean — Kalshi integration didn't require touching Layers 2, 3, or 4
- [ ] ≥10 enabled pairs in `event_map.yaml`, each with ≥5 edge cases documented
- [ ] Cross-market threshold is derived, not magic
- [ ] Adverse selection filters running and producing sensible rejection reasons
- [ ] Daily report shows annualized return distributions, not just counts
- [ ] At least one cross-market opportunity detected AND manually verified; if zero all week, cross-market edge is questionable (noted in decision doc)
- [ ] You can recite 3 ways two markets can "look the same but resolve differently," from memory
- [ ] You can explain why your cross-market threshold is higher than your intra-market threshold, with numbers

The last two criteria confirm internalization of the core lesson.
