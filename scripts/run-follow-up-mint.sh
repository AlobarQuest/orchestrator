#!/usr/bin/env bash
# One follow-up minting pass: mint the work units whose package-declared follow-up reviews have
# come due (ADR-0007, WS-P2.8), in the `follow-up-mint` lane (ADR-0059 Amendment 10, increment 9).
#
# The orchestrator runs no scheduler of its own (ADR-0007, "Scheduled trigger"), so something
# outside it asks for each pass. This lane asks once a day with `orchestrator mint-follow-ups`,
# which posts `POST /api/v1/follow-ups/mint` as SYSTEM. The route is idempotent per revision: a
# unit's id is derived from its revision, so a second pass the same day mints nothing twice and
# reports the revision `already_minted`.
#
# THIS CHECK IS AN OUTCOME ALARM. A failed pass is the one thing worth telling: every non-zero exit
# pings `/fail` with the report as the body, and a pass that stops pinging goes down after the
# grace (ADR-0021 amendment 2). The launcher sends no alert-hub message: the lane holds no sender
# token (ADR-0059 Amendment 10, decision item 3).
#
# EXIT CODES (`orchestrator/cli.py`, `mint-follow-ups`):
#   0  the pass ran: it minted every due follow-up unit, or found none due.
#   1  the pass failed: no lane token, an unreadable or empty SYSTEM bearer, or the CLI's own
#      failure (the API refused, timed out or couldn't be reached). Any other code the CLI exits
#      with (2 is a usage error, 127 a missing binary) is reported as 1.
#
# CREDENTIALS, one secret: the orchestrator SYSTEM bearer (BWS `221a48d5-…`, key id
# `orchestrator-system`, never `orchestrator-drift-reporter`), read every pass.
#
# THE CONTAINER ONLY. The lane has no Mac job: minting runs on the Mac as a non-fatal step of
# infraops-mcp-server's `drift-audit.sh` until a pull request there removes it. So there is no
# Keychain branch and no activation: `lane-run` passes the lane's own token as `BWS_ACCESS_TOKEN`,
# the bearer is read with it, in the environment, never as a process argument, and the token is
# unset before the CLI runs, which needs none. Without `BWS_ACCESS_TOKEN` the launcher refuses
# before it resolves a ping URL or reads anything.
#
# Usage (in the lane's container, through `lane-run`):
#   lane-run follow-up-mint
set -uo pipefail

CHECK_NAME="sds-follow-up-mint"
ORCHESTRATOR_SYSTEM_UUID="221a48d5-3f29-4898-b300-b4820140c880"   # orchestrator-system SYSTEM bearer

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$#" -gt 0 ]; then
  echo "FATAL: run-follow-up-mint.sh takes no arguments" >&2
  exit 1
fi
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  echo "FATAL: no BWS_ACCESS_TOKEN: the follow-up-mint lane runs only in its container, through lane-run" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_outcome_begin "$CHECK_NAME" follow-up-mint

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"
# `sds_bws_value` with one argument takes the lane's token from the environment. An empty value
# fails closed here, before any call: the CLI would otherwise send an empty bearer.
ORCHESTRATOR_API_TOKEN="$(sds_bws_value "$ORCHESTRATOR_SYSTEM_UUID")"
[ -n "$ORCHESTRATOR_API_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the orchestrator system credential from BWS (the lane's token), or it is empty"
# The CLI needs no BWS identity, so the lane's token never reaches it.
unset BWS_ACCESS_TOKEN
ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-system"
export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN

# The key form infra-drift's Node CLI sends; the route accepts it and never reads it.
IDEMPOTENCY_KEY="follow-up-mint:$(date -u +%Y-%m-%dT%H:%M:%SZ)"
"$REPO_ROOT/.venv/bin/orchestrator" mint-follow-ups --idempotency-key "$IDEMPOTENCY_KEY" --json \
    > "$SDS_OUTCOME_REPORT" 2>&1
rc=$?
# A code outside {0,1} is the CLI dying in a way it doesn't describe; report it as the pass failing
# rather than folding it to 0.
case "$rc" in
  0|1) ;;
  *) echo "FATAL: orchestrator mint-follow-ups exited $rc" >> "$SDS_OUTCOME_REPORT"; rc=1 ;;
esac
sds_outcome_end "$rc"
