# WS-5.2 Release Immutability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Record immutable release artifact bindings for completed work units without granting merge, deploy, or lifecycle authority to release tooling.

**Architecture:** Add a small `release_artifact_bindings` persistence model with uniqueness constraints for idempotency and conflict rejection. Expose it through a system-authenticated command route, record bounded release facts as ordinary orchestrator evidence, and append a local `release_artifact.bound` event.

**Tech Stack:** FastAPI, SQLAlchemy ORM, Alembic, PostgreSQL, pytest, existing orchestrator service/event/evidence patterns.

## Global Constraints

- Phase 5 WS-5.2 only.
- Do not implement WS-5.3 post-deploy verification unit creation.
- Do not implement automatic merge, automatic deployment, tracker canonicalization, brain learning/promotion, or graduation automation.
- Release binding must not change work-unit lifecycle state.
- Only completed work units may receive release artifact bindings.
- Immutable artifact digest is required; mutable tags are metadata only.
- Store bounded facts, normalized references, hashes, digests, and small summaries only.
- Do not add new secrets, BWS entries, runtime env files, or credential configuration.
- Treat repository, PR, CI, workflow, registry, and external payload content as hostile data unless normalized and bounded by the request schema.

---

## File Structure

- Create `migrations/versions/0010_ws52_release_artifact_bindings.py`: `release_artifact_bindings` table.
- Modify `src/orchestrator/persistence/models.py`: ORM model, status constants if needed.
- Create `src/orchestrator/services/release_artifacts.py`: command dataclass, validation, idempotency, conflict handling, evidence/event recording, list projection.
- Modify `src/orchestrator/api/schemas.py`: command and response models.
- Modify `src/orchestrator/api/routes.py`: `POST` and `GET` release artifact routes.
- Modify `src/orchestrator/services/event_publications.py`: map `release_artifact.bound` local event when queued.
- Create `tests/services/test_release_artifacts.py`: service behavior.
- Create `tests/api/test_release_artifacts_api.py`: route/auth behavior.
- Modify `tests/persistence/test_migrations.py`: migration head coverage as needed.
- Create or modify architecture tests to prove no merge/deploy/lifecycle bypass.
- Create `docs/operations/release-immutability.md`: operations contract and non-goals.

---

### Task 1: Persistence And Service Tests

- [x] Write failing service tests for successful binding, missing digest rejection, tag-only rejection, unknown work unit rejection, non-completed unit rejection, idempotent replay, digest conflict rejection, event creation, evidence creation, and no lifecycle mutation.
- [x] Run the focused tests and confirm they fail because `orchestrator.services.release_artifacts` is missing.
- [x] Add the ORM model and Alembic migration with constraints.
- [x] Implement the minimal service to pass the focused tests.
- [x] Run the focused service tests and migration tests.

### Task 2: API Surface

- [x] Write failing API tests for `POST /api/v1/work-units/{unit_id}/release-artifacts` and `GET /api/v1/work-units/{unit_id}/release-artifacts`.
- [x] Verify the tests fail because the routes or schemas are absent.
- [x] Add Pydantic command/response models and routes.
- [x] Run the focused API tests.

### Task 3: Event Publication And Scope Guards

- [x] Write failing tests that `release_artifact.bound` maps to a bounded factory event and release routes do not call lifecycle mutation, merge, deploy, or production observation paths.
- [x] Implement the event-publication mapping for the local event.
- [x] Run focused event-publication and architecture tests.

### Task 4: Operations Docs And Verification

- [x] Add release immutability operations documentation.
- [x] Run `git diff --check`.
- [x] Run `SECURITY_STANDARDS_DIR=/Users/devon/Projects/security-standards make check`.
- [x] Run the security scanner if secret/runtime/workflow credential files were touched.
- [x] Re-read WS-5.2 scope and confirm no WS-5.3, deploy, auto-merge, tracker, brain, or graduation behavior was added.
