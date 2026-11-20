#!/usr/bin/env bash
# One bump-driver pass: decompose registered dependency-update intakes, dispatch ready bump units
# and land completed ones, as SYSTEM, with no person acting (Landing Part 4).
#
# It runs no repository code. Its mirrors run only git, and it builds a decomposition with
# `factory decompose --no-execute` (`src/bump_driver/`, `docs/operations/landing-lanes.md`).
#
# THIS CHECK IS AN OUTCOME ALARM. A finding is something a person decides, so every non-zero exit
# pings `/fail` with the report as the body, and a pass that stops pinging goes down after the
# grace (ADR-0021 amendment 2).
#
# EXIT CODES (`bump_driver/cli.py`):
#   0  every bump moved, or waits on something that clears by itself.
#   1  the tool itself failed (a missing credential, an unusable URL).
#   2  a person decides something: a refused decomposition, dispatch or merge, a unit in
#      revision_required, a run nobody claimed, or a bump held for the Mac's conformance facts.
#      Every such finding is delivered as a keyed alert before the exit, so the check pings
#      success with the report (`--told 2`). A proposal a person declined isn't a finding.
#   3  a record, a unit or a pull request could not be read, or alert-hub could not be told: a
#      failed delivery is 3, never 2, so it still pings /fail.
#
# CREDENTIALS, each read straight into its own variable, never an ambient `BWS_ACCESS_TOKEN` (the
# two-identity rule, docs/operations/credentials.md):
#   - the orchestrator SYSTEM bearer (BWS `221a48d5-…`) and the `sds-bridge` alert-hub sender token
#     (BWS `b415d03b-…`), through the narrow `sds-operator` identity; SYSTEM also reads the member
#     record;
#   - the change-manager READ bearer (BWS `314f276d-…`) and the two Brain contributor keys (BWS
#     `750f737f-…`, `da8134b0-…`, project `Brains - Contributor`), through the broad identity;
#   - GitHub: the operator's own `gh` login, which has no BWS record. It reaches the lane's own
#     git commands through their environment, never an argument, `.git/config` or `factory`.
#
# WHAT `factory` NEEDS. It runs from intent-packages' own venv, so that goes on PATH, with this
# repository's venv for the `orchestrator conformance-claim` it shells out to. The claim reads
# project-standards' compliance matrix, so `FACTORY_PYTHONPATH` names that, and the lane hands it to
# `factory` alone as PYTHONPATH. A claim that can't be produced refuses the decomposition. `factory`
# runs on an allowlist of variables holding only the Brain keys as secrets (`cli.py`).
#
# TWO HOSTS, ONE LAUNCHER (ADR-0059 increment 5, step 3g-3). `sds_in_lane_container` means the
# lane's container: `lane-run` names the secrets broker's mounted socket in `SDS_SECRETS_SOCKET`
# (ADR-0055 amendment 30), or passes a lane's own token as its one secret, and the Mac's launchd
# job sets neither. There the launcher skips activation (the container runs the image's own code),
# reads no Keychain item and calls no `gh`, and reads the same five secrets through the socket, or
# with that one token in the environment, never as a process argument, each fatal as on the Mac,
# on every pass, plus the read-only App's key (`314a966c-…`), piped straight into the mint. The
# broker serves this lane only the UUIDs its allowlist lists
# (`lanes/secrets-broker/allowlist.toml`), exactly the seven this launcher reads. It mints
# `BUMP_DRIVER_GITHUB_TOKEN` from the read-only App, in place of `gh auth token`, for exactly the
# repositories `bump-driver repositories` prints (the three clones and each `dependency_updates`
# grant), with `metadata`, `pull_requests` and `contents`, read: a granted pull request's state
# (`GitHub.pull_state`), and the shallow clones of the mirrors and of decision 1. It clones
# intent-packages, project-standards and security-standards, shallow at `main`, through
# `scripts/sds-git.sh` into `$HOME/Projects/`, and code-standards into `$HOME/Developer/`, the paths
# the Mac reads, under the new `HOME` that `lane-run` gives the pass and removes after it.
# `factory` and `code-standards` are shims running the image's interpreter on their clones' `src`,
# as the bump proposer runs intent-packages' lifecycle; no venv is built and nothing is fetched
# beyond the clones.
#
# THE MAC'S CONFORMANCE FACTS (ADR-0059 Amendment 13, D-62 C). The claim's other two Mac-only
# inputs, the infra-drift report and the LaunchAgent and hook wiring, come from the newest
# `mac_conformance.reported` event in the factory-events chain. Every container pass walks the
# chain with `orchestrator.factory_chain_read`, under the read role, from the image's pinned
# security-standards, as the work carrier does: the chain's three settings come from the compose
# (a missing one fails the pass), and the read password (BWS `2f708d08-…`) is read like the other
# secrets and passed to that walk alone, as an assignment prefix, never exported, never to
# `factory`, git, the mint or the driver. The walk's verdict, its summary and its newest event (or the reason
# there is none) go to a file this pass owns, which the driver reads as `--conformance-facts`. The
# driver judges it, holds a bump whose member reads it when it is unusable, and otherwise writes
# the facts under `HOME` where project-standards' matrix reads them (`bump_driver/conformance.py`).
# A walk that didn't verify is a hold with a keyed alert in the driver, not a failed pass: the
# dispatches and merges of bumps already decomposed don't wait on the chain. It unsets
# `BWS_ACCESS_TOKEN` and `SDS_SECRETS_SOCKET` before the clones and the driver run, and git's
# credential before the driver runs. The socket itself stays reachable, as the design accepts: the
# driver already runs as the same user. A missing `BUMP_DRIVER_STATE_DIR` fails the pass: the
# lock, the alerts and the mirrors must be on the lane's volume. The container block runs the
# whole pass and exits, so everything after it is the Mac's launchd job, unchanged until
# increment 6. Exit 1 there also covers a failed mint
# or clone; a member record the repository list couldn't be read from exits 3.
#
# Usage:
#   scripts/run-bump-driver.sh [--dry-run]
# Install as a scheduled job with:
#   scripts/install-bump-driver-launchd.sh
set -uo pipefail

CHECK_NAME="sds-bump-driver"
ORCHESTRATOR_SYSTEM_UUID="221a48d5-3f29-4898-b300-b4820140c880"   # orchestrator-system SYSTEM bearer
CHANGE_MANAGER_READ_UUID="314f276d-55ca-4ddc-a24d-b4a3013508cd"   # change-manager READ bearer
SDS_BRIDGE_ALERT_UUID="b415d03b-f92d-46b1-8a9d-b4f10103df49"      # alert-hub sds-bridge sender
CODE_BRAIN_KEY_UUID="750f737f-4cb6-4876-9a98-b48200ea1c0b"        # CODE_BRAIN_CONTRIBUTOR_KEY
INFRA_BRAIN_KEY_UUID="da8134b0-565f-45c8-8965-b48200ea1c40"       # INFRA_BRAIN_CONTRIBUTOR_KEY
# The read-only lane App's private key and id (ADR-0059 increment 3, Q9), `alobar-sds-lanes-read`,
# in `SDS / Lane Apps`. Read in the container only, piped straight into `sds-github-mint`.
READ_APP_KEY_UUID="314a966c-5654-4e3c-8358-b4fd01268775"
READ_APP_ID="5684758"
# The factory-events chain's read-role password (ADR-0055 amendment 26), in `Factory Events`. Read
# in the container only, for the chain walk alone (ADR-0059 Amendment 13 decision items 4 and 7).
FACTORY_EVENTS_READ_UUID="2f708d08-ea5d-4d11-bde0-b50200c9a60b"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Sourced before activation, whose host test is its `sds_in_lane_container`.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"
# ACTIVATION: these launchers run the working copy, so a merged change is live here only once it
# is pulled. Best-effort: the helper prints one `[activation]` line and returns 0 whatever it finds.
# Skipped in the container, which runs the image's own code and has no `activate-checkout.sh`.
if ! sds_in_lane_container; then
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

_acting=1
for _arg in "$@"; do
  if [ "$_arg" = "--dry-run" ]; then _acting=0; fi
done
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-deadman.sh"
sds_outcome_begin "$CHECK_NAME" bump-driver "$_acting"

# ---- THE LANE'S CONTAINER (ADR-0059 increment 5, step 3g-3) -----------------------------------
# See "TWO HOSTS, ONE LAUNCHER" in the header. Every read is `sds_bws_value` with one argument:
# through the broker's socket, or with the lane's one token from the environment, so no token
# appears in a process argument, and each secret is always read, never taken from the environment.
# Every failure is `sds_outcome_fail`, which names it and pings `/fail` with it as the body.
if sds_in_lane_container; then
  FROM=" (the lane's socket or token)"

  # The compose sets it to the lane's volume (decision 1, "Lane state"). Without it the pass lock,
  # the alerts and the mirrors would go to this run's `HOME`, which `lane-run` discards, and two
  # passes would no longer exclude each other.
  [ -n "${BUMP_DRIVER_STATE_DIR:-}" ] || sds_outcome_fail \
    "FATAL: BUMP_DRIVER_STATE_DIR is not set in the lane's container"
  export BUMP_DRIVER_STATE_DIR
  # The clones, the shims and the Mac's conformance facts go under `HOME`, so it must be the new,
  # empty directory `lane-run` gives each pass. A run under a real `HOME` (a hand run with a BWS
  # token exported or a socket named) refuses here, before it reads a secret or writes over a
  # checkout, a real `factory`, an infra-drift report, a LaunchAgent or a Claude settings file.
  for _written in Projects Developer .local infra-drift Library .claude; do
    if [ -e "$HOME/$_written" ]; then
      _why="it holds $_written, which the pass would write over"
      sds_outcome_fail "FATAL: HOME ($HOME) is not a new directory: $_why"
    fi
  done

  # THE CHAIN'S SETTINGS COME FROM THE COMPOSE, and a missing one fails the pass before anything is
  # read, as in the work carrier's container. Kept in this script's own variables and passed to the
  # chain walk alone; an ambient chain password is never used.
  for _setting in FACTORY_EVENTS_DB_HOST FACTORY_EVENTS_DB_PORT FACTORY_EVENTS_DB_NAME; do
    [ -n "${!_setting:-}" ] || sds_outcome_fail \
      "FATAL: $_setting is not set in the lane's container: the factory-events chain can't be read"
  done
  CHAIN_DB_HOST="$FACTORY_EVENTS_DB_HOST"
  CHAIN_DB_PORT="$FACTORY_EVENTS_DB_PORT"
  CHAIN_DB_NAME="$FACTORY_EVENTS_DB_NAME"
  unset FACTORY_EVENTS_APPEND_PASSWORD FACTORY_EVENTS_READ_PASSWORD \
    FACTORY_EVENTS_DB_HOST FACTORY_EVENTS_DB_PORT FACTORY_EVENTS_DB_NAME

  # The five the Mac reads, in its order, each fatal as there, on every pass.
  ORCHESTRATOR_API_TOKEN="$(sds_bws_value "$ORCHESTRATOR_SYSTEM_UUID")"
  [ -n "$ORCHESTRATOR_API_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the orchestrator system credential from BWS$FROM"
  SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID")"
  [ -n "$SDS_BRIDGE_ALERT_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the alert-hub sender credential from BWS$FROM"
  CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_READ_UUID")"
  [ -n "$CHANGE_MANAGER_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the change-manager READ credential from BWS$FROM"
  CODE_BRAIN_KEY="$(sds_bws_value "$CODE_BRAIN_KEY_UUID")"
  [ -n "$CODE_BRAIN_KEY" ] || sds_outcome_fail \
    "FATAL: could not read the Code Brain key from BWS$FROM"
  INFRA_BRAIN_KEY="$(sds_bws_value "$INFRA_BRAIN_KEY_UUID")"
  [ -n "$INFRA_BRAIN_KEY" ] || sds_outcome_fail \
    "FATAL: could not read the Infra Brain key from BWS$FROM"
  ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-system"
  export ORCHESTRATOR_API_TOKEN ORCHESTRATOR_API_CREDENTIAL_KEY_ID SDS_BRIDGE_ALERT_TOKEN \
    CHANGE_MANAGER_TOKEN CODE_BRAIN_KEY INFRA_BRAIN_KEY
  # The chain's read password, fatal like the five. It stays in this unexported variable until the
  # chain walk, which alone is given it.
  CHAIN_PASSWORD="$(sds_bws_value "$FACTORY_EVENTS_READ_UUID")"
  [ -n "$CHAIN_PASSWORD" ] || sds_outcome_fail \
    "FATAL: could not read the factory-events read password from BWS$FROM"

  # THE REPOSITORIES ARE THE PROGRAM'S, NOT LISTED HERE: `bump-driver repositories` prints the
  # four clones and each `dependency_updates` grant, read from the member record with the SYSTEM
  # bearer (ADR-0059 decision 2: the mint names only the lane's repositories). AN UNDERIVABLE LIST
  # MINTS NOTHING: the subcommand exits 3 for an unreadable member record, as the pass would, and
  # its code is the pass's; its reason, its last stderr line, is in the failure's message.
  _DERIVE_ERR="$SDS_OUTCOME_REPORT.derive"
  REPOSITORIES="$(env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET \
    "$REPO_ROOT/.venv/bin/bump-driver" repositories 2> "$_DERIVE_ERR")"
  derive_rc=$?
  _derive_reason="$(tail -n 1 "$_DERIVE_ERR")"
  rm -f -- "$_DERIVE_ERR"
  if [ "$derive_rc" -ne 0 ]; then
    sds_outcome_fail \
      "the bump driver's repositories could not be derived: bump-driver repositories exited $derive_rc (${_derive_reason:-no reason given}); nothing was minted or asked" \
      "$derive_rc"
  fi
  TARGETS=()
  while IFS= read -r repository; do
    [ -n "$repository" ] && TARGETS+=(--repository "$repository")
  done <<< "$REPOSITORIES"
  [ "${#TARGETS[@]}" -gt 0 ] || sds_outcome_fail \
    "FATAL: bump-driver repositories printed no repository; nothing was minted or asked"

  # THE READ TOKEN IS MINTED EACH PASS, dry or acting. The App's key goes from the BWS read
  # straight to the mint's stdin in its stored form, base64 of the PEM; it is never in a variable,
  # an argument or a file, and the mint runs without the lane's token or the socket's marker. Its
  # permissions are what the pass's GitHub reads use: `metadata`, which every installation token
  # carries; `pull_requests`, a granted pull request's state (`bump_driver/client.py`,
  # `GitHub.pull_state`); and `contents`, the mirrors' clones and fetches (`bump_driver/mirror.py`)
  # and decision 1's four clones. On the four cloned repositories the pass reads only `contents`,
  # by the clone: no pull request read names one, since `GitHub.pull_state` asks only of a granted
  # member's bump unit. The driver writes nothing to GitHub: it dispatches and lands through the
  # orchestrator as SYSTEM.
  BUMP_DRIVER_GITHUB_TOKEN="$(sds_bws_value "$READ_APP_KEY_UUID" \
    | env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET "$REPO_ROOT/.venv/bin/sds-github-mint" \
        --app-id "$READ_APP_ID" "${TARGETS[@]}" \
        --permission metadata=read --permission pull_requests=read --permission contents=read)"
  mint_rc=$?
  if [ "$mint_rc" -ne 0 ] || [ -z "$BUMP_DRIVER_GITHUB_TOKEN" ]; then
    sds_outcome_fail "FATAL: could not mint the bump driver's GitHub read token from the read-only App (the key's BWS read or sds-github-mint exited $mint_rc)"
  fi
  export BUMP_DRIVER_GITHUB_TOKEN

  # EVERY BWS READ IS DONE, so neither the lane's token nor the socket's marker reaches git or any
  # program.
  unset BWS_ACCESS_TOKEN SDS_SECRETS_SOCKET

  # DECISION 1'S CLONES, shallow, at `main`, with the read token through git's environment, into
  # `$HOME/Projects/` and `$HOME/Developer/`: the Mac's paths, so `FACTORY_PYTHONPATH` is the Mac's
  # string and project-standards' checkers find security-standards and code-standards where they
  # look for them (Amendment 13 decision item 1 for code-standards). `HOME` is the new directory
  # `lane-run` gives this pass and removes after it, so the pass discards the clones. git's own
  # message is kept for the report; it never holds the header (no trace is on).
  # shellcheck disable=SC1091
  source "$REPO_ROOT/scripts/sds-git.sh"
  sds_git_credential BUMP_DRIVER_GITHUB_TOKEN || sds_outcome_fail \
    "FATAL: could not give git the bump driver's GitHub read credential"
  _CLONES="$HOME/Projects"
  _CODE_STANDARDS="$HOME/Developer/code-standards"
  mkdir -p "$_CLONES" "$HOME/Developer" || sds_outcome_fail \
    "FATAL: could not create the clone directories $_CLONES and $HOME/Developer"
  for _clone in intent-packages project-standards security-standards code-standards; do
    _dest="$_CLONES/$_clone"
    [ "$_clone" = code-standards ] && _dest="$_CODE_STANDARDS"
    _clone_out="$(git clone --quiet --depth 1 --single-branch --branch main \
      "https://github.com/AlobarQuest/$_clone.git" "$_dest" 2>&1)" || sds_outcome_fail \
      "FATAL: could not clone AlobarQuest/$_clone into $_dest: $_clone_out"
  done
  # The driver's own git commands build their environment from the token (`Mirrors.environment`),
  # and `factory`'s is an allowlist, so git's credential goes now, as the Mac's pass has none.
  unset GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM GIT_TERMINAL_PROMPT GIT_CONFIG_COUNT \
    GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0

  # `factory`, as a shim: the image's interpreter on the intent-packages clone's `src`, as the bump
  # proposer runs intent-packages' lifecycle (`run-bump-proposer.sh`). The image's venv carries
  # intent-packages' runtime dependencies (httpx, PyYAML) and the `orchestrator` the conformance
  # claim shells out to. No venv is built, so nothing is fetched beyond the clones. The shim prepends
  # the clone to the `PYTHONPATH` the driver gives `factory`, which is `FACTORY_PYTHONPATH`.
  _SHIMS="$HOME/.local/bin"
  mkdir -p "$_SHIMS" || sds_outcome_fail "FATAL: could not create the shim directory $_SHIMS"
  cat > "$_SHIMS/factory" <<SHIM || sds_outcome_fail "FATAL: could not write the factory shim at $_SHIMS/factory"
#!/bin/sh
PYTHONPATH='$_CLONES/intent-packages/src'\${PYTHONPATH:+:\$PYTHONPATH} exec '$REPO_ROOT/.venv/bin/python' -P -m intent_packages.factory_cli "\$@"
SHIM
  chmod 0755 "$_SHIMS/factory" || sds_outcome_fail \
    "FATAL: could not make the factory shim at $_SHIMS/factory executable"
  # `code-standards`, as a shim at the path the matrix tries first (`checkers._code_cmd`: the
  # clone's `.venv/bin/code-standards`, else `uv run`, which would reach PyPI): the image's
  # interpreter, which carries code-standards' one dependency, `tomlkit` (this repository's
  # `pyproject.toml`), on the clone's `src`. The matrix runs it from the member's mirror, so `-P`
  # keeps that directory off `sys.path`.
  _CODE_SHIM="$_CODE_STANDARDS/.venv/bin/code-standards"
  mkdir -p "$(dirname "$_CODE_SHIM")" || sds_outcome_fail \
    "FATAL: could not create the code-standards shim's directory"
  cat > "$_CODE_SHIM" <<SHIM || sds_outcome_fail "FATAL: could not write the code-standards shim at $_CODE_SHIM"
#!/bin/sh
PYTHONPATH='$_CODE_STANDARDS/src'\${PYTHONPATH:+:\$PYTHONPATH} exec '$REPO_ROOT/.venv/bin/python' -P -m code_standards.cli "\$@"
SHIM
  chmod 0755 "$_CODE_SHIM" || sds_outcome_fail \
    "FATAL: could not make the code-standards shim at $_CODE_SHIM executable"

  PATH="$_SHIMS:$REPO_ROOT/.venv/bin:$PATH"
  FACTORY_PYTHONPATH="$HOME/Projects/project-standards/src:$HOME/Projects/security-standards/src"
  export PATH FACTORY_PYTHONPATH

  # THE CHAIN WALK, EVERY CONTAINER PASS, dry or acting (Amendment 13 decision item 4). The image's
  # pinned security-standards (`$REPO_ROOT/security-standards`, which the work carrier's walk uses
  # too), never the clone at `main`, so both readers verify with one walker and one schema. The
  # password reaches this one child, by assignment prefix. Its stderr is discarded deliberately,
  # as the work carrier discards it: a driver's text can repeat connection details (ADR-0055
  # amendment 24 item 13), and nothing is lost, because anything but a verified record is written
  # as an unverified walk, which the driver holds and alerts on. The record is the last line; only
  # its summary fields reach the report.
  # Under the pass's `HOME`, so `lane-run` discards it with the clones on every exit path.
  _FACTS_FILE="$(mktemp "$HOME/conformance-facts.XXXXXX")" \
    || sds_outcome_fail "FATAL: could not create the pass's conformance facts file"
  _walk_out="$(FACTORY_EVENTS_READ_PASSWORD="$CHAIN_PASSWORD" \
    FACTORY_EVENTS_DB_HOST="$CHAIN_DB_HOST" \
    FACTORY_EVENTS_DB_PORT="$CHAIN_DB_PORT" \
    FACTORY_EVENTS_DB_NAME="$CHAIN_DB_NAME" \
    PYTHONPATH="$REPO_ROOT/security-standards/src" \
    "$REPO_ROOT/.venv/bin/python" -m orchestrator.factory_chain_read 2>/dev/null)"
  _walk_rc=$?
  unset CHAIN_PASSWORD
  _walk_summary="$(printf '%s\n' "$_walk_out" | tail -n 1 | "$REPO_ROOT/.venv/bin/python" -c '
import json, sys
try:
    record = json.loads(sys.stdin.read())
except ValueError:
    record = None
if not isinstance(record, dict):
    record = {"outcome": "unreadable"}
fields = ("outcome", "seq", "head", "events", "error", "error_type")
summary = " ".join(f"{name}={record[name]}" for name in fields if name in record)
verified = record.get("outcome") == "verified" and sys.argv[2] == "0"
facts = {"verified": verified, "summary": f"{summary} exit={sys.argv[2]}"}
if verified:
    facts["mac_conformance"] = record.get("mac_conformance")
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(facts, handle)
print(summary)
' "$_FACTS_FILE" "$_walk_rc")" || sds_outcome_fail \
    "FATAL: could not write the pass's conformance facts file"
  echo "[chain] ${_walk_summary} (exit $_walk_rc)" > "$SDS_OUTCOME_REPORT"

  "$REPO_ROOT/.venv/bin/bump-driver" "$@" --conformance-facts "$_FACTS_FILE" \
    >> "$SDS_OUTCOME_REPORT" 2>&1
  rc=$?
  case "$rc" in
    0|1|2|3) ;;
    *) echo "FATAL: bump-driver exited $rc" >> "$SDS_OUTCOME_REPORT"; rc=1 ;;
  esac
  sds_outcome_end "$rc" --told 2
fi

# ---- THE MAC, unchanged until increment 6 ------------------------------------------------------
NARROW_IDENTITY="${BWS_ACCESS_TOKEN_SDS:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_SDS' -w 2>/dev/null || true)}"
[ -n "$NARROW_IDENTITY" ] || sds_outcome_fail \
    "FATAL: no BWS identity for the orchestrator credential (Keychain Claude/BWS_ACCESS_TOKEN_SDS)"
BROAD_IDENTITY="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
[ -n "$BROAD_IDENTITY" ] || sds_outcome_fail \
    "FATAL: no BWS identity for the change-manager credential (Keychain Claude/BWS_ACCESS_TOKEN_VPS_BACKUP)"
ORCHESTRATOR_API_TOKEN="$(sds_bws_value "$ORCHESTRATOR_SYSTEM_UUID" "$NARROW_IDENTITY")"
[ -n "$ORCHESTRATOR_API_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the orchestrator system credential from BWS"
SDS_BRIDGE_ALERT_TOKEN="$(sds_bws_value "$SDS_BRIDGE_ALERT_UUID" "$NARROW_IDENTITY")"
[ -n "$SDS_BRIDGE_ALERT_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the alert-hub sender credential from BWS"
CHANGE_MANAGER_TOKEN="$(sds_bws_value "$CHANGE_MANAGER_READ_UUID" "$BROAD_IDENTITY")"
[ -n "$CHANGE_MANAGER_TOKEN" ] || sds_outcome_fail \
    "FATAL: could not read the change-manager READ credential from BWS"
CODE_BRAIN_KEY="$(sds_bws_value "$CODE_BRAIN_KEY_UUID" "$BROAD_IDENTITY")"
[ -n "$CODE_BRAIN_KEY" ] || sds_outcome_fail \
    "FATAL: could not read the Code Brain key from BWS"
INFRA_BRAIN_KEY="$(sds_bws_value "$INFRA_BRAIN_KEY_UUID" "$BROAD_IDENTITY")"
[ -n "$INFRA_BRAIN_KEY" ] || sds_outcome_fail \
    "FATAL: could not read the Infra Brain key from BWS"
unset NARROW_IDENTITY BROAD_IDENTITY
BUMP_DRIVER_GITHUB_TOKEN="${BUMP_DRIVER_GITHUB_TOKEN:-$(gh auth token 2>/dev/null || true)}"
[ -n "$BUMP_DRIVER_GITHUB_TOKEN" ] || sds_outcome_fail \
    "FATAL: no GitHub credential. Set BUMP_DRIVER_GITHUB_TOKEN, or authenticate gh."

ORCHESTRATOR_API_URL="${ORCHESTRATOR_API_URL:-https://sds.alobar.net}"
ORCHESTRATOR_API_CREDENTIAL_KEY_ID="orchestrator-system"
PATH="$REPO_ROOT/.venv/bin:$HOME/Projects/intent-packages/.venv/bin:$PATH"
FACTORY_PYTHONPATH="$HOME/Projects/project-standards/src:$HOME/Projects/security-standards/src"
export PATH FACTORY_PYTHONPATH ORCHESTRATOR_API_URL ORCHESTRATOR_API_CREDENTIAL_KEY_ID \
  ORCHESTRATOR_API_TOKEN SDS_BRIDGE_ALERT_TOKEN CHANGE_MANAGER_TOKEN CODE_BRAIN_KEY \
  INFRA_BRAIN_KEY BUMP_DRIVER_GITHUB_TOKEN

"$REPO_ROOT/.venv/bin/bump-driver" "$@" > "$SDS_OUTCOME_REPORT" 2>&1
rc=$?
# A code outside {0,1,2,3} is the program dying in a way it doesn't describe (127 is a missing
# binary); report it as the tool failing rather than folding it to 0.
case "$rc" in
  0|1|2|3) ;;
  *) echo "FATAL: bump-driver exited $rc" >> "$SDS_OUTCOME_REPORT"; rc=1 ;;
esac
sds_outcome_end "$rc" --told 2
