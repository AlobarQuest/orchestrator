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
# only the broad machine account can. A launcher bootstrapping with either alone dies on the
# other's fetch. The GitHub token is neither: it comes from `gh auth token`, exactly as the two
# proposers' and the ledger's do, and has no BWS record.
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
# TWO HOSTS, ONE LAUNCHER (ADR-0059 increment 5, step 3g-1). A set `BWS_ACCESS_TOKEN` means the
# lane's container: `lane-run` passes the lane's own token as its one secret, and the Mac's launchd
# job never sets it. There the launcher skips activation (the container runs the image's own code),
# reads no Keychain item and calls no `gh`, and reads every secret with that one token, in the
# environment, never as a process argument:
#   - every pass: the change-manager READ bearer (`314f276d-…`), the orchestrator SYSTEM bearer
#     (`221a48d5-…`) and the read-only App's key (`314a966c-…`, piped straight into the mint);
#   - `--submit` only: the `sds-bridge` alert sender (`b415d03b-…`), as on the Mac.
# It mints `INERT_LANDING_GITHUB_TOKEN` from the read-only App, in place of `gh auth token`, for
# exactly the repositories `inert-landing repositories` prints: the landing policy's inert
# population, the only repositories a pass lists pull requests in. The token carries `metadata` and
# `pull_requests`, read, and nothing else; this lane lands through the orchestrator's dispatch App,
# never with it. It unsets `BWS_ACCESS_TOKEN` before the lander runs. The bare run, the exit codes
# and the dead-man switch are the Mac's. The container block runs the whole pass and exits, so
# everything after it is the Mac's launchd job, unchanged until increment 6.
# Exit 1 there also covers a failed mint; exit 2 also covers a landing policy the repository list
# couldn't be read from.
#
# Usage:
#   scripts/run-inert-landing.sh              # reports; asks for nothing
#   scripts/run-inert-landing.sh --submit     # asks for the landings that are admissible
# Install as a scheduled job with:
#   scripts/install-inert-landing-launchd.sh
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
# ACTIVATION — a merged change is not live on this machine until the code is pulled, and this
# program runs from a working copy here rather than from a deployed image (orchestrator ADR-0031).
# Best-effort by construction: the helper prints one `[activation]` line and returns 0 whatever it
# finds, so this job is never gated on being able to update itself. It re-execs this script when
# HEAD moves, because bash reads a script incrementally by byte offset and the file it just
# rewrote is this one.
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
# reach nobody: a pass that stops running, or that fails every hour, is silent. Armed AFTER
# activation, because `activate_checkout` may `exec` and an `exec` does not fire an EXIT handler.
# It reports and never gates — every failure inside it logs a line and returns 0. The check
# `sds-inert-landing` must exist in Healthchecks; until it does, arming logs one line naming that
# and this lane runs unalerted.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-inert-landing --finding 3 "$@"


# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe,
# which breaks the parse below.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

# ---- THE LANE'S CONTAINER (ADR-0059 increment 5, step 3g-1) -----------------------------------
# See "TWO HOSTS, ONE LAUNCHER" in the header. Every read takes the lane's one token from the
# environment (`sds_bws_value` with one argument), so it never appears in a process argument, and
# each secret is always read, never taken from the environment. Every failure names itself and
# exits with the code the header gives it; the dead-man switch, armed above, pings on the way out.
if [ -n "${BWS_ACCESS_TOKEN:-}" ]; then
  FROM=" (the lane's token)"
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
  REPOSITORIES="$(env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/inert-landing" repositories)"
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
  # an argument or a file, and the mint runs without the lane's token. The two permissions are what
  # the pass's one GitHub read uses: each repository's open pull requests
  # (`GitHubReader.open_pull_requests`, `pull_requests`), and `metadata`, which every installation
  # token carries.
  INERT_LANDING_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
    | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
        --app-id "$READ_APP_ID" "${TARGETS[@]}" \
        --permission metadata=read --permission pull_requests=read)"
  mint_rc=$?
  if [ "$mint_rc" -ne 0 ] || [ -z "$INERT_LANDING_GITHUB_TOKEN" ]; then
    echo "FATAL: could not mint the inert lane's GitHub read token from the read-only App (sds-github-mint exited $mint_rc)" >&2
    exit 1
  fi
  export INERT_LANDING_GITHUB_TOKEN

  INERT_LANDING_ORCHESTRATOR_TOKEN="$(sds_bws_value "$ORCHESTRATOR_SYSTEM_UUID")"
  if [ -z "$INERT_LANDING_ORCHESTRATOR_TOKEN" ]; then
    echo "FATAL: could not read the orchestrator system credential from BWS$FROM" >&2
    exit 1
  fi
  export INERT_LANDING_ORCHESTRATOR_TOKEN

  # THE ALERT TOKEN IS READ ONLY FOR A SUBMITTING RUN, the only one that sends, as on the Mac.
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

  # EVERY BWS READ IS DONE, so the lane's token reaches no program.
  unset BWS_ACCESS_TOKEN
  "$REPO_ROOT/.venv/bin/inert-landing" "$@"
  rc=$?
  exit "$rc"
fi

# SOURCED, not executed. `sds-token.sh` EXPORTS the value and prints nothing — its own header says
# so — so command-substituting it yields the empty string and every fetch below fails with a
# message about a missing environment variable rather than about a missing token.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/scripts/sds-token.sh"
fi
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  echo "FATAL: BWS_ACCESS_TOKEN not available for the orchestrator credential" >&2
  exit 1
fi
SDS_IDENTITY="$BWS_ACCESS_TOKEN"

# The broad machine account, for the change-manager project alone. Read into a DISTINCT variable
# rather than exported: `sds-token.sh` above respects an already-set `BWS_ACCESS_TOKEN`, so one
# ambient value would silently become both identities and no value of it works — the narrow one
# is denied the change-manager project and the broad one the orchestrator's.
BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
if [ -z "$BROAD_IDENTITY" ]; then
  echo "FATAL: no BWS identity for the change-manager credential (Keychain service Claude)" >&2
  exit 1
fi

if [ -z "${INERT_LANDING_CHANGE_MANAGER_TOKEN:-}" ]; then
  INERT_LANDING_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_UUID" "$BROAD_IDENTITY")"
  export INERT_LANDING_CHANGE_MANAGER_TOKEN
fi
if [ -z "${INERT_LANDING_ORCHESTRATOR_TOKEN:-}" ]; then
  INERT_LANDING_ORCHESTRATOR_TOKEN="$(sds_bws_value "$ORCHESTRATOR_SYSTEM_UUID" "$SDS_IDENTITY")"
  export INERT_LANDING_ORCHESTRATOR_TOKEN
fi

# THE GITHUB CREDENTIAL HAS NO BWS RECORD, exactly as the two proposers' and the ledger's do not.
# It is the operator's own `gh` login, which is a real dependency worth naming: it breaks the
# moment the login is re-issued, and nothing would say so but this script's exit 1.
if [ -z "${INERT_LANDING_GITHUB_TOKEN:-}" ]; then
  INERT_LANDING_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
  export INERT_LANDING_GITHUB_TOKEN
fi

# `set -e` is deliberately not used, so a failed fetch would otherwise leave these EMPTY and fall
# through — into an exit 2 that reports "unusable input" for what is actually a credential
# failure. Name each here, so the exit code means what this header says it means.
if [ -z "${INERT_LANDING_CHANGE_MANAGER_TOKEN:-}" ]; then
  echo "FATAL: could not read the change-manager credential from BWS" >&2
  exit 1
fi
if [ -z "${INERT_LANDING_ORCHESTRATOR_TOKEN:-}" ]; then
  echo "FATAL: could not read the orchestrator system credential from BWS" >&2
  exit 1
fi
if [ -z "${INERT_LANDING_GITHUB_TOKEN:-}" ]; then
  echo "FATAL: no GitHub token (set INERT_LANDING_GITHUB_TOKEN or run gh auth login)" >&2
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
    ;;
esac

"$REPO_ROOT/.venv/bin/inert-landing" "$@"
