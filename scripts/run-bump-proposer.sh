#!/usr/bin/env bash
# One bump-proposer pass: a dependency bump the estate will not land by itself becomes proposed
# work (ADR-0028). WHICH BUMPS THOSE ARE IS READ FROM change-manager's LANDING POLICY (ADR-0038),
# not from a transcription of the GitHub Actions workflow that used to enforce it -- that workflow
# is being removed and the orchestrator becomes the merger, which would otherwise have left this
# pass reading a registry with no subject.
#
# WHAT A WRITING PASS ACTUALLY DOES, because it is more than the siblings do. It edits two
# lines of a standing package in the intent-packages checkout, takes the revision through
# that repository's audited lifecycle, re-pins its hash fixture, COMMITS the three files,
# PUBLISHES that commit to the authoring repository's default branch (ADR-0033), and proposes
# a work record in change-manager. It does not approve the record: a person does that, which
# is the decision ADR-0028 keeps and the one this producer sits on the other side of.
#
# PUBLISHING IS THE COMMIT FINISHED, NOT A SECOND ACT. Until ADR-0033 the pass stopped at the
# commit and nobody was told to move the branch, so the revision got no CI, the `source_commit`
# the intake records named a commit only this machine held, and local `main` drifted from
# origin under every other lane that reads this checkout. It surrenders no gate: that branch
# takes a direct push and reports its required checks afterwards whoever performs it.
#
# IT REFUSES A DIRTY CHECKOUT. Committing is not tidiness -- the orchestrator's intake payload
# records `source_commit` as that checkout's git HEAD, so a revision left uncommitted is
# registered against a commit that does not contain it. Refusing a dirty tree is what stops
# this sweeping somebody else's work-in-progress into a commit it wrote the message for.
#
# WHY HOURLY IS WRONG FOR THIS ONE, unlike the deploy producer. Every writing pass commits to
# a checkout on this machine, so a pass that finds nothing to do is free and a pass that finds
# something is a commit somebody has to notice. Daily, and before the carry's own 07:05 run
# -- though be precise about what that ordering buys, because it is less than it reads:
# the carry fires once, at 07:05, and reads only APPROVED records, so a record proposed
# here is carried the same day only if a person approves it within fifteen minutes.
#
# EXIT CODES, the whole interface a scheduled run has:
#   0  nothing arose that needs a person for an anomalous reason. NOT "the pass did
#      nothing": a pass that revised a package, committed it and proposed a record also
#      exits 0, because `proposed` is this program's ordinary output rather than a
#      finding. Read the lines to learn whether anything was written.
#   1  the tool itself failed (a missing or unreadable credential).
#   2  the tool ran but could not use its inputs (no standing packages, a dirty checkout, a
#      checkout carrying a commit a previous pass wrote and could not publish -- a state no
#      further revision may be built on, and one only a person can resolve -- or, since
#      ADR-0038, a landing policy it could not read: unreachable, refused, malformed, declaring
#      no inert population at all, or declaring one that does not permit the update bot whose
#      pull requests are the only ones this pass can see. One declaration covers every
#      repository, so a rule this pass cannot read stops the pass rather than one repository.
#      An absent declaration is a REFUSAL and not a waiver: it is a version that did not decide
#      the question, and this pass will not guess what it would have decided).
#   3  something was found -- a standing package targeting a repository the landing policy does
#      not declare inert, a pull request whose title and update trailer disagree, a record
#      stranded by a bump that moved, a refused proposal, or a revision that was committed and
#      could not be published (ADR-0033: a failed publish is a finding, not a warning; the line
#      names the sha it stranded).
#
# BARE INVOCATION IS A DRY RUN. `--submit` separates reporting from writing, and this wrapper
# must not supply it: an operator reaching for "just look at what it would do" would otherwise
# mint package revisions in a tamper-evident chain that has no undo.
#
# Usage:
#   scripts/run-bump-proposer.sh              # dry run: reports, writes nothing
#   scripts/run-bump-proposer.sh --submit     # revises, approves by policy, proposes
set -uo pipefail

# The PROPOSE-scoped change-manager bearer, shared with the deploy producer. Deliberately NOT
# the full one: this program must never be able to approve the record it writes, or the human
# decision ADR-0028 keeps would be one the machine could take for itself.
CHANGE_MANAGER_PROPOSE_UUID="${BUMP_PROPOSER_BWS_UUID:-acccb346-4baa-43ec-a1d4-b4a400c048ee}"

# The READ-scoped change-manager bearer (ADR-0038). A SECOND credential rather than a wider use of
# the one above, and the reason is the property this script states two paragraphs down: a dry run
# must not need -- or touch -- the credential that could write. Since ADR-0038 a dry run must READ
# change-manager, because the rule it reports against is declared there rather than transcribed
# into this repository; had this credential not already existed, that property would have had to
# be traded for the read. Measured 2026-08-31, and the pair is what makes it mean something:
# `GET /api/landing-policy` answers 200 with this bearer and 401 with a garbage one, while
# `POST /api/deploy-changes` answers 403 with this bearer and 422 -- request validation, i.e.
# reached -- with the propose one. The scope is enforced by change-manager, not merely declared.
CHANGE_MANAGER_READ_UUID="${BUMP_PROPOSER_READ_BWS_UUID:-314f276d-55ca-4ddc-a24d-b4a3013508cd}"

# The orchestrator's OBSERVER bearer (G1+G2). The signal->work contract requires every proposed
# record to name the observation that caused it, so a writing pass files that fact first -- and
# this is the credential whose ENTIRE write surface is `POST /api/v1/observations`. Deliberately
# not the SYSTEM bearer, which can transition units and land pull requests: this lane must not be
# able to, and the whole point of the record it writes is that a person decides.
#
# It is the same bearer every observe-and-report lane holds, by design, which is why the three
# variables exported for it below are the estate-wide spelling rather than a `BUMP_PROPOSER_` one.
OBSERVER_BEARER_UUID="${BUMP_PROPOSER_OBSERVER_BWS_UUID:-f793576f-e9aa-4f9d-8089-b4a000b9e2d5}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export BUMP_PROPOSER_PACKAGES_CHECKOUT="${BUMP_PROPOSER_PACKAGES_CHECKOUT:-$HOME/Projects/intent-packages}"

# THE DEAD-MAN SWITCH. `launchd` discards this script's exit code, so the codes documented above
# reach nobody -- and this is the lane that proves it: it sat UNSCHEDULED for eight days with
# nothing saying so. There is no `activate_checkout` here to arm after, so this is the first thing
# the pass does. It reports and never gates -- every failure inside it logs a line and returns 0.
#
# ARMED BEFORE THE BWS BLOCK BELOW, deliberately. That block can `exit 1` on a Keychain item this
# machine does not have, which is exactly the silent morning this switch exists to report; arming
# after it would report only the failures the credential fetch survived. The helper reads its own
# broad identity into a local variable rather than exporting `BWS_ACCESS_TOKEN`, so it cannot
# satisfy -- or corrupt -- the fetch below.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-bump-proposer --finding 3 "$@"

# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a
# pipe, which breaks the parse below. The IDENTITY IS THE SECOND ARGUMENT rather than an ambient
# export -- see the block underneath.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE BWS_ACCESS_TOKEN="$2" \
    bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

# TWO BWS IDENTITIES SINCE G1+G2, AND NEITHER CAN READ THE OTHER'S SECRET. The change-manager
# bearers live in a project only the BROAD machine account can read; the orchestrator's observer
# bearer lives in `SDS Operator`, readable only by the narrow read-only `sds-operator` account.
# Each is read directly from its own Keychain item into its own variable and passed to the fetch
# that needs it, so no identity is ever ambient and each fetch says which one it used.
#
# THIS SCRIPT USED TO EXPORT ONE, and that was correct while every secret it read belonged to the
# broad account. The upgrade is NOT "add a source of sds-token.sh": that helper respects an
# already-set `BWS_ACCESS_TOKEN` and exports, so alongside the old bootstrap one ambient value
# would have served as both identities -- after which NO value of it works, a failure that appears
# only in an operator's shell and never under launchd.
#
# NAME THE FAILURE, because it does not name itself: `bws` answers `404 Resource not found` for a
# secret an identity is DENIED, which reads as a missing secret rather than as the wrong identity
# and has cost this estate a diagnosis more than once.
BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
if [ -z "$BROAD_IDENTITY" ]; then
  echo "FATAL: no broad BWS identity for the change-manager credentials (service Claude)" >&2
  exit 1
fi

# FETCHED ON EVERY RUN, dry or writing. The landing policy is what this pass reports against, so
# a dry run needs it; it is READ-scoped, so needing it surrenders nothing. Fetched BEFORE the
# propose credential so that a run which can read but not write fails on the write path, where
# the message names the credential that is actually missing.
BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN="$(_bws_value "$CHANGE_MANAGER_READ_UUID" "$BROAD_IDENTITY")"
export BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN
if [ -z "${BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN:-}" ]; then
  echo "FATAL: could not read the READ-scoped change-manager credential from BWS (broad)" >&2
  exit 1
fi

# ONLY FETCHED FOR A WRITING RUN. A dry run reports what it would do and sends nothing, so it
# must not need -- or touch -- a credential that could write. THE OBSERVER BEARER IS ON THIS SIDE
# OF THAT LINE: filing an observation is a write, and a dry run proposes nothing, so it has no
# cause to file.
case " $* " in
  *" --submit "*) NEEDS_CREDENTIAL=1 ;;
  *) NEEDS_CREDENTIAL=0 ;;
esac

if [ "$NEEDS_CREDENTIAL" -eq 1 ]; then
  BUMP_PROPOSER_CHANGE_MANAGER_TOKEN="$(_bws_value "$CHANGE_MANAGER_PROPOSE_UUID" "$BROAD_IDENTITY")"
  export BUMP_PROPOSER_CHANGE_MANAGER_TOKEN

  SDS_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
  if [ -z "$SDS_IDENTITY" ]; then
    echo "FATAL: no narrow BWS identity for the observer bearer (service Claude)" >&2
    exit 1
  fi
  ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
  ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
  ORCHESTRATOR_API_TOKEN="$(_bws_value "$OBSERVER_BEARER_UUID" "$SDS_IDENTITY")"
  export ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN
fi

# `set -e` is deliberately not used, so a failed fetch would otherwise leave these EMPTY and
# fall through. The tool refuses an empty credential and would exit 2 -- fail-closed, but
# reporting "unusable input" for what is actually a credential failure. Named separately so the
# message points at the identity that could not read, which is the question a 404 from `bws`
# does not answer by itself.
if [ "$NEEDS_CREDENTIAL" -eq 1 ] && [ -z "${BUMP_PROPOSER_CHANGE_MANAGER_TOKEN:-}" ]; then
  echo "FATAL: could not read the propose-scoped change-manager credential from BWS (broad)" >&2
  exit 1
fi
if [ "$NEEDS_CREDENTIAL" -eq 1 ] && [ -z "${ORCHESTRATOR_API_TOKEN:-}" ]; then
  echo "FATAL: could not read the orchestrator observer bearer from BWS (narrow)" >&2
  exit 1
fi

# THE GITHUB CREDENTIAL HAS NO BWS RECORD, exactly as the deploy producer's and the ledger's do
# not. It falls back to `gh auth token`, an interactive login: a scheduled job resting on one
# breaks the moment the login is re-issued, and nothing would say so but this script's exit 1.
# That is a gap, not a design.
if [ -z "${BUMP_PROPOSER_GITHUB_TOKEN:-}" ]; then
  BUMP_PROPOSER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
  export BUMP_PROPOSER_GITHUB_TOKEN
fi
if [ -z "${BUMP_PROPOSER_GITHUB_TOKEN:-}" ]; then
  echo "FATAL: no GitHub token (set BUMP_PROPOSER_GITHUB_TOKEN or run gh auth login)" >&2
  exit 1
fi

"$REPO_ROOT/.venv/bin/bump-proposer" "$@"
