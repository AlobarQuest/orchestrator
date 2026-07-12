#!/bin/bash
# Shared harness for the WS-P2.1 recovery drills (AC-010), in the vps-backup restore-drill.sh
# mold: disposable scratch, trapped idempotent teardown, die() vs accumulating fail(),
# timestamped log(), exit 0 = PASS.
#
# EVERY DRILL IS READ-ONLY TOWARD PRODUCTION AND SHARED SYSTEMS. Each run owns, and destroys:
#   - one throwaway Postgres container   (drill-pg-$$)
#   - one throwaway database             (orchestrator_drill -- NEVER orchestrator_test, which
#                                         tests/conftest.py drops and recreates; pointing a drill
#                                         at it would erase a concurrent test run's database)
#   - one throwaway uvicorn              (bound to 127.0.0.1)
# Credentials are generated per run and never written to a tracked file.
#
# Source, don't execute:  . "$(dirname "$0")/drill_common.sh"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRILL_DB="orchestrator_drill"
PG_IMAGE="postgres:16-alpine"
CONTAINER="drill-pg-$$"
PG_PORT="${DRILL_PG_PORT:-55432}"
API_PORT="${DRILL_API_PORT:-58000}"
API_URL="http://127.0.0.1:${API_PORT}"
DRILL_DIR="$(mktemp -d /tmp/orchestrator-drill-XXXXXX)"
LOG_FILE="${DRILL_DIR}/drill.log"
SERVER_PID=""
KEEP=0

# To STDERR, deliberately. Helpers like seed_unit echo their result on stdout and are read
# through command substitution -- a log line on stdout would be captured AS that result, and a
# failure message would silently become the work-unit id it was reporting the absence of.
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE" >&2; }
FAILURES=()
fail() { log "FAIL: $*"; FAILURES+=("$*"); }
die() { log "ERROR: $*"; exit 1; }

parse_common_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --keep) KEEP=1; shift ;;
            *) echo "unknown argument: $1" >&2; exit 2 ;;
        esac
    done
}

cleanup() {
    if [ -n "$SERVER_PID" ]; then kill -9 "$SERVER_PID" >/dev/null 2>&1 || true; fi
    if [ "$KEEP" -eq 1 ]; then
        log "--keep set: leaving $CONTAINER and $DRILL_DIR in place"
        return
    fi
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    rm -rf "$DRILL_DIR"
}
trap 'cleanup' EXIT

preflight() {
    command -v docker >/dev/null || die "docker not found"
    command -v jq >/dev/null || die "jq not found"
    command -v curl >/dev/null || die "curl not found"
    [ -f "$REPO_ROOT/alembic.ini" ] || die "must run from the orchestrator repo"
    [ -x "$REPO_ROOT/.venv/bin/python" ] || die ".venv not found -- run 'uv sync' first"
    # A leftover listener would answer every request in this drill with ANOTHER run's database
    # and ANOTHER run's credentials, and the drill would report on a system it never started.
    # Refuse to run rather than silently drill the wrong process.
    ! lsof -ti ":$API_PORT" >/dev/null 2>&1 \
        || die "port $API_PORT is already in use -- kill the stale listener, or set DRILL_API_PORT"
    ! lsof -ti ":$PG_PORT" >/dev/null 2>&1 \
        || die "port $PG_PORT is already in use -- kill the stale container, or set DRILL_PG_PORT"
}

start_scratch_postgres() {
    log "[pg] starting throwaway $PG_IMAGE as $CONTAINER on 127.0.0.1:$PG_PORT"
    docker run -d --name "$CONTAINER" \
        -e POSTGRES_PASSWORD=drill -e POSTGRES_DB="$DRILL_DB" \
        -p "127.0.0.1:${PG_PORT}:5432" "$PG_IMAGE" >>"$LOG_FILE" 2>&1
    # The postgres image starts a TEMPORARY init server, stops it, then starts the real one -- a
    # single probe can land in that gap, so require two consecutive successes.
    local ready=0
    for _ in $(seq 1 40); do
        if docker exec "$CONTAINER" psql -U postgres -d "$DRILL_DB" -qAtc "SELECT 1" >/dev/null 2>&1; then
            sleep 1
            if docker exec "$CONTAINER" psql -U postgres -d "$DRILL_DB" -qAtc "SELECT 1" >/dev/null 2>&1; then
                ready=1; break
            fi
        fi
        sleep 1
    done
    [ "$ready" -eq 1 ] || die "[pg] scratch postgres never became ready"
    export ORCHESTRATOR_DATABASE_URL="postgresql+psycopg://postgres:drill@127.0.0.1:${PG_PORT}/${DRILL_DB}"
}

# Run SQL against the THROWAWAY database only.
scratch_sql() {
    docker exec -i "$CONTAINER" psql -U postgres -d "$DRILL_DB" -qAt -v ON_ERROR_STOP=1 -c "$1"
}

write_auth_env() {
    SYSTEM_TOKEN="$(uuidgen)"; WORKER_TOKEN="$(uuidgen)"; VERIFIER_TOKEN="$(uuidgen)"
    local sys_hash worker_hash verifier_hash
    sys_hash="$(printf '%s' "$SYSTEM_TOKEN" | shasum -a 256 | cut -d' ' -f1)"
    worker_hash="$(printf '%s' "$WORKER_TOKEN" | shasum -a 256 | cut -d' ' -f1)"
    verifier_hash="$(printf '%s' "$VERIFIER_TOKEN" | shasum -a 256 | cut -d' ' -f1)"

    # One actor per credential, because `_validate_m2m_credentials` requires the mapping to be
    # ONE-TO-ONE: three credential keys sharing an agent_id is rejected as a misconfiguration,
    # not silently accepted. The drill therefore needs three distinct machine identities, and
    # the ROLE still comes from ORCHESTRATOR_M2M_ROLES keyed by credential -- the same two-part
    # arrangement production uses.
    cat >"$DRILL_DIR/registry.json" <<'JSON'
{"schema": "orchestrator-actor-bundle/v1",
 "source_revision": "0123456789abcdef0123456789abcdef01234567",
 "actors": [
  {"agent_id": "orchestrator", "version": 1, "status": "active", "runtime": "orchestrator", "authority_profile": "system-v1"},
  {"agent_id": "worker",       "version": 1, "status": "active", "runtime": "runner",       "authority_profile": "agent-queue-v1"},
  {"agent_id": "verifier",     "version": 1, "status": "active", "runtime": "verifier",     "authority_profile": "verifier-v1"},
  {"agent_id": "devon",        "version": 1, "status": "active", "runtime": "human",        "authority_profile": "human-operator-v1"}
 ]}
JSON

    export ORCHESTRATOR_REGISTRY_BUNDLE="$DRILL_DIR/registry.json"
    export ORCHESTRATOR_M2M_CREDENTIALS="{\"system-key\":{\"agent_id\":\"orchestrator\",\"token_hash\":\"$sys_hash\"},\"worker-key\":{\"agent_id\":\"worker\",\"token_hash\":\"$worker_hash\"},\"verifier-key\":{\"agent_id\":\"verifier\",\"token_hash\":\"$verifier_hash\"}}"
    export ORCHESTRATOR_M2M_ROLES='{"system-key":"system","worker-key":"worker","verifier-key":"verifier"}'
    export ORCHESTRATOR_EMAIL_TO_ACTOR='{"devon@example.invalid":"devon"}'
    export ORCHESTRATOR_TRUSTED_PROXY_IPS='["127.0.0.1"]'
    export ORCHESTRATOR_PROXY_MARKER="drill-marker"
    export ORCHESTRATOR_CSRF_SECRET="drill-only-csrf-secret-with-at-least-32-bytes"
}

migrate_scratch() {
    log "[db] alembic upgrade head against $DRILL_DB"
    (cd "$REPO_ROOT" && .venv/bin/alembic upgrade head) >>"$LOG_FILE" 2>&1 \
        || die "[db] migration failed -- see $LOG_FILE"
}

start_orchestrator() {
    log "[api] starting throwaway uvicorn on $API_URL"
    # `exec`, so the backgrounded subshell BECOMES uvicorn and $! is the server's own pid.
    # Without it $! is the subshell, and killing the subshell orphans a live uvicorn that keeps
    # the port -- which then answers the next drill from a dead run's database.
    (cd "$REPO_ROOT" && exec .venv/bin/uvicorn orchestrator.main:app \
        --host 127.0.0.1 --port "$API_PORT") >>"$LOG_FILE" 2>&1 &
    SERVER_PID=$!
    disown "$SERVER_PID" 2>/dev/null || true  # we poll the port; suppress job-control noise
    for _ in $(seq 1 40); do
        if curl -fsS "$API_URL/health/live" >/dev/null 2>&1; then return 0; fi
        sleep 0.5
    done
    die "[api] orchestrator never became live -- see $LOG_FILE"
}

# Simulate process death: SIGKILL, no graceful shutdown.
#
# Waits for the port to actually free before returning. Otherwise a restart could probe the
# DYING process's socket, find it live, and "restart" nothing -- the drill would then report a
# clean recovery it never performed.
kill_orchestrator() {
    [ -n "$SERVER_PID" ] || return 0
    kill -9 "$SERVER_PID" >/dev/null 2>&1 || true
    SERVER_PID=""
    for _ in $(seq 1 40); do
        lsof -ti ":$API_PORT" >/dev/null 2>&1 || return 0
        sleep 0.5
    done
    die "[api] port $API_PORT never freed after SIGKILL"
}

# api <METHOD> <PATH> <system|worker|verifier|human> [JSON_BODY]
#
# `human` authenticates the way a human really does here -- forward-auth headers from a trusted
# proxy, never a bearer token. The two are mutually exclusive: presenting both is rejected. The
# governance gates (intake, authority approval) require a human actor, and a drill that let a
# machine credential open them would be drilling a system we do not run.
api() {
    local method="$1" path="$2" role="$3" body="${4:-}"
    local -a auth
    case "$role" in
        system)   auth=(-H "Authorization: Bearer $SYSTEM_TOKEN"   -H "X-Credential-Key-Id: system-key") ;;
        worker)   auth=(-H "Authorization: Bearer $WORKER_TOKEN"   -H "X-Credential-Key-Id: worker-key") ;;
        verifier) auth=(-H "Authorization: Bearer $VERIFIER_TOKEN" -H "X-Credential-Key-Id: verifier-key") ;;
        human)    auth=(-H "X-Alobar-Proxy: $ORCHESTRATOR_PROXY_MARKER" -H "X-Alobar-Email: devon@example.invalid") ;;
        *) die "unknown role: $role" ;;
    esac
    if [ -n "$body" ]; then
        curl -sS -X "$method" "$API_URL$path" "${auth[@]}" \
            -H "Content-Type: application/json" -d "$body"
    else
        curl -sS -X "$method" "$API_URL$path" "${auth[@]}"
    fi
}

summarize() {
    if [ ${#FAILURES[@]} -eq 0 ]; then
        log "$1 PASS"
        exit 0
    fi
    log "$1 FAILED: ${#FAILURES[@]} failure(s)"
    for f in "${FAILURES[@]}"; do log "  - $f"; done
    exit 1
}

# --- canonical-state readers (SELECT only; assertions may read, state changes may not) -------

unit_version() { scratch_sql "SELECT version FROM work_units WHERE id='$1'"; }
unit_state()   { scratch_sql "SELECT state   FROM work_units WHERE id='$1'"; }
unit_attempts() { scratch_sql "SELECT attempt_count FROM work_units WHERE id='$1'"; }
count_sql()    { scratch_sql "SELECT count(*) FROM $1"; }

# The domain error code from a rejected API call: {"error": {"code": ..., "message": ...}}.
# Echoes "none" for a response that is not an error, so a silently-succeeding call that should
# have been refused fails the assertion instead of passing it.
api_error() { echo "$1" | jq -r '.error.code // "none"'; }

expect() {
    local what="$1" actual="$2" expected="$3"
    if [ "$actual" = "$expected" ]; then
        log "  ok: $what = $expected"
    else
        fail "$what: expected '$expected', got '$actual'"
    fi
}

# Seed a readiness-satisfied, claimable work unit THROUGH THE PUBLIC API.
# Echoes: "<revision_id> <unit_id>"
seed_unit() {
    local label="$1"
    local revision unit approval response
    response=$(api POST /api/v1/revisions human "$(jq -nc --arg k "$label-rev" --arg h "sha256:$label" '{
        idempotency_key:$k, expected_version:0, package_id:("pkg-"+$k), source_repository:"AlobarQuest/orchestrator",
        revision:1, content_hash:$h, source_path:"intent.md", source_commit:"abc123",
        approved_by:"devon", approved_at:"2026-07-05T00:00:00+00:00",
        approval_event_id:"00000000-0000-4000-8000-000000000001",
        enforcement_snapshot:{acceptance_criteria:["ac-1"]},
        authority:{capabilities:{repository_write:"allowed"},budgets:{max_attempts:3,max_llm_calls:4}},
        registry_version:1}')")
    revision=$(echo "$response" | jq -r '.id // empty')
    [ -n "$revision" ] || die "could not register a revision ($label): $response"

    response=$(api POST "/api/v1/revisions/$revision/work-units" human "$(jq -nc --arg k "$label-unit" '{
        idempotency_key:$k, expected_version:0, unit_key:$k, title:"drill unit", outcome:"drill",
        required_capability:"repository_write",
        authority:{capabilities:{repository_write:"allowed"},budgets:{max_attempts:3,max_llm_calls:4}},
        max_attempts:3, approved_by:"devon", approved_at:"2026-07-05T00:00:00+00:00"}')")
    unit=$(echo "$response" | jq -r '.id // empty')
    [ -n "$unit" ] || die "could not register a work unit ($label): $response"

    # The authority approval is what makes the unit genuinely READY. Without it a reclaim or
    # requeue would land the unit in READY while claim_unit still refuses it -- the exact trap
    # `_readiness_eligibility_error` exists to prevent, and a drill that skipped it would be
    # exercising a state real recovery can never produce.
    approval=$(api POST "/api/v1/work-units/$unit/approvals" human "$(jq -nc --arg k "$label-approval" \
        --argjson v "$(unit_version "$unit")" '{
        idempotency_key:$k, expected_version:$v, subject_type:"authority", reason:"drill authority"}')")
    echo "$approval" | jq -e '.id' >/dev/null 2>&1 || die "authority approval failed ($label): $approval"

    api POST "/api/v1/work-units/$unit/commands/ready" system "$(jq -nc --arg k "$label-ready" \
        --argjson v "$(unit_version "$unit")" '{idempotency_key:$k, expected_version:$v}')" >/dev/null
    [ "$(unit_state "$unit")" = "ready" ] || die "unit did not reach READY ($label)"

    echo "$revision $unit"
}

# Age a claim's lease into the past, on the THROWAWAY database.
#
# This is ENVIRONMENT SETUP standing in for elapsed wall clock, not a state transition:
# LEASE_DURATION is a hardcoded 15 minutes (kernel/leases.py) with no env override, so a drill
# cannot make a lease lapse through any public surface without actually waiting 15 minutes. Every
# orchestrator STATE CHANGE in these drills still goes through the public API.
expire_lease() {
    local unit="$1"
    scratch_sql "UPDATE claims SET lease_expires_at = acquired_at
                 WHERE work_unit_id='$unit' AND released_at IS NULL" >/dev/null
}
