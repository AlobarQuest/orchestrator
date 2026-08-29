#!/usr/bin/env bash
# One estate-landing pass: land the pull requests the estate has routed and approved, into the
# repositories where landing changes something already serving (ADR-0019 increment 5b).
#
# WHY THIS RUNS IN THE CHANGE WINDOW AND THE PRODUCER DOES NOT. Proposing is not acting. A record
# that exists before the window opens is the entire point of having one; landing IS the change,
# and policy declares the hours in which something already serving may change. The orchestrator
# refuses outside them regardless -- this schedule is so the pass HAPPENS then, not so that it is
# allowed to.
#
# BARE INVOCATION ASKS FOR NOTHING. It reports every routed pull request and the condition each one
# misses; `--submit` is what turns the report into a request. This wrapper must not supply it.
#
# BOTH CREDENTIALS ARE FETCHED EITHER WAY, and that is a correction rather than an oversight. The
# producer's launcher fetches its writing credential only for a writing run, because its dry run
# reads GitHub and nothing else. This program's read surface is the ORCHESTRATOR, and the
# admission route is authenticated -- there is no read-only credential for it -- so a run that
# skipped the fetch would exit 2 having examined nothing, which is the success-shaped silence this
# estate keeps finding. Reported by adversarial review; the first version had exactly that bug.
#
# TWO BWS IDENTITIES, and neither can do the other's half. MEASURED 2026-08-12, both directions:
# the orchestrator bearer lives in a project only the narrow `sds-operator` account can read, and
# the change-manager bearer in one only the broad machine account can. A launcher bootstrapping
# with either alone dies on the other's fetch. `infraops-mcp-server/scripts/drift-audit.sh` is the
# in-estate precedent for overriding the identity for a single foreign call.
#
# EXPECT A FIRST PASS TO LAND NOTHING, and read that as the conditions working rather than as the
# lane failing. A held pull request that names its condition is the whole point of the report.
#
# EXIT CODES, the whole interface a scheduled run has:
#   0  everything was measured; nothing was held for a reason that needs a person.
#   1  the tool itself failed (a missing or unreadable credential, an unhandled error).
#   2  the tool ran but could not use its inputs.
#   3  something was found -- a pull request held on a condition somebody has to act on.
#
# NOTHING HERE DECIDES ANYTHING. Every term is evaluated by the orchestrator, inside the
# transaction that records the act. This script asks it two questions and relays the answers.
#
# Usage:
#   scripts/run-estate-landing.sh              # reports; asks for nothing
#   scripts/run-estate-landing.sh --submit     # asks for the landings that are admissible
# Install as a scheduled job with:
#   scripts/install-estate-landing-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo).
# The SYSTEM bearer. The landing route is SYSTEM-only: not the worker, because a runner asking for
# its own work to be landed attests to its own compliance, and not a human, because a person can
# land a pull request themselves.
ORCHESTRATOR_SYSTEM_UUID="221a48d5-3f29-4898-b300-b4820140c880"
# The READ-scoped change-manager bearer, which enumerates which changes were routed and reads what
# the policy requires -- and nothing else. Adversarial review found this launcher reaching for the
# OBSERVE credential, which carries one write this program never makes (recording a rollout
# observation, the watcher's job) and which the watcher already holds, so a rotation would have
# broken two schedules with no signal. `change-manager/M2M_TOKEN_READ` already existed; the review
# reported it as never minted, and it was there. Probed against production 2026-08-12: 200 on the
# listing and on the policy, 403 on approve and on deploy-observation.
CHANGE_MANAGER_UUID="314f276d-55ca-4ddc-a24d-b4a3013508cd"

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
# reach nobody: a pass that stops running, or that fails every night, is silent. Armed AFTER
# activation, because `activate_checkout` may `exec` and an `exec` does not fire an EXIT handler.
# It reports and never gates -- every failure inside it logs a line and returns 0. Note this lane
# runs FOUR times a night and the check is scheduled to expect all four: a single missed window is
# a lane that half-stopped, which is the thing worth hearing about.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-estate-landing "$@"


# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe,
# which breaks the parse below.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE BWS_ACCESS_TOKEN="$2" \
    bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

# SOURCED, not executed. `sds-token.sh` EXPORTS the value and prints nothing -- its own header says
# so -- so command-substituting it yields the empty string and every fetch below fails with a
# message about a missing environment variable rather than about a missing token. That was the
# first version of this script, and under launchd (which sets no BWS_ACCESS_TOKEN) it could never
# have completed a single pass.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/scripts/sds-token.sh"
fi
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  echo "FATAL: BWS_ACCESS_TOKEN not available for the orchestrator credential" >&2
  exit 1
fi
SDS_IDENTITY="$BWS_ACCESS_TOKEN"

# The broad machine account, for the change-manager project alone. Named here rather than silently
# different, exactly as the producer's launcher names its own.
BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
if [ -z "$BROAD_IDENTITY" ]; then
  echo "FATAL: no BWS identity for the change-manager credential (Keychain service Claude)" >&2
  exit 1
fi

if [ -z "${ESTATE_LANDING_CHANGE_MANAGER_TOKEN:-}" ]; then
  ESTATE_LANDING_CHANGE_MANAGER_TOKEN="$(_bws_value "$CHANGE_MANAGER_UUID" "$BROAD_IDENTITY")"
  export ESTATE_LANDING_CHANGE_MANAGER_TOKEN
fi
if [ -z "${ESTATE_LANDING_ORCHESTRATOR_TOKEN:-}" ]; then
  ESTATE_LANDING_ORCHESTRATOR_TOKEN="$(_bws_value "$ORCHESTRATOR_SYSTEM_UUID" "$SDS_IDENTITY")"
  export ESTATE_LANDING_ORCHESTRATOR_TOKEN
fi

# `set -e` is deliberately not used, so a failed fetch would otherwise leave these EMPTY and fall
# through -- into an exit 2 that reports "unusable input" for what is actually a credential
# failure. Name each here, so the exit code means what this header says it means.
if [ -z "${ESTATE_LANDING_CHANGE_MANAGER_TOKEN:-}" ]; then
  echo "FATAL: could not read the change-manager credential from BWS" >&2
  exit 1
fi
if [ -z "${ESTATE_LANDING_ORCHESTRATOR_TOKEN:-}" ]; then
  echo "FATAL: could not read the orchestrator system credential from BWS" >&2
  exit 1
fi

"$REPO_ROOT/.venv/bin/estate-landing" "$@"
