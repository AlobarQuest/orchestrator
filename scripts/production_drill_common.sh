#!/bin/bash
# Shared helpers for the live production recovery-drill runner.

set -euo pipefail

api_base_url="https://sds.alobar.net"
redacted_authorization="Authorization: Bearer <redacted>"
required_openapi_operations=(
    "GET /health/ready"
    "GET /api/v1/production-drills/{run_id}/state"
    "POST /api/v1/production-drills/{run_id}/scenarios/{scenario}"
    "POST /api/v1/production-drills/{run_id}/fail"
)
scenarios=(
    "crash_recovery"
    "evidence_recovery"
    "external_pr_conflict"
    "deploy_split_brain"
    "stalled_approval"
)

log() {
    printf '[production-drill] %s\n' "$1" >&2
}

redact() {
    sed -E "s/(authorization:[[:space:]]*bearer)[[:space:]]+[^[:space:]]+/$(printf '%s' "$redacted_authorization" | sed 's/[&/]/\\\\&/g')/Ig"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        log "required command is unavailable: $1"
        return 1
    }
}

load_drill_credential() {
    local secret_uuid="${ORCHESTRATOR_PRODUCTION_DRILL_SECRET_UUID:?ORCHESTRATOR_PRODUCTION_DRILL_SECRET_UUID is required}"
    local secret_json

    require_command bws
    require_command jq
    # The BWS client reads its own authentication from the process environment; no bearer is argv.
    secret_json="$(bws secret get "$secret_uuid" --output json)"
    DRILL_TOKEN="$(jq -er '.value | fromjson | .ORCHESTRATOR_PRODUCTION_DRILL_TOKEN' <<<"$secret_json")"
    DRILL_CREDENTIAL_KEY_ID="$(jq -er '.value | fromjson | .credential_key_id' <<<"$secret_json")"
    unset secret_json
}

api_request() {
    local method="$1"
    local path="$2"
    local payload="${3:-}"
    local config
    local payload_file=""
    local status

    config="$(mktemp)"
    chmod 600 "$config"
    {
        printf 'url = "%s%s"\n' "$api_base_url" "$path"
        printf 'request = "%s"\n' "$method"
        printf 'fail-with-body\n'
        printf 'silent\n'
        printf 'show-error\n'
        if [ -n "${DRILL_TOKEN:-}" ]; then
            printf 'header = "Authorization: Bearer %s"\n' "$DRILL_TOKEN"
            printf 'header = "X-Credential-Key-Id: %s"\n' "$DRILL_CREDENTIAL_KEY_ID"
        fi
        if [ -n "$payload" ]; then
            payload_file="$(mktemp)"
            chmod 600 "$payload_file"
            printf '%s' "$payload" >"$payload_file"
            printf 'header = "Content-Type: application/json"\n'
            printf 'data-binary = "@%s"\n' "$payload_file"
        fi
    } >"$config"

    curl --config "$config" 2> >(redact >&2)
    status=$?
    rm -f "$config" "$payload_file"
    return "$status"
}

preflight_openapi() {
    local openapi
    local operation
    local method
    local path

    openapi="$(api_request GET /openapi.json)"
    for operation in "${required_openapi_operations[@]}"; do
        method="${operation%% *}"
        path="${operation#* }"
        jq -e --arg path "$path" --arg method "$method" \
            '.paths[$path][$method | ascii_downcase] != null' <<<"$openapi" >/dev/null || {
            log "production OpenAPI preflight missing required operation: $method $path"
            return 1
        }
    done
}

preflight_readiness() {
    api_request GET /health/ready >/dev/null
}

write_evidence() {
    local status="$1"
    local detail="$2"
    local escaped_detail

    escaped_detail="$(printf '%s' "$detail" | redact)"
    jq -n \
        --arg status "$status" \
        --arg run_id "$RUN_ID" \
        --arg idempotency_prefix "$IDEMPOTENCY_PREFIX" \
        --arg detail "$escaped_detail" \
        --argjson assertions "$ASSERTIONS_JSON" \
        --argjson final_state "$FINAL_STATE_JSON" \
        '{status: $status, run_id: $run_id, idempotency_prefix: $idempotency_prefix, detail: $detail, assertions: $assertions, final_state: $final_state}' \
        >"$EVIDENCE_FILE"
}

record_assertion() {
    local name="$1"
    local status="$2"
    ASSERTIONS_JSON="$(jq -c --arg name "$name" --arg status "$status" \
        '. + [{name: $name, status: $status}]' <<<"$ASSERTIONS_JSON")"
}

assert_scenario_state() {
    local scenario="$1"
    local state="$2"
    local base="(.run_id == \$run_id) and (.status == \"asserting\") and any(.units[]?; (.unit_key | contains(\$scenario)))"
    local predicate

    case "$scenario" in
        crash_recovery)
            predicate="$base and any(.units[]?; (.unit_key | contains(\$scenario)) and .state == \"ready\")"
            ;;
        evidence_recovery)
            predicate="$base and (.evidence | length == 2) and ([.evidence[]? | select(.is_head == true)] | length == 1) and any(.evidence[]?; .supersedes_evidence_id != null)"
            ;;
        external_pr_conflict)
            predicate="$base and (.observations | length >= 2) and any(.conditions[]?; .condition_type == \"external_merge_alarm\" and .is_open == true)"
            ;;
        deploy_split_brain)
            predicate="$base and (.deployment_observations | length == 1) and any(.conditions[]?; .condition_type == \"deploy_split_brain\" and .is_open == true)"
            ;;
        stalled_approval)
            predicate="$base and any(.units[]?; (.unit_key | contains(\$scenario)) and .state == \"awaiting_approval\" and .active_claim == null)"
            ;;
        *)
            return 1
            ;;
    esac
    jq -e --arg run_id "$RUN_ID" --arg scenario "$scenario" "$predicate" <<<"$state" >/dev/null
}

run_scenario() {
    local scenario="$1"
    local payload
    local state

    payload="$(jq -nc --arg key "${IDEMPOTENCY_PREFIX}:scenario:${scenario}" \
        '{idempotency_key: $key, expected_version: 0}')"
    state="$(api_request POST "/api/v1/production-drills/${RUN_ID}/scenarios/${scenario}" "$payload")" || return 1
    assert_scenario_state "$scenario" "$state" || return 1
    record_assertion "$scenario" "passed"
}

record_failure() {
    local failure_code="$1"
    local payload

    payload="$(jq -nc \
        --arg key "${IDEMPOTENCY_PREFIX}:failure:${failure_code}" \
        --arg failure_code "$failure_code" \
        --arg diagnostic_ref "drill://redacted/${IDEMPOTENCY_PREFIX}/${failure_code}" \
        '{idempotency_key: $key, expected_version: 0, failure_code: $failure_code, diagnostic_ref: $diagnostic_ref}')"
    api_request POST "/api/v1/production-drills/${RUN_ID}/fail" "$payload" >/dev/null
}

RUN_ID=""
APPROVE_LIVE_RESTART=0
IDEMPOTENCY_PREFIX="production-drill-bootstrap"
EVIDENCE_FILE=""
ASSERTIONS_JSON="${ASSERTIONS_JSON:-[]}"
FINAL_STATE_JSON='{}'

usage() {
    printf 'Usage: %s --run-id UUID [--approve-live-restart] [--evidence-file PATH]\n' "$0" >&2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --run-id)
            RUN_ID="${2:-}"
            shift 2
            ;;
        --approve-live-restart)
            APPROVE_LIVE_RESTART=1
            shift
            ;;
        --evidence-file)
            EVIDENCE_FILE="${2:-}"
            shift 2
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

if [ -z "$RUN_ID" ]; then
    log "RUN_ID is required; a HUMAN must start the run before this runner is invoked"
    exit 2
fi

if ! [[ "$RUN_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
    log "RUN_ID must be a canonical UUID"
    exit 2
fi

if [ -z "$EVIDENCE_FILE" ]; then
    EVIDENCE_FILE="${TMPDIR:-/tmp}/production-drill-${RUN_ID}-${IDEMPOTENCY_PREFIX}.json"
fi

fail_run() {
    local failure_code="$1"
    local detail="$2"

    log "$detail"
    if [ -n "${DRILL_TOKEN:-}" ] && [ -n "${DRILL_CREDENTIAL_KEY_ID:-}" ] && ! record_failure "$failure_code"; then
        log "audited SYSTEM failure could not be recorded"
    fi
    write_evidence "failed" "$failure_code"
    log "evidence: $EVIDENCE_FILE"
    exit 1
}

write_bootstrap_evidence() {
    local detail="$1"

    printf '{"status":"failed","run_id":"%s","idempotency_prefix":"%s","detail":"%s","assertions":[],"final_state":{}}\n' \
        "$RUN_ID" "$IDEMPOTENCY_PREFIX" "$detail" >"$EVIDENCE_FILE"
}

if ! require_command uuidgen; then
    write_bootstrap_evidence "uuidgen_unavailable"
    exit 1
fi
IDEMPOTENCY_PREFIX="production-drill-$(uuidgen | tr '[:upper:]' '[:lower:]')"
preflight_openapi || fail_run "runner_preflight_failed" "production OpenAPI preflight failed"
preflight_readiness || fail_run "runner_preflight_failed" "production readiness preflight failed"
if ! load_drill_credential; then
    write_bootstrap_evidence "credential_load_failed"
    exit 1
fi

for scenario in "${scenarios[@]}"; do
    run_scenario "$scenario" || fail_run "${scenario}_failed" "${scenario} scenario failed"
done

FINAL_STATE_JSON="$(api_request GET "/api/v1/production-drills/${RUN_ID}/state")" || \
    fail_run "runner_preflight_failed" "final run-scoped state read failed"
jq -e --arg run_id "$RUN_ID" '.run_id == $run_id and .status == "asserting"' \
    <<<"$FINAL_STATE_JSON" >/dev/null || fail_run "runner_preflight_failed" "final run-scoped state is invalid"

if [ "$APPROVE_LIVE_RESTART" -eq 1 ]; then
    preflight_readiness || fail_run "crash_recovery_failed" "readiness preflight before restart failed"
    # This repository intentionally has no executable restart hook. The approved Coolify action
    # is an operator handoff, so an approval flag cannot turn into an arbitrary host command.
    log "Coolify operator handoff is required; perform the approved restart outside this runner"
    fail_run "crash_recovery_failed" "fixed Coolify control is not configured in this runner"
fi

write_evidence "restart_handoff_required" "all fixed API scenarios passed; restart requires operator handoff"
log "evidence: $EVIDENCE_FILE"
exit 3
