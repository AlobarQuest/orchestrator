#!/bin/bash
# WS-P2.15 -- STALLED APPROVAL GATE.
#
# Scenario: a work unit reaches a human approval gate, and the human never answers. Not
# "answers late" -- never. What does the system do?
#
# Before WS-P2.15 the answer was: nothing, and nobody is told. `age_out_human_gates` existed to
# report exactly this, was fully unit-tested, and had ZERO production callers -- disabled by
# default, wired to nothing. A fully-implemented guard that reported nothing for an entire
# workstream. That is the defect class this whole workstream exists to eliminate, and it is why
# this drill exists: a test that calls a service is not evidence the service has a caller.
# Only driving the real HTTP surface proves the report is reachable.
#
# What this proves, over the public API and nothing else:
#   1. The gate is REPORTED. An unanswered approval gate past the threshold appears in the
#      dead-letter view, with NO query parameter of any kind -- the threshold has no off switch.
#   2. It is NOT REQUEUE-ELIGIBLE. It needs a human DECISION, not a retry. Offering requeue
#      would be offering the wrong affordance.
#   3. SILENCE IS NEVER APPROVAL. Reading the report transitions nothing: the unit is in exactly
#      the state, and at exactly the version, it was before. Time cannot answer a human gate.
#
# The threshold is set to 0 via the env var so the drill needs no sleep -- the same trick
# drill 4 uses for the split-brain stall clock. The DEFAULT (7 days, and non-nullable) is
# asserted in tests/services/test_stalled_approvals.py; here we exercise the reporting path.

# shellcheck source=scripts/drill_common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/drill_common.sh"

parse_common_args "$@"
preflight
log "=== DRILL 5: a human approval gate nobody answers ==="

start_scratch_postgres
write_auth_env
migrate_scratch

# Everything is already overdue. Nothing is aged, and nothing sleeps: a database trigger
# (set_work_unit_updated_at) rewrites work_units.updated_at on EVERY update, so a row cannot be
# back-dated even by raw SQL. Shrinking the window is the only honest way to make a fresh unit
# overdue -- and it is the same knob production would turn.
export ORCHESTRATOR_DEAD_LETTER_STALLED_APPROVAL_SECONDS=0

start_orchestrator

seeded=$(seed_unit drill5)
read -r _revision unit <<<"$seeded"
log "[seed] work unit $unit is READY"

# Drive it to the approval gate through the public API. claim -> start -> request-approval.
lease=$(api POST "/api/v1/work-units/$unit/claim" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" \
        '{idempotency_key:"drill5-claim", expected_version:$v}')")
attempt=$(echo "$lease" | jq -r '.attempt')
lease_token=$(echo "$lease" | jq -r '.lease_token')
[ "$lease_token" != "null" ] || die "claim failed: $lease"

api POST "/api/v1/work-units/$unit/commands/start" worker \
    "$(jq -nc --arg k "drill5-start" --arg t "$lease_token" --argjson a "$attempt" \
        --argjson v "$(unit_version "$unit")" \
        '{idempotency_key:$k, expected_version:$v, attempt:$a, lease_token:$t}')" >/dev/null

gate=$(api POST "/api/v1/work-units/$unit/commands/request-approval" worker \
    "$(jq -nc --arg k "drill5-gate" --arg t "$lease_token" --argjson a "$attempt" \
        --argjson v "$(unit_version "$unit")" \
        '{idempotency_key:$k, expected_version:$v, attempt:$a, lease_token:$t}')")
echo "$gate" | jq -e '.state == "awaiting_approval"' >/dev/null \
    || die "unit did not reach the approval gate: $gate"

state_before=$(unit_state "$unit")
version_before=$(unit_version "$unit")
log "unit is at the gate: state=$state_before version=$version_before -- and nobody will answer"

# ---------------------------------------------------------------------------------------------
# 1. The gate is REPORTED -- with no query parameter at all.
# ---------------------------------------------------------------------------------------------
report=$(api GET "/api/v1/dead-letter" human)
entry=$(echo "$report" | jq -c --arg u "$unit" '.[] | select(.work_unit_id == $u and .source == "stalled_approval")')

if [ -z "$entry" ]; then
    fail "the unanswered approval gate is NOT reported in the dead-letter view. A gate nobody \
answers and nobody is told about is the exact failure this drill exists for. Report: $report"
else
    log "reported: $entry"
    expect "the stalled gate carries a named reason" \
        "$(echo "$entry" | jq -r '.reason_code')" "approval_unanswered"
    expect "the report names the gate the unit is stuck at" \
        "$(echo "$entry" | jq -r '.unit_state')" "awaiting_approval"

    # ---------------------------------------------------------------------------------------
    # 2. It is NOT requeue-eligible. A stalled gate needs a DECISION, not a retry.
    # ---------------------------------------------------------------------------------------
    expect "a stalled approval gate is reported but not requeue-eligible" \
        "$(echo "$entry" | jq -r '.requeue_eligible')" "false"
fi

# ---------------------------------------------------------------------------------------------
# 3. SILENCE IS NEVER APPROVAL. Reading the report changed nothing.
#
# Asserted against the database, not against the response body -- the WS-P2.1 defect was a
# writer whose in-session object looked correct while the row was discarded. If the report ever
# became a write, this is what would catch it.
# ---------------------------------------------------------------------------------------------
expect "reporting a stalled gate does not transition the unit" \
    "$(unit_state "$unit")" "$state_before"
expect "reporting a stalled gate performs no write at all -- the version is unchanged" \
    "$(unit_version "$unit")" "$version_before"

# And it is still reported on a second read: the report is derived, not consumed.
again=$(api GET "/api/v1/dead-letter" human)
echo "$again" | jq -e --arg u "$unit" \
    '[.[] | select(.work_unit_id == $u and .source == "stalled_approval")] | length == 1' >/dev/null \
    || fail "the stalled gate vanished on a second read -- the report must be derived, not consumed"

summarize "DRILL 5"
