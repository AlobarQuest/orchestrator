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
#   2  something was found -- an application is behind its branch or serving something not on it.
#      An acting pass has told a person: one keyed alert per application
#      (`revision-stale:<application>`), resolved when a later pass reads it current. An
#      application running and answering with a revision while absent from the declared table is
#      reported and is NOT a finding: it may not be the SDS's, so it raises nothing.
#   3  some application could not be read, a row could not be filed, or the declared table could
#      not be policed, so the answer is missing rather than clean. ALSO the code for an acting pass
#      whose alert-hub send failed or whose alert state file was unusable (after the pass's own
#      work), so a failed send cannot hide a finding.
# 3 outranks 2: an incomplete pass cannot claim it found everything there was to find. A broken
# tool and an honest finding sharing one code is a collision this estate has already paid for.
#
# THE ACTING PASS TELLS A PERSON: any pass without `--dry-run` (the plist passes no arguments). It
# fetches the `sds-bridge` alert token through the narrow identity, as SDS_BRIDGE_ALERT_TOKEN, and
# exits 1 when it cannot read it. A `--dry-run` pass sends nothing and fetches no alert token.
#
# NOTHING HERE ACTS. It asks applications what they serve, asks GitHub what a branch names, and
# files one observation per application. It deploys nothing, restarts nothing and merges nothing;
# correcting a stale application stays a person's decision.
#
# TWO HOSTS, ONE LAUNCHER (ADR-0059 increment 3). A set `BWS_ACCESS_TOKEN` means the lane's
# container: `lane-run` passes the lane's own token as its one secret, and every secret below is
# read with it, in the environment, never as a process argument. There the GitHub credential is an
# installation token minted each pass from the read-only App `alobar-sds-lanes-read`, narrowed to
# the repositories of the member record's apps and the two read permissions the watcher's calls
# use, and `gh` is never asked.
# Without it this is the Mac's launchd job, unchanged until increment 6: two Keychain identities
# and the operator's `gh` login. Either way the BWS token is unset before the watcher runs, which
# needs neither it nor the App's key.
#
# ACTIVATION is the Mac's: the container runs the image's own code and has no
# `activate-checkout.sh`, so there it is skipped rather than logged as missing.
#
# Usage:
#   scripts/run-revision-watcher.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-revision-watcher-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER bearer
PLATFORM_TOKEN_UUID="bbd71f41-b7df-4ae9-8fdb-b41501447308"    # hosting-platform API token (can write envs; this script only lists apps)
ESTATE_READ_KEY_UUID="726a18ba-7a38-4ecc-aa03-b49a015fd302"   # App Brain READ-ONLY key
SDS_BRIDGE_ALERT_UUID="${REVISION_WATCHER_ALERT_BWS_UUID:-b415d03b-f92d-46b1-8a9d-b4f10103df49}"  # sds-bridge alert sender
# The read-only lane App `alobar-sds-lanes-read`: its key (base64 of the PEM) and its id, no secret.
READ_APP_KEY_UUID="314a966c-5654-4e3c-8358-b4fd01268775"
READ_APP_ID="5684758"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ACTIVATION — a merged change is not live on this machine until the code is pulled, because these
# launchers run the working copy. Best-effort by construction: the helper prints one `[activation]`
# line and returns 0 whatever it finds, so this job is never gated on being able to update itself.
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

# THE DEAD-MAN SWITCH. `launchd` discards this script's exit code, so the codes above reach nobody:
# a pass that stops running is silent. Armed AFTER activation, because `activate_checkout` may
# `exec` and an `exec` does not fire an EXIT handler. Note what it does NOT do: a declared finding
# code pings the check SUCCESS, so exit 2 never pages. The check answers "is this lane alive"; the
# finding's durable home is the observation the pass files, and the acting pass also tells a person
# through a keyed alert (see the header).
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-revision-watcher --finding 2 "$@"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

# IN THE CONTAINER, ONE BWS IDENTITY: the lane's own token, already in the environment, reads every
# secret this pass needs (ADR-0059 decision 2: one lane, one token).
#
# ON THE MAC, TWO BWS IDENTITIES, AND NEITHER CAN READ THE OTHER'S SECRET. Measured 2026-09-08, both
# directions: the narrow `sds-operator` account reads the observer bearer and is DENIED the
# platform token; the broad account reads the platform token and is DENIED the observer bearer.
# So they are named separately and each fetch says which one it used. Both Keychain items are read
# DIRECTLY: a set `BWS_ACCESS_TOKEN` means the container, never a Mac identity, and one ambient
# value serving as both would make NO value of it work. Overrides stay available by name
# (`BWS_ACCESS_TOKEN_SDS`, `BWS_ACCESS_TOKEN_BROAD`).
if [ -n "${BWS_ACCESS_TOKEN:-}" ]; then
  IN_CONTAINER=1
  _narrow_secret() { sds_bws_value "$1"; }
  _broad_secret() { sds_bws_value "$1"; }
  NARROW_NAME="the lane's token"
  BROAD_NAME="the lane's token"
else
  IN_CONTAINER=0
  SDS_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
  if [ -z "$SDS_IDENTITY" ]; then
    echo "FATAL: no BWS identity for the orchestrator credential (Keychain service Claude," \
         "account BWS_ACCESS_TOKEN_SDS)" >&2
    exit 1
  fi
  BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
  if [ -z "$BROAD_IDENTITY" ]; then
    echo "FATAL: no BWS identity for the platform credential (Keychain service Claude)" >&2
    exit 1
  fi
  _narrow_secret() { sds_bws_value "$1" "$SDS_IDENTITY"; }
  _broad_secret() { sds_bws_value "$1" "$BROAD_IDENTITY"; }
  NARROW_NAME="narrow identity"
  BROAD_NAME="broad identity"
fi

ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
ORCHESTRATOR_API_TOKEN="$(_narrow_secret "$OBSERVER_BEARER_UUID")"
export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN

REVISION_WATCHER_PLATFORM_URL="${REVISION_WATCHER_PLATFORM_URL:-http://coolify-1.devonwatkins.com}"
REVISION_WATCHER_PLATFORM_TOKEN="$(_broad_secret "$PLATFORM_TOKEN_UUID")"
export REVISION_WATCHER_PLATFORM_URL REVISION_WATCHER_PLATFORM_TOKEN

# WHAT BEING BEHIND MEANS, which is a different question from whether an application IS behind.
# The estate's own record says whether landing on a repository's default branch redeploys it; for
# one where it does, a gap means something went wrong, and for one where merge and deploy are
# separate tracks a gap is the queue. On the Mac, the same broad identity as the platform token —
# measured 2026-09-08, the narrow `sds-operator` account cannot read this project either.
REVISION_WATCHER_ESTATE_URL="${REVISION_WATCHER_ESTATE_URL:-https://app-brain.devonwatkins.com}"
REVISION_WATCHER_ESTATE_KEY="$(_broad_secret "$ESTATE_READ_KEY_UUID")"
export REVISION_WATCHER_ESTATE_URL REVISION_WATCHER_ESTATE_KEY

# `set -e` is deliberately not used, so a failed fetch would otherwise leave these EMPTY and fall
# through -- the observer bearer into an exit 1 that says the credential is unconfigured, which is
# right, and the platform token into an exit 3 that says coverage went unmeasured, which is also
# right but says the platform was unreachable when in fact the secret was. Named here so each exit
# code means what this header says it means.
if [ -z "$ORCHESTRATOR_API_TOKEN" ]; then
  echo "FATAL: could not read the observer bearer from BWS ($NARROW_NAME)" >&2
  exit 1
fi
if [ -z "$REVISION_WATCHER_PLATFORM_TOKEN" ]; then
  echo "FATAL: could not read the platform token from BWS ($BROAD_NAME)" >&2
  exit 1
fi
if [ -z "$REVISION_WATCHER_ESTATE_KEY" ]; then
  echo "FATAL: could not read the App Brain read key from BWS ($BROAD_NAME)" >&2
  exit 1
fi

# Acting means no `--dry-run`, the same term the program applies.
_revision_is_dry_run() {
  for argument in "$@"; do
    [ "$argument" = "--dry-run" ] && return 0
  done
  return 1
}
if ! _revision_is_dry_run "$@"; then
  SDS_BRIDGE_ALERT_TOKEN="$(_narrow_secret "$SDS_BRIDGE_ALERT_UUID")"
  export SDS_BRIDGE_ALERT_TOKEN
  if [ -z "${SDS_BRIDGE_ALERT_TOKEN:-}" ]; then
    echo "FATAL: could not read the sds-bridge alert sender token from BWS ($NARROW_NAME)." >&2
    exit 1
  fi
fi
unset SDS_IDENTITY BROAD_IDENTITY

if [ "$IN_CONTAINER" -eq 1 ]; then
  # THE REPOSITORIES ARE THE MEMBER RECORD'S, NOT LISTED HERE: `sds_scope apps` prints the
  # repositories of the live members' apps, the same record and the same population the watcher's
  # subjects come from (`src/revision_watcher/subjects.py`), each once. AN UNDERIVABLE LIST MINTS
  # NOTHING, AND SAYS SO: an unreadable record or an empty population exits 3 with nothing on
  # stdout, and the empty-list check below exits 3, so the hour reads as incomplete rather than
  # clean. Any other non-zero code is the tool failing (1) or not running at all (126, 127, 137),
  # and is passed through verbatim.
  REPOSITORIES="$(cd "$REPO_ROOT" && SDS_SCOPE_ORCHESTRATOR_TOKEN="$ORCHESTRATOR_API_TOKEN" \
    env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/python" -m scripts.sds_scope apps)"
  scope_rc=$?
  if [ "$scope_rc" -ne 0 ] && [ "$scope_rc" -ne 3 ]; then
    echo "the watcher's repositories could not be derived: sds_scope exited $scope_rc" >&2
    exit "$scope_rc"
  fi
  if [ "$scope_rc" -ne 0 ] || [ -z "$REPOSITORIES" ]; then
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
  # without the lane's token. The two permissions are what the watcher's GET calls use
  # (`src/revision_watcher/github.py`): a branch, a commit and a compare, all `contents`, with the
  # `metadata` GitHub grants every token. Any preset value is replaced, so the pass runs only on
  # the token it minted.
  REVISION_WATCHER_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
    | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
        --app-id "$READ_APP_ID" "${TARGETS[@]}" \
        --permission metadata=read --permission contents=read)"
  mint_rc=$?
  export REVISION_WATCHER_GITHUB_TOKEN
  if [ "$mint_rc" -ne 0 ] || [ -z "$REVISION_WATCHER_GITHUB_TOKEN" ]; then
    echo "FATAL: could not mint the watcher's GitHub token from the read-only App" >&2
    exit 1
  fi
else
  # ON THE MAC THE GITHUB CREDENTIAL IS THE OPERATOR'S `gh` LOGIN, until increment 6 retires this
  # path: a scheduled job resting on an interactive login breaks the moment that login is
  # re-issued, and nothing would say so but this script's exit 1. The container never takes it
  # (above).
  if [ -z "${REVISION_WATCHER_GITHUB_TOKEN:-}" ]; then
    REVISION_WATCHER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
    export REVISION_WATCHER_GITHUB_TOKEN
  fi
  if [ -z "${REVISION_WATCHER_GITHUB_TOKEN:-}" ]; then
    echo "FATAL: no GitHub credential. Set REVISION_WATCHER_GITHUB_TOKEN, or authenticate gh." >&2
    exit 1
  fi
fi
unset BWS_ACCESS_TOKEN

"$REPO_ROOT/.venv/bin/revision-watcher" "$@"
rc=$?

# THE CONDITION HAS NO CHECK OF ITS OWN. A finding's durable home is the `production_revision`
# observation each pass files; `sds-revision-watcher` answers only whether this lane ran, and pings
# success on a finding. The keyed alert is what tells a person.

# A code outside {0,1,2,3} is the program dying in a way it does not describe -- a missing binary
# is 127. Reported as 1 (the tool failed) rather than folded to 0, which is what several sibling
# launchers used to do.
case "$rc" in
  0|1|2|3) exit "$rc" ;;
  *) echo "FATAL: revision-watcher exited $rc" >&2; exit 1 ;;
esac
