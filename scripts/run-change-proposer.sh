#!/usr/bin/env bash
# One change-proposer pass: a change record for every deploying merge waiting to happen
# (ADR-0019 increment 5a).
#
# WHY THIS RUNS HOURLY RATHER THAN IN THE CHANGE WINDOW. Proposing is not acting. A record
# that exists before the window opens is the entire point of having one, and landing is
# window-gated on the other side by the orchestrator. Tying the producer to the window would
# mean a pull request opened at 09:00 has no record until 02:00, for no gain.
#
# AND A REPEAT PASS IS NOT A NO-OP, which is what makes the cadence worth having.
# change-manager answers 200 and re-evaluates: a record that has become conformant is
# approved, and one whose rollout workflow has moved is refreshed and, if it no longer
# conforms, revoked.
#
# BE PRECISE ABOUT WHAT THAT DOES AND DOES NOT BUY, because an earlier version of this
# comment said "this job is how a policy bump takes effect" and that overstates it in a way
# that matters. Re-evaluation is OPPORTUNISTIC, not a sweep: `_apply_policy` runs only inside
# a proposal, so narrowing the policy revokes NOTHING by itself. A record is re-evaluated only
# if this pass proposes that specific pull request again -- which needs it still open, still
# bot-authored, still on the trigger branch, its rollout workflow still transcribed, AND this
# job installed AND the credential minted. A record whose pull request has since closed or
# merged is never seen again and can never be revoked by any mechanism here.
#
# EXIT CODES, the whole interface a scheduled run has:
#   0  everything was measured and nothing was found.
#   1  the tool itself failed (a missing or unreadable credential, an unhandled error).
#   2  the tool ran but could not use its inputs: no scope resolved (including an unreadable
#      member record -- scope is the repositories the record grants `rollouts`, narrowed to those
#      with a transcribed workflow and rollback plan), a refused client, or a
#      rollout workflow whose current bytes nobody has transcribed — the lane's own scope,
#      which it could not establish. An unreadable read is here too: nothing was compared for
#      that repository, which is never the same as agreeing.
#   3  something was found — including a pull request whose rollout workflow nobody has
#      transcribed, which is the case that used to exit 0 in silence.
# 2 OUTRANKS 3, the same rule the rollout watcher states in the opposite direction: a pass that
# could not use some of its inputs cannot claim it found everything there was to find. A repository
# that has silently left the lane's scope is not a finding about a subject; it is the lane no longer
# covering what it says it covers.
# On a `--submit` run, 1, 2 and 3 all ping `/fail` with the report as the body, and 0 pings success.
# The program's code is this script's code.
#
# NOTHING HERE APPROVES. The credential is propose-scoped: change-manager refuses it every
# route that could move a record's status, and since increment 5a it refuses APPROVAL to
# every credential including the full one. Approval is conformance to a pinned policy.
#
# BARE INVOCATION IS A DRY RUN. `--submit` is the flag that separates reporting from writing, and
# this wrapper must not supply it for you: an operator reaching for "just look at what it would
# do" would otherwise mint approved change records and authority-grant rows in the tamper-evident
# chain. The scheduled job passes `--submit` explicitly, in the plist, where it is visible.
#
# TWO HOSTS, ONE LAUNCHER (ADR-0059 increment 4). A set `BWS_ACCESS_TOKEN` means the lane's
# container: `lane-run` passes the lane's own token as its one secret, and every secret is read with
# it, in the environment, never as a process argument:
#   - the observer bearer (BWS `f793576f-…`), every run, which also derives the mint's repositories;
#   - the change-manager PROPOSE bearer (BWS `acccb346-…`), only on `--submit`, as on the Mac;
#   - the read-only App `alobar-sds-lanes-read`'s key (BWS `314a966c-…`), piped straight into
#     `sds-github-mint`. The pass's GitHub token is minted from it each pass, dry run or not, for the
#     repositories `change-proposer repositories` prints and the three read permissions the
#     program's calls use. `gh` is never asked there.
# A repository list that can't be derived exits 2 before anything is minted (or the derivation's own
# code when it fails otherwise). Without the token this is the Mac's launchd job, unchanged until
# increment 6: two Keychain identities and the operator's `gh` login. Either way the BWS token is
# unset before the program runs, which needs neither it nor the App's key. The container runs the
# image's own code, so activation is the Mac's alone.
#
# Usage:
#   scripts/run-change-proposer.sh              # dry run: reports, writes nothing
#   scripts/run-change-proposer.sh --submit     # proposes
# Install as a scheduled job with:
#   scripts/install-change-proposer-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo).
# The PROPOSE-scoped change-manager bearer. Deliberately NOT change-manager/M2M_TOKEN: that
# one can reach every route, and a producer holding it would be a system asking itself for
# permission — the property ADR-0019 increment 4 shipped task zero to establish.
CHANGE_MANAGER_PROPOSE_UUID="${CHANGE_PROPOSER_BWS_UUID:-acccb346-4baa-43ec-a1d4-b4a400c048ee}"
# The read-only lane App `alobar-sds-lanes-read`: its key (base64 of the PEM) and its id, no secret.
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
# Skipped in the container, which has no `activate-checkout.sh`, rather than logged as missing.
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

# THIS CHECK IS AN OUTCOME ALARM (ADR-0021 amendment 2), as `sds-fork-sync-watch`'s is. `launchd`
# discards this script's exit code, so the codes above reach a person only through Healthchecks:
# `/start` before the pass, then success on 0 or `/fail` on anything else, with the pass's report
# as the body, which alert-hub delivers. A finding pages; a pass that stops pinging goes down after
# the grace. Only a `--submit` run pings: a dry run is a person looking, and must neither page nor
# reset the timer of a schedule that may have stopped. Resolved AFTER activation, because
# `activate_checkout` may `exec`. An unresolved check logs one line and never stops the pass.
CHECK_NAME="sds-change-proposer"
case " $* " in
  *" --submit "*) SUBMITTING=1 ;;
  *) SUBMITTING=0 ;;
esac
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_outcome_begin "$CHECK_NAME" change-proposer "$SUBMITTING"

# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a
# pipe, which breaks the parse below.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

# ONLY FETCHED FOR A WRITING RUN. A dry run reports what it would propose and sends nothing, so
# it must not need -- or touch -- the credential that could write. Fetching unconditionally would
# make the inspection path fail on a machine that cannot read the secret, which is precisely the
# machine on which somebody most wants to inspect before granting it one. `SUBMITTING` is set
# above, beside the alarm, which keys on the same flag.

# THE OBSERVER BEARER IS FETCHED ON EVERY RUN, dry or writing, because the member record is the
# scope authority: which repositories this lane considers is read from it, so even a dry run needs
# the read. Landing Part 3b reads the same bearer a second time, for `GET /api/v1/opened-reverts`:
# which open pull requests are the SDS's own reverts, filed as `dependency-revert` and never as a
# bump, so a dry run reports them truly too. It lives in `Rotation / Keepers`. On the Mac the narrow
# read-only `sds-operator` identity reads it by a direct grant and the broad identity cannot, so it
# has its own Keychain item and its own variable; in the container the lane's token reads it. It is
# exported only as `SDS_MEMBERS_OBSERVER_TOKEN`: this lane files no observation.
OBSERVER_BEARER_UUID="${CHANGE_PROPOSER_OBSERVER_BWS_UUID:-f793576f-e9aa-4f9d-8089-b4a000b9e2d5}"

if [ -n "${BWS_ACCESS_TOKEN:-}" ]; then
  # THE CONTAINER. Every secret is read with the lane's one token, already in the environment.
  if [ "$SUBMITTING" -eq 1 ]; then
    CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_PROPOSE_UUID")"
    [ -n "$CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN" ] || sds_outcome_fail \
        "FATAL: could not read the propose-scoped change-manager credential from BWS (the lane's token)"
    export CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN
  fi
  SDS_MEMBERS_OBSERVER_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID")"
  [ -n "$SDS_MEMBERS_OBSERVER_TOKEN" ] || sds_outcome_fail \
      "FATAL: could not read the orchestrator observer bearer from BWS (the lane's token)"
  export SDS_MEMBERS_OBSERVER_TOKEN

  # THE REPOSITORIES ARE THE PROGRAM'S, NOT LISTED HERE: `change-proposer repositories` reads the
  # member record and prints every repository the pass's GitHub calls read, the `rollouts` scope
  # and the scope sweep's transcribed rollout workflows, each once. AN UNDERIVABLE LIST MINTS
  # NOTHING, AND SAYS SO: an unreadable record or an empty list exits 2 (the lane couldn't
  # establish its scope) with the pass not run. Any other non-zero code is the tool failing (1) or
  # not running at all (126, 127, 137), and is passed through verbatim.
  REPOSITORIES="$(env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/change-proposer" repositories)"
  derive_rc=$?
  if [ "$derive_rc" -ne 0 ] && [ "$derive_rc" -ne 2 ]; then
    sds_outcome_fail \
        "the change proposer's repositories could not be derived: change-proposer repositories exited $derive_rc" \
        "$derive_rc"
  fi
  if [ "$derive_rc" -ne 0 ] || [ -z "$REPOSITORIES" ]; then
    sds_outcome_fail \
        "the change proposer's repositories could not be derived; nothing was minted or proposed" 2
  fi
  TARGETS=()
  while IFS= read -r repository; do
    TARGETS+=(--repository "$repository")
  done <<< "$REPOSITORIES"

  # THE GITHUB TOKEN IS MINTED EACH PASS, dry run or not, after the derivation, because the mint
  # names its repositories. The App's key goes from the BWS read straight to the mint's stdin in its
  # stored form, base64 of the PEM; it is never in a variable, an argument or a file, and the mint
  # runs without the lane's token. The three permissions are what the program's GET calls use
  # (`deploy_watcher/github.py`, through `change_proposer/cli.py`): the rollout workflow's bytes
  # (`contents`), the open pull requests and one pull request's disposition (`pull_requests`), and
  # `metadata`, which GitHub grants with every token. Any preset value is replaced, so the pass runs
  # only on the token it minted.
  CHANGE_PROPOSER_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
    | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
        --app-id "$READ_APP_ID" "${TARGETS[@]}" \
        --permission metadata=read --permission contents=read --permission pull_requests=read)"
  mint_rc=$?
  if [ "$mint_rc" -ne 0 ] || [ -z "$CHANGE_PROPOSER_GITHUB_TOKEN" ]; then
    sds_outcome_fail "FATAL: could not mint the change proposer's GitHub token from the read-only App (sds-github-mint exited $mint_rc)"
  fi
  export CHANGE_PROPOSER_GITHUB_TOKEN
else
  # THE BROAD IDENTITY IS NEVER EXPORTED. The propose keeper is in `Rotation / Keepers`, which only the
  # broad machine account reads (a direct grant), so this launcher reads that account's token into a
  # local variable and hands it to the one `bws` call that needs it, as `run-estate-landing.sh` does.
  # It used to export it as `BWS_ACCESS_TOKEN`, which made it the identity of every process started
  # after it, the tool and `gh` included, though neither reads BWS.
  if [ "$SUBMITTING" -eq 1 ]; then
    BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
      -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
    [ -n "$BROAD_IDENTITY" ] || sds_outcome_fail \
      "FATAL: no BWS identity for the change-manager credential (Keychain service Claude)"
    CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_PROPOSE_UUID" "$BROAD_IDENTITY")"
    export CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN
  fi

  SDS_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
  [ -n "$SDS_IDENTITY" ] || sds_outcome_fail \
    "FATAL: no narrow BWS identity for the observer bearer (service Claude)"
  SDS_MEMBERS_OBSERVER_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID" "$SDS_IDENTITY")"
  [ -n "$SDS_MEMBERS_OBSERVER_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the orchestrator observer bearer from BWS (narrow)"
  export SDS_MEMBERS_OBSERVER_TOKEN

  # `set -e` is deliberately not used here (the watcher's launcher does the same), so a failed
  # fetch would otherwise leave this EMPTY and fall through. The tool refuses an empty credential
  # and would exit 2 -- which is fail-closed but reports "unusable input" for what is actually a
  # credential failure. Name it here so the exit code means what this header says it means.
  if [ "$SUBMITTING" -eq 1 ] && [ -z "${CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN:-}" ]; then
    sds_outcome_fail "FATAL: could not read the propose-scoped change-manager credential from BWS"
  fi

  # ON THE MAC THE GITHUB CREDENTIAL IS THE OPERATOR'S `gh` LOGIN, until increment 6 retires this
  # path: a scheduled job resting on an interactive login breaks the moment the login is
  # re-issued. The scheduled run then pings `/fail` with the reason (ADR-0021 amendment 2). The
  # container never takes it (above).
  if [ -z "${CHANGE_PROPOSER_GITHUB_TOKEN:-}" ]; then
    CHANGE_PROPOSER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
    export CHANGE_PROPOSER_GITHUB_TOKEN
  fi
  if [ -z "${CHANGE_PROPOSER_GITHUB_TOKEN:-}" ]; then
    sds_outcome_fail "FATAL: no GitHub token (set CHANGE_PROPOSER_GITHUB_TOKEN or run gh auth login)"
  fi
fi
# The program needs no BWS identity, so the lane's token never reaches it.
unset BWS_ACCESS_TOKEN

"$REPO_ROOT/.venv/bin/change-proposer" "$@" > "$SDS_OUTCOME_REPORT" 2>&1
rc=$?
sds_outcome_end "$rc"
