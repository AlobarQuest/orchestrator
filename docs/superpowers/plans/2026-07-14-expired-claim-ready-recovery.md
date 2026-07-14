# Expired-Claim Ready Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover expired production work to `ready` without minting an unusable lease, and make factory-runner report coding/finalization failures through the ordinary worker lifecycle.

**Architecture:** Orchestrator receives an additive SYSTEM-only `recover-expired-claim` operation that reuses the existing lease-expiry and readiness rules but deliberately stops before replacement claim acquisition. Factory-runner gains a workspace-backed `fail-run` command and workflow conditions that finalize only successful coding and report failures while the worker lease remains active.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic, Typer, httpx, pytest, GitHub Actions YAML.

## Global Constraints

- Preserve `POST /work-units/{unit_id}/reclaim-expired-claim` unchanged as the worker-to-worker handoff contract.
- The new recovery operation ends in `ready`, creates no `Claim`, returns no lease authority, and does not increment `attempt_count`.
- Lease tokens remain one-shot secrets and must never appear in logs, events, tracked files, test failure messages, or workflow inputs.
- Eligibility failure commits the honest `failed` state and released claim before returning `attempts_exhausted` or `readiness_not_satisfied`.
- Failure reasons are bounded stage identifiers: `coding_action_failed` or `finalization_failed`; raw action output is never sent to Orchestrator.
- Finalization runs only after coding success; failure reporting must not turn a failed GitHub job green.
- Do not add GitHub polling, automatic budget increases, lease heartbeat, PR-binding changes, evidence redesign, or validation-kit changes.
- Use repo-local `CLAUDE.md` invariants and `~/Developer/code-standards/STANDARDS.md`.
- Write every behavior test first and observe the expected failure before implementation.

---

### Task 1: Orchestrator expired-claim recovery service

**Files:**
- Create: `tests/services/test_recover_expired_claim.py`
- Modify: `src/orchestrator/services/claims.py`
- Modify: `tests/idempotency/test_reclaim_idempotency.py`

**Interfaces:**
- Consumes: existing `_locked_unit`, `_current_claim`, `_validate_expired_active_claim`, `release_claim`, `_readiness_eligibility_error`, and `_transition` helpers.
- Produces: `recover_expired_claim(session, unit_id, actor, idempotency_key, *, expected_version=None) -> WorkUnit | DomainError`.

- [ ] **Step 1: Write the service behavior tests**

Add tests that create an approved unit, claim it, expire the claim with the existing `expire` helper, and assert:

```python
result = recover_expired_claim(
    migrated_session,
    unit.id,
    SYSTEM,
    "recover-expired-1",
    expected_version=unit.version,
)
assert isinstance(result, WorkUnit)
assert result.state == WorkUnitState.READY
assert result.attempt_count == 1
assert migrated_session.scalar(
    select(func.count()).select_from(Claim).where(Claim.work_unit_id == unit.id)
) == 1
```

Cover both `claimed` and `executing` sources; verify the old claim has `released_at` and
`terminal_reason == "lease_expired"`; verify the two transition events share one correlation ID.
Add negative tests for a live lease, WORKER caller, exhausted budget, and unsatisfied readiness.
Eligibility failures must assert the unit persists as `failed` and the claim persists released.

- [ ] **Step 2: Run the focused service tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/services/test_recover_expired_claim.py -q
```

Expected: collection/import failure because `recover_expired_claim` does not exist.

- [ ] **Step 3: Add idempotency tests and verify RED**

Extend `tests/idempotency/test_reclaim_idempotency.py` with exact replay and conflict coverage:

```python
one = recover_expired_claim(session, unit.id, SYSTEM, KEY, expected_version=version)
two = recover_expired_claim(session, unit.id, SYSTEM, KEY, expected_version=version)
assert isinstance(one, WorkUnit) and isinstance(two, WorkUnit)
assert one.version == two.version
assert _count(session, Event, f"{KEY}:failed") == 1
assert _count(session, Event, KEY) == 1
assert _count(session, Claim, KEY) == 0
```

Reuse the same key with a different expected version or actor and assert
`idempotency_conflict`. Run the new idempotency tests and confirm they fail for the missing service.

- [ ] **Step 4: Implement the minimal transactional service**

Add the public wrapper and private operation beside `reclaim_expired_claim`:

```python
def recover_expired_claim(
    session: Session,
    unit_id: uuid.UUID,
    actor: ActorContext,
    idempotency_key: str,
    *,
    expected_version: int | None = None,
) -> WorkUnit | DomainError:
    try:
        result = _perform_expired_claim_recovery(
            session, unit_id, actor, idempotency_key, expected_version=expected_version
        )
        session.commit()
        return result
    except DomainError as error:
        session.rollback()
        return error
    except Exception:
        session.rollback()
        raise
```

The private operation must validate replay before current-state checks, lock the unit and latest
claim, enforce exact version and SYSTEM role, release the expired claim, write correlated
`source -> failed` and `failed -> ready` events, and return before `_acquire_reclaimed_claim`.
Use `key:failed` for the failure event and the bare key for the ready event. For an eligibility
error, include `result_error_code` in the failed-event payload and return the error so the wrapper
commits it.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/services/test_recover_expired_claim.py \
  tests/idempotency/test_reclaim_idempotency.py -q
```

Expected: all tests pass; no warning or skipped recovery test.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/orchestrator/services/claims.py \
  tests/services/test_recover_expired_claim.py \
  tests/idempotency/test_reclaim_idempotency.py
git commit -m "feat: recover expired claims to ready"
```

---

### Task 2: Orchestrator API, lifecycle reason, and ingress contracts

**Files:**
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/api/routes.py`
- Create: `tests/api/test_recover_expired_claim_api.py`
- Modify: `tests/api/test_lifecycle_api.py`
- Modify: `tests/architecture/test_scope_guards.py`
- Modify: `tests/architecture/test_recovery_actions_cannot_complete.py`
- Modify: `tests/idempotency/matrix.py`

**Interfaces:**
- Consumes: Task 1 `recover_expired_claim(...)` and existing `UnitResponse`.
- Produces: `POST /api/v1/work-units/{unit_id}/recover-expired-claim`; optional `LifecycleCommand.reason` forwarded into `TransitionCommand.reason`.

- [ ] **Step 1: Write API and schema tests**

Create an API test that expires a real claim through the database fixture, calls the new endpoint
as SYSTEM, and asserts exactly the ordinary unit projection:

```python
assert response.status_code == 200
assert response.json() == {"id": str(unit_id), "state": "ready", "version": start_version + 2}
assert "lease_token" not in response.json()
assert "claim_id" not in response.json()
```

Add WORKER-forbidden coverage. Assert OpenAPI binds the route's 200 response to `UnitResponse` and
that `LifecycleCommand` exposes optional `reason`. Extend a lifecycle failure request with
`"reason": "coding_action_failed"` and assert unit history stores that reason.

- [ ] **Step 2: Run API tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/api/test_recover_expired_claim_api.py \
  tests/api/test_lifecycle_api.py -q
```

Expected: the new route returns 404 or test import fails, and the reason assertion fails because
the public schema does not accept/forward it.

- [ ] **Step 3: Add the route and optional failure reason**

Add:

```python
class RecoverExpiredClaimCommand(CommandBase):
    pass

class LifecycleCommand(CommandBase):
    reason: str | None = None
    # existing fields remain unchanged
```

Wire the route beside the existing reclaim route:

```python
@router.post(
    "/work-units/{unit_id}/recover-expired-claim",
    response_model=UnitResponse,
)
def recover_expired(unit_id: UUID, body: RecoverExpiredClaimCommand, actor: ActorDep,
                    session: SessionDep) -> object:
    return _raise_error(
        recover_expired_claim(
            session,
            unit_id,
            actor,
            body.idempotency_key,
            expected_version=body.expected_version,
        )
    )
```

Pass `reason=body.reason` when constructing `TransitionCommand` in the generic lifecycle route.

- [ ] **Step 4: Update explicit ingress guards**

Add the new path to `test_production_post_route_inventory_is_explicit`, add
`recover_expired_claim` to `RECOVERY_ENTRY_POINTS`, and add a `COMPOUND_KEY` matrix row pointing
to the exact duplicate-recovery test. Do not weaken or bypass the inventory checks.

- [ ] **Step 5: Run focused API/architecture/idempotency tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/api/test_recover_expired_claim_api.py \
  tests/api/test_lifecycle_api.py \
  tests/architecture/test_scope_guards.py \
  tests/architecture/test_recovery_actions_cannot_complete.py \
  tests/idempotency/test_matrix.py \
  tests/idempotency/test_reclaim_idempotency.py -q
```

Expected: all selected tests pass and the route inventory remains exact.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/orchestrator/api/schemas.py src/orchestrator/api/routes.py \
  tests/api/test_recover_expired_claim_api.py tests/api/test_lifecycle_api.py \
  tests/architecture/test_scope_guards.py \
  tests/architecture/test_recovery_actions_cannot_complete.py \
  tests/idempotency/matrix.py
git commit -m "feat: expose expired claim ready recovery"
```

---

### Task 3: Factory-runner failure client and CLI

**Files:**
- Modify: `/Users/devon/Projects/factory-runner/src/factory_runner/client.py`
- Modify: `/Users/devon/Projects/factory-runner/src/factory_runner/cli.py`
- Modify: `/Users/devon/Projects/factory-runner/tests/test_client.py`
- Modify: `/Users/devon/Projects/factory-runner/tests/test_cli.py`

**Interfaces:**
- Consumes: Orchestrator command `POST /api/v1/work-units/{unit_id}/commands/fail` with optional reason.
- Produces: `OrchestratorClient.fail(...)` and `factory-runner fail-run`.

- [ ] **Step 1: Write the client test and verify RED**

Assert `client.fail(...)` sends this exact request:

```json
{
  "expected_version": 5,
  "idempotency_key": "factory-runner:unit-1:fail:a1:coding_action_failed",
  "attempt": 1,
  "lease_token": "lease-redacted",
  "reason": "coding_action_failed"
}
```

to `/api/v1/work-units/unit-1/commands/fail`. Run the single test and confirm failure because the
method does not exist.

- [ ] **Step 2: Implement `OrchestratorClient.fail` and verify GREEN**

Implement it as a typed convenience wrapper over `command(unit_id, "fail", payload)`. Run:

```bash
.venv/bin/pytest tests/test_client.py -q
```

- [ ] **Step 3: Write `fail-run` CLI tests and verify RED**

Create a workspace containing `run.json` with attempt, lease token, work-unit ID, and
`submit_expected_version`. Invoke `fail-run` and assert the fake client receives those exact
values plus the bounded reason and idempotency key. Assert command output contains the unit and
attempt but not `lease-redacted`.

Add local rejection tests for:

- requested work-unit ID differing from `run.json`;
- reason outside `{coding_action_failed, finalization_failed}`;
- missing `run.json`.

The rejected cases must assert no client mutation occurred.

- [ ] **Step 4: Implement `fail-run` and verify GREEN**

Add a Typer command that reads the existing sanitized workspace JSON helpers, validates the unit,
selects one of the two fixed reasons, and calls:

```python
client.fail(
    work_unit_id,
    expected_version=int(run["submit_expected_version"]),
    idempotency_key=f"factory-runner:{work_unit_id}:fail:a{attempt}:{reason}",
    attempt=attempt,
    lease_token=str(run["lease_token"]),
    reason=reason,
)
```

Print only `failed work unit {work_unit_id} attempt {attempt}` after success.

- [ ] **Step 5: Run factory-runner client/CLI tests**

Run:

```bash
.venv/bin/pytest tests/test_client.py tests/test_cli.py -q
```

Expected: all tests pass; captured output contains no lease secret.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/factory_runner/client.py src/factory_runner/cli.py \
  tests/test_client.py tests/test_cli.py
git commit -m "feat: report factory runner failures"
```

---

### Task 4: Factory-runner workflow failure routing and command contract

**Files:**
- Modify: `/Users/devon/Projects/factory-runner/.github/workflows/factory-runner.yml`
- Modify: `/Users/devon/Projects/factory-runner/tests/test_workflow_contract.py`
- Modify: `/Users/devon/Projects/factory-runner/tests/fixtures/orchestrator_command_contract.json`
- Modify: `/Users/devon/Projects/factory-runner/tests/test_orchestrator_command_contract.py`

**Interfaces:**
- Consumes: Task 3 `factory-runner fail-run`; Orchestrator's generated `LifecycleCommand` schema.
- Produces: workflow steps with IDs `coding`, `finalize`, and `report_failure`.

- [ ] **Step 1: Write workflow contract tests and verify RED**

Parse the YAML and assert:

```python
assert coding["id"] == "coding"
assert finalize["id"] == "finalize"
assert "steps.coding.outcome == 'success'" in finalize["if"]
assert report["id"] == "report_failure"
assert "always()" in report["if"]
assert "steps.prepare.outcome == 'success'" in report["if"]
assert "steps.coding.outcome == 'failure'" in report["if"]
assert "steps.finalize.outcome == 'failure'" in report["if"]
assert "factory-runner fail-run" in report["run"]
```

Also assert the run script selects only `coding_action_failed` or `finalization_failed`, and that
the step has runner credentials but no Anthropic or PR token.

Run `tests/test_workflow_contract.py` and confirm it fails on the current `always()` finalizer.

- [ ] **Step 2: Implement the workflow conditions and failure reporter**

Set the coding step ID, gate finalization strictly on coding success, set finalizer ID, and add an
`always()` failure step. Its shell selects `finalization_failed` only when finalization failed;
otherwise it selects `coding_action_failed`, then calls `factory-runner fail-run` with the existing
Orchestrator URL, credential ID, work-unit ID, and workspace directory.

Do not add `continue-on-error`; the original failed coding/finalization step must keep the job red.

- [ ] **Step 3: Refresh the cross-repo lifecycle command contract**

Regenerate `tests/fixtures/orchestrator_command_contract.json` from the approved Orchestrator
branch's Pydantic schemas using the existing fixture format. Confirm `LifecycleCommand` lists
`reason` as an optional property, not a required field. Recalculate and update only the pinned
`CONTRACT_SHA256` constant.

- [ ] **Step 4: Run focused workflow and contract tests**

Run:

```bash
.venv/bin/pytest tests/test_workflow_contract.py \
  tests/test_orchestrator_command_contract.py -q
```

Expected: all tests pass; the fixture hash is exact.

- [ ] **Step 5: Run full factory-runner gate and commit Task 4**

Run:

```bash
make check
```

Expected: lint, type checking, and all collected tests pass.

```bash
git add .github/workflows/factory-runner.yml \
  tests/test_workflow_contract.py \
  tests/fixtures/orchestrator_command_contract.json \
  tests/test_orchestrator_command_contract.py
git commit -m "fix: close failed factory runner attempts"
```

---

### Task 5: Cross-repo verification and review

**Files:**
- Modify only if verification exposes a defect in Tasks 1–4.

**Interfaces:**
- Consumes: both completed branches.
- Produces: terminal test evidence and review-ready branches; no deployment.

- [ ] **Step 1: Run the complete Orchestrator gate**

From the Orchestrator worktree:

```bash
make check
```

Expected: lint, type checking, and a non-zero collected test count all pass.

- [ ] **Step 2: Run the complete factory-runner gate again**

From the factory-runner worktree:

```bash
make check
```

Expected: lint, type checking, and all tests pass.

- [ ] **Step 3: Verify cross-repo schema identity**

Generate the command schema from the Orchestrator branch and compare canonical bytes with
factory-runner's fixture. Assert the SHA-256 equals the pinned runner constant.

- [ ] **Step 4: Review both diffs against code standards**

Review each merge-base diff for correctness, unnecessary abstractions, duplicate lifecycle logic,
weak tests, secret exposure, and new suppression comments. Resolve all Critical and Important
findings and rerun covering tests.

- [ ] **Step 5: Prepare separate review-ready PRs**

Push the two branches and open draft PRs. Orchestrator must merge and deploy before the
factory-runner workflow can rely on the optional reason field, although the field is backward
compatible and the `/commands/fail` route already exists.

Do not deploy or recover the production work unit until Devon approves and merges the PRs.
