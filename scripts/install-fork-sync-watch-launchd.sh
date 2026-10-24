#!/usr/bin/env bash
# Install (or reinstall) the hourly fork-sync stuck-lane watcher (ADR-0056 amendment 6), and
# create its Healthchecks check.
#
# THE CHECK IS CREATED HERE, idempotently: `unique: ["name"]` makes a re-run return the existing
# check (HTTP 200) rather than add one (201). Its settings:
#   name      sds-fork-sync-watch (the launcher finds its ping URL by this exact name)
#   schedule  55 * * * *, tz America/New_York (the plist's minute, in launchd's local zone)
#   grace     1800 seconds. A finding pings `/fail`, which alerts at once whatever the grace, so
#             the grace only decides how soon a watcher that STOPPED is reported, and how long a
#             started pass may take. A pass takes about a minute, so half an hour is generous for
#             both and reports a stopped watcher before its next pass would have run.
#   channels  "*" (every integration that exists when the check is created).
# Neither the API key nor the ping URL is printed: curl reads the key from stdin as its config
# and the response, which carries the ping URL, is discarded for its status code. A failure to
# create the check is reported and does not stop the install: the pass still runs and logs, and
# rerunning this creates the check.
#
# A SEPARATE OPERATOR STEP, and deliberately not something a build session runs.
#
# Verify afterwards with:
#   launchctl list | grep fork-sync-watch
#   /bin/bash scripts/run-fork-sync-watch.sh
#
# Uninstall with:
#   launchctl bootout "gui/$(id -u)/com.devon.fork-sync-watch"
#   rm ~/Library/LaunchAgents/com.devon.fork-sync-watch.plist
set -euo pipefail

LABEL="com.devon.fork-sync-watch"
CHECK_NAME="sds-fork-sync-watch"
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

if [ ! -x "$REPO_ROOT/.venv/bin/fork-sync" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/fork-sync is missing. Run: uv sync --frozen" >&2
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
  payload='{"name": "'"$CHECK_NAME"'", "schedule": "55 * * * *", "tz": "America/New_York",'
  payload+=' "grace": 1800, "channels": "*", "unique": ["name"],'
  payload+=' "desc": "orchestrator scripts/run-fork-sync-watch.sh: the fork-sync stuck-lane'
  payload+=' alarm (ADR-0056 amendment 6). /fail carries the report."}'
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
echo "  runs hourly at :55 local; log: $HOME/Library/Logs/fork-sync-watch.log"
echo "  one pass now (it pings): $REPO_ROOT/scripts/run-fork-sync-watch.sh"
launchctl list | grep "$LABEL" || true
