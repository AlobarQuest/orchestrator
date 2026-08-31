#!/usr/bin/env bash
# Install (or reinstall) the hourly inert-landing LaunchAgent (ADR-0038 part 2a).
#
# A SEPARATE OPERATOR STEP, and deliberately not something a build session runs. Writing a
# LaunchAgent changes what Devon's machine does when nobody is watching.
#
# INSTALLING IT IS NOT ENOUGH, and that is by design. The orchestrator refuses every landing until
# `ORCHESTRATOR_INERT_LANDING_ENABLED` is true in its environment, and it does not serve this
# lane's routes at all until the release that carries them. So this schedule is inert on its own:
# the job runs, asks, cannot be answered, and reports. Two separate acts, in two separate systems.
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
if [ "$(git -C "$REPO_ROOT" rev-parse --git-dir)" != \
     "$(git -C "$REPO_ROOT" rev-parse --git-common-dir)" ]; then
  echo "FATAL: $REPO_ROOT is a linked worktree. Install from the main tree." >&2
  exit 1
fi

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
echo "  it lands NOTHING until ORCHESTRATOR_INERT_LANDING_ENABLED is true in production"
echo "  and nothing at all until production serves /api/v1/inert-pr-merge-admission"
echo "  report now, without asking for anything: $REPO_ROOT/scripts/run-inert-landing.sh"
launchctl list | grep "$LABEL" || true
