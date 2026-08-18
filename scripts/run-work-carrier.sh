#!/usr/bin/env bash
# One carry pass: every approved change-manager work proposal becomes a ready orchestrator
# intake payload, printed for a human to paste (ADR-0026).
#
# THIS PASS WRITES NOTHING, to either system. It reads change-manager's approved work proposals
# and runs `orchestrator emit-intake-payload` against the package each one names. There is no
# write path in the program, so "a record it could not prepare is left exactly as it was" is a
# property of its shape rather than of a branch that has to be reached correctly.
#
# WHY THE LAST STEP IS A HUMAN AND NOT THIS SCRIPT. Package intake requires an `ActorRole.HUMAN`
# actor, and human gates are browser-only, permanently (ADR-0006). ADR-0026 deliberately did NOT
# decide whether a machine may ever register one -- that would be the first automated path into
# canonical work, a standing-authority decision of ADR-0025's weight -- and said this work is the
# evidence on which to make that decision rather than the making of it. So the pass ends with a
# payload on stdout and a person pasting it into /review/intakes/new.
#
# WHY IT IS NOT IN THE CHANGE WINDOW. Nothing here changes anything, so there is nothing for a
# window to bound. It runs in the morning so the queue a human approved yesterday is prepared
# before they look at it.
#
# ONE CREDENTIAL, and it is the READ-scoped one. This program reads a listing; the `read` scope is
# exactly that and change-manager refuses everything else to it. It must NOT reach for the full
# bearer, which can approve a record -- a carry that could approve the proposal it is carrying
# would be a system asking itself for permission.
#
# ONE BWS IDENTITY, unlike the landing lane's two: the only secret this pass needs lives in the
# change-manager project, which the broad machine account reads. `sds-token.sh` is deliberately
# NOT sourced -- it exports a narrow identity that cannot read this project, and sourcing it
# alongside a default would make one ambient value stand for both identities, which is the failure
# this estate already recorded on the launchers that do need two.
#
# THE VENV'S BIN GOES ON PATH, and that is a requirement rather than tidiness: the carry resolves
# `orchestrator` from PATH to build each payload, and without it every record refuses with
# `emitter_not_on_path` -- a clean refusal, but a whole pass of them.
#
# EXIT CODES, the whole interface a scheduled run has:
#   0  every approved record was either prepared or there were none.
#   1  the tool itself failed (a missing or unreadable credential, change-manager unreachable).
#   2  the tool ran but could not use its inputs (no checkout root, no credential configured).
#   3  something was found -- a record that could not be prepared, which needs a person.
#
# A PREPARED RECORD IS NOT A FINDING. It is ordinary work waiting on the paste that IS the design,
# and making it one would leave this control permanently red for doing its job -- which this
# estate has now recorded itself doing four times.
#
# Usage:
#   scripts/run-work-carrier.sh
set -uo pipefail

# The READ-scoped change-manager bearer: it enumerates change records and can do nothing else.
# Probed against production 2026-08-12 by the landing lane, which holds the same secret: 200 on
# the listing, 403 on approve and on deploy-observation.
CHANGE_MANAGER_UUID="314f276d-55ca-4ddc-a24d-b4a3013508cd"

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

export PATH="$REPO_ROOT/.venv/bin:$PATH"
"$REPO_ROOT/.venv/bin/work-carrier" "$@"
