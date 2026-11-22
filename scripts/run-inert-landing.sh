#!/usr/bin/env bash
# One inert-landing pass: land the update bot's pull requests into the repositories where landing
# on the default branch changes nothing already serving (ADR-0038 part 2a).
#
# WHY THIS RUNS HOURLY AND ITS SIBLING RUNS IN THE CHANGE WINDOW. The estate lander is in the
# window because its pass ends in something changing a running service, and policy declares the
# hours in which something already serving may change. The DEFINING property of this lane's
# population is that landing there changes nothing already serving, so no hour is better than
# another and there is no window to sit inside -- the orchestrator applies none.
#
# WHY HOURLY RATHER THAN LESS OFTEN. Freshness is required, so a landing puts every sibling pull
# request in that repository behind its base: at most one lands per repository per pass, and the
# pass rate IS the drain rate. A queue of N needs N passes. It is still strictly slower than the
# GitHub Actions cascade this replaces, which merged the instant the required checks went green
# with no pace at all -- and it is not a landing rate in any case, since a pass lands only what
# those same checks already permit.
#
# BARE INVOCATION ASKS FOR NOTHING. It reports every open pull request in the declared population
# and the condition each one misses; `--submit` is what turns the report into a request. This
# wrapper must not supply it.
#
# ALL THREE CREDENTIALS ARE FETCHED EITHER WAY, and that is deliberate rather than lazy. This
# program's read surface is the ORCHESTRATOR, whose admission route is authenticated -- there is
# no read-only credential for it -- so a run that skipped a fetch would exit 2 having examined
# nothing, which is the success-shaped silence this estate keeps finding.
#
# A `--submit` pass also raises one alert-hub alert, as the `sds-bridge` sender, for each pull
# request held (or unreadable) for a day (`lander.held_alerts`); its token is fetched only then,
# because a report run sends nothing.
#
# TWO BWS IDENTITIES, and neither can do the other's half. The orchestrator bearer lives in a
# project only the narrow `sds-operator` account can read, and the change-manager bearer in one
# only the broad machine account can. The lane's container reads each through the secrets broker's
# socket. The GitHub token is neither: it is minted from the read-only App (below) and has no BWS
# record.
#
# UNTIL THE ORCHESTRATOR SERVES THIS LANE'S ROUTES, EVERY PASS REPORTS `unreadable` AND EXITS 3.
# They are merged and undeployed, and a route the deployed image does not serve answers 404 --
# which the program reports as a pull request it could not ask about, in different words from one
# it asked about and was refused. That is a lane waiting on a release, not a finding about a
# pull request, and the report says which.
#
# EXIT CODES, the whole interface a scheduled run has:
#   0  everything was measured; nothing was held for a reason that needs a person.
#   1  the tool itself failed (a missing or unreadable credential, an unhandled error).
#   2  the tool ran but could not use its inputs.
#   3  something was found -- a pull request held on a condition somebody has to act on.
#
# THE VOCABULARY IS THE LANDER GROUP'S, CHOSEN RATHER THAN INHERITED. The estate's launchers split
# into two groups in which 2 and 3 mean opposite things, so a new lane must pick one and say so.
# It picks this one because the two landers are read side by side by the same person, and two
# programs doing the same job with opposite codes is the worst available outcome.
#
# THE PROGRAM'S CODE IS THIS SCRIPT'S CODE. There is no `for rc in ...` fold at the end: such a
# fold lets any code outside {0,1,2,3} -- 127 for a missing binary -- fall through to exit 0, and
# a missing binary is exactly what a `git pull` without a `uv sync` produces here.
#
# NOTHING HERE DECIDES ANYTHING. Every term is evaluated by the orchestrator, inside the
# transaction that records the act. This script asks it two questions and relays the answers.
#
# THE LANE RUNS ONLY IN ITS CONTAINER (ADR-0059 increment 5, step 3g-1). `sds_in_lane_container` means the lane's container: `lane-run` names the secrets
# broker's mounted socket in `SDS_SECRETS_SOCKET` (ADR-0055 amendment 30), or passes a lane's own
# token as its one secret. The container runs the image's own code, and the launcher reads every
# secret through the socket, or with that one token in the environment, never as a process
# argument. The broker serves this lane only the UUIDs its allowlist lists
# (`lanes/secrets-broker/allowlist.toml`), exactly these:
#   - every pass: the change-manager READ bearer (`314f276d-…`), the orchestrator SYSTEM bearer
#     (`221a48d5-…`) and the read-only App's key (`314a966c-…`, piped straight into the mint);
#   - `--submit` only: the `sds-bridge` alert sender (`b415d03b-…`).
# It mints `INERT_LANDING_GITHUB_TOKEN` from the read-only App, for
# exactly the repositories `inert-landing repositories` prints: the landing policy's inert
# population, the only repositories a pass lists pull requests in. The token carries `metadata` and
# `pull_requests`, read, and nothing else; this lane lands through the orchestrator's dispatch App,
# never with it. It unsets `BWS_ACCESS_TOKEN` and `SDS_SECRETS_SOCKET` before the lander runs. The
# socket itself stays reachable, as the design accepts: the lander already runs as the same user.
# The ping-URL secret the dead-man helper reads is the compose's. Outside the container the
# launcher refuses with exit 1.
# Exit 1 also covers a failed mint; exit 2 also covers a landing policy the repository list
# couldn't be read from.
#
# Usage:
#   scripts/run-inert-landing.sh              # reports; asks for nothing
#   scripts/run-inert-landing.sh --submit     # asks for the landings that are admissible
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo).
# The SYSTEM bearer. Both acts are SYSTEM-only: not the worker, because a runner asking for its
# own work to be landed attests to its own compliance, and not a human, because a person can land
# a pull request themselves.
ORCHESTRATOR_SYSTEM_UUID="221a48d5-3f29-4898-b300-b4820140c880"
# The READ-scoped change-manager bearer, which reads the declaration naming this lane's
# repositories and permitted authors -- and nothing else. The same credential the estate lander
# and the bump proposer already read the same document with; probed against production 2026-08-12
# at 200 on the policy and 403 on approve.
CHANGE_MANAGER_UUID="314f276d-55ca-4ddc-a24d-b4a3013508cd"
# The `sds-bridge` sender's alert-hub token, BWS `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE` in `SDS
# Operator`, read with the narrow identity, as the bump proposer reads it.
SDS_BRIDGE_ALERT_UUID="b415d03b-f92d-46b1-8a9d-b4f10103df49"
# The read-only lane App's private key and id (ADR-0059 increment 3, Q9), `alobar-sds-lanes-read`,
# in `SDS / Lane Apps`. Read in the container only, piped straight into `sds-github-mint`.
READ_APP_KEY_UUID="314a966c-5654-4e3c-8358-b4fd01268775"
READ_APP_ID="5684758"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe,
# which breaks the parse below.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

# Outside the lane's container this refuses before arming the dead-man switch or reading anything:
# armed here, the switch would ping the production check.
if ! sds_in_lane_container; then
  echo "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN: the inert-landing lane runs only in its container, through lane-run" >&2
  exit 1
fi

# THE DEAD-MAN SWITCH. The codes documented above reach nobody on a scheduled run: a pass that
# stops running, or that fails every hour, is silent. It reports and never gates — every failure
# inside it logs a line and returns 0. The check `sds-inert-landing` must exist in Healthchecks;
# until it does, arming logs one line naming that and this lane runs unalerted.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-inert-landing --finding 3 "$@"


# ---- THE LANE'S CONTAINER (ADR-0059 increment 5, step 3g-1) -----------------------------------
# See "THE LANE RUNS ONLY IN ITS CONTAINER" in the header. Every read is `sds_bws_value` with one argument:
# through the broker's socket, or with the lane's one token from the environment, so no token
# appears in a process argument, and each secret is always read, never taken from the environment.
# Every failure names itself and exits with the code the header gives it; the dead-man switch,
# armed above, pings on the way out.
if sds_in_lane_container; then
  FROM=" (the lane's socket or token)"
  unset INERT_LANDING_CHANGE_MANAGER_TOKEN INERT_LANDING_ORCHESTRATOR_TOKEN \
    INERT_LANDING_GITHUB_TOKEN SDS_BRIDGE_ALERT_TOKEN

  # The compose sets it to the lane's volume (decision 1, "Lane state"). Without it the held and
  # revert alerts' state would go to this run's `HOME`, which `lane-run` discards.
  if [ -z "${LANDER_STATE_DIR:-}" ]; then
    echo "FATAL: LANDER_STATE_DIR is not set in the lane's container" >&2
    exit 1
  fi
  export LANDER_STATE_DIR

  INERT_LANDING_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_UUID")"
  if [ -z "$INERT_LANDING_CHANGE_MANAGER_TOKEN" ]; then
    echo "FATAL: could not read the change-manager credential from BWS$FROM" >&2
    exit 1
  fi
  export INERT_LANDING_CHANGE_MANAGER_TOKEN

  # THE REPOSITORIES ARE THE PROGRAM'S, NOT LISTED HERE: `inert-landing repositories` prints the
  # landing policy's inert population, read with the change-manager credential alone (ADR-0059
  # decision 2: the mint names only the lane's repositories). AN UNDERIVABLE LIST MINTS NOTHING:
  # the subcommand exits 2 with its reason on stderr for a policy it can't read, as the pass would,
  # and its code is the pass's.
  REPOSITORIES="$(env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET \
    "$REPO_ROOT/.venv/bin/inert-landing" repositories)"
  derive_rc=$?
  if [ "$derive_rc" -ne 0 ]; then
    echo "FATAL: the inert lane's repositories could not be derived (inert-landing repositories exited $derive_rc); nothing was minted or asked" >&2
    exit "$derive_rc"
  fi
  TARGETS=()
  while IFS= read -r repository; do
    [ -n "$repository" ] && TARGETS+=(--repository "$repository")
  done <<< "$REPOSITORIES"
  if [ "${#TARGETS[@]}" -eq 0 ]; then
    echo "FATAL: the inert lane's repositories could not be derived (an empty list); nothing was minted or asked" >&2
    exit 2
  fi

  # THE READ TOKEN IS MINTED EACH PASS, bare or submitting. The App's key goes from the BWS read
  # straight to the mint's stdin in its stored form, base64 of the PEM; it is never in a variable,
  # an argument or a file, and the mint runs without the lane's token or the socket's marker. The
  # two permissions are what the pass's one GitHub read uses: each repository's open pull requests
  # (`GitHubReader.open_pull_requests`, `pull_requests`), and `metadata`, which every installation
  # token carries.
  INERT_LANDING_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
    | env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET "$REPO_ROOT/.venv/bin/sds-github-mint" \
        --app-id "$READ_APP_ID" "${TARGETS[@]}" \
        --permission metadata=read --permission pull_requests=read)"
  mint_rc=$?
  if [ "$mint_rc" -ne 0 ] || [ -z "$INERT_LANDING_GITHUB_TOKEN" ]; then
    echo "FATAL: could not mint the inert lane's GitHub read token from the read-only App (the key's BWS read or sds-github-mint exited $mint_rc)" >&2
    exit 1
  fi
  export INERT_LANDING_GITHUB_TOKEN

  INERT_LANDING_ORCHESTRATOR_TOKEN="$(sds_bws_value "$ORCHESTRATOR_SYSTEM_UUID")"
  if [ -z "$INERT_LANDING_ORCHESTRATOR_TOKEN" ]; then
    echo "FATAL: could not read the orchestrator system credential from BWS$FROM" >&2
    exit 1
  fi
  export INERT_LANDING_ORCHESTRATOR_TOKEN

  # THE ALERT TOKEN IS READ ONLY FOR A SUBMITTING RUN, the only one that sends.
  case " $* " in
    *" --submit "*)
      SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID")"
      if [ -z "$SDS_BRIDGE_ALERT_TOKEN" ]; then
        echo "FATAL: could not read the sds-bridge alert sender token from BWS$FROM" >&2
        exit 1
      fi
      export SDS_BRIDGE_ALERT_TOKEN
      ;;
  esac

  # EVERY BWS READ IS DONE, so neither the lane's token nor the socket's marker reaches a program.
  unset BWS_ACCESS_TOKEN SDS_SECRETS_SOCKET
  "$REPO_ROOT/.venv/bin/inert-landing" "$@"
  rc=$?
  exit "$rc"
fi
