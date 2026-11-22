#!/usr/bin/env bash
# One estate-landing pass: land the pull requests the estate has routed and approved, into the
# repositories where landing changes something already serving (ADR-0019 increment 5b), and take
# the next step for any of this lane's landings that went bad (Landing Part 3b).
#
# HOURLY, AND IT LANDS BUMPS ONLY 02:00-06:00 NEW YORK. Proposing is not acting, and policy
# declares the hours in which something already serving may change: the program itself asks about
# ordinary bumps only in passes inside those hours (the orchestrator refuses outside its window
# regardless). Every pass reads the watch and steps a bad landing on -- pointing production back at
# the build before the bump, opening the revert, putting the moving tag back -- and lands the SDS's
# own revert pull requests, which policy exempts from the window: a production pinned to an old
# build should not wait a day for its revert.
#
# BARE INVOCATION ASKS FOR NOTHING. It reports every routed pull request and the condition each one
# misses; `--submit` is what turns the report into a request. This wrapper must not supply it.
#
# BOTH CREDENTIALS ARE FETCHED EITHER WAY, and that is a correction rather than an oversight. The
# producer's launcher fetches its writing credential only for a writing run, because its dry run
# reads GitHub and nothing else. This program's read surface is the ORCHESTRATOR, and the
# admission route is authenticated -- there is no read-only credential for it -- so a run that
# skipped the fetch would exit 2 having examined nothing, which is the success-shaped silence this
# estate keeps finding. Reported by adversarial review; the first version had exactly that bug.
#
# A `--submit` pass also raises one alert-hub alert, as the `sds-bridge` sender, for each pull
# request held (or unreadable) for a day (`lander.held_alerts`), and one per revert record and per
# landing pinned or not pointed back; its token is fetched only then, because a report run sends
# nothing.
#
# THE COOLIFY TOKEN IS FETCHED ONLY ON `--submit` TOO, and its absence is not fatal: the program
# refuses every step that would change production by name and raises `landing-not-pointed-back`.
# It is a dedicated token a person mints in Coolify; until the UUID below names it (or
# `ESTATE_LANDING_COOLIFY_UUID` does), nothing is fetched. It is sent only over the tailnet.
#
# THE GITHUB TOKEN reads the open pull requests a revert is in and the registry's digests of this
# estate's images. The pass mints it each time from the read-only App ("THE LANE'S CONTAINER").
#
# EXPECT A FIRST PASS TO LAND NOTHING, and read that as the conditions working rather than as the
# lane failing. A held pull request that names its condition is the whole point of the report.
#
# EXIT CODES, the whole interface a scheduled run has:
#   0  everything was measured; nothing was held for a reason that needs a person.
#   1  the tool itself failed (a missing or unreadable credential, an unhandled error).
#   2  the tool ran but could not use its inputs.
#   3  something was found -- a pull request held on a condition somebody has to act on.
#
# NOTHING HERE DECIDES ANYTHING. Every term is evaluated by the orchestrator, inside the
# transaction that records the act. This script asks it two questions and relays the answers.
#
# THE LANE'S CONTAINER (ADR-0059 increment 5, step 3g-2). The lane runs only there:
# `sds_in_lane_container` means `lane-run` named the secrets broker's mounted socket in
# `SDS_SECRETS_SOCKET` (ADR-0055 amendment 30), or passed a lane's own token as its one secret;
# anywhere else the launcher refuses. Every secret is read through the socket, or with that one
# token in the environment, never as a process argument. The broker serves this lane only the
# UUIDs its allowlist lists (`lanes/secrets-broker/allowlist.toml`), exactly these:
#   - every pass: the change-manager READ bearer (`314f276d-…`), the read-only App's key
#     (`314a966c-…`, piped straight into the mint) and the orchestrator SYSTEM bearer
#     (`221a48d5-…`);
#   - `--submit` only: the `sds-bridge` alert sender (`b415d03b-…`), fatal if unreadable, and the
#     Coolify token (`f9f59233-…`), not fatal.
# It mints `ESTATE_LANDING_GITHUB_TOKEN` from the read-only App, for exactly the repositories
# `estate-landing repositories` prints: the landing policy's deploying population, where this
# lane's landings are. The token carries what the pass's GitHub reads use and nothing else:
# `metadata` and `pull_requests`, read, on every pass (the open pull requests a revert is in), and
# `contents`, read, on `--submit` only (whether production serves a commit at or after the
# revert's, a compare only a submitting pass makes). The same token is the registry's credential on
# `--submit`. This lane lands through the orchestrator's dispatch App, never with it. It unsets
# `BWS_ACCESS_TOKEN` and `SDS_SECRETS_SOCKET` before the lander runs. The socket itself stays
# reachable, as the design accepts: the lander already runs as the same user. Exit 1 also covers a
# failed mint; exit 2 also covers a landing policy the repository list couldn't be read from.
#
# Usage:
#   scripts/run-estate-landing.sh              # reports; asks for nothing
#   scripts/run-estate-landing.sh --submit     # asks for the landings that are admissible
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo).
# The SYSTEM bearer. The landing route is SYSTEM-only: not the worker, because a runner asking for
# its own work to be landed attests to its own compliance, and not a human, because a person can
# land a pull request themselves.
ORCHESTRATOR_SYSTEM_UUID="221a48d5-3f29-4898-b300-b4820140c880"
# The READ-scoped change-manager bearer, which enumerates which changes were routed and reads what
# the policy requires -- and nothing else. Adversarial review found this launcher reaching for the
# OBSERVE credential, which carries one write this program never makes (recording a rollout
# observation, the watcher's job) and which the watcher already holds, so a rotation would have
# broken two schedules with no signal. `change-manager/M2M_TOKEN_READ` already existed; the review
# reported it as never minted, and it was there. Probed against production 2026-08-12: 200 on the
# listing and on the policy, 403 on approve and on deploy-observation.
CHANGE_MANAGER_UUID="314f276d-55ca-4ddc-a24d-b4a3013508cd"
# The `sds-bridge` sender's alert-hub token, BWS `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` in `SDS
# Operator`, read with the narrow identity, as the bump proposer reads it.
SDS_BRIDGE_ALERT_UUID="b415d03b-f92d-46b1-8a9d-b4f10103df49"
# The estate lander's own Coolify token (Landing Part 3b): `read`, `write` and `deploy`, minted by a
# person in Coolify and stored in `SDS Operator`, read with the narrow identity. The client bounds it
# to one repository's applications (`estate_lander.image_repoint`). BWS
# `COOLIFY_TOKEN_SDS_ESTATE_LANDER`; `ESTATE_LANDING_COOLIFY_UUID` overrides it.
COOLIFY_TOKEN_UUID="${ESTATE_LANDING_COOLIFY_UUID:-f9f59233-0794-4ea8-9806-b4f3011e6147}"
# The read-only lane App's private key and id (ADR-0059 increment 3, Q9), `alobar-sds-lanes-read`,
# in `SDS / Lane Apps`. Read in the container only, piped straight into `sds-github-mint`.
READ_APP_KEY_UUID="314a966c-5654-4e3c-8358-b4fd01268775"
READ_APP_ID="5684758"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe,
# which breaks the parse below. Sourced first: the host test below is its `sds_in_lane_container`.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"
if ! sds_in_lane_container; then
  echo "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN: the estate-landing lane runs only in its container, through lane-run" >&2
  exit 1
fi

# THE DEAD-MAN SWITCH. The scheduler discards this script's exit code, so the codes documented above
# reach nobody: a pass that stops running, or that fails every night, is silent. It reports and
# never gates -- every failure inside it logs a line and returns 0. Note this lane runs every hour
# and the check is scheduled to expect each: a missed hour is a lane that half-stopped, which is
# the thing worth hearing about.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-estate-landing --finding 3 "$@"

# ---- THE LANE'S CONTAINER (ADR-0059 increment 5, step 3g-2) -----------------------------------
# See "THE LANE'S CONTAINER" in the header. Every read is `sds_bws_value` with one argument:
# through the broker's socket, or with the lane's one token from the environment, so no token
# appears in a process argument, and each secret is always read, never taken from the environment.
# Every failure names itself and exits with the code the header gives it; the dead-man switch,
# armed above, pings on the way out.
FROM=" (the lane's socket or token)"
unset ESTATE_LANDING_CHANGE_MANAGER_TOKEN ESTATE_LANDING_ORCHESTRATOR_TOKEN \
  ESTATE_LANDING_GITHUB_TOKEN ESTATE_LANDING_COOLIFY_TOKEN SDS_BRIDGE_ALERT_TOKEN

# The compose sets it to the lane's volume (decision 1, "Lane state"). Without it the held and
# revert alerts' state would go to this run's `HOME`, which `lane-run` discards.
if [ -z "${LANDER_STATE_DIR:-}" ]; then
  echo "FATAL: LANDER_STATE_DIR is not set in the lane's container" >&2
  exit 1
fi
export LANDER_STATE_DIR

ESTATE_LANDING_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_UUID")"
if [ -z "$ESTATE_LANDING_CHANGE_MANAGER_TOKEN" ]; then
  echo "FATAL: could not read the change-manager credential from BWS$FROM" >&2
  exit 1
fi
export ESTATE_LANDING_CHANGE_MANAGER_TOKEN

# THE REPOSITORIES ARE THE PROGRAM'S, NOT LISTED HERE: `estate-landing repositories` prints the
# landing policy's deploying population, read with the change-manager credential alone (ADR-0059
# decision 2: the mint names only the lane's repositories). AN UNDERIVABLE LIST MINTS NOTHING:
# the subcommand exits 2 with its reason on stderr for a policy it can't read or one declaring no
# deploying population, and its code is the pass's.
REPOSITORIES="$(env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET \
  "$REPO_ROOT/.venv/bin/estate-landing" repositories)"
derive_rc=$?
if [ "$derive_rc" -ne 0 ]; then
  echo "FATAL: the estate lane's repositories could not be derived (estate-landing repositories exited $derive_rc); nothing was minted or asked" >&2
  exit "$derive_rc"
fi
TARGETS=()
while IFS= read -r repository; do
  [ -n "$repository" ] && TARGETS+=(--repository "$repository")
done <<< "$REPOSITORIES"
if [ "${#TARGETS[@]}" -eq 0 ]; then
  echo "FATAL: the estate lane's repositories could not be derived (an empty list); nothing was minted or asked" >&2
  exit 2
fi

# THE READ TOKEN IS MINTED EACH PASS, bare or submitting. The App's key goes from the BWS read
# straight to the mint's stdin in its stored form, base64 of the PEM; it is never in a variable,
# an argument or a file, and the mint runs without the lane's token or the socket's marker. Its
# permissions are what the pass's GitHub reads use: `metadata`, which every installation token
# carries, and each watched repository's open pull requests (`GitHubReader.open_pull_requests`,
# `pull_requests`) on every pass; and on `--submit` alone, the compare of a served commit with
# the revert's (`GitHubReader.compare_status`, `contents`), which a bare pass never makes.
PERMISSIONS=(--permission metadata=read --permission pull_requests=read)
case " $* " in
  *" --submit "*) PERMISSIONS+=(--permission contents=read) ;;
esac
ESTATE_LANDING_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
  | env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET "$REPO_ROOT/.venv/bin/sds-github-mint" \
      --app-id "$READ_APP_ID" "${TARGETS[@]}" "${PERMISSIONS[@]}")"
mint_rc=$?
if [ "$mint_rc" -ne 0 ] || [ -z "$ESTATE_LANDING_GITHUB_TOKEN" ]; then
  echo "FATAL: could not mint the estate lane's GitHub read token from the read-only App (the key's BWS read or sds-github-mint exited $mint_rc)" >&2
  exit 1
fi
export ESTATE_LANDING_GITHUB_TOKEN

ESTATE_LANDING_ORCHESTRATOR_TOKEN="$(sds_bws_value "$ORCHESTRATOR_SYSTEM_UUID")"
if [ -z "$ESTATE_LANDING_ORCHESTRATOR_TOKEN" ]; then
  echo "FATAL: could not read the orchestrator system credential from BWS$FROM" >&2
  exit 1
fi
export ESTATE_LANDING_ORCHESTRATOR_TOKEN

# THE ALERT AND COOLIFY TOKENS ARE READ ONLY FOR A SUBMITTING RUN: only it sends,
# and only it reaches production. The Coolify token's absence is not fatal: the
# program refuses every re-point and restore by name and alerts `landing-not-pointed-back`. Its
# failed read logs this line and `sds_bws_value`'s own on stderr; nothing hides it.
case " $* " in
  *" --submit "*)
    SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID")"
    if [ -z "$SDS_BRIDGE_ALERT_TOKEN" ]; then
      echo "FATAL: could not read the sds-bridge alert sender token from BWS$FROM" >&2
      exit 1
    fi
    export SDS_BRIDGE_ALERT_TOKEN
    ESTATE_LANDING_COOLIFY_TOKEN="$(sds_bws_value "$COOLIFY_TOKEN_UUID")"
    if [ -z "$ESTATE_LANDING_COOLIFY_TOKEN" ]; then
      echo "[estate-landing] the Coolify token could not be read$FROM: re-points are refused" >&2
    else
      export ESTATE_LANDING_COOLIFY_TOKEN
    fi
    ;;
esac

# EVERY BWS READ IS DONE, so neither the lane's token nor the socket's marker reaches a program.
unset BWS_ACCESS_TOKEN SDS_SECRETS_SOCKET
"$REPO_ROOT/.venv/bin/estate-landing" "$@"
rc=$?
exit "$rc"
