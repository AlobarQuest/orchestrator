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
# machine account only to find the ping URL, and that read never gates the pass.
#
# Usage:
#   scripts/run-fork-harden.sh
# Install as a scheduled job with:
#   scripts/install-fork-harden-launchd.sh
set -uo pipefail

CHECK_NAME="sds-fork-hardening"

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
"$REPO_ROOT/.venv/bin/fork-harden" run --push > "$REPORT" 2>&1
rc=$?
cat "$REPORT"
if [ "$rc" -eq 0 ]; then
    harden_ping "" "$REPORT"
else
    echo "[harden] exit $rc — reporting a failure"
    harden_ping "/fail" "$REPORT"
fi
exit "$rc"
