#!/usr/bin/env bash
# The dead-man switch every scheduled SDS lane arms at the top of its pass.
#
# WHY THIS EXISTS. `launchd` discards a job's exit code. Every one of these launchers documents its
# codes as "the whole interface a scheduled run has" and then hands them to something that throws
# them away, so a lane that stops running, or that runs and fails every morning, reports nothing at
# all. `bump_proposer` sat unscheduled for eight days with nothing saying so, and `intent-packages`
# `main` sat red for 31 hours with its failure reported within minutes and nobody subscribed.
# Detection has never been this estate's problem; SUBSCRIPTION has.
#
# Source this (don't execute it) and arm once, immediately after `activate_checkout`:
#     source "$(dirname "${BASH_SOURCE[0]}")/sds-deadman.sh"
#     sds_deadman_arm sds-landing-ledger
#
# WHY IT LOOKS UP THE CHECK BY NAME RATHER THAN READING A PING URL FROM AN ENV FILE. Two patterns
# exist in this estate and the difference matters at seven checks. `infraops-mcp-server`'s
# `drift-audit.sh` reads a ping URL from an env file: one secret per check, and this estate has
# already been bitten by a credential copied per repository and rotated in only some of the copies
# (`FACTORY_PR_TOKEN`, two dead copies surviving a rotation for two weeks). `vps-backup`'s
# `backup-mini.sh` resolves the URL at runtime from the Healthchecks Management API, looking the
# check up BY NAME, with one API key in BWS. One secret, N checks, and a rotation that cannot leave
# a stale copy behind. This is that shape.
#
# IT REPORTS; IT NEVER GATES. Every failure here -- no BWS identity, no API key, no such check, an
# unreachable Healthchecks -- logs one line and returns 0. A switch that can take down the lane it
# watches is a way to stop a lane, which is the opposite of what it is for. The cost of that choice
# is real and worth naming: a run whose arming silently failed is indistinguishable, to
# Healthchecks, from a run that never happened -- so the check goes down and reports a lane that is
# in fact working. That is the right direction to fail in. A false page is answerable; a lane that
# stopped reporting is not.
#
# TWO BWS IDENTITIES, and this one is the BROAD account, not the narrow `sds-operator`. The
# Healthchecks Management API key lives in a project only the broad machine account can read, while
# the orchestrator bearers live in one only the narrow account can. That is why this helper reads
# the Keychain item DIRECTLY into a local variable and passes it per-call rather than exporting
# `BWS_ACCESS_TOKEN`: `sds-token.sh` is sourced AFTER this and respects an already-set value, so
# exporting the broad token here would silently make it the identity for every orchestrator fetch
# downstream, each of which would then fail with a bare `404 Resource not found` naming nothing.
# `run-estate-landing.sh` is the worked example of the same discipline.

# The Healthchecks Management API key. Value fetched at runtime; never stored in this repo.
SDS_DEADMAN_API_KEY_UUID="260cc8ad-f170-44cc-a672-b47000df3350"
SDS_DEADMAN_API="https://healthchecks.io/api/v3/checks/"

# Resolved at arm time. Empty means alerting is disabled for this run, which every ping honours.
SDS_DEADMAN_PING_URL=""
SDS_DEADMAN_NAME=""
# Why it is empty, when it is. Carried rather than logged where it happens, so a run that could
# not read the API key says THAT and does not also say the check is missing -- two lines naming
# two causes for one failure is how a reader is sent after the wrong one.
SDS_DEADMAN_REASON=""

sds_deadman_log() {
    echo "[deadman] $*"
}

# The ping URL of the check named "$1", or nothing. Never fails the caller.
sds_deadman_resolve() {
    local name="$1" broad key
    broad="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
        -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
    if [ -z "$broad" ]; then
        SDS_DEADMAN_REASON="no broad BWS identity"
        return 0
    fi
    # `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
    # CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a
    # pipe, which breaks the parse -- a portfolio-wide defect fixed locally in three repos and
    # generalised in none. It costs nothing where the behaviour never fires.
    key="$(env -u FORCE_COLOR -u CLICOLOR_FORCE BWS_ACCESS_TOKEN="$broad" \
        bws secret get "$SDS_DEADMAN_API_KEY_UUID" --output json --color no 2>/dev/null \
        | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])' 2>/dev/null || true)"
    if [ -z "$key" ]; then
        SDS_DEADMAN_REASON="the Healthchecks API key could not be read"
        return 0
    fi
    local listing answer
    listing="$(curl -fsS --max-time 15 "$SDS_DEADMAN_API" -H "X-Api-Key: $key" 2>/dev/null)"
    if [ -z "$listing" ]; then
        SDS_DEADMAN_REASON="Healthchecks could not be reached"
        return 0
    fi
    # THREE OUTCOMES, NAMED SEPARATELY, because they all produce an empty ping url and only one
    # of them is "somebody forgot to create the check". The third is the one that would waste a
    # morning: the Management API omits `ping_url` for a READ-ONLY key, so a rotation to a
    # read-only key disables every lane's alerting at once while the log blames a missing check.
    # The check is matched on its EXACT name -- pinging a near-miss would report a lane as alive
    # that has not run, which is worse than reporting nothing.
    answer="$(printf '%s' "$listing" | SDS_DEADMAN_WANTED="$name" python3 -c 'import os, sys, json
wanted = os.environ["SDS_DEADMAN_WANTED"]
for check in json.load(sys.stdin).get("checks", []):
    if check.get("name") == wanted:
        print("URL " + check["ping_url"] if check.get("ping_url") else "NOPING")
        break
else:
    print("NONAME")' 2>/dev/null || true)"
    case "$answer" in
        "URL "*) SDS_DEADMAN_PING_URL="${answer#URL }" ;;
        NOPING) SDS_DEADMAN_REASON="check '$name' exposes no ping url (a read-only API key?)" ;;
        NONAME) SDS_DEADMAN_REASON="no check named '$name'" ;;
        *) SDS_DEADMAN_REASON="the Healthchecks listing could not be read" ;;
    esac
    return 0
}

# The ping itself. `$1` is the suffix: empty for success, `/start` or `/fail`.
sds_deadman_ping() {
    [ -n "$SDS_DEADMAN_PING_URL" ] || return 0
    curl -fsS --max-time 10 "${SDS_DEADMAN_PING_URL}$1" >/dev/null 2>&1 \
        || sds_deadman_log "ping '${1:-/}' failed (non-fatal)"
    return 0
}

# THE EXIT HANDLER IS THE POINT. Every one of these launchers can `exit 1` on a missing credential
# long before it reaches its program, and those are exactly the failures nobody currently sees. A
# ping placed after the program would report only the failures the program itself survived.
sds_deadman_finish() {
    local rc=$?
    if [ "$rc" -eq 0 ]; then
        sds_deadman_ping ""
    else
        sds_deadman_ping "/fail"
    fi
    # Explicit rather than relying on bash preserving the pre-handler status: the handler's own
    # last command would otherwise decide it, and a `curl` that failed would rewrite the pass's
    # answer on its way out.
    exit "$rc"
}

# A DRY RUN MUST NOT COUNT AS A PASS, and this is the one argument the helper reads. Every lane
# documents a dry run as writing nothing, and `run-activation-sweep.sh` goes so far as to suppress
# its own activation step under `--dry-run` for exactly that reason. A ping is a write to the one
# thing that watches the lane: an operator inspecting a lane by hand would reset the timer of a
# schedule that has in fact stopped, which is precisely the condition the switch exists to detect.
#
# THE RESIDUAL IS NAMED RATHER THAN PAPERED OVER. Two lanes spell their reporting mode as the BARE
# invocation and their scheduled mode as `--submit` (`run-bump-proposer.sh`,
# `run-estate-landing.sh`), so a bare operator run of those two still pings. Keying on `--submit`
# instead would need per-launcher knowledge, and getting that wrong disables a lane's switch
# silently -- the quiet failure this whole file exists to end.
sds_deadman_is_dry_run() {
    for argument in "$@"; do
        [ "$argument" = "--dry-run" ] && return 0
    done
    return 1
}

# `$1` is the check name; everything after it is the pass's own arguments.
sds_deadman_arm() {
    SDS_DEADMAN_NAME="$1"
    shift
    if sds_deadman_is_dry_run "$@"; then
        sds_deadman_log "--dry-run: not arming, so this run cannot mask a schedule that stopped"
        return 0
    fi
    sds_deadman_resolve "$SDS_DEADMAN_NAME"
    if [ -n "$SDS_DEADMAN_PING_URL" ]; then
        sds_deadman_log "armed ($SDS_DEADMAN_NAME)"
    else
        sds_deadman_log "$SDS_DEADMAN_REASON — alerting disabled this run"
    fi
    # Installed WHETHER OR NOT the check resolved, so there is one code path rather than two. Every
    # ping is a no-op on an empty URL.
    trap sds_deadman_finish EXIT
    sds_deadman_ping "/start"
    return 0
}
