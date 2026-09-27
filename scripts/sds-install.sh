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
