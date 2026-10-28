#!/usr/bin/env bash
# Install the hourly change-proposer LaunchAgent (ADR-0019 increment 5a).
#
# Run by whoever is building the lane. This header used to say it was "not something a build
# session installs on its own"; Devon corrected that on 2026-09-04 -- his role is the decision, and
# installing what he has already decided on is mechanics. He does not install LaunchAgents.
#
# It still refuses to run from a linked worktree, which is the real hazard: REPO_ROOT is written
# into the plist verbatim, so a plist installed from a torn-down worktree dies every morning with
# nothing reporting it.
#
# UNTIL THIS IS RUN, THE PRODUCER HAS NO CALLER -- and a producer with no caller is this estate's
# most-repeated defect, named in increment 4's own report as the debt this increment pays. It also
# needs the PROPOSE-scoped credential to exist; the wrapper refuses loudly rather than falling back
# to a bearer that could approve its own proposals.
#
# Usage: scripts/install-change-proposer-launchd.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.devon.change-proposer"
CHECK_NAME="sds-change-proposer"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

# Refuses to run from a linked worktree: REPO_ROOT is written into the plist verbatim.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-install.sh"
sds_refuse_linked_worktree "$REPO_ROOT"

if [ ! -x "$REPO_ROOT/.venv/bin/change-proposer" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/change-proposer is missing — run 'uv pip install -e .' first" >&2
  exit 1
fi

# The check, before the agent, so it exists when the first pass pings. `channels` is "alert-hub"
# (the alert-hub webhook; "*" would also attach email and deliver every failure twice). A failure to
# upsert it is reported and does not stop the install: rerunning this retries it.
create_check() {
  local payload outcome
  payload='{"name": "'"$CHECK_NAME"'", "timeout": 3600, "tz": "America/New_York",'
  payload+=' "grace": 1800, "channels": "alert-hub", "unique": ["name"],'
  payload+=' "desc": "orchestrator scripts/run-change-proposer.sh: the change proposer, an outcome alarm'
  payload+=' (ADR-0021 amendment 2). /fail carries the report."}'
  if outcome="$(sds_upsert_check "$payload")"; then
    echo "Healthchecks check $CHECK_NAME $outcome"
  else
    echo "WARNING: Healthchecks check $CHECK_NAME was not upserted" >&2
  fi
}
create_check

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$TARGET"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
echo "  runs hourly; log: $HOME/Library/Logs/change-proposer.log"
echo "  run one pass now with: launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "  or by hand, without writing anything: $REPO_ROOT/scripts/run-change-proposer.sh"
echo "  (bare is a dry run; add --submit to propose)"
