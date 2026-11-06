#!/bin/sh
# The lane image's entrypoint (ADR-0059 decision 1). Its one argument is the lane, which the lane's
# compose names as `command:`. It reads LANE_SCHEDULE_ENABLED once, at start:
#   exactly `true`  runs the lane's tracked crontab, /app/lanes/<lane>/crontab;
#   anything else   runs a comment-only crontab, so the container stays up and runs no launcher,
#                   and can still be entered for a one-off `lane-run`.
# Either file is checked with `supercronic -test` first, then supercronic replaces this shell as
# PID 1, under USER orchestrator and TZ from the image.
set -eu

lane="${1:-}"
case "$lane" in
  '' | *[!a-z0-9-]*)
    echo '{"level":"error","msg":"usage: entrypoint.sh <lane>"}'
    exit 64
    ;;
esac
tracked="/app/lanes/$lane/crontab"
if [ ! -r "$tracked" ]; then
  echo "{\"level\":\"error\",\"lane\":\"$lane\",\"msg\":\"no tracked crontab at $tracked\"}"
  exit 64
fi

if [ "${LANE_SCHEDULE_ENABLED:-}" = "true" ]; then
  crontab="$tracked"
  scheduled=true
else
  crontab="$(mktemp)"
  echo "# $lane is unscheduled: LANE_SCHEDULE_ENABLED is not true." > "$crontab"
  scheduled=false
fi

supercronic -json -no-reap -test "$crontab"
echo "{\"level\":\"info\",\"lane\":\"$lane\",\"scheduled\":$scheduled,\"crontab\":\"$crontab\"}"
exec supercronic -json "$crontab"
