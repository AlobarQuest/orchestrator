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
# stops pinging goes down after the grace, so it is a liveness check too. Only the shared
# helper's lookup of the ping URL by check name is reused; its arming is not, because it pings
# success on a lane's finding code. A success ping carries the report too, so a rule upstream has
# fixed ("can retire") is visible in the check's log.
#
# EXIT CODES, the whole interface a scheduled run has (`fork_harden/cli.py`):
#   0  no fork is held.
#   1  the tool itself failed.
#   2  a fork could not be read or pushed, and none is held.
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
# security_patches`. A scope failure of any kind exits 2 without hardening and pings `/fail`; a
# fork in `rules.FORKS` the record doesn't grant is skipped and exits 2 (`fork_harden/cli.py`).
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
sds_deadman_resolve "$CHECK_NAME"
if [ -z "$SDS_DEADMAN_PING_URL" ]; then
    echo "[harden] $SDS_DEADMAN_REASON — alerting disabled this run"
fi

# One ping. The URL goes to curl on stdin as its config, never as an argument. `$1` is the suffix
# (`/start`, empty for success, `/fail`); `$2`, when given, a file sent as the ping's body.
harden_ping() {
    [ -n "$SDS_DEADMAN_PING_URL" ] || return 0
    local body=()
    [ -n "${2:-}" ] && body=(--data-binary "@$2")
    printf 'url = "%s%s"\n' "$SDS_DEADMAN_PING_URL" "$1" \
        | curl -fsS --max-time 10 --config - ${body[@]+"${body[@]}"} >/dev/null 2>&1 \
        || echo "[harden] ping '${1:-/}' failed (non-fatal)"
    return 0
}

REPORT="$(mktemp "${TMPDIR:-/tmp}/fork-harden.XXXXXX")" || {
    echo "FATAL: could not create the report file" >&2
    harden_ping "/fail"
    exit 1
}
trap 'rm -f "$REPORT"' EXIT
harden_ping "/start"

# The grants, read before anything is hardened. Every failure here is reported through the same
# check as a held fork, with the reason as the ping body.
scope_fail() {
    local code="${*: -1}"
    echo "${*:1:$#-1}" | tee "$REPORT" >&2
    harden_ping "/fail" "$REPORT"
    exit "$code"
}
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"
NARROW_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
[ -n "$NARROW_IDENTITY" ] || scope_fail \
    "FATAL: no BWS identity for the observer credential (Keychain Claude/BWS_ACCESS_TOKEN_SDS)" 1
OBSERVER_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID" "$NARROW_IDENTITY")"
[ -n "$OBSERVER_TOKEN" ] || scope_fail \
    "FATAL: could not read the orchestrator observer credential from BWS" 1
GRANTED="$(cd "$REPO_ROOT" && SDS_SCOPE_ORCHESTRATOR_TOKEN="$OBSERVER_TOKEN" \
    "$REPO_ROOT/.venv/bin/python" -m scripts.sds_scope granted security_patches 2>"$REPORT")"
scope_rc=$?
if [ "$scope_rc" -ne 0 ]; then
    scope_fail "the forks granted security patches could not be read (sds_scope exited" \
        "$scope_rc): nothing was hardened. $(cat "$REPORT")" 2
fi
GRANT_ARGS=()
while IFS= read -r repository; do
    [ -n "$repository" ] && GRANT_ARGS+=(--granted "$repository")
done <<< "$GRANTED"

"$REPO_ROOT/.venv/bin/fork-harden" run --push ${GRANT_ARGS[@]+"${GRANT_ARGS[@]}"} > "$REPORT" 2>&1
rc=$?
cat "$REPORT"
if [ "$rc" -eq 0 ]; then
    harden_ping "" "$REPORT"
else
    echo "[harden] exit $rc — reporting a failure"
    harden_ping "/fail" "$REPORT"
fi
exit "$rc"
