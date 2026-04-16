# Deployment Guide

Keep the orchestrator running forever, survive restarts, leave stats visible.

## Local machine (paper mode, no keys)

The simplest path: install the OS-native service unit, it'll run on login/boot
and restart on any crash.

### macOS

```bash
./deployment/install.sh
# agent now runs, launchd will restart it if it dies
launchctl list | grep arbagent
curl http://127.0.0.1:9100/health
tail -f logs/orchestrator.log
```

### Linux

```bash
./deployment/install.sh
sudo systemctl status arb-agent
curl http://127.0.0.1:9100/health
sudo journalctl -u arb-agent -f
```

### Uninstall

```bash
./deployment/install.sh --uninstall
```

## Controls

| Action | Command |
|---|---|
| Stop trading only (keep recording) | `touch /tmp/arb_agent.kill` |
| Resume after manual halt | `rm /tmp/arb_agent.kill && python scripts/kill_switch_reset.py --trigger manual --by $USER` |
| Stop everything (macOS) | `launchctl unload ~/Library/LaunchAgents/com.arbagent.orchestrator.plist` |
| Stop everything (Linux) | `sudo systemctl stop arb-agent` |
| Restart (macOS) | `launchctl kickstart -k gui/$(id -u)/com.arbagent.orchestrator` |
| Restart (Linux) | `sudo systemctl restart arb-agent` |
| Check health | `curl http://127.0.0.1:9100/health` |
| Prometheus metrics | `curl http://127.0.0.1:9100/metrics` |
| Disable one Phase 5 strategy | `touch /tmp/arb_agent_flags/disable_<name>.flag` |

## Adding real API keys (Phase 3 onward)

**Do not put keys in config.yaml** — that file is committed. Use environment
variables instead. Three options, from simplest to most secure:

### Option 1 (dev): shell export

```bash
export POLYMARKET_API_KEY="..."
export KALSHI_API_KEY="..."
export ANTHROPIC_API_KEY="..."   # optional for Phase 4 real extraction
# then start manually
python -m src.main --config config.yaml
```

This works for quick tests but doesn't survive reboots or auto-restarts.

### Option 2 (macOS persistent): launchctl setenv

```bash
launchctl setenv POLYMARKET_API_KEY "..."
launchctl setenv KALSHI_API_KEY "..."
# Then edit the plist to propagate into the process env, OR just restart
# the agent — launchd forwards its environment to children.
```

Caveat: `launchctl setenv` persists until reboot only. For reboot-survival,
add to `~/Library/LaunchAgents/setenv.plist` (see Apple docs).

### Option 3 (Linux, production): EnvironmentFile

```bash
sudo tee /etc/arb-agent.env <<'EOF'
POLYMARKET_API_KEY=...
KALSHI_API_KEY=...
ANTHROPIC_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
EOF
sudo chmod 600 /etc/arb-agent.env
sudo chown root:root /etc/arb-agent.env
```

Then uncomment `EnvironmentFile=/etc/arb-agent.env` in
`/etc/systemd/system/arb-agent.service` and run
`sudo systemctl daemon-reload && sudo systemctl restart arb-agent`.

## Cloud deployment (future)

Recommended stack once you're past Phase 3:

- Small VM (AWS Lightsail $10/mo, DigitalOcean droplet, Hetzner $5/mo)
- Ubuntu 22.04 LTS, 1 GB RAM is enough (orchestrator uses <200 MB)
- Use the Linux systemd unit above
- Prometheus + Grafana on the same box, scraping `/metrics`
- Telegram bot for alerts (already wired)
- Postgres instead of SQLite once you're live (see `docs/HANDOFF.md` — switch
  via `get_backend("postgres")` once the implementation body is written)

## What to expect after install

- First minute: `parquet_rollover` events in the log as writers open today's file
- First 2 minutes: USDC + NTP probes fire, populating `clock_drift_seconds`
  and `usdc_price_usd` gauges
- Every 5 minutes: reconcile runs (paper mode checks internal consistency)
- Every 24 hours: UTC midnight Parquet rollover
- If anything trips: `CRITICAL` Telegram alert (if configured), otherwise
  logged as `kill_switch_events` row in SQLite

The orchestrator is silent when healthy. The right way to confirm it's
actually working is `curl http://127.0.0.1:9100/metrics` and check that
counters are advancing.
