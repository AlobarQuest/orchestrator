#!/usr/bin/env bash
# BOTH of ADR-0030's lanes, in one invocation over the operator machine's working copies.
#
#   sweep  one observation per enrolled working copy: what it will execute at its next start,
#          and whether that is what was merged. One per member working copy, OBSERVER credential.
#   bind   one release artifact binding per completed work unit whose landing this machine has
#          actually pulled, AND the activation check that follows it: whether the artifact just
#          bound is what the next start will execute. Only the machine-local members granted rollouts
#          (`sds_scope bindable`), SYSTEM credential.
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
#   3  some checkout could not be measured, the member record could not be read, or a row could
#      not be filed, so the answer is missing rather than clean. 3 outranks 2.
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

# THE ENROLLED WORKING COPIES come from the SDS member record (`sds_scope checkouts` and `bindable`),
# not a list here; what each population means is in docs/operations/membership.md.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ACTIVATION — this job pulls itself before it runs, exactly as its siblings do (ADR-0031), and
# the obvious objection deserves an answer: does the control not then heal the very repository it
# measures? It does, for `orchestrator` alone, and that is the correct behaviour rather than a
# blind spot. Activation is best-effort and its failures are SILENT BY DESIGN -- the helper prints
# one line and returns 0 whatever it finds -- so if it cannot pull, nothing is healed and the
# sweep reports this checkout behind, which is the ADR-0031 control working. If it can pull, the
# machine genuinely is current and saying so is true. The population comes from the member record;
# no working copy it does not name is ever touched.
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
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

ACTIVATION_SWEEP_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID")"
export ACTIVATION_SWEEP_TOKEN
if [ -z "${ACTIVATION_SWEEP_TOKEN:-}" ]; then
  echo "FATAL: could not read the OBSERVER bearer from BWS." >&2
  exit 1
fi

ACTIVATION_BIND_TOKEN="$(sds_bws_value "$SYSTEM_BEARER_UUID")"
export ACTIVATION_BIND_TOKEN
if [ -z "${ACTIVATION_BIND_TOKEN:-}" ]; then
  echo "FATAL: could not read the SYSTEM bearer from BWS." >&2
  exit 1
fi

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

# THE SCOPE COMES FROM THE MEMBER RECORD, one read per pass. `sds_scope` prints a population one
# directory name per line and exits 0, or exits 3 printing nothing when the record could not be
# read. A pass whose scope is unknown does not run -- it cannot claim a clean machine over a list it
# does not have -- and counts as incomplete (3); the other pass still runs. Any other code is the
# tool failing and is recorded verbatim, which `_rank` makes dominant.
_scope_targets() {
  local names rc
  names="$(cd "$REPO_ROOT" && SDS_SCOPE_ORCHESTRATOR_TOKEN="$ACTIVATION_SWEEP_TOKEN" \
    "$REPO_ROOT/.venv/bin/python" -m scripts.sds_scope "$1")"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    return "$rc"
  fi
  while IFS= read -r name; do
    printf '%s\n' "--checkout" "$HOME/Projects/$name"
  done <<< "$names"
}

TARGETS=()
BIND_TARGETS=()

if scope="$(_scope_targets checkouts)"; then
  while IFS= read -r argument; do TARGETS+=("$argument"); done <<< "$scope"
  "$REPO_ROOT/.venv/bin/activation-sweep" sweep "${TARGETS[@]}" "$@"
  _record_outcome "$?"
else
  _record_outcome "$?"
fi

# THE BIND PASS RUNS SECOND, and unlike the work lane's pair the order is a preference rather than
# a requirement: the two read different things and neither changes what the other sees. It is
# second because the sweep is the control over ADR-0031 and should be filed even on a pass where
# the orchestrator is unreachable.
if scope="$(_scope_targets bindable)"; then
  while IFS= read -r argument; do BIND_TARGETS+=("$argument"); done <<< "$scope"
  "$REPO_ROOT/.venv/bin/activation-sweep" bind "${BIND_TARGETS[@]}" "$@"
  _record_outcome "$?"
else
  _record_outcome "$?"
fi

exit "$WORST_CODE"
