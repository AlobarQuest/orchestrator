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
#   2  the tool ran but could not use its inputs (no scope resolved, a refused client).
#   3  something was found — including a pull request whose rollout workflow nobody has
#      transcribed, which is the case that used to exit 0 in silence.
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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The change-manager tokens live in a BWS project the narrow `sds-operator` account behind
# scripts/sds-token.sh cannot read, so this launcher bootstraps with the broad machine
# account — named rather than silently different, exactly as the rollout watcher's launcher
# names it. Narrowing it is open work for both.
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  BWS_ACCESS_TOKEN="$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)"
  export BWS_ACCESS_TOKEN
fi
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  echo "FATAL: BWS_ACCESS_TOKEN not found in Keychain (service Claude)" >&2
  exit 1
fi

# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR /
# CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a
# pipe, which breaks the parse below.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

# ONLY FETCHED FOR A WRITING RUN. A dry run reports what it would propose and sends nothing, so
# it must not need -- or touch -- the credential that could write. Fetching unconditionally would
# make the inspection path fail on a machine that cannot read the secret, which is precisely the
# machine on which somebody most wants to inspect before granting it one.
case " $* " in
  *" --submit "*) NEEDS_CREDENTIAL=1 ;;
  *) NEEDS_CREDENTIAL=0 ;;
esac

if [ "$NEEDS_CREDENTIAL" -eq 1 ]; then
  CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN="$(_bws_value "$CHANGE_MANAGER_PROPOSE_UUID")"
  export CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN
fi

# `set -e` is deliberately not used here (the watcher's launcher does the same), so a failed
# fetch would otherwise leave this EMPTY and fall through. The tool refuses an empty credential
# and would exit 2 -- which is fail-closed but reports "unusable input" for what is actually a
# credential failure. Name it here so the exit code means what this header says it means.
if [ "$NEEDS_CREDENTIAL" -eq 1 ] && [ -z "${CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN:-}" ]; then
  echo "FATAL: could not read the propose-scoped change-manager credential from BWS" >&2
  exit 1
fi

# THE GITHUB CREDENTIAL HAS NO BWS RECORD, exactly as the rollout watcher's and the landing
# ledger's do not. It falls back to `gh auth token`, an interactive login: a scheduled job
# resting on one breaks the moment the login is re-issued, and nothing would say so but this
# script's exit 1. That is a gap, not a design.
if [ -z "${CHANGE_PROPOSER_GITHUB_TOKEN:-}" ]; then
  CHANGE_PROPOSER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
  export CHANGE_PROPOSER_GITHUB_TOKEN
fi
if [ -z "${CHANGE_PROPOSER_GITHUB_TOKEN:-}" ]; then
  echo "FATAL: no GitHub token (set CHANGE_PROPOSER_GITHUB_TOKEN or run gh auth login)" >&2
  exit 1
fi

"$REPO_ROOT/.venv/bin/change-proposer" "$@"
