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
# ADR-0022. The orchestrator's OBSERVER bearer, whose entire write surface is
# `POST /api/v1/observations` -- the same credential the landing ledger holds, deliberately: an
# observation row carries `source_system` and `source_reference`, so the ROW says who spoke and the
# credential does not have to.
ORCHESTRATOR_OBSERVER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer

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
# reach nobody: a pass that stops running, or that fails every hour, is silent. Armed AFTER
# activation, because `activate_checkout` may `exec` and an `exec` does not fire an EXIT handler.
# It reports and never gates -- every failure inside it logs a line and returns 0.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-deploy-watcher "$@"


# TWO BWS IDENTITIES, AND NEITHER CAN DO THE OTHER'S HALF. Measured 2×2 with controls on
# 2026-08-13: the change-manager bearer lives in a project only the BROAD machine account can read,
# and the orchestrator's observer bearer in one only the narrow `sds-operator` account can. A
# launcher bootstrapping with either alone dies on the other's fetch -- which is exactly the shape
# `run-estate-landing.sh` already carries, and `infraops-mcp-server/scripts/drift-audit.sh` is the
# in-estate precedent for overriding the identity for a single foreign call.
#
# **NEITHER IDENTITY IS TAKEN FROM `BWS_ACCESS_TOKEN`, and that is the correction rather than a
# style choice.** Both Keychain items are read DIRECTLY. A first version took the broad one from
# `${BWS_ACCESS_TOKEN:-…}` and the narrow one by sourcing `sds-token.sh`, which respects an
# already-set `BWS_ACCESS_TOKEN` -- so a single ambient value became BOTH identities and NO value
# of it worked: exported broad, the observer fetch is denied; exported narrow, the change-manager
# fetch is denied. Under launchd nothing is exported and it worked; the shell an operator debugs
# this job from is exactly the shell that has one exported, and the failure names BWS rather than
# the cause. Found by two independent reviewers. Overrides stay available, per credential, by name.
BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
if [ -z "$BROAD_IDENTITY" ]; then
  echo "FATAL: no BWS identity for the change-manager credential (Keychain service Claude," \
       "account BWS_ACCESS_TOKEN_VPS_BACKUP)" >&2
  exit 1
fi
NARROW_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
if [ -z "$NARROW_IDENTITY" ]; then
  echo "FATAL: no BWS identity for the orchestrator observer credential (Keychain service Claude," \
       "account BWS_ACCESS_TOKEN_SDS)" >&2
  exit 1
fi

# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe,
# which breaks the parse below.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE BWS_ACCESS_TOKEN="$2" \
    bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

if [ -z "${DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN:-}" ]; then
  DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN="$(_bws_value "$CHANGE_MANAGER_M2M_UUID" "$BROAD_IDENTITY")"
  export DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN
fi
if [ -z "${DEPLOY_WATCHER_ORCHESTRATOR_TOKEN:-}" ]; then
  DEPLOY_WATCHER_ORCHESTRATOR_TOKEN="$(_bws_value "$ORCHESTRATOR_OBSERVER_UUID" "$NARROW_IDENTITY")"
  export DEPLOY_WATCHER_ORCHESTRATOR_TOKEN
fi

# `set -e` is deliberately not used, so a failed fetch would otherwise leave these EMPTY and fall
# through into the program's own "variable is not set" exit -- which reports the wrong cause. Name
# each here, so the exit code means what this header says it means.
if [ -z "${DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN:-}" ]; then
  echo "FATAL: could not read the change-manager credential from BWS" >&2
  exit 1
fi
if [ -z "${DEPLOY_WATCHER_ORCHESTRATOR_TOKEN:-}" ]; then
  echo "FATAL: could not read the orchestrator observer credential from BWS" >&2
  exit 1
fi

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

# AN OUT-OF-VOCABULARY CODE IS REPORTED VERBATIM, and this branch must come FIRST. The fold below
# ranks the four codes this program can return; anything else -- 126 for a program that is not
# executable, 127 for one that is missing, 137 for one that was killed -- matches none of them and
# used to fall through to `exit 0`, turning the loudest possible failure into the quietest possible
# answer. That was survivable while nothing subscribed to the exit code. It is not survivable now
# that the dead-man switch reports on it: a missing binary would ping success.
for rc in "$watch_rc" "$recheck_rc"; do
  case "$rc" in
    0 | 1 | 2 | 3) ;;
    *) exit "$rc" ;;
  esac
done

# The worst answer wins, and "could not measure" is worse than "found something".
for rc in 1 3 2; do
  if [ "$watch_rc" -eq "$rc" ] || [ "$recheck_rc" -eq "$rc" ]; then
    exit "$rc"
  fi
done
exit 0
