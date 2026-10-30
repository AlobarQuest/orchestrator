#!/usr/bin/env bash
# One bump-driver pass: decompose registered dependency-update intakes, dispatch ready bump units
# and land completed ones, as SYSTEM, with no person acting (Landing Part 4).
#
# It runs no repository code. Its mirrors run only git, and it builds a decomposition with
# `factory decompose --no-execute` (`src/bump_driver/`, `docs/operations/landing-lanes.md`).
#
# THIS CHECK IS AN OUTCOME ALARM. A finding is something a person decides, so every non-zero exit
# pings `/fail` with the report as the body, and a pass that stops pinging goes down after the
# grace (ADR-0021 amendment 2).
#
# EXIT CODES (`bump_driver/cli.py`):
#   0  every bump moved, or waits on something that clears by itself.
#   1  the tool itself failed (a missing credential, an unusable URL).
#   2  a person decides something: a refused decomposition, a declined or superseded proposal, a
#      refused dispatch or merge, or a failed unit.
#   3  a record, a unit or a pull request could not be read, or alert-hub could not be told.
#
# CREDENTIALS, each read straight into its own variable, never an ambient `BWS_ACCESS_TOKEN` (the
# two-identity rule, docs/operations/credentials.md):
#   - the orchestrator SYSTEM bearer (BWS `221a48d5-…`) and the `sds-bridge` alert-hub sender token
#     (BWS `b415d03b-…`), through the narrow `sds-operator` identity; SYSTEM also reads the member
#     record;
#   - the change-manager READ bearer (BWS `314f276d-…`) and the two Brain contributor keys (BWS
#     `750f737f-…`, `da8134b0-…`, project `brains`), through the broad identity;
#   - GitHub: the operator's own `gh` login, which has no BWS record. It reaches git through the
#     mirror's environment, never an argument or `.git/config`.
#
# WHAT `factory` NEEDS. It runs from intent-packages' own venv, so that goes on PATH, with this
# repository's venv for the `orchestrator conformance-claim` it shells out to. The claim reads
# project-standards' compliance matrix, so `FACTORY_PYTHONPATH` names that, and the lane hands it to
# `factory` alone as PYTHONPATH. A claim that can't be produced refuses the decomposition.
#
# Usage:
#   scripts/run-bump-driver.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-bump-driver-launchd.sh
set -uo pipefail

CHECK_NAME="sds-bump-driver"
ORCHESTRATOR_SYSTEM_UUID="221a48d5-3f29-4898-b300-b4820140c880"   # orchestrator-system SYSTEM bearer
CHANGE_MANAGER_READ_UUID="314f276d-55ca-4ddc-a24d-b4a3013508cd"   # change-manager READ bearer
SDS_BRIDGE_ALERT_UUID="b415d03b-f92d-46b1-8a9d-b4f10103df49"      # alert-hub sds-bridge sender
CODE_BRAIN_KEY_UUID="750f737f-4cb6-4876-9a98-b48200ea1c0b"        # CODE_BRAIN_CONTRIBUTOR_KEY
INFRA_BRAIN_KEY_UUID="da8134b0-565f-45c8-8965-b48200ea1c40"       # INFRA_BRAIN_CONTRIBUTOR_KEY

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

_acting=1
for _arg in "$@"; do
  if [ "$_arg" = "--dry-run" ]; then _acting=0; fi
done
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_outcome_begin "$CHECK_NAME" bump-driver "$_acting"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"
NARROW_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
[ -n "$NARROW_IDENTITY" ] || sds_outcome_fail \
    "FATAL: no BWS identity for the orchestrator credential (Keychain Claude/BWS_ACCESS_TOKEN_SDS)"
BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
[ -n "$BROAD_IDENTITY" ] || sds_outcome_fail \
    "FATAL: no BWS identity for the change-manager credential (Keychain Claude/BWS_ACCESS_TOKEN_VPS_BACKUP)"
ORCHESTRATOR_API_TOKEN="$(sds_bws_value "$ORCHESTRATOR_SYSTEM_UUID" "$NARROW_IDENTITY")"
[ -n "$ORCHESTRATOR_API_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the orchestrator system credential from BWS"
SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID" "$NARROW_IDENTITY")"
[ -n "$SDS_BRIDGE_ALERT_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the alert-hub sender credential from BWS"
CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_READ_UUID" "$BROAD_IDENTITY")"
[ -n "$CHANGE_MANAGER_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the change-manager READ credential from BWS"
CODE_BRAIN_KEY="$(sds_bws_value "$CODE_BRAIN_KEY_UUID" "$BROAD_IDENTITY")"
[ -n "$CODE_BRAIN_KEY" ] || sds_outcome_fail \
    "FATAL: could not read the Code Brain key from BWS"
INFRA_BRAIN_KEY="$(sds_bws_value "$INFRA_BRAIN_KEY_UUID" "$BROAD_IDENTITY")"
[ -n "$INFRA_BRAIN_KEY" ] || sds_outcome_fail \
    "FATAL: could not read the Infra Brain key from BWS"
unset NARROW_IDENTITY BROAD_IDENTITY
BUMP_DRIVER_GITHUB_TOKEN="${BUMP_DRIVER_GITHUB_TOKEN:-$(gh auth token 2>/dev/null || true)}"
[ -n "$BUMP_DRIVER_GITHUB_TOKEN" ] || sds_outcome_fail \
    "FATAL: no GitHub credential. Set BUMP_DRIVER_GITHUB_TOKEN, or authenticate gh."

ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-system"
PATH="$REPO_ROOT/.venv/bin:$HOME/Projects/intent-packages/.venv/bin:$PATH"
FACTORY_PYTHONPATH="$HOME/Projects/project-standards/src:$HOME/Projects/security-standards/src"
export PATH FACTORY_PYTHONPATH ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID \
  ORCHESTRATOR_API_TOKEN SDS_BRIDGE_ALERT_TOKEN CHANGE_MANAGER_TOKEN CODE_BRAIN_KEY \
  INFRA_BRAIN_KEY BUMP_DRIVER_GITHUB_TOKEN

"$REPO_ROOT/.venv/bin/bump-driver" "$@" > "$SDS_OUTCOME_REPORT" 2>&1
rc=$?
# A code outside {0,1,2,3} is the program dying in a way it doesn't describe (127 is a missing
# binary); report it as the tool failing rather than folding it to 0.
case "$rc" in
  0|1|2|3) ;;
  *) echo "FATAL: bump-driver exited $rc" >> "$SDS_OUTCOME_REPORT"; rc=1 ;;
esac
sds_outcome_end "$rc"
