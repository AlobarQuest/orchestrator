# Automated Check Verifier Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record verifier-owned post-CI named-check evidence and use it to adjudicate the existing WS-6.4 AC-006 `automated_check` criterion.

**Architecture:** A narrow verifier-only command validates dispatch, repository, PR, and armed-head bindings before reusing the existing append-only verifier evidence writer. The evaluator preserves legacy judgment routing unless the current evidence is the reserved verifier named-check type, then deterministically validates the stored check and assertion facts.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, PostgreSQL, pytest.

## Global Constraints

- No database migration, GitHub polling, webhook, runner change, harness, package revision, decomposition change, or automatic merge.
- Do not invoke the live verifier or alter the submitted WS-6.4 unit until the repair is merged and deployed.
- Only `ActorRole.VERIFIER` may record `verifier.github.named_check` evidence.
- Worker evidence submission must reject every `verifier.*` evidence type before claim validation or persistence.
- Post-CI evidence must bind the unit, approved mapped AC, revision, dispatched attempt, target repository, PR number, canonical PR URL, and `UnitPrBinding.verification_read_head_sha`.
- `success` is the only passing check conclusion; `neutral` and `skipped` do not pass.
- Missing, malformed, stale, mismatched, or unsupported facts fail closed.
- Existing `automated_check` criteria without trusted verifier evidence continue routing to `awaiting_review`.
- Preserve append-only evidence by superseding the current AC head.

---

### Task 1: Verifier-Owned Named-Check Evidence And Deterministic Evaluation

**Files:**
- Create: `src/orchestrator/kernel/evidence_types.py`
- Create: `src/orchestrator/services/verifier_evidence.py`
- Modify: `src/orchestrator/services/evidence.py`
- Modify: `src/orchestrator/services/verifier_evaluators.py`
- Modify: `tests/services/test_verifier.py`
- Create: `tests/services/test_verifier_evidence.py`

**Interfaces:**
- Produces: `VERIFIER_NAMED_CHECK_EVIDENCE_TYPE = "verifier.github.named_check"`.
- Produces: immutable `NamedCheckAssertion(name, expected, observed)` and `NamedCheckEvidenceCommand` values.
- Produces: `record_named_check_evidence(session, command) -> Evidence | DomainError`.
- Consumed by: the API route in Task 2 and `evaluate_criterion()`.

- [ ] **Step 1: Write failing service tests**

Create fixtures for one mapped `automated_check` unit whose authority contains `constraints.target_repository`, whose current attempt has a `DispatchRecord(status="dispatched")`, whose `UnitPrBinding` carries both the PR head and the same armed verification head, and whose current evidence is the pre-CI `runner.pr.opened` row.

Add tests proving:

```python
def test_verifier_named_check_supersedes_worker_evidence_and_completes(...): ...
def test_automated_check_without_verifier_named_check_remains_judgment_required(...): ...
def test_worker_cannot_submit_reserved_verifier_evidence_type(...): ...
@pytest.mark.parametrize("field", ["dispatch_id", "repository", "pr_number", "pr_url", "head_sha"])
def test_named_check_rejects_canonical_binding_mismatch(...): ...
@pytest.mark.parametrize("conclusion", ["neutral", "skipped"])
def test_named_check_non_success_conclusion_cannot_pass(...): ...
def test_named_check_explicit_failure_is_failed(...): ...
def test_named_check_assertion_mismatch_fails_closed(...): ...
def test_named_check_replay_does_not_duplicate_evidence(...): ...
```

The passing payload must include the exact WS-6.4 facts as scalar assertions: dispatch target `AlobarQuest/change-manager`, exact head SHA, check name `Quality`, Ruff `passed`, Pyright error count `0`, and tests passed `105`.

- [ ] **Step 2: Run the focused tests and prove red**

Run:

```bash
.venv/bin/pytest tests/services/test_verifier.py tests/services/test_verifier_evidence.py -q
```

Expected: collection failure because the new evidence type, command, and service do not exist.

- [ ] **Step 3: Add the reserved evidence vocabulary**

Create `src/orchestrator/kernel/evidence_types.py`:

```python
VERIFIER_EVIDENCE_PREFIX = "verifier."
VERIFIER_NAMED_CHECK_EVIDENCE_TYPE = "verifier.github.named_check"
```

In `_store_evidence`, after idempotency replay/version checks but before claim validation, return a `DomainError` with code `evidence_type_reserved` whenever `evidence_type.startswith(VERIFIER_EVIDENCE_PREFIX)`.

In `_store_verifier_evidence`, replace the literal attempt `1` with the locked unit's `attempt_count` in the idempotency command and persisted `Evidence` row. Keep existing verifier findings working.

- [ ] **Step 4: Implement the named-check service**

Create immutable command values in `services/verifier_evidence.py`:

```python
Scalar = str | int | bool

@dataclass(frozen=True)
class NamedCheckAssertion:
    name: str
    expected: Scalar
    observed: Scalar

@dataclass(frozen=True)
class NamedCheckEvidenceCommand:
    unit_id: uuid.UUID
    work_package_revision_id: uuid.UUID
    ac_id: str
    dispatch_id: uuid.UUID
    repository: str
    pr_number: int
    pr_url: str
    head_sha: str
    check_name: str
    conclusion: str
    run_id: str
    run_url: str
    assertions: tuple[NamedCheckAssertion, ...]
    actor: ActorContext
    expected_version: int
    idempotency_key: str
```

`record_named_check_evidence()` must:

1. reject non-verifier actors;
2. lock/load the unit and require `submitted` or `verifying` plus matching version/revision;
3. load the unit's required criteria and require the command AC is mapped and has `evidence_type == "automated_check"`;
4. normalize stored authority and require a non-empty target repository equal to the command repository;
5. load `DispatchRecord` by `dispatch_id` and require matching unit, revision, `runner_attempt == unit.attempt_count`, `status == "dispatched"`, and target repository;
6. load `UnitPrBinding` and require the PR number, `verification_read_attempt == unit.attempt_count`, and armed head equal the command head;
7. require `pr_url == f"https://github.com/{repository}/pull/{pr_number}"`, non-empty run/check identities, a supported conclusion, and one or more unique non-empty scalar assertions;
8. call `append_verifier_evidence` with the reserved type, `stable_ref=run_url`, `source_revision=head_sha`, and a bounded JSON payload containing the validated fields and assertions.

Return validation failures as stable `DomainError` codes without writing evidence. Reuse the existing writer's transaction, idempotency, and supersession semantics.

- [ ] **Step 5: Implement automated-check evaluation**

In `evaluate_criterion()`, handle `criterion.evidence_type.strip().lower() == "automated_check"` before the general judgment/unknown branch:

```python
if evidence_type == "automated_check":
    if evidence is None or evidence.evidence_type != VERIFIER_NAMED_CHECK_EVIDENCE_TYPE:
        return ("judgment_required", None, "automated_check requires verifier named-check evidence")
    return _named_check_result(evidence)
```

`_named_check_result` validates the complete stored shape again. It requires `stable_ref == run_url`, `source_revision == head_sha`, non-empty repository/PR/check/run identities, a non-empty assertion list with unique names, and `expected == observed` for every assertion. `success` passes; explicit GitHub failure conclusions fail; malformed/unknown/`neutral`/`skipped` evidence fails closed.

- [ ] **Step 6: Run focused tests and prove green**

Run:

```bash
.venv/bin/pytest tests/services/test_verifier.py tests/services/test_verifier_evidence.py -q
```

Expected: all selected tests pass with a nonzero collected count.

- [ ] **Step 7: Keep Task 1 uncommitted until the API is wired**

The repository's unreachable-service architecture guard requires the service and its public
verifier-only caller to land atomically. Do not add a temporary unreachable-function allowlist and
do not create an intermediate Task 1 commit. Proceed directly to Task 2 with the green working tree.

---

### Task 2: Verifier Evidence API And Operator Contract

**Files:**
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/api/routes.py`
- Modify: `tests/api/test_verifier_api.py`
- Modify: `docs/operations/verifier.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `NamedCheckAssertion`, `NamedCheckEvidenceCommand`, and `record_named_check_evidence()` from Task 1.
- Produces: `POST /api/v1/work-units/{unit_id}/verifier-evidence/named-check` returning `EvidenceResponse`.

- [ ] **Step 1: Write failing API tests**

Extend `tests/api/test_verifier_api.py` to prove:

```python
def test_openapi_declares_verifier_named_check_evidence_route(...): ...
def test_worker_cannot_record_verifier_named_check_evidence(...): ...
def test_verifier_named_check_api_supersedes_and_replays(...): ...
```

The success test must assert the response type is `verifier.github.named_check`, its attempt equals the unit's current attempt, and its `supersedes_evidence_id` is the pre-CI worker row.

- [ ] **Step 2: Run API tests and prove red**

Run:

```bash
.venv/bin/pytest tests/api/test_verifier_api.py -q
```

Expected: failure because the route/schema are absent.

- [ ] **Step 3: Add strict bounded request schemas**

Add Pydantic models with `extra="forbid"`:

```python
class NamedCheckAssertionModel(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expected: StrictStr | StrictInt | StrictBool
    observed: StrictStr | StrictInt | StrictBool

class VerifierNamedCheckEvidenceCommandModel(CommandBase):
    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1, max_length=100)
    dispatch_id: UUID
    repository: str = Field(min_length=1, max_length=300)
    pr_number: int = Field(gt=0)
    pr_url: str = Field(min_length=1, max_length=2000)
    head_sha: str = Field(min_length=7, max_length=64)
    check_name: str = Field(min_length=1, max_length=200)
    conclusion: Literal[
        "success", "failure", "cancelled", "timed_out",
        "action_required", "neutral", "skipped"
    ]
    run_id: str = Field(min_length=1, max_length=100)
    run_url: str = Field(min_length=1, max_length=2000)
    assertions: list[NamedCheckAssertionModel] = Field(min_length=1, max_length=32)
```

- [ ] **Step 4: Add the verifier-only route**

Add the route beside `/verify`. Convert request assertions to immutable service values, call `record_named_check_evidence`, and pass its result through `_raise_error`. The service, not the HTTP handler, remains the authority boundary.

- [ ] **Step 5: Document the operator sequence and invariant**

Update `docs/operations/verifier.md` with the exact order: wait for named CI, inspect bounded facts, POST verifier named-check evidence, then POST `/verify`. State that the verifier does not call GitHub and that the current armed head is authoritative.

Update the existing evidence-vocabulary invariant in `CLAUDE.md`: `automated_check` is deterministically supported only when current evidence is verifier-owned `verifier.github.named_check`; pre-CI worker evidence continues to require review. Do not claim `automated_test` is fixed.

- [ ] **Step 6: Run focused and full verification**

Run:

```bash
.venv/bin/pytest tests/api/test_verifier_api.py tests/services/test_verifier.py tests/services/test_verifier_evidence.py -q
SECURITY_STANDARDS_DIR=/Users/devon/Projects/security-standards make check
PYTHONPATH="$HOME/Projects/security-standards/src" \
  python3 -m security_scan.cli . --category security
git diff --check
```

Require nonzero test collection, zero failures, and zero security `BLOCK` findings.

- [ ] **Step 7: Commit the atomic service, evaluator, and API repair**

```bash
git add src/orchestrator/kernel/evidence_types.py \
  src/orchestrator/services/verifier_evidence.py \
  src/orchestrator/services/evidence.py \
  src/orchestrator/services/verifier_evaluators.py \
  src/orchestrator/api/schemas.py src/orchestrator/api/routes.py \
  tests/services/test_verifier.py tests/services/test_verifier_evidence.py \
  tests/api/test_verifier_api.py tests/architecture/test_ws32_scope_guards.py \
  docs/operations/verifier.md CLAUDE.md \
  docs/superpowers/plans/2026-07-15-automated-check-verifier-evidence.md
git commit -m "fix: adjudicate trusted automated check evidence"
```

---

### Task 3: Review, Publish, Deploy, And Resume The Existing Unit

**Files:**
- Review the complete branch diff against `origin/main`.
- Update the WS-6.4 run evidence only after terminal observations.

- [ ] **Step 1: Run the portfolio code-review gate**

Review the full diff against `~/Developer/code-standards/STANDARDS.md`. Reject wrong abstractions, duplicated validation without a defense-in-depth reason, weak tests, overengineering, newly added suppressions, or behavior broader than `automated_check` plus verifier named-check evidence.

- [ ] **Step 2: Push and open a reviewed Orchestrator PR**

Push `fix/automated-check-verifier` and open a non-draft PR. Do not merge it. Require terminal CI success and inspect the collected/passed count.

- [ ] **Step 3: Stop at Devon's Orchestrator merge gate**

Devon reviews and merges the repair personally. No agent invokes a merge command or API.

- [ ] **Step 4: Deploy through the existing reviewed Orchestrator production lane**

Build and deploy the merged immutable commit using the same established Coolify application and amd64 image/digest controls. Verify live/ready, OpenAPI route presence, migration head, running digest, and auth behavior. This repair adds no migration.

- [ ] **Step 5: Resume the existing WS-6.4 unit**

Record verifier-owned named-check evidence for unit `4c8c2af4-f963-5511-b3c3-330da81f6373`, revision `f5bfa951-0b21-46d3-a24a-035d105d5a74`, dispatch `14919130-5dd0-438c-8a63-ea65a31832cc`, PR #26, and head `a8297cf18c76549295bf16ae32466fa40e15f19e`. Include the two successful Quality observations and bounded assertions for Ruff, Pyright zero errors, and 105 passed tests.

Then invoke `/verify` once with a fresh idempotency key. Require a passed AC-006 adjudication referencing the new evidence and terminal unit state `completed`.

- [ ] **Step 6: Stop at Devon's Change Manager merge gate**

Devon reviews and merges or closes PR #26 personally. Record the result separately from verifier completion.
