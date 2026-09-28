#!/usr/bin/env bash
# Install (or reinstall) the hourly inert-landing LaunchAgent (ADR-0038 part 2a).
#
# A SEPARATE OPERATOR STEP, and deliberately not something a build session runs. Writing a
# LaunchAgent changes what Devon's machine does when nobody is watching.
#
# THE ORCHESTRATOR BOUNDS EVERY PASS, not this schedule. Every term is composed there, and
# `ORCHESTRATOR_INERT_LANDING_ENABLED=false` in its environment refuses every landing (the lane
# defaults ON since ADR-0046; the variable is the off switch).
#
# THE DEAD-MAN CHECK IS A THIRD ACT. `scripts/run-inert-landing.sh` arms `sds-inert-landing`;
# until a Healthchecks check of exactly that name exists, arming logs one line and this lane runs
# unalerted — which is the silence the switch exists to end.
#
# Verify afterwards with:
#   launchctl list | grep inert-landing
#   /bin/bash scripts/run-inert-landing.sh          # reports; asks for nothing
#
# Uninstall with:
#   launchctl bootout "gui/$(id -u)/com.devon.inert-landing"
#   rm ~/Library/LaunchAgents/com.devon.inert-landing.plist
set -euo pipefail

LABEL="com.devon.inert-landing"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

# INSTALL FROM THE MAIN TREE, NEVER FROM A BUILD WORKTREE. `REPO_ROOT` is resolved from this
# script's own location and is written into the plist verbatim, so installing from a worktree
# pins the LaunchAgent to a path that gets deleted at teardown -- after which the job dies every
# hour with nothing reporting it. Building sessions work in worktrees here by convention, so this
# is the likely mistake; the two rev-parse answers differ in a linked worktree and are equal in a
# main tree.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-install.sh"
sds_refuse_linked_worktree "$REPO_ROOT"

# A console script does NOT arrive with a `git pull`: `uv sync` installs it, and a fresh worktree
# may additionally need `uv sync --reinstall-package orchestrator`. Refusing here turns that into
# a message at install time rather than a 127 at :35.
if [ ! -x "$REPO_ROOT/.venv/bin/inert-landing" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/inert-landing is missing. Run: uv sync --frozen" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$TARGET"

# `bootout` first so a reinstall replaces rather than layering. It fails when nothing is loaded,
# which is the ordinary first-install case, so its status is deliberately ignored.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
echo "  runs hourly at :35 local; log: $HOME/Library/Logs/inert-landing.log"
echo "  ORCHESTRATOR_INERT_LANDING_ENABLED=false in production stops every landing"
echo "  report now, without asking for anything: $REPO_ROOT/scripts/run-inert-landing.sh"
launchctl list | grep "$LABEL" || true
