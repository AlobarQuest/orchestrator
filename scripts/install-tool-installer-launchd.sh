#!/usr/bin/env bash
# Install (or reinstall) the nightly tool-installer LaunchAgent (ADR-0042).
#
# Run by whoever is building the lane. Devon's role is the decision; installing what he has
# already decided on is mechanics, and he does not install LaunchAgents.
#
# It refuses to run from a linked worktree, which is the real hazard: REPO_ROOT is written into
# the plist verbatim, so a plist installed from a torn-down worktree dies every night with nothing
# reporting it.
#
# NOTE THE PLIST PASSES `--install`. That is what makes the scheduled pass act; the change window
# it reads from the deployed policy is the other term, and both must hold. Run the launcher by
# hand without that flag to see what a pass would do without touching a binary.
#
# Verify afterwards with:
#   launchctl list | grep tool-installer
#   /bin/bash scripts/run-tool-installer.sh --dry-run   # touches nothing, files nothing
#
# Uninstall with:
#   launchctl bootout "gui/$(id -u)/com.devon.tool-installer"
#   rm ~/Library/LaunchAgents/com.devon.tool-installer.plist
set -euo pipefail

LABEL="com.devon.tool-installer"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

# INSTALL FROM THE MAIN TREE, NEVER FROM A BUILD WORKTREE. `REPO_ROOT` is resolved from this
# script's own location and is written into the plist verbatim, so installing from a worktree pins
# the LaunchAgent to a path that gets deleted at teardown -- after which the job dies every night
# with nothing reporting it. Building sessions work in worktrees here by convention, so this is
# the likely mistake. The discriminator is that `--git-dir` and `--git-common-dir` differ in a
# linked worktree and are equal in a main tree.
if [ "$(git -C "$REPO_ROOT" rev-parse --git-dir)" != \
     "$(git -C "$REPO_ROOT" rev-parse --git-common-dir)" ]; then
  echo "FATAL: $REPO_ROOT is a linked worktree. Install from the main tree." >&2
  exit 1
fi

# A console script does NOT arrive with a `git pull`: `uv sync` installs it, and a fresh worktree
# may additionally need `uv sync --reinstall-package orchestrator`. Refusing here turns that into
# a message at install time rather than a 127 at 03:15.
if [ ! -x "$REPO_ROOT/.venv/bin/tool-installer" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/tool-installer is missing. Run: uv sync --frozen" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$TARGET"

# `bootout` first so a reinstall replaces rather than layering. It fails when nothing is loaded,
# which is the ordinary first-install case, so its status is deliberately ignored.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
launchctl list | grep "$LABEL" || true
