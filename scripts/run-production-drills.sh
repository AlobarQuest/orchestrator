#!/bin/bash
# Entry point for the production recovery-drill runner.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/production_drill_common.sh" "$@"
