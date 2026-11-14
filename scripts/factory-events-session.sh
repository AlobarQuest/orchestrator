#!/usr/bin/env bash
# Run one factory-events command that appends to the database chain, for an interactive session
# (FE-5 cut-over plan, P-OR-2; ADR-0055 amendment 25 items 3 and 4, amendment 26). Runbook:
# docs/operations/factory-events-chain.md.
#
#     scripts/factory-events-session.sh approve <intent-packages checkout> <package-dir>
#     scripts/factory-events-session.sh import-file <events file>
#
# Exactly one child runs, and only it gets the chain: the append password and the three settings,
# as an assignment prefix on its command line, in its environment alone. The password is read here
# through the `factory-events-session` account (Keychain `Claude` /
# `BWS_ACCESS_TOKEN_FACTORY_EVENTS_SESSION`), and that token reaches only `bws`, through
# `sds_bws_value`'s assignment prefix. A session runs this with nothing secret on its command line,
# so nothing secret reaches its transcript. Its output is the child's output.
#
#   - `approve`: `<checkout>/.venv/bin/python -m intent_packages approve <package-dir>`, run in
#     `<checkout>`, because `approve` records `git rev-parse HEAD` of its working directory as the
#     approval's commit (sds-hq's standing approval runs it in its clone the same way). A relative
#     `<package-dir>` is taken from the caller's directory. No other argument passes through: the
#     approver stays the CLI's default, `devon`, and the event's actor stays `FACTORY_AGENT_ID` or
#     the emitter's default, exactly as a session's `approve` records them without this wrapper.
#     The settings come from the switch, `~/.factory/chain-db.env`; without it this refuses.
#   - `import-file`: security-standards' `.venv-events` interpreter, `-m factory_events import-file
#     --path <events file>`. The settings come from the act's staging copy,
#     `~/.factory/chain-db.env.pending`, only, and this refuses while the switch exists: an
#     `approve` that appended before the import would make the import refuse a non-empty chain.
#
# There is no `verify` (plan open question 8): a session doesn't read the chain.
#
# FAIL CLOSED. Every refusal prints `factory-events-session: <reason>` on stderr and exits non-zero:
# an unknown subcommand, a wrong argument count, a missing or malformed settings file, a missing
# interpreter, a missing Keychain item, and an empty password. It never falls back to the file
# chain, and it refuses before it reads any credential wherever it can.
#
# NAMES. Every name this script assigns, and the five chain variables, are unset first, so a value
# a caller (or `~/.factory/env` under `set -a`) exported can't reach the child. A retired
# `FACTORY_EVENTS_*_DSN` is left set, so the child refuses it by name. A `BWS_ACCESS_TOKEN` in the
# caller's environment is never used for the fetch: the token is passed to `sds_bws_value` as its
# second argument. Nothing here is exported.
#
# EXIT CODES: the child's, when it runs; 2 for every refusal here.

set -uo pipefail

unset SESSION_COMMAND SESSION_CHECKOUT SESSION_PACKAGE SESSION_EVENTS_FILE SESSION_SETTINGS \
  SESSION_PYTHON SESSION_EVENTS_IDENTITY CHAIN_PASSWORD CHAIN_DB_HOST CHAIN_DB_PORT CHAIN_DB_NAME \
  CHAIN_SETTINGS_REFUSAL REPO_ROOT SWITCH STAGING FACTORY_EVENTS_APPEND_UUID rc
unset FACTORY_EVENTS_APPEND_PASSWORD FACTORY_EVENTS_READ_PASSWORD \
  FACTORY_EVENTS_DB_HOST FACTORY_EVENTS_DB_PORT FACTORY_EVENTS_DB_NAME

FACTORY_EVENTS_APPEND_UUID="2ce0dbc7-eeac-4bf0-8204-b50200c97516"  # factory-events-append-password

refuse() {
  echo "factory-events-session: $*" >&2
  exit 2
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-bws.sh"

[ -n "${HOME:-}" ] || refuse "HOME is not set"
SWITCH="$HOME/.factory/chain-db.env"
STAGING="$HOME/.factory/chain-db.env.pending"

SESSION_COMMAND="${1:-}"
case "$SESSION_COMMAND" in
  approve)
    [ "$#" -eq 3 ] || refuse "usage: approve <intent-packages checkout> <package-dir>"
    case "$2" in
      "" | -*) refuse "approve: the checkout must be a path, not an option or empty" ;;
    esac
    # Absolute, so the interpreter checked here is the one run after entering the checkout.
    SESSION_CHECKOUT="$(cd "$2" 2>/dev/null && pwd -P)" \
      || refuse "approve: $2 is not a directory"
    SESSION_PACKAGE="$3"
    case "$SESSION_PACKAGE" in
      "" | -*) refuse "approve: the package directory must be a path, not an option or empty" ;;
      /*) ;;
      *) SESSION_PACKAGE="$PWD/$SESSION_PACKAGE" ;;
    esac
    factory_events_switch_present "$SWITCH" \
      || refuse "approve: $SWITCH is absent; the chain is not switched to the database"
    SESSION_SETTINGS="$SWITCH"
    SESSION_PYTHON="$SESSION_CHECKOUT/.venv/bin/python"
    ;;
  import-file)
    [ "$#" -eq 2 ] || refuse "usage: import-file <events file>"
    SESSION_EVENTS_FILE="$2"
    case "$SESSION_EVENTS_FILE" in
      "" | -*) refuse "import-file: the events file must be a path, not an option or empty" ;;
    esac
    if factory_events_switch_present "$SWITCH"; then
      refuse "import-file: $SWITCH exists; the import runs only before the switch is set"
    fi
    factory_events_switch_present "$STAGING" || refuse "import-file: $STAGING is absent"
    SESSION_SETTINGS="$STAGING"
    SESSION_PYTHON="$HOME/Projects/security-standards/.venv-events/bin/python"
    ;;
  *)
    refuse "unknown subcommand '${SESSION_COMMAND}'; expected approve or import-file"
    ;;
esac

factory_events_read_settings "$SESSION_SETTINGS" \
  || refuse "$SESSION_COMMAND: $(factory_events_settings_refusal)"
[ -x "$SESSION_PYTHON" ] || refuse "$SESSION_COMMAND: $SESSION_PYTHON is missing or not executable"
if [ "$SESSION_COMMAND" = "approve" ]; then
  cd "$SESSION_CHECKOUT" || refuse "approve: cannot enter $SESSION_CHECKOUT"
fi

SESSION_EVENTS_IDENTITY="$(/usr/bin/security find-generic-password \
  -s 'Claude' -a 'BWS_ACCESS_TOKEN_FACTORY_EVENTS_SESSION' -w 2>/dev/null || true)"
[ -n "$SESSION_EVENTS_IDENTITY" ] || refuse \
  "$SESSION_COMMAND: no BWS identity (Keychain Claude / BWS_ACCESS_TOKEN_FACTORY_EVENTS_SESSION)"
CHAIN_PASSWORD="$(sds_bws_value "$FACTORY_EVENTS_APPEND_UUID" "$SESSION_EVENTS_IDENTITY" || true)"
unset SESSION_EVENTS_IDENTITY
[ -n "$CHAIN_PASSWORD" ] || refuse \
  "$SESSION_COMMAND: could not read the factory-events append password from BWS (factory-events-session)"

if [ "$SESSION_COMMAND" = "approve" ]; then
  FACTORY_EVENTS_APPEND_PASSWORD="$CHAIN_PASSWORD" \
    FACTORY_EVENTS_DB_HOST="$CHAIN_DB_HOST" \
    FACTORY_EVENTS_DB_PORT="$CHAIN_DB_PORT" \
    FACTORY_EVENTS_DB_NAME="$CHAIN_DB_NAME" \
    "$SESSION_PYTHON" -m intent_packages approve "$SESSION_PACKAGE"
else
  FACTORY_EVENTS_APPEND_PASSWORD="$CHAIN_PASSWORD" \
    FACTORY_EVENTS_DB_HOST="$CHAIN_DB_HOST" \
    FACTORY_EVENTS_DB_PORT="$CHAIN_DB_PORT" \
    FACTORY_EVENTS_DB_NAME="$CHAIN_DB_NAME" \
    "$SESSION_PYTHON" -m factory_events import-file --path "$SESSION_EVENTS_FILE"
fi
rc=$?
unset CHAIN_PASSWORD
exit "$rc"
