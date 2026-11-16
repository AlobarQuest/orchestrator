#!/usr/bin/env bash
# git's GitHub credential, from the environment (ADR-0059 decision 2, "git gets its credential from
# the environment"). A lane launcher that clones sources this and calls the helper once, before its
# first clone.
#
# Source this (don't execute it):
#     source "$REPO_ROOT/scripts/sds-git.sh"
#
# Then:
#     sds_git_credential <token-variable>                      # the work carrier
#     sds_git_credential <token-variable> <user.name> <user.email>  # a proposer's `[bot]` identity
#
# It takes the token from the variable NAMED by its first argument, never the token itself, so the
# token is never one of this function's arguments. It exports git's environment configuration:
#   - `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`, so no global or system file adds a
#     credential helper, an identity or a URL rewrite;
#   - `GIT_TERMINAL_PROMPT=0`, so a refused credential fails instead of waiting on a prompt;
#   - `GIT_CONFIG_COUNT` with `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` pairs. Pair 0 sets
#     `http.https://github.com/.extraheader` to `AUTHORIZATION: basic` and the base64 of
#     `x-access-token:<token>`, the shape `Mirrors.environment` builds (`src/bump_driver/mirror.py`).
#     Pairs 1 and 2, only when the identity is passed, set `user.name` and `user.email`. They are for
#     the two proposers, whose commits must be the "SDS lanes" App's; the work carrier commits
#     nothing and passes none.
#
# THE TOKEN IS NEVER IN A PROCESS ARGUMENT. `printf` is a shell builtin, so the token reaches
# `base64` on its stdin, and no `exec` carries it in argv. Remotes stay bare `https://github.com/`
# URLs, so the token is never in any `.git/config`, and git sends the header only to `github.com`.
# A git process holds it only in its environment, which every process of the pass already shares
# through the lane's own variable.
#
# Nothing here prints the token or the header, and it turns git's trace off. Returns non-zero,
# exporting nothing, when the named variable is empty or the identity is half given.

sds_git_credential() {
  local name="${1:-}" user_name="${2:-}" user_email="${3:-}" token encoded
  case "$name" in
    '' | [!A-Za-z_]* | *[!A-Za-z0-9_]*)
      echo "sds_git_credential: '$name' is not a variable name" >&2
      return 1
      ;;
  esac
  if [ "$#" -ne 1 ] && [ "$#" -ne 3 ]; then
    echo "sds_git_credential: pass a variable name, and optionally user.name and user.email" >&2
    return 1
  fi
  if [ "$#" -eq 3 ] && { [ -z "$user_name" ] || [ -z "$user_email" ]; }; then
    echo "sds_git_credential: an identity needs both user.name and user.email" >&2
    return 1
  fi
  token="${!name:-}"
  if [ -z "$token" ]; then
    echo "sds_git_credential: $name is empty; git has no credential" >&2
    return 1
  fi
  # GNU base64 wraps at 76 columns, so the newlines go; `tr` sees only the encoding.
  encoded="$(printf 'x-access-token:%s' "$token" | base64 | tr -d '\n')"
  if [ -z "$encoded" ]; then
    echo "sds_git_credential: could not encode the credential" >&2
    return 1
  fi
  # git's trace and curl's verbose output would print the header; none of them is left on.
  unset GIT_TRACE GIT_TRACE_CURL GIT_TRACE_PACKET GIT_CURL_VERBOSE
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0
  export GIT_CONFIG_KEY_0="http.https://github.com/.extraheader"
  export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $encoded"
  if [ "$#" -eq 3 ]; then
    export GIT_CONFIG_KEY_1="user.name" GIT_CONFIG_VALUE_1="$user_name"
    export GIT_CONFIG_KEY_2="user.email" GIT_CONFIG_VALUE_2="$user_email"
    export GIT_CONFIG_COUNT=3
  else
    unset GIT_CONFIG_KEY_1 GIT_CONFIG_VALUE_1 GIT_CONFIG_KEY_2 GIT_CONFIG_VALUE_2
    export GIT_CONFIG_COUNT=1
  fi
}
