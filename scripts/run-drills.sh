#!/bin/bash
# Run every recovery drill (AC-010). Exit 0 = all passed.
#
# Not a Makefile target: the Makefile is vendored from code-standards ("edit upstream and sync"),
# so a target added there would be silently clobbered on the next sync.
#
# The drills run one at a time, on purpose. Each binds a fixed local port and a scratch Postgres,
# and running them concurrently would have them fight over both -- turning a real failure into a
# port collision and an operator into a debugger of the harness rather than the system.

set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1

failed=()
for drill in scripts/drill-[0-9]*.sh; do
    echo
    echo "──────── $drill"
    if ! "./$drill" "$@"; then
        failed+=("$drill")
    fi
done

echo
if [ ${#failed[@]} -eq 0 ]; then
    echo "ALL DRILLS PASSED"
    exit 0
fi
echo "DRILLS FAILED: ${failed[*]}"
exit 1
