#!/usr/bin/env bash
# Read one secret's value out of Bitwarden Secrets Manager: the ONE copy every script here uses.
# It also holds the one reader of the factory-events chain switch (the last section).
# Every launcher that fetches a secret sources it, and so do `sds-deadman.sh` (the Healthchecks key
# read, which reaches every `scripts/run-*.sh`), `sds-install.sh` and the installers that read a
# secret.
#
# Source this (don't execute it):
#     source "$REPO_ROOT/scripts/sds-bws.sh"
#
# Then:
#     sds_bws_value <secret-uuid>            # uses the BWS_ACCESS_TOKEN already in the environment
#     sds_bws_value <secret-uuid> <token>    # uses <token> for this one fetch, and nothing else
#
# Prints the value on stdout and nothing else. On any failure it prints NOTHING on stdout and
# returns non-zero, so a caller's `X="$(sds_bws_value …)"` followed by its own `[ -z "$X" ]` check
# behaves exactly as it did when each launcher carried its own copy. It never echoes what `bws`
# returned, on either stream.
#
# THE TOKEN IS AN ASSIGNMENT PREFIX, NEVER AN ARGUMENT. In the two-argument form the shell puts
# `BWS_ACCESS_TOKEN` in `env`'s environment, which `bws` inherits; spelled as `env ... NAME=value`,
# the token would be one of `env`'s arguments and show in the process list (FE-5 cut-over plan,
# P-OR-1; `tests/scripts/test_sds_bws.py`).
#
# THE TWO-ARGUMENT FORM IS KEYED ON THE ARGUMENT COUNT, NOT ON WHETHER THE SECOND IS EMPTY.
# `BWS_ACCESS_TOKEN=""` for one fetch is a different request from inheriting the ambient token, and
# the launchers that read both identities (`grep -l BWS_ACCESS_TOKEN_VPS_BACKUP scripts/run-*.sh`)
# pass the second argument precisely so that one ambient value can never serve both
# (docs/operations/credentials.md #175, "sds-token.sh RESPECTS an already-set BWS_ACCESS_TOKEN").
# So this helper is identity-agnostic on purpose, and it does NOT source `sds-token.sh`: that helper
# reads the Keychain and exports as a side effect, which those launchers deliberately avoid.
#
# THE COLOUR GUARD. FORCE_COLOR / CLICOLOR_FORCE make `bws secret get` wrap its JSON in ANSI
# escapes even when stdout is a pipe. Both are unset for the call AND `--color no` is passed, so
# neither depends on the other winning.
#
# THE INTERPRETER IS THE REPOSITORY'S OWN VENV, BY ABSOLUTE PATH, AND NEVER `python3` FROM PATH.
# Under launchd, PATH resolves bare `python3` to /usr/bin/python3, an Xcode shim that refuses to
# run while the Xcode license is unaccepted. On 2026-09-15 an Xcode update reset the license and
# every lane stopped at this exact parse, each reporting that BWS could not be read -- BWS was
# fine. The venv is already a precondition of every lane (each launcher runs
# `$REPO_ROOT/.venv/bin/<program>` and each installer refuses without it), so this adds no new
# dependency, and the parse below is byte-identical to the one it replaces. The path is resolved
# from THIS file's location rather than from the caller's REPO_ROOT, so the helper needs nothing
# from its caller. NOTE WHAT THIS DOES NOT FIX: `/usr/bin/git` is the same kind of shim, so a
# license reset still stops any lane that runs git; this makes the secret read say the true thing.
#
# SHARED: `sds-deadman.sh` parses the Healthchecks check listing with `$_SDS_BWS_PYTHON` too, so
# renaming it or changing what it points at changes that parse as well.

_SDS_BWS_PYTHON="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.venv/bin/python"

sds_bws_value() {
  if [ ! -x "$_SDS_BWS_PYTHON" ]; then
    echo "sds_bws_value: $_SDS_BWS_PYTHON is missing; cannot parse the BWS response." \
         "Run: uv sync --frozen" >&2
    return 1
  fi
  if [ "$#" -ge 2 ]; then
    BWS_ACCESS_TOKEN="$2" env -u FORCE_COLOR -u CLICOLOR_FORCE \
      bws secret get "$1" --output json --color no \
      | "$_SDS_BWS_PYTHON" -c 'import sys, json; print(json.load(sys.stdin)["value"])'
  else
    env -u FORCE_COLOR -u CLICOLOR_FORCE \
      bws secret get "$1" --output json --color no \
      | "$_SDS_BWS_PYTHON" -c 'import sys, json; print(json.load(sys.stdin)["value"])'
  fi
}

# ── The factory-events chain switch (FE-5 cut-over plan, "The switch") ─────────────────────────
# `~/.factory/chain-db.env` is one non-secret settings file. Absent, a holder behaves as it always
# has: it fetches no password and sets no chain variable. Present, it holds exactly three lines,
# `FACTORY_EVENTS_DB_HOST=`, `FACTORY_EVENTS_DB_PORT=` and `FACTORY_EVENTS_DB_NAME=`, and the
# holder fetches its role's password through its own account and passes the password and the three
# settings to the one child that needs them, as an assignment prefix. The bump proposer, the work
# carrier and the session wrapper read it here, and security-standards' `factory-events-nightly.sh`
# (4e83d7a) reads it with the same refusals, so every holder reads the file the same way.
#
# Each function takes the file's path, defaulting to the switch, FACTORY_EVENTS_SWITCH_FILE. The
# session wrapper passes the act's staging copy, `chain-db.env.pending`, instead.
#
# `factory_events_switch_present [path]` is true when the path exists or is a symlink, dangling or
# not, so a symlinked switch is refused by the reader below rather than read as absent.
#
# `factory_events_read_settings [path]` parses the file and never sources it, so the file can set
# nothing but these three values. It returns 0 with CHAIN_DB_HOST, CHAIN_DB_PORT and CHAIN_DB_NAME
# set and NOT exported: it unsets each first, because a name the caller's environment exported
# (`~/.factory/env` is sourced under `set -a` elsewhere) stays exported after a plain assignment.
# It returns 1 when it refuses the file, and `factory_events_settings_refusal` then prints why,
# naming the file, the line number and the key, never a value, in case a secret was pasted in. The
# reader prints nothing and calls no outcome alarm: each caller fails its own pass with the refusal.
#
# It runs builtins only (`tests/architecture/test_runtime_edges.py` pins this file's commands), so
# the line number is the length of a string grown by one character a line, not arithmetic.
FACTORY_EVENTS_SWITCH_FILE="${HOME:-}/.factory/chain-db.env"

factory_events_switch_present() {
  local file="${1:-$FACTORY_EVENTS_SWITCH_FILE}"
  [ -e "$file" ] || [ -L "$file" ]
}

factory_events_settings_refusal() {
  printf '%s\n' "${CHAIN_SETTINGS_REFUSAL:-}"
}

_factory_events_refuse() {
  unset CHAIN_DB_HOST CHAIN_DB_PORT CHAIN_DB_NAME
  CHAIN_SETTINGS_REFUSAL="$1"
}

factory_events_read_settings() {
  local file="${1:-$FACTORY_EVENTS_SWITCH_FILE}" line key value previous lines=""
  unset CHAIN_DB_HOST CHAIN_DB_PORT CHAIN_DB_NAME CHAIN_SETTINGS_REFUSAL
  CHAIN_DB_HOST=""
  CHAIN_DB_PORT=""
  CHAIN_DB_NAME=""
  if [ -L "$file" ] || [ ! -f "$file" ]; then
    _factory_events_refuse "$file is not a regular file"
    return 1
  fi
  if [ ! -r "$file" ]; then
    _factory_events_refuse "$file is not readable"
    return 1
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    lines="$lines."
    case "$line" in
      *=*) ;;
      *)
        _factory_events_refuse "$file line ${#lines} is not KEY=value"
        return 1 ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      FACTORY_EVENTS_DB_HOST) previous="$CHAIN_DB_HOST"; CHAIN_DB_HOST="$value" ;;
      FACTORY_EVENTS_DB_PORT) previous="$CHAIN_DB_PORT"; CHAIN_DB_PORT="$value" ;;
      FACTORY_EVENTS_DB_NAME) previous="$CHAIN_DB_NAME"; CHAIN_DB_NAME="$value" ;;
      *)
        _factory_events_refuse "$file line ${#lines} has an unknown key"
        return 1 ;;
    esac
    if [ -n "$previous" ]; then
      _factory_events_refuse "$file sets $key twice"
      return 1
    fi
    # No quoting and no spaces: a value is a host, a port or a database name, taken literally.
    case "$value" in
      "" | *[!A-Za-z0-9._:-]*)
        _factory_events_refuse \
          "$file line ${#lines}: $key is empty or has a character outside [A-Za-z0-9._:-]"
        return 1 ;;
    esac
  done < "$file"
  if [ -z "$CHAIN_DB_HOST" ]; then
    _factory_events_refuse "$file has no FACTORY_EVENTS_DB_HOST"
    return 1
  fi
  if [ -z "$CHAIN_DB_PORT" ]; then
    _factory_events_refuse "$file has no FACTORY_EVENTS_DB_PORT"
    return 1
  fi
  if [ -z "$CHAIN_DB_NAME" ]; then
    _factory_events_refuse "$file has no FACTORY_EVENTS_DB_NAME"
    return 1
  fi
}
