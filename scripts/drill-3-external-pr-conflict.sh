#!/bin/bash
# WS-P2.1 AC-010, drill 3 of 4 -- SOMEONE ELSE TOUCHED THE PULL REQUEST.
#
# Two conflicts arrive on the same ingest path, and this drill exercises BOTH, because they are
# not the same test:
#
#   * external_merge_alarm  -- the PR was merged outside the session before the unit completed.
#     This rule never consults the armed head at all. A drill that stopped here would go green
#     with the entire arming half of the feature dead.
#   * pr_state_divergence   -- the head moved AFTER the worker handed it over for adjudication.
#     This is the rule the armed head exists for, and only this one can vouch for it.
#
# It also proves the two ways the alarm must STAY SILENT, which matter more than the alarms: a
# detector that cries wolf on normal iteration gets ignored, and an ignored detector is worse
# than none. A rebase before submit is normal work and must not fire.
#
# Everything here goes through PUBLIC surfaces -- the worker reports its PR through the route a
# real worker uses, and the head is armed as a side effect of a real submit. Seeding the binding
# with SQL or a service call would let this drill pass over a production path that never runs,
# which is exactly the defect that made these alarms dead code in the first place.

# shellcheck source=scripts/drill_common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/drill_common.sh"

parse_common_args "$@"
preflight
log "=== DRILL 3: external merge, and a head that moved after it was armed ==="

start_scratch_postgres
write_auth_env
migrate_scratch
start_orchestrator

PR=4242
HEAD_A="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
HEAD_B="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
HEAD_C="cccccccccccccccccccccccccccccccccccccccc"

conditions_of_type() {
    scratch_sql "SELECT count(*) FROM reconciliation_conditions WHERE condition_type='$1'"
}
condition_count() { count_sql reconciliation_conditions; }

# Push a github_pr observation, as the reconciliation runner does: SYSTEM actor, /observations.
observe_pr() {
    local key="$1" head="$2" state="$3" merged="$4" observed_at="$5"
    api POST /api/v1/observations system "$(jq -nc \
        --arg k "$key" --arg u "$unit" --arg h "$head" --arg s "$state" \
        --argjson m "$merged" --argjson n "$PR" --arg t "$observed_at" '{
        idempotency_key:$k, expected_version:0,
        source_system:"github", source_reference:("github_pr:"+$k), trust_classification:"delivery_system",
        subject_type:"work_unit", subject_reference:$u,
        observation_type:"github_pr", status:"observed", severity:"info",
        observed_at:$t, summary:"pull request observed",
        facts:{pr_number:$n, head_sha:$h, state:$s, merged:$m}}')" >/dev/null
}

seeded=$(seed_unit drill3)
read -r _revision unit <<<"$seeded"

lease=$(api POST "/api/v1/work-units/$unit/claim" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" '{idempotency_key:"drill3-claim", expected_version:$v}')")
attempt=$(echo "$lease" | jq -r '.attempt')
token=$(echo "$lease" | jq -r '.lease_token')
[ "$token" != "null" ] || die "claim failed: $lease"
api POST "/api/v1/work-units/$unit/commands/start" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" --argjson a "$attempt" --arg t "$token" \
        '{idempotency_key:"drill3-start", expected_version:$v, attempt:$a, lease_token:$t}')" >/dev/null

# --- the worker opens a PR, through the route a real worker uses -----------------------------
binding=$(api POST "/api/v1/work-units/$unit/pr-binding" worker \
    "$(jq -nc --argjson n "$PR" --arg h "$HEAD_A" --argjson a "$attempt" --arg t "$token" \
        '{idempotency_key:"drill3-binding-1", expected_version:0, pr_number:$n, head_sha:$h, attempt:$a, lease_token:$t}')")
expect "the worker reported its PR head" "$(echo "$binding" | jq -r '.head_sha')" "$HEAD_A"
expect "reporting a head does NOT arm the alarm" \
    "$(echo "$binding" | jq -r '.verification_read_head_sha')" "null"

# --- SILENCE 1: iterating before submit is normal work ---------------------------------------
observe_pr drill3-obs-1 "$HEAD_A" open false "2026-07-11T12:00:00+00:00"
expect "an observation matching the reported head raises nothing" "$(condition_count)" "0"

rebased=$(api POST "/api/v1/work-units/$unit/pr-binding" worker \
    "$(jq -nc --argjson n "$PR" --arg h "$HEAD_B" --argjson a "$attempt" --arg t "$token" \
        '{idempotency_key:"drill3-binding-2", expected_version:0, pr_number:$n, head_sha:$h, attempt:$a, lease_token:$t}')")
expect "a rebase moves the reported head" "$(echo "$rebased" | jq -r '.head_sha')" "$HEAD_B"
observe_pr drill3-obs-2 "$HEAD_B" open false "2026-07-11T12:05:00+00:00"
expect "a rebase BEFORE submit raises nothing -- it is normal iteration" "$(condition_count)" "0"

# --- the worker submits: THIS is what arms the alarm ------------------------------------------
api POST "/api/v1/work-units/$unit/commands/submit" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" --argjson a "$attempt" --arg t "$token" \
        '{idempotency_key:"drill3-submit", expected_version:$v, attempt:$a, lease_token:$t}')" >/dev/null
expect "the unit is submitted" "$(unit_state "$unit")" "submitted"

# Read the armed head back through the runner's OWN view -- the surface that decides what the
# runner polls. If arming were dead, this is where the silence would show.
armed=$(api GET /api/v1/in-flight-units system | jq -r --arg u "$unit" '.units[] | select(.work_unit_id==$u) | .verification_read_head_sha')
expect "SUBMIT armed the alarm on the head it handed over" "$armed" "$HEAD_B"

# --- ALARM 1: the head moved after it was armed ------------------------------------------------
observe_pr drill3-obs-3 "$HEAD_C" open false "2026-07-11T12:10:00+00:00"
expect "a head change AFTER arming raises pr_state_divergence" "$(conditions_of_type pr_state_divergence)" "1"
expect "the condition records the head that was actually observed" \
    "$(scratch_sql "SELECT observed_state->>'head_sha' FROM reconciliation_conditions WHERE condition_type='pr_state_divergence'")" "$HEAD_C"
expect "and what the orchestrator expected instead" \
    "$(scratch_sql "SELECT stored_state->>'verification_read_head_sha' FROM reconciliation_conditions WHERE condition_type='pr_state_divergence'")" "$HEAD_B"

# --- ALARM 2: merged behind our back -----------------------------------------------------------
observe_pr drill3-obs-4 "$HEAD_C" closed true "2026-07-11T12:15:00+00:00"
expect "a merge outside the session raises external_merge_alarm" "$(conditions_of_type external_merge_alarm)" "1"
expect "the alarm names the pull request" \
    "$(scratch_sql "SELECT observed_state->>'pr_number' FROM reconciliation_conditions WHERE condition_type='external_merge_alarm'")" "$PR"

# --- what detection must NEVER do ---------------------------------------------------------------
expect "the merged PR did NOT complete the unit" "$(unit_state "$unit")" "submitted"
expect "detection wrote no adjudication" \
    "$(scratch_sql "SELECT count(*) FROM adjudications WHERE work_unit_id='$unit'")" "0"
expect "detection made no outbound call" "$(count_sql dispatch_records)" "0"
expect "every condition is still OPEN -- detection never auto-resolves" \
    "$(count_sql reconciliation_resolutions)" "0"

# --- the operator's view ------------------------------------------------------------------------
# The conditions are worthless if the human who must act on them cannot see them.
open_conditions=$(scratch_sql "SELECT count(*) FROM reconciliation_conditions c
    WHERE NOT EXISTS (SELECT 1 FROM reconciliation_resolutions r WHERE r.condition_id = c.id)")
expect "both conditions are open and awaiting a human" "$open_conditions" "2"

summarize "DRILL 3 (external merge + post-arming head divergence)"
