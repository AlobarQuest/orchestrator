# WS-4.4 Infra-Lane Linkage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record infra-lane linkages from orchestrator work units to change-manager/infraops artifacts without duplicating infra approval, execution, change-window, verification, or rollback machinery.

**Architecture:** Add a narrow orchestrator-owned `infra_lane_links` record with an idempotent service and API. The record stores non-secret external references and evidence pointers; work-unit lifecycle, claims, and lease recovery remain in the existing orchestrator protocol, while change-manager/infraops remain the infra approval/execution authorities.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Postgres/SQLite-compatible tests, pytest, existing orchestrator service/API patterns.

## Global Constraints

- Do not implement WS-4.2 dispatch automation.
- Do not modify WS-4.3 local-heavy runtime except documentation references.
- Do not rebuild change-manager approvals or infraops execution.
- Do not implement Phase 5 verifier/release immutability.
- No automatic merge behavior.
- Store only non-secret IDs, URLs, and evidence references.

---

### Task 1: Persistence And Service Contract

**Files:**
- Modify: `src/orchestrator/persistence/models.py`
- Create: `migrations/versions/0009_ws44_infra_lane_linkage.py`
- Create: `src/orchestrator/services/infra_links.py`
- Test: `tests/services/test_infra_links.py`
- Test: `tests/persistence/test_migrations.py`

**Interfaces:**
- Produces: `record_infra_lane_link(session, InfraLaneLinkCommand) -> InfraLaneLink | DomainError`
- Produces: `list_infra_lane_links(session, work_unit_id) -> tuple[InfraLaneLink, ...]`

- [ ] Write service tests that fail because `orchestrator.services.infra_links` does not exist.
- [ ] Add `InfraLaneLink` model with status constraints, unique `idempotency_key`, and unique `(work_unit_id, attempt, change_manager_ref)`.
- [ ] Add migration `0009_ws44_infra_lane_linkage`.
- [ ] Implement the service with work-unit existence checks, expected-version checks, attempt validation, actor authorization, idempotent replay, and event creation.
- [ ] Run focused tests and migration tests.

### Task 2: HTTP API

**Files:**
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/api/routes.py`
- Test: `tests/api/test_infra_links_api.py`

**Interfaces:**
- POST `/api/v1/work-units/{unit_id}/infra-lane-links`
- GET `/api/v1/work-units/{unit_id}/infra-lane-links`
- Response model `InfraLaneLinkResponse`

- [ ] Write API tests that fail because the routes/schemas do not exist.
- [ ] Add command and response schemas.
- [ ] Wire routes to the service and commit via the existing session dependency.
- [ ] Confirm worker/system actors can record links and unauthenticated requests remain rejected by existing auth.

### Task 3: Operations Documentation

**Files:**
- Create: `docs/operations/infra-lane-linkage.md`
- Modify: `docs/operations/dispatch.md`

**Interfaces:**
- Documents routing criteria and the boundary between orchestrator, change-manager, and infraops.

- [ ] Document GitHub-hosted vs local-heavy vs infra-lane routing criteria.
- [ ] Document the linkage fields and evidence expectations.
- [ ] Document recovery through orchestrator claim/reclaim APIs.
- [ ] State that WS-4.4 introduces no new credential.

### Task 4: Verification

**Files:**
- No code files beyond prior tasks.

- [ ] Run focused service/API tests.
- [ ] Run `SECURITY_STANDARDS_DIR=/Users/devon/Projects/security-standards make check`.
- [ ] Run security scan if any secret/reference-handling config is touched.
- [ ] Run `cd /Users/devon/Projects/project-standards && uv run portfolio foundation`.
- [ ] Recheck production `/health/live`, `/health/ready`, missing M2M 401, and configured M2M 200 without printing secret values.
