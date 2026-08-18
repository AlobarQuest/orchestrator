#!/usr/bin/env bash
# Install the work-carrier LaunchAgent (ADR-0026, completed by ADR-0027).
#
# A SEPARATE OPERATOR STEP, like every schedule here -- and unlike the version this replaces, the
# installed job WRITES. It passes --register, so each approved change-manager work proposal
# becomes an orchestrator package intake with no person in between. Installing it opens a lane;
# not installing it leaves a lane with no throughput, which is the trade to make deliberately.
#
# WHAT IT CANNOT DO IS THE REASON THIS IS SAFE TO SCHEDULE. A registered intake is a package
# revision, and a package revision cannot become work until a person approves a breakdown and
# then an authority envelope -- both browser-only decisions (ADR-0006, narrowed by ADR-0027 for
# intake alone). So a pass ends at a queue for a human, never at a running change. It also
# registers only what change-manager reports as APPROVED, and only through the SYSTEM bearer,
# which the orchestrator refuses unless the intake names the change record that caused it.
#
# THE JOB READS THE MAIN TREE'S WORKING COPY. `run-work-carrier.sh` resolves its repository root
# from its own path and runs `$REPO_ROOT/.venv/bin/work-carrier`, so merging a change to this
# program alters nothing on this machine until the main tree is pulled. No `uv sync` is needed
# for an edit to an existing module; a NEW dependency does need one.
#
# Usage: scripts/install-work-carrier-launchd.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.devon.work-carrier"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$REPO_ROOT/.venv/bin/work-carrier" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/work-carrier is missing — run 'uv sync' first" >&2
  exit 1
fi
# The carry resolves `orchestrator` from PATH to build each payload. The wrapper puts the venv's
# bin there, so a missing console script is a whole pass of `emitter_not_on_path` refusals --
# clean, and useless. Named here so the failure is caught at install rather than at 07:05.
if [ ! -x "$REPO_ROOT/.venv/bin/orchestrator" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/orchestrator is missing — run 'uv sync' first" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$TARGET"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
echo "  runs at 07:05 local, with --register; log: $HOME/Library/Logs/work-carrier.log"
echo "  each approved change-manager work proposal becomes an orchestrator package intake"
echo "  inspect without writing: $REPO_ROOT/scripts/run-work-carrier.sh"
echo "  one real pass now:       $REPO_ROOT/scripts/run-work-carrier.sh --register"
