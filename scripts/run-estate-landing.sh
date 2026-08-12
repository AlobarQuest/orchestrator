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
# BARE INVOCATION IS A DRY RUN. It reports every waiting pull request and the condition each one
# misses, and asks the orchestrator to land nothing. `--submit` is what separates reporting from
# acting, and this wrapper must not supply it for you.
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
#   scripts/run-estate-landing.sh              # dry run: reports, lands nothing
#   scripts/run-estate-landing.sh --submit     # lands what is admissible
# Install as a scheduled job with:
#   scripts/install-estate-landing-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo).
# The SYSTEM bearer. The landing route is SYSTEM-only: not the worker, because a runner asking for
# its own work to be landed attests to its own compliance, and not a human, because a person can
# land a pull request themselves.
ORCHESTRATOR_SYSTEM_UUID="221a48d5-3f29-4898-b300-b4820140c880"
# The read-scoped change-manager bearer, for enumerating which pull requests have a record at all.
# Deliberately NOT the full one: this script chooses nothing and approves nothing.
CHANGE_MANAGER_READ_UUID="3b9503da-eb7e-401d-b4a7-b4a400c07efb"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The orchestrator bearers live in a BWS project the narrow `sds-operator` account CAN read, so
# this launcher bootstraps with that account rather than the broad machine one -- unlike the
# producer's launcher, whose change-manager secrets live in a project that account cannot reach.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  BWS_ACCESS_TOKEN="$("$REPO_ROOT/scripts/sds-token.sh" 2>/dev/null || true)"
  export BWS_ACCESS_TOKEN
fi
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  echo "FATAL: BWS_ACCESS_TOKEN not available" >&2
  exit 1
fi

# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe,
# which breaks the parse below.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

# ONLY FETCHED FOR AN ACTING RUN. A dry run reports what it would ask for and asks for nothing, so
# it must not need the credential that could act -- which matters most on the machine where
# somebody most wants to inspect before granting it one.
case " $* " in
  *" --submit "*) NEEDS_CREDENTIAL=1 ;;
  *) NEEDS_CREDENTIAL=0 ;;
esac

if [ -z "${ESTATE_LANDING_CHANGE_MANAGER_TOKEN:-}" ]; then
  ESTATE_LANDING_CHANGE_MANAGER_TOKEN="$(_bws_value "$CHANGE_MANAGER_READ_UUID")"
  export ESTATE_LANDING_CHANGE_MANAGER_TOKEN
fi
if [ -z "${ESTATE_LANDING_CHANGE_MANAGER_TOKEN:-}" ]; then
  echo "FATAL: could not read the change-manager credential from BWS" >&2
  exit 1
fi

if [ "$NEEDS_CREDENTIAL" -eq 1 ] && [ -z "${ESTATE_LANDING_ORCHESTRATOR_TOKEN:-}" ]; then
  ESTATE_LANDING_ORCHESTRATOR_TOKEN="$(_bws_value "$ORCHESTRATOR_SYSTEM_UUID")"
  export ESTATE_LANDING_ORCHESTRATOR_TOKEN
fi
# `set -e` is deliberately not used, so a failed fetch would otherwise leave this EMPTY and fall
# through into a 401 the tool would report as an orchestrator failure. Name it here, so the exit
# code means what this header says it means.
if [ "$NEEDS_CREDENTIAL" -eq 1 ] && [ -z "${ESTATE_LANDING_ORCHESTRATOR_TOKEN:-}" ]; then
  echo "FATAL: could not read the orchestrator system credential from BWS" >&2
  exit 1
fi

"$REPO_ROOT/.venv/bin/estate-landing" "$@"
