# WS-P2.1 Implementation Plan — Tasks 14–17

> **Grounding note.** Every path, symbol, and constant below was read from the live tree: `src/orchestrator/api/routes.py` (44 route decorators), `src/orchestrator/api/schemas.py` (`ObservationCommandModel`), `src/orchestrator/services/observations.py` (`SECRET_KEY_PARTS`, `_fact_identity`, the dedup/conflict branches at ~154–179), `src/orchestrator/persistence/models.py` (`OBSERVATION_*` enums at 60–102; `ReleaseArtifactBinding` / `DeploymentObservation` columns), `src/orchestrator/kernel/transitions.py` (`WORKER_EDGES`), `src/orchestrator/kernel/leases.py` (`LEASE_DURATION = timedelta(minutes=15)`, no env override), `src/orchestrator/config.py` (`dispatch_enabled: bool = False`), `src/orchestrator/main.py` (`load_auth_config()` env contract), `tests/api/conftest.py` (`db_client`, registry/M2M fixture shape), `tests/architecture/test_scope_guards.py`, and `/Users/devon/Projects/factory-runner/src/factory_runner/{cli,client}.py`.
>
> **Assumed landed from Tasks 1–13 (canonical cross-task contracts):**
> - Migration `0014_wsp21_recovery_controls` creates `reconciliation_conditions`, `reconciliation_resolutions`, `unit_pr_binding`, and the partial unique index `uq_evidence_unsuperseded_head` on `evidence (work_package_revision_id, work_unit_id, ac_id) WHERE supersedes_evidence_id IS NULL`.
> - `Settings.reconcile_split_brain_stall_seconds` (default **900**, env `ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS`) — Task 1.
> - `record_reconciliation_condition(session, command: ConditionCommand) -> ConditionOutcome | DomainError`, where `ConditionOutcome` is a frozen dataclass `(condition, suppressed: bool)`.
> - `POST /api/v1/reconciliation/detect` (returns `conditions_recorded` / `skipped_correlations` / `suppressed_duplicates`); on-ingest condition recording for `github_pr` / `github_check`.
> - `POST /api/v1/work-units/{id}/attempts/{attempt}/recover-evidence` — implemented in `services/evidence.py` (Task 9); **there is no `services/evidence_recovery.py`**.
> - `POST /review/reconciliation/conditions/{condition_id}/resolution` — HUMAN-only, `/review` router (Task 5b).
> - `GET /api/v1/dead-letter` + `POST /api/v1/work-units/{id}/requeue` (Task 9/10); `GET /api/v1/consistency-check` (Task 13).
> - `tests/architecture/test_scope_guards.py::test_production_get_route_inventory_is_explicit` — the pinned **GET** inventory — is created in **Task 10**. Task 14 extends it.

---

### Task 14: `GET /api/v1/in-flight-units` — the runner's read surface

**Files:**
- **Create:** `src/orchestrator/services/in_flight.py`
- **Modify:** `src/orchestrator/api/schemas.py`, `src/orchestrator/api/routes.py`, `tests/architecture/test_scope_guards.py`
- **Test:** `tests/services/test_in_flight.py`, `tests/api/test_in_flight_units_api.py`

**Interfaces:**
- **Consumes:** `sqlalchemy.orm.Session`; `orchestrator.persistence.models.{WorkUnit, UnitPrBinding, ReleaseArtifactBinding, DeploymentObservation}`; `orchestrator.kernel.states.WorkUnitState`; `orchestrator.api.dependencies.{ActorDep, SessionDep}`.
- **Produces:** `in_flight_snapshot(session: Session, *, completed_binding_window_hours: int = 168) -> InFlightSnapshot`; dataclasses `InFlightUnitView`, `ReleaseBindingView`, `InFlightSnapshot`; Pydantic `InFlightUnitModel`, `ReleaseBindingModel`, `InFlightUnitsResponse`; route function `in_flight_units` at `GET /api/v1/in-flight-units`.

**Why release bindings and recently-completed units are in scope (load-bearing):** an implementation unit that carries a `ReleaseArtifactBinding` is `COMPLETED` and therefore *not* in-flight. Without them the runner is structurally blind to §1.4's "deploy nobody reported" case — a binding with **no** `DeploymentObservation` and thus **no** post-deploy verification unit. That case is exactly what ADR-0002 rejects Alternative A for. `has_post_deploy_unit=False` in this payload **is** the signal.

- [ ] **Step 14.1 — Failing service test.** Create `tests/services/test_in_flight.py`:

```python
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import UnitPrBinding
from orchestrator.services.in_flight import in_flight_snapshot
from tests.services.conftest import seed_release_binding, seed_unit  # existing helpers


def test_in_flight_snapshot_carries_units_their_pr_binding_and_release_bindings(
    migrated_session: Session,
) -> None:
    executing = seed_unit(migrated_session, state=WorkUnitState.EXECUTING)
    migrated_session.add(
        UnitPrBinding(
            work_unit_id=executing.id,
            pr_number=41,
            head_sha="a" * 40,
            verification_read_head_sha=None,
            updated_at=datetime.now(UTC),
        )
    )
    completed = seed_unit(migrated_session, state=WorkUnitState.COMPLETED)
    binding = seed_release_binding(
        migrated_session, work_unit=completed, artifact_digest="sha256:" + "b" * 64
    )
    migrated_session.flush()

    snapshot = in_flight_snapshot(migrated_session)

    assert [view.work_unit_id for view in snapshot.units] == [executing.id]
    assert snapshot.units[0].pr_number == 41
    assert snapshot.units[0].head_sha == "a" * 40
    # The COMPLETED unit is NOT in-flight, but its binding must still be visible:
    # a binding with no DeploymentObservation is the "deploy nobody reported" case.
    assert [view.binding_id for view in snapshot.release_bindings] == [binding.id]
    assert snapshot.release_bindings[0].work_unit_state == "completed"
    assert snapshot.release_bindings[0].has_post_deploy_unit is False


def test_in_flight_snapshot_excludes_terminal_units_without_bindings(
    migrated_session: Session,
) -> None:
    seed_unit(migrated_session, state=WorkUnitState.COMPLETED)
    seed_unit(migrated_session, state=WorkUnitState.CANCELLED)
    migrated_session.flush()

    snapshot = in_flight_snapshot(migrated_session)

    assert snapshot.units == ()
    assert snapshot.release_bindings == ()
```

- [ ] **Step 14.2 — Run it, confirm the expected failure.** `uv run pytest tests/services/test_in_flight.py -x` → `ModuleNotFoundError: No module named 'orchestrator.services.in_flight'`.

- [ ] **Step 14.3 — Minimal implementation.** Create `src/orchestrator/services/in_flight.py`:

```python
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import (
    DeploymentObservation,
    ReleaseArtifactBinding,
    UnitPrBinding,
    WorkUnit,
)

IN_FLIGHT_STATES = frozenset(
    {
        WorkUnitState.READY,
        WorkUnitState.CLAIMED,
        WorkUnitState.EXECUTING,
        WorkUnitState.BLOCKED,
        WorkUnitState.AWAITING_APPROVAL,
        WorkUnitState.SUBMITTED,
        WorkUnitState.VERIFYING,
        WorkUnitState.AWAITING_REVIEW,
        WorkUnitState.REVISION_REQUIRED,
    }
)


@dataclass(frozen=True)
class InFlightUnitView:
    work_unit_id: UUID
    unit_key: str
    state: str
    version: int
    attempt_count: int
    work_package_revision_id: UUID
    pr_number: int | None
    head_sha: str | None
    verification_read_head_sha: str | None


@dataclass(frozen=True)
class ReleaseBindingView:
    binding_id: UUID
    work_unit_id: UUID
    work_unit_state: str
    source_repository: str
    implementation_pr_number: int
    artifact_digest: str
    has_post_deploy_unit: bool
    post_deploy_unit_state: str | None
    post_deploy_unit_created_at: datetime | None


@dataclass(frozen=True)
class InFlightSnapshot:
    units: tuple[InFlightUnitView, ...]
    release_bindings: tuple[ReleaseBindingView, ...]


def in_flight_snapshot(
    session: Session, *, completed_binding_window_hours: int = 168
) -> InFlightSnapshot:
    """Read-only. Never writes, never transitions — the runner's entire view of reality."""
    in_flight = session.execute(
        select(WorkUnit, UnitPrBinding)
        .outerjoin(UnitPrBinding, UnitPrBinding.work_unit_id == WorkUnit.id)
        .where(WorkUnit.state.in_([state.value for state in IN_FLIGHT_STATES]))
        .order_by(WorkUnit.created_at, WorkUnit.id)
    ).all()
    units = tuple(
        InFlightUnitView(
            work_unit_id=unit.id,
            unit_key=unit.unit_key,
            state=unit.state,
            version=unit.version,
            attempt_count=unit.attempt_count,
            work_package_revision_id=unit.work_package_revision_id,
            pr_number=binding.pr_number if binding is not None else None,
            head_sha=binding.head_sha if binding is not None else None,
            verification_read_head_sha=(
                binding.verification_read_head_sha if binding is not None else None
            ),
        )
        for unit, binding in in_flight
    )

    cutoff = datetime.now(UTC) - timedelta(hours=completed_binding_window_hours)
    rows = session.execute(
        select(ReleaseArtifactBinding, WorkUnit, DeploymentObservation)
        .join(WorkUnit, WorkUnit.id == ReleaseArtifactBinding.work_unit_id)
        .outerjoin(
            DeploymentObservation,
            DeploymentObservation.release_artifact_binding_id == ReleaseArtifactBinding.id,
        )
        .where(
            WorkUnit.state.in_([state.value for state in IN_FLIGHT_STATES])
            | (WorkUnit.updated_at >= cutoff)
        )
        .order_by(ReleaseArtifactBinding.recorded_at, ReleaseArtifactBinding.id)
    ).all()
    bindings: list[ReleaseBindingView] = []
    for binding, unit, observation in rows:
        post_unit = (
            session.get(WorkUnit, observation.post_deploy_work_unit_id)
            if observation is not None
            else None
        )
        bindings.append(
            ReleaseBindingView(
                binding_id=binding.id,
                work_unit_id=unit.id,
                work_unit_state=unit.state,
                source_repository=binding.source_repository,
                implementation_pr_number=binding.implementation_pr_number,
                artifact_digest=binding.artifact_digest,
                has_post_deploy_unit=post_unit is not None,
                post_deploy_unit_state=post_unit.state if post_unit is not None else None,
                post_deploy_unit_created_at=(
                    post_unit.created_at if post_unit is not None else None
                ),
            )
        )
    return InFlightSnapshot(units=units, release_bindings=tuple(bindings))
```

- [ ] **Step 14.4 — Run, expect pass.** `uv run pytest tests/services/test_in_flight.py -q` → 2 passed.

- [ ] **Step 14.5 — Failing API test.** Create `tests/api/test_in_flight_units_api.py`:

```python
from fastapi.testclient import TestClient

from tests.api.test_lifecycle_api import HUMAN, SYSTEM, WORKER


def test_in_flight_units_route_is_declared_with_a_schema(client: TestClient) -> None:
    document = client.get("/openapi.json").json()
    operation = document["paths"]["/api/v1/in-flight-units"]["get"]

    assert set(operation["responses"]) >= {"200", "401", "403"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert "post" not in document["paths"]["/api/v1/in-flight-units"]


def test_in_flight_units_requires_a_machine_actor(client: TestClient) -> None:
    assert client.get("/api/v1/in-flight-units").status_code == 401
    assert client.get("/api/v1/in-flight-units", headers=HUMAN).status_code in {401, 403}


def test_in_flight_units_returns_units_and_release_bindings(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/in-flight-units", headers=SYSTEM)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"units", "release_bindings"}
    assert isinstance(body["units"], list)
    assert isinstance(body["release_bindings"], list)


def test_in_flight_units_is_readable_by_the_worker_credential_but_writes_nothing(
    db_client: TestClient,
) -> None:
    before = db_client.get("/api/v1/in-flight-units", headers=SYSTEM).json()
    db_client.get("/api/v1/in-flight-units", headers=WORKER)
    after = db_client.get("/api/v1/in-flight-units", headers=SYSTEM).json()

    assert before == after
```

- [ ] **Step 14.6 — Run, confirm failure.** `uv run pytest tests/api/test_in_flight_units_api.py -x` → `KeyError: '/api/v1/in-flight-units'`.

- [ ] **Step 14.7 — Add the schemas.** In `src/orchestrator/api/schemas.py` (alongside `ObservationResponse`):

```python
class InFlightUnitModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    work_unit_id: UUID
    unit_key: str
    state: str
    version: int
    attempt_count: int
    work_package_revision_id: UUID
    pr_number: int | None
    head_sha: str | None
    verification_read_head_sha: str | None


class ReleaseBindingModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    binding_id: UUID
    work_unit_id: UUID
    work_unit_state: str
    source_repository: str
    implementation_pr_number: int
    artifact_digest: str
    has_post_deploy_unit: bool
    post_deploy_unit_state: str | None
    post_deploy_unit_created_at: datetime | None


class InFlightUnitsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    units: list[InFlightUnitModel]
    release_bindings: list[ReleaseBindingModel]
```

- [ ] **Step 14.8 — Add the route.** In `src/orchestrator/api/routes.py`, beside `observations` (the closest read precedent — `_actor: ActorDep` proves the auth requirement without using the actor):

```python
@router.get("/in-flight-units", response_model=InFlightUnitsResponse)
def in_flight_units(_actor: ActorDep, session: SessionDep) -> object:
    return in_flight_snapshot(session)
```

  Imports: `from orchestrator.api.schemas import InFlightUnitsResponse` and `from orchestrator.services.in_flight import in_flight_snapshot`.

- [ ] **Step 14.9 — Run, expect pass.** `uv run pytest tests/api/test_in_flight_units_api.py -q` → 4 passed.

- [ ] **Step 14.10 — Extend the pinned GET inventory (deliberately).** The GET pin `test_production_get_route_inventory_is_explicit` **already exists** — Task 10 created it in `tests/architecture/test_scope_guards.py` (the pre-WS-P2.1 file pinned **POSTs only**), and it already carries `/api/v1/dead-letter` (Task 10) and `/api/v1/consistency-check` (Task 13). This step **adds one literal** to that existing set:

```python
def test_production_get_route_inventory_is_explicit() -> None:
    paths = create_app().openapi()["paths"]
    observed = {
        path
        for path, operations in paths.items()
        if "get" in operations and path.startswith("/api/v1/")
    }
    assert observed == {
        "/api/v1/package-intakes/{revision_id}",
        "/api/v1/package-intakes/{revision_id}/decomposition-proposals",
        "/api/v1/decomposition-proposals/{proposal_id}",
        "/api/v1/work-units/{unit_id}/readiness",
        "/api/v1/work-units/{unit_id}/runner-brief",
        "/api/v1/work-units/{unit_id}/context-snapshots",
        "/api/v1/work-units/{unit_id}/infra-lane-links",
        "/api/v1/work-units/{unit_id}/release-artifacts",
        "/api/v1/work-units/{unit_id}/evidence",
        "/api/v1/work-units/{unit_id}/history",
        "/api/v1/release-artifacts/{binding_id}/deployment-observations",
        "/api/v1/observations",
        "/api/v1/knowledge-promotion-proposals",
        "/api/v1/status-ledger",
        "/api/v1/event-publications",
        "/api/v1/dead-letter",         # Task 10
        "/api/v1/consistency-check",   # Task 13
        "/api/v1/in-flight-units",     # <-- THIS TASK: the only line Task 14 adds
    }
```

  The first fifteen are the GET surface on `main`. If the assertion diff shows a GET that Tasks 1–13 added and this literal omits, **add it** — never delete a route to make the pin green.

- [ ] **Step 14.11 — Run the whole architecture suite.** `uv run pytest tests/architecture -q` → all pass (the POST inventory is untouched; `in-flight-units` declares no POST).

- [ ] **Step 14.12 — Commit.**
```bash
git add src/orchestrator/services/in_flight.py src/orchestrator/api/schemas.py src/orchestrator/api/routes.py tests/services/test_in_flight.py tests/api/test_in_flight_units_api.py tests/architecture/test_scope_guards.py && git commit -m "feat(api): add read-only GET /api/v1/in-flight-units with release bindings (WS-P2.1 AC-009)"
```

---

### Task 15: The report-only reconciliation runner (AC-009)

**Files:**
- **Create:** `src/reconciliation_runner/__init__.py`, `src/reconciliation_runner/facts.py`, `src/reconciliation_runner/client.py`, `src/reconciliation_runner/cli.py`
- **Modify:** `pyproject.toml`
- **Test:** `tests/reconciliation_runner/__init__.py`, `tests/reconciliation_runner/test_facts.py`, `tests/reconciliation_runner/test_runner_pass.py`, `tests/services/test_reconciliation_runner_contract.py`, `tests/architecture/test_reconciliation_runner_isolation.py`

**Interfaces:**
- **Consumes:** `httpx`, `pydantic`, `typer` — **and nothing else**. It imports **no** `orchestrator.*` module (crucially not `orchestrator.persistence`, which would give it a direct DB write path and gut report-only). Over the wire it consumes `GET /api/v1/in-flight-units` (Task 14) and a **fixture** reality file (live GitHub/Coolify wiring is explicitly out of scope).
- **Produces:** `NormalizedFacts` (pydantic, `extra="forbid"`); `fact_digest(facts) -> str`; `pr_observation(...)`, `check_observation(...)`, `deploy_observation(...) -> dict[str, Any]` (bodies matching `ObservationCommandModel`); `ReconciliationClient` with exactly `in_flight_units()`, `record_observation(payload)`, `detect(payload)`; console script `reconciliation-runner = "reconciliation_runner.cli:app"`.

**The two halves of the observation contract, and why they ship together.** `_fact_identity` (`services/observations.py:355-369`) includes `observed_at`. A content-addressed `source_reference` paired with a **wall-clock** `observed_at` therefore hits the `same_source`-but-different-`fact_hash` branch (`observations.py:165-179`) on the *second unchanged pull* and raises `observation_conflict` — every pass, forever. So: `source_reference = pr:{number}@{head_sha}:{sha256(normalized_facts)}` **and** `observed_at` is the **upstream** timestamp (`PR.updated_at`, `check_run.completed_at`, deployment completion time), never the pull time. Unchanged reality then re-mints the *identical* `(source_reference, fact_hash)` and the content dedup early-returns (`observations.py:154-164`): no new row, no conflict, no unbounded growth.

- [ ] **Step 15.1 — Failing fact test.** Create `tests/reconciliation_runner/__init__.py` (empty) and `tests/reconciliation_runner/test_facts.py`:

```python
from datetime import UTC, datetime

from reconciliation_runner.facts import (
    NormalizedFacts,
    check_observation,
    deploy_observation,
    fact_digest,
    pr_observation,
)

UPDATED_AT = datetime(2026, 7, 10, 9, 15, tzinfo=UTC)
HEAD = "a" * 40
UNIT = "3f6b6c68-0000-4000-8000-000000000001"


def test_pr_source_reference_is_content_addressed_over_the_normalized_facts() -> None:
    body = pr_observation(
        work_unit_id=UNIT, pr_number=41, head_sha=HEAD, state="open", merged=False,
        observed_at=UPDATED_AT,
    )
    facts = body["facts"]

    assert body["source_reference"] == f"pr:41@{HEAD}:{fact_digest(facts)}"
    # observed_at comes from upstream, never from the runner's clock.
    assert body["observed_at"] == UPDATED_AT.isoformat()
    assert facts["observed_at"] == UPDATED_AT.isoformat()


def test_unchanged_reality_repulled_is_byte_identical() -> None:
    first = pr_observation(
        work_unit_id=UNIT, pr_number=41, head_sha=HEAD, state="open", merged=False,
        observed_at=UPDATED_AT,
    )
    second = pr_observation(
        work_unit_id=UNIT, pr_number=41, head_sha=HEAD, state="open", merged=False,
        observed_at=UPDATED_AT,
    )

    assert first == second
    assert first["idempotency_key"] == second["idempotency_key"]


def test_changed_reality_mints_a_new_source_reference() -> None:
    unchanged = pr_observation(
        work_unit_id=UNIT, pr_number=41, head_sha=HEAD, state="open", merged=False,
        observed_at=UPDATED_AT,
    )
    merged = pr_observation(
        work_unit_id=UNIT, pr_number=41, head_sha=HEAD, state="closed", merged=True,
        observed_at=datetime(2026, 7, 10, 11, 0, tzinfo=UTC),
    )

    assert merged["source_reference"] != unchanged["source_reference"]


def test_normalized_facts_reject_raw_provider_keys() -> None:
    # SECRET_KEY_PARTS contains "log", so GitHub's standard logs_url would be REJECTED
    # by the observation service outright. The schema forbids it at the source.
    try:
        NormalizedFacts(observed_at=UPDATED_AT, logs_url="https://api.github.com/x/logs")
    except ValueError:
        return
    raise AssertionError("NormalizedFacts must forbid raw provider payload keys")


def test_check_and_deploy_references_use_their_own_namespaces() -> None:
    check = check_observation(
        work_unit_id=UNIT, check_name="Quality", head_sha=HEAD, conclusion="success",
        observed_at=UPDATED_AT,
    )
    deploy = deploy_observation(
        binding_id="7c1c0f4a-0000-4000-8000-000000000002",
        artifact_digest="sha256:" + "b" * 64,
        deploy_status="succeeded",
        environment="production",
        observed_at=UPDATED_AT,
    )

    assert check["source_reference"].startswith(f"check:Quality@{HEAD}:")
    assert deploy["source_reference"].startswith("deploy:7c1c0f4a-0000-4000-8000-000000000002@")
    assert check["subject_type"] == "work_unit"
    assert deploy["subject_type"] == "release_binding"
```

- [ ] **Step 15.2 — Run, confirm failure.** `uv run pytest tests/reconciliation_runner -x` → `ModuleNotFoundError: No module named 'reconciliation_runner'`.

- [ ] **Step 15.3 — Implement `facts.py`.** Create `src/reconciliation_runner/__init__.py` (empty) and `src/reconciliation_runner/facts.py`:

```python
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

# Every constant below is a member of the orchestrator's existing enums
# (persistence/models.py:60-102) — the runner needs NO schema migration.
SOURCE_SYSTEM_GITHUB = "github"
SOURCE_SYSTEM_DEPLOY = "deployment_observation"
TRUST_CLASSIFICATION = "delivery_system"


class NormalizedFacts(BaseModel):
    """The runner's ONLY fact shape. Raw provider payloads are never forwarded:
    SECRET_KEY_PARTS (services/observations.py:33-44) contains "log", so GitHub's
    standard `logs_url` key is rejected outright by the observation service."""

    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    pr_number: int | None = None
    head_sha: str | None = None
    state: str | None = None
    merged: bool | None = None
    check_name: str | None = None
    conclusion: str | None = None
    deploy_status: str | None = None
    artifact_digest: str | None = None

    def as_facts(self) -> dict[str, Any]:
        payload: dict[str, Any] = self.model_dump(exclude_none=True)
        payload["observed_at"] = self.observed_at.isoformat()
        return payload


def fact_digest(facts: dict[str, Any]) -> str:
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _observation(
    *,
    source_system: str,
    source_reference: str,
    subject_type: str,
    subject_reference: str,
    observation_type: str,
    status: str,
    environment: str | None,
    summary: str,
    facts: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        # Content-addressed key: an exact re-delivery dedups on idempotency_key (AC-007);
        # an unchanged re-pull dedups on (source_reference, fact_hash).
        "idempotency_key": f"reconcile:{source_reference}",
        "expected_version": None,
        "source_system": source_system,
        "source_reference": source_reference,
        "source_url": None,
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": subject_type,
        "subject_reference": subject_reference,
        "environment": environment,
        "observation_type": observation_type,
        "status": status,
        "severity": "info",
        # UPSTREAM timestamp. _fact_identity (observations.py:355-369) hashes observed_at,
        # so a wall-clock value here would guarantee observation_conflict on pass 2.
        "observed_at": observed_at.isoformat(),
        "summary": summary,
        "facts": facts,
        "payload_digest": None,
    }


def pr_observation(
    *,
    work_unit_id: str,
    pr_number: int,
    head_sha: str,
    state: str,
    merged: bool,
    observed_at: datetime,  # PR.updated_at
) -> dict[str, Any]:
    facts = NormalizedFacts(
        pr_number=pr_number, head_sha=head_sha, state=state, merged=merged,
        observed_at=observed_at,
    ).as_facts()
    return _observation(
        source_system=SOURCE_SYSTEM_GITHUB,
        source_reference=f"pr:{pr_number}@{head_sha}:{fact_digest(facts)}",
        subject_type="work_unit",
        subject_reference=work_unit_id,
        observation_type="github_pr",
        status="observed",
        environment=None,
        summary=f"pull request {pr_number} is {state}",
        facts=facts,
        observed_at=observed_at,
    )


def check_observation(
    *,
    work_unit_id: str,
    check_name: str,
    head_sha: str,
    conclusion: str,
    observed_at: datetime,  # check_run.completed_at
) -> dict[str, Any]:
    facts = NormalizedFacts(
        check_name=check_name, head_sha=head_sha, conclusion=conclusion, observed_at=observed_at
    ).as_facts()
    return _observation(
        source_system=SOURCE_SYSTEM_GITHUB,
        source_reference=f"check:{check_name}@{head_sha}:{fact_digest(facts)}",
        subject_type="work_unit",
        subject_reference=work_unit_id,
        observation_type="github_check",
        status="passed" if conclusion == "success" else "failed",
        environment=None,
        summary=f"check {check_name} concluded {conclusion}",
        facts=facts,
        observed_at=observed_at,
    )


def deploy_observation(
    *,
    binding_id: str,
    artifact_digest: str,
    deploy_status: str,
    environment: str,
    observed_at: datetime,  # deployment completion time
) -> dict[str, Any]:
    facts = NormalizedFacts(
        deploy_status=deploy_status, artifact_digest=artifact_digest, observed_at=observed_at
    ).as_facts()
    return _observation(
        source_system=SOURCE_SYSTEM_DEPLOY,
        source_reference=f"deploy:{binding_id}@{artifact_digest}:{fact_digest(facts)}",
        subject_type="release_binding",
        subject_reference=binding_id,
        observation_type="deployment",
        status="passed" if deploy_status == "succeeded" else "failed",
        environment=environment,
        summary=f"deployment of {artifact_digest} {deploy_status}",
        facts=facts,
        observed_at=observed_at,
    )
```

- [ ] **Step 15.4 — Run, expect pass.** `uv run pytest tests/reconciliation_runner/test_facts.py -q` → 5 passed.

- [ ] **Step 15.5 — Failing endpoint-set test.** Create `tests/reconciliation_runner/test_runner_pass.py`:

```python
import json
from pathlib import Path

import httpx
import pytest

from reconciliation_runner.client import (
    ALLOWED_WRITE_ENDPOINTS,
    ForbiddenEndpointError,
    ReconciliationClient,
)
from reconciliation_runner.cli import run_pass

HEAD = "a" * 40
UNIT = "3f6b6c68-0000-4000-8000-000000000001"
BINDING = "7c1c0f4a-0000-4000-8000-000000000002"

IN_FLIGHT = {
    "units": [
        {
            "work_unit_id": UNIT, "unit_key": "impl-1", "state": "submitted", "version": 4,
            "attempt_count": 1, "work_package_revision_id": UNIT,
            "pr_number": 41, "head_sha": HEAD, "verification_read_head_sha": HEAD,
        }
    ],
    "release_bindings": [
        {
            "binding_id": BINDING, "work_unit_id": UNIT, "work_unit_state": "completed",
            "source_repository": "AlobarQuest/orchestrator", "implementation_pr_number": 41,
            "artifact_digest": "sha256:" + "b" * 64, "has_post_deploy_unit": False,
            "post_deploy_unit_state": None, "post_deploy_unit_created_at": None,
        }
    ],
}

REALITY = {
    "pull_requests": [
        {"work_unit_id": UNIT, "pr_number": 41, "head_sha": HEAD, "state": "closed",
         "merged": True, "updated_at": "2026-07-10T11:00:00+00:00"}
    ],
    "check_runs": [
        {"work_unit_id": UNIT, "check_name": "Quality", "head_sha": HEAD,
         "conclusion": "success", "completed_at": "2026-07-10T10:00:00+00:00"}
    ],
    "deployments": [
        {"binding_id": BINDING, "artifact_digest": "sha256:" + "b" * 64,
         "deploy_status": "succeeded", "environment": "production",
         "completed_at": "2026-07-10T10:30:00+00:00"}
    ],
}


def _recording_transport(seen: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/api/v1/in-flight-units":
            return httpx.Response(200, json=IN_FLIGHT)
        if request.url.path == "/api/v1/observations":
            return httpx.Response(201, json={"id": UNIT})
        if request.url.path == "/api/v1/reconciliation/detect":
            return httpx.Response(
                200,
                json={"conditions_recorded": 2, "skipped_correlations": 0,
                      "suppressed_duplicates": 0},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_a_full_pass_touches_exactly_the_two_allowed_write_endpoints(tmp_path: Path) -> None:
    reality = tmp_path / "reality.json"
    reality.write_text(json.dumps(REALITY))
    seen: list[str] = []
    client = ReconciliationClient(
        base_url="https://sds.invalid", credential_key_id="reconciliation-key",
        token="fixture-token", transport=_recording_transport(seen),
    )

    summary = run_pass(client, reality)

    writes = {entry for entry in seen if entry.startswith("POST")}
    assert writes == {
        "POST /api/v1/observations",
        "POST /api/v1/reconciliation/detect",
    }
    assert writes == {f"POST {path}" for path in ALLOWED_WRITE_ENDPOINTS}
    assert not any("deployment-observations" in entry for entry in seen)
    assert not any(entry.startswith(("PUT", "PATCH", "DELETE")) for entry in seen)
    assert summary["observations_pushed"] == 3
    assert summary["conditions_recorded"] == 2


def test_the_client_structurally_refuses_any_other_write(tmp_path: Path) -> None:
    client = ReconciliationClient(
        base_url="https://sds.invalid", credential_key_id="reconciliation-key",
        token="fixture-token", transport=_recording_transport([]),
    )

    with pytest.raises(ForbiddenEndpointError):
        client.post_raw(f"/api/v1/release-artifacts/{BINDING}/deployment-observations", {})
    with pytest.raises(ForbiddenEndpointError):
        client.post_raw(f"/api/v1/work-units/{UNIT}/commands/submit", {})
```

- [ ] **Step 15.6 — Run, confirm failure.** `uv run pytest tests/reconciliation_runner/test_runner_pass.py -x` → `ModuleNotFoundError: reconciliation_runner.client`.

- [ ] **Step 15.7 — Implement `client.py`** (mirrors `factory_runner/client.py`: `httpx.Client` with `base_url` + bearer + `X-Credential-Key-Id`, injectable transport; the allowlist is enforced in `_request`, so report-only is a *runtime* property, not just a test convention):

```python
from __future__ import annotations

from typing import Any

import httpx

# The report-only mandate, in code. /observations appends a row; /reconciliation/detect
# appends conditions. Neither mints a WorkUnit nor sets lifecycle state. Any other write —
# above all …/deployment-observations, which mints a WorkUnit + 5 Evidence rows per push
# (deployment_observations.py:156-231) — is structurally unreachable from this client.
ALLOWED_WRITE_ENDPOINTS = frozenset(
    {"/api/v1/observations", "/api/v1/reconciliation/detect"}
)
IN_FLIGHT_ENDPOINT = "/api/v1/in-flight-units"


class ReconciliationError(RuntimeError):
    pass


class ForbiddenEndpointError(ReconciliationError):
    pass


class ReconciliationClient:
    def __init__(
        self,
        *,
        base_url: str,
        credential_key_id: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "X-Credential-Key-Id": credential_key_id,
            },
            timeout=30.0,
            transport=transport,
        )

    def in_flight_units(self) -> dict[str, Any]:
        return self._request("GET", IN_FLIGHT_ENDPOINT).json()

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post_raw("/api/v1/observations", payload)

    def detect(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.post_raw("/api/v1/reconciliation/detect", payload or {})

    def post_raw(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path not in ALLOWED_WRITE_ENDPOINTS:
            raise ForbiddenEndpointError(f"runner may not write to {path}")
        return self._request("POST", path, json=payload).json()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if method not in {"GET", "POST"}:
            raise ForbiddenEndpointError(f"runner may not use {method}")
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ReconciliationError(f"orchestrator request failed: {response.status_code}")
        return response
```

- [ ] **Step 15.8 — Implement `cli.py`** (typer, bearer from env — the factory-runner pattern; reality comes from a **fixture file**, live GitHub/Coolify wiring is out of scope):

```python
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from reconciliation_runner.client import ReconciliationClient
from reconciliation_runner.facts import check_observation, deploy_observation, pr_observation

app = typer.Typer(no_args_is_help=True)


@app.callback(invoke_without_command=True)
def main() -> None:
    return None


def _client(orchestrator_url: str, credential_key_id: str) -> ReconciliationClient:
    token = os.environ.get("RECONCILIATION_RUNNER_TOKEN")
    if not token:
        typer.echo("RECONCILIATION_RUNNER_TOKEN environment variable is required", err=True)
        raise typer.Exit(code=1)
    return ReconciliationClient(
        base_url=orchestrator_url, credential_key_id=credential_key_id, token=token
    )


def _reality(path: Path) -> dict[str, Any]:
    """Reality is read from a fixture file. Live GitHub/Coolify wiring is out of scope
    for WS-P2.1: this task proves the observation contract and the report-only mandate."""
    return json.loads(path.read_text())


def run_pass(client: ReconciliationClient, reality_path: Path) -> dict[str, Any]:
    snapshot = client.in_flight_units()
    known_units = {unit["work_unit_id"] for unit in snapshot["units"]}
    known_bindings = {binding["binding_id"] for binding in snapshot["release_bindings"]}
    reality = _reality(reality_path)

    bodies: list[dict[str, Any]] = []
    for pull in reality.get("pull_requests", []):
        if pull["work_unit_id"] not in known_units:
            continue
        bodies.append(
            pr_observation(
                work_unit_id=pull["work_unit_id"], pr_number=pull["pr_number"],
                head_sha=pull["head_sha"], state=pull["state"], merged=pull["merged"],
                observed_at=datetime.fromisoformat(pull["updated_at"]),
            )
        )
    for check in reality.get("check_runs", []):
        if check["work_unit_id"] not in known_units:
            continue
        bodies.append(
            check_observation(
                work_unit_id=check["work_unit_id"], check_name=check["check_name"],
                head_sha=check["head_sha"], conclusion=check["conclusion"],
                observed_at=datetime.fromisoformat(check["completed_at"]),
            )
        )
    for deployment in reality.get("deployments", []):
        if deployment["binding_id"] not in known_bindings:
            continue
        bodies.append(
            deploy_observation(
                binding_id=deployment["binding_id"],
                artifact_digest=deployment["artifact_digest"],
                deploy_status=deployment["deploy_status"],
                environment=deployment["environment"],
                observed_at=datetime.fromisoformat(deployment["completed_at"]),
            )
        )

    for body in bodies:
        client.record_observation(body)
    detected = client.detect()
    return {"observations_pushed": len(bodies), **detected}


@app.command("run-pass")
def run_pass_command(
    reality: Annotated[Path, typer.Option(help="Fixture reality file (JSON).")],
    orchestrator_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    credential_key_id: Annotated[str, typer.Option()] = "reconciliation-key",
) -> None:
    summary = run_pass(_client(orchestrator_url, credential_key_id), reality)
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))
```

- [ ] **Step 15.9 — Register the console script.** In `pyproject.toml`, extend `[project.scripts]` and make package discovery explicit (auto-discovery would still find both under `src/`, but the second top-level package makes the intent worth pinning):

```toml
[project.scripts]
orchestrator = "orchestrator.cli:app"
reconciliation-runner = "reconciliation_runner.cli:app"

[tool.setuptools.packages.find]
where = ["src"]
```

  No new dependencies: `httpx`, `pydantic`, `typer` are already in `[project.dependencies]`.

- [ ] **Step 15.10 — Run, expect pass.** `uv sync && uv run pytest tests/reconciliation_runner -q` → 7 passed. Then `uv run reconciliation-runner --help` → shows `run-pass`.

- [ ] **Step 15.11 — Commit.**
```bash
git add src/reconciliation_runner tests/reconciliation_runner pyproject.toml && git commit -m "feat(runner): add report-only reconciliation runner with content-addressed observations (WS-P2.1 AC-009)"
```

- [ ] **Step 15.12 — Failing Postgres-backed contract test.** The dedup/conflict behaviour and the `SECRET_KEY_PARTS` rejection are properties of the **real** service, so this test drives the real API with the runner's real bodies. Create `tests/services/test_reconciliation_runner_contract.py`:

```python
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Observation, WorkUnit
from reconciliation_runner.facts import pr_observation
from tests.api.test_lifecycle_api import SYSTEM
from tests.services.conftest import seed_unit  # existing helper

HEAD = "a" * 40
UPDATED_AT = datetime(2026, 7, 10, 9, 15, tzinfo=UTC)


def _observation_count(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(Observation)) or 0


def test_unchanged_reality_repulled_dedups_and_grows_no_rows(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    with Session(migrated_engine) as session:
        unit = seed_unit(session, state="submitted")
        session.commit()
        unit_id = str(unit.id)

    body = pr_observation(
        work_unit_id=unit_id, pr_number=41, head_sha=HEAD, state="open", merged=False,
        observed_at=UPDATED_AT,
    )
    first = db_client.post("/api/v1/observations", json=body, headers=SYSTEM)
    after_first = _observation_count(migrated_engine)

    second = db_client.post("/api/v1/observations", json=body, headers=SYSTEM)

    assert first.status_code == 201
    # Re-pull of unchanged reality: dedups. NOT a 409 observation_conflict — which is
    # exactly what a wall-clock observed_at would have produced (_fact_identity:355-369).
    assert second.status_code in {200, 201}
    assert second.json()["id"] == first.json()["id"]
    assert _observation_count(migrated_engine) == after_first


def test_changed_reality_mints_a_new_row(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    with Session(migrated_engine) as session:
        unit = seed_unit(session, state="submitted")
        session.commit()
        unit_id = str(unit.id)

    db_client.post(
        "/api/v1/observations",
        json=pr_observation(
            work_unit_id=unit_id, pr_number=41, head_sha=HEAD, state="open", merged=False,
            observed_at=UPDATED_AT,
        ),
        headers=SYSTEM,
    )
    before = _observation_count(migrated_engine)

    merged = db_client.post(
        "/api/v1/observations",
        json=pr_observation(
            work_unit_id=unit_id, pr_number=41, head_sha=HEAD, state="closed", merged=True,
            observed_at=datetime(2026, 7, 10, 11, 0, tzinfo=UTC),
        ),
        headers=SYSTEM,
    )

    assert merged.status_code == 201
    assert _observation_count(migrated_engine) == before + 1


def test_a_raw_provider_payload_is_rejected_and_the_normalized_one_is_accepted(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    with Session(migrated_engine) as session:
        unit = seed_unit(session, state="submitted")
        session.commit()
        unit_id = str(unit.id)

    normalized = pr_observation(
        work_unit_id=unit_id, pr_number=41, head_sha=HEAD, state="open", merged=False,
        observed_at=UPDATED_AT,
    )
    raw = dict(normalized)
    raw["idempotency_key"] = "raw-payload"
    # GitHub's standard check payload key. SECRET_KEY_PARTS contains "log"
    # (observations.py:33-44), so this is rejected outright.
    raw["facts"] = dict(normalized["facts"], logs_url="https://api.github.com/x/logs")

    rejected = db_client.post("/api/v1/observations", json=raw, headers=SYSTEM)
    accepted = db_client.post("/api/v1/observations", json=normalized, headers=SYSTEM)

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "observation_invalid"
    assert accepted.status_code == 201


def test_a_full_pass_transitions_no_existing_unit(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    with Session(migrated_engine) as session:
        unit = seed_unit(session, state="submitted")
        session.commit()
        unit_id, before = str(unit.id), (unit.state, unit.version, unit.attempt_count)

    db_client.post(
        "/api/v1/observations",
        json=pr_observation(
            work_unit_id=unit_id, pr_number=41, head_sha=HEAD, state="closed", merged=True,
            observed_at=UPDATED_AT,
        ),
        headers=SYSTEM,
    )
    db_client.post("/api/v1/reconciliation/detect", json={}, headers=SYSTEM)

    with Session(migrated_engine) as session:
        after_unit = session.get(WorkUnit, unit_id)
        assert after_unit is not None
        assert (after_unit.state, after_unit.version, after_unit.attempt_count) == before
```

- [ ] **Step 15.13 — Run.** `uv run pytest tests/services/test_reconciliation_runner_contract.py -q`. All four must pass **without touching the runner** — they assert the contract Step 15.3 already encodes. If `test_unchanged_reality_repulled_dedups_and_grows_no_rows` returns **409 `observation_conflict`**, the upstream-`observed_at` half was lost; fix `facts.py`, not the test.

- [ ] **Step 15.14 — Failing import-isolation guard.** Create `tests/architecture/test_reconciliation_runner_isolation.py` (modeled on `test_application_has_no_external_mutation_integrations`, `test_scope_guards.py:10-25` — and **bidirectional**):

```python
import ast
from pathlib import Path

from reconciliation_runner.client import ALLOWED_WRITE_ENDPOINTS

RUNNER = Path("src/reconciliation_runner")
ORCHESTRATOR = Path("src/orchestrator")


def _imports(root: Path) -> set[str]:
    names: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        names.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        names.update(
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        )
    return names


def test_runner_imports_nothing_from_the_orchestrator_package() -> None:
    # orchestrator.persistence above all: a DB handle would let the runner write canonical
    # state directly and gut the report-only mandate.
    offenders = {name for name in _imports(RUNNER) if name.split(".")[0] == "orchestrator"}

    assert offenders == set()


def test_the_orchestrator_imports_nothing_from_the_runner() -> None:
    offenders = {
        name for name in _imports(ORCHESTRATOR) if name.split(".")[0] == "reconciliation_runner"
    }

    assert offenders == set()


def test_runner_depends_only_on_httpx_pydantic_and_typer() -> None:
    allowed = {
        "httpx", "pydantic", "typer",
        "json", "os", "hashlib", "datetime", "pathlib", "typing", "__future__",
    }
    offenders = {name.split(".")[0] for name in _imports(RUNNER)} - allowed

    assert offenders == set()


def test_the_runners_write_surface_is_exactly_two_endpoints() -> None:
    assert ALLOWED_WRITE_ENDPOINTS == frozenset(
        {"/api/v1/observations", "/api/v1/reconciliation/detect"}
    )
    source = "\n".join(path.read_text() for path in RUNNER.rglob("*.py"))
    # The endpoint that mints a WorkUnit + 5 Evidence rows per push must not even be namable.
    assert "deployment-observations" not in source
    assert "/commands/" not in source
    assert "/evidence" not in source
```

- [ ] **Step 15.15 — Run, expect pass.** `uv run pytest tests/architecture -q` → all pass.

- [ ] **Step 15.16 — Commit.**
```bash
git add tests/services/test_reconciliation_runner_contract.py tests/architecture/test_reconciliation_runner_isolation.py && git commit -m "test(runner): pin the observation contract, report-only endpoint set, and import isolation (WS-P2.1 AC-009)"
```

---

### Task 16: Four scripted recovery drills (AC-010)

**Files:**
- **Create:** `scripts/drill_common.sh`, `scripts/drill-1-dispatch-crash.sh`, `scripts/drill-2-evidence-recovery.sh`, `scripts/drill-3-external-merge.sh`, `scripts/drill-4-deploy-split-brain.sh`, `docs/operations/recovery-drills.md`
- **Modify:** `Makefile` (a `drills` target)
- **Test:** the drills **are** the test (exit 0 = PASS); plus `tests/architecture/test_drill_scripts.py` pinning the safety invariants.

**Interfaces:**
- **Consumes:** `docker`, `psql`, `curl`, `jq`, `uv`; the orchestrator's public API/CLI only; `alembic upgrade head` (through `0014_wsp21_recovery_controls`).
- **Produces:** four exit-0/exit-1 scripts; scratch DB `orchestrator_drill` (**never** `orchestrator_test` — `tests/conftest.py:10` and `docker-compose.yml:6` own that name and the fixtures drop/recreate it).

**Three grounded facts the scripts must respect.**
1. `dispatch_enabled` defaults **False** (`config.py:9`), so `POST …/dispatch` writes a `DispatchRecord(status="skipped", reason_code="dispatch_disabled")` (`dispatch.py:174-175`) and makes **no live `workflow_dispatch`**. Drill 1 says so in its header.
2. `LEASE_DURATION` is a hardcoded `timedelta(minutes=15)` (`kernel/leases.py:4`) with **no env override**. Drills 1–2 therefore age the lease with one explicit SQL `UPDATE` on the **throwaway** DB's `claims.lease_expires_at`. That is *environment* setup (simulating elapsed wall clock), not a state transition: every orchestrator state change still goes through the public API. It is called out in each header.
3. `Settings` is `BaseSettings(env_prefix="ORCHESTRATOR_")` behind an `lru_cache`d `get_settings()` (`config.py:7,34-36`), and `reconcile_split_brain_stall_seconds` defaults to **900**. Drill 4 therefore exports `ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS=1` **before starting** its throwaway process — no sleep, no private time manipulation.

- [ ] **Step 16.1 — Write the shared harness.** Create `scripts/drill_common.sh`:

```bash
#!/bin/bash
# Shared harness for the WS-P2.1 recovery drills (AC-010), in the vps-backup
# restore-drill.sh mold: disposable scratch, trapped idempotent teardown, die() vs
# accumulating fail(), timestamped log(), exit 0 = PASS.
#
# Every drill is READ-ONLY toward production and shared systems. It owns:
#   - one throwaway Postgres container  (drill-pg-$$)
#   - one throwaway database            (orchestrator_drill — NEVER orchestrator_test,
#                                        which tests/conftest.py:10 drops and recreates)
#   - one throwaway uvicorn process     (bound to 127.0.0.1 on a free port)
# The M2M token is generated per run and never written to a tracked file.
#
# Source, don't execute: . "$(dirname "$0")/drill_common.sh"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRILL_DB="orchestrator_drill"
PG_IMAGE="postgres:16"
CONTAINER="drill-pg-$$"
PG_PORT="${DRILL_PG_PORT:-55432}"
API_PORT="${DRILL_API_PORT:-58000}"
API_URL="http://127.0.0.1:${API_PORT}"
DRILL_DIR="$(mktemp -d /tmp/orchestrator-drill-XXXXXX)"
LOG_FILE="${DRILL_DIR}/drill.log"
SERVER_PID=""
KEEP=0

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
FAILURES=()
fail() { log "FAIL: $*"; FAILURES+=("$*"); }
die()  { log "ERROR: $*"; exit 1; }

parse_common_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --keep) KEEP=1; shift ;;
            *) echo "unknown arg: $1" >&2; exit 2 ;;
        esac
    done
}

cleanup() {
    if [ -n "$SERVER_PID" ]; then kill "$SERVER_PID" >/dev/null 2>&1 || true; fi
    [ "$KEEP" -eq 1 ] && { log "--keep set: leaving $CONTAINER and $DRILL_DIR in place"; return; }
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    rm -rf "$DRILL_DIR"
}
trap 'cleanup' EXIT

preflight() {
    command -v docker >/dev/null || die "docker CLI not found"
    command -v jq     >/dev/null || die "jq not found"
    command -v curl   >/dev/null || die "curl not found"
    command -v uv     >/dev/null || die "uv not found"
    [ -f "$REPO_ROOT/alembic.ini" ] || die "must run from the orchestrator repo"
}

start_scratch_postgres() {
    log "[pg] starting throwaway $PG_IMAGE as $CONTAINER on :$PG_PORT"
    docker run -d --name "$CONTAINER" -e POSTGRES_PASSWORD=drill \
        -e POSTGRES_DB="$DRILL_DB" -p "127.0.0.1:${PG_PORT}:5432" "$PG_IMAGE" \
        >> "$LOG_FILE" 2>&1
    # The postgres image starts a TEMPORARY init server, stops it, then starts the real
    # one — a single probe can land in that gap. Require two consecutive successes.
    local ready=0
    for _ in $(seq 1 30); do
        if docker exec "$CONTAINER" psql -U postgres -d "$DRILL_DB" -qAtc "SELECT 1" >/dev/null 2>&1; then
            sleep 2
            if docker exec "$CONTAINER" psql -U postgres -d "$DRILL_DB" -qAtc "SELECT 1" >/dev/null 2>&1; then
                ready=1; break
            fi
        fi
        sleep 1
    done
    [ "$ready" -eq 1 ] || die "[pg] scratch postgres never became ready"
    export ORCHESTRATOR_DATABASE_URL="postgresql+psycopg://postgres:drill@127.0.0.1:${PG_PORT}/${DRILL_DB}"
}

scratch_sql() {
    docker exec -i "$CONTAINER" psql -U postgres -d "$DRILL_DB" -qAt -v ON_ERROR_STOP=1 -c "$1"
}

write_auth_env() {
    # Registry bundle + M2M credentials, exactly the shape main.py:load_auth_config() parses
    # (mirrors tests/api/conftest.py). Tokens are generated per run — never tracked.
    SYSTEM_TOKEN="$(uuidgen)"; WORKER_TOKEN="$(uuidgen)"; VERIFIER_TOKEN="$(uuidgen)"
    local sys_hash worker_hash verifier_hash
    sys_hash="$(printf '%s' "$SYSTEM_TOKEN" | shasum -a 256 | cut -d' ' -f1)"
    worker_hash="$(printf '%s' "$WORKER_TOKEN" | shasum -a 256 | cut -d' ' -f1)"
    verifier_hash="$(printf '%s' "$VERIFIER_TOKEN" | shasum -a 256 | cut -d' ' -f1)"

    cat > "$DRILL_DIR/registry.json" <<JSON
{"schema": "orchestrator-actor-bundle/v1",
 "source_revision": "0123456789abcdef0123456789abcdef01234567",
 "actors": [
  {"agent_id": "system",   "version": 1, "status": "active", "runtime": "orchestrator", "authority_profile": "system-v1"},
  {"agent_id": "worker",   "version": 3, "status": "active", "runtime": "runner",       "authority_profile": "agent-queue-v1"},
  {"agent_id": "verifier", "version": 1, "status": "active", "runtime": "verifier",     "authority_profile": "verifier-v1"},
  {"agent_id": "devon",    "version": 1, "status": "active", "runtime": "human",        "authority_profile": "human-operator-v1"}
 ]}
JSON

    export ORCHESTRATOR_REGISTRY_BUNDLE="$DRILL_DIR/registry.json"
    export ORCHESTRATOR_M2M_CREDENTIALS="{\"system-key\":{\"agent_id\":\"system\",\"token_hash\":\"$sys_hash\"},\"worker-key\":{\"agent_id\":\"worker\",\"token_hash\":\"$worker_hash\"},\"verifier-key\":{\"agent_id\":\"verifier\",\"token_hash\":\"$verifier_hash\"}}"
    export ORCHESTRATOR_M2M_ROLES='{"system-key":"system","verifier-key":"verifier"}'
    export ORCHESTRATOR_EMAIL_TO_ACTOR='{"devon@example.invalid":"devon"}'
    export ORCHESTRATOR_TRUSTED_PROXY_IPS='["127.0.0.1"]'
    export ORCHESTRATOR_PROXY_MARKER="drill-marker"
    export ORCHESTRATOR_CSRF_SECRET="drill-only-csrf-secret-with-32-bytes!"
}

migrate_scratch() {
    log "[db] alembic upgrade head (through 0014_wsp21_recovery_controls) against $DRILL_DB"
    (cd "$REPO_ROOT" && uv run alembic upgrade head) >> "$LOG_FILE" 2>&1 \
        || die "[db] migration failed"
}

start_orchestrator() {
    log "[api] starting throwaway uvicorn on :$API_PORT"
    (cd "$REPO_ROOT" && uv run uvicorn orchestrator.main:app --host 127.0.0.1 --port "$API_PORT") \
        >> "$LOG_FILE" 2>&1 &
    SERVER_PID=$!
    for _ in $(seq 1 40); do
        if curl -fsS "$API_URL/health/live" >/dev/null 2>&1; then return 0; fi
        sleep 0.5
    done
    die "[api] orchestrator never became live on $API_URL"
}

stop_orchestrator() {  # simulate process death — SIGKILL, no graceful shutdown
    [ -n "$SERVER_PID" ] || return 0
    kill -9 "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
}

# api <METHOD> <PATH> <ROLE:system|worker|verifier> [JSON_BODY]
api() {
    local method="$1" path="$2" role="$3" body="${4:-}"
    local token key
    case "$role" in
        system)   token="$SYSTEM_TOKEN";   key="system-key" ;;
        worker)   token="$WORKER_TOKEN";   key="worker-key" ;;
        verifier) token="$VERIFIER_TOKEN"; key="verifier-key" ;;
        *) die "unknown role: $role" ;;
    esac
    if [ -n "$body" ]; then
        curl -sS -X "$method" "$API_URL$path" \
            -H "Authorization: Bearer $token" -H "X-Credential-Key-Id: $key" \
            -H "Content-Type: application/json" -d "$body"
    else
        curl -sS -X "$method" "$API_URL$path" \
            -H "Authorization: Bearer $token" -H "X-Credential-Key-Id: $key"
    fi
}

summarize() {  # $1 = drill name
    if [ ${#FAILURES[@]} -eq 0 ]; then
        log "$1 PASS"
        exit 0
    fi
    log "$1 FAILED: ${#FAILURES[@]} failure(s)"
    for f in "${FAILURES[@]}"; do log "  - $f"; done
    exit 1
}
```

- [ ] **Step 16.2 — Drill 1.** Create `scripts/drill-1-dispatch-crash.sh`:

```bash
#!/bin/bash
# DRILL 1 (AC-010): the orchestrator dies after dispatch.
#
# Proves: a dispatch recorded immediately before process death leaves NO orphaned
# canonical state — the unit is recoverable via reclaim / requeue through the public API.
#
# NO LIVE workflow_dispatch FIRES. `dispatch_enabled` defaults False (config.py:9), so
# POST …/dispatch writes DispatchRecord(status="skipped", reason_code="dispatch_disabled")
# (dispatch.py:174-175) and calls GitHub not at all. This drill touches no shared system.
#
# The ONE non-API write is `UPDATE claims SET lease_expires_at` on the THROWAWAY database:
# LEASE_DURATION is a hardcoded 15 minutes (kernel/leases.py:4) with no env override, so
# this stands in for elapsed wall clock. Every state change still goes through the API.
#
# Usage: ./scripts/drill-1-dispatch-crash.sh [--keep]     (exit 0 = PASS)

set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/drill_common.sh"
parse_common_args "$@"

log "═══════════════════════════════════════════"
log "DRILL 1 — orchestrator dies after dispatch"
preflight
start_scratch_postgres
write_auth_env
migrate_scratch
start_orchestrator

REVISION=$(api POST /api/v1/revisions system \
    '{"idempotency_key":"drill1-rev","revision_hash":"'"$(printf 'drill1' | shasum -a 256 | cut -c1-64)"'","source_repository":"AlobarQuest/orchestrator","source_ref":"main"}' \
    | jq -r '.id')
[ -n "$REVISION" ] && [ "$REVISION" != "null" ] || die "could not register a revision"

UNIT=$(api POST "/api/v1/revisions/$REVISION/work-units" system \
    '{"idempotency_key":"drill1-unit","unit_key":"drill1-impl","title":"drill unit","outcome":"drill","required_capability":"code","authority":{},"max_attempts":3}' \
    | jq -r '.id')
[ -n "$UNIT" ] && [ "$UNIT" != "null" ] || die "could not register a work unit"

log "[1/5] dispatch (dispatch_enabled=false → recorded, never sent)"
DISPATCH=$(api POST "/api/v1/work-units/$UNIT/dispatch" system '{"idempotency_key":"drill1-dispatch"}')
RECORD_STATUS=$(scratch_sql "SELECT status FROM dispatch_records WHERE work_unit_id='$UNIT'")
RECORD_REASON=$(scratch_sql "SELECT reason_code FROM dispatch_records WHERE work_unit_id='$UNIT'")
[ "$RECORD_STATUS" = "skipped" ] || fail "expected DispatchRecord status=skipped, got '$RECORD_STATUS' ($DISPATCH)"
[ "$RECORD_REASON" = "dispatch_disabled" ] || fail "expected reason_code=dispatch_disabled, got '$RECORD_REASON'"

log "[2/5] worker claims (attempt 1)"
CLAIM=$(api POST "/api/v1/work-units/$UNIT/claim" worker \
    '{"expected_version":1,"idempotency_key":"drill1-claim","standing_context":{}}')
CLAIMED_STATE=$(scratch_sql "SELECT state FROM work_units WHERE id='$UNIT'")
[ "$CLAIMED_STATE" = "claimed" ] || fail "expected state=claimed after claim, got '$CLAIMED_STATE' ($CLAIM)"

log "[3/5] SIGKILL the orchestrator (process death, no graceful shutdown)"
stop_orchestrator

log "[4/5] restart against the SAME scratch database — canonical state must have survived"
start_orchestrator
AFTER_STATE=$(scratch_sql "SELECT state FROM work_units WHERE id='$UNIT'")
AFTER_ATTEMPTS=$(scratch_sql "SELECT attempt_count FROM work_units WHERE id='$UNIT'")
ORPHANS=$(scratch_sql "SELECT count(*) FROM claims WHERE work_unit_id='$UNIT' AND released_at IS NULL")
[ "$AFTER_STATE" = "claimed" ] || fail "state changed across the crash: '$AFTER_STATE'"
[ "$AFTER_ATTEMPTS" = "1" ] || fail "attempt_count changed across the crash: '$AFTER_ATTEMPTS'"
[ "$ORPHANS" = "1" ] || fail "expected exactly 1 unreleased claim after the crash, found $ORPHANS"
DISPATCH_ROWS=$(scratch_sql "SELECT count(*) FROM dispatch_records WHERE work_unit_id='$UNIT'")
[ "$DISPATCH_ROWS" = "1" ] || fail "dispatch record did not survive the crash (rows=$DISPATCH_ROWS)"

log "[5/5] recover: age the lease (throwaway DB), then reclaim through the public API"
scratch_sql "UPDATE claims SET lease_expires_at = now() - interval '1 minute' WHERE work_unit_id='$UNIT' AND released_at IS NULL" >/dev/null
RECLAIM=$(api POST "/api/v1/work-units/$UNIT/reclaim-expired-claim" system \
    '{"next_owner_id":"worker","idempotency_key":"drill1-reclaim"}')
RECOVERED_STATE=$(scratch_sql "SELECT state FROM work_units WHERE id='$UNIT'")
UNRELEASED=$(scratch_sql "SELECT count(*) FROM claims WHERE work_unit_id='$UNIT' AND released_at IS NULL")
TERMINAL=$(scratch_sql "SELECT terminal_reason FROM claims WHERE work_unit_id='$UNIT' AND released_at IS NOT NULL LIMIT 1")
[ "$RECOVERED_STATE" = "ready" ] || fail "unit is not recoverable: state='$RECOVERED_STATE' ($RECLAIM)"
[ "$UNRELEASED" = "0" ] || fail "orphaned claim remains after reclaim (unreleased=$UNRELEASED)"
[ "$TERMINAL" = "lease_expired" ] || fail "expected terminal_reason=lease_expired, got '$TERMINAL'"

summarize "DRILL 1"
```

- [ ] **Step 16.3 — Run it.** `chmod +x scripts/*.sh && ./scripts/drill-1-dispatch-crash.sh` → `DRILL 1 PASS`, exit 0. A first run may fail on a body field the create routes require; read `$DRILL_DIR/drill.log` (re-run with `--keep`) and fix the **script**, never the API.

- [ ] **Step 16.4 — Drill 2.** Create `scripts/drill-2-evidence-recovery.sh` — the AC-004 scenario: the lease lapses **before** submit, leaving the claim **expired-but-UNRELEASED**:

```bash
#!/bin/bash
# DRILL 2 (AC-010): evidence submission fails after the worker completed the work.
#
# Proves, end to end, all five AC-004 promises:
#   a. recover-evidence attaches the evidence SUPERSEDING any existing head (never a
#      second NULL-supersedes head — two heads permanently wedge the unit, evidence.py:853;
#      the partial unique index uq_evidence_unsuperseded_head makes it structurally
#      impossible, and this drill proves the service honors it)
#   b. recovery RELEASES the expired-but-unreleased claim and SYSTEM-fails the unit
#   c. it mints NO new attempt
#   d. the worker still cannot reach COMPLETED
#   e. attempt n+1 submits WITHOUT re-executing the work
#
# The ONE non-API write is `UPDATE claims SET lease_expires_at` on the THROWAWAY database
# (LEASE_DURATION is a hardcoded 15 minutes, kernel/leases.py:4 — no env override); it
# stands in for elapsed wall clock. Every state change goes through the public API.
#
# Usage: ./scripts/drill-2-evidence-recovery.sh [--keep]    (exit 0 = PASS)

set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/drill_common.sh"
parse_common_args "$@"

log "═══════════════════════════════════════════"
log "DRILL 2 — evidence submission fails after worker completion"
preflight
start_scratch_postgres
write_auth_env
migrate_scratch
start_orchestrator

REVISION=$(api POST /api/v1/revisions system \
    '{"idempotency_key":"drill2-rev","revision_hash":"'"$(printf 'drill2' | shasum -a 256 | cut -c1-64)"'","source_repository":"AlobarQuest/orchestrator","source_ref":"main"}' | jq -r '.id')
UNIT=$(api POST "/api/v1/revisions/$REVISION/work-units" system \
    '{"idempotency_key":"drill2-unit","unit_key":"drill2-impl","title":"drill unit","outcome":"drill","required_capability":"code","authority":{},"max_attempts":3}' | jq -r '.id')
[ -n "$UNIT" ] && [ "$UNIT" != "null" ] || die "could not register a work unit"

log "[1/6] worker claims and executes (attempt 1) — the work IS done"
api POST "/api/v1/work-units/$UNIT/claim" worker \
    '{"expected_version":1,"idempotency_key":"drill2-claim","standing_context":{}}' >/dev/null
api POST "/api/v1/work-units/$UNIT/commands/start" worker '{"idempotency_key":"drill2-start"}' >/dev/null
STATE=$(scratch_sql "SELECT state FROM work_units WHERE id='$UNIT'")
[ "$STATE" = "executing" ] || fail "expected state=executing, got '$STATE'"

log "[2/6] the lease lapses BEFORE submit — claim left expired-but-UNRELEASED"
scratch_sql "UPDATE claims SET lease_expires_at = now() - interval '1 minute' WHERE work_unit_id='$UNIT' AND released_at IS NULL" >/dev/null
SUBMIT=$(api POST "/api/v1/work-units/$UNIT/commands/submit" worker '{"idempotency_key":"drill2-submit"}')
SUBMIT_CODE=$(echo "$SUBMIT" | jq -r '.error.code // "none"')
[ "$SUBMIT_CODE" = "lease_expired" ] || fail "expected the worker's submit to be refused with lease_expired, got '$SUBMIT_CODE'"

log "[3/6] SYSTEM recovers the evidence for attempt 1 (never the expired worker)"
RECOVER=$(api POST "/api/v1/work-units/$UNIT/attempts/1/recover-evidence" system \
    '{"idempotency_key":"drill2-recover","ac_id":"AC-1","evidence_type":"pr_opened","payload":{"pr_number":41},"rationale":"lease lapsed before submit"}')
EVIDENCE_ID=$(echo "$RECOVER" | jq -r '.id // empty')
[ -n "$EVIDENCE_ID" ] || fail "recover-evidence did not return an evidence row: $RECOVER"

# (a) exactly ONE unsuperseded head for (revision, unit, ac) — never two.
HEADS=$(scratch_sql "SELECT count(*) FROM evidence WHERE work_unit_id='$UNIT' AND ac_id='AC-1' AND supersedes_evidence_id IS NULL")
[ "$HEADS" = "1" ] || fail "expected exactly 1 supersession head, found $HEADS (two heads wedge the unit forever)"
PROVENANCE=$(scratch_sql "SELECT count(*) FROM events WHERE subject_id='$EVIDENCE_ID' AND payload::text LIKE '%recovered_from_expired_lease%'")
[ "$PROVENANCE" -ge 1 ] || fail "recovery is not provenance-tagged recovered_from_expired_lease"

log "[4/6] recovery released the claim and SYSTEM-failed the unit — with NO new attempt"
STATE=$(scratch_sql "SELECT state FROM work_units WHERE id='$UNIT'")
ATTEMPTS=$(scratch_sql "SELECT attempt_count FROM work_units WHERE id='$UNIT'")
UNRELEASED=$(scratch_sql "SELECT count(*) FROM claims WHERE work_unit_id='$UNIT' AND released_at IS NULL")
[ "$STATE" = "failed" ] || fail "expected SYSTEM-failed unit, got state='$STATE'"
[ "$ATTEMPTS" = "1" ] || fail "recovery minted a new attempt (attempt_count=$ATTEMPTS, expected 1)"
[ "$UNRELEASED" = "0" ] || fail "recovery left the claim unreleased"

log "[5/6] the worker STILL cannot reach completed"
COMPLETE=$(api POST "/api/v1/work-units/$UNIT/commands/complete" worker '{"idempotency_key":"drill2-complete"}')
COMPLETE_CODE=$(echo "$COMPLETE" | jq -r '.error.code // "none"')
[ "$COMPLETE_CODE" != "none" ] || fail "a worker command reached completed — WORKER_EDGES must have no ->COMPLETED edge"
FINAL_STATE=$(scratch_sql "SELECT state FROM work_units WHERE id='$UNIT'")
[ "$FINAL_STATE" != "completed" ] || fail "unit reached completed via a worker path"

log "[6/6] attempt 2 submits WITHOUT re-executing the work — the evidence is already there"
api POST "/api/v1/work-units/$UNIT/requeue" system '{"idempotency_key":"drill2-requeue"}' >/dev/null
api POST "/api/v1/work-units/$UNIT/claim" worker \
    '{"idempotency_key":"drill2-claim-2","standing_context":{}}' >/dev/null
api POST "/api/v1/work-units/$UNIT/commands/start" worker '{"idempotency_key":"drill2-start-2"}' >/dev/null
api POST "/api/v1/work-units/$UNIT/commands/submit" worker '{"idempotency_key":"drill2-submit-2"}' >/dev/null
STATE=$(scratch_sql "SELECT state FROM work_units WHERE id='$UNIT'")
HEADS=$(scratch_sql "SELECT count(*) FROM evidence WHERE work_unit_id='$UNIT' AND ac_id='AC-1' AND supersedes_evidence_id IS NULL")
RECOVERED_HEAD=$(scratch_sql "SELECT count(*) FROM evidence WHERE id='$EVIDENCE_ID'")
[ "$STATE" = "submitted" ] || fail "attempt 2 did not submit (state='$STATE')"
[ "$HEADS" = "1" ] || fail "attempt 2 produced a second head (heads=$HEADS)"
[ "$RECOVERED_HEAD" = "1" ] || fail "the recovered evidence row vanished — evidence is append-only"

summarize "DRILL 2"
```

- [ ] **Step 16.5 — Run it.** `./scripts/drill-2-evidence-recovery.sh` → `DRILL 2 PASS`.

- [ ] **Step 16.6 — Drill 3.** Create `scripts/drill-3-external-merge.sh`:

```bash
#!/bin/bash
# DRILL 3 (AC-010): a PR is merged outside the session.
#
# Proves: pushing a merged `github_pr` observation for a NOT-yet-completed unit makes the
# ON-INGEST hook record an `external_merge_alarm` condition — and the orchestrator does
# NOT auto-complete the unit and does NOT merge anything. Detection only ever appends.
# Resolution is HUMAN-only (POST /review/reconciliation/conditions/{id}/resolution) and
# this drill performs none, so "zero resolutions" is the correct expectation.
#
# Read-only toward GitHub: the observation is a fixture body, not a live pull. Nothing in
# this drill ever attempts a merge.
#
# Usage: ./scripts/drill-3-external-merge.sh [--keep]      (exit 0 = PASS)

set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/drill_common.sh"
parse_common_args "$@"

log "═══════════════════════════════════════════"
log "DRILL 3 — PR merged outside the session"
preflight
start_scratch_postgres
write_auth_env
migrate_scratch
start_orchestrator

HEAD_SHA="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
REVISION=$(api POST /api/v1/revisions system \
    '{"idempotency_key":"drill3-rev","revision_hash":"'"$(printf 'drill3' | shasum -a 256 | cut -c1-64)"'","source_repository":"AlobarQuest/orchestrator","source_ref":"main"}' | jq -r '.id')
UNIT=$(api POST "/api/v1/revisions/$REVISION/work-units" system \
    '{"idempotency_key":"drill3-unit","unit_key":"drill3-impl","title":"drill unit","outcome":"drill","required_capability":"code","authority":{},"max_attempts":3}' | jq -r '.id')
[ -n "$UNIT" ] && [ "$UNIT" != "null" ] || die "could not register a work unit"

log "[1/4] the unit is in flight with a PR binding (pr 41 @ $HEAD_SHA)"
api POST "/api/v1/work-units/$UNIT/claim" worker \
    '{"expected_version":1,"idempotency_key":"drill3-claim","standing_context":{}}' >/dev/null
api POST "/api/v1/work-units/$UNIT/commands/start" worker '{"idempotency_key":"drill3-start"}' >/dev/null
scratch_sql "INSERT INTO unit_pr_binding (work_unit_id, pr_number, head_sha, verification_read_head_sha, updated_at) VALUES ('$UNIT', 41, '$HEAD_SHA', NULL, now())" >/dev/null
BEFORE_STATE=$(scratch_sql "SELECT state FROM work_units WHERE id='$UNIT'")
BEFORE_VERSION=$(scratch_sql "SELECT version FROM work_units WHERE id='$UNIT'")
[ "$BEFORE_STATE" != "completed" ] || die "fixture unit must not be completed"

log "[2/4] someone merges the PR out of band — the runner reports it (normalized facts only)"
FACTS='{"pr_number":41,"head_sha":"'"$HEAD_SHA"'","state":"closed","merged":true,"observed_at":"2026-07-10T11:00:00+00:00"}'
DIGEST=$(echo "$FACTS" | jq -cS . | tr -d '\n' | shasum -a 256 | cut -d' ' -f1)
REF="pr:41@${HEAD_SHA}:${DIGEST}"
OBS=$(api POST /api/v1/observations system "$(jq -nc \
  --arg ref "$REF" --arg unit "$UNIT" --argjson facts "$FACTS" \
  '{idempotency_key:("reconcile:"+$ref),expected_version:null,source_system:"github",
    source_reference:$ref,source_url:null,trust_classification:"delivery_system",
    subject_type:"work_unit",subject_reference:$unit,environment:null,
    observation_type:"github_pr",status:"observed",severity:"info",
    observed_at:"2026-07-10T11:00:00+00:00",summary:"pull request 41 is closed",
    facts:$facts,payload_digest:null}')")
echo "$OBS" | jq -e '.id' >/dev/null || fail "the merged github_pr observation was rejected: $OBS"

log "[3/4] the ON-INGEST hook must have recorded external_merge_alarm"
ALARMS=$(scratch_sql "SELECT count(*) FROM reconciliation_conditions WHERE work_unit_id='$UNIT' AND condition_type='external_merge_alarm'")
[ "$ALARMS" = "1" ] || fail "expected 1 external_merge_alarm on ingest, found $ALARMS"
EVENTS=$(scratch_sql "SELECT count(*) FROM events WHERE action='reconciliation.required' AND subject_id IN (SELECT id FROM reconciliation_conditions WHERE work_unit_id='$UNIT')")
[ "$EVENTS" -ge 1 ] || fail "no reconciliation.required event was emitted"

log "[4/4] and the unit was NOT auto-completed and NOT merged"
AFTER_STATE=$(scratch_sql "SELECT state FROM work_units WHERE id='$UNIT'")
AFTER_VERSION=$(scratch_sql "SELECT version FROM work_units WHERE id='$UNIT'")
[ "$AFTER_STATE" = "$BEFORE_STATE" ] || fail "detection mutated the unit: '$BEFORE_STATE' -> '$AFTER_STATE'"
[ "$AFTER_VERSION" = "$BEFORE_VERSION" ] || fail "detection bumped the unit version ($BEFORE_VERSION -> $AFTER_VERSION)"
[ "$AFTER_STATE" != "completed" ] || fail "the unit was auto-completed by an external merge"
RESOLUTIONS=$(scratch_sql "SELECT count(*) FROM reconciliation_resolutions")
[ "$RESOLUTIONS" = "0" ] || fail "detection auto-resolved a condition (resolutions=$RESOLUTIONS) — resolution is HUMAN-only"

summarize "DRILL 3"
```

- [ ] **Step 16.7 — Drill 4.** Create `scripts/drill-4-deploy-split-brain.sh`:

```bash
#!/bin/bash
# DRILL 4 (AC-010): the deployment succeeds while verification times out.
#
# Proves: with the split-brain stall threshold set LOW, a post-deploy verification unit
# still sitting in SUBMITTED past the threshold is detected as `deploy_split_brain` by the
# detect-pass — the deployment is not silently accepted.
#
# NO SLEEP OF THE THRESHOLD, NO TIME MANIPULATION. Settings is
# BaseSettings(env_prefix="ORCHESTRATOR_") behind an lru_cache'd accessor (config.py:7,34-36)
# and reconcile_split_brain_stall_seconds defaults to 900, so the drill simply exports the
# override BEFORE the throwaway process starts. That is the whole trick.
#
# Usage: ./scripts/drill-4-deploy-split-brain.sh [--keep]  (exit 0 = PASS)

set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/drill_common.sh"
parse_common_args "$@"

log "═══════════════════════════════════════════"
log "DRILL 4 — deployment succeeds while verification times out"
preflight
start_scratch_postgres
write_auth_env

# Set BEFORE start_orchestrator: get_settings() is lru_cached, so the process reads it once.
export ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS=1
log "[env] ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS=1 (default 900; set before start)"

migrate_scratch
start_orchestrator

REVISION=$(api POST /api/v1/revisions system \
    '{"idempotency_key":"drill4-rev","revision_hash":"'"$(printf 'drill4' | shasum -a 256 | cut -c1-64)"'","source_repository":"AlobarQuest/orchestrator","source_ref":"main"}' | jq -r '.id')
UNIT=$(api POST "/api/v1/revisions/$REVISION/work-units" system \
    '{"idempotency_key":"drill4-unit","unit_key":"drill4-impl","title":"drill unit","outcome":"drill","required_capability":"code","authority":{},"max_attempts":3}' | jq -r '.id')
[ -n "$UNIT" ] && [ "$UNIT" != "null" ] || die "could not register a work unit"

log "[1/4] a release binding exists and the deploy is observed (the trusted deploy path)"
DIGEST="sha256:$(printf 'drill4-artifact' | shasum -a 256 | cut -d' ' -f1)"
BINDING=$(api POST "/api/v1/work-units/$UNIT/release-artifacts" system "$(jq -nc --arg d "$DIGEST" \
  --arg c "$(printf 'c' | shasum -a 256 | cut -c1-40)" --arg m "$(printf 'm' | shasum -a 256 | cut -c1-40)" \
  '{idempotency_key:"drill4-binding",source_repository:"AlobarQuest/orchestrator",
    implementation_pr_number:41,source_commit:$c,merge_commit:$m,
    artifact_registry:"ghcr.io",artifact_repository:"alobarquest/orchestrator",
    artifact_name:"orchestrator",artifact_digest:$d,artifact_tag:"drill",
    workflow_run_id:"1",workflow_run_attempt:1,workflow_path:".github/workflows/release.yml",
    workflow_ref:"main",workflow_run_url:"https://github.com/AlobarQuest/orchestrator/actions/runs/1",
    builder_id:"github-actions",builder_class:"hosted",summary:"drill binding"}')" | jq -r '.id')
[ -n "$BINDING" ] && [ "$BINDING" != "null" ] || die "could not record a release binding"

# The deployment-observation ingest mints the post-deploy verification unit in SUBMITTED.
DEPLOY=$(api POST "/api/v1/release-artifacts/$BINDING/deployment-observations" system "$(jq -nc --arg d "$DIGEST" \
  '{idempotency_key:"drill4-deploy",environment:"production",base_url:"https://drill.invalid",
    observed_artifact_digest:$d,deployment_ref:"drill",deployment_url:"https://drill.invalid/deploy/1",
    deployer:"system",observed_at:"2026-07-10T10:30:00+00:00",
    probe_summary:{},route_summary:{},auth_summary:{},dispatch_summary:{},status_summary:{}}')")
POST_UNIT=$(echo "$DEPLOY" | jq -r '.post_deploy_work_unit_id // empty')
[ -n "$POST_UNIT" ] || fail "the deployment observation did not mint a post-deploy unit: $DEPLOY"

log "[2/4] verification never completes — the post-deploy unit sits in SUBMITTED"
POST_STATE=$(scratch_sql "SELECT state FROM work_units WHERE id='$POST_UNIT'")
[ "$POST_STATE" = "submitted" ] || fail "expected the post-deploy unit in submitted, got '$POST_STATE'"

log "[3/4] run the detect-pass (threshold = 1s, so the unit is already stalled)"
sleep 2   # exceed the 1-SECOND threshold — not a 15-minute lease. Bounded and honest.
DETECT=$(api POST /api/v1/reconciliation/detect system '{"idempotency_key":"drill4-detect"}')
RECORDED=$(echo "$DETECT" | jq -r '.conditions_recorded // 0')
SKIPPED=$(echo "$DETECT" | jq -r '.skipped_correlations // 0')
[ "$RECORDED" -ge 1 ] || fail "detect recorded no conditions: $DETECT"
[ "$SKIPPED" = "0" ] || fail "detect skipped $SKIPPED correlation(s) — a silent miss"

log "[4/4] deploy_split_brain must be recorded, and nothing auto-resolved"
SPLIT=$(scratch_sql "SELECT count(*) FROM reconciliation_conditions WHERE condition_type='deploy_split_brain' AND work_unit_id='$POST_UNIT'")
[ "$SPLIT" = "1" ] || fail "expected 1 deploy_split_brain condition, found $SPLIT"
STILL=$(scratch_sql "SELECT state FROM work_units WHERE id='$POST_UNIT'")
[ "$STILL" = "submitted" ] || fail "detection mutated the post-deploy unit: '$STILL'"
RESOLUTIONS=$(scratch_sql "SELECT count(*) FROM reconciliation_resolutions")
[ "$RESOLUTIONS" = "0" ] || fail "detect auto-resolved a condition — resolution is HUMAN-only"

summarize "DRILL 4"
```

- [ ] **Step 16.8 — Run drills 3 and 4.** `./scripts/drill-3-external-merge.sh && ./scripts/drill-4-deploy-split-brain.sh` → both PASS, exit 0.

- [ ] **Step 16.9 — Pin the drills' safety invariants.** Create `tests/architecture/test_drill_scripts.py`:

```python
from pathlib import Path

DRILLS = sorted(Path("scripts").glob("drill-*.sh"))


def test_there_are_four_drills_and_a_shared_harness() -> None:
    assert len(DRILLS) == 4
    assert Path("scripts/drill_common.sh").exists()


def test_every_drill_is_hardened_and_disposable() -> None:
    for path in [*DRILLS, Path("scripts/drill_common.sh")]:
        source = path.read_text()
        assert source.startswith("#!/bin/bash")
        assert "set -euo pipefail" in source


def test_no_drill_touches_the_test_database_or_a_shared_system() -> None:
    for path in [*DRILLS, Path("scripts/drill_common.sh")]:
        source = path.read_text().lower()
        # tests/conftest.py:10 drops and recreates orchestrator_test — a drill must never
        # point at it, or a concurrent test run loses its database mid-suite.
        assert "orchestrator_test" not in source
        assert "sds.alobar.net" not in source
        assert "coolify" not in source
        assert "gh pr merge" not in source
        assert "git push origin main" not in source


def test_every_drill_traps_its_teardown_and_offers_keep() -> None:
    assert "trap 'cleanup' EXIT" in Path("scripts/drill_common.sh").read_text()
    for path in DRILLS:
        assert "parse_common_args" in path.read_text()
```

- [ ] **Step 16.10 — Run.** `uv run pytest tests/architecture/test_drill_scripts.py -q` → 4 passed. Also `shellcheck scripts/*.sh` → clean.

- [ ] **Step 16.11 — Write the runbook.** Create `docs/operations/recovery-drills.md`:

```markdown
# Recovery drills (WS-P2.1 AC-010)

**Cadence: quarterly.** Same discipline as `vps-backup/restore-drill.sh` — a recovery path
you have not executed is a recovery path you do not have.

| Drill | Script | Proves |
|---|---|---|
| 1 | `scripts/drill-1-dispatch-crash.sh` | Orchestrator death after dispatch leaves no orphaned canonical state; the unit is reclaimable. |
| 2 | `scripts/drill-2-evidence-recovery.sh` | Lease lapses before submit → `recover-evidence` attaches evidence superseding the head, releases the claim, SYSTEM-fails without a new attempt; the worker cannot complete; attempt n+1 does not redo the work. |
| 3 | `scripts/drill-3-external-merge.sh` | An out-of-band merge raises `external_merge_alarm` on ingest and auto-completes nothing. |
| 4 | `scripts/drill-4-deploy-split-brain.sh` | A deploy whose verification stalls is detected as `deploy_split_brain`, not silently accepted. |

## Running

    make drills                      # all four, sequentially
    ./scripts/drill-2-evidence-recovery.sh --keep    # leave the container + logs to inspect

Exit 0 = PASS. Any failure prints every accumulated `FAIL:` line and exits 1.

## What the drills touch

Nothing shared. Each run owns a throwaway Postgres container (`drill-pg-$$`), a scratch
database (`orchestrator_drill` — **never** `orchestrator_test`, which the pytest fixtures
drop and recreate), and a throwaway uvicorn bound to 127.0.0.1. Teardown is trapped and
idempotent. Credentials are generated per run and never written to a tracked file.

- **No live `workflow_dispatch`.** `dispatch_enabled` defaults `False` (`config.py:9`), so
  drill 1 exercises the *recorded*-dispatch path (`status="skipped"`,
  `reason_code="dispatch_disabled"`) and calls GitHub not at all.
- **No 15-minute waits.** `LEASE_DURATION` is a hardcoded 15 minutes (`kernel/leases.py:4`)
  with no env override, so drills 1–2 age the lease with one `UPDATE` on the throwaway
  database. That is environment setup standing in for wall clock; every orchestrator state
  change still goes through the public API.
- **No clock patching in drill 4.** `ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS`
  (default 900) is exported before the throwaway process starts — `Settings` is
  `BaseSettings` with `env_prefix` behind an `lru_cache`d accessor (`config.py:7,34-36`).
- **No drill resolves a condition.** Resolution is HUMAN-only
  (`POST /review/reconciliation/conditions/{id}/resolution`); the drills assert zero
  resolutions precisely because detection must never auto-resolve.

## Operator guidance: the split-brain threshold

`ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS` defaults to **900** (15 minutes).
Production must sit above a normal post-deploy verification's worst case; the drill sets
it to 1 so the stall is instantaneous. Raise it if healthy verifications trip the alarm —
never lower it to silence a real one.

## When a drill fails

The drill is the alarm, not the bug. Read `$DRILL_DIR/drill.log` (re-run with `--keep`),
fix the **orchestrator**, and re-run. Never weaken an assertion to make a drill green.
```

- [ ] **Step 16.12 — Add the Make target.** In `Makefile`:

```make
drills: ## Run the four WS-P2.1 recovery drills (quarterly cadence)
	./scripts/drill-1-dispatch-crash.sh
	./scripts/drill-2-evidence-recovery.sh
	./scripts/drill-3-external-merge.sh
	./scripts/drill-4-deploy-split-brain.sh
```

- [ ] **Step 16.13 — Commit.**
```bash
git add scripts/drill_common.sh scripts/drill-*.sh docs/operations/recovery-drills.md Makefile tests/architecture/test_drill_scripts.py && git commit -m "feat(drills): add four scripted recovery drills and the quarterly runbook (WS-P2.1 AC-010)"
```

---

### Task 17: AC-011 invariant scan

**Files:**
- **Create:** `tests/architecture/test_ac011_invariants.py`
- **Modify:** `tests/architecture/test_no_automatic_merge.py`
- **Test:** these files are the test.

**Interfaces:**
- **Consumes:** `orchestrator.main.create_app`; `orchestrator.kernel.states.{WorkUnitState, LEGAL_EDGES}`; `orchestrator.kernel.transitions.WORKER_EDGES`; `orchestrator.services.observations.BWS_TOKEN_SHAPE`; the filesystem under `src/orchestrator/**`, `.github/workflows/**`, and `git ls-files`.
- **Produces:** four assertion groups (auto-merge, repo-wide outbound, background loop, workers-cannot-complete) + a no-tracked-secret assertion.

**This creates the repo's FIRST repo-wide outbound scan.** Today there is only an import-*name* check (`test_scope_guards.py:10-25`, whose `forbidden` tuple does not include `httpx`) and two per-file source scans (`test_ws53_scope_guards.py`, `test_ws61_scope_guards.py`). The four legitimate `httpx` importers are `cli.py` (API client), `services/dispatch.py` (the one sanctioned push-out), `services/github_app.py` (App-token mint), `services/knowledge_promotions.py` (Brain submit). Everything else — above all the new reconciliation/recovery modules — is forbidden. Note that `services/evidence.py` (which gains `recover_evidence` in Task 9) is **not** on the allowlist either: it is a long-standing module that imports no `httpx`, and the repo-wide scan covers it exactly like every other non-allowlisted module.

- [ ] **Step 17.1 — Failing assertion (1): no auto-merge.** Extend `tests/architecture/test_no_automatic_merge.py`:

```python
from pathlib import Path

from orchestrator.kernel.states import LEGAL_EDGES, WorkUnitState
from orchestrator.kernel.transitions import WORKER_EDGES
from orchestrator.main import create_app

# ... existing test_workflows_never_merge_deploy_or_push_main stays unchanged ...


def test_no_api_or_review_endpoint_can_merge() -> None:
    paths = create_app().openapi()["paths"]

    assert [path for path in paths if "merge" in path.lower()] == []


def test_no_lifecycle_edge_expresses_a_merge_and_no_worker_edge_completes() -> None:
    assert [state for state in WorkUnitState if "merge" in state.value] == []
    # LEGAL_EDGES has no merge edge because there is no merge state to be an edge to.
    assert all(
        source in WorkUnitState and target in WorkUnitState for source, target in LEGAL_EDGES
    )
    # transitions.py:24-33 — WORKER_EDGES reaches SUBMITTED at most; never COMPLETED.
    assert [edge for edge in WORKER_EDGES if edge[1] is WorkUnitState.COMPLETED] == []


def test_no_source_file_calls_a_merge_api() -> None:
    offenders = [
        str(path)
        for path in Path("src").rglob("*.py")
        if any(
            marker in path.read_text().lower()
            for marker in ("gh pr merge", "/merges", "merge_method", "merge-method")
        )
    ]

    assert offenders == []
```

- [ ] **Step 17.2 — Run.** `uv run pytest tests/architecture/test_no_automatic_merge.py -q` → 4 passed. These should be green immediately: they *pin* an existing property, and pinning it is the deliverable.

- [ ] **Step 17.3 — Failing assertions (2)(3)(4)+secret.** Create `tests/architecture/test_ac011_invariants.py`:

```python
import ast
import subprocess
from pathlib import Path

from orchestrator.kernel.states import WorkUnitState
from orchestrator.kernel.transitions import WORKER_EDGES

# Reuse the service's own token-shape regex (observations.py:31) rather than restating it:
# the arch test then cannot drift from the guard the service actually enforces, and this
# file carries no token-shaped literal of its own.
from orchestrator.services.observations import BWS_TOKEN_SHAPE

SRC = Path("src/orchestrator")
ROUTES = SRC / "api" / "routes.py"

# ── (2) the first repo-wide outbound scan ────────────────────────────────────────
# The orchestrator process is push-only. It legitimately reaches the network in exactly
# four places; active pulling lives in the separate report-only reconciliation runner.
OUTBOUND_ALLOWLIST = frozenset(
    {
        SRC / "cli.py",                                # the API client
        SRC / "services" / "dispatch.py",              # the one sanctioned push-out
        SRC / "services" / "github_app.py",            # App-token mint
        SRC / "services" / "knowledge_promotions.py",  # Brain submit
    }
)
OUTBOUND_MARKERS = ("httpx.", "requests.", "coolify")

# The WS-P2.1 reconciliation/recovery modules are in the FORBIDDEN set, permanently.
# (recover_evidence lives in the long-standing services/evidence.py, which is not
# allowlisted either — the repo-wide scan above covers it like any other module.)
RECONCILIATION_FILES = (
    SRC / "services" / "reconciliation.py",
    SRC / "services" / "reconciliation_detection.py",
    SRC / "services" / "pr_bindings.py",
    SRC / "services" / "in_flight.py",
    SRC / "services" / "dead_letter.py",
    SRC / "services" / "consistency.py",
)


def test_no_outbound_call_outside_the_four_sanctioned_importers() -> None:
    offenders = sorted(
        str(path)
        for path in SRC.rglob("*.py")
        if path not in OUTBOUND_ALLOWLIST
        and any(marker in path.read_text().lower() for marker in OUTBOUND_MARKERS)
    )

    assert offenders == []


def test_each_allowlisted_importer_exists_and_actually_imports_httpx() -> None:
    # An allowlist entry that no longer needs the exemption is an unearned hole.
    for path in OUTBOUND_ALLOWLIST:
        assert path.exists(), f"{path} is allowlisted but does not exist"
        assert "import httpx" in path.read_text()


def test_the_reconciliation_and_recovery_modules_are_forbidden_from_the_network() -> None:
    assert not set(RECONCILIATION_FILES) & OUTBOUND_ALLOWLIST
    for path in RECONCILIATION_FILES:
        assert path.exists(), f"{path} must exist by Task 17"
        source = path.read_text().lower()
        assert "httpx" not in source
        assert "requests" not in source
        assert "coolify" not in source


def test_recover_evidence_lives_in_evidence_py_and_is_not_allowlisted() -> None:
    evidence = SRC / "services" / "evidence.py"
    source = evidence.read_text()

    assert evidence not in OUTBOUND_ALLOWLIST
    assert "def recover_evidence" in source
    assert "httpx" not in source.lower()


# ── (3) no background loop, scheduler, or cron ───────────────────────────────────
LOOP_MARKERS = (
    "backgroundtasks",
    "apscheduler",
    "create_task",
    "asyncio.run",
    "threading",
    "schedule.every",
    "crontab",
)


def test_no_module_declares_a_background_loop_or_scheduler() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        source = path.read_text()
        lowered = source.lower()
        offenders.extend(f"{path}:{marker}" for marker in LOOP_MARKERS if marker in lowered)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.While) and (
                (isinstance(node.test, ast.Constant) and node.test.value is True)
                or (isinstance(node.test, ast.Name) and node.test.id == "True")
            ):
                offenders.append(f"{path}:while True")

    assert sorted(offenders) == []


def test_no_workflow_declares_a_schedule() -> None:
    workflows = "\n".join(
        path.read_text().lower() for path in Path(".github/workflows").glob("*")
    )

    assert "schedule:" not in workflows
    assert "cron:" not in workflows


# ── (4) workers cannot complete ──────────────────────────────────────────────────
# Structural (no WORKER_EDGES target is COMPLETED) PLUS the per-endpoint hardcoded
# target — because "no worker edge to COMPLETED" is a non-sequitur for retry/cancel,
# which are HUMAN-surfaced, and HUMAN_EDGES *does* reach COMPLETED.
RECOVERY_ROUTE_FUNCTIONS = ("requeue", "recover_evidence")


def _state_attributes(function: ast.FunctionDef) -> set[str]:
    return {
        node.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "WorkUnitState"
    }


def test_no_worker_edge_targets_completed() -> None:
    assert [edge for edge in WORKER_EDGES if edge[1] is WorkUnitState.COMPLETED] == []


def test_each_recovery_endpoint_hardcodes_a_target_that_is_not_completed() -> None:
    source_text = ROUTES.read_text()
    tree = ast.parse(source_text)
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in RECOVERY_ROUTE_FUNCTIONS
    }
    assert set(functions) == set(RECOVERY_ROUTE_FUNCTIONS), "a recovery route is missing"

    for name, function in functions.items():
        segment = (ast.get_source_segment(source_text, function) or "").lower()
        assert "COMPLETED" not in _state_attributes(function), f"{name} names COMPLETED"
        assert "completed" not in segment.replace("completion", ""), name
    # requeue's hardcoded target is READY and nothing else.
    assert _state_attributes(functions["requeue"]) <= {"READY", "FAILED", "BLOCKED"}


# ── no tracked secret ────────────────────────────────────────────────────────────
# BWS_TOKEN_SHAPE (imported above) matches the FULL machine-account token, not a prefix:
# a pattern that stops early yields wrong fingerprints and false negatives.
SECRET_SHAPES = ("BWS_ACCESS_TOKEN=", "-----BEGIN OPENSSH PRIVATE KEY-----", "ghp_")


def test_no_tracked_file_carries_a_secret() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()
    offenders: list[str] = []
    for name in tracked:
        path = Path(name)
        if not path.is_file() or path.suffix in {".png", ".jpg", ".lock"}:
            continue
        try:
            source = path.read_text()
        except UnicodeDecodeError:
            continue
        if BWS_TOKEN_SHAPE.search(source) or any(shape in source for shape in SECRET_SHAPES):
            offenders.append(name)

    assert offenders == []
```

  Note: `SECRET_SHAPES` intentionally matches the *assignment* (`BWS_ACCESS_TOKEN=`), not a token literal. A tracked `.env.example` that documents the variable name with an empty value will trip this — that is the correct signal to move the example to a gitignored file or drop the `=`.

- [ ] **Step 17.4 — Run, read the failures honestly.** `uv run pytest tests/architecture/test_ac011_invariants.py -q`.
  - `test_no_outbound_call_outside_the_four_sanctioned_importers` may fail on a **comment or docstring** that merely mentions Coolify (`test_ws53` only forbids `"coolify api"` / `"coolify deploy"` in that one file, which implies a bare mention may exist). **The fix is to reword the comment** — e.g. `# the deploy monitor` instead of `# the Coolify deploy monitor`. **Never widen `OUTBOUND_ALLOWLIST` to make this green**: the allowlist is the invariant, not the escape hatch.
  - `test_the_reconciliation_and_recovery_modules_are_forbidden_from_the_network` fails if Tasks 1–13 named a module differently than the canonical six. Reconcile against the real tree, keeping every module in the **forbidden** set.
  - `test_each_recovery_endpoint_hardcodes_a_target_that_is_not_completed` fails if the route functions carry other names — update `RECOVERY_ROUTE_FUNCTIONS` from `routes.py`, keeping the assertion.

- [ ] **Step 17.5 — Run the full architecture suite.** `uv run pytest tests/architecture -q` → all pass, including the pinned POST and GET route inventories, the runner isolation guards, and the drill guards.

- [ ] **Step 17.6 — Run the security scanner and attach the evidence.** `PYTHONPATH="$HOME/Projects/security-standards/src" python3 -m security_scan.cli . --category security` → no BLOCK findings. The in-repo `test_no_tracked_file_carries_a_secret` is the CI-portable half (it does not depend on the scanner being installed on the runner); the scanner is the local gate.

- [ ] **Step 17.7 — Commit.**
```bash
git add tests/architecture/test_ac011_invariants.py tests/architecture/test_no_automatic_merge.py && git commit -m "test(arch): add the AC-011 invariant scan — first repo-wide outbound scan, no-loop, no-merge, workers-cannot-complete (WS-P2.1 AC-011)"
```

- [ ] **Step 17.8 — Full green.** `uv run pytest -q && uv run ruff check . && uv run pyright && shellcheck scripts/*.sh` → all clean. This is the AC-001..011 evidence that lands on the PR-head **Quality** check.

---

## Residual items carried out of Tasks 14–17

1. **`OBSERVATION_SOURCE_SYSTEMS` for deploy facts** — the runner uses the existing `"deployment_observation"` member (`models.py:60-67`), so **no migration**. If a future health/uptime fact needs a new member, that is a *listed* migration (design §13).
2. **`LEASE_DURATION` has no env override** (`kernel/leases.py:4`). Drills 1–2 age the lease with one SQL `UPDATE` on the throwaway DB. If a later workstream wants drills to be pure-API end to end, promote `LEASE_DURATION` into `Settings` — do **not** add an "expire this lease" API endpoint, which would be a live foot-gun in production.
3. **`reconcile_split_brain_stall_seconds` production default is 900.** The drill overrides it to 1. Confirm 900 sits above a normal post-deploy verification's worst case, and record the number in `docs/operations/recovery-drills.md`.
4. **The GET route inventory** (created in Task 10, extended in Step 14.10) is now a pin like the POST one: any new read surface must be added deliberately.
