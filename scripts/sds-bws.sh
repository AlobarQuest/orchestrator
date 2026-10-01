#!/usr/bin/env bash
# Read one secret's value out of Bitwarden Secrets Manager: the ONE copy every launcher uses.
#
# Source this (don't execute it) from a `scripts/run-*.sh` launcher:
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
# THE TWO-ARGUMENT FORM IS KEYED ON THE ARGUMENT COUNT, NOT ON WHETHER THE SECOND IS EMPTY.
# `BWS_ACCESS_TOKEN=""` for one fetch is a different request from inheriting the ambient token, and
# the six launchers that juggle two identities pass the second argument precisely so that one
# ambient value can never serve both (docs/operations/credentials.md #175, "sds-token.sh RESPECTS an already-set
# BWS_ACCESS_TOKEN"). So this helper is identity-agnostic on purpose, and it does NOT source
# `sds-token.sh`: that helper reads the Keychain and exports as a side effect, which those six
# deliberately avoid.
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

_SDS_BWS_PYTHON="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.venv/bin/python"

sds_bws_value() {
  if [ ! -x "$_SDS_BWS_PYTHON" ]; then
    echo "sds_bws_value: $_SDS_BWS_PYTHON is missing; cannot parse the BWS response." \
         "Run: uv sync --frozen" >&2
    return 1
  fi
  if [ "$#" -ge 2 ]; then
    env -u FORCE_COLOR -u CLICOLOR_FORCE BWS_ACCESS_TOKEN="$2" \
      bws secret get "$1" --output json --color no \
      | "$_SDS_BWS_PYTHON" -c 'import sys, json; print(json.load(sys.stdin)["value"])'
  else
    env -u FORCE_COLOR -u CLICOLOR_FORCE \
      bws secret get "$1" --output json --color no \
      | "$_SDS_BWS_PYTHON" -c 'import sys, json; print(json.load(sys.stdin)["value"])'
  fi
}
