#!/usr/bin/env bash
# One rotation-decomposer pass: propose the decomposition of each carried rotation that has none
# (ADR-0055 amendment 15, increment 5).
#
# After Devon approves a rotation's change record and the work carrier registers its intake, the
# rotation needs a decomposition. This lane generates it from the intake's own criteria and the
# type's derived plan, and proposes it as SYSTEM; a person approves it on /review. It never
# decides one, and never proposes over a proposal a person rejected or sent back.
#
# THIS CHECK IS AN OUTCOME ALARM. A finding is something a person decides (a package that can't
# be decomposed by machine, a declined proposal), so every non-zero exit pings `/fail` with the
# report as the body, and a pass that stops pinging goes down after the grace (ADR-0021
# amendment 2).
#
# EXIT CODES (`rotation_decomposer/cli.py`):
#   0  every carried rotation has a decomposition, or none waits.
#   1  the tool itself failed (a missing credential, an unusable URL).
#   2  a person decides something: a package that can't be decomposed by machine, or a proposal a
#      person rejected or sent back.
#   3  a record or the registry could not be read.
#
# CREDENTIALS, three secrets, each read into its own variable:
#   - the orchestrator SYSTEM bearer (BWS `221a48d5-…`), on every pass: a dry run reads the same
#     intakes and proposals through it;
#   - the `sds-bridge` alert-hub sender token (BWS `b415d03b-…`), on an acting pass only: a dry run
#     sends nothing. A proposal waiting on a person for 48 hours alerts; a failed send prints to the
#     report and never changes the exit code;
#   - the change-manager READ bearer (BWS `314f276d-…`), on every pass.
#
# TWO HOSTS, ONE LAUNCHER (ADR-0059 increment 4). A set `BWS_ACCESS_TOKEN` means the lane's
# container: `lane-run` passes the lane's own token as its one secret, and all three secrets are
# read with it, in the environment, never as a process argument, and no Keychain item is read.
# Without it this is the Mac's launchd job, unchanged until increment 6: the SYSTEM bearer and the
# sender token through the narrow `sds-operator` identity and the READ bearer through the broad one,
# each identity read straight from its own Keychain item into its own variable, never an ambient
# `BWS_ACCESS_TOKEN` (the two-identity rule, docs/operations/credentials.md). Either way the BWS
# token is unset before the program runs, which needs none. No GitHub credential on either host:
# the program calls no GitHub API, and its registry mirrors fetch the four public registry
# repositories anonymously (`rotation_worker/git_mirror.py`, ADR-0059 decision 1). In the container
# they sit under `lane-run`'s new `HOME`, so each pass refetches them. The container runs the
# image's own code, so activation is the Mac's alone.
#
# Usage:
#   scripts/run-rotation-decomposer.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-rotation-decomposer-launchd.sh
set -uo pipefail

CHECK_NAME="sds-rotation-decomposer"
ORCHESTRATOR_SYSTEM_UUID="221a48d5-3f29-4898-b300-b4820140c880"   # orchestrator-system SYSTEM bearer
CHANGE_MANAGER_READ_UUID="314f276d-55ca-4ddc-a24d-b4a3013508cd"   # change-manager READ bearer
SDS_BRIDGE_ALERT_UUID="b415d03b-f92d-46b1-8a9d-b4f10103df49"       # sds-bridge alert-hub sender token

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ACTIVATION: these launchers run the working copy, so a merged change is live here only once it
# is pulled. Best-effort: the helper prints one `[activation]` line and returns 0 whatever it finds.
# Skipped in the container, which has no `activate-checkout.sh`, rather than logged as missing.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
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
fi

_acting=1
for _arg in "$@"; do
  if [ "$_arg" = "--dry-run" ]; then _acting=0; fi
done
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_outcome_begin "$CHECK_NAME" rotation-decomposer "$_acting"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"
if [ -n "${BWS_ACCESS_TOKEN:-}" ]; then
  # THE CONTAINER. Each read takes the lane's one token from the environment (`sds_bws_value` with
  # one argument), so it never appears in a process argument.
  _narrow_secret() { sds_bws_value "$1"; }
  _broad_secret() { sds_bws_value "$1"; }
  FROM=" (the lane's token)"
else
  NARROW_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
  [ -n "$NARROW_IDENTITY" ] || sds_outcome_fail \
      "FATAL: no BWS identity for the orchestrator credential (Keychain Claude/BWS_ACCESS_TOKEN_SDS)"
  BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
  [ -n "$BROAD_IDENTITY" ] || sds_outcome_fail \
      "FATAL: no BWS identity for the change-manager credential (Keychain Claude/BWS_ACCESS_TOKEN_VPS_BACKUP)"
  _narrow_secret() { sds_bws_value "$1" "$NARROW_IDENTITY"; }
  _broad_secret() { sds_bws_value "$1" "$BROAD_IDENTITY"; }
  FROM=""
fi
CHANGE_MANAGER_TOKEN="$(_broad_secret "$CHANGE_MANAGER_READ_UUID")"
[ -n "$CHANGE_MANAGER_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the change-manager READ credential from BWS$FROM"
ORCHESTRATOR_API_TOKEN="$(_narrow_secret "$ORCHESTRATOR_SYSTEM_UUID")"
[ -n "$ORCHESTRATOR_API_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the orchestrator system credential from BWS$FROM"
if [ "$_acting" -eq 1 ]; then
  SDS_BRIDGE_ALERT_TOKEN="$(_narrow_secret "$SDS_BRIDGE_ALERT_UUID")"
  [ -n "$SDS_BRIDGE_ALERT_TOKEN" ] || sds_outcome_fail \
      "FATAL: could not read the sds-bridge alert sender token from BWS$FROM"
  export SDS_BRIDGE_ALERT_TOKEN
fi
unset NARROW_IDENTITY BROAD_IDENTITY
# The program needs no BWS identity, so the lane's token never reaches it.
unset BWS_ACCESS_TOKEN
ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-system"
export CHANGE_MANAGER_TOKEN ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN

"$REPO_ROOT/.venv/bin/rotation-decomposer" "$@" > "$SDS_OUTCOME_REPORT" 2>&1
rc=$?
# A code outside {0,1,2,3} is the program dying in a way it doesn't describe (127 is a missing
# binary); report it as the tool failing rather than folding it to 0.
case "$rc" in
  0|1|2|3) ;;
  *) echo "FATAL: rotation-decomposer exited $rc" >> "$SDS_OUTCOME_REPORT"; rc=1 ;;
esac
sds_outcome_end "$rc"
