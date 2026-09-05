#!/usr/bin/env bash
# Operator-invoked inbound tracker reconciliation pass (WS-P2.7, Increment 2).
#
# No scheduler and no loop (ADR-0003/0004): one pass, then exit. It reads each bound Todoist
# item's completion state and reports it to the orchestrator, which records append-only
# divergence conditions an operator resolves. It never changes canonical state.
#
# Prerequisites:
#   - `uv pip install -e .` so the `tracker-projection-adapter` entry point exists.
#   - TODOIST_PROJECT_ID is set to the target Todoist project id.
#   - The macOS login Keychain holds BWS_ACCESS_TOKEN_SDS (loaded by sds-token.sh).
#
# Usage:
#   TODOIST_PROJECT_ID=<id> scripts/run-tracker-reconciliation.sh [--dry-run]
set -euo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
SYSTEM_BEARER_UUID="221a48d5-3f29-4898-b300-b4820140c880"   # orchestrator-system SYSTEM bearer
TODOIST_TOKEN_UUID="ff396349-aec1-4250-b2f0-b493015188da"   # Todoist REST API token

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load BWS_ACCESS_TOKEN from the Keychain via the approved helper (never a plaintext file).
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/sds-token.sh"

# `bws secret get <uuid>` returns JSON; extract only the "value" field, never echoing it.
#
# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe,
# which breaks the parse below. This launcher carried the bare form until 2026-09-05 and was the
# last of twelve here to do so; the nine that had been given a dead-man switch had all been fixed
# and these three had not. Measured the same day, same secret, one flag apart: bare output began
# `1b 5b 33 38` (an ANSI escape) and `json.load` died at byte 0, while the guarded form began
# `7b 0a` -- `{`. `--output json` is explicit for the same reason: it is not the default.
#
# The trigger is the environment rather than the bws version, so this fires wherever FORCE_COLOR
# is set -- which is every agent session on this machine, and is how it was found. It costs
# nothing where the behaviour never fires.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

TRACKER_PROJECTION_TOKEN="$(_bws_value "$SYSTEM_BEARER_UUID")"
TODOIST_API_TOKEN="$(_bws_value "$TODOIST_TOKEN_UUID")"
export TRACKER_PROJECTION_TOKEN TODOIST_API_TOKEN

exec "$REPO_ROOT/.venv/bin/tracker-projection-adapter" reconcile \
  --todoist-project-id "${TODOIST_PROJECT_ID:?set TODOIST_PROJECT_ID to the target Todoist project id}" \
  --pass-id "$(date -u +%Y%m%dT%H%M%SZ)" \
  "$@"
