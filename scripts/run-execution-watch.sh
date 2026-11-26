#!/usr/bin/env bash
# One execution-watch pass: a work unit that is failed, held, stalled, blocked at dispatch or
# dispatched and never claimed becomes one keyed alert-hub alert, and resolves when it clears
# (Execution Part 1). It reads the orchestrator's `execution-status` route and writes only to
# alert-hub; it moves no unit.
#
# THREE INVOCATIONS.
#   (bare)     reads and prints what is raised. Needs the observer bearer only, sends nothing,
#              writes nothing, and does NOT ping the dead-man check.
#   --seed     writes the known-subjects file from one read and sends nothing. It refuses when the
#              file exists. Run it once before the first scheduled pass.
#   --submit   the scheduled pass. Needs the `sds-bridge` alert token as well, pings the check.
#
# EXIT CODES, the whole interface a scheduled run has:
#   0  the pass ran.
#   1  the lane could not do its job: no lane socket or token, a missing credential, an unusable
#      state directory, alert-hub refusing a send, a refused --seed, or a missing known-subjects
#      file (which also raises `execution-watch-unread`).
#   2  something was found: the orchestrator could not be read, so every remembered key was held.
#      This is the lane's FINDING code: the check answers whether the lane is alive, and an orchestrator deploy
#      must not page; `execution-watch-unread` is the page, after two hours.
# Anything else is reported as 1.
#
# THE LANE RUNS ONLY IN ITS CONTAINER (ADR-0059), where `sds_in_lane_container` is true: `lane-run`
# names the secrets broker's mounted socket in `SDS_SECRETS_SOCKET` (ADR-0055 amendment 30), or
# passes a lane's own token as its one secret. Every fetch below is `sds_bws_value` with one
# argument, through the socket or with that token in the environment, never as a process argument;
# the broker serves this lane only the UUIDs its entry in `lanes/secrets-broker/allowlist.toml`
# lists. `BWS_ACCESS_TOKEN` and `SDS_SECRETS_SOCKET` are unset before the program runs, which needs
# neither; the socket itself stays reachable, as the design accepts. The alert token is fetched
# only on a --submit pass: a pass that sends nothing needs none.
#
# Usage:
#   scripts/run-execution-watch.sh [--seed | --submit]
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
OBSERVER_BEARER_UUID="${EXECUTION_WATCH_OBSERVER_BWS_UUID:-f793576f-e9aa-4f9d-8089-b4a000b9e2d5}"
SDS_BRIDGE_ALERT_UUID="${EXECUTION_WATCH_ALERT_BWS_UUID:-b415d03b-f92d-46b1-8a9d-b4f10103df49}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The shared BWS reader (`sds-bws.sh`), sourced first: the host test below is its
# `sds_in_lane_container`.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"
if ! sds_in_lane_container; then
  echo "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN: the execution-watch lane runs only in its container, through lane-run" >&2
  exit 1
fi

# The dead-man switch, armed only by the scheduled pass (--submit) and before any credential fetch,
# so a fetch failure is reported too. A bare or --seed run is an operator inspecting the lane and
# must not reset the timer of a schedule that stopped.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
case " $* " in
  *" --submit "*) _submitting=1 ;;
  *) _submitting=0 ;;
esac
# `--dry-run` is the one argument the helper reads as "do not arm", so a non-submitting run hands it
# that instead of its own arguments.
_arm_args=("$@")
[ "$_submitting" -eq 1 ] || _arm_args=(--dry-run)
sds_deadman_arm sds-execution-watch --finding 2 "${_arm_args[@]}"

# Read through the socket, or the token already in the environment.
_lane_secret() { sds_bws_value "$1"; }
ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
ORCHESTRATOR_API_TOKEN="$(_lane_secret "$OBSERVER_BEARER_UUID")"
export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN
if [ -z "${ORCHESTRATOR_API_TOKEN:-}" ]; then
  echo "FATAL: could not read the orchestrator observer bearer from BWS (the lane's socket or token)" >&2
  exit 1
fi

if [ "$_submitting" -eq 1 ]; then
  SDS_BRIDGE_ALERT_TOKEN="$(_lane_secret "$SDS_BRIDGE_ALERT_UUID")"
  export SDS_BRIDGE_ALERT_TOKEN
  if [ -z "${SDS_BRIDGE_ALERT_TOKEN:-}" ]; then
    echo "FATAL: could not read the sds-bridge alert sender token from BWS (the lane's socket or token)" >&2
    exit 1
  fi
fi
unset BWS_ACCESS_TOKEN SDS_SECRETS_SOCKET

if [ ! -x "$REPO_ROOT/.venv/bin/execution-watch" ]; then
  echo "FATAL: no execution-watch in $REPO_ROOT/.venv (run uv sync)" >&2
  exit 1
fi
"$REPO_ROOT/.venv/bin/execution-watch" "$@"
rc=$?

# A code outside {0,1,2} is the program dying in a way it does not describe -- a missing binary is
# 127. Reported as 1 rather than folded to success.
case "$rc" in
  0|1|2) exit "$rc" ;;
  *) echo "FATAL: execution-watch exited $rc" >&2; exit 1 ;;
esac
