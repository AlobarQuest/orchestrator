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
# TWO BWS IDENTITIES, and neither can do the other's half. MEASURED 2026-08-12, both directions:
# the orchestrator bearer lives in a project only the narrow `sds-operator` account can read, and
# the change-manager bearer in one only the broad machine account can. A launcher bootstrapping
# with either alone dies on the other's fetch. `infraops-mcp-server/scripts/drift-audit.sh` is the
# in-estate precedent for overriding the identity for a single foreign call.
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
# THE GITHUB TOKEN is the operator's own `gh` login on the Mac, as the inert lander's: it reads the
# open pull requests a revert is in and the registry's digests of this estate's images. The lane's
# container mints a read token instead ("TWO HOSTS, ONE LAUNCHER").
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
# TWO HOSTS, ONE LAUNCHER (ADR-0059 increment 5, step 3g-2). A set `BWS_ACCESS_TOKEN` means the
# lane's container: `lane-run` passes the lane's own token as its one secret, and the Mac's launchd
# job never sets it. There the launcher skips activation (the container runs the image's own code),
# reads no Keychain item and calls no `gh`, and reads every secret with that one token, in the
# environment, never as a process argument:
#   - every pass: the change-manager READ bearer (`314f276d-…`), the read-only App's key
#     (`314a966c-…`, piped straight into the mint) and the orchestrator SYSTEM bearer
#     (`221a48d5-…`);
#   - `--submit` only: the `sds-bridge` alert sender (`b415d03b-…`), fatal if unreadable, and the
#     Coolify token (`f9f59233-…`), not fatal, as on the Mac.
# It mints `ESTATE_LANDING_GITHUB_TOKEN` from the read-only App, in place of `gh auth token`, for
# exactly the repositories `estate-landing repositories` prints: the landing policy's deploying
# population, where this lane's landings are. The token carries what the pass's GitHub reads use
# and nothing else: `metadata` and `pull_requests`, read, on every pass (the open pull requests a
# revert is in), and `contents`, read, on `--submit` only (whether production serves a commit at
# or after the revert's, a compare only a submitting pass makes). The same token is the registry's
# credential on `--submit`. This lane lands through the orchestrator's dispatch App, never with
# it. It unsets `BWS_ACCESS_TOKEN` before the lander runs. The bare run, the exit codes and the
# dead-man switch are the Mac's. The container block runs the whole pass and exits, so everything
# after it is the Mac's launchd job, unchanged until increment 6.
# Exit 1 there also covers a failed mint; exit 2 also covers a landing policy the repository list
# couldn't be read from.
#
# Usage:
#   scripts/run-estate-landing.sh              # reports; asks for nothing
#   scripts/run-estate-landing.sh --submit     # asks for the landings that are admissible
# Install as a scheduled job with:
#   scripts/install-estate-landing-launchd.sh
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
# ACTIVATION — a merged change is not live on this machine until the code is pulled. The estate's
# Dependabot cascade stops at the merge, which is complete for a repository whose landing redeploys
# a hosted application and incomplete for this one, whose code runs from a working copy here
# (orchestrator ADR-0031). Best-effort by construction: the helper prints one `[activation]` line
# and returns 0 whatever it finds, so this job is never gated on being able to update itself. It
# re-execs this script when HEAD moves, because bash reads a script incrementally by byte offset
# and the file it just rewrote is this one.
# Skipped in the container, which runs the image's own code and has no `activate-checkout.sh`.
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
# reach nobody: a pass that stops running, or that fails every night, is silent. Armed AFTER
# activation, because `activate_checkout` may `exec` and an `exec` does not fire an EXIT handler.
# It reports and never gates -- every failure inside it logs a line and returns 0. Note this lane
# runs every hour and the check is scheduled to expect each: a missed hour is a lane that
# half-stopped, which is the thing worth hearing about.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-estate-landing --finding 3 "$@"


# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe,
# which breaks the parse below.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

# ---- THE LANE'S CONTAINER (ADR-0059 increment 5, step 3g-2) -----------------------------------
# See "TWO HOSTS, ONE LAUNCHER" in the header. Every read takes the lane's one token from the
# environment (`sds_bws_value` with one argument), so it never appears in a process argument, and
# each secret is always read, never taken from the environment. Every failure names itself and
# exits with the code the header gives it; the dead-man switch, armed above, pings on the way out.
if [ -n "${BWS_ACCESS_TOKEN:-}" ]; then
  FROM=" (the lane's token)"
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
  REPOSITORIES="$(env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/estate-landing" repositories)"
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
  # an argument or a file, and the mint runs without the lane's token. Its permissions are what the
  # pass's GitHub reads use: `metadata`, which every installation token carries, and each watched
  # repository's open pull requests (`GitHubReader.open_pull_requests`, `pull_requests`) on every
  # pass; and on `--submit` alone, the compare of a served commit with the revert's
  # (`GitHubReader.compare_status`, `contents`), which a bare pass never makes.
  PERMISSIONS=(--permission metadata=read --permission pull_requests=read)
  case " $* " in
    *" --submit "*) PERMISSIONS+=(--permission contents=read) ;;
  esac
  ESTATE_LANDING_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
    | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
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

  # THE ALERT AND COOLIFY TOKENS ARE READ ONLY FOR A SUBMITTING RUN, as on the Mac: only it sends,
  # and only it reaches production. The Coolify token's absence is not fatal, as on the Mac: the
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

  # EVERY BWS READ IS DONE, so the lane's token reaches no program.
  unset BWS_ACCESS_TOKEN
  "$REPO_ROOT/.venv/bin/estate-landing" "$@"
  rc=$?
  exit "$rc"
fi

# SOURCED, not executed. `sds-token.sh` EXPORTS the value and prints nothing -- its own header says
# so -- so command-substituting it yields the empty string and every fetch below fails with a
# message about a missing environment variable rather than about a missing token. That was the
# first version of this script, and under launchd (which sets no BWS_ACCESS_TOKEN) it could never
# have completed a single pass.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/scripts/sds-token.sh"
fi
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  echo "FATAL: BWS_ACCESS_TOKEN not available for the orchestrator credential" >&2
  exit 1
fi
SDS_IDENTITY="$BWS_ACCESS_TOKEN"

# The broad machine account, for the change-manager project alone. Named here rather than silently
# different, exactly as the producer's launcher names its own.
BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
if [ -z "$BROAD_IDENTITY" ]; then
  echo "FATAL: no BWS identity for the change-manager credential (Keychain service Claude)" >&2
  exit 1
fi

if [ -z "${ESTATE_LANDING_CHANGE_MANAGER_TOKEN:-}" ]; then
  ESTATE_LANDING_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_UUID" "$BROAD_IDENTITY")"
  export ESTATE_LANDING_CHANGE_MANAGER_TOKEN
fi
if [ -z "${ESTATE_LANDING_ORCHESTRATOR_TOKEN:-}" ]; then
  ESTATE_LANDING_ORCHESTRATOR_TOKEN="$(sds_bws_value "$ORCHESTRATOR_SYSTEM_UUID" "$SDS_IDENTITY")"
  export ESTATE_LANDING_ORCHESTRATOR_TOKEN
fi

if [ -z "${ESTATE_LANDING_GITHUB_TOKEN:-}" ]; then
  ESTATE_LANDING_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
  export ESTATE_LANDING_GITHUB_TOKEN
fi

# `set -e` is deliberately not used, so a failed fetch would otherwise leave these EMPTY and fall
# through -- into an exit 2 that reports "unusable input" for what is actually a credential
# failure. Name each here, so the exit code means what this header says it means.
if [ -z "${ESTATE_LANDING_GITHUB_TOKEN:-}" ]; then
  echo "FATAL: no GitHub token (set ESTATE_LANDING_GITHUB_TOKEN or run gh auth login)" >&2
  exit 1
fi
if [ -z "${ESTATE_LANDING_CHANGE_MANAGER_TOKEN:-}" ]; then
  echo "FATAL: could not read the change-manager credential from BWS" >&2
  exit 1
fi
if [ -z "${ESTATE_LANDING_ORCHESTRATOR_TOKEN:-}" ]; then
  echo "FATAL: could not read the orchestrator system credential from BWS" >&2
  exit 1
fi

# THE ALERT TOKEN IS FETCHED ONLY FOR A SUBMITTING RUN, the only one that sends.
case " $* " in
  *" --submit "*)
    if [ -z "${SDS_BRIDGE_ALERT_TOKEN:-}" ]; then
      SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID" "$SDS_IDENTITY")"
    fi
    if [ -z "${SDS_BRIDGE_ALERT_TOKEN:-}" ]; then
      echo "FATAL: could not read the sds-bridge alert sender token from BWS (narrow)" >&2
      exit 1
    fi
    export SDS_BRIDGE_ALERT_TOKEN
    if [ -z "${ESTATE_LANDING_COOLIFY_TOKEN:-}" ]; then
      ESTATE_LANDING_COOLIFY_TOKEN="$(sds_bws_value "$COOLIFY_TOKEN_UUID" "$SDS_IDENTITY" || true)"
      if [ -z "$ESTATE_LANDING_COOLIFY_TOKEN" ]; then
        echo "[estate-landing] the Coolify token could not be read: re-points are refused" >&2
      fi
      export ESTATE_LANDING_COOLIFY_TOKEN
    fi
    ;;
esac

"$REPO_ROOT/.venv/bin/estate-landing" "$@"
