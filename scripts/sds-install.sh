#!/usr/bin/env bash
# The refusal every `scripts/install-*-launchd.sh` must make before it writes anything.
#
# Source this (don't execute it) from an installer, then call it BEFORE the plist is written:
#     source "$REPO_ROOT/scripts/sds-install.sh"
#     sds_refuse_linked_worktree "$REPO_ROOT"
#
# INSTALL FROM THE MAIN TREE, NEVER FROM A BUILD WORKTREE. An installer resolves REPO_ROOT from its
# own location and writes it into the plist verbatim, so installing from a worktree pins the
# LaunchAgent to a path that is deleted at teardown -- after which the job fails on every run with
# nothing reporting it. Build sessions work in worktrees here by convention, so this is the likely
# mistake. `--git-dir` and `--git-common-dir` differ in a linked worktree and are equal in a main
# tree, measured both ways.
#
# ONE COPY, BECAUSE THE COPIES DRIFTED: until 2026-09-27 six installers carried this check inline
# and five did not -- two of those five with a header comment claiming it.
# `tests/scripts/test_launchd_installers.py` fails if any installer stops calling it, or calls it
# after the plist is written.

sds_refuse_linked_worktree() {
  local root="$1"
  if [ "$(git -C "$root" rev-parse --git-dir)" != \
       "$(git -C "$root" rev-parse --git-common-dir)" ]; then
    echo "FATAL: $root is a linked worktree. Install from the main tree." >&2
    exit 1
  fi
}

# Upsert one Healthchecks check from a v3 API payload (JSON, with `"unique": ["name"]`), so a re-run
# updates the check rather than adding another. Prints `created` (201) or `updated` (200) on stdout;
# on anything else prints one WARNING line on stderr and returns non-zero. Neither the API key nor
# the ping URL is printed: the key reaches curl on stdin as its config, and the response (which
# carries the ping URL) is discarded for its status code.
#
# The caller decides whether a failure stops the install; the installers report it and carry on,
# because the pass still runs and logs, and rerunning the installer retries the upsert.
sds_upsert_check() {
  local payload="$1" broad key status
  broad="${BWS_ACCESS_TOKEN_BROAD:-$(/usr/bin/security find-generic-password \
    -s 'Claude' -a 'BWS_ACCESS_TOKEN_VPS_BACKUP' -w 2>/dev/null || true)}"
  if [ -z "$broad" ]; then
    echo "WARNING: no broad BWS identity; the Healthchecks check was not upserted" >&2
    return 1
  fi
  # shellcheck disable=SC1091
  source "$(dirname "${BASH_SOURCE[0]}")/sds-bws.sh"
  key="$(sds_bws_value "51509d5e-64f5-4781-ae82-b4ed00b644e1" "$broad" 2>/dev/null || true)"
  if [ -z "$key" ]; then
    echo "WARNING: the Healthchecks API key could not be read; the check was not upserted" >&2
    return 1
  fi
  status="$(printf 'header = "X-Api-Key: %s"\n' "$key" \
    | curl -sS --max-time 15 --config - -o /dev/null -w '%{http_code}' -X POST \
      "https://healthcheck.alobar.net/api/v3/checks/" \
      -H "Content-Type: application/json" --data-binary "$payload" 2>/dev/null || true)"
  case "$status" in
    200) echo "updated" ;;
    201) echo "created" ;;
    *)
      echo "WARNING: upserting the Healthchecks check answered '${status:-nothing}'" >&2
      return 1
      ;;
  esac
}
