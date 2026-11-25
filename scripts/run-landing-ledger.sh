#!/usr/bin/env bash
# One landing-ledger pass, then one audit pass over what it recorded (WS-P3.6 Increment 3).
#
# RECORD FIRST, AUDIT SECOND, ALWAYS. Detector A re-evaluates what the ledger holds, so an audit
# over a stale ledger reports on a window that has already moved. Detector B reads GitHub directly
# and is unaffected -- which is why the audit still runs when recording came back incomplete.
#
# EXIT CODES, and they are the whole interface a scheduled run has:
#   0  everything was measured and nothing was found.
#   1  the tool itself failed (a missing credential, an unhandled error).
#   2  something was found. The pass worked; reality did not.
#   3  some part of reality could not be read, so the answer is missing rather than clean.
# 3 outranks 2: an incomplete pass cannot claim it found everything there was to find. A broken
# tool and an honest finding sharing one code is a collision this estate has already paid for.
#
# NOTHING HERE ACTS. Both passes read and file observations; neither changes a pull request, a
# repository setting, or any canonical state.
#
# THE LANE RUNS ONLY IN ITS CONTAINER (ADR-0059 increment 3). `sds_in_lane_container` means the
# lane's container: `lane-run` names the secrets broker's mounted socket in `SDS_SECRETS_SOCKET`
# (ADR-0055 amendment 30), or passes a lane's own token as its one secret. Every BWS read below is
# `sds_bws_value` with one argument, through the socket or with that token in the environment,
# never as a process argument; the broker serves this lane only the UUIDs its entry in
# `lanes/secrets-broker/allowlist.toml` lists. The GitHub credential is an installation token
# minted each pass from the read-only App `alobar-sds-lanes-read`, narrowed to the scoped
# repositories and the four read permissions the ledger's calls use. `BWS_ACCESS_TOKEN` and
# `SDS_SECRETS_SOCKET` are removed from every child and unset before the ledger runs, which needs
# neither nor the App's key; the socket itself stays reachable, as the design accepts. Without a
# socket or a token the launcher refuses with exit 1.
#
# Usage:
#   scripts/run-landing-ledger.sh [--dry-run]
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER bearer
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
  echo "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN: the landing-ledger lane runs only in its container, through lane-run" >&2
  exit 1
fi

# THE DEAD-MAN SWITCH. The codes documented above reach nobody on a scheduled run: a pass that
# stops running, or that fails every morning, is silent. It reports and never gates -- every
# failure inside it logs a line and returns 0.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-landing-ledger --finding 2 "$@"



LANDING_LEDGER_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID")"
export LANDING_LEDGER_TOKEN
if [ -z "$LANDING_LEDGER_TOKEN" ]; then
  echo "FATAL: could not read the orchestrator observer credential from BWS" >&2
  exit 1
fi

# THE REPOSITORIES ARE READ FROM THE MEMBER RECORD, NOT LISTED: the ledger covers the record's
# `observed` population (docs/operations/membership.md). A hand list here drifted from the
# declarations it was meant to follow.
#
# AN INCOMPLETE DERIVATION RECORDS NOTHING, AND SAYS SO. `sds_scope` exits 3 with nothing on stdout
# when the member record could not be read or the population is empty: there is no partial list.
# The empty-list check below then exits 3, so the day reads as incomplete rather than clean. Any
# other non-zero code is the tool failing (1) or not running at all (126, 127, 137), and is passed
# through verbatim.
REPOSITORIES="$(cd "$REPO_ROOT" && SDS_SCOPE_ORCHESTRATOR_TOKEN="$LANDING_LEDGER_TOKEN" \
  env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET \
  "$REPO_ROOT/.venv/bin/python" -m scripts.sds_scope ledger)"
scope_rc=$?
if [ "$scope_rc" -ne 0 ] && [ "$scope_rc" -ne 3 ]; then
  echo "the ledger's repositories could not be derived: sds_scope exited $scope_rc" >&2
  exit "$scope_rc"
fi
if [ -z "$REPOSITORIES" ]; then
  echo "the ledger's repositories could not be derived; nothing was recorded" >&2
  exit 3
fi
TARGETS=()
while IFS= read -r repository; do
  TARGETS+=(--repository "$repository")
done <<< "$REPOSITORIES"

# THE GITHUB TOKEN IS MINTED HERE, after the scope, because the mint names the
# scoped repositories: so an unreadable scope (exit 3 above) mints nothing. The App's key goes from
# the BWS read straight to the mint's stdin in its stored form, base64 of the PEM; it is never in a
# variable, an argument or a file, and the mint runs without the lane's token or the socket's
# marker. The four permissions are what the ledger's GET calls use (`src/landing_ledger/github.py`):
# the repository, branch, commits and gate file (`metadata`, `contents`), pull requests
# (`pull_requests`), and workflow runs, jobs and attempts (`actions`).
# Any preset value is replaced, so the pass runs only on the token it minted.
LANDING_LEDGER_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
  | env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET "$REPO_ROOT/.venv/bin/sds-github-mint" \
      --app-id "$READ_APP_ID" "${TARGETS[@]}" \
      --permission metadata=read --permission contents=read \
      --permission pull_requests=read --permission actions=read)"
mint_rc=$?
export LANDING_LEDGER_GITHUB_TOKEN
if [ "$mint_rc" -ne 0 ] || [ -z "$LANDING_LEDGER_GITHUB_TOKEN" ]; then
  echo "FATAL: could not mint the ledger's GitHub token from the read-only App" >&2
  exit 1
fi
unset BWS_ACCESS_TOKEN SDS_SECRETS_SOCKET

PASS_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LEDGER="$REPO_ROOT/.venv/bin/landing-ledger"

# SEVEN DAYS, NOT THE THIRTY-DAY DEFAULT, and this is a correctness setting rather than a
# preference. Recording costs about twelve GitHub requests per landing; the 2026-08-08 backfill
# read thirty days across the eight repositories it covered then and exhausted the 5000/hour limit in one pass.
# A daily job at that window would therefore run out partway through every morning, mark the later
# repositories unavailable, and exit 3 forever -- a permanently red signal, which is a signal
# nobody reads. Seven days is roughly ninety landings, survives a week of the machine being
# closed (re-recording an unchanged landing replays rather than conflicting), and leaves headroom
# for the audit's own reads. A LONGER gap than that needs one manual `record --days N`.
"$LEDGER" record "${TARGETS[@]}" --days 7 "$@"
record_rc=$?

"$LEDGER" audit "${TARGETS[@]}" --pass-id "$PASS_ID" "$@"
audit_rc=$?

# AN OUT-OF-VOCABULARY CODE IS REPORTED VERBATIM, and this branch must come FIRST. The fold below
# ranks the four codes this program can return; anything else -- 126 for a program that is not
# executable, 127 for one that is missing, 137 for one that was killed -- matches none of them and
# used to fall through to `exit 0`, turning the loudest possible failure into the quietest possible
# answer. That was survivable while nothing subscribed to the exit code. It is not survivable now
# that the dead-man switch reports on it: a missing binary would ping success.
for rc in "$record_rc" "$audit_rc"; do
  case "$rc" in
    0 | 1 | 2 | 3) ;;
    *) exit "$rc" ;;
  esac
done

# The worst answer wins, and "could not measure" is worse than "found something". An incomplete
# derivation (`scope_rc` 3) counts as not measuring the repositories it couldn't name.
for rc in 1 3 2; do
  if [ "$record_rc" -eq "$rc" ] || [ "$audit_rc" -eq "$rc" ] || [ "$scope_rc" -eq "$rc" ]; then
    exit "$rc"
  fi
done
exit 0
