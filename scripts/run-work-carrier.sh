#!/usr/bin/env bash
# One pass over the work lane's approved queue, in TWO phases (ADR-0026/0027, and ADR-0029).
#
# RETIREMENT FIRST, then the carry. A record whose work the delivery system has already built is
# retired (`work-watcher`); every record still waiting becomes an orchestrator package intake
# (`work-carrier`). The order is load-bearing and the reason is at the invocation below.
#
# WHAT A PASS DOES DEPENDS ON ONE FLAG. Without `--register` it reads change-manager's approved
# work proposals, runs `orchestrator emit-intake-payload` against the package each names, prints
# the payloads, and writes NOTHING to either system. With `--register` it also registers each
# prepared intake. The scheduled job passes `--register`; the bare form is how a person inspects
# what would be registered without anything being.
#
# WHY THE LAST STEP IS NO LONGER A HUMAN PASTE. Intake registration used to require an
# `ActorRole.HUMAN` actor. ADR-0027 removed that, having found the gate was protecting a
# transcription rather than a judgment: every intake in production was authored by an AI and
# typed into a form by a person. What replaced it is attribution -- a machine-registered intake
# must name the approved change record that caused it, which this pass has and a paste did not.
# ADR-0006 is narrowed, not overturned: the breakdown approval and the authority approval are
# decisions and are still a person in a browser, so this pass ends at a queue for a human rather
# than at a running change.
#
# WHY IT IS NOT IN THE CHANGE WINDOW. A registered intake changes nothing that is running: it
# creates a package revision that cannot become work until a human approves a breakdown and then
# an authority envelope. The window bounds acts on live services, and this is not one. It runs in
# the morning so the queue a person approved yesterday is carried before they look at it.
#
# TWO BWS IDENTITIES, and each Keychain item is read DIRECTLY rather than through
# `sds-token.sh`. That helper respects an already-set BWS_ACCESS_TOKEN, so sourcing it beside a
# second `${BWS_ACCESS_TOKEN:-...}` default makes ONE ambient value stand for BOTH identities --
# and then no value of it works, because exported broad the SDS project is denied and exported
# narrow the change-manager project is. Under launchd nothing is exported and it works, so the
# failure appears only in the shell an operator debugs from, naming BWS rather than the cause.
# Each override below therefore has its own variable name.
#   - the change-manager project, read by the BROAD machine account: the READ-scoped bearer,
#     which enumerates approved records and can do nothing else. It must NOT reach for the full
#     bearer, which can approve a record -- a carry that could approve the proposal it is
#     carrying would be a system asking itself for permission.
#   - the `SDS Operator` project, read by the narrow `sds-operator` account: the orchestrator
#     SYSTEM bearer. ADR-0027 admits SYSTEM and HUMAN to intake registration and nothing else.
#     Fetched ONLY when the arguments carry --register: it is a canonical-mutation credential,
#     and a pass that cannot use it should not hold it. That also keeps the read-only invocation
#     usable on a machine with no access to that project.
#
# THE VENV'S BIN GOES ON PATH, and that is a requirement rather than tidiness: the carry resolves
# `orchestrator` from PATH to build each payload, and without it every record refuses with
# `emitter_not_on_path` -- a clean refusal, but a whole pass of them.
#
# EXIT CODES, the whole interface a scheduled run has. BOTH phases report on this scale and
# the WORST outcome is what the pass exits with -- see the ranking at the invocation below, which
# deliberately does not use the `for rc in 1 3 2` fold that lets a 127 read as success.
#   0  nothing needed doing, or everything that did was done.
#   1  a tool itself failed (an unreadable credential, change-manager unreachable).
#   2  a tool ran but could not use its inputs (no checkout root, no credential configured).
#   3  something was found -- a record that could not be prepared, one the orchestrator refused
#      to register, or one whose retirement change-manager refused. Each needs a person.
#
# A CARRIED RECORD IS NOT A FINDING, and neither is one merely prepared on a pass that was not
# asked to register, nor one the carry finds it has ALREADY carried. Making any of them one
# would leave this control permanently red for doing its job -- which this estate has now
# recorded itself doing five times. The same rule governs the retirement phase: a record
# retired, a retirement replayed, a record whose work is merely incomplete, and a record with
# no work at all are all ordinary and none is a finding.
#
# THE CARRY ASKS BEFORE IT REGISTERS. Nothing marks a change record carried -- this lane holds
# no write to change-manager -- so an approved record stays in the approved queue from the
# moment it is carried until the watcher or a person retires it. The carry therefore asks the
# orchestrator what the record has already caused (ADR-0029's route, the same one the watcher
# reads) and skips a record that has caused anything. Only a `--register` pass asks: the read
# exists to prevent this program's own write, and requiring the orchestrator credential for a
# reporting pass would break the read-only invocation this header promises works anywhere.
#
# Usage:
#   scripts/run-work-carrier.sh                # reports both phases; writes nothing
#   scripts/run-work-carrier.sh --register     # retires what is built, registers what is not
# Install as a scheduled job with:
#   scripts/install-work-carrier-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo).
# The READ-scoped change-manager bearer. Probed against production 2026-08-12 by the landing
# lane, which holds the same secret: 200 on the listing, 403 on approve and on the observation.
CHANGE_MANAGER_UUID="314f276d-55ca-4ddc-a24d-b4a3013508cd"
# The orchestrator SYSTEM bearer, in the `SDS Operator` project.
ORCHESTRATOR_SYSTEM_UUID="221a48d5-3f29-4898-b300-b4820140c880"
# ADR-0029. The PROPOSE-scoped change-manager bearer the retirement writes with, in the same
# BWS project as the read bearer above and read by the same broad identity. The scope reaches
# more than the retirement route; `work_watcher/change_manager.py` allowlists exactly one path,
# which is where that bound is asserted and tested.
CHANGE_MANAGER_PROPOSE_UUID="acccb346-4baa-43ec-a1d4-b4a400c048ee"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ACTIVATION — a merged change is not live on this machine until the code is pulled. The estate's
# Dependabot cascade stops at the merge, which is complete for a repository whose landing redeploys
# a hosted application and incomplete for this one, whose code runs from a working copy here
# (orchestrator ADR-0031). Best-effort by construction: the helper prints one `[activation]` line
# and returns 0 whatever it finds, so this job is never gated on being able to update itself. It
# re-execs this script when HEAD moves, because bash reads a script incrementally by byte offset
# and the file it just rewrote is this one.
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

# THE DEAD-MAN SWITCH. `launchd` discards this script's exit code, so the codes documented above
# reach nobody: a pass that stops running, or that fails every morning, is silent. Armed AFTER
# activation, because `activate_checkout` may `exec` and an `exec` does not fire an EXIT handler.
# It reports and never gates -- every failure inside it logs a line and returns 0.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-work-carrier


# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe,
# which breaks the parse below.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE BWS_ACCESS_TOKEN="$2" \
    bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
if [ -z "$BROAD_IDENTITY" ]; then
  echo "FATAL: no BWS identity for the change-manager credential (Keychain service Claude)" >&2
  exit 1
fi

if [ -z "${CHANGE_MANAGER_TOKEN:-}" ]; then
  CHANGE_MANAGER_TOKEN="$(_bws_value "$CHANGE_MANAGER_UUID" "$BROAD_IDENTITY")"
  export CHANGE_MANAGER_TOKEN
fi

# `set -e` is deliberately not used, so a failed fetch would otherwise leave this EMPTY and fall
# through -- into an exit 2 that reports "unusable input" for what is actually a credential
# failure. Name it here, so the exit code means what this header says it means.
if [ -z "${CHANGE_MANAGER_TOKEN:-}" ]; then
  echo "FATAL: could not read the change-manager credential from BWS" >&2
  exit 1
fi

# THE SYSTEM BEARER IS FETCHED ONLY FOR A PASS THAT WILL WRITE, and the gate is not tidiness. It
# is a canonical-mutation credential, and a pass that cannot use it should not hold it. Fetching
# it unconditionally also broke the read-only invocation this file's own header advertises: on
# any machine that cannot read the `SDS Operator` project -- which is every machine but this one
# -- `run-work-carrier.sh` with no arguments would exit 1 on a credential it was never going to
# send. Inspecting what would be registered must not require the right to register it.
_wants_register=0
for _arg in "$@"; do
  if [ "$_arg" = "--register" ]; then _wants_register=1; fi
done

if [ "$_wants_register" -eq 1 ]; then
  SDS_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
  if [ -z "$SDS_IDENTITY" ]; then
    echo "FATAL: no BWS identity for the orchestrator credential (Keychain service Claude)" >&2
    exit 1
  fi
  if [ -z "${WORK_CARRIER_ORCHESTRATOR_TOKEN:-}" ]; then
    WORK_CARRIER_ORCHESTRATOR_TOKEN="$(_bws_value "$ORCHESTRATOR_SYSTEM_UUID" "$SDS_IDENTITY")"
    export WORK_CARRIER_ORCHESTRATOR_TOKEN
  fi
  if [ -z "${WORK_CARRIER_ORCHESTRATOR_TOKEN:-}" ]; then
    echo "FATAL: could not read the orchestrator system credential from BWS" >&2
    exit 1
  fi
fi

# THE RETIREMENT BEARER, and it is a THIRD secret rather than a third identity: the `propose`
# credential lives in the change-manager BWS project the BROAD account already reads for the
# listing above. Fetched only for a pass that will act, exactly as the SYSTEM bearer is -- a
# reporting pass lists with the READ-scoped bearer it already has and cannot write anyway,
# because the watcher's write is gated on its own flag and not on which credential is present.
if [ "$_wants_register" -eq 1 ]; then
  if [ -z "${WORK_WATCHER_CHANGE_MANAGER_TOKEN:-}" ]; then
    WORK_WATCHER_CHANGE_MANAGER_TOKEN="$(_bws_value "$CHANGE_MANAGER_PROPOSE_UUID" \
      "$BROAD_IDENTITY")"
    export WORK_WATCHER_CHANGE_MANAGER_TOKEN
  fi
  if [ -z "${WORK_WATCHER_CHANGE_MANAGER_TOKEN:-}" ]; then
    echo "FATAL: could not read the change-manager propose credential from BWS" >&2
    exit 1
  fi
else
  # A reporting pass lists with the bearer it already has. It cannot write whatever it holds:
  # the watcher's write is gated on `--retire`, not on which credential is present.
  export WORK_WATCHER_CHANGE_MANAGER_TOKEN="$CHANGE_MANAGER_TOKEN"
  # The orchestrator bearer was NOT fetched above, because a reporting pass is not entitled to
  # the right to register. The watcher still needs a READ against the orchestrator to learn what
  # is complete, so try for it here and SKIP the phase if this machine cannot read that project
  # -- rather than reporting the whole pass unusable, which would break the read-only invocation
  # this file's header promises works anywhere.
  SDS_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
  if [ -n "$SDS_IDENTITY" ] && [ -z "${WORK_CARRIER_ORCHESTRATOR_TOKEN:-}" ]; then
    WORK_CARRIER_ORCHESTRATOR_TOKEN="$(_bws_value "$ORCHESTRATOR_SYSTEM_UUID" "$SDS_IDENTITY")"
  fi
fi
export WORK_WATCHER_ORCHESTRATOR_TOKEN="${WORK_CARRIER_ORCHESTRATOR_TOKEN:-}"

export PATH="$REPO_ROOT/.venv/bin:$PATH"

# THE WORST OUTCOME WINS, and an UNRECOGNISED code is the worst of all.
#
# The `for rc in 1 3 2` fold these launchers have used elsewhere lets any code outside {0,1,2,3}
# -- 127 for a missing binary is the one that actually happens -- fall through to `exit 0`, so a
# scheduled job that never ran reports a clean pass. Ranking instead means an unknown code is
# both preserved and dominant.
_rank() {
  case "$1" in
    0) echo 0 ;;
    3) echo 1 ;;
    1) echo 2 ;;
    2) echo 3 ;;
    *) echo 4 ;;
  esac
}

WORST_CODE=0
WORST_RANK=0
_record_outcome() {
  local rank
  rank="$(_rank "$1")"
  if [ "$rank" -gt "$WORST_RANK" ]; then
    WORST_RANK="$rank"
    WORST_CODE="$1"
  fi
}

# THE WATCHER RUNS FIRST, and the order is load-bearing rather than cosmetic. The carry selects
# on `status=approved`; a record whose work is already built is still in that queue until the
# watcher retires it, so a carry that read the listing first would re-register a finished
# revision and draw the very refusal ADR-0029 exists to remove -- then watch the record be
# retired a second later, having reported a finding on the morning the defect was fixed.
if [ -z "${WORK_WATCHER_ORCHESTRATOR_TOKEN:-}" ]; then
  # Reporting pass on a machine that cannot read the orchestrator's project. Skipping is right
  # and contributes NO exit code: nothing was found and nothing failed, so folding a 2 in here
  # would report an unusable pass for a phase this invocation was never entitled to run.
  echo "[SKIPPED]  the retirement phase needs an orchestrator credential this pass has not got"
elif [ "$_wants_register" -eq 1 ]; then
  "$REPO_ROOT/.venv/bin/work-watcher" --retire
  _record_outcome "$?"
else
  "$REPO_ROOT/.venv/bin/work-watcher"
  _record_outcome "$?"
fi

"$REPO_ROOT/.venv/bin/work-carrier" "$@"
_record_outcome "$?"

exit "$WORST_CODE"
