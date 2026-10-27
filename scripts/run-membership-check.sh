#!/usr/bin/env bash
# One consistency pass over the SDS member record: `membership-check` compares the record with
# every repository's `factory-target.toml`, change-manager's inert landing population, and the
# configuration lists the rollout, tool, hardening, fork-sync and rotation lanes carry (ADR-0058).
#
# DAILY AT 07:45, AFTER THE LANDING LEDGER (07:30). The lanes take their scope from the record;
# this is what notices that a list somewhere still says something else.
#
# THIS CHECK IS AN OUTCOME ALARM, AS `sds-fork-sync-watch` AND `sds-fork-hardening` ARE. A
# disagreement is a lane about to act on a population the record doesn't hold, so every non-zero
# exit pings `/fail` with the report as the body. A pass that stops pinging goes down after the
# grace, so it is a liveness check too. Only the shared helper's lookup of the ping URL by check
# name is reused; its arming is not, because it pings success on a lane's finding code.
#
# EXIT CODES, the whole interface a scheduled run has (`membership_check/cli.py`):
#   0  the record and every list agree.
#   1  the tool itself failed (a missing credential, an unhandled error).
#   2  at least one disagreement. Disagreements outrank gaps.
#   3  nothing disagrees, but a source could not be read.
# 1, 2 and 3 all ping `/fail`. The program's code is this script's code.
#
# CREDENTIALS, each read straight from its own Keychain item into its own variable, never an
# ambient `BWS_ACCESS_TOKEN` (the two-identity rule, docs/operations/credentials.md):
#   - the observer bearer (BWS `f793576f-…`), through the narrow `sds-operator` identity;
#   - the change-manager READ bearer (BWS `314f276d-…`), through the broad identity, which also
#     reads the Healthchecks API key (BWS `51509d5e-…`) to find the ping URL;
#   - GitHub, through the operator's own `gh` login.
#
# Usage:
#   scripts/run-membership-check.sh
# Install as a scheduled job with:
#   scripts/install-membership-check-launchd.sh
set -uo pipefail

CHECK_NAME="sds-membership-check"
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER bearer
CHANGE_MANAGER_READ_UUID="314f276d-55ca-4ddc-a24d-b4a3013508cd"   # change-manager READ bearer

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
sds_deadman_resolve "$CHECK_NAME"
if [ -z "$SDS_DEADMAN_PING_URL" ]; then
    echo "[membership] $SDS_DEADMAN_REASON — alerting disabled this run"
fi

REPORT="$(mktemp "${TMPDIR:-/tmp}/membership-check.XXXXXX")" || {
    echo "FATAL: could not create the report file" >&2
    sds_outcome_ping "/fail"
    exit 1
}
trap 'rm -f "$REPORT"' EXIT
sds_outcome_ping "/start"

# Every failure before the check runs is the tool failing: reported through the same check, with
# the reason as the ping body.
tool_fail() {
    echo "$1" | tee "$REPORT" >&2
    sds_outcome_ping "/fail" "$REPORT"
    exit 1
}
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"
NARROW_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
[ -n "$NARROW_IDENTITY" ] || tool_fail \
    "FATAL: no BWS identity for the observer credential (Keychain Claude/BWS_ACCESS_TOKEN_SDS)"
BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
[ -n "$BROAD_IDENTITY" ] || tool_fail \
    "FATAL: no BWS identity for the change-manager credential (Keychain Claude/BWS_ACCESS_TOKEN_VPS_BACKUP)"
SDS_MEMBERS_OBSERVER_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID" "$NARROW_IDENTITY")"
[ -n "$SDS_MEMBERS_OBSERVER_TOKEN" ] || tool_fail \
    "FATAL: could not read the orchestrator observer credential from BWS"
SDS_MEMBERS_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_READ_UUID" "$BROAD_IDENTITY")"
[ -n "$SDS_MEMBERS_CHANGE_MANAGER_TOKEN" ] || tool_fail \
    "FATAL: could not read the change-manager READ credential from BWS"
SDS_MEMBERS_GITHUB_TOKEN="${SDS_MEMBERS_GITHUB_TOKEN:-$(gh auth token 2>/dev/null || true)}"
[ -n "$SDS_MEMBERS_GITHUB_TOKEN" ] || tool_fail \
    "FATAL: no GitHub credential. Set SDS_MEMBERS_GITHUB_TOKEN, or authenticate gh."
export SDS_MEMBERS_OBSERVER_TOKEN SDS_MEMBERS_CHANGE_MANAGER_TOKEN SDS_MEMBERS_GITHUB_TOKEN

"$REPO_ROOT/.venv/bin/membership-check" > "$REPORT" 2>&1
rc=$?
cat "$REPORT"
if [ "$rc" -eq 0 ]; then
    sds_outcome_ping "" "$REPORT"
else
    echo "[membership] exit $rc — reporting a failure"
    sds_outcome_ping "/fail" "$REPORT"
fi
exit "$rc"
