#!/usr/bin/env bash
# Install the hourly change-proposer LaunchAgent (ADR-0019 increment 5a).
#
# A SEPARATE OPERATOR STEP, deliberately. Writing a LaunchAgent changes what Devon's machine does
# when nobody is watching, which is not something a build session installs on its own.
#
# UNTIL THIS IS RUN, THE PRODUCER HAS NO CALLER -- and a producer with no caller is this estate's
# most-repeated defect, named in increment 4's own report as the debt this increment pays. It also
# needs the PROPOSE-scoped credential to exist; the wrapper refuses loudly rather than falling back
# to a bearer that could approve its own proposals.
#
# Usage: scripts/install-change-proposer-launchd.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.devon.change-proposer"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$REPO_ROOT/.venv/bin/change-proposer" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/change-proposer is missing — run 'uv pip install -e .' first" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$TARGET"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
echo "  runs hourly; log: $HOME/Library/Logs/change-proposer.log"
echo "  run one pass now with: launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "  or by hand, without writing anything: $REPO_ROOT/scripts/run-change-proposer.sh"
echo "  (bare is a dry run; add --submit to propose)"
