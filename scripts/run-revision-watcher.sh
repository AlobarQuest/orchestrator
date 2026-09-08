#!/usr/bin/env bash
# One revision-watcher pass: is every deployed application serving the commit its branch names?
#
# WHAT THIS EXISTS BECAUSE OF. Three surfaces report a rollout that went WRONG -- the target
# repository's own CI, the deploy watcher, and the landing ledger's `default_branch_not_green` --
# and every one of them reports a MOMENT. On 2026-09-06 all three fired correctly and `app-brain`
# served a build behind `main` for a day and a half anyway, because a moment that has passed is
# not a condition anyone can still see. This lane reports the CONDITION, and it is
# cause-independent: a deployment that failed and rolled back, an image that never built, a
# rotated webhook secret and a manual swap nobody performed all end in the same measurable state.
#
# EXIT CODES, and they are the whole interface a scheduled run has:
#   0  everything was measured and every application serves what its branch names.
#   1  the tool itself failed (a missing credential, an unusable URL).
#   2  something was found -- an application is behind its branch, serving something not on it,
#      or running and answering with a revision while absent from the declared table.
#   3  some application could not be read, a row could not be filed, or the declared table could
#      not be policed, so the answer is missing rather than clean.
# 3 outranks 2: an incomplete pass cannot claim it found everything there was to find. A broken
# tool and an honest finding sharing one code is a collision this estate has already paid for.
#
# NOTHING HERE ACTS. It asks applications what they serve, asks GitHub what a branch names, and
# files one observation per application. It deploys nothing, restarts nothing and merges nothing;
# correcting a stale application stays a person's decision.
#
# Usage:
#   scripts/run-revision-watcher.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-revision-watcher-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER bearer
PLATFORM_TOKEN_UUID="bbd71f41-b7df-4ae9-8fdb-b41501447308"    # read-only hosting-platform API token

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ACTIVATION — a merged change is not live on this machine until the code is pulled, because these
# launchers run the working copy. Best-effort by construction: the helper prints one `[activation]`
# line and returns 0 whatever it finds, so this job is never gated on being able to update itself.
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

# THE DEAD-MAN SWITCH. `launchd` discards this script's exit code, so the codes above reach nobody:
# a pass that stops running is silent. Armed AFTER activation, because `activate_checkout` may
# `exec` and an `exec` does not fire an EXIT handler. Note what it does NOT do: a declared finding
# code pings the check SUCCESS, so exit 2 never pages. The check answers "is this lane alive"; the
# finding's durable home is the observation the pass files. Whether a standing finding HERE should
# reach a person by some other route is an open decision and is deliberately not taken in this file.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-revision-watcher --finding 2 "$@"

# TWO BWS IDENTITIES, AND NEITHER CAN READ THE OTHER'S SECRET. Measured 2026-09-08, both
# directions: the narrow `sds-operator` account reads the observer bearer and is DENIED the
# platform token; the broad account reads the platform token and is DENIED the observer bearer.
# So they are named separately and each fetch says which one it used. Sourcing `sds-token.sh`
# alongside a `${BWS_ACCESS_TOKEN:-…}` default would make one ambient value serve as both, and
# then NO value of it works -- a failure that appears only in an operator's shell, never under
# launchd, and that names BWS rather than the cause.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE BWS_ACCESS_TOKEN="$2" \
    bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

# SOURCED, not executed. `sds-token.sh` EXPORTS the value and prints nothing, so command-
# substituting it yields the empty string and every fetch below fails with a message about a
# missing environment variable rather than about a missing token.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/scripts/sds-token.sh"
fi
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  echo "FATAL: BWS_ACCESS_TOKEN not available for the orchestrator credential" >&2
  exit 1
fi
SDS_IDENTITY="$BWS_ACCESS_TOKEN"

BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
if [ -z "$BROAD_IDENTITY" ]; then
  echo "FATAL: no BWS identity for the platform credential (Keychain service Claude)" >&2
  exit 1
fi

ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
ORCHESTRATOR_API_TOKEN="$(_bws_value "$OBSERVER_BEARER_UUID" "$SDS_IDENTITY")"
export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN

REVISION_WATCHER_PLATFORM_URL="${REVISION_WATCHER_PLATFORM_URL:-http://coolify-1.devonwatkins.com}"
REVISION_WATCHER_PLATFORM_TOKEN="$(_bws_value "$PLATFORM_TOKEN_UUID" "$BROAD_IDENTITY")"
export REVISION_WATCHER_PLATFORM_URL REVISION_WATCHER_PLATFORM_TOKEN

# `set -e` is deliberately not used, so a failed fetch would otherwise leave these EMPTY and fall
# through -- the observer bearer into an exit 1 that says the credential is unconfigured, which is
# right, and the platform token into an exit 3 that says coverage went unmeasured, which is also
# right but says the platform was unreachable when in fact the secret was. Named here so each exit
# code means what this header says it means.
if [ -z "$ORCHESTRATOR_API_TOKEN" ]; then
  echo "FATAL: could not read the observer bearer from BWS (narrow identity)" >&2
  exit 1
fi
if [ -z "$REVISION_WATCHER_PLATFORM_TOKEN" ]; then
  echo "FATAL: could not read the platform token from BWS (broad identity)" >&2
  exit 1
fi

# THE GITHUB CREDENTIAL HAS NO BWS RECORD, the same gap the landing ledger and the pin watcher
# carry and for the same reason: this reads public repository metadata and has never needed a
# dedicated identity. A scheduled job resting on an interactive login breaks the moment that login
# is re-issued, and nothing would say so but this script's exit 1.
if [ -z "${REVISION_WATCHER_GITHUB_TOKEN:-}" ]; then
  REVISION_WATCHER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
  export REVISION_WATCHER_GITHUB_TOKEN
fi
if [ -z "${REVISION_WATCHER_GITHUB_TOKEN:-}" ]; then
  echo "FATAL: no GitHub credential. Set REVISION_WATCHER_GITHUB_TOKEN, or authenticate gh." >&2
  exit 1
fi

"$REPO_ROOT/.venv/bin/revision-watcher" "$@"
rc=$?

# A code outside {0,1,2,3} is the program dying in a way it does not describe -- a missing binary
# is 127. Reported as 1 (the tool failed) rather than folded to 0, which is what several sibling
# launchers used to do.
case "$rc" in
  0|1|2|3) exit "$rc" ;;
  *) echo "FATAL: revision-watcher exited $rc" >&2; exit 1 ;;
esac
