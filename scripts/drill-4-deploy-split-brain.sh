#!/bin/bash
# WS-P2.1 AC-010, drill 4 of 4 -- DEPLOY SPLIT-BRAIN.
#
# Scenario: a release deployed successfully, and the post-deploy verification that was supposed
# to confirm it never finished. The deploy is live; the ledger still says "verifying". That gap
# is the split brain, and nobody is coming to tell us about it -- there is no observation to
# push, because the thing that happened is that NOTHING happened.
#
# This is the one approved deviation from on-ingest detection, and it is structural rather than a
# shortcut (ADR-0002, design 1.1). The post-deploy unit is minted SUBMITTED *inside* the
# deployment-ingest transaction -- zero seconds old -- so "verification stalled" is a claim about
# ELAPSED TIME that cannot be true at the instant of ingest under any design. It needs a pass
# that runs later and looks back.
#
# What this proves:
#   1. The detect-pass finds the stall and records a deploy_split_brain condition.
#   2. It is a REPORT. It creates no work unit, sets no lifecycle state, and never un-completes
#      the unit whose deploy it is complaining about. ADR-0002 chose a report-only runner exactly
#      so reconciliation could never become a second, competing writer of the lifecycle.
#   3. Re-running it does not pile up duplicate conditions -- the operator sees one alarm per
#      divergence, not one per pass.
#   4. Fail-open is COUNTED: the pass reports skipped_correlations, so a miss is visible rather
#      than silent.
#
# The stall threshold is set LOW before the process starts, rather than sleeping for the real 15
# minutes. Settings are read once behind an lru_cache, so this must be exported before uvicorn
# boots -- and it is environment setup, not a private back door: the pass itself is invoked
# through its public route, as an operator would invoke it.

# shellcheck source=scripts/drill_common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/drill_common.sh"

parse_common_args "$@"
preflight
log "=== DRILL 4: a deploy that landed while its verification never finished ==="

DIGEST="sha256:$(printf 'a%.0s' $(seq 1 64))"
COMMIT="1111111111111111111111111111111111111111"
MERGE_COMMIT="2222222222222222222222222222222222222222"

start_scratch_postgres
write_auth_env

# Any post-deploy unit older than one second is stalled. Exported BEFORE the server starts,
# because Settings is cached for the life of the process.
export ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS=1

migrate_scratch
start_orchestrator

seeded=$(seed_unit drill4)
read -r revision unit <<<"$seeded"

# --- carry the unit all the way to COMPLETED, through the public lifecycle --------------------
# A release artifact may only be bound to a completed unit, so the drill has to earn that state
# rather than assert it: claim, execute, evidence, submit, verify, adjudicate, review, complete.
lease=$(api POST "/api/v1/work-units/$unit/claim" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" '{idempotency_key:"drill4-claim", expected_version:$v}')")
attempt=$(echo "$lease" | jq -r '.attempt')
token=$(echo "$lease" | jq -r '.lease_token')
[ "$token" != "null" ] || die "claim failed: $lease"

api POST "/api/v1/work-units/$unit/commands/start" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" --argjson a "$attempt" --arg t "$token" \
        '{idempotency_key:"drill4-start", expected_version:$v, attempt:$a, lease_token:$t}')" >/dev/null

evidence=$(api POST "/api/v1/work-units/$unit/evidence" worker \
    "$(jq -nc --arg r "$revision" --argjson a "$attempt" --arg t "$token" --argjson v "$(unit_version "$unit")" '{
        idempotency_key:"drill4-evidence", expected_version:$v,
        work_package_revision_id:$r, ac_id:"ac-1", attempt:$a, lease_token:$t,
        evidence_type:"test", stable_ref:"artifact://drill4/tests", source_revision:"abc123"}')")
evidence_id=$(echo "$evidence" | jq -r '.id // empty')
[ -n "$evidence_id" ] || die "evidence failed: $evidence"

# A PR-capable unit must record a binding for THIS attempt before it may submit (WS-P2.16 U3).
api POST "/api/v1/work-units/$unit/pr-binding" worker \
    "$(jq -nc --argjson a "$attempt" --arg t "$token" \
        '{idempotency_key:"drill4-binding", expected_version:0, pr_number:400,
          head_sha:"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", attempt:$a, lease_token:$t}')" >/dev/null

api POST "/api/v1/work-units/$unit/commands/submit" worker \
    "$(jq -nc --argjson v "$(unit_version "$unit")" --argjson a "$attempt" --arg t "$token" \
        '{idempotency_key:"drill4-submit", expected_version:$v, attempt:$a, lease_token:$t}')" >/dev/null

api POST "/api/v1/work-units/$unit/commands/verify" verifier \
    "$(jq -nc --argjson v "$(unit_version "$unit")" '{idempotency_key:"drill4-verify", expected_version:$v}')" >/dev/null

# A HUMAN adjudicates, because a verifier may only record what it evaluated (WS-P2.32). The
# criterion `ac-1` describes is declared at registration -- see `seed_unit` -- which is what makes
# it decidable by anyone at all.
adjudication=$(api POST "/api/v1/work-units/$unit/adjudications" human \
    "$(jq -nc --arg r "$revision" --arg e "$evidence_id" --argjson v "$(unit_version "$unit")" '{
        idempotency_key:"drill4-adjudicate", expected_version:$v,
        work_package_revision_id:$r, ac_id:"ac-1", outcome:"passed",
        rationale:"drill: evidence accepted", evidence_id:$e}')")
echo "$adjudication" | jq -e '.id' >/dev/null 2>&1 || die "adjudication failed: $adjudication"

api POST "/api/v1/work-units/$unit/commands/review" verifier \
    "$(jq -nc --argjson v "$(unit_version "$unit")" '{idempotency_key:"drill4-review", expected_version:$v}')" >/dev/null

api POST "/api/v1/work-units/$unit/commands/complete" human \
    "$(jq -nc --argjson v "$(unit_version "$unit")" '{idempotency_key:"drill4-complete", expected_version:$v}')" >/dev/null
expect "the unit completed through the public lifecycle" "$(unit_state "$unit")" "completed"

# --- the release ships, and a deploy is observed ----------------------------------------------
binding=$(api POST "/api/v1/work-units/$unit/release-artifacts" system \
    "$(jq -nc --arg r "$revision" --arg d "$DIGEST" --arg c "$COMMIT" --arg m "$MERGE_COMMIT" \
        --argjson v "$(unit_version "$unit")" '{
        idempotency_key:"drill4-binding", expected_version:$v,
        package_revision_id:$r, package_revision_hash:"sha256:drill4",
        source_repository:"AlobarQuest/orchestrator", implementation_pr_number:20,
        source_commit:$c, merge_commit:$m,
        artifact_registry:"ghcr.io", artifact_repository:"alobarquest/orchestrator",
        artifact_name:"orchestrator", artifact_digest:$d, artifact_tag:"drill4",
        workflow_run_id:"123456789"}')")
binding_id=$(echo "$binding" | jq -r '.id // empty')
[ -n "$binding_id" ] || die "release binding failed: $binding"

deploy=$(api POST "/api/v1/release-artifacts/$binding_id/deployment-observations" system \
    "$(jq -nc --arg d "$DIGEST" '{
        idempotency_key:"drill4-deploy", expected_version:0,
        environment:"production", base_url:"https://sds.example.invalid",
        observed_artifact_digest:$d, deployment_ref:"coolify:drill4", deployer:"coolify",
        deployment_url:"https://coolify.example.invalid/project/orchestrator/drill4",
        observed_at:"2026-07-11T20:00:00+00:00",
        probe_summary:{probes:[{name:"live", method:"GET", endpoint:"/health/live",
            expected_status_min:200, expected_status_max:299, status_code:200,
            observed_at:"2026-07-11T20:00:00+00:00"}]},
        route_summary:{routes:[{path:"/api/v1/observations", present:true}]},
        auth_summary:{missing_m2m_status:401, configured_m2m_status:200},
        dispatch_summary:{dispatch_enabled:false},
        status_summary:{status:"observed", summary:"bounded"}}')")
echo "$deploy" | jq -e '.id' >/dev/null 2>&1 || die "deployment observation failed: $deploy"

post_deploy_unit=$(scratch_sql "SELECT post_deploy_work_unit_id FROM deployment_observations LIMIT 1")
expect "the deploy minted a post-deploy verification unit" \
    "$(scratch_sql "SELECT state FROM work_units WHERE id='$post_deploy_unit'")" "submitted"

# --- the verification never finishes -----------------------------------------------------------
# It is SUBMITTED and zero seconds old. Nothing is coming to say so; the event is a NON-event.
sleep 2

# --- the detect-pass, invoked through its public route as an operator would --------------------
detected=$(api POST /api/v1/reconciliation/detect system \
    '{"idempotency_key":"drill4-detect","expected_version":0}')
expect "the pass recorded the split brain" "$(echo "$detected" | jq -r '.conditions_recorded')" "1"
expect "and counted no skipped correlation -- fail-open is visible, not silent" \
    "$(echo "$detected" | jq -r '.skipped_correlations')" "0"
expect "the condition is a deploy_split_brain" \
    "$(scratch_sql "SELECT condition_type FROM reconciliation_conditions")" "deploy_split_brain"
expect "raised against the stalled post-deploy unit" \
    "$(scratch_sql "SELECT work_unit_id FROM reconciliation_conditions")" "$post_deploy_unit"

# --- it is a REPORT, not a writer ---------------------------------------------------------------
expect "the pass created no work unit" "$(count_sql work_units)" "2"
expect "the stalled unit was not transitioned" \
    "$(scratch_sql "SELECT state FROM work_units WHERE id='$post_deploy_unit'")" "submitted"
expect "the deployed unit was NOT un-completed" "$(unit_state "$unit")" "completed"
expect "the pass made no outbound call" "$(count_sql dispatch_records)" "0"
expect "and resolved nothing on its own" "$(count_sql reconciliation_resolutions)" "0"

# --- a second pass must not re-alarm ------------------------------------------------------------
again=$(api POST /api/v1/reconciliation/detect system \
    '{"idempotency_key":"drill4-detect-2","expected_version":0}')
expect "a second pass records no duplicate condition" "$(echo "$again" | jq -r '.conditions_recorded')" "0"
expect "it reports the duplicate it suppressed" "$(echo "$again" | jq -r '.suppressed_duplicates')" "1"
expect "the operator still sees exactly one alarm" "$(count_sql reconciliation_conditions)" "1"

summarize "DRILL 4 (deploy split-brain)"
