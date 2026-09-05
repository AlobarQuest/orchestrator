#!/usr/bin/env bash
# Install (or reinstall) the daily landing-ledger + audit LaunchAgent.
#
# Run by whoever is building the lane. This header used to call it a step for Devon; he corrected
# that on 2026-09-04 -- his role is the decision, installing what he has already decided on is
# mechanics, and he does not install LaunchAgents. What IS worth the care is that it writes a
# plist from REPO_ROOT verbatim, so it must never run from a linked worktree.
#
# WHEN THIS WAS WRITTEN THE REPOSITORY HAD NO PRECEDENT TO COPY: the three launchers this one was
# modelled on were described throughout the repository as scheduled and were scheduled by nothing;
# every pass any of them ever made was typed by hand. That is now resolved in both directions --
# nine lanes have a LaunchAgent, and on 2026-09-05 ADR-0040 deleted two of those three rather than
# scheduling them. The survivor is run-follow-up-mint.sh, still operator-invoked and still the open
# question that ADR names.
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
