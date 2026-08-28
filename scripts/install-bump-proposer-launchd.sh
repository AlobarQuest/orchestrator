#!/usr/bin/env bash
# Install (or reinstall) the daily bump-proposer LaunchAgent (ADR-0028).
#
# A SEPARATE OPERATOR STEP, like every schedule here -- and the installed job WRITES more than
# any sibling does. It passes --submit, so each pass may mint a package revision in
# intent-packages' tamper-evident chain, commit it to that checkout, and propose a work record.
# Installing it opens the head of the work lane; leaving it uninstalled leaves a producer nothing
# invokes, which is the state this replaces.
#
# WHAT IT CANNOT DO IS WHY THIS IS SAFE TO SCHEDULE. The bearer is propose-scoped, so the job
# cannot approve the record it writes -- that is the gate ADR-0028 keeps. It proposes only bumps
# the transcribed auto-merge cascade REFUSES, reading that gate rather than re-deciding it, and
# only for repositories a standing package already targets. A pass ends at a queue for a human.
#
# IT COMMITS, AND IT DOES NOT PUSH. The pass leaves ~/Projects/intent-packages one commit ahead
# of its upstream; a person pushes it. Nothing waits on that push -- the 07:05 carry reads the
# checkout's working files -- but until it happens the revision has had no CI run. The activation
# sweep carries the number as `ahead_by` in every row and does not classify it.
#
# THE JOB READS THE MAIN TREE'S WORKING COPY. `run-bump-proposer.sh` resolves its repository root
# from its own path and runs `$REPO_ROOT/.venv/bin/bump-proposer`, so merging a change to that
# program alters nothing on this machine until the main tree is pulled. An edit to an existing
# module needs no `uv sync`; a new console script or dependency does.
#
# Verify afterwards with:
#   launchctl list | grep bump-proposer
#   scripts/run-bump-proposer.sh          # dry run: reports, and cannot write
#
# A DRY RUN STILL NEEDS TWO CREDENTIALS, and the wrapper's own comment is the precise one: what
# `--submit` gates is "the credential that could write". A bare run reads the Keychain bootstrap
# token and a GitHub token unconditionally and exits 1 without either, so an operator running
# this check on a machine where `gh` is not logged in gets a FATAL that is about their login and
# not about this installation. Only the propose-scoped change-manager bearer is withheld.
#
# Uninstall with:
#   launchctl bootout "gui/$(id -u)/com.devon.bump-proposer"
#   rm ~/Library/LaunchAgents/com.devon.bump-proposer.plist
#
# Usage: scripts/install-bump-proposer-launchd.sh
set -euo pipefail

LABEL="com.devon.bump-proposer"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

# INSTALL FROM THE MAIN TREE, NEVER FROM A BUILD WORKTREE. `REPO_ROOT` is resolved from this
# script's own location and is written into the plist verbatim, so installing from a worktree
# pins the LaunchAgent to a path that gets deleted at teardown -- after which the job dies every
# morning with nothing reporting it. Building sessions work in worktrees here by convention, so
# this is the likely mistake. `--git-dir` and `--git-common-dir` differ in a linked worktree and
# are equal in a main tree, measured both ways.
if [ "$(git -C "$REPO_ROOT" rev-parse --git-dir)" != \
     "$(git -C "$REPO_ROOT" rev-parse --git-common-dir)" ]; then
  echo "FATAL: $REPO_ROOT is a linked worktree. Install from the main tree." >&2
  exit 1
fi

# A console script does NOT arrive with a `git pull`: `uv sync` installs it, and a fresh worktree
# may additionally need `uv sync --reinstall-package orchestrator`. Refusing here turns that into
# a message at install time rather than a 127 at 06:50.
if [ ! -x "$REPO_ROOT/.venv/bin/bump-proposer" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/bump-proposer is missing. Run: uv sync --frozen" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$TARGET"

# THE JOB DEPENDS ON A SECOND TREE, and until this block existed nothing checked it. The pass
# runs `$BUMP_PROPOSER_PACKAGES_CHECKOUT/.venv/bin/python` for every intent-packages lifecycle
# command and reads `packages/` there to find the standing packages at all. A checkout that was
# cloned but never synced installs fine, dry-runs fine, and exits 0 on every day there is
# nothing to do -- surfacing only on the first day there IS a bump, as a per-pull-request
# `error` in a log launchd discards. That is the failure class the block above exists to move
# to install time, so it is moved for this dependency too.
#
# The path is READ BACK OUT OF THE PLIST just written rather than restated here: the plist is
# where the answer lives, and a second copy is a second thing to forget.
PACKAGES_CHECKOUT="$(plutil -extract EnvironmentVariables.BUMP_PROPOSER_PACKAGES_CHECKOUT raw \
  -o - "$TARGET")"
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

# `bootout` first so a reinstall replaces rather than layering. It fails when nothing is loaded,
# which is the ordinary first-install case, so its status is deliberately ignored.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
echo "  runs at 06:50 local, with --submit; log: $HOME/Library/Logs/bump-proposer.log"
echo "  a cascade-refused bump becomes a proposed work record a person then approves"
echo "  inspect without writing: $REPO_ROOT/scripts/run-bump-proposer.sh"
echo "  one real pass now:       $REPO_ROOT/scripts/run-bump-proposer.sh --submit"
launchctl list | grep "$LABEL" || true
