# WS-3.3 Protocol Smoke Runtime Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the orchestrator runtime protocol end to end with public API/CLI smoke paths, standing-context enforcement, safe lease recovery, authority-update semantics, and a read-only status ledger.

**Architecture:** Add a narrow context/preflight subsystem around existing claims, lifecycle, evidence, package intake, and events. Keep canonical lifecycle state in existing work-unit tables; add immutable context snapshots and read-only projections. Closed Phase-2 packages enter only through explicit protocol-fixture intake, not the WS-3.2 executable intake path.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL 16, Pydantic, Typer, pytest, existing orchestrator API/CLI/test patterns.

## Global Constraints

- Approved package: `ws-3.3-protocol-smoke-runtime-semantics` revision 1, hash `7829f22bfa30630a906d75131c84bc018c5dac3ceac7b933b7c9b46d23e5047a`.
- Preserve WS-3.1 lifecycle, claim, lease, dependency, evidence, adjudication, waiver, event, API, CLI, UI, migration, and architecture behavior.
- Preserve WS-3.2 package intake and decomposition boundaries.
- Existing executable package intake remains approved-status-only.
- Closed package handling is fixture/protocol acceptance intake only.
- No factory-runner dispatch, GitHub Actions worker execution, production deployment, Coolify mutation, external `factory-event/v1` publication, tracker-canonical state, automatic merge, autonomous intent approval, autonomous decomposition approval, or worker-controlled canonical completion.
- No UI in WS-3.3 unless API/CLI cannot satisfy acceptance; current design says API/CLI is sufficient.
- Use TDD: write failing tests, run them red, implement minimal code, run green.
- Final local verification command: `PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test make check`.

---

## File Structure

- Create `migrations/versions/0004_ws33_protocol_runtime.py`: context snapshot schema, claim/evidence context links, fixture intake enum extension.
- Modify `src/orchestrator/persistence/models.py`: `ContextSnapshot` model and new nullable FK columns.
- Create `src/orchestrator/kernel/context.py`: pure normalization, fingerprinting, version comparison, context classification.
- Create `src/orchestrator/services/context.py`: transactional preflight recording and policy decisions.
- Modify `src/orchestrator/services/claims.py`: claim-time preflight, claim context binding, reclaim stale-context guarantees.
- Modify `src/orchestrator/services/lifecycle.py`: start-time preflight and execution context binding.
- Modify `src/orchestrator/services/evidence.py`: evidence context binding to active execution snapshot.
- Modify `src/orchestrator/services/package_intake.py`: explicit `protocol_fixture` intake purpose while preserving executable `approved` status rule.
- Modify `src/orchestrator/package_sources.py`: fixture payload loading for chain-verified closed packages without weakening executable intake loader.
- Create `src/orchestrator/services/status_ledger.py`: read-only status projection.
- Modify `src/orchestrator/api/schemas.py`: context, preflight, fixture intake, status ledger request/response schemas.
- Modify `src/orchestrator/api/routes.py`: preflight, context snapshots, status-ledger routes; claim/start/evidence request handling.
- Modify `src/orchestrator/cli.py`: `preflight`, `list-context-snapshots`, `status-ledger`; `--context` support for claim/start.
- Add tests in `tests/kernel/test_context_policy.py`, `tests/services/test_context_preflight.py`, `tests/services/test_status_ledger.py`, `tests/services/test_protocol_fixture_intake.py`, `tests/api/test_context_api.py`, `tests/api/test_status_ledger_api.py`, `tests/cli/test_context_cli.py`, `tests/cli/test_status_ledger_cli.py`, `tests/protocol/test_ws33_smoke.py`, and architecture tests.
- Add fixtures under `tests/fixtures/intent-packages/ws33-protocol-smoke/`, `tests/fixtures/intent-packages/ws24-ci-evidence-control-closed/`, and `tests/fixtures/intent-packages/ws24-listing-launch-closed/`.
- Create `docs/evidence/ws-3.3-evidence-index.md` after implementation verification.

---

### Task 1: Migration and Persistence Model

**Files:**
- Create: `migrations/versions/0004_ws33_protocol_runtime.py`
- Modify: `src/orchestrator/persistence/models.py`
- Test: `tests/persistence/test_migrations.py`
- Test: `tests/persistence/test_constraints.py`

**Interfaces:**
- Produces: `ContextSnapshot` SQLAlchemy model.
- Produces: nullable `Claim.context_snapshot_id`, `Claim.execution_context_snapshot_id`, and `Evidence.context_snapshot_id`.
- Produces: `work_package_revisions.intake_source` accepts `protocol_fixture` in addition to existing values.

- [ ] **Step 1: Write migration tests**

Add tests that inspect migrated database metadata:

```python
def test_ws33_context_snapshot_tables_and_links_exist(migrated_engine):
    inspector = inspect(migrated_engine)
    columns = {c["name"] for c in inspector.get_columns("context_snapshots")}
    assert {
        "id",
        "work_package_revision_id",
        "work_unit_id",
        "claim_id",
        "attempt",
        "actor_id",
        "actor_role",
        "context",
        "context_fingerprint",
        "classification",
        "decision",
        "approval_id",
        "event_id",
        "idempotency_key",
        "created_at",
    } <= columns
    claim_columns = {c["name"] for c in inspector.get_columns("claims")}
    assert {"context_snapshot_id", "execution_context_snapshot_id"} <= claim_columns
    evidence_columns = {c["name"] for c in inspector.get_columns("evidence")}
    assert "context_snapshot_id" in evidence_columns
```

Add a constraint test:

```python
def test_context_snapshot_claim_attempt_must_match_claim(migrated_session, ready_unit):
    claim = Claim(
        work_unit_id=ready_unit.id,
        attempt=1,
        claimed_by="worker-1",
        lease_token_hash="hash",
        idempotency_key="claim",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    migrated_session.add(claim)
    migrated_session.flush()
    migrated_session.add(
        ContextSnapshot(
            work_package_revision_id=ready_unit.work_package_revision_id,
            work_unit_id=ready_unit.id,
            claim_id=claim.id,
            attempt=2,
            actor_id="worker-1",
            actor_role="worker",
            context={},
            context_fingerprint="fp",
            classification="accepted",
            decision="accepted",
            event_id=uuid.uuid4(),
            idempotency_key="ctx",
        )
    )
    with pytest.raises(IntegrityError):
        migrated_session.commit()
```

- [ ] **Step 2: Run tests red**

Run:

```bash
pytest tests/persistence/test_migrations.py::test_ws33_context_snapshot_tables_and_links_exist tests/persistence/test_constraints.py::test_context_snapshot_claim_attempt_must_match_claim -q
```

Expected: fail because table/model/columns do not exist.

- [ ] **Step 3: Implement migration**

Create `0004_ws33_protocol_runtime.py` with:

```python
revision = "0004_ws33_protocol_runtime"
down_revision = "0003_ws32_intake_decomposition"
```

Migration must:

- create `context_snapshots`;
- add nullable context FK columns to `claims` and `evidence`;
- replace `ck_work_package_revisions_intake_source` to include `protocol_fixture`;
- add check constraints for classification and decision;
- add composite FK/check behavior so claim-bound snapshots match claim attempt. If PostgreSQL cannot express the attempt match with a plain check, add a composite unique constraint on `claims(id, attempt)` and a composite FK from `context_snapshots(claim_id, attempt)`.

- [ ] **Step 4: Implement model**

Add constants and model fields in `models.py`:

```python
INTAKE_SOURCES = ("manual_ws31", "package_cli", "protocol_fixture")
CONTEXT_CLASSIFICATIONS = (
    "accepted",
    "same_scope",
    "authority_expanding",
    "missing_required",
    "stale",
)
CONTEXT_DECISIONS = ("accepted", "rejected", "requires_approval")
```

Add `ContextSnapshot` class with matching columns and add nullable FK fields on `Claim` and `Evidence`.

- [ ] **Step 5: Run migration tests green**

Run:

```bash
pytest tests/persistence/test_migrations.py tests/persistence/test_constraints.py -q
```

Expected: pass.

---

### Task 2: Pure Standing-Context Policy

**Files:**
- Create: `src/orchestrator/kernel/context.py`
- Test: `tests/kernel/test_context_policy.py`

**Interfaces:**
- Produces: `normalize_standing_context(value: Mapping[str, object]) -> dict[str, object]`.
- Produces: `context_fingerprint(context: Mapping[str, object]) -> str`.
- Produces: `classify_context_update(previous: Mapping[str, object] | None, current: Mapping[str, object], required: Mapping[str, object], allowed_capabilities: set[str]) -> ContextDecision`.
- Produces dataclass `ContextDecision(classification: str, decision: str, reasons: tuple[str, ...])`.

- [ ] **Step 1: Write policy tests**

Cover:

- missing required field -> `missing_required`/`rejected`;
- unchanged context -> `accepted`/`accepted`;
- same-scope newer standard version -> `same_scope`/`accepted`;
- narrower capability set -> `same_scope`/`accepted`;
- added capability -> `authority_expanding`/`requires_approval`;
- broader authority profile -> `authority_expanding`/`requires_approval`;
- deterministic fingerprint independent of key order.

Example:

```python
def test_added_capability_requires_approval():
    previous = valid_context(capabilities=["repository_read"])
    current = valid_context(capabilities=["repository_read", "repository_write"])
    decision = classify_context_update(
        previous,
        current,
        required=required_context(),
        allowed_capabilities={"repository_read"},
    )
    assert decision.classification == "authority_expanding"
    assert decision.decision == "requires_approval"
    assert "capabilities_expanded" in decision.reasons
```

- [ ] **Step 2: Run tests red**

Run:

```bash
pytest tests/kernel/test_context_policy.py -q
```

Expected: import failure for missing module.

- [ ] **Step 3: Implement pure policy**

Implement with no database imports. Version comparison should parse dotted integer versions such as `1`, `1.0`, and `1.2.3`; non-matching versions are `stale` unless identical strings.

Required context fields for WS-3.3:

```python
REQUIRED_CONTEXT_FIELDS = (
    "code_standards_version",
    "security_standards_version",
    "project_standards_version",
    "agent_id",
    "authority_profile",
    "runtime_name",
    "runtime_version",
    "skill_bundle_id",
    "skill_bundle_version",
    "capabilities",
)
```

- [ ] **Step 4: Run tests green**

Run:

```bash
pytest tests/kernel/test_context_policy.py -q
```

Expected: pass.

---

### Task 3: Context Preflight Service

**Files:**
- Create: `src/orchestrator/services/context.py`
- Modify: `src/orchestrator/persistence/models.py`
- Test: `tests/services/test_context_preflight.py`

**Interfaces:**
- Produces: `PreflightCommand`.
- Produces: `record_preflight(session, command, actor) -> ContextSnapshot | DomainError`.
- Produces: `require_claim_context(...) -> ContextSnapshot`.
- Produces: `require_execution_context(...) -> ContextSnapshot`.

- [ ] **Step 1: Write service tests**

Tests:

- diagnostic preflight records event `context.preflight_recorded`;
- idempotent replay returns same snapshot;
- missing required context returns `DomainError("context_missing_required")`;
- authority expansion before claim returns `DomainError("context_authority_expanding")`;
- same-scope update records `context.update_accepted`;
- authority expansion with matching human approval records accepted snapshot.

Use `ready_unit` and `ActorContext("worker-1", ActorRole.WORKER)`.

- [ ] **Step 2: Run tests red**

Run:

```bash
pytest tests/services/test_context_preflight.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement service**

Service rules:

- lock `WorkUnit` for any preflight tied to claim/start;
- derive required context from `WorkPackageRevision.enforcement_snapshot.get("required_context", {})`;
- derive allowed capabilities from the work-unit authority envelope where possible and from `required_capability`;
- create `ContextSnapshot` and `Event` in the same transaction;
- do not commit inside helper functions used by claim/start; top-level diagnostic route may commit via route/service wrapper.

- [ ] **Step 4: Run focused tests green**

Run:

```bash
pytest tests/kernel/test_context_policy.py tests/services/test_context_preflight.py -q
```

Expected: pass.

---

### Task 4: Claim, Start, and Evidence Context Enforcement

**Files:**
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/services/claims.py`
- Modify: `src/orchestrator/services/lifecycle.py`
- Modify: `src/orchestrator/services/evidence.py`
- Test: `tests/services/test_claims.py`
- Test: `tests/services/test_lifecycle_guards.py`
- Test: `tests/services/test_evidence.py`

**Interfaces:**
- `ClaimCommand` gains `standing_context: dict[str, Any]`.
- `LifecycleCommand` gains `standing_context: dict[str, Any] | None` and `context_snapshot_id: UUID | None`.
- `EvidenceCommand` gains `context_snapshot_id: UUID | None`.
- `LeaseResponse` gains `context_snapshot_id: UUID | None`.
- `EvidenceResponse` gains `context_snapshot_id: UUID | None`.

- [ ] **Step 1: Write claim/start/evidence tests**

Tests:

- claim without context is rejected in WS-3.3 protocol path;
- claim with valid context stores `claims.context_snapshot_id`;
- start rechecks context and stores `claims.execution_context_snapshot_id`;
- start with authority-expanding context is rejected until approval;
- evidence stores active execution context;
- evidence with stale context snapshot from old attempt is rejected.

Example assertion:

```python
claim = claim_unit(session, unit.id, worker(), "claim-ctx", standing_context=valid_context())
assert isinstance(claim, LeaseGrant)
row = session.get(Claim, claim.claim_id)
assert row.context_snapshot_id is not None
```

- [ ] **Step 2: Run tests red**

Run:

```bash
pytest tests/services/test_claims.py tests/services/test_lifecycle_guards.py tests/services/test_evidence.py -q
```

Expected: new context tests fail.

- [ ] **Step 3: Update services**

Modify `claim_unit` to accept `standing_context: dict[str, Any] | None = None`. For legacy tests that do not opt into WS-3.3 context, keep existing behavior only when no package-required context is present and no API/CLI WS-3.3 command requires context. Protocol smoke tests must require context.

Modify start transition path:

- only `command.target == WorkUnitState.EXECUTING` needs execution preflight;
- require active claim first;
- create/validate execution snapshot under lock;
- set `Claim.execution_context_snapshot_id`.

Modify evidence storage:

- `_validate_attempt` returns the active `Claim`;
- evidence context defaults to `claim.execution_context_snapshot_id`;
- reject when missing for WS-3.3 protocol paths;
- reject explicit snapshot if unit/claim/actor/attempt does not match.

- [ ] **Step 4: Run focused tests green**

Run:

```bash
pytest tests/services/test_claims.py tests/services/test_lifecycle_guards.py tests/services/test_evidence.py -q
```

Expected: pass.

---

### Task 5: API and CLI Context Surface

**Files:**
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/api/routes.py`
- Modify: `src/orchestrator/cli.py`
- Test: `tests/api/test_context_api.py`
- Test: `tests/cli/test_context_cli.py`
- Test: `tests/cli/test_cli_http_parity.py`

**Interfaces:**
- Adds `POST /api/v1/work-units/{unit_id}/preflight`.
- Adds `GET /api/v1/work-units/{unit_id}/context-snapshots`.
- CLI commands: `preflight`, `list-context-snapshots`.
- CLI `claim` and lifecycle `start` accept `--context @file.json`.

- [ ] **Step 1: Write API/CLI tests**

Test OpenAPI includes schemas. Test CLI and API parity with `httpx.MockTransport` matching existing `test_cli_http_parity.py`.

Example CLI call:

```python
result = CliRunner().invoke(
    app,
    [
        "claim",
        unit_id,
        "--idempotency-key",
        "claim-1",
        "--expected-version",
        "2",
        "--context",
        "@/tmp/context.json",
        "--json",
    ],
)
assert result.exit_code == 0
assert json.loads(result.stdout)["context_snapshot_id"]
```

- [ ] **Step 2: Run tests red**

Run:

```bash
pytest tests/api/test_context_api.py tests/cli/test_context_cli.py tests/cli/test_cli_http_parity.py -q
```

Expected: route/command missing failures.

- [ ] **Step 3: Implement schemas/routes/CLI**

Add Pydantic models:

- `StandingContextCommand`;
- `PreflightCommandModel`;
- `ContextSnapshotResponse`;
- update `ClaimCommand`, `LifecycleCommand`, `EvidenceCommand`, `LeaseResponse`, `EvidenceResponse`.

Add CLI helper `_json_file_or_object(value: str)` reusing `_json_object` behavior for `@file`.

- [ ] **Step 4: Run tests green**

Run:

```bash
pytest tests/api/test_context_api.py tests/cli/test_context_cli.py tests/cli/test_cli_http_parity.py -q
```

Expected: pass.

---

### Task 6: Protocol Fixture Intake for Closed Packages

**Files:**
- Modify: `src/orchestrator/package_sources.py`
- Modify: `src/orchestrator/services/package_intake.py`
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/api/routes.py`
- Modify: `src/orchestrator/cli.py`
- Test: `tests/services/test_protocol_fixture_intake.py`
- Test: `tests/cli/test_package_intake_cli.py`
- Test fixtures under `tests/fixtures/intent-packages/`

**Interfaces:**
- Adds `load_protocol_fixture_intake_payload(path, source_repository=...)`.
- Adds `PackageIntakeRegistration.intake_purpose: Literal["executable", "protocol_fixture"] = "executable"`.
- Existing `intake-package` remains executable approved-status-only.
- New CLI `intake-protocol-fixture` accepts closed packages.

- [ ] **Step 1: Write fixture intake tests**

Tests:

- executable loader rejects closed package;
- protocol fixture loader accepts chain-verified closed package;
- protocol fixture registration stores `intake_source == "protocol_fixture"` and `status_at_intake == "closed"`;
- protocol fixture units cannot be moved to Ready unless explicitly created from the synthetic protocol package or marked executable by an approved decomposition flag;
- architecture search asserts executable `_VALID_STATUSES` remains `{"approved"}` for package-cli intake.

- [ ] **Step 2: Run tests red**

Run:

```bash
pytest tests/services/test_protocol_fixture_intake.py tests/cli/test_package_intake_cli.py -q
```

Expected: new protocol fixture symbols missing.

- [ ] **Step 3: Implement fixture loader and service path**

Do not change `load_package_intake_payload` approval-only behavior. Add a separate loader:

```python
def load_protocol_fixture_intake_payload(path: Path, *, source_repository: str) -> dict[str, object]:
    ...
```

It must:

- require `package.status == lineage.current_state == "closed"`;
- verify approval for current revision/hash;
- preserve `status_at_intake: "closed"`;
- set `intake_source` or `intake_purpose` to `protocol_fixture`;
- add `verification_limitations["protocol_fixture_only"] = True`.

Service path must require human actor and reject any attempt to use `protocol_fixture` as `package_cli`.

- [ ] **Step 4: Run tests green**

Run:

```bash
pytest tests/services/test_protocol_fixture_intake.py tests/cli/test_package_intake_cli.py -q
```

Expected: pass.

---

### Task 7: Status Ledger Projection

**Files:**
- Create: `src/orchestrator/services/status_ledger.py`
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/api/routes.py`
- Modify: `src/orchestrator/cli.py`
- Test: `tests/services/test_status_ledger.py`
- Test: `tests/api/test_status_ledger_api.py`
- Test: `tests/cli/test_status_ledger_cli.py`

**Interfaces:**
- Produces `status_ledger(session, filters) -> tuple[StatusLedgerRow, ...]`.
- Adds `GET /api/v1/status-ledger`.
- Adds CLI `status-ledger`.

- [ ] **Step 1: Write projection tests**

Create a claimed/executing unit with renew event, dependency blocker, action approval pending, evidence, adjudication, failure event, and context snapshot. Assert row contains:

- actor ID;
- unit ID/key/title/state;
- claim ID/attempt/lease expiry;
- last heartbeat/event time;
- blockers;
- pending approvals;
- latest evidence;
- latest adjudication;
- last failure;
- context classification/decision.

Add API/CLI tests and a route guard that no POST/PATCH/DELETE exists under `/api/v1/status-ledger`.

- [ ] **Step 2: Run tests red**

Run:

```bash
pytest tests/services/test_status_ledger.py tests/api/test_status_ledger_api.py tests/cli/test_status_ledger_cli.py -q
```

Expected: missing module/route/command.

- [ ] **Step 3: Implement projection**

Use SQLAlchemy queries over current tables. Do not write any events on read. Sort by `last_event_at desc nulls last`, then actor ID.

- [ ] **Step 4: Run tests green**

Run:

```bash
pytest tests/services/test_status_ledger.py tests/api/test_status_ledger_api.py tests/cli/test_status_ledger_cli.py -q
```

Expected: pass.

---

### Task 8: End-to-End Protocol Smoke Suite

**Files:**
- Create: `tests/protocol/test_ws33_smoke.py`
- Create fixtures: `tests/fixtures/intent-packages/ws33-protocol-smoke/{package.yaml,lineage.yaml}`
- Use existing API/CLI fixtures.

**Interfaces:**
- Consumes public API and CLI behavior from previous tasks.
- Produces acceptance proof for WS-3.3 protocol paths.

- [ ] **Step 1: Write smoke tests**

Implement tests for:

- approved package intake -> approved decomposition -> Draft work units;
- Draft -> Ready;
- claim with context;
- renew;
- start with execute context;
- block -> ready;
- request approval -> approval -> ready;
- submit evidence -> verify -> complete;
- revision required -> ready;
- failed -> authorize retry -> ready;
- lease expiry -> reclaim -> stale credential rejection;
- status ledger reflects meaningful states.

Use API/CLI calls where practical; service shortcuts are allowed only for fixture setup that is not under test.

- [ ] **Step 2: Run smoke tests red**

Run:

```bash
pytest tests/protocol/test_ws33_smoke.py -q
```

Expected: failures until all integration edges are complete.

- [ ] **Step 3: Implement missing integration glue only**

Fix only failures in the public-path integration. Do not add new lifecycle edges.

- [ ] **Step 4: Run smoke tests green**

Run:

```bash
pytest tests/protocol/test_ws33_smoke.py -q
```

Expected: pass.

---

### Task 9: Architecture Guards and Evidence

**Files:**
- Modify: `tests/architecture/test_scope_guards.py`
- Modify or create: `tests/architecture/test_ws33_scope_guards.py`
- Create: `docs/evidence/ws-3.3-evidence-index.md`

**Interfaces:**
- Produces guard tests for excluded scope.
- Produces evidence index after verification.

- [ ] **Step 1: Write architecture guards**

Tests must assert:

- no `workflow_dispatch` or factory-runner dispatch code;
- no external `factory_events` publisher import in runtime code;
- no status-ledger mutation routes;
- no automatic merge path;
- executable package intake still rejects closed packages;
- protocol fixture path is named `protocol_fixture` and guarded.

- [ ] **Step 2: Run architecture guards**

Run:

```bash
pytest tests/architecture/test_scope_guards.py tests/architecture/test_ws33_scope_guards.py -q
```

Expected: pass after implementation.

- [ ] **Step 3: Run focused suite**

Run:

```bash
pytest tests/kernel/test_context_policy.py tests/services/test_context_preflight.py tests/services/test_protocol_fixture_intake.py tests/services/test_status_ledger.py tests/protocol/test_ws33_smoke.py -q
```

Expected: pass.

- [ ] **Step 4: Run full local gate**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test make check
```

Expected: all tests pass. Existing Starlette/httpx warning may remain.

- [ ] **Step 5: Write evidence index**

Create `docs/evidence/ws-3.3-evidence-index.md` with:

- intent package ID/revision/hash;
- baseline results;
- focused test commands and results;
- full `make check` result;
- scope guard result;
- absent evidence called out honestly.

---

### Task 10: Final Review and PR

**Files:**
- All changed implementation, test, migration, and evidence files.

**Interfaces:**
- Produces ready-for-review GitHub PR.

- [ ] **Step 1: Run code-standards diff review**

Review the diff against `~/Developer/code-standards/STANDARDS.md`. Specifically inspect:

- wrong abstractions;
- over-engineering;
- duplicated context policy logic;
- comments that restate code;
- new suppression comments;
- private service shortcuts in smoke tests.

- [ ] **Step 2: Run final verification before completion**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test make check
```

Expected: pass.

- [ ] **Step 3: Push and open PR ready for review**

Branch: `codex/ws33-protocol-smoke-runtime-semantics`.

PR body must include:

- approved package revision/hash;
- design/review docs;
- implementation summary;
- verification commands;
- explicit exclusions;
- note that Devon alone merges.

- [ ] **Step 4: Wait for exact named CI checks**

Run:

```bash
gh pr checks <PR_NUMBER>
```

Expected: `Quality` succeeds on the exact PR head.

- [ ] **Step 5: Stop at Devon merge gate**

Do not merge. Do not close the intent package. After Devon merges, open the separate intent-package closure PR if lifecycle requires it.

