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
# THE ACTING PASS TELLS A PERSON: any pass without `--dry-run` (the crontab passes no arguments). It
# fetches the `sds-bridge` alert token like the other secrets, as SDS_BRIDGE_ALERT_TOKEN, and
# exits 1 when it cannot read it. A `--dry-run` pass sends nothing and fetches no alert token.
#
# NOTHING HERE ACTS. It reads GitHub and files one observation per caller. It changes no workflow,
# no pin and no repository; advancing a pin stays a person's one-line pull request.
#
# THE LANE RUNS ONLY IN ITS CONTAINER (ADR-0059 increment 3). `sds_in_lane_container` means the
# lane's container: `lane-run` names the secrets broker's mounted socket in `SDS_SECRETS_SOCKET`
# (ADR-0055 amendment 30), or passes a lane's own token as its one secret. Every secret below is
# read with `sds_bws_value` and one argument, through the socket or with that token in the
# environment, never as a process argument; the broker serves this lane only the UUIDs its entry in
# `lanes/secrets-broker/allowlist.toml` lists. The GitHub credential is an installation token
# minted each pass from the read-only App `alobar-sds-lanes-read`, narrowed to the member record's
# observed repositories and the two read permissions the watcher's calls use. `BWS_ACCESS_TOKEN`
# and `SDS_SECRETS_SOCKET` are removed from every child and unset before the watcher runs, which
# needs neither nor the App's key; the socket itself stays reachable, as the design accepts.
# Without a socket or a token the launcher refuses with exit 1.
#
# Usage:
#   scripts/run-pin-watcher.sh [--dry-run]
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER bearer
SDS_BRIDGE_ALERT_UUID="${PIN_WATCHER_ALERT_BWS_UUID:-b415d03b-f92d-46b1-8a9d-b4f10103df49}"  # sds-bridge alert sender
# The read-only lane App `alobar-sds-lanes-read`: its key (base64 of the PEM) and its id, no secret.
READ_APP_KEY_UUID="314a966c-5654-4e3c-8358-b4fd01268775"
READ_APP_ID="5684758"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The shared BWS reader (`sds-bws.sh`), which carries the colour guard and the venv interpreter.
# Sourced first: the host test below is its `sds_in_lane_container`.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

# Outside the lane's container this refuses before arming the dead-man switch or reading anything:
# armed here, the switch would ping the production check.
if ! sds_in_lane_container; then
  echo "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN: the pin-watcher lane runs only in its container, through lane-run" >&2
  exit 1
fi

# THE DEAD-MAN SWITCH. The codes above reach nobody on a scheduled run: a pass that stops running
# is silent. Note what it does NOT do: a declared finding
# code pings the check SUCCESS, so exit 2 never pages. The check answers "is this lane alive"; the
# finding's durable home is the observation the pass files.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-pin-watcher --finding 2 "$@"


ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
ORCHESTRATOR_API_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID")"
export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN

# The derivation below reads the member record with the observer bearer on every pass, a dry run
# included, so an unread bearer is fatal before anything else.
if [ -z "$ORCHESTRATOR_API_TOKEN" ]; then
  echo "FATAL: could not read the observer bearer from BWS (the lane's socket or token)." >&2
  exit 1
fi

# Acting means no `--dry-run`, the same term the program applies.
_pin_is_dry_run() {
  for argument in "$@"; do
    [ "$argument" = "--dry-run" ] && return 0
  done
  return 1
}
if ! _pin_is_dry_run "$@"; then
  SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID")"
  export SDS_BRIDGE_ALERT_TOKEN
  if [ -z "${SDS_BRIDGE_ALERT_TOKEN:-}" ]; then
    echo "FATAL: could not read the sds-bridge alert sender token from BWS (the lane's socket or token)." >&2
    exit 1
  fi
fi

# THE REPOSITORIES ARE THE MEMBER RECORD'S, NOT LISTED HERE: `sds_scope ledger` prints the
# record's observed repositories (onboarding and member), the same population the watcher's own
# members come from (`src/pin_watcher/cli.py`, `observed(members)`), each once. The mint never
# names a public non-member: the watcher reads those public repositories through the same token
# (ADR-0059 amendment 4). AN UNDERIVABLE LIST MINTS NOTHING, AND SAYS SO: an unreadable record or
# an empty population exits 3 with nothing on stdout, and the empty-list check below exits 3, so
# the day reads as incomplete rather than clean. Any other non-zero code is the tool failing (1)
# or not running at all (126, 127, 137), and is passed through verbatim.
REPOSITORIES="$(cd "$REPO_ROOT" && SDS_SCOPE_ORCHESTRATOR_TOKEN="$ORCHESTRATOR_API_TOKEN" \
  env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET "$REPO_ROOT/.venv/bin/python" -m scripts.sds_scope ledger)"
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

# THE GITHUB TOKEN IS MINTED EACH PASS, a dry run included, after the derivation, because the
# mint names its repositories. The App's key goes from the BWS read straight to the mint's stdin
# in its stored form, base64 of the PEM; it is never in a variable, an argument or a file, and
# the mint runs without the lane's token or the socket's marker. The two permissions are what the watcher's GET calls
# use (`src/pin_watcher/github.py`): the recommendation, each caller file, a compare and a commit
# (`contents`), and each repository and each owner's listing (`metadata`). Members in more than
# one owner make the mint refuse, which is a failed mint here (exit 1). Any preset value is
# replaced, so the pass runs only on the token it minted.
PIN_WATCHER_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
  | env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET "$REPO_ROOT/.venv/bin/sds-github-mint" \
      --app-id "$READ_APP_ID" "${TARGETS[@]}" \
      --permission metadata=read --permission contents=read)"
mint_rc=$?
export PIN_WATCHER_GITHUB_TOKEN
if [ "$mint_rc" -ne 0 ] || [ -z "$PIN_WATCHER_GITHUB_TOKEN" ]; then
  echo "FATAL: could not mint the watcher's GitHub token from the read-only App" \
       "(sds-github-mint exited $mint_rc)" >&2
  exit 1
fi
unset BWS_ACCESS_TOKEN SDS_SECRETS_SOCKET

"$REPO_ROOT/.venv/bin/pin-watcher" "$@"
rc=$?

# A code outside {0,1,2,3} is the program dying in a way it does not describe -- a missing binary
# is 127. Reported as 1 (the tool failed) rather than folded to 0, which is what several sibling
# launchers used to do.
case "$rc" in
  0|1|2|3) exit "$rc" ;;
  *) echo "FATAL: pin-watcher exited $rc" >&2; exit 1 ;;
esac
