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

# The repositories the ledger covers. Every repository this estate lands code in, including the
# two with no gate installed -- a repository whose updates never land unattended is exactly the
# one whose backlog of green updates is worth counting.
REPOSITORIES=(
  AlobarQuest/orchestrator
  AlobarQuest/intent-packages
  AlobarQuest/factory-runner
  AlobarQuest/security-standards
  AlobarQuest/infraops-mcp-server
  AlobarQuest/change-manager
  AlobarQuest/brain
  AlobarQuest/project-standards
)

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


# Load BWS_ACCESS_TOKEN from the Keychain via the approved helper (never a plaintext file).
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/sds-token.sh"

# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR / CLICOLOR_FORCE
# make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe, which breaks the
# parse below -- a portfolio-wide defect that has been fixed locally in three repos and generalised
# in none. It costs nothing where the behaviour never fires.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

LANDING_LEDGER_TOKEN="$(_bws_value "$OBSERVER_BEARER_UUID")"
export LANDING_LEDGER_TOKEN

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

TARGETS=()
for repository in "${REPOSITORIES[@]}"; do
  TARGETS+=(--repository "$repository")
done

PASS_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LEDGER="$REPO_ROOT/.venv/bin/landing-ledger"

# SEVEN DAYS, NOT THE THIRTY-DAY DEFAULT, and this is a correctness setting rather than a
# preference. Recording costs about twelve GitHub requests per landing; the 2026-08-08 backfill
# read thirty days across these eight repositories and exhausted the 5000/hour limit in one pass.
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

# The worst answer wins, and "could not measure" is worse than "found something".
for rc in 1 3 2; do
  if [ "$record_rc" -eq "$rc" ] || [ "$audit_rc" -eq "$rc" ]; then
    exit "$rc"
  fi
done
exit 0
