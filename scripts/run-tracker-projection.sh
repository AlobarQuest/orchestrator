#!/usr/bin/env bash
# Operator-invoked outbound tracker projection pass (WS-P2.7, Increment 1).
#
# There is no scheduler and no loop (ADR-0003 / ADR-0002): this runs one projection pass
# and exits. It sources the two runtime secrets from BWS via the approved Keychain helper,
# never writing or echoing any secret value, then execs the adapter's console script.
#
# Prerequisites:
#   - `uv pip install -e .` has been run so the `tracker-projection-adapter` entry point exists.
#   - TODOIST_PROJECT_ID is set to the target Todoist project id.
#   - The macOS login Keychain holds BWS_ACCESS_TOKEN_SDS (loaded by sds-token.sh).
#
# Usage:
#   TODOIST_PROJECT_ID=<id> scripts/run-tracker-projection.sh [--dry-run]
set -euo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
SYSTEM_BEARER_UUID="221a48d5-3f29-4898-b300-b4820140c880"   # orchestrator-system SYSTEM bearer
TODOIST_TOKEN_UUID="ff396349-aec1-4250-b2f0-b493015188da"   # Todoist REST API token

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load BWS_ACCESS_TOKEN from the Keychain via the approved helper (never a plaintext file).
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/sds-token.sh"

# `bws secret get <uuid>` returns JSON; extract only the "value" field, never echoing it.
_bws_value() {
  bws secret get "$1" | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

TRACKER_PROJECTION_TOKEN="$(_bws_value "$SYSTEM_BEARER_UUID")"
TODOIST_API_TOKEN="$(_bws_value "$TODOIST_TOKEN_UUID")"
export TRACKER_PROJECTION_TOKEN TODOIST_API_TOKEN

exec "$REPO_ROOT/.venv/bin/tracker-projection-adapter" project \
  --todoist-project-id "${TODOIST_PROJECT_ID:?set TODOIST_PROJECT_ID to the target Todoist project id}" \
  "$@"
