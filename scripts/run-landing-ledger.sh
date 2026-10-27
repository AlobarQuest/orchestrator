#!/usr/bin/env bash
# One landing-ledger pass, then one audit pass over what it recorded (WS-P3.6 Increment 3).
#
# RECORD FIRST, AUDIT SECOND, ALWAYS. Detector A re-evaluates what the ledger holds, so an audit
# over a stale ledger reports on a window that has already moved. Detector B reads GitHub directly
# and is unaffected -- which is why the audit still runs when recording came back incomplete.
#
# EXIT CODES, and they are the whole interface a scheduled run has:
#   0  everything was measured and nothing was found.
#   1  the tool itself failed (a missing credential, an unhandled error).
#   2  something was found. The pass worked; reality did not.
#   3  some part of reality could not be read, so the answer is missing rather than clean.
# 3 outranks 2: an incomplete pass cannot claim it found everything there was to find. A broken
# tool and an honest finding sharing one code is a collision this estate has already paid for.
#
# NOTHING HERE ACTS. Both passes read and file observations; neither changes a pull request, a
# repository setting, or any canonical state.
#
# Usage:
#   scripts/run-landing-ledger.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-landing-ledger-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER bearer

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ACTIVATION — a merged change is not live on this machine until the code is pulled. The estate's
# Dependabot cascade stops at the merge, which is complete for a repository whose landing redeploys
# a hosted application and incomplete for this one, whose code runs from a working copy here
# (orchestrator ADR-0031). Best-effort by construction: the helper prints one `[activation]` line
# and returns 0 whatever it finds, so this job is never gated on being able to update itself. It
# re-execs this script when HEAD moves, because bash reads a script incrementally by byte offset
# and the file it just rewrote is this one.
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

# THE DEAD-MAN SWITCH. `launchd` discards this script's exit code, so the codes documented above
# reach nobody: a pass that stops running, or that fails every morning, is silent. Armed AFTER
# activation, because `activate_checkout` may `exec` and an `exec` does not fire an EXIT handler.
# It reports and never gates -- every failure inside it logs a line and returns 0.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-landing-ledger --finding 2 "$@"


# ONE BWS IDENTITY: the observer bearer is in a project only the narrow `sds-operator` account can
# read, fetched from its own Keychain item and never from an ambient `BWS_ACCESS_TOKEN`. The
# change-manager READ bearer this launcher used to fetch with a second, broad identity fed only the
# scope derivation, which now asks the orchestrator for the member record.
NARROW_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
if [ -z "$NARROW_IDENTITY" ]; then
  echo "FATAL: no BWS identity for the orchestrator observer credential (Keychain service Claude," \
       "account BWS_ACCESS_TOKEN_SDS)" >&2
  exit 1
fi

# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR / CLICOLOR_FORCE
# make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe, which breaks the
# parse below -- a portfolio-wide defect that has been fixed locally in three repos and generalised
# in none. It costs nothing where the behaviour never fires.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

LANDING_LEDGER_TOKEN="$(sds_bws_value "$OBSERVER_BEARER_UUID" "$NARROW_IDENTITY")"
export LANDING_LEDGER_TOKEN
if [ -z "$LANDING_LEDGER_TOKEN" ]; then
  echo "FATAL: could not read the orchestrator observer credential from BWS" >&2
  exit 1
fi

# THE GITHUB CREDENTIAL HAS NO BWS RECORD. The backfill was run by hand with an ad-hoc token, so
# there is nothing to fetch by UUID and this falls back to the operator's own `gh` credential.
# That is a real gap, not a design: a scheduled job resting on an interactive login breaks the
# moment the login is re-issued, and nothing would say so but this script's exit 1.
if [ -z "${LANDING_LEDGER_GITHUB_TOKEN:-}" ]; then
  LANDING_LEDGER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
  export LANDING_LEDGER_GITHUB_TOKEN
fi
if [ -z "${LANDING_LEDGER_GITHUB_TOKEN:-}" ]; then
  echo "FATAL: no GitHub credential. Set LANDING_LEDGER_GITHUB_TOKEN, or authenticate gh." >&2
  exit 1
fi

# THE REPOSITORIES ARE READ FROM THE MEMBER RECORD, NOT LISTED: the ledger covers the record's
# `observed` population (docs/operations/membership.md). A hand list here drifted from the
# declarations it was meant to follow.
#
# AN INCOMPLETE DERIVATION STILL RUNS, AND STILL SAYS SO. `sds_scope` exits 3 when part of the
# answer couldn't be read, after printing every name it could determine. The passes below run on
# those names, and the 3 is folded into this pass's answer, so the day is recorded where it can be
# and the pass still reads as incomplete rather than clean. Any other non-zero code is the tool
# failing (1) or not running at all (126, 127, 137), and is passed through verbatim.
REPOSITORIES="$(cd "$REPO_ROOT" && SDS_SCOPE_ORCHESTRATOR_TOKEN="$LANDING_LEDGER_TOKEN" \
  "$REPO_ROOT/.venv/bin/python" -m scripts.sds_scope ledger)"
scope_rc=$?
if [ "$scope_rc" -ne 0 ] && [ "$scope_rc" -ne 3 ]; then
  echo "the ledger's repositories could not be derived: sds_scope exited $scope_rc" >&2
  exit "$scope_rc"
fi
if [ -z "$REPOSITORIES" ]; then
  echo "the ledger's repositories could not be derived; nothing was recorded" >&2
  exit 3
fi
TARGETS=()
while IFS= read -r repository; do
  TARGETS+=(--repository "$repository")
done <<< "$REPOSITORIES"

PASS_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LEDGER="$REPO_ROOT/.venv/bin/landing-ledger"

# SEVEN DAYS, NOT THE THIRTY-DAY DEFAULT, and this is a correctness setting rather than a
# preference. Recording costs about twelve GitHub requests per landing; the 2026-08-08 backfill
# read thirty days across the eight repositories it covered then and exhausted the 5000/hour limit in one pass.
# A daily job at that window would therefore run out partway through every morning, mark the later
# repositories unavailable, and exit 3 forever -- a permanently red signal, which is a signal
# nobody reads. Seven days is roughly ninety landings, survives a week of the machine being
# closed (re-recording an unchanged landing replays rather than conflicting), and leaves headroom
# for the audit's own reads. A LONGER gap than that needs one manual `record --days N`.
"$LEDGER" record "${TARGETS[@]}" --days 7 "$@"
record_rc=$?

"$LEDGER" audit "${TARGETS[@]}" --pass-id "$PASS_ID" "$@"
audit_rc=$?

# AN OUT-OF-VOCABULARY CODE IS REPORTED VERBATIM, and this branch must come FIRST. The fold below
# ranks the four codes this program can return; anything else -- 126 for a program that is not
# executable, 127 for one that is missing, 137 for one that was killed -- matches none of them and
# used to fall through to `exit 0`, turning the loudest possible failure into the quietest possible
# answer. That was survivable while nothing subscribed to the exit code. It is not survivable now
# that the dead-man switch reports on it: a missing binary would ping success.
for rc in "$record_rc" "$audit_rc"; do
  case "$rc" in
    0 | 1 | 2 | 3) ;;
    *) exit "$rc" ;;
  esac
done

# The worst answer wins, and "could not measure" is worse than "found something". An incomplete
# derivation (`scope_rc` 3) counts as not measuring the repositories it couldn't name.
for rc in 1 3 2; do
  if [ "$record_rc" -eq "$rc" ] || [ "$audit_rc" -eq "$rc" ] || [ "$scope_rc" -eq "$rc" ]; then
    exit "$rc"
  fi
done
exit 0
