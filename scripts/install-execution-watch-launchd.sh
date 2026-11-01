#!/usr/bin/env bash
# Install (or reinstall) the execution-watch LaunchAgent, every fifteen minutes.
#
# THE INSTALLED JOB SENDS ALERTS (--submit) and moves no work unit. Run
# `scripts/run-execution-watch.sh --seed` first: without a known-subjects file the pass refuses and
# raises `execution-watch-unread`.
#
# It refuses to run from a linked worktree: REPO_ROOT is written into the plist verbatim, so a plist
# installed from a torn-down worktree dies on every run with nothing reporting it.
#
# Verify afterwards with:
#   launchctl list | grep execution-watch
#   /bin/bash scripts/run-execution-watch.sh     # bare: reads and prints, sends nothing
#
# Uninstall with:
#   launchctl bootout "gui/$(id -u)/com.devon.execution-watch"
#   rm ~/Library/LaunchAgents/com.devon.execution-watch.plist
set -euo pipefail

LABEL="com.devon.execution-watch"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-install.sh"
sds_refuse_linked_worktree "$REPO_ROOT"

# A console script does not arrive with a `git pull`: `uv sync` installs it.
if [ ! -x "$REPO_ROOT/.venv/bin/execution-watch" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/execution-watch is missing. Run: uv sync --frozen" >&2
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
