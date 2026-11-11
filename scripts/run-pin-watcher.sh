#!/usr/bin/env bash
# One pin-watcher pass: does every repository the factory can dispatch to run the runner we chose?
#
# WHAT THIS EXISTS BECAUSE OF. On 2026-09-04 five of six caller workflows were pinned twenty-three
# commits behind `RECOMMENDED_CALLER_PIN`, carrying none of that week's runner fixes. Nothing
# reported it. A caller's `uses:` SHA IS the runner revision a dispatch executes, so a stale caller
# does not fail -- it runs a runner nobody chose and spends a work unit's attempt on it.
#
# EXIT CODES, and they are the whole interface a scheduled run has:
#   0  everything was measured and nothing was found.
#   1  the tool itself failed (a missing credential, an unusable URL).
#   2  something was found -- a caller is not at the recommendation. The pass worked; the estate
#      did not. An acting pass has told a person: one keyed alert per repository
#      (`pin-behind:<repository>`), resolved when a later pass finds the caller at the pin.
#   3  some caller could not be read, or its row could not be filed, so the answer is missing
#      rather than clean. ALSO the code for an acting pass whose alert-hub send failed or whose
#      alert state file was unusable (after the pass's own work), so a failed send cannot hide a
#      finding.
# 3 outranks 2: an incomplete pass cannot claim it found everything there was to find. A broken
# tool and an honest finding sharing one code is a collision this estate has already paid for.
#
# THE ACTING PASS TELLS A PERSON: any pass without `--dry-run` (the plist passes no arguments). It
# fetches the `sds-bridge` alert token through the narrow identity, as SDS_BRIDGE_ALERT_TOKEN, and
# exits 1 when it cannot read it. A `--dry-run` pass sends nothing and fetches no alert token.
#
# NOTHING HERE ACTS. It reads GitHub and files one observation per caller. It changes no workflow,
# no pin and no repository; advancing a pin stays a person's one-line pull request.
#
# Usage:
#   scripts/run-pin-watcher.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-pin-watcher-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER bearer
SDS_BRIDGE_ALERT_UUID="${PIN_WATCHER_ALERT_BWS_UUID:-b415d03b-f92d-46b1-8a9d-b4f10103df49}"  # sds-bridge alert sender

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
# finding's durable home is the observation the pass files.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-pin-watcher --finding 2 "$@"

# Load BWS_ACCESS_TOKEN from the Keychain via the approved helper (never a plaintext file).
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/sds-token.sh"

# The shared BWS reader (`sds-bws.sh`), which carries the colour guard and the venv interpreter.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
ORCHESTRATOR_API_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID")"
export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN

# Acting means no `--dry-run`, the same term the program applies.
_pin_is_dry_run() {
  for argument in "$@"; do
    [ "$argument" = "--dry-run" ] && return 0
  done
  return 1
}
if ! _pin_is_dry_run "$@"; then
  # Fatal here on an acting pass, which files rows. A dry run reads the member record with it too,
  # so the program itself exits 1 when it is unset.
  if [ -z "$ORCHESTRATOR_API_TOKEN" ]; then
    echo "FATAL: could not read the observer bearer from BWS." >&2
    exit 1
  fi
  SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID")"
  export SDS_BRIDGE_ALERT_TOKEN
  if [ -z "${SDS_BRIDGE_ALERT_TOKEN:-}" ]; then
    echo "FATAL: could not read the sds-bridge alert sender token from BWS (narrow)." >&2
    exit 1
  fi
fi

# THE GITHUB CREDENTIAL HAS NO BWS RECORD, the same gap the landing ledger carries and for the same
# reason: this reads repository metadata and has never needed a dedicated identity. It reads the
# member record's repositories and the public repositories of each account that owns one (ADR-0059
# amendment 4), never every repository the login owns. A scheduled job resting on an interactive
# login breaks the moment that login is re-issued, and nothing would say so but this script's
# exit 1.
if [ -z "${PIN_WATCHER_GITHUB_TOKEN:-}" ]; then
  PIN_WATCHER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
  export PIN_WATCHER_GITHUB_TOKEN
fi
if [ -z "${PIN_WATCHER_GITHUB_TOKEN:-}" ]; then
  echo "FATAL: no GitHub credential. Set PIN_WATCHER_GITHUB_TOKEN, or authenticate gh." >&2
  exit 1
fi

"$REPO_ROOT/.venv/bin/pin-watcher" "$@"
rc=$?

# A code outside {0,1,2,3} is the program dying in a way it does not describe -- a missing binary
# is 127. Reported as 1 (the tool failed) rather than folded to 0, which is what several sibling
# launchers used to do.
case "$rc" in
  0|1|2|3) exit "$rc" ;;
  *) echo "FATAL: pin-watcher exited $rc" >&2; exit 1 ;;
esac
