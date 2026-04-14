# Runbook — Operating the Arb Agent

Written when the system is calm. Re-read it twice a year.

## Quick reference

| Need | Command |
|---|---|
| Start orchestrator | `python -m src.main --config config.yaml` |
| Reconcile (paper) | `python scripts/reconcile.py --config config.yaml` |
| Replay determinism check | `python scripts/replay.py --from 2026-04-14 --to 2026-04-15` |
| Threshold calculator | `python scripts/threshold_calc.py --intra 0.20 --p-div 0.05` |
| Reset all kill switches | `python scripts/kill_switch_reset.py --trigger <name>` |
| Manual halt | `touch /tmp/arb_agent.kill` (auto-detected next cycle) |
| Manual unhalt | `rm /tmp/arb_agent.kill` then reset that trigger |
| Health endpoint | `curl http://127.0.0.1:9100/health` |
| Metrics endpoint | `curl http://127.0.0.1:9100/metrics` |
| Tail structured logs | `tail -f logs/arb.log \| jq` |

## 1. `daily_loss_exceeded` tripped — what now?

1. `curl localhost:9100/health` — confirm trip.
2. Pull the last 24h trades:
   ```sql
   SELECT * FROM paper_trades WHERE opened_at > datetime('now','-1 day');
   ```
3. For each losing trade: pull the underlying snapshot from Parquet, recompute by hand. Was the math wrong, or was it a real adverse outcome?
4. If the math was wrong → fix the bug, write a regression test, deploy, then reset.
5. If the loss was real → investigate whether the strategy's edge has degraded. Consider raising thresholds before resetting.
6. Reset only after root cause is documented:
   ```bash
   python scripts/kill_switch_reset.py --trigger daily_loss_exceeded --by your-name
   ```

**Never** reset just because you're impatient. The whole point is to slow down and look.

## 2. Reconciliation mismatch

Reconcile compares `paper_trades.capital_locked_usd` against `opportunities.capital_at_risk_usd`. Mismatches usually mean either a bug in the paper executor or a manual edit to the DB.

1. Run `python scripts/reconcile.py` and capture the JSON output.
2. For each `error` finding, query both rows in SQLite and compare by hand.
3. **Do not** "fix" by overwriting one side to match the other. Find which side is wrong and why.
4. After fix is applied and a re-run shows zero `error` findings, reset `position_mismatch` if it was tripped.

## 3. Exchange API is down

The `api_disconnect` switch trips automatically when heartbeats go silent.

1. Confirm via `curl https://gamma-api.polymarket.com/markets?limit=1` (or kalshi equivalent).
2. If both platforms confirm down → the kill switch worked correctly. Wait, monitor, reset when service resumes.
3. If only your local network is down → fix locally, then reset.
4. If only one platform is affected → optionally edit config to disable the affected platform's fetcher, restart, continue with the other.

## 4. Manually close all open paper positions

```python
# Run inside `python -i`
from src.config import load_config
from src.storage import state_db
cfg = load_config('config.yaml')
conn = state_db.connect(cfg.storage.state_db_path)
state_db.init_schema(conn)
from datetime import datetime, timezone
from decimal import Decimal
for pos in state_db.open_positions(conn):
    state_db.mark_resolved(conn, pos.client_order_id, Decimal('0'),
                           datetime.now(timezone.utc).isoformat())
```

For LIVE mode (Phase 3+), this is not enough — you must also place actual close orders on each exchange.

## 5. Roll back to a previous code version

1. `git log --oneline --decorate -20` — find the previous good commit hash.
2. Stop the orchestrator (Ctrl+C or SIGTERM).
3. `git checkout <good-hash>`.
4. `pytest tests/` — verify all tests still pass on the older code (in case the DB schema has moved).
5. If schema mismatch (Phase 2's `schema_version` lower than file's): you cannot roll back without a manual migration. Document and avoid moving forward without backup.
6. Restart orchestrator.

## 6. Reset a kill switch — when is it acceptable?

Acceptable reasons to reset:
- Root cause identified, fix deployed, regression test added.
- The trigger was a false positive AND its threshold has been adjusted.
- The trigger fired during a known maintenance window (e.g., scheduled rates calibration).

Unacceptable reasons:
- "It's been a while, probably fine."
- "I want to trade the next opportunity."
- "I changed the threshold to be more permissive."

Use:
```bash
python scripts/kill_switch_reset.py --trigger <name> --by <your-name>
```
The reset is logged with reset_at + reset_by. Audit trails matter.

## 7. Where do alerts go?

- Telegram bot configured via env vars `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.
- If unset → stub mode (alerts log to stdout only). Useful for development.
- On-call: you (until Phase 6).

## 8. Coming back from vacation, system halted for 3 days

1. `curl localhost:9100/health` — see what's tripped.
2. Read `kill_switch_events` for the trip details:
   ```sql
   SELECT * FROM kill_switch_events
   WHERE trigger = '<name>' ORDER BY occurred_at DESC LIMIT 5;
   ```
3. Check Parquet recordings still happened during the halt — Layer 1 keeps recording even when trading halts.
4. If you missed real opportunities: tally the cost via replay (`scripts/replay.py`) over the halted window. This is feedback for whether your thresholds were too tight.
5. Decide whether to reset or extend the halt with adjusted config.

## Chaos test results (last full run)

See `tests/test_chaos.py` — every scenario from `phase-2-risk-reliability.md` section "Chaos testing" has an automated assertion. Run before any production change:

```bash
pytest tests/test_chaos.py -v
```

Failing chaos tests are deploy blockers.
