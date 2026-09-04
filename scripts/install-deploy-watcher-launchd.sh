#!/usr/bin/env bash
# Install the hourly rollout-watcher LaunchAgent (ADR-0019 increment 2).
#
# Run by whoever is building the lane. This header used to say it was "not something a build
# session installs on its own"; Devon corrected that on 2026-09-04 -- his role is the decision, and
# installing what he has already decided on is mechanics. He does not install LaunchAgents.
#
# It still refuses to run from a linked worktree, which is the real hazard: REPO_ROOT is written
# into the plist verbatim, so a plist installed from a torn-down worktree dies every morning with
# nothing reporting it.
#
# UNTIL THIS IS RUN, THE WATCHER HAS NO CALLER -- and a detector with no caller is this estate's
# most-repeated defect. The increment's own justification is "without it a failed rollout is
# invisible", and that stays true of a watcher nothing invokes. The tool is proven against real
# GitHub either way; what this adds is that it runs when nobody types.
#
# Usage: scripts/install-deploy-watcher-launchd.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.devon.deploy-watcher"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$REPO_ROOT/.venv/bin/deploy-watcher" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/deploy-watcher is missing — run 'uv pip install -e .' first" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$TARGET"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
echo "  runs hourly; log: $HOME/Library/Logs/deploy-watcher.log"
echo "  run one pass now with: launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "  or by hand, without writing anything: $REPO_ROOT/scripts/run-deploy-watcher.sh --dry-run"
