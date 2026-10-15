#!/usr/bin/env bash
# Install (or reinstall) the daily rotation-proposer LaunchAgent (ADR-0054 amendment 1).
#
# THE INSTALLED JOB WRITES. It passes --submit, so a pass may revise a standing rotation package,
# take it to `ready_for_review`, commit and publish it to intent-packages, and, once a revision is
# approved, propose a `work` change record.
#
# WHAT IT CANNOT DO IS WHY THIS IS SAFE TO SCHEDULE. It never approves the revision: no approval
# policy grants this profile, so Devon approves it by name. It never approves the record either:
# its change-manager bearer is propose-scoped, so a click approves it. A pass ends at a queue for
# a person, never at a rotation.
#
# 06:30, TWENTY MINUTES BEFORE bump-proposer's 06:50. Both lanes commit to the same intent-packages
# checkout, and the work carrier (07:05) and activation sweep (07:10) read it, so this pass runs
# clear of all three. The plist template states the measurement behind the gap.
#
# THE JOB READS THE MAIN TREE'S WORKING COPY. `run-rotation-proposer.sh` resolves its repository
# root from its own path and runs `$REPO_ROOT/.venv/bin/rotation-proposer`, so merging a change to
# that program alters nothing on this machine until the main tree is pulled. An edit to an existing
# module needs no `uv sync`; a new module, console script or dependency does.
#
# Verify afterwards with:
#   launchctl list | grep rotation-proposer
#   scripts/run-rotation-proposer.sh          # dry run: reads infraops, writes nothing
#
# Uninstall with:
#   launchctl bootout "gui/$(id -u)/com.devon.rotation-proposer"
#   rm ~/Library/LaunchAgents/com.devon.rotation-proposer.plist
#
# Usage: scripts/install-rotation-proposer-launchd.sh
set -euo pipefail

LABEL="com.devon.rotation-proposer"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

# Refuses to run from a linked worktree: REPO_ROOT is written into the plist verbatim.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-install.sh"
sds_refuse_linked_worktree "$REPO_ROOT"

if [ ! -x "$REPO_ROOT/.venv/bin/rotation-proposer" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/rotation-proposer is missing. Run: uv sync --frozen" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$TARGET"

# The two trees the pass depends on, read back out of the plist just written. Each one missing
# installs fine and then exits 2 every morning.
PACKAGES_CHECKOUT="$(plutil -extract EnvironmentVariables.BUMP_PROPOSER_PACKAGES_CHECKOUT raw \
  -o - "$TARGET")"
INFRAOPS_CHECKOUT="$(plutil -extract EnvironmentVariables.ROTATION_PROPOSER_INFRAOPS_CHECKOUT raw \
  -o - "$TARGET")"
if [ ! -x "$PACKAGES_CHECKOUT/.venv/bin/python" ]; then
  echo "FATAL: $PACKAGES_CHECKOUT/.venv/bin/python is missing. Every intent-packages lifecycle" \
       "command runs through it. Run: uv sync --frozen in $PACKAGES_CHECKOUT" >&2
  exit 1
fi
if [ ! -f "$INFRAOPS_CHECKOUT/dist/cli/security-drift-cli.js" ]; then
  echo "FATAL: $INFRAOPS_CHECKOUT/dist/cli/security-drift-cli.js is missing. Build that checkout." >&2
  exit 1
fi

# `bootout` first so a reinstall replaces rather than layering. It fails when nothing is loaded,
# which is the ordinary first-install case, so its status is deliberately ignored.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
echo "  runs at 06:30 local, with --submit; log: $HOME/Library/Logs/rotation-proposer.log"
echo "  inspect without writing: $REPO_ROOT/scripts/run-rotation-proposer.sh"
launchctl list | grep "$LABEL" || true
