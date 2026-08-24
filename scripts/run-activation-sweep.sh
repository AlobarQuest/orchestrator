#!/usr/bin/env bash
# One machine-activation sweep over the nine enrolled working copies (ADR-0030).
#
# It reads local git and files one observation per working copy. It FETCHES -- without that,
# `behind` is measured against stale remote-tracking refs and is always zero, so the control
# would report the machine current because it never looked. It NEVER PULLS: ADR-0030 stops at
# recording, and making the machine self-update is a separate decision with its own authority
# argument. The subcommand allowlist in `activation_sweep/checkout.py` makes that structural
# rather than a matter of what this script happens to pass.
#
# EXIT CODES, and they are the whole interface a scheduled run has:
#   0  every enrolled checkout was measured, filed, and is current and clean.
#   1  the tool itself failed (a missing credential, a missing binary, an unusable URL).
#   2  something was found: a checkout is behind its upstream, or carries modified tracked files.
#   3  some checkout could not be measured, or its row could not be filed, so the answer is
#      missing rather than clean. 3 outranks 2.
# The sweep is `exec`ed rather than run and folded. A fold over several exit codes is where the
# sibling launchers lose 127 -- a missing binary reported as success -- and there is only one
# command here, so the code that reaches launchd is the CLI's own, untouched.
#
# Usage:
#   scripts/run-activation-sweep.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-activation-sweep-launchd.sh
set -uo pipefail

# BWS UUID (the value is fetched at runtime; never stored in this repo). See .bws-secrets.toml.
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER

# THE ENROLLED WORKING COPIES, and the list is a parameter of ADR-0030's RULE rather than the
# rule: a repository is enrolled when its consumers begin a fresh process in the ordinary course
# of operation, without a human deciding to restart them. Adding such a consumer enrols its
# repository, and the KeepAlive daemons are deliberately absent -- a sweep attests the disk, and
# for a process serving continuously between starts the gap between the disk and the loaded code
# is exactly what goes unobserved.
#
# The paths are enumerated here rather than globbed, because the consumers are enumerated from
# the LaunchAgent plists and `~/.claude.json` and not from a path convention: `project-standards`
# is driven by `integrations/portfolio-scan.sh`, `FacelessTT` by `tools/tiktok_scraper_cron.sh`,
# and a sweep of `Projects/*/scripts/*.sh` misses both.
CHECKOUTS=(
  "$HOME/Projects/orchestrator"
  "$HOME/Projects/vps-backup"
  "$HOME/Projects/infraops-mcp-server"
  "$HOME/Projects/project-standards"
  "$HOME/Projects/intent-packages"
  "$HOME/Projects/security-standards"
  "$HOME/Projects/email-capture"
  "$HOME/Projects/FacelessTT"
  "$HOME/.claude"
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
activate_checkout "$REPO_ROOT" "$0" "$@"

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

TARGETS=()
for path in "${CHECKOUTS[@]}"; do
  TARGETS+=(--checkout "$path")
done

# `exec`, so the CLI's exit code IS this script's, and a missing binary is bash's own 127 rather
# than a status a fold rounded to zero.
exec "$REPO_ROOT/.venv/bin/activation-sweep" sweep "${TARGETS[@]}" "$@"
