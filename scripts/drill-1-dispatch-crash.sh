#!/bin/bash
# WS-P2.1 AC-010, drill 1 of 4 -- CRASH.
#
# Scenario: the orchestrator process dies (SIGKILL, no graceful shutdown) immediately after it
# has recorded a dispatch. The claim it granted is now held by nobody.
#
# What this proves:
#   1. The crash leaves NO orphaned canonical state -- the dispatch is recorded exactly once, the
#      unit is still EXECUTING at the attempt it was on, and the claim is still open. Nothing is
#      half-written, because every write committed in one transaction or not at all.
#   2. A restarted orchestrator sees the same reality. Canonical state lives in Postgres; the
#      process holds none of it.
#   3. The unit is RECOVERABLE through a public surface: once the lease lapses, an operator
#      reclaims it to a next owner and work resumes. The crash costs one attempt, not the unit.
#
# No live workflow_dispatch is fired: `dispatch_enabled` defaults to False, so the dispatch is
# recorded as `skipped`/`dispatch_disabled` and the drill touches no GitHub, no shared system.
# That is asserted below, not assumed -- an outbound call from a drill would be the bug.

# shellcheck source=scripts/drill_common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/drill_common.sh"

parse_common_args "$@"
preflight
log "=== DRILL 1: orchestrator crash after dispatch ==="

start_scratch_postgres
write_auth_env
migrate_scratch
start_orchestrator

# A plain assignment, not `read <<<"$(seed_unit)"`: only the assignment form lets `set -e` see a
# failing command substitution, so a seed that dies cannot be read as a work-unit id.
seeded=$(seed_unit drill1)
read -r _revision unit <<<"$seeded"
log "[seed] work unit $unit is READY"

# --- the worker takes the work -------------------------------------------------------------
lease=$(api POST "/api/v1/work-units/$unit/claim" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" '{idempotency_key:"drill1-claim", expected_version:$v}')")
attempt=$(echo "$lease" | jq -r '.attempt')
lease_token=$(echo "$lease" | jq -r '.lease_token')
[ "$lease_token" != "null" ] || die "claim failed: $lease"
log "[claim] attempt $attempt, lease held by worker"

api POST "/api/v1/work-units/$unit/commands/start" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" --argjson a "$attempt" --arg t "$lease_token" \
        '{idempotency_key:"drill1-start", expected_version:$v, attempt:$a, lease_token:$t}')" >/dev/null
expect "state after start" "$(unit_state "$unit")" "executing"

# --- dispatch, then die --------------------------------------------------------------------
dispatch=$(api POST "/api/v1/work-units/$unit/dispatch" system \
    "$(jq -nc --argjson v "$(unit_version "$unit")" --argjson a "$attempt" \
        '{idempotency_key:"drill1-dispatch", expected_version:$v, runner_attempt:$a}')")
expect "dispatch status" "$(echo "$dispatch" | jq -r '.status')" "skipped"
expect "dispatch reason (no outbound call was made)" "$(echo "$dispatch" | jq -r '.reason_code')" "dispatch_disabled"

state_before="$(unit_state "$unit"):$(unit_version "$unit"):$(unit_attempts "$unit")"
log "[crash] SIGKILL to the orchestrator, mid-flight"
kill_orchestrator

# --- 1. the crash left nothing half-written ------------------------------------------------
expect "dispatch records after the crash" "$(count_sql dispatch_records)" "1"
expect "open claims after the crash" \
    "$(scratch_sql "SELECT count(*) FROM claims WHERE work_unit_id='$unit' AND released_at IS NULL")" "1"
expect "canonical state survived the crash unchanged" \
    "$(unit_state "$unit"):$(unit_version "$unit"):$(unit_attempts "$unit")" "$state_before"

# --- 2. a restarted orchestrator sees the same reality --------------------------------------
log "[restart] bringing a fresh orchestrator process up against the same database"
start_orchestrator
expect "state seen by the restarted process" "$(unit_state "$unit")" "executing"
expect "attempt seen by the restarted process" "$(unit_attempts "$unit")" "$attempt"

# --- 3. the unit is recoverable through a public surface ------------------------------------
# The worker that held this lease is gone. Nothing can recover the unit while the lease is live,
# and that is correct: a lease that has not lapsed may still have a worker behind it.
premature=$(api POST "/api/v1/work-units/$unit/reclaim-expired-claim" system \
    "$(jq -nc --argjson v "$(unit_version "$unit")" \
        '{idempotency_key:"drill1-premature", expected_version:$v, next_owner_id:"worker"}')")
expect "reclaiming a LIVE lease is refused" "$(api_error "$premature")" "lease_not_expired"

expire_lease "$unit"
log "[recover] lease has lapsed; operator reclaims to a next owner"
grant=$(api POST "/api/v1/work-units/$unit/reclaim-expired-claim" system \
    "$(jq -nc --argjson v "$(unit_version "$unit")" \
        '{idempotency_key:"drill1-reclaim", expected_version:$v, next_owner_id:"worker"}')")
new_attempt=$(echo "$grant" | jq -r '.attempt')
[ "$(echo "$grant" | jq -r '.lease_token')" != "null" ] || fail "reclaim did not grant a lease: $grant"
expect "reclaim opens the NEXT attempt" "$new_attempt" "$((attempt + 1))"
expect "the dead worker's claim is now released, with a reason" \
    "$(scratch_sql "SELECT count(*) FROM claims WHERE work_unit_id='$unit' AND released_at IS NOT NULL AND terminal_reason IS NOT NULL")" "1"
expect "exactly one live claim -- the new owner's" \
    "$(scratch_sql "SELECT count(*) FROM claims WHERE work_unit_id='$unit' AND released_at IS NULL")" "1"
expect "the crash cost one attempt, not the unit" "$(unit_attempts "$unit")" "$new_attempt"
expect "attempt budget is not exhausted" \
    "$(scratch_sql "SELECT (attempt_count < max_attempts) FROM work_units WHERE id='$unit'")" "t"

# The crash never reached GitHub, and no recovery action did either.
expect "no outbound dispatch was ever sent" \
    "$(scratch_sql "SELECT count(*) FROM dispatch_records WHERE status <> 'skipped'")" "0"

summarize "DRILL 1 (crash after dispatch)"
