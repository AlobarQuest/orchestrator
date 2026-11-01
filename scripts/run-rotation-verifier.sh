#!/usr/bin/env bash
# One rotation-verifier pass: run `/verify` on every submitted rotation unit (ADR-0055 amendment 14).
#
# The orchestrator's verifier completes a rotation unit whose step evidence is clean, and the
# completion hands the next unit its turn. Nothing ran the verifier on a schedule, so each unit
# waited for a session to call it. This lane is that call, and nothing more: the verifier decides
# every criterion, and a unit it can't prove goes to `awaiting_review` for a person.
#
# EXIT CODES:
#   0  every submitted rotation unit completed, or none was submitted.
#   1  the tool itself failed (a missing credential, an unusable URL).
#   2  something was found: a unit went to a person or an alert names it.
#   3  the queue could not be read, or alert-hub could not be told.
#
# A unit the lane couldn't verify is a keyed alert (`src/rotation_verifier/alerts.py`). 2 is the
# finding code, so that cause pings success and raises the alert, never both. 3 pings `/fail`: it is
# what's left when an alert can't carry the news.
#
# A dependency-bump unit is verified too: the lane files the repository's required checks as
# named-check evidence, and reads those from branch protection with the operator's own `gh` login.
#
# Usage:
#   scripts/run-rotation-verifier.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-rotation-verifier-launchd.sh
set -uo pipefail

# BWS UUID (value fetched at runtime; never stored in this repo). See .bws-secrets.toml.
VERIFIER_BEARER_UUID="660d5846-abcb-4751-be86-b483012899eb"   # orchestrator-verifier VERIFIER bearer
SDS_BRIDGE_ALERT_UUID="b415d03b-f92d-46b1-8a9d-b4f10103df49"  # alert-hub sds-bridge sender

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ACTIVATION: these launchers run the working copy, so a merged change is live here only once it
# is pulled. Best-effort: the helper prints one `[activation]` line and returns 0 whatever it finds.
_SDS_ACTIVATE="$HOME/.claude/bin/activate-checkout.sh"
if [ -r "$_SDS_ACTIVATE" ]; then
    # shellcheck source=/dev/null
    . "$_SDS_ACTIVATE"
else
    activate_checkout() {
        echo "[activation] helper missing at $HOME/.claude/bin/activate-checkout.sh —" \
             "this run is not activated"
    }
fi
activate_checkout "$REPO_ROOT" "$0" "$@"

# THE DEAD-MAN SWITCH, armed after activation (which may `exec`). The finding code 2 pings success:
# the check answers whether this lane runs, and a unit waiting for a person is on `/review`.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-rotation-verifier --finding 2 "$@"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

# SOURCED, not executed: `sds-token.sh` exports the value and prints nothing.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/scripts/sds-token.sh"
fi
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  echo "FATAL: BWS_ACCESS_TOKEN not available for the orchestrator credential" >&2
  exit 1
fi

ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-verifier"
ORCHESTRATOR_API_TOKEN="$(sds_bws_value "$VERIFIER_BEARER_UUID")"
export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN
if [ -z "$ORCHESTRATOR_API_TOKEN" ]; then
  echo "FATAL: could not read the verifier bearer from BWS" >&2
  exit 1
fi

# A FAILED FETCH NEVER STOPS A ROTATION: the pass runs without it and exits 3 once it has verified
# everything, so the check says what the alert could not.
SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID" || true)"
export SDS_BRIDGE_ALERT_TOKEN
if [ -z "$SDS_BRIDGE_ALERT_TOKEN" ]; then
  echo "WARNING: could not read the alert-hub sender credential from BWS" >&2
fi

# THE GITHUB CREDENTIAL HAS NO BWS RECORD, exactly as the landing lanes' does not: it is the
# operator's `gh` login, read here and never printed. It only reads branch protection, so a
# missing one fails closed on bump units alone (they are refused) and leaves rotation units be.
if [ -z "${ROTATION_VERIFIER_GITHUB_TOKEN:-}" ]; then
  ROTATION_VERIFIER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
  export ROTATION_VERIFIER_GITHUB_TOKEN
fi

"$REPO_ROOT/.venv/bin/rotation-verifier" "$@"
rc=$?

# A code outside {0,1,2,3} is the program dying in a way it doesn't describe (127 is a missing
# binary); report it as the tool failing rather than folding it to 0.
case "$rc" in
  0|1|2|3) exit "$rc" ;;
  *) echo "FATAL: rotation-verifier exited $rc" >&2; exit 1 ;;
esac
