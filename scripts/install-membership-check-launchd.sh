#!/usr/bin/env bash
# Install (or reinstall) the daily SDS membership consistency check (ADR-0058), and create its Healthchecks
# check.
#
# THE CHECK IS CREATED HERE, idempotently: `unique: ["name"]` makes a re-run return the existing
# check (HTTP 200) rather than add one (201). Its settings:
#   name      sds-membership-check (the launcher finds its ping URL by this exact name)
#   schedule  45 7 * * *, tz America/New_York (the plist's time, in launchd's local zone)
#   grace     7200 seconds. A disagreement pings `/fail`, which alerts at once whatever the
#             grace, so the grace only decides how soon a pass that STOPPED is reported, and how
#             long a started pass may take. A pass reads one declaration per repository and takes
#             a few minutes at most; two hours still reports a missed pass the same morning.
#   channels  "*" (every integration that exists when the check is created).
# Neither the API key nor the ping URL is printed: curl reads the key from stdin as its config
# and the response, which carries the ping URL, is discarded for its status code. A failure to
# create the check is reported and does not stop the install: the pass still runs and logs, and
# rerunning this creates the check.
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
HC_API="https://healthcheck.alobar.net/api/v3/checks/"
# The Healthchecks Management API key (read-write). Value fetched at runtime; never stored here.
HC_KEY_UUID="51509d5e-64f5-4781-ae82-b4ed00b644e1"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

# INSTALL FROM THE MAIN TREE, NEVER FROM A BUILD WORKTREE (scripts/sds-install.sh says why).
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-install.sh"
sds_refuse_linked_worktree "$REPO_ROOT"

if [ ! -x "$REPO_ROOT/.venv/bin/sds-members" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/sds-members is missing. Run: uv sync --frozen" >&2
  exit 1
fi

# The check, before the agent, so it exists when the first pass pings.
create_check() {
  local broad key status payload
  broad="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
  if [ -z "$broad" ]; then
    echo "WARNING: no broad BWS identity; Healthchecks check $CHECK_NAME not created" >&2
    return 0
  fi
  # shellcheck disable=SC1091
  source "$REPO_ROOT/scripts/sds-bws.sh"
  key="$(sds_bws_value "$HC_KEY_UUID" "$broad" 2>/dev/null || true)"
  if [ -z "$key" ]; then
    echo "WARNING: the Healthchecks API key could not be read; $CHECK_NAME not created" >&2
    return 0
  fi
  payload='{"name": "'"$CHECK_NAME"'", "schedule": "45 7 * * *", "tz": "America/New_York",'
  payload+=' "grace": 7200, "channels": "*", "unique": ["name"],'
  payload+=' "desc": "orchestrator scripts/run-membership-check.sh: the SDS membership'
  payload+=' consistency check (ADR-0058). /fail carries the report."}'
  status="$(printf 'header = "X-Api-Key: %s"\n' "$key" \
    | curl -sS --max-time 15 --config - -o /dev/null -w '%{http_code}' -X POST "$HC_API" \
      -H "Content-Type: application/json" --data-binary "$payload" 2>/dev/null || true)"
  case "$status" in
    200) echo "Healthchecks check $CHECK_NAME already exists" ;;
    201) echo "Healthchecks check $CHECK_NAME created" ;;
    *) echo "WARNING: creating Healthchecks check $CHECK_NAME answered '${status:-nothing}'" >&2 ;;
  esac
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
