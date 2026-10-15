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
# A BARE RUN STILL PINGS THE DEAD-MAN CHECK. `sds_deadman_is_dry_run` recognises only `--dry-run`,
# so a hand-run dry run resets `sds-rotation-proposer`'s timer as a scheduled pass would. To learn
# whether the schedule is running, read the log's modification time, not the check.
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

# RENDERED TO A TEMPORARY FILE, CHECKED, AND ONLY THEN MOVED INTO PLACE. launchd loads every plist
# in ~/Library/LaunchAgents at login, so a plist left there by an install that failed a check
# below would still start writing passes.
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$RENDERED"

# The two trees the pass depends on, read back out of the rendered plist. A missing or stale one
# installs fine and then exits 2 every morning, so each is checked here instead.
plist_value() {
  plutil -extract "EnvironmentVariables.$1" raw -o - "$RENDERED"
}
PACKAGES_CHECKOUT="$(plist_value BUMP_PROPOSER_PACKAGES_CHECKOUT)"
INFRAOPS_CHECKOUT="$(plist_value ROTATION_PROPOSER_INFRAOPS_CHECKOUT)"
if [ ! -d "$PACKAGES_CHECKOUT/packages" ]; then
  echo "FATAL: $PACKAGES_CHECKOUT/packages is missing. The pass reads the standing packages" \
       "there; check BUMP_PROPOSER_PACKAGES_CHECKOUT in $TEMPLATE." >&2
  exit 1
fi
if [ ! -x "$PACKAGES_CHECKOUT/.venv/bin/python" ]; then
  echo "FATAL: $PACKAGES_CHECKOUT/.venv/bin/python is missing. Every intent-packages lifecycle" \
       "command runs through it. Run: uv sync --frozen in $PACKAGES_CHECKOUT" >&2
  exit 1
fi
# The pass's own first read, run with the job's PATH: it fails on a missing `node`, an unbuilt
# checkout, and a build without `cred-findings`. It reads and writes nothing else, and exits 0
# when it reports a credential due.
if ! env -i PATH="$(plist_value PATH)" HOME="$HOME" \
    node "$INFRAOPS_CHECKOUT/dist/cli/security-drift-cli.js" cred-findings > /dev/null; then
  echo "FATAL: infraops' cred-findings did not answer from $INFRAOPS_CHECKOUT with the job's" \
       "PATH. Pull and build that checkout." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
mv "$RENDERED" "$TARGET"

# `bootout` first so a reinstall replaces rather than layering. It fails when nothing is loaded,
# which is the ordinary first-install case, so its status is deliberately ignored.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
echo "  runs at 06:30 local, with --submit; log: $HOME/Library/Logs/rotation-proposer.log"
echo "  dry run (pings the check): $REPO_ROOT/scripts/run-rotation-proposer.sh"
launchctl list | grep "$LABEL" || true
