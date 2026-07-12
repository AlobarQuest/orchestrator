#!/bin/bash
# WS-P2.1 AC-010, drill 2 of 4 -- LEASE LAPSE BEFORE SUBMIT.
#
# Scenario: a worker does real work, records evidence, and then its 15-minute lease lapses before
# it can finish. The claim is expired but NOT released -- nothing has reclaimed it. The work is
# done and sitting there; the worker has no standing to submit it.
#
# What this proves:
#   1. The expired worker is locked out. Its lease token no longer buys anything.
#   2. An operator can ATTACH that evidence without redoing the work -- and the attachment
#      SUPERSEDES the current evidence head rather than forking it. This is the drill's real
#      subject. A forked chain (two unsuperseded heads for one AC) makes `_terminal` raise: the
#      AC can never be adjudicated, no further evidence can be written, and `evidence` is
#      append-only -- so the row can never be repaired and the unit can NEVER complete. The
#      recovery path is the one place that could wedge a unit permanently, so the drill checks
#      the head count, not merely that a row appeared.
#   3. Recovery releases the dead claim and SYSTEM-fails the unit WITHOUT minting a new attempt.
#      Attaching evidence is not completing work.
#   4. A worker still cannot complete the unit. Recovery hands nobody a shortcut past the gates.
#   5. The unit goes on to succeed: requeue, a fresh attempt, submit -- WITHOUT re-running the
#      job, because the evidence is already attached. That is what AC-004 actually promises.

# shellcheck source=scripts/drill_common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/drill_common.sh"

parse_common_args "$@"
preflight
log "=== DRILL 2: lease lapses before submit; evidence is recovered, not redone ==="

start_scratch_postgres
write_auth_env
migrate_scratch
start_orchestrator

seeded=$(seed_unit drill2)
read -r revision unit <<<"$seeded"
log "[seed] work unit $unit is READY"

# --- the worker takes the work and does it --------------------------------------------------
lease=$(api POST "/api/v1/work-units/$unit/claim" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" '{idempotency_key:"drill2-claim", expected_version:$v}')")
attempt=$(echo "$lease" | jq -r '.attempt')
lease_token=$(echo "$lease" | jq -r '.lease_token')
[ "$lease_token" != "null" ] || die "claim failed: $lease"

api POST "/api/v1/work-units/$unit/commands/start" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" --argjson a "$attempt" --arg t "$lease_token" \
        '{idempotency_key:"drill2-start", expected_version:$v, attempt:$a, lease_token:$t}')" >/dev/null

first=$(api POST "/api/v1/work-units/$unit/evidence" worker \
    "$(jq -nc --arg r "$revision" --argjson a "$attempt" --arg t "$lease_token" --argjson v "$(unit_version "$unit")" '{
        idempotency_key:"drill2-evidence-1", expected_version:$v,
        work_package_revision_id:$r, ac_id:"ac-1", attempt:$a, lease_token:$t,
        evidence_type:"test", stable_ref:"artifact://drill2/run-1", source_revision:"abc123"}')")
first_id=$(echo "$first" | jq -r '.id // empty')
[ -n "$first_id" ] || die "worker could not record evidence: $first"
log "[work] attempt $attempt recorded evidence $first_id for ac-1"

# --- the lease lapses ------------------------------------------------------------------------
expire_lease "$unit"
log "[lapse] the 15-minute lease has expired; the claim is NOT released"

# 1. The expired worker is locked out.
locked_out=$(api POST "/api/v1/work-units/$unit/evidence" worker \
    "$(jq -nc --arg r "$revision" --argjson a "$attempt" --arg t "$lease_token" --argjson v "$(unit_version "$unit")" '{
        idempotency_key:"drill2-evidence-late", expected_version:$v,
        work_package_revision_id:$r, ac_id:"ac-1", attempt:$a, lease_token:$t,
        evidence_type:"test", stable_ref:"artifact://drill2/too-late", source_revision:"abc123"}')")
expect "an expired lease buys nothing" "$(api_error "$locked_out")" "claim_not_active"

# 2. The operator attaches the work the worker already did.
#
# The body is built ONCE and re-sent byte-identical below, because that is what a duplicate
# delivery actually is. Rebuilding it would re-read the unit version -- which recovery itself
# changes -- and the resend would then be a DIFFERENT command under the same key, which the
# replay-equality check correctly rejects. That is the contract, not a duplicate.
recover_body=$(jq -nc --arg r "$revision" --argjson v "$(unit_version "$unit")" '{
    idempotency_key:"drill2-recover", expected_version:$v,
    work_package_revision_id:$r, ac_id:"ac-1",
    evidence_type:"test", stable_ref:"artifact://drill2/run-1", source_revision:"abc123"}')
recovered=$(api POST "/api/v1/work-units/$unit/attempts/$attempt/recover-evidence" system "$recover_body")
recovered_id=$(echo "$recovered" | jq -r '.id // empty')
[ -n "$recovered_id" ] || die "recovery failed: $recovered"
log "[recover] operator attached evidence $recovered_id"

# THE assertion. Not "a row exists" -- exactly one unsuperseded head, and it supersedes the row
# the worker wrote. A second head here would wedge the unit beyond repair.
expect "the recovered evidence SUPERSEDES the worker's head" \
    "$(scratch_sql "SELECT supersedes_evidence_id FROM evidence WHERE id='$recovered_id'")" "$first_id"
expect "exactly ONE unsuperseded head for ac-1 -- the chain did not fork" \
    "$(scratch_sql "SELECT count(*) FROM evidence e WHERE e.work_unit_id='$unit' AND e.ac_id='ac-1'
                    AND NOT EXISTS (SELECT 1 FROM evidence s WHERE s.supersedes_evidence_id = e.id)")" "1"
expect "the worker's evidence was preserved, not overwritten" \
    "$(scratch_sql "SELECT count(*) FROM evidence WHERE work_unit_id='$unit' AND ac_id='ac-1'")" "2"

# A duplicate delivery -- the same body, the same key -- must REPLAY, not write a second head.
replay=$(api POST "/api/v1/work-units/$unit/attempts/$attempt/recover-evidence" system "$recover_body")
expect "a duplicate recovery replays the same row" "$(echo "$replay" | jq -r '.id')" "$recovered_id"
expect "and writes no second head" \
    "$(scratch_sql "SELECT count(*) FROM evidence e WHERE e.work_unit_id='$unit' AND e.ac_id='ac-1'
                    AND NOT EXISTS (SELECT 1 FROM evidence s WHERE s.supersedes_evidence_id = e.id)")" "1"

# The other half of the idempotency contract: the same key carrying a DIFFERENT command is not a
# duplicate, and must be refused rather than silently replayed as if it were.
impostor=$(api POST "/api/v1/work-units/$unit/attempts/$attempt/recover-evidence" system \
    "$(echo "$recover_body" | jq -c '.stable_ref = "artifact://drill2/something-else"')")
[ "$(api_error "$impostor")" != "none" ] || fail "a different command reused the same key and was accepted"
log "  ok: the same key with a different body is refused ($(api_error "$impostor"))"

# 3. Recovery released the dead claim and failed the unit -- without minting a new attempt.
expect "the dead claim was released as lease_expired" \
    "$(scratch_sql "SELECT terminal_reason FROM claims WHERE work_unit_id='$unit' AND attempt=$attempt")" "lease_expired"
expect "the unit was SYSTEM-failed" "$(unit_state "$unit")" "failed"
expect "no new attempt was minted -- attaching evidence is not doing work" \
    "$(unit_attempts "$unit")" "$attempt"

# 4. Recovery is not a shortcut past the gates.
forbidden=$(api POST "/api/v1/work-units/$unit/commands/complete" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" '{idempotency_key:"drill2-worker-complete", expected_version:$v}')")
[ "$(api_error "$forbidden")" != "none" ] || fail "a worker was allowed to complete the unit"
log "  ok: a worker cannot complete the unit ($(api_error "$forbidden"))"

# 5. The unit goes on to succeed -- without re-running the job.
api POST "/api/v1/work-units/$unit/requeue" system \
    "$(jq -nc --argjson v "$(unit_version "$unit")" \
        '{idempotency_key:"drill2-requeue", expected_version:$v, reason:"lease lapsed; evidence recovered"}')" >/dev/null
expect "requeue returns the unit to READY" "$(unit_state "$unit")" "ready"

lease2=$(api POST "/api/v1/work-units/$unit/claim" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" '{idempotency_key:"drill2-claim-2", expected_version:$v}')")
attempt2=$(echo "$lease2" | jq -r '.attempt')
token2=$(echo "$lease2" | jq -r '.lease_token')
expect "a fresh attempt is granted" "$attempt2" "$((attempt + 1))"

api POST "/api/v1/work-units/$unit/commands/start" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" --argjson a "$attempt2" --arg t "$token2" \
        '{idempotency_key:"drill2-start-2", expected_version:$v, attempt:$a, lease_token:$t}')" >/dev/null

# The new attempt submits WITHOUT recording evidence again: the recovered evidence still stands.
api POST "/api/v1/work-units/$unit/commands/submit" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" --argjson a "$attempt2" --arg t "$token2" \
        '{idempotency_key:"drill2-submit", expected_version:$v, attempt:$a, lease_token:$t}')" >/dev/null
expect "the new attempt submits WITHOUT redoing the work" "$(unit_state "$unit")" "submitted"
expect "and the evidence it submits on is the recovered evidence" \
    "$(scratch_sql "SELECT e.id FROM evidence e WHERE e.work_unit_id='$unit' AND e.ac_id='ac-1'
                    AND NOT EXISTS (SELECT 1 FROM evidence s WHERE s.supersedes_evidence_id = e.id)")" "$recovered_id"

summarize "DRILL 2 (lease lapse; evidence recovered, not redone)"
