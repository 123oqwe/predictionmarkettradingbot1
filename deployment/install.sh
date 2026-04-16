#!/usr/bin/env bash
# One-shot installer. Detects macOS vs Linux, customizes the unit file
# with this repo's path, drops it into the right location, loads it.
#
#   Usage:  ./deployment/install.sh
#   Uninstall: ./deployment/install.sh --uninstall
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNINSTALL="${1:-}"

mkdir -p "$REPO_ROOT/logs"

if [[ "$(uname)" == "Darwin" ]]; then
    PLIST_SRC="$REPO_ROOT/deployment/com.arbagent.orchestrator.plist"
    PLIST_DST="$HOME/Library/LaunchAgents/com.arbagent.orchestrator.plist"

    if [[ "$UNINSTALL" == "--uninstall" ]]; then
        [[ -f "$PLIST_DST" ]] && launchctl unload "$PLIST_DST" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "Uninstalled macOS launchd unit."
        exit 0
    fi

    # Create the customized plist in the destination.
    mkdir -p "$HOME/Library/LaunchAgents"
    sed "s|REPLACE_REPO_PATH|$REPO_ROOT|g" "$PLIST_SRC" > "$PLIST_DST"
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    launchctl load "$PLIST_DST"
    echo "Installed launchd unit. Verify:"
    echo "  launchctl list | grep arbagent"
    echo "  curl http://127.0.0.1:9100/health"
    exit 0
fi

if [[ "$(uname)" == "Linux" ]]; then
    UNIT_SRC="$REPO_ROOT/deployment/arb-agent.service"
    UNIT_DST="/etc/systemd/system/arb-agent.service"

    if [[ "$UNINSTALL" == "--uninstall" ]]; then
        sudo systemctl stop arb-agent 2>/dev/null || true
        sudo systemctl disable arb-agent 2>/dev/null || true
        sudo rm -f "$UNIT_DST"
        sudo systemctl daemon-reload
        echo "Uninstalled systemd unit."
        exit 0
    fi

    sed -e "s|REPLACE_REPO_PATH|$REPO_ROOT|g" \
        -e "s|REPLACE_USER|$USER|g" "$UNIT_SRC" | sudo tee "$UNIT_DST" > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable arb-agent
    sudo systemctl restart arb-agent
    echo "Installed systemd unit. Verify:"
    echo "  sudo systemctl status arb-agent"
    echo "  curl http://127.0.0.1:9100/health"
    exit 0
fi

echo "Unsupported OS: $(uname)"
exit 1
