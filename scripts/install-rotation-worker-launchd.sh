#!/usr/bin/env bash
# Install or update the rotation executor's Keychain instance on this Mac (ADR-0055 amendment 12).
#
# IT RUNS A PINNED COMMIT, NOT THE MAIN TREE. Every other LaunchAgent here runs the main tree's
# working copy, which any session can edit. This one rotates credentials, so, like the VM instance
# (`scripts/rotation-worker-vm/provision.sh`), it checks the orchestrator out at exactly the commit
# given into ~/Library/Application Support/rotation-worker/src, and builds its environment there
# from the lockfile. An update is this script again with a newer commit.
#
# IT WRITES NO CREDENTIAL. The configuration it writes holds names only. The instance's bearer is
# a Keychain item `rotation-executor`/`worker-bearer`, created by `rotation-worker mint-bearer`,
# and until it exists the agent is installed but not loaded. Run this again after minting.
#
# Verify afterwards with:
#   launchctl list | grep rotation-worker
#   tail ~/Library/Logs/rotation-worker.log
#
# Uninstall with:
#   launchctl bootout "gui/$(id -u)/com.devon.rotation-worker"
#   rm ~/Library/LaunchAgents/com.devon.rotation-worker.plist
#
# Usage: scripts/install-rotation-worker-launchd.sh <40-character orchestrator commit>
set -euo pipefail

LABEL="com.devon.rotation-worker"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

# Refuses to run from a linked worktree, as every installer does, though nothing here runs from
# REPO_ROOT but the template.
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/sds-install.sh"
sds_refuse_linked_worktree "$REPO_ROOT"

commit="${1:-}"
if [[ ! "$commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "FATAL: pass the full 40-character orchestrator commit to install" >&2
  exit 1
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
if [ ! -x "$UV" ]; then
  echo "FATAL: uv is missing; it builds the executor's environment from the lockfile" >&2
  exit 1
fi

BASE="$HOME/Library/Application Support/rotation-worker"
SRC="$BASE/src"
SERVICE="rotation-executor"
BEARER="worker-bearer"
HEALTHCHECKS_KEY="healthchecks-key"

if [ ! -d "$SRC/.git" ]; then
  git clone --quiet --no-checkout https://github.com/AlobarQuest/orchestrator.git "$SRC"
fi
# GitHub serves any commit reachable from a ref by its id, so a pull request's commit can be
# installed to test it before it merges.
git -C "$SRC" fetch --quiet origin "$commit"
git -C "$SRC" -c advice.detachedHead=false checkout --quiet --force "$commit"
UV_PROJECT_ENVIRONMENT="$BASE/venv" \
  "$UV" sync --quiet --frozen --no-dev --group rotation-worker --project "$SRC"

mkdir -p "$BASE/state"
cat > "$BASE/config.toml.new" <<EOF
# Written by scripts/install-rotation-worker-launchd.sh for ${commit:0:12}. Names only, no value.
store = "keychain"
orchestrator_url = "https://sds.alobar.net"
credential_key_id = "rotation-executor-keychain-1"
work_project_id = "$SERVICE"
bearer_secret_id = "$BEARER"
state_dir = "$BASE/state"
source_revision = "${commit:0:12}"
EOF
# Named only once the item exists, so a pass before then reads no check rather than failing on a
# missing item. Devon creates it ("Install or update the Keychain instance").
if /usr/bin/security find-generic-password -s "$SERVICE" -a "$HEALTHCHECKS_KEY" > /dev/null 2>&1; then
  echo "healthchecks_key_id = \"$HEALTHCHECKS_KEY\"" >> "$BASE/config.toml.new"
fi
mv "$BASE/config.toml.new" "$BASE/config.toml"

# RENDERED TO A TEMPORARY FILE AND MOVED INTO PLACE ONLY WHEN IT WILL BE LOADED: launchd loads
# every plist in ~/Library/LaunchAgents at login, and a pass without a bearer fails every five
# minutes.
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
sed -e "s|__BASE__|$BASE|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$RENDERED"
plutil -lint "$RENDERED" > /dev/null

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if ! /usr/bin/security find-generic-password -s "$SERVICE" -a "$BEARER" > /dev/null 2>&1; then
  rm -f "$TARGET"
  echo "installed ${commit:0:12} in $BASE; the agent is NOT loaded: no bearer yet."
  echo "  mint it: \"$BASE/venv/bin/rotation-worker\" mint-bearer --config \"$BASE/config.toml\""
  echo "  then register its hash with the orchestrator and run this script again."
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
mv "$RENDERED" "$TARGET"
launchctl bootstrap "gui/$(id -u)" "$TARGET"
echo "installed ${commit:0:12}; a pass every five minutes; log: $HOME/Library/Logs/rotation-worker.log"
launchctl list | grep "$LABEL" || true
