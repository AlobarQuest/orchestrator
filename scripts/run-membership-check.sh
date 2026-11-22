#!/usr/bin/env bash
# One consistency pass over the SDS member record: `membership-check` compares the record with
# every repository's `factory-target.toml`, change-manager's inert and deploying landing
# populations, the lists the rollout and rotation lanes carry, the
# rollback table, the standing dependency-update packages, the registry rows and files, and each
# member's auto-merge state (ADR-0058; the full list is `membership_check/consistency.py`).
#
# DAILY, AFTER THE LANDING LEDGER (07:30): 07:47 in the lane's container
# (`lanes/membership-check/crontab`). The lanes take their scope from the record; this is what
# notices that a list somewhere still says something else.
#
# THIS CHECK IS AN OUTCOME ALARM (ADR-0021 amendment 2). A
# disagreement is a lane about to act on a population the record doesn't hold, so every non-zero
# exit pings `/fail` with the report as the body. A pass that stops pinging goes down after the
# grace, so while the check is up it is a liveness check too (ADR-0021 amendment 2). The shared
# helper's outcome-alarm functions run the pass; its arming is not used, because it pings success
# on a lane's finding code.
#
# EXIT CODES, the whole interface a scheduled run has (`membership_check/cli.py`):
#   0  the record and every list agree.
#   1  the tool itself failed (a missing credential, an unhandled error).
#   2  at least one disagreement. Disagreements outrank gaps.
#   3  nothing disagrees, but a source could not be read.
# 1, 2 and 3 all ping `/fail`. The program's code is this script's code, except that in the
# container a repository list that can't be derived exits 3 (or `sds_scope`'s own failure code)
# before the program runs.
#
# THE LANE RUNS ONLY IN ITS CONTAINER (ADR-0059 increment 3, amendment 5). A set
# `BWS_ACCESS_TOKEN` means the lane's container: `lane-run` passes the lane's own token as its one
# secret. Without it the launcher refuses, through the outcome alarm.
#
# CREDENTIALS IN THE CONTAINER, every one read with the lane's token, in the environment, never as
# a process argument:
#   - the observer bearer (BWS `f793576f-…`), which also derives the mint's repositories;
#   - the change-manager READ bearer (BWS `314f276d-…`);
#   - the read-only App `alobar-sds-lanes-read`'s key (BWS `314a966c-…`), piped straight into
#     `sds-github-mint`. The pass's GitHub token is minted from it each pass, narrowed to the member
#     record's observed repositories and three read permissions, and exported as both
#     `SDS_MEMBERS_GITHUB_TOKEN` and `GH_TOKEN` (the fork list's `gh api` read). `gh` is never asked
#     for a login.
#   The Healthchecks API key (BWS `51509d5e-…`) is never read there: no lane holds it.
# The container also clones intent-packages anonymously each pass into the pass's `HOME`, for the
# standing dependency-update packages, and points `BUMP_PROPOSER_PACKAGES_CHECKOUT` at it.
#
# Usage:
#   scripts/run-membership-check.sh
set -uo pipefail

CHECK_NAME="sds-membership-check"
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER bearer
CHANGE_MANAGER_READ_UUID="314f276d-55ca-4ddc-a24d-b4a3013508cd"   # change-manager READ bearer
# The read-only lane App `alobar-sds-lanes-read`: its key (base64 of the PEM) and its id, no secret.
READ_APP_KEY_UUID="314a966c-5654-4e3c-8358-b4fd01268775"
READ_APP_ID="5684758"
# intent-packages, the repository `PACKAGES_REPOSITORY` names (`src/bump_proposer/standing.py`),
# spelled as a literal; `tests/scripts/test_run_membership_check.py` holds the two equal.
PACKAGES_CLONE_URL="https://github.com/AlobarQuest/intent-packages.git"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Outside the lane's container this refuses before arming the dead-man switch or reading anything:
# armed here, the switch would ping the production check.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  echo "FATAL: no BWS_ACCESS_TOKEN: the membership-check lane runs only in its container, through lane-run" >&2
  exit 1
fi

# The ping URL, resolved by exact check name.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
# Every failure before the check runs is the tool failing: reported through the same check, with
# the reason as the ping body (`sds_outcome_fail`).
sds_outcome_begin "$CHECK_NAME" membership-check

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"
if [ -n "${BWS_ACCESS_TOKEN:-}" ]; then
  # THE CONTAINER. Every secret is read with the lane's one token, already in the environment.
  SDS_MEMBERS_OBSERVER_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID")"
  [ -n "$SDS_MEMBERS_OBSERVER_TOKEN" ] || sds_outcome_fail \
      "FATAL: could not read the orchestrator observer credential from BWS (the lane's token)"
  SDS_MEMBERS_CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_READ_UUID")"
  [ -n "$SDS_MEMBERS_CHANGE_MANAGER_TOKEN" ] || sds_outcome_fail \
      "FATAL: could not read the change-manager READ credential from BWS (the lane's token)"

  # THE REPOSITORIES ARE THE MEMBER RECORD'S, NOT LISTED HERE: `sds_scope ledger` prints the
  # record's observed repositories (onboarding and member), each once: the population the check's
  # declaration and registry-file reads cover (`src/membership_check/cli.py`). Its auto-merge read
  # also covers retired members, which the mint doesn't name, so a retired member's state is a gap
  # in the container. intent-packages and factory-runner are members, so the fork list's read is
  # covered too. The mint
  # never names a public non-member: the listing reads those through the same token. AN UNDERIVABLE
  # LIST MINTS NOTHING, AND SAYS SO: an unreadable record or an empty population exits 3 (a source
  # couldn't be read) with the program not run. Any other non-zero code is the tool failing (1) or
  # not running at all (126, 127, 137), and is passed through verbatim.
  REPOSITORIES="$(cd "$REPO_ROOT" && SDS_SCOPE_ORCHESTRATOR_TOKEN="$SDS_MEMBERS_OBSERVER_TOKEN" \
    env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/python" -m scripts.sds_scope ledger)"
  scope_rc=$?
  if [ "$scope_rc" -ne 0 ] && [ "$scope_rc" -ne 3 ]; then
    sds_outcome_fail \
        "the membership check's repositories could not be derived: sds_scope exited $scope_rc" \
        "$scope_rc"
  fi
  if [ "$scope_rc" -ne 0 ] || [ -z "$REPOSITORIES" ]; then
    sds_outcome_fail \
        "the membership check's repositories could not be derived; nothing was minted or checked" 3
  fi
  TARGETS=()
  while IFS= read -r repository; do
    TARGETS+=(--repository "$repository")
  done <<< "$REPOSITORIES"

  # THE GITHUB TOKEN IS MINTED EACH PASS, after the derivation, because the mint names its
  # repositories. The App's key goes from the BWS read straight to the mint's stdin in its stored
  # form; it is never in a variable, an argument or a file, and the mint runs without the lane's
  # token. The three permissions are what the check's GitHub reads use: `contents` for
  # `factory-target.toml`, the registry files, the workflow files and the fork list's file;
  # `metadata` for the listings and each repository. `administration` is minted but no read is
  # known to need it: the auto-merge setting is read over GraphQL (`auto_merge.py`), because
  # GitHub serves REST's merge-related settings only to a token with `contents:write`. Any preset
  # value is replaced, so the pass runs only on the token it minted.
  SDS_MEMBERS_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
    | env -u BWS_ACCESS_TOKEN "$REPO_ROOT/.venv/bin/sds-github-mint" \
        --app-id "$READ_APP_ID" "${TARGETS[@]}" \
        --permission metadata=read --permission contents=read --permission administration=read)"
  mint_rc=$?
  if [ "$mint_rc" -ne 0 ] || [ -z "$SDS_MEMBERS_GITHUB_TOKEN" ]; then
    sds_outcome_fail "FATAL: could not mint the membership check's GitHub token from the read-only App (sds-github-mint exited $mint_rc)"
  fi
  # `gh` reads `GH_TOKEN`, so the fork list's `gh api` read uses the minted token too.
  GH_TOKEN="$SDS_MEMBERS_GITHUB_TOKEN"

  # THE STANDING PACKAGES COME FROM AN ANONYMOUS SHALLOW CLONE OF intent-packages (ADR-0059
  # amendment 5), into the pass's `HOME`, which `lane-run` makes new and deletes after the pass.
  # git runs with no global or system configuration and no prompt, so no credential helper or
  # stored login applies, and none of the pass's secrets is in its environment: the clone is public
  # and carries no credential. A FAILED CLONE IS NOT THIS LAUNCHER'S FAILURE: its error goes into
  # the report, the variable is set anyway, and the program records "the packages checkout couldn't
  # be read" as a gap, so the pass exits 3 (or 2 if something disagrees). The report's clone line
  # names the commit read.
  _sds_anonymous_git() {
    env -u BWS_ACCESS_TOKEN -u GH_TOKEN -u SDS_MEMBERS_GITHUB_TOKEN \
        -u SDS_MEMBERS_OBSERVER_TOKEN -u SDS_MEMBERS_CHANGE_MANAGER_TOKEN \
        GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 git "$@"
  }
  BUMP_PROPOSER_PACKAGES_CHECKOUT="$HOME/intent-packages"
  if clone_error="$(_sds_anonymous_git clone --quiet --depth 1 --branch main \
      "$PACKAGES_CLONE_URL" "$BUMP_PROPOSER_PACKAGES_CHECKOUT" 2>&1)"; then
    clone_commit="$(_sds_anonymous_git -C "$BUMP_PROPOSER_PACKAGES_CHECKOUT" rev-parse HEAD 2>&1)"
    echo "[membership-check] intent-packages clone: commit $clone_commit" >> "$SDS_OUTCOME_REPORT"
  else
    clone_rc=$?
    echo "[membership-check] intent-packages clone failed: git exited $clone_rc:" \
         "$(printf '%s' "$clone_error" | tr '\n' ' ')" >> "$SDS_OUTCOME_REPORT"
  fi
  export GH_TOKEN BUMP_PROPOSER_PACKAGES_CHECKOUT
fi
export SDS_MEMBERS_OBSERVER_TOKEN SDS_MEMBERS_CHANGE_MANAGER_TOKEN SDS_MEMBERS_GITHUB_TOKEN
# The program needs no BWS identity, so the lane's token never reaches it.
unset BWS_ACCESS_TOKEN

# APPENDED, never truncated: the report already holds the container's clone line.
"$REPO_ROOT/.venv/bin/membership-check" >> "$SDS_OUTCOME_REPORT" 2>&1
sds_outcome_end "$?" --body-on-success
