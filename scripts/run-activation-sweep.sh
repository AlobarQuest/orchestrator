#!/usr/bin/env bash
# BOTH of ADR-0030's lanes, in one invocation over the operator machine's working copies.
#
#   sweep  one observation per enrolled working copy: what it will execute at its next start,
#          and whether that is what was merged. Six checkouts, OBSERVER credential.
#   bind   one release artifact binding per completed work unit whose landing this machine has
#          actually pulled, AND the activation check that follows it: whether the artifact just
#          bound is what the next start will execute. FOUR checkouts, SYSTEM credential -- see
#          BINDABLE below for why the two hosted-application repositories are absent.
#
# THE ACTIVATION CHECK NEEDS NO NEW CREDENTIAL, and that is the answer to where it belongs. It is
# recorded as a deployment observation, which only the SYSTEM actor may write -- and this lane
# already holds SYSTEM because binding does. The sibling sweep's OBSERVER credential may write to
# `/api/v1/observations` and nothing else, and that narrowness is what the estate's negative
# tests certify, so it stays exactly as it is.
#
# Both read local git and neither PULLS: ADR-0030 stops at recording, and making the machine
# self-update is a separate decision with its own authority argument. The subcommand allowlist in
# `activation_sweep/checkout.py` makes that structural rather than a matter of what this script
# happens to pass. Both FETCH -- without that, `behind` is measured against stale remote-tracking
# refs and is always zero, so the control would report the machine current because it never
# looked.
#
# EXIT CODES, and they are the whole interface a scheduled run has. Both lanes report on this
# scale, and the worst outcome across the two is what this script exits with:
#   0  every checkout was measured and filed; the machine is current and clean, and every unit
#      whose landing it holds is bound.
#   1  the tool itself failed (a missing credential, a missing binary, an unusable URL).
#   2  something was found: a checkout is behind its upstream, carries modified tracked files, or
#      holds a bound artifact that is not fully activated -- a console entry point that was never
#      installed, or an environment that does not match its lockfile. Somebody has to act.
#      WAITING is still not this: a unit whose landing is not yet pulled is the ordinary state
#      between a unit completing and the next pull, and so is a SUPERSEDED artifact, whose window
#      for being observed closed when HEAD moved past it.
#   3  some checkout could not be measured, or a row could not be filed, so the answer is missing
#      rather than clean. 3 outranks 2.
#
# Usage:
#   scripts/run-activation-sweep.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-activation-sweep-launchd.sh
set -uo pipefail

# BWS UUIDs (values are fetched at runtime; never stored in this repo). See .bws-secrets.toml.
#
# TWO BEARERS, because ADR-0030 names two lanes with two different rights. The sweep records
# observations and is OBSERVER; binding a release artifact is admitted only for the SYSTEM actor.
# Both live in the `SDS Operator` project, so ONE BWS identity reads both and there is no second
# Keychain item to keep in step -- the two-identity trap this estate has hit in the launchers and
# in `factory decompose` does not arise here, and would if either secret ever moved project.
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER
SYSTEM_BEARER_UUID="221a48d5-3f29-4898-b300-b4820140c880"     # orchestrator-system  SYSTEM

# THE ENROLLED WORKING COPIES: the SDS targets. One condition, not two.
#
# CORRECTED 2026-08-24 on Devon's ruling. The list shipped with NINE entries under a rule --
# "a repository is enrolled when its consumers begin a fresh process in the ordinary course" --
# that NOBODY RATIFIED. ADR-0030 recorded that rule as Devon's; he decided only Q1 (watcher
# first). Q2 was answered by HQ, which wrote "no decision needed from you"; the build session
# then generalised HQ's list into that rule, which admitted `email-capture`, `FacelessTT` and
# `~/.claude` -- none of them SDS repositories.
#
# The rule is now the estate's OWN definition of an SDS target, already ratified elsewhere and
# not reinvented here: the repository self-identifies in `PROJECT.md` frontmatter (ADR-0015) and
# the conformance kit judges it ready. `project-standards` declares `factory_target: false` and
# is therefore correctly absent; `orchestrator` self-declares and is not on the dispatch
# allowlist only because it IS the system and cannot be dispatched to.
#
# An earlier draft of this list added a SECOND condition -- "something on this machine executes
# from the working copy" -- and dropped `change-manager` and `brain` on it. That condition was
# invented here and is not the estate's. It was also weaker than it looked: a build session's
# first act is `git worktree add ... main`, which branches from the LOCAL default branch, so a
# stale checkout of a hosted-application repository starts a session on stale code, and nothing
# else watches those two. The measurement this sweep files is the state of a working copy
# relative to its upstream, which is true and useful for every SDS target.
#
# TODO(scope-registry): this list is the FOURTH place the estate answers "which repos are in
# SDS scope", after `PROJECT.md` frontmatter, ORCHESTRATOR_DISPATCH_ALLOWED_TARGET_REPOSITORIES
# and the presence of a caller workflow -- and those three already disagree. It should be
# DERIVED rather than written here; see the scope-registry spec.
CHECKOUTS=(
  "$HOME/Projects/orchestrator"
  "$HOME/Projects/intent-packages"
  "$HOME/Projects/security-standards"
  "$HOME/Projects/infraops-mcp-server"
  "$HOME/Projects/change-manager"
  "$HOME/Projects/brain"
)

# THE UNIT-CAUSED LANE'S CHECKOUTS ARE A STRICT SUBSET, and the two absentees are the whole point
# of the distinction. `change-manager` and `brain` become live when a hosted application swaps a
# container image -- the FIRST activation model, which already has its own release artifact with a
# registry digest. Binding a machine-local artifact for either would assert that a working copy on
# this machine is what serves them, which is false, and it is exactly the collapse the `kind`
# discriminator exists to prevent. They are still SWEPT: what the sweep files is the state of a
# working copy relative to its upstream, which is true and useful for every SDS target, and a
# build session's `git worktree add ... main` branches from the LOCAL default branch whether or
# not anything else runs from it.
BINDABLE=(
  "$HOME/Projects/orchestrator"
  "$HOME/Projects/intent-packages"
  "$HOME/Projects/security-standards"
  "$HOME/Projects/infraops-mcp-server"
)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ACTIVATION — this job pulls itself before it runs, exactly as its siblings do (ADR-0031), and
# the obvious objection deserves an answer: does the control not then heal the very repository it
# measures? It does, for `orchestrator` alone, and that is the correct behaviour rather than a
# blind spot. Activation is best-effort and its failures are SILENT BY DESIGN -- the helper prints
# one line and returns 0 whatever it finds -- so if it cannot pull, nothing is healed and the
# sweep reports this checkout behind, which is the ADR-0031 control working. If it can pull, the
# machine genuinely is current and saying so is true. The other eight are never touched.
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
# A DRY RUN MUST NOT ACTIVATE, and this is not fussiness. `activate_checkout` fast-forwards the
# working copy, may re-run `uv sync`, and re-execs -- so an operator running the installer's own
# `--dry-run` verification step, which is documented as writing nothing, would get a repository
# mutation they were told would not happen. On a feature branch the helper returns early, which is
# exactly what hides it during a build session.
_sweep_is_dry_run() {
  for argument in "$@"; do
    [ "$argument" = "--dry-run" ] && return 0
  done
  return 1
}
if _sweep_is_dry_run "$@"; then
  echo "[activation] --dry-run: not activating; this run changes nothing"
else
  activate_checkout "$REPO_ROOT" "$0" "$@"
fi

# THE DEAD-MAN SWITCH. `launchd` discards this script's exit code, so the codes documented above
# reach nobody: a pass that stops running, or that fails every morning, is silent. Armed AFTER
# activation, because `activate_checkout` may `exec` and an `exec` does not fire an EXIT handler.
# It reports and never gates -- every failure inside it logs a line and returns 0.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-activation-sweep --finding 2 "$@"

# Load BWS_ACCESS_TOKEN from the Keychain via the approved helper (never a plaintext file). ONE
# identity: the three secrets this sweep's siblings juggle are not needed here, because it reads
# only local git and speaks only to the orchestrator.
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/sds-token.sh"

# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR / CLICOLOR_FORCE
# make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe, which breaks the
# parse below -- a portfolio-wide defect fixed locally in three repos and generalised in none. It
# costs nothing where the behaviour never fires.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

ACTIVATION_SWEEP_TOKEN="$(_bws_value "$OBSERVER_BEARER_UUID")"
export ACTIVATION_SWEEP_TOKEN
if [ -z "${ACTIVATION_SWEEP_TOKEN:-}" ]; then
  echo "FATAL: could not read the OBSERVER bearer from BWS." >&2
  exit 1
fi

ACTIVATION_BIND_TOKEN="$(_bws_value "$SYSTEM_BEARER_UUID")"
export ACTIVATION_BIND_TOKEN
if [ -z "${ACTIVATION_BIND_TOKEN:-}" ]; then
  echo "FATAL: could not read the SYSTEM bearer from BWS." >&2
  exit 1
fi

TARGETS=()
for path in "${CHECKOUTS[@]}"; do
  TARGETS+=(--checkout "$path")
done

BIND_TARGETS=()
for path in "${BINDABLE[@]}"; do
  BIND_TARGETS+=(--checkout "$path")
done

# THE WORST OUTCOME WINS, and an UNRECOGNISED code is the worst of all.
#
# This script used to `exec` a single command, which kept the CLI's own code untouched. Two lanes
# means a fold, and the `for rc in 1 3 2` form these launchers use elsewhere lets any code outside
# {0,1,2,3} -- 127 for a missing binary is the one that actually happens -- fall through to
# `exit 0`, so a scheduled job that never ran would report a clean pass. Ranking instead means an
# unknown code is both preserved and dominant. The order below is the CLI's own: 3 outranks 2
# because an incomplete pass cannot claim it found everything, and a broken tool outranks both.
_rank() {
  case "$1" in
    0) echo 0 ;;
    2) echo 1 ;;
    3) echo 2 ;;
    1) echo 3 ;;
    *) echo 4 ;;
  esac
}

WORST_CODE=0
WORST_RANK=0
_record_outcome() {
  local rank
  rank="$(_rank "$1")"
  if [ "$rank" -gt "$WORST_RANK" ]; then
    WORST_RANK="$rank"
    WORST_CODE="$1"
  fi
}

"$REPO_ROOT/.venv/bin/activation-sweep" sweep "${TARGETS[@]}" "$@"
_record_outcome "$?"

# THE BIND PASS RUNS SECOND, and unlike the work lane's pair the order is a preference rather than
# a requirement: the two read different things and neither changes what the other sees. It is
# second because the sweep is the control over ADR-0031 and should be filed even on a pass where
# the orchestrator is unreachable.
"$REPO_ROOT/.venv/bin/activation-sweep" bind "${BIND_TARGETS[@]}" "$@"
_record_outcome "$?"

exit "$WORST_CODE"
