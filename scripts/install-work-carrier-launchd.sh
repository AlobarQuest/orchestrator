#!/usr/bin/env bash
# Install the work-carrier LaunchAgent (ADR-0026).
#
# A SEPARATE OPERATOR STEP, like every schedule here -- but the least consequential of them. The
# pass writes NOTHING, to either system: it reads change-manager's approved work proposals and
# prints the intake payload for each. Installing it cannot cause a change; at worst it produces a
# log nobody reads.
#
# INSTALLING IT IS NOT THE WHOLE LANE, and the missing half is a person. Package intake requires
# an ActorRole.HUMAN actor and human gates are browser-only, permanently (ADR-0006), so what this
# job produces is a payload to paste into /review/intakes/new -- never an intake. ADR-0026
# deliberately left the question of whether a machine may ever register one undecided.
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
echo "  runs at 07:05 local; log: $HOME/Library/Logs/work-carrier.log"
echo "  it writes nothing: each approved record becomes a payload to paste at"
echo "  https://sds.alobar.net/review/intakes/new"
echo "  run one pass now: $REPO_ROOT/scripts/run-work-carrier.sh"
