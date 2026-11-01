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
#   1  the lane could not do its job: a missing credential, an unusable state directory, alert-hub
#      refusing a send, a refused --seed, or a missing known-subjects file (which also raises
#      `execution-watch-unread`).
#   2  something was found: the orchestrator could not be read, so every remembered key was held.
#      This is the lane's FINDING code: the check answers whether the lane is alive, and an orchestrator deploy
#      must not page; `execution-watch-unread` is the page, after two hours.
# Anything else is reported as 1.
#
# ONE BWS IDENTITY, the narrow `sds-operator` account read from the Keychain into a variable that is
# never exported, so it can serve only the fetches below. The alert token is fetched only on a
# --submit pass: a pass that sends nothing needs none.
#
# Usage:
#   scripts/run-execution-watch.sh [--seed | --submit]
# Install as a scheduled job with:
#   scripts/install-execution-watch-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
OBSERVER_BEARER_UUID="${EXECUTION_WATCH_OBSERVER_BWS_UUID:-f793576f-e9aa-4f9d-8089-b4a000b9e2d5}"
SDS_BRIDGE_ALERT_UUID="${EXECUTION_WATCH_ALERT_BWS_UUID:-b415d03b-f92d-46b1-8a9d-b4f10103df49}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Activation: best-effort, so a merged change is live on this machine before the pass runs.
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

# The dead-man switch, armed only by the scheduled pass (--submit), after activation (which may
# exec) and before any credential fetch, so a fetch failure is reported too. A bare or --seed run
# is an operator inspecting the lane and must not reset the timer of a schedule that stopped.
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

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

SDS_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
if [ -z "$SDS_IDENTITY" ]; then
  echo "FATAL: no narrow BWS identity for the observer bearer (service Claude)" >&2
  exit 1
fi
ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
ORCHESTRATOR_API_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID" "$SDS_IDENTITY")"
export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN
if [ -z "${ORCHESTRATOR_API_TOKEN:-}" ]; then
  echo "FATAL: could not read the orchestrator observer bearer from BWS (narrow)" >&2
  exit 1
fi

if [ "$_submitting" -eq 1 ]; then
  SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID" "$SDS_IDENTITY")"
  export SDS_BRIDGE_ALERT_TOKEN
  if [ -z "${SDS_BRIDGE_ALERT_TOKEN:-}" ]; then
    echo "FATAL: could not read the sds-bridge alert sender token from BWS (narrow)" >&2
    exit 1
  fi
fi
unset SDS_IDENTITY

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
