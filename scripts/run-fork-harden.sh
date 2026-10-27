#!/usr/bin/env bash
# One fork-hardening pass (ADR-0057): `fork-harden run --push`, which rebuilds each fork's
# `hardened` branch from its landed `main` with every hardening rule's fix applied, and moves the
# branch only when every rule's check passes on the result.
#
# DAILY AT 03:00, BEFORE THE TOOL INSTALLER'S 03:15. The order is a convenience, not a safety
# property: if this pass hasn't run, or refused, `hardened` hasn't moved, and the installer has
# nothing new to install.
#
# THIS CHECK IS AN OUTCOME ALARM, AS `sds-fork-sync-watch` IS. A held fork is a version Devon
# isn't getting, so every non-zero exit pings `/fail` with the report as the body. A pass that
# stops pinging goes down after the grace, so while the check is up it is a liveness check too
# (ADR-0021 amendment 2). The shared helper's outcome-alarm functions run the pass; its arming is
# not used, because it pings success on a lane's finding code. A success ping carries the report
# too, so a rule upstream has fixed ("can retire") is visible in the check's log.
#
# EXIT CODES, the whole interface a scheduled run has (`fork_harden/cli.py`):
#   0  no fork is held.
#   1  the tool itself failed.
#   2  a fork could not be read or pushed, a `FORKS` entry the member record doesn't grant was
#      skipped, or the grants could not be read; and none is held.
#   3  a fork is held: its `hardened` did not move.
# 1, 2 and 3 all ping `/fail`. The program's code is this script's code.
#
# CREDENTIALS. GitHub is read and written with the operator's own git credentials
# (`gh auth git-credential`). The Healthchecks API key (BWS `51509d5e-…`) is read through the broad
# machine account only to find the ping URL, and that read never gates the pass. The observer
# bearer (BWS `f793576f-…`) is read through the narrow `sds-operator` identity, from its own
# Keychain item and its own variable, never an ambient `BWS_ACCESS_TOKEN` (the two-identity rule,
# docs/operations/credentials.md).
#
# SCOPE. Which forks to harden comes from the SDS member record: `sds_scope granted
# security_patches`. A failed grant read exits 2 without hardening and pings `/fail`; a missing
# narrow identity or an empty BWS read is the tool failing and exits 1. A fork in `rules.FORKS` the
# record doesn't grant is skipped and exits 2 (`fork_harden/cli.py`).
#
# Usage:
#   scripts/run-fork-harden.sh
# Install as a scheduled job with:
#   scripts/install-fork-harden-launchd.sh
set -uo pipefail

CHECK_NAME="sds-fork-hardening"
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER bearer

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ACTIVATION, as every lane does it: best-effort, one `[activation]` line, re-execs on a moved HEAD.
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

# The ping URL, resolved by exact check name after activation (an `exec` there would lose it).
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
# The grants are read before anything is hardened. Every failure before the program runs is
# reported through the same check as a held fork, with the reason as the ping body
# (`sds_outcome_fail`).
sds_outcome_begin "$CHECK_NAME" fork-harden

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"
NARROW_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
[ -n "$NARROW_IDENTITY" ] || sds_outcome_fail \
    "FATAL: no BWS identity for the observer credential (Keychain Claude/BWS_ACCESS_TOKEN_SDS)"
OBSERVER_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID" "$NARROW_IDENTITY")"
[ -n "$OBSERVER_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the orchestrator observer credential from BWS"
GRANTED="$(cd "$REPO_ROOT" && SDS_SCOPE_ORCHESTRATOR_TOKEN="$OBSERVER_TOKEN" \
    "$REPO_ROOT/.venv/bin/python" -m scripts.sds_scope granted security_patches \
    2>"$SDS_OUTCOME_REPORT")"
scope_rc=$?
if [ "$scope_rc" -ne 0 ]; then
    reason="the forks granted security patches could not be read (sds_scope exited $scope_rc)"
    sds_outcome_fail "$reason: nothing was hardened. $(cat "$SDS_OUTCOME_REPORT")" 2
fi
GRANT_ARGS=()
while IFS= read -r repository; do
    [ -n "$repository" ] && GRANT_ARGS+=(--granted "$repository")
done <<< "$GRANTED"

"$REPO_ROOT/.venv/bin/fork-harden" run --push ${GRANT_ARGS[@]+"${GRANT_ARGS[@]}"} \
    > "$SDS_OUTCOME_REPORT" 2>&1
sds_outcome_end "$?" --body-on-success
