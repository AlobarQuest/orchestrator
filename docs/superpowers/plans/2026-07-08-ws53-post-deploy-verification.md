# WS-5.3 Post-Deploy Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create bounded post-deploy verification work from immutable release artifact bindings and normalized deployment observations.

**Architecture:** Add a small `deployment_observations` persistence model with uniqueness constraints for one generated post-deploy verification work unit per release binding and environment. Expose it through a system-authenticated API, record bounded evidence and events, extend verifier criteria/evaluators for generated post-deploy units, and document the production closeout boundary.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy ORM, Alembic, PostgreSQL, pytest, existing orchestrator evidence/event/verifier/lifecycle services.

## Global Constraints

- WS-5.3 implementation only; production deployment is a separate Devon-approved closeout after merge.
- Phase 5 is not complete until WS-5.1, WS-5.2, and WS-5.3 are deployed to production and the exit condition is proven.
- Phase 5 exit condition: a package cannot reach Completed without every criterion's evidence recorded; a deployed artifact traces to an approved intent revision.
- Do not implement automatic merge or give any worker/verifier/release/deploy actor merge permission.
- Do not implement automatic deployment or mutate production infrastructure.
- Do not enable production dispatch automation.
- Do not mark the original implementation work unit complete.
- Do not bypass WS-5.1 verifier or existing lifecycle completion guards.
- Do not make CI, deployment tooling, trackers, workers, verifiers, release tooling, or production observation canonical lifecycle authorities.
- Require observed artifact digest to match the immutable WS-5.2 release binding digest exactly.
- Store bounded facts only: stable references, hashes/digests, small summaries, probe statuses, route/auth/posture summaries, and provenance pointers.
- Do not store raw tokens, full logs, unbounded response bodies, private infra mutation details, tracker text, external instructions, or production observation text.
- Do not add a new secret, BWS manifest entry, runtime env file, workflow credential, merge authority, or deploy authority.

---

## File Structure

- Create `migrations/versions/0011_ws53_deployment_observations.py`: `deployment_observations` table.
- Modify `src/orchestrator/persistence/models.py`: add `DeploymentObservation` ORM model.
- Create `src/orchestrator/services/deployment_observations.py`: command dataclasses, validation, idempotency/conflict handling, generated unit creation, evidence/event recording, list projection.
- Modify `src/orchestrator/services/verifier_criteria.py`: load generated post-deploy criteria for generated units.
- Modify `src/orchestrator/services/verifier_evaluators.py`: deterministic evaluation for new bounded post-deploy evidence types.
- Modify `src/orchestrator/services/event_publications.py`: map deployment observation events and resolve revision context.
- Modify `src/orchestrator/api/schemas.py`: deployment observation command/response models and bounded nested fact models.
- Modify `src/orchestrator/api/routes.py`: `POST` and `GET` deployment observation routes.
- Modify `tests/persistence/test_migrations.py`: migration coverage.
- Create `tests/services/test_deployment_observations.py`: service behavior and verifier integration.
- Create `tests/api/test_deployment_observations_api.py`: route/auth/OpenAPI behavior.
- Create `tests/architecture/test_ws53_scope_guards.py`: no merge/deploy/tracker/brain/dispatch bypass.
- Modify `tests/services/test_event_publications.py`: factory event mapping.
- Add `docs/operations/post-deploy-verification.md`: operations contract and production closeout boundary.
- Add `docs/superpowers/evidence/2026-07-08-ws53-post-deploy-verification-evidence.md`: final implementation evidence after verification.

---

### Task 1: Persistence And Service Red Tests

**Files:**
- Create: `tests/services/test_deployment_observations.py`
- Modify: `tests/persistence/test_migrations.py`
- Create later implementation targets: `migrations/versions/0011_ws53_deployment_observations.py`, `src/orchestrator/persistence/models.py`, `src/orchestrator/services/deployment_observations.py`

**Interfaces:**
- Consumes: `ReleaseArtifactBinding`, `WorkUnit`, `WorkPackageRevision`, `Event`, `Evidence`, `ActorContext`.
- Produces: `DeploymentObservationCommand`, `record_deployment_observation(session, command)`, `list_deployment_observations(session, binding_id)`.

- [ ] **Step 1: Write failing service tests**

Add tests that import the not-yet-created service:

```python
from orchestrator.services.deployment_observations import (
    DeploymentObservationCommand,
    record_deployment_observation,
)
```

Create fixtures by reusing `completed_unit()` and `command()` from
`tests.services.test_release_artifacts`, then call `record_release_artifact()`.

Required tests:

- `test_records_deployment_observation_and_generated_post_deploy_unit`
- `test_rejects_unknown_release_binding`
- `test_rejects_release_binding_when_implementation_unit_is_not_completed`
- `test_rejects_digest_mismatch`
- `test_rejects_missing_required_observation_facts`
- `test_rejects_secret_shaped_observation_metadata`
- `test_replay_is_idempotent_and_conflict_rejects_changed_facts`
- `test_observation_records_bounded_evidence_and_events`
- `test_generated_post_deploy_unit_verifies_through_ws51`
- `test_observation_does_not_mutate_original_implementation_unit`

Use these expected names and facts:

```python
ENVIRONMENT = "production"
BASE_URL = "https://sds.alobar.net"
DEPLOYMENT_REF = "coolify:eqj5l7k705fhi12x9i74fqf0:ws53"
DEPLOYMENT_URL = "https://coolify.example.invalid/project/orchestrator/ws53"
DEPLOYER = "coolify"
OBSERVED_AT = datetime(2026, 7, 8, 20, 0, tzinfo=UTC)
```

The generated unit must have:

```python
state == WorkUnitState.SUBMITTED
required_capability == "post_deploy_verification"
unit_key == f"post-deploy:{binding.id}:production"
work_package_revision_id == binding.work_package_revision_id
```

The original implementation unit state/version must remain unchanged.

- [ ] **Step 2: Write failing migration test**

Extend `tests/persistence/test_migrations.py` with a test asserting
`deployment_observations` exists with the required columns, unique constraints
on `idempotency_key` and `(release_artifact_binding_id, environment)`, and
foreign keys to release bindings, work units, package revisions, events, and
evidence.

- [ ] **Step 3: Run red tests**

Run:

```bash
pytest tests/services/test_deployment_observations.py tests/persistence/test_migrations.py -q
```

Expected: FAIL because `orchestrator.services.deployment_observations` and the
`deployment_observations` table do not exist.

### Task 2: Persistence And Service Green Implementation

**Files:**
- Create: `migrations/versions/0011_ws53_deployment_observations.py`
- Modify: `src/orchestrator/persistence/models.py`
- Create: `src/orchestrator/services/deployment_observations.py`

**Interfaces:**
- Produces:
  - `DeploymentObservationCommand`
  - `DeploymentObservationFact`
  - `record_deployment_observation(session, command) -> DeploymentObservation | DomainError`
  - `list_deployment_observations(session, binding_id) -> tuple[DeploymentObservation, ...] | DomainError`

- [ ] **Step 1: Add the migration and ORM model**

Create `deployment_observations` with columns:

- `id`
- `release_artifact_binding_id`
- `implementation_work_unit_id`
- `work_package_revision_id`
- `package_revision_hash`
- `post_deploy_work_unit_id`
- `environment`
- `base_url`
- `observed_artifact_digest`
- `deployment_ref`
- `deployment_url`
- `deployer`
- `observed_at`
- `probe_summary`
- `route_summary`
- `auth_summary`
- `dispatch_summary`
- `status_summary`
- `recorded_by`
- `recorded_at`
- `event_id`
- `post_deploy_event_id`
- `evidence_ids`
- `idempotency_key`

Constraints:

- unique `idempotency_key`;
- unique `(release_artifact_binding_id, environment)`;
- required text check for environment/base URL/digest/deployment ref/deployer/package hash;
- foreign keys to `release_artifact_bindings`, `work_units`, `work_package_revisions`, `events`, and generated post-deploy `work_units`.

- [ ] **Step 2: Implement command validation**

In `src/orchestrator/services/deployment_observations.py`, validate:

- actor role is `system`;
- release binding exists;
- implementation work unit exists and is `completed`;
- revision exists and `revision.content_hash == binding.package_revision_hash`;
- observed digest matches binding digest and is `sha256:` plus 64 lowercase hex chars;
- environment is a small lowercase token;
- base URL and deployment URL are HTTPS URLs;
- deployment ref and deployer are non-empty bounded strings;
- observed-at is timezone-aware;
- summaries are dictionaries/lists with bounded keys/values and no secret-shaped keys or values.

- [ ] **Step 3: Implement idempotency and generated unit creation**

Use advisory idempotency locking like `release_artifacts.py`.

For a new observation:

- create deterministic generated work unit ID using `uuid.uuid5(uuid.NAMESPACE_URL, f"sds:post-deploy:{binding.id}:{environment}")`;
- create unit directly with state `submitted` and approved decomposition fields populated from the observation actor/time;
- set authority to a bounded normalized payload with only `post_deploy_verification`;
- create one `deployment.observed` event for the observation row;
- create one `post_deploy_verification.created` event for the generated unit.

Do not call deployment tooling. Do not call production endpoints. Do not change
the original implementation work unit.

- [ ] **Step 4: Record bounded evidence rows**

Create evidence for the generated unit:

- `post-deploy-artifact` / `release.deployment_observed`
- `post-deploy-health` / `production.health`
- `post-deploy-routes` / `production.route_presence`
- `post-deploy-auth` / `production.auth_behavior`
- `post-deploy-dispatch` / `production.dispatch_posture`

Each evidence row should have attempt `1`, source revision set to the release
binding merge commit, stable refs under `orchestrator://deployment-observations/{id}/...`,
and payload containing only normalized bounded facts.

- [ ] **Step 5: Run focused green tests**

Run:

```bash
pytest tests/services/test_deployment_observations.py tests/persistence/test_migrations.py -q
```

Expected: PASS.

### Task 3: Verifier Integration

**Files:**
- Modify: `src/orchestrator/services/verifier_criteria.py`
- Modify: `src/orchestrator/services/verifier_evaluators.py`
- Modify: `tests/services/test_deployment_observations.py`
- Modify as needed: `tests/services/test_verifier.py`

**Interfaces:**
- Consumes: generated post-deploy work unit linked from `DeploymentObservation.post_deploy_work_unit_id`.
- Produces: generated `PackageAcceptanceCriterion`-like criteria returned by `load_required_criteria()`.

- [ ] **Step 1: Write red verifier tests**

Add tests proving:

- generated post-deploy unit verifies to `completed` when all bounded evidence passes;
- generated post-deploy unit verifies to `revision_required` when a required route is missing or an auth/dispatch summary fails closed;
- verifier still rejects non-submitted/non-verifying generated units through existing lifecycle rules.

Run:

```bash
pytest tests/services/test_deployment_observations.py::test_generated_post_deploy_unit_verifies_through_ws51 -q
```

Expected: FAIL until criteria/evaluators are added.

- [ ] **Step 2: Extend generated criteria loading**

In `load_required_criteria()`, before the approved-decomposition path, detect a
deployment observation whose `post_deploy_work_unit_id == unit.id`.

Return in-memory criterion objects or persisted rows that expose the same
attributes used by `evaluate_criterion()`:

- `post-deploy-artifact`, evidence type `release.deployment_observed`
- `post-deploy-health`, evidence type `production.health`
- `post-deploy-routes`, evidence type `production.route_presence`
- `post-deploy-auth`, evidence type `production.auth_behavior`
- `post-deploy-dispatch`, evidence type `production.dispatch_posture`

- [ ] **Step 3: Extend deterministic evaluators**

Add evidence types to `DETERMINISTIC_TYPES`.

Evaluation rules:

- `release.deployment_observed`: pass only when `observed_artifact_digest == release_artifact_digest` and `binding_id` is present.
- `production.route_presence`: pass only when all listed routes have `present is True`.
- `production.auth_behavior`: pass only when missing M2M status is `401` and configured M2M status is `200` when supplied.
- `production.dispatch_posture`: pass only when `dispatch_enabled is False`.
- malformed or missing required fields fail closed.

- [ ] **Step 4: Run focused verifier tests**

Run:

```bash
pytest tests/services/test_deployment_observations.py tests/services/test_verifier.py -q
```

Expected: PASS.

### Task 4: API Surface And Event Publication

**Files:**
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/api/routes.py`
- Modify: `src/orchestrator/services/event_publications.py`
- Create: `tests/api/test_deployment_observations_api.py`
- Modify: `tests/services/test_event_publications.py`

**Interfaces:**
- API command: `DeploymentObservationCommandModel`
- API response: `DeploymentObservationResponse`

- [ ] **Step 1: Write red API and event tests**

API tests must prove:

- OpenAPI declares both routes and schemas;
- system actor can record and list observations;
- worker/verifier actors are rejected;
- conflicts return the existing DomainError code.

Event tests must prove:

- `deployment.observed` maps to `orchestrator.deployment_observed`;
- `post_deploy_verification.created` maps to `orchestrator.post_deploy_verification_created`;
- target and revision context resolve from `DeploymentObservation`;
- mapping evidence includes local action and raw actor ID only, not raw external payload/log text.

Run:

```bash
pytest tests/api/test_deployment_observations_api.py tests/services/test_event_publications.py -q
```

Expected: FAIL until routes/schemas/event mappings are implemented.

- [ ] **Step 2: Add schemas and routes**

Add nested Pydantic models for probe, route, auth, dispatch, and status facts.
Use the route:

```text
POST /api/v1/release-artifacts/{binding_id}/deployment-observations
GET  /api/v1/release-artifacts/{binding_id}/deployment-observations
```

Both routes use standard auth dependency. The write route passes `actor` into
`DeploymentObservationCommand`.

- [ ] **Step 3: Map event publication**

Update `_factory_action()` and `_revision_id_for_event_subject()` for
`deployment_observation` subjects.

- [ ] **Step 4: Run focused API/event tests**

Run:

```bash
pytest tests/api/test_deployment_observations_api.py tests/services/test_event_publications.py -q
```

Expected: PASS.

### Task 5: Scope Guards, Docs, Evidence, And Full Verification

**Files:**
- Create: `tests/architecture/test_ws53_scope_guards.py`
- Add: `docs/operations/post-deploy-verification.md`
- Add: `docs/superpowers/evidence/2026-07-08-ws53-post-deploy-verification-evidence.md`
- Update if needed: `docs/operations/verifier.md`, `docs/operations/release-immutability.md`

**Interfaces:**
- Documents the implementation and explicit production closeout boundary.

- [ ] **Step 1: Add red scope guard tests**

Tests should assert WS-5.3 files do not contain calls or authority surfaces for:

- merge APIs;
- deploy APIs;
- Coolify mutation;
- GitHub workflow dispatch;
- tracker canonicalization;
- brain promotion;
- production secret values;
- dispatch automation enablement.

Run:

```bash
pytest tests/architecture/test_ws53_scope_guards.py -q
```

Expected: PASS after the guard is written if implementation stayed inside scope.

- [ ] **Step 2: Add operations docs**

Document:

- routes;
- required facts;
- generated work unit behavior;
- verifier behavior;
- evidence boundaries;
- idempotency/conflict rules;
- no-secret/no-merge/no-deploy boundaries;
- Phase 5 production closeout steps after Devon merge approval.

- [ ] **Step 3: Add implementation evidence**

Record:

- branch;
- commits;
- route list;
- migration;
- test commands and outcomes;
- security scan outcome;
- production closeout not performed during implementation.

- [ ] **Step 4: Run full verification**

Run:

```bash
git diff --check
SECURITY_STANDARDS_DIR=/Users/devon/Projects/security-standards make check
PYTHONPATH="$HOME/Projects/security-standards/src" python3 -m security_scan.cli . --category security
```

Expected:

- `git diff --check`: no output;
- `make check`: all tests pass;
- security scan: `0 BLOCK`.

- [ ] **Step 5: Code standards review**

Run:

```bash
/Users/devon/Developer/code-standards/.venv/bin/code-standards check
```

Review the diff for wrong abstractions, over-engineering, duplication, comments
that restate code, weak tests, and any new suppression comments.

- [ ] **Step 6: Write next handoff prompt**

Create a concise next-session closeout handoff covering:

- WS-5.3 implementation commit/branch/PR;
- all verification evidence;
- exact production closeout boundary for deploying WS-5.1/WS-5.2/WS-5.3 after
  Devon merge;
- reminder that Devon's merge gate is permanent and no production mutation has
  happened during implementation.
