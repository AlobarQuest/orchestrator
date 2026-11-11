#!/usr/bin/env bash
# One verifier pass: run `/verify` on every submitted factory unit (Verification Part 2).
#
# The verifier decides every criterion, and a unit it can't prove goes to `awaiting_review` for a
# person.
#
# EXIT CODES:
#   0  every submitted unit completed or is waiting on a check, or none was submitted.
#   1  the tool itself failed (a missing credential, an unusable URL).
#   2  something was found: a unit went to a person or an alert names it.
#   3  the queue could not be read, or alert-hub could not be told.
#      In the container 3 also follows a pass that ran with no GitHub token, because the token's
#      repositories could not be derived or its mint failed.
#
# A unit the lane couldn't verify is a keyed alert (`src/unit_verifier/alerts.py`). 2 is the
# finding code, so that cause pings success and raises the alert, never both. 3 pings `/fail`: it is
# what's left when an alert can't carry the news.
#
# Bump and app-change units have the repository's required checks filed as named-check evidence,
# read from branch protection. App-change units read the member record with the same VERIFIER
# bearer.
#
# TWO HOSTS, ONE LAUNCHER (ADR-0059 increment 3). A set `BWS_ACCESS_TOKEN` means the lane's
# container: `lane-run` passes the lane's own token as its one secret, and every secret below is
# read with it, in the environment, never as a process argument. There the GitHub credential is an
# installation token minted each pass from the read-only App `alobar-sds-lanes-read`, narrowed to
# the repositories `unit-verifier repositories` derives from the member record and the two read
# permissions branch protection's required checks need, and `gh` is never asked. Without it this
# is the Mac's launchd job, unchanged until increment 6: the Keychain identity through
# `sds-token.sh` and the operator's `gh` login.
#
# ACTIVATION is the Mac's: the container runs the image's own code and has no
# `activate-checkout.sh`, so there it is skipped rather than logged as missing.
#
# Usage:
#   scripts/run-unit-verifier.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-unit-verifier-launchd.sh
set -uo pipefail

# BWS UUID (value fetched at runtime; never stored in this repo). See .bws-secrets.toml.
VERIFIER_BEARER_UUID="660d5846-abcb-4751-be86-b483012899eb"   # orchestrator-verifier VERIFIER bearer
SDS_BRIDGE_ALERT_UUID="b415d03b-f92d-46b1-8a9d-b4f10103df49"  # alert-hub sds-bridge sender
# The read-only lane App `alobar-sds-lanes-read`: its key (base64 of the PEM) and its id, no secret.
READ_APP_KEY_UUID="314a966c-5654-4e3c-8358-b4fd01268775"
READ_APP_ID="5684758"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ACTIVATION: these launchers run the working copy, so a merged change is live here only once it
# is pulled. Best-effort: the helper prints one `[activation]` line and returns 0 whatever it finds.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
    IN_CONTAINER=0
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
else
    IN_CONTAINER=1
fi

# THE DEAD-MAN SWITCH, armed after activation (which may `exec`). The finding code 2 pings success:
# the check answers whether this lane runs, and a unit waiting for a person is on `/review`.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-unit-verifier --finding 2 "$@"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

# SOURCED, not executed: `sds-token.sh` exports the value and prints nothing. The Mac's only: in the
# container the lane's token is already set (ADR-0059 decision 2: one lane, one token).
if [ "$IN_CONTAINER" -eq 0 ]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/scripts/sds-token.sh"
fi
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  echo "FATAL: BWS_ACCESS_TOKEN not available for the orchestrator credential" >&2
  exit 1
fi

ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-verifier"
ORCHESTRATOR_API_TOKEN="$(sds_bws_value "$VERIFIER_BEARER_UUID")"
export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN
if [ -z "$ORCHESTRATOR_API_TOKEN" ]; then
  echo "FATAL: could not read the verifier bearer from BWS" >&2
  exit 1
fi

# A FAILED FETCH NEVER STOPS A ROTATION: the pass runs without it and exits 3 once it has verified
# everything, so the check says what the alert could not.
SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID" || true)"
export SDS_BRIDGE_ALERT_TOKEN
if [ -z "$SDS_BRIDGE_ALERT_TOKEN" ]; then
  echo "WARNING: could not read the alert-hub sender credential from BWS" >&2
fi

# THE GITHUB TOKEN ONLY READS BRANCH PROTECTION, so a missing one fails closed on units with checks
# to file (each is held unverified as `verify-refused`, with its alert, and retried next pass) and
# leaves rotation units be: like the alert token, a GitHub token that can't be had never stops a
# rotation.
GITHUB_TOKEN_MISSING=0
if [ "$IN_CONTAINER" -eq 1 ]; then
  # Any preset value is dropped, so the pass runs only on the token minted here or on none.
  ROTATION_VERIFIER_GITHUB_TOKEN=""
  # THE REPOSITORIES ARE THE MEMBER RECORD'S, NOT LISTED HERE: `unit-verifier repositories` reads
  # the record with this lane's VERIFIER bearer and prints those granted `dependency_updates` or
  # `app_changes`, the grants a check-filing unit's repository is held to, each once. An unreadable
  # record or no granted repository prints nothing and exits 3.
  REPOSITORIES="$(env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/unit-verifier" repositories)"
  derive_rc=$?
  if [ "$derive_rc" -ne 0 ] || [ -z "$REPOSITORIES" ]; then
    echo "WARNING: the verifier's repositories could not be derived (exit $derive_rc);" \
         "nothing was minted and this pass runs with no GitHub token" >&2
    GITHUB_TOKEN_MISSING=1
  else
    TARGETS=()
    while IFS= read -r repository; do
      TARGETS+=(--repository "$repository")
    done <<< "$REPOSITORIES"
    # MINTED EACH PASS, after the derivation, because the mint names its repositories. The App's
    # key goes from the BWS read straight to the mint's stdin in its stored form, base64 of the PEM;
    # it is never in a variable, an argument or a file, and the mint runs without the lane's token.
    # `administration=read` is what `GET .../branches/main/protection/required_status_checks`
    # needs (`src/unit_verifier/required_checks.py`, ADR-0059 decision 2), and the mint requires
    # `metadata=read`, which GitHub grants with every token.
    ROTATION_VERIFIER_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
      | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
          --app-id "$READ_APP_ID" "${TARGETS[@]}" \
          --permission metadata=read --permission administration=read)"
    mint_rc=$?
    if [ "$mint_rc" -ne 0 ] || [ -z "$ROTATION_VERIFIER_GITHUB_TOKEN" ]; then
      echo "WARNING: could not mint the verifier's GitHub token from the read-only App" \
           "(exit $mint_rc); this pass runs with no GitHub token" >&2
      ROTATION_VERIFIER_GITHUB_TOKEN=""
      GITHUB_TOKEN_MISSING=1
    fi
  fi
  export ROTATION_VERIFIER_GITHUB_TOKEN
  unset BWS_ACCESS_TOKEN
else
  # ON THE MAC IT IS THE OPERATOR'S `gh` LOGIN, read here and never printed, until increment 6
  # retires this path. The container never takes it (above).
  if [ -z "${ROTATION_VERIFIER_GITHUB_TOKEN:-}" ]; then
    ROTATION_VERIFIER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
    export ROTATION_VERIFIER_GITHUB_TOKEN
  fi
fi

"$REPO_ROOT/.venv/bin/unit-verifier" "$@"
rc=$?

# A code outside {0,1,2,3} is the program dying in a way it doesn't describe (127 is a missing
# binary); report it as the tool failing rather than folding it to 0.
case "$rc" in
  0|1|2|3) ;;
  *) echo "FATAL: unit-verifier exited $rc" >&2; rc=1 ;;
esac
# A pass that ran without its GitHub token is incomplete, so it reports 3 and the check pings
# `/fail`, unless the tool failed (1) or the pass already reports 3.
if [ "$GITHUB_TOKEN_MISSING" -eq 1 ] && { [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]; }; then
  rc=3
fi
exit "$rc"
