#!/usr/bin/env bash
# Install (or reinstall) the daily SDS membership consistency check (ADR-0058), and upsert its Healthchecks
# check.
#
# THE CHECK IS UPSERTED HERE, idempotently: `unique: ["name"]` makes a re-run UPDATE the existing
# check (HTTP 200) to the values below rather than add one (201). Its settings:
#   name      sds-membership-check (the launcher finds its ping URL by this exact name)
#   schedule  45 7 * * *, tz America/New_York (the plist's time, in launchd's local zone)
#   grace     14400 seconds, as for the other nightly SDS lanes. A disagreement pings `/fail`,
#             which alerts at once whatever the grace, so the grace only decides how soon a pass
#             that STOPPED is reported, and how long a started pass may take.
#   channels  "alert-hub" (the alert-hub webhook; "*" would also attach email and deliver every
#             failure twice).
# Neither the API key nor the ping URL is printed: curl reads the key from stdin as its config
# and the response, which carries the ping URL, is discarded for its status code. A failure to
# upsert the check is reported and does not stop the install: the pass still runs and logs, and
# rerunning this retries it.
#
# Run it from the main tree (it refuses a linked worktree).
#
# Verify afterwards with:
#   launchctl list | grep sds-membership-check
#   /bin/bash scripts/run-membership-check.sh
#
# Uninstall with:
#   launchctl bootout "gui/$(id -u)/com.devon.sds-membership-check"
#   rm ~/Library/LaunchAgents/com.devon.sds-membership-check.plist
set -euo pipefail

LABEL="com.devon.sds-membership-check"
CHECK_NAME="sds-membership-check"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

# INSTALL FROM THE MAIN TREE, NEVER FROM A BUILD WORKTREE (scripts/sds-install.sh says why).
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-install.sh"
sds_refuse_linked_worktree "$REPO_ROOT"

if [ ! -x "$REPO_ROOT/.venv/bin/membership-check" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/membership-check is missing. Run: uv sync --frozen" >&2
  exit 1
fi

# The check, before the agent, so it exists when the first pass pings.
create_check() {
  local payload outcome
  payload='{"name": "'"$CHECK_NAME"'", "schedule": "45 7 * * *", "tz": "America/New_York",'
  payload+=' "grace": 14400, "channels": "alert-hub", "unique": ["name"],'
  payload+=' "desc": "orchestrator scripts/run-membership-check.sh: the SDS membership'
  payload+=' consistency check (ADR-0058). /fail carries the report."}'
  if outcome="$(sds_upsert_check "$payload")"; then
    echo "Healthchecks check $CHECK_NAME $outcome"
  else
    echo "WARNING: Healthchecks check $CHECK_NAME was not upserted" >&2
  fi
}
create_check

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$TARGET"

# `bootout` first so a reinstall replaces rather than layering; it fails when nothing is loaded.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
echo "  runs daily at 07:45 local; log: $HOME/Library/Logs/sds-membership-check.log"
echo "  one pass now (it pings): $REPO_ROOT/scripts/run-membership-check.sh"
launchctl list | grep "$LABEL" || true
