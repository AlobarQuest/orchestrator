#!/usr/bin/env bash
# One tool-installer pass: is the tool this machine runs the one the fork's branch holds?
#
# WHAT THIS EXISTS BECAUSE OF. `AlobarQuest/rtk` is a fork whose upstream sync runs end to end --
# sync, security review, `cargo audit`, hardening, pull request, the fork's own required check,
# the inert landing lane. Landing does not install. The binary on this machine is whatever was
# last installed by hand, so a merged fix arrives only when somebody remembers. Every other lane
# in this estate ends in an observation; this one ended in an intention.
#
# THE FACTORY CANNOT DO THIS. factory-runner executes on GitHub-hosted runners and they cannot
# touch this machine, so a local scheduled pass was never a choice between designs -- it is the
# only shape that reaches the binary.
#
# EXIT CODES, and they are the whole interface a scheduled run has:
#   0  measured, and either nothing to install or an install succeeded and probed clean. A tool
#      that is behind with nothing permitted to act on it is ALSO 0 -- the ordinary state between
#      a merge and the next window, whose durable home is the `degraded` observation the pass
#      files rather than an exit code.
#   1  the tool itself failed (a missing credential, an unusable URL).
#   2  could not use its inputs: the change window was unreadable, the fork's revision was
#      unreadable, or an install was called for with no toolchain to do it with. Also the code for
#      a pass whose row could not be FILED -- an unfiled pass cannot claim it reported anything.
#   3  something was found: the install failed, or the artifact failed its probe and was rolled
#      back to the previous one.
#
# THE ESTATE'S LAUNCHERS DO NOT SHARE ONE EXIT VOCABULARY, AND 2 AND 3 ARE INVERTED BETWEEN THE
# TWO GROUPS. This one matches the ACTING group -- `run-estate-landing.sh`,
# `run-change-proposer.sh`, `run-work-carrier.sh`, `run-bump-proposer.sh` -- because those are its
# siblings. A reader arriving from `run-landing-ledger.sh`, `run-deploy-watcher.sh` or
# `run-activation-sweep.sh` will otherwise assume the wrong one: there, 2 means something was
# found and 3 means something could not be read.
#
# A BARE PASS TOUCHES NO BINARY. `--install` is what makes a pass act, and it is not enough on its
# own: the operator machine's change window, read from the DEPLOYED policy, must be open too.
# There is deliberately no override -- an out-of-hours install is a person running one command.
#
# THIS PASS CAN PUBLISH A COMMIT, and that is authority this lane did not used to have. The second
# row is the `octo` Claude Code plugin, whose version is pinned in the marketplace manifest of
# `AlobarQuest/devon-plugins` -- so an install that succeeds and proves itself ends by committing
# that one line and pushing it. It happens ONLY with `--install`, ONLY inside the change window,
# ONLY after the install has been verified, and any failure of the publish rolls the whole act
# back locally. See `src/tool_installer/plugin.py` for the reasoning and the merge guard's own
# register for the exemption it required.
#
# Usage:
#   scripts/run-tool-installer.sh [--install] [--dry-run] [--install-root DIR]
# Install as a scheduled job with:
#   scripts/install-tool-installer-launchd.sh
set -uo pipefail

# BWS UUIDs (values fetched at runtime; never stored in this repo). See .bws-secrets.toml.
SYSTEM_BEARER_UUID="221a48d5-3f29-4898-b300-b4820140c880"     # orchestrator-system SYSTEM bearer
OBSERVER_BEARER_UUID="f793576f-e9aa-4f9d-8089-b4a000b9e2d5"   # orchestrator-observer OBSERVER

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ACTIVATION — a merged change is not live on this machine until the code is pulled, because these
# launchers run the working copy. Best-effort by construction: the helper prints one `[activation]`
# line and returns 0 whatever it finds, so this job is never gated on being able to update itself.
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

# THE DEAD-MAN SWITCH. `launchd` discards this script's exit code, so the codes above reach nobody:
# a pass that stops running is silent. Armed AFTER activation, because `activate_checkout` may
# `exec` and an `exec` does not fire an EXIT handler. Note what it does NOT do: a declared finding
# code pings the check SUCCESS, so exit 3 never pages. The check answers "is this lane alive"; the
# finding's durable home is the observation the pass files.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_deadman_arm sds-tool-installer --finding 3 "$@"

# Load BWS_ACCESS_TOKEN from the Keychain via the approved helper (never a plaintext file). Both
# bearers below live in the `SDS Operator` project, which this one narrow identity reads -- so this
# script needs ONE BWS identity for two secrets, and must not set a second. The dead-man switch
# above uses the BROAD account and reads its Keychain item directly into a local of its own, which
# is why sourcing this after it is safe: `sds-token.sh` respects an already-set value, and
# exporting the broad token here would silently become the identity for both fetches below, each
# then failing with a bare `404 Resource not found` naming nothing.
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/sds-token.sh"

# `--color no` AND an environment with the forcing variables removed. FORCE_COLOR / CLICOLOR_FORCE
# make `bws secret get` wrap its JSON in ANSI escapes even when stdout is a pipe, which breaks the
# parse below -- a portfolio-wide defect fixed locally in three repos and generalised in none.
_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE bws secret get "$1" --output json --color no \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"

# TWO ORCHESTRATOR CREDENTIALS, AND THE SPLIT IS FORCED BY THE ENDPOINT RATHER THAN CHOSEN.
# Measured against production 2026-09-06: `GET /api/v1/factory-policy` answers 403 to
# `orchestrator-observer` and 200 to `orchestrator-system`. The two are handed to two clients that
# each reach exactly one route, so the SYSTEM bearer -- which can drive a work unit's lifecycle --
# is held by the half that can only read a policy.
ORCHESTRATOR_POLICY_CREDENTIAL_KEY_ID="orchestrator-system"
ORCHESTRATOR_POLICY_TOKEN="$(_bws_value "$SYSTEM_BEARER_UUID")"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-observer"
ORCHESTRATOR_API_TOKEN="$(_bws_value "$OBSERVER_BEARER_UUID")"
export ORCHESTRATOR_API_URL
export ORCHESTRATOR_POLICY_CREDENTIAL_KEY_ID ORCHESTRATOR_POLICY_TOKEN
export ORCHESTRATOR_API_CREDENTIAL_KEY_ID ORCHESTRATOR_API_TOKEN

# THE GITHUB CREDENTIAL HAS NO BWS RECORD, the same gap the landing ledger and the pin watcher
# carry and for the same reason: this reads public repository metadata and has never needed a
# dedicated identity. A scheduled job resting on an interactive login breaks the moment that login
# is re-issued, and nothing would say so but this script's exit 1.
if [ -z "${TOOL_INSTALLER_GITHUB_TOKEN:-}" ]; then
  TOOL_INSTALLER_GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
  export TOOL_INSTALLER_GITHUB_TOKEN
fi
if [ -z "${TOOL_INSTALLER_GITHUB_TOKEN:-}" ]; then
  echo "FATAL: no GitHub credential. Set TOOL_INSTALLER_GITHUB_TOKEN, or authenticate gh." >&2
  exit 1
fi

"$REPO_ROOT/.venv/bin/tool-installer" "$@"
rc=$?

# A code outside {0,1,2,3} is the program dying in a way it does not describe -- a missing binary
# is 127. Reported as 1 (the tool failed) rather than folded to 0, which is what several sibling
# launchers used to do.
case "$rc" in
  0|1|2|3) exit "$rc" ;;
  *) echo "FATAL: tool-installer exited $rc" >&2; exit 1 ;;
esac
