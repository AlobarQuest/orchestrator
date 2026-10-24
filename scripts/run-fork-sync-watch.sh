#!/usr/bin/env bash
# One stuck-lane pass over the fork-sync lane (ADR-0056 amendment 6): `fork-sync watch`, which
# reads every fork's sync, pull request, checks and alert, and says whether each one is moving.
#
# WHY A SEPARATE PROCESS. The lanes' dead-man pings prove only that a pass RAN. A pass that runs
# and holds everything looks healthy to them, and that is how three holds of 2026-10-21/22 went
# unseen until a person looked: a conflicted sync pull request on which no check ever reported, a
# stale change-manager bearer that held every passing sync for a day, and an exhausted GitHub rate
# limit under which every lane read 403. This watcher reads only, changes nothing, and sits beside
# the lanes rather than inside one, so it cannot hold a lane and a lane cannot hide from it.
#
# THIS CHECK IS AN OUTCOME ALARM, NOT A DEAD-MAN SWITCH, AND ITS PINGS SAY SO. The lanes' shared
# helper (`scripts/sds-deadman.sh`) pings SUCCESS on a lane's finding code, because its question is
# "did the lane run". This check's question is "is anything stuck", so a finding must page: every
# non-zero exit pings `/fail` with the report as the body, which Healthchecks stores and shows. It
# is still a liveness check too: a pass that stops pinging goes down after the grace, so the
# helper's arming is not used here (it would turn every finding into a healthy ping). Only its
# lookup of the ping URL by check name is reused: one API key in BWS, nothing committed.
#
# EXIT CODES, the whole interface a scheduled run has, in the LANDER group's vocabulary (the
# inert landing lane is this watcher's sibling and the two are read side by side):
#   0  nothing is stuck.
#   1  the tool itself failed (a missing binary, an unhandled error).
#   2  the tool could not use its inputs: GitHub could not be read (a 403 rate limit among them),
#      or a fork's declaration does not parse.
#   3  a fork is stuck.
# 1, 2 and 3 all ping `/fail`. The program's code is this script's code.
#
# CREDENTIALS. GitHub is read with the operator's own `gh` login and nothing else; there is no BWS
# read in the pass. The Healthchecks API key (BWS `51509d5e-…`) is read through the broad machine
# account only to find the ping URL, and that read never gates the pass.
#
# Usage:
#   scripts/run-fork-sync-watch.sh
# Install as a scheduled job with:
#   scripts/install-fork-sync-watch-launchd.sh
set -uo pipefail

CHECK_NAME="sds-fork-sync-watch"

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
    echo "[watch] $SDS_DEADMAN_REASON — alerting disabled this run"
fi

# One ping. The URL goes to curl on stdin as its config, never as an argument. `$1` is the suffix
# (`/start`, empty for success, `/fail`); `$2`, when given, a file sent as the ping's body.
watch_ping() {
    [ -n "$SDS_DEADMAN_PING_URL" ] || return 0
    local body=()
    [ -n "${2:-}" ] && body=(--data-binary "@$2")
    printf 'url = "%s%s"\n' "$SDS_DEADMAN_PING_URL" "$1" \
        | curl -fsS --max-time 10 --config - ${body[@]+"${body[@]}"} >/dev/null 2>&1 \
        || echo "[watch] ping '${1:-/}' failed (non-fatal)"
    return 0
}

REPORT="$(mktemp -t fork-sync-watch)"
trap 'rm -f "$REPORT"' EXIT
watch_ping "/start"
"$REPO_ROOT/.venv/bin/fork-sync" watch > "$REPORT" 2>&1
rc=$?
cat "$REPORT"
if [ "$rc" -eq 0 ]; then
    watch_ping ""
else
    echo "[watch] exit $rc — reporting a failure"
    watch_ping "/fail" "$REPORT"
fi
exit "$rc"
