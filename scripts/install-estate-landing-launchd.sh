#!/usr/bin/env bash
# Install the in-window estate-landing LaunchAgent (ADR-0019 increment 5b).
#
# A SEPARATE OPERATOR STEP, and the most consequential one in this repository. It is the only
# schedule whose pass can end in something changing a running service, so installing it is not
# something a build session does on its own.
#
# INSTALLING IT IS NOT ENOUGH, and that is deliberate. The orchestrator refuses every landing
# until `ORCHESTRATOR_ESTATE_LANDING_ENABLED` is true in its environment, so this schedule is
# inert on its own: the job runs, asks, is refused with `landing_not_enabled`, and reports. Two
# separate acts, in two separate systems, and either one alone changes nothing.
#
# Usage: scripts/install-estate-landing-launchd.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.devon.estate-landing"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$REPO_ROOT/.venv/bin/estate-landing" ]; then
  echo "FATAL: $REPO_ROOT/.venv/bin/estate-landing is missing — run 'uv pip install -e .' first" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" -e "s|__HOME__|$HOME|g" "$TEMPLATE" > "$TARGET"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "installed $TARGET"
echo "  runs at 02:15, 03:15, 04:15 and 05:15 local; log: $HOME/Library/Logs/estate-landing.log"
echo "  it lands NOTHING until ORCHESTRATOR_ESTATE_LANDING_ENABLED is true in production"
echo "  report now, without asking for anything: $REPO_ROOT/scripts/run-estate-landing.sh"
echo "  (bare reports; add --submit to ask for the landings)"
