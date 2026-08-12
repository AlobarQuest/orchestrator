#!/usr/bin/env bash
# One rollout-watcher pass, then one re-check of what it has recorded (ADR-0019 increment 2).
#
# WATCH FIRST, RECHECK SECOND. `watch` appends observations; `recheck` re-derives every stored
# one from GitHub and reports where they no longer agree. Running the re-check first would
# re-derive a ledger that has already moved.
#
# WHY THE RE-CHECK EXISTS AT ALL, since it will almost always find nothing. change-manager has no
# GitHub egress, so an observation is ASSERTED by the watcher and the server cannot verify it;
# every /api route shares one static bearer and `actor` is caller-declared free text, so the
# server cannot tell a watcher from anything else holding that secret either. Per-caller identity
# would fix that and is not built. Making the assertion re-derivable, and then re-deriving it, is
# the honest option that is available.
#
# EXIT CODES, and they are the whole interface a scheduled run has:
#   0  everything was measured and nothing was found.
#   1  the tool itself failed (a missing credential, an unhandled error).
#   2  something was found. The pass worked; reality did not.
#   3  some part of reality could not be read, so the answer is missing rather than clean.
# 3 outranks 2: an incomplete pass cannot claim it found everything there was to find.
#
# NOTHING HERE ACTS. No image is re-pointed, no commit reverted, no application redeployed, and
# no change record's state is moved. A failed rollout produces a row and an exit code.
#
# Usage:
#   scripts/run-deploy-watcher.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-deploy-watcher-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo).
# The OBSERVE-scoped bearer, minted 2026-08-12. Until then this job held
# change-manager/M2M_TOKEN -- the FULL credential -- because the observe scope increment 4
# shipped had never been given a value, so the narrowing existed on paper and nowhere else.
# The scope reaches the four read routes plus this job's one write; every route by which a
# record's status could be chosen answers 403.
CHANGE_MANAGER_M2M_UUID="3b9503da-eb7e-401d-b4a7-b4a400c07efb"   # change-manager/M2M_TOKEN_OBSERVE

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The change-manager token lives in the `Ops / Platform` BWS project, which the narrow
# `sds-operator` account behind scripts/sds-token.sh cannot read -- so this launcher bootstraps
# with the broad machine account instead. Named rather than silently different: it is a WIDER
# identity than the other launchers in this directory use, and narrowing it is open work.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  BWS_ACCESS_TOKEN="$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)"
  export BWS_ACCESS_TOKEN
fi
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  echo "FATAL: BWS_ACCESS_TOKEN not found in Keychain (service Claude)" >&2
  exit 1
fi

# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe,
# which breaks the parse below.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN="$(_bws_value "$CHANGE_MANAGER_M2M_UUID")"
export DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN

# THE GITHUB CREDENTIAL HAS NO BWS RECORD, exactly as the landing ledger's does not. It falls
# back to `gh auth token`, which is an interactive login: a scheduled job resting on one breaks
# the moment the login is re-issued, and nothing would say so but this script's exit 1. That is a
# gap, not a design.
if [ -z "${DEPLOY_WATCHER_GITHUB_TOKEN:-}" ]; then
  DEPLOY_WATCHER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
  export DEPLOY_WATCHER_GITHUB_TOKEN
fi
if [ -z "${DEPLOY_WATCHER_GITHUB_TOKEN:-}" ]; then
  echo "FATAL: no GitHub token (set DEPLOY_WATCHER_GITHUB_TOKEN or run gh auth login)" >&2
  exit 1
fi

"$REPO_ROOT/.venv/bin/deploy-watcher" watch "$@"
watch_rc=$?

"$REPO_ROOT/.venv/bin/deploy-watcher" recheck
recheck_rc=$?

# The worst answer wins, and "could not measure" is worse than "found something".
for rc in 1 3 2; do
  if [ "$watch_rc" -eq "$rc" ] || [ "$recheck_rc" -eq "$rc" ]; then
    exit "$rc"
  fi
done
exit 0
