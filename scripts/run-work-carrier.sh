#!/usr/bin/env bash
# One carry pass: every approved change-manager work proposal becomes an orchestrator package
# intake (ADR-0026, completed by ADR-0027).
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
#
# THE VENV'S BIN GOES ON PATH, and that is a requirement rather than tidiness: the carry resolves
# `orchestrator` from PATH to build each payload, and without it every record refuses with
# `emitter_not_on_path` -- a clean refusal, but a whole pass of them.
#
# EXIT CODES, the whole interface a scheduled run has:
#   0  every approved record was carried (or prepared, on a pass not asked to register), or
#      there were none.
#   1  the tool itself failed (a missing or unreadable credential, change-manager unreachable).
#   2  the tool ran but could not use its inputs (no checkout root, no credential configured).
#   3  something was found -- a record that could not be prepared, or one the orchestrator
#      refused to register. Either needs a person.
#
# A CARRIED RECORD IS NOT A FINDING, and neither is one merely prepared on a pass that was not
# asked to register. Making either one would leave this control permanently red for doing its
# job -- which this estate has now recorded itself doing four times.
#
# Usage:
#   scripts/run-work-carrier.sh                # reports; writes nothing
#   scripts/run-work-carrier.sh --register     # registers the intakes it prepared
# Install as a scheduled job with:
#   scripts/install-work-carrier-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo).
# The READ-scoped change-manager bearer. Probed against production 2026-08-12 by the landing
# lane, which holds the same secret: 200 on the listing, 403 on approve and on the observation.
CHANGE_MANAGER_UUID="314f276d-55ca-4ddc-a24d-b4a3013508cd"
# The orchestrator SYSTEM bearer, in the `SDS Operator` project.
ORCHESTRATOR_SYSTEM_UUID="221a48d5-3f29-4898-b300-b4820140c880"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

SDS_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
if [ -z "$SDS_IDENTITY" ]; then
  echo "FATAL: no BWS identity for the orchestrator credential (Keychain service Claude)" >&2
  exit 1
fi

if [ -z "${CHANGE_MANAGER_TOKEN:-}" ]; then
  CHANGE_MANAGER_TOKEN="$(_bws_value "$CHANGE_MANAGER_UUID" "$BROAD_IDENTITY")"
  export CHANGE_MANAGER_TOKEN
fi
if [ -z "${WORK_CARRIER_ORCHESTRATOR_TOKEN:-}" ]; then
  WORK_CARRIER_ORCHESTRATOR_TOKEN="$(_bws_value "$ORCHESTRATOR_SYSTEM_UUID" "$SDS_IDENTITY")"
  export WORK_CARRIER_ORCHESTRATOR_TOKEN
fi

# `set -e` is deliberately not used, so a failed fetch would otherwise leave these EMPTY and fall
# through -- into an exit 2 that reports "unusable input" for what is actually a credential
# failure. Name each here, so the exit code means what this header says it means.
if [ -z "${CHANGE_MANAGER_TOKEN:-}" ]; then
  echo "FATAL: could not read the change-manager credential from BWS" >&2
  exit 1
fi
if [ -z "${WORK_CARRIER_ORCHESTRATOR_TOKEN:-}" ]; then
  echo "FATAL: could not read the orchestrator system credential from BWS" >&2
  exit 1
fi

export PATH="$REPO_ROOT/.venv/bin:$PATH"
"$REPO_ROOT/.venv/bin/work-carrier" "$@"
