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
# NOTHING HERE REMEDIATES. No image is re-pointed, no commit reverted and no application
# redeployed. A failed rollout produces a row and an exit code. A change record's state moves only
# through a stated fact: the observation that settles it, or the later green rollout that retires
# it (ADR-0022 amendment 1), with change-manager choosing the outcome.
#
# TWO HOSTS, ONE LAUNCHER (ADR-0059 increment 3). A set `BWS_ACCESS_TOKEN` means the lane's
# container: `lane-run` passes the lane's own token as its one secret, and both bearers are read
# with it, in the environment, never as a process argument. There the GitHub credential is an
# installation token minted each pass from the read-only App `alobar-sds-lanes-read`, narrowed to
# the repositories `ROLLOUT_WORKFLOWS` names and the four read permissions the watcher's calls use,
# and `gh` is never asked.
# Without it this is the Mac's launchd job, unchanged until increment 6: two Keychain identities
# and the operator's `gh` login. Either way the BWS token is unset before the watcher runs, which
# needs neither it nor the App's key.
#
# ACTIVATION is the Mac's: the container runs the image's own code and has no
# `activate-checkout.sh`, so there it is skipped rather than logged as missing.
#
# Usage:
#   scripts/run-deploy-watcher.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-deploy-watcher-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
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
# The read-only lane App `alobar-sds-lanes-read`: its key (base64 of the PEM) and its id, no secret.
READ_APP_KEY_UUID="314a966c-5654-4e3c-8358-b4fd01268775"
READ_APP_ID="5684758"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ACTIVATION — a merged change is not live on this machine until the code is pulled. The estate's
# Dependabot cascade stops at the merge, which is complete for a repository whose landing redeploys
# a hosted application and incomplete for this one, whose code runs from a working copy here
# (orchestrator ADR-0031). Best-effort by construction: the helper prints one `[activation]` line
# and returns 0 whatever it finds, so this job is never gated on being able to update itself. It
# re-execs this script when HEAD moves, because bash reads a script incrementally by byte offset
# and the file it just rewrote is this one.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
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
fi

# THE DEAD-MAN SWITCH. `launchd` discards this script's exit code, so the codes documented above
# reach nobody: a pass that stops running, or that fails every hour, is silent. Armed AFTER
# activation, because `activate_checkout` may `exec` and an `exec` does not fire an EXIT handler.
# It reports and never gates -- every failure inside it logs a line and returns 0.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-deploy-watcher --finding 2 "$@"


# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe,
# which breaks the parse below.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

# IN THE CONTAINER, ONE BWS IDENTITY: the lane's own token, already in the environment, reads both
# bearers (ADR-0059 decision 2: one lane, one token).
#
# ON THE MAC, TWO BWS IDENTITIES, AND NEITHER CAN DO THE OTHER'S HALF. Measured 2×2 with controls
# on 2026-08-13: the change-manager bearer lives in a project only the BROAD machine account can
# read, and the orchestrator's observer bearer in one only the narrow `sds-operator` account can. A
# launcher bootstrapping with either alone dies on the other's fetch -- which is exactly the shape
# `run-estate-landing.sh` already carries, and `infraops-mcp-server/scripts/drift-audit.sh` is the
# in-estate precedent for overriding the identity for a single foreign call.
#
# **ON THE MAC NEITHER IDENTITY IS TAKEN FROM `BWS_ACCESS_TOKEN`, and that is the correction rather
# than a style choice.** Both Keychain items are read DIRECTLY. A first version took the broad one
# from `${BWS_ACCESS_TOKEN:-…}` and the narrow one by sourcing `sds-token.sh`, which respects an
# already-set `BWS_ACCESS_TOKEN` -- so a single ambient value became BOTH identities and NO value
# of it worked. Found by two independent reviewers. A set `BWS_ACCESS_TOKEN` now means the
# container, never a Mac identity. Overrides stay available, per credential, by name.
if [ -n "${BWS_ACCESS_TOKEN:-}" ]; then
  IN_CONTAINER=1
  _change_manager_secret() { sds_bws_value "$1"; }
  _observer_secret() { sds_bws_value "$1"; }
else
  IN_CONTAINER=0
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
  _change_manager_secret() { sds_bws_value "$1" "$BROAD_IDENTITY"; }
  _observer_secret() { sds_bws_value "$1" "$NARROW_IDENTITY"; }
fi

if [ -z "${DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN:-}" ]; then
  DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN="$(_change_manager_secret "$CHANGE_MANAGER_M2M_UUID")"
  export DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN
fi
if [ -z "${DEPLOY_WATCHER_ORCHESTRATOR_TOKEN:-}" ]; then
  DEPLOY_WATCHER_ORCHESTRATOR_TOKEN="$(_observer_secret "$ORCHESTRATOR_OBSERVER_UUID")"
  export DEPLOY_WATCHER_ORCHESTRATOR_TOKEN
fi
unset BROAD_IDENTITY NARROW_IDENTITY

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

if [ "$IN_CONTAINER" -eq 1 ]; then
  # THE REPOSITORIES ARE THE ROLLOUT TABLE'S, NOT LISTED HERE: `deploy-watcher repositories` prints
  # the keys of `ROLLOUT_WORKFLOWS`, the repositories the watcher reads (a record naming any other
  # is refused before a read). AN UNDERIVABLE LIST MINTS NOTHING, AND SAYS SO: an empty table
  # exits 3 with nothing on stdout, and the empty-list check below exits 3, so the hour reads as
  # incomplete rather than clean. Any other non-zero code is the tool failing (1) or not running at
  # all (126, 127, 137), and is passed through verbatim.
  REPOSITORIES="$(env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/deploy-watcher" repositories)"
  derive_rc=$?
  if [ "$derive_rc" -ne 0 ] && [ "$derive_rc" -ne 3 ]; then
    echo "the watcher's repositories could not be derived: deploy-watcher repositories" \
         "exited $derive_rc" >&2
    exit "$derive_rc"
  fi
  if [ "$derive_rc" -ne 0 ] || [ -z "$REPOSITORIES" ]; then
    echo "the watcher's repositories could not be derived; nothing was minted or watched" >&2
    exit 3
  fi
  TARGETS=()
  while IFS= read -r repository; do
    TARGETS+=(--repository "$repository")
  done <<< "$REPOSITORIES"

  # THE GITHUB TOKEN IS MINTED EACH PASS, after the derivation, because the mint names its
  # repositories. The App's key goes from the BWS read straight to the mint's stdin in its stored
  # form, base64 of the PEM; it is never in a variable, an argument or a file, and the mint runs
  # without the lane's token. The four permissions are what the watcher's GET calls use
  # (`src/deploy_watcher/github.py`): the repository, commits, compare and workflow file
  # (`metadata`, `contents`), pull requests (`pull_requests`), and workflows, runs and jobs
  # (`actions`). Any preset value is replaced, so the pass runs only on the token it minted.
  DEPLOY_WATCHER_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
    | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
        --app-id "$READ_APP_ID" "${TARGETS[@]}" \
        --permission metadata=read --permission contents=read \
        --permission pull_requests=read --permission actions=read)"
  mint_rc=$?
  export DEPLOY_WATCHER_GITHUB_TOKEN
  if [ "$mint_rc" -ne 0 ] || [ -z "$DEPLOY_WATCHER_GITHUB_TOKEN" ]; then
    echo "FATAL: could not mint the watcher's GitHub token from the read-only App" >&2
    exit 1
  fi
else
  # ON THE MAC THE GITHUB CREDENTIAL IS THE OPERATOR'S `gh` LOGIN, until increment 6 retires this
  # path: a scheduled job resting on an interactive login breaks the moment the login is re-issued,
  # and nothing would say so but this script's exit 1. The container never takes it (above).
  if [ -z "${DEPLOY_WATCHER_GITHUB_TOKEN:-}" ]; then
    DEPLOY_WATCHER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
    export DEPLOY_WATCHER_GITHUB_TOKEN
  fi
  if [ -z "${DEPLOY_WATCHER_GITHUB_TOKEN:-}" ]; then
    echo "FATAL: no GitHub token (set DEPLOY_WATCHER_GITHUB_TOKEN or run gh auth login)" >&2
    exit 1
  fi
fi
unset BWS_ACCESS_TOKEN

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
