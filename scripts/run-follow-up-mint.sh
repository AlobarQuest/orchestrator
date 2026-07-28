#!/usr/bin/env bash
# Operator-invoked follow-up minting pass (WS-P2.8).
#
# No scheduler and no loop (ADR-0002/0003/0007): one pass, then exit. It mints the work units
# whose package-declared follow-up reviews have come due and prints a counted summary. It never
# changes any other unit's state.
#
# The credential is orchestrator-system. It must NOT be orchestrator-drift-reporter: that
# identity's registry profile is observe-and-propose, minting a work unit is canonical mutation,
# and agent_id attribution is permanent.
#
# Prerequisites:
#   - `uv pip install -e .` so the `orchestrator` entry point exists.
#   - The macOS login Keychain holds BWS_ACCESS_TOKEN_VPS_BACKUP (loaded by bws-token.sh).
#
# Usage:
#   scripts/run-follow-up-mint.sh [--json]
set -euo pipefail

# BWS UUID (value fetched at runtime; never stored in this repo). See .bws-secrets.toml.
SYSTEM_BEARER_UUID="221a48d5-3f29-4898-b300-b4820140c880"   # orchestrator-system SYSTEM bearer

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck disable=SC1091
source "$HOME/Projects/vps-backup/bws-token.sh"

# `bws secret get <uuid>` returns JSON; extract only the "value" field, never echoing it.
_bws_value() {
  bws secret get "$1" | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

ORCHESTRATOR_API_TOKEN="$(_bws_value "$SYSTEM_BEARER_UUID")"
ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-system"
export ORCHESTRATOR_API_TOKEN ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID

exec "$REPO_ROOT/.venv/bin/orchestrator" mint-follow-ups \
  --idempotency-key "follow-up-mint:$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "$@"
