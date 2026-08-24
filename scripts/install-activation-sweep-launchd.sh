#!/usr/bin/env bash
# Install (or reinstall) the daily machine-activation sweep LaunchAgent (ADR-0030).
#
# Deliberately a separate, operator-run step. Writing a LaunchAgent changes what Devon's machine
# does when nobody is watching, which is not something a build session installs on its own.
#
# Verify afterwards with:
#   launchctl list | grep activation-sweep
#   /bin/bash scripts/run-activation-sweep.sh --dry-run   # writes nothing; prints what it would
#
# Uninstall with:
#   launchctl bootout "gui/$(id -u)/com.devon.activation-sweep"
#   rm ~/Library/LaunchAgents/com.devon.activation-sweep.plist
set -euo pipefail

LABEL="com.devon.activation-sweep"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

# A console script does NOT arrive with a `git pull`: `uv sync` installs it, and a fresh worktree
# may additionally need `uv sync --reinstall-package orchestrator`. Refusing here turns that into
# a message at install time rather than a 127 at 07:10.
if [ ! -x "$REPO_ROOT/.venv/bin/activation-sweep" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/activation-sweep is missing. Run: uv sync --frozen" >&2
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
