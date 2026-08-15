#!/usr/bin/env bash
# Install (or reinstall) the daily landing-ledger + audit LaunchAgent.
#
# Deliberately a separate, operator-run step. Writing a LaunchAgent changes what Devon's machine
# does when nobody is watching, which is not something a build session installs on its own -- and
# THE REPOSITORY HAS NO PRECEDENT TO COPY: the three launchers this one is modelled on
# (run-tracker-projection.sh, run-tracker-reconciliation.sh, run-follow-up-mint.sh) are described
# throughout the repository as scheduled and are scheduled by nothing. There is no LaunchAgent and
# no crontab entry for any of them; every pass any of them has ever made was typed by hand.
#
# Verify afterwards with:
#   launchctl list | grep landing-ledger
#   /bin/bash scripts/run-landing-ledger.sh --dry-run   # writes nothing; prints what it would
#
# Uninstall with:
#   launchctl bootout "gui/$(id -u)/com.devon.landing-ledger"
#   rm ~/Library/LaunchAgents/com.devon.landing-ledger.plist
set -euo pipefail

LABEL="com.devon.landing-ledger"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$REPO_ROOT/.venv/bin/landing-ledger" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/landing-ledger is missing. Run: uv sync --frozen" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$TARGET"

# `bootout` first so a reinstall replaces rather than layering. It fails when nothing is loaded,
# which is the ordinary first-install case, so its status is deliberately ignored.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
launchctl list | grep "$LABEL" || true
