#!/usr/bin/env bash
# Install (or reinstall) the daily pin-watcher LaunchAgent.
#
# Run by whoever is building the lane. An earlier generation of these installers said this was
# "not something a build session installs on its own"; Devon corrected that on 2026-09-04 -- his
# role is the decision, and installing what he has already decided on is mechanics. The decision
# here is that this lane should run on a clock; the `launchctl` call is not a second decision.
#
# It still refuses to run from a linked worktree, which is the real hazard: REPO_ROOT is written
# into the plist verbatim, so a plist installed from a torn-down worktree dies every morning with
# nothing reporting it.
#
# Verify afterwards with:
#   launchctl list | grep pin-watcher
#   /bin/bash scripts/run-pin-watcher.sh --dry-run   # writes nothing; prints what it would
#
# Uninstall with:
#   launchctl bootout "gui/$(id -u)/com.devon.pin-watcher"
#   rm ~/Library/LaunchAgents/com.devon.pin-watcher.plist
set -euo pipefail

LABEL="com.devon.pin-watcher"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

# INSTALL FROM THE MAIN TREE, NEVER FROM A BUILD WORKTREE. `REPO_ROOT` is resolved from this
# script's own location and is written into the plist verbatim, so installing from a worktree
# pins the LaunchAgent to a path that gets deleted at teardown -- after which the job dies every
# morning with nothing reporting it, which is the exact failure class the pin chain exists to catch.
# Building sessions work in worktrees here by convention, so this is the likely mistake.
if [ "$(git -C "$REPO_ROOT" rev-parse --git-dir)" != \
     "$(git -C "$REPO_ROOT" rev-parse --git-common-dir)" ]; then
  echo "FATAL: $REPO_ROOT is a linked worktree. Install from the main tree." >&2
  exit 1
fi

# A console script does NOT arrive with a `git pull`: `uv sync` installs it, and a fresh worktree
# may additionally need `uv sync --reinstall-package orchestrator`. Refusing here turns that into
# a message at install time rather than a 127 at 07:10.
if [ ! -x "$REPO_ROOT/.venv/bin/pin-watcher" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/pin-watcher is missing. Run: uv sync --frozen" >&2
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
