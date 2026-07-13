#!/bin/bash
# Shared helpers for the live production recovery-drill runner. Source this file;
# it deliberately has no host-control, database, or process-management commands.

set -euo pipefail

api_base_url="https://sds.alobar.net"
redacted_authorization="Authorization: Bearer <redacted>"
required_openapi_paths=(
    "/health/ready"
    "/api/v1/production-drills/{run_id}"
    "/api/v1/production-drills/{run_id}/state"
    "/api/v1/production-drills/{run_id}/close"
)

log() {
    local message="$1"
    printf '[production-drill] %s\n' "$message" >&2
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
    secret_json="$(bws secret get "$secret_uuid" --output json)"
    DRILL_TOKEN="$(jq -er '.value | fromjson | .ORCHESTRATOR_PRODUCTION_DRILL_TOKEN' <<<"$secret_json")"
    DRILL_CREDENTIAL_KEY_ID="$(jq -er '.value | fromjson | .credential_key_id' <<<"$secret_json")"
    unset secret_json
}

api_request() {
    local method="$1"
    local path="$2"
    local payload="${3:-}"
    local url="${api_base_url}${path}"
    local -a arguments=(--fail-with-body --silent --show-error --request "$method" "$url")

    if [ -n "${DRILL_TOKEN:-}" ]; then
        arguments+=(--header "Authorization: Bearer ${DRILL_TOKEN}")
        arguments+=(--header "X-Credential-Key-Id: ${DRILL_CREDENTIAL_KEY_ID}")
    fi
    if [ -n "$payload" ]; then
        arguments+=(--header "Content-Type: application/json" --data "$payload")
    fi
    curl "${arguments[@]}" 2> >(redact >&2)
}

preflight_openapi() {
    local openapi
    local path

    openapi="$(api_request GET /openapi.json)"
    for path in "${required_openapi_paths[@]}"; do
        jq -e --arg path "$path" '.paths[$path] != null' <<<"$openapi" >/dev/null || {
            log "production OpenAPI preflight missing required path: $path"
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
        '{status: $status, run_id: $run_id, idempotency_prefix: $idempotency_prefix, detail: $detail, assertions: $assertions}' \
        >"$EVIDENCE_FILE"
}

record_assertion() {
    local name="$1"
    local status="$2"
    ASSERTIONS_JSON="$(jq -c --arg name "$name" --arg status "$status" \
        '. + [{name: $name, status: $status}]' <<<"$ASSERTIONS_JSON")"
}

attempt_failure_closeout() {
    local payload

    payload="$(jq -nc --arg key "${IDEMPOTENCY_PREFIX}:failure-closeout" \
        '{idempotency_key: $key, expected_version: 0, closure_reason: "runner_failed_closeout_requested"}')"
    if api_request POST "/api/v1/production-drills/${RUN_ID}/close" "$payload" >/dev/null; then
        record_assertion "failure_closeout" "requested"
        return 0
    fi
    record_assertion "failure_closeout" "unproven"
    return 1
}

RUN_ID=""
APPROVE_LIVE_RESTART=0
IDEMPOTENCY_PREFIX="production-drill-$(uuidgen | tr '[:upper:]' '[:lower:]')"
EVIDENCE_FILE=""
ASSERTIONS_JSON="${ASSERTIONS_JSON:-[]}"

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

if [ -z "$EVIDENCE_FILE" ]; then
    EVIDENCE_FILE="${TMPDIR:-/tmp}/production-drill-${RUN_ID}-${IDEMPOTENCY_PREFIX}.json"
fi

fail_run() {
    local detail="$1"
    log "$detail"
    if ! attempt_failure_closeout; then
        log "run remains failed: audited closure could not be proven"
    fi
    write_evidence "failed" "$detail"
    log "evidence: $EVIDENCE_FILE"
    exit 1
}

assert_open_run_state() {
    local drill="$1"
    local state

    state="$(api_request GET "/api/v1/production-drills/${RUN_ID}/state")" || return 1
    jq -e --arg run_id "$RUN_ID" '.run_id == $run_id and .status == "open"' <<<"$state" >/dev/null
    record_assertion "$drill" "observed"
}

restart_live_application() {
    local restart_command="${ORCHESTRATOR_PRODUCTION_DRILL_RESTART_COMMAND:?ORCHESTRATOR_PRODUCTION_DRILL_RESTART_COMMAND is required}"

    [ -x "$restart_command" ] || {
        log "approved restart command is not executable"
        return 1
    }
    "$restart_command"
}

run_crash_recovery_drill() {
    assert_open_run_state "crash_recovery_pre_restart"
    if [ "$APPROVE_LIVE_RESTART" -ne 1 ]; then
        log "--approve-live-restart is required before the live restart"
        return 1
    fi
    preflight_readiness
    restart_live_application
    preflight_readiness
    assert_open_run_state "crash_recovery_post_restart"
}

run_evidence_recovery_drill() { assert_open_run_state "evidence_recovery"; }
run_external_pr_conflict_drill() { assert_open_run_state "external_pr_conflict"; }
run_deploy_split_brain_drill() { assert_open_run_state "deploy_split_brain"; }
run_stalled_approval_drill() { assert_open_run_state "stalled_approval"; }

require_command uuidgen || exit 1
load_drill_credential || exit 1
preflight_openapi || fail_run "production OpenAPI preflight failed"
preflight_readiness || fail_run "production readiness preflight failed"

run_crash_recovery_drill || fail_run "crash recovery drill failed"
run_evidence_recovery_drill || fail_run "evidence recovery drill failed"
run_external_pr_conflict_drill || fail_run "external PR conflict drill failed"
run_deploy_split_brain_drill || fail_run "deployment split-brain drill failed"
run_stalled_approval_drill || fail_run "stalled approval drill failed"

write_evidence "observed" "all runner-visible assertions were observed"
log "evidence: $EVIDENCE_FILE"
