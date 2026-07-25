# WS-P2.6 Traceability Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a bidirectional traceability query — `GET /api/v1/traceability` — that, given any node on the `intent ↔ unit ↔ PR ↔ commit ↔ artifact ↔ deployment ↔ observation` chain, returns the full ordered chain ("why is this code in production?") as structured JSON.

**Architecture:** A thin resolver + assembler in a new read-only service module. The resolver maps any entry key to anchor work unit(s) via query-time filters on the existing hub tables; the assembler composes each unit's chain by reusing the WS-P2.5 per-unit projection plus the existing release-artifact / deployment-observation fetchers and the reconciliation-condition / observation linkage. No new canonical data, no migration, no GUI, no markdown, no egress.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x (`Session`), Pydantic v2, pytest. Postgres-backed tests via the repo `session` fixture.

**Spec:** `docs/superpowers/specs/2026-07-25-traceability-query-design.md`

## Global Constraints

- **Read-only.** The module never writes, never transitions, never dispatches/deploys/writes to git. Pure projection over canonical rows, like `services/release_evidence_pack.py`.
- **Compose, don't reimplement.** Reuse `evidence_pack_projection`, `list_release_artifacts`, `list_deployment_observations`, `get_pr_binding`, and the `_correlated_unit` linkage pattern. No new graph walk.
- **Route input parsing raises `DomainError`, never stdlib.** Only `DomainError` and `APIAuthenticationError` have exception handlers (`main.py`); anything else is an unhandled HTTP 500. Wrap every `uuid.UUID(...)`, int coercion, and hex check and reject bad input up front (WS-P2.3).
- **New GET route → exact route-inventory set.** Add `/api/v1/traceability` to `tests/architecture/test_scope_guards.py::test_production_get_route_inventory_is_explicit` or CI reds. JSON only → **no** `NON_JSON_SUCCESS_PATHS` entry.
- **ws32/ws33 word guards.** In `services/traceability.py`, keep docstrings/prose free of the bare tokens `deploy`, `dispatch`, and `merges`. Suffixed forms (`deployment`, `deploys`, `dispatches`, `merge`, `merged`) and attribute/string keys (`row.merge_commit`, `"merge_commit"`) are fine — the guards match bare tokens in string literals/prose only.
- **`make check` exit 0 ≠ tests ran.** Read the `collected N items` count; run on a **clean tree** (`ruff format --check .` may red on pre-existing debt in untouched files — differential, not this change). Resolve Python tools from repo-local `.venv/bin`.
- **Auth posture:** `_actor: ActorDep`, no role gate — identical to the evidence-pack routes; full-fidelity JSON inside the trust boundary.

---

## File Structure

- **Create** `src/orchestrator/services/traceability.py` — `TraceabilityAnchor`, `resolve_anchors`, `build_chain`, `traceability_response`.
- **Modify** `src/orchestrator/api/schemas.py` — add the `Traceability*Response` / hop models.
- **Modify** `src/orchestrator/api/routes.py` — add `traceability_route` (`GET /traceability`) with up-front input validation.
- **Modify** `tests/architecture/test_scope_guards.py` — add the route to the GET inventory set.
- **Create** `tests/services/test_traceability.py` — resolver + assembler unit tests.
- **Create** `tests/api/test_traceability_api.py` — route auth / round-trip / malformed-input tests.

## Data reference (verified 2026-07-25)

- `ReleaseArtifactBinding` (hub): `work_unit_id`, `work_package_revision_id`, `source_repository`, `implementation_pr_number`, `source_commit`, `merge_commit`, `artifact_digest`, `artifact_tag`, `artifact_registry/repository/name`, `workflow_*`, `builder_*`, `provenance_*`, `sbom_*`, `recorded_at`, `id`.
- `UnitPrBinding`: PK `work_unit_id`, `pr_number`, `head_sha`.
- `DeploymentObservation`: `release_artifact_binding_id`, `implementation_work_unit_id`, `work_package_revision_id`, `environment`, `observed_artifact_digest`, `deployment_ref`, `deployment_url`, `deployer`, `observed_at`, `recorded_at`, `probe_summary`, `route_summary`, `auth_summary`, `dispatch_summary`, `status_summary`, `id`.
- `ReconciliationCondition`: FK `work_unit_id`, `observation_kind`, `condition_type`, `detail`, `resolution_generation`, `detected_at`, `observation_id`, `deployment_observation_id`, `id`. OPEN = no `ReconciliationResolution` with matching `condition_id`.
- `ReconciliationResolution`: `condition_id`, `decision`, `resolved_by`, `resolved_at`.
- `Observation`: `subject_type`, `subject_reference`, `source_system`, `observation_type`, `status`, `severity`, `summary`, `facts`, `observed_at`, `received_at`, `id`. Unit link: `subject_type == "work_unit" AND subject_reference == str(unit_id)`.

Reusable fetchers: `list_release_artifacts(session, work_unit_id) -> tuple[...] | DomainError`; `list_deployment_observations(session, binding_id) -> tuple[...] | DomainError`; `get_pr_binding(session, work_unit_id) -> UnitPrBinding | None`; `evidence_pack_projection(session, unit_id) -> dict` (keys: `unit`, `revision`, `approvals`, …).

---

### Task 1: Response schemas

**Files:**
- Modify: `src/orchestrator/api/schemas.py` (append near the `EvidencePack*Response` / `ReleaseEvidencePack*Response` block)
- Test: `tests/api/test_traceability_api.py` (new — schema construction test only in this task)

**Interfaces:**
- Produces (consumed by Tasks 3 & 4):
  - `TraceabilityAnchorResponse(matched_on: str, value: str)`
  - `TraceabilityIntentHop(revision: int, content_hash: str, source_path: str, source_commit: str, registered_by: str)`
  - `TraceabilityUnitHop(id: uuid.UUID, unit_key: str, title: str, state: str, authority_fingerprint: str, authority_approved_by: str | None, authority_decision: str | None)`
  - `TraceabilityPrHop(pr_number: int, head_sha: str)`
  - `TraceabilityCommitHop(source_repository: str, source_commit: str, merge_commit: str, implementation_pr_number: int | None)`
  - `TraceabilityArtifactHop(artifact_digest: str, artifact_registry: str, artifact_repository: str, artifact_name: str, artifact_tag: str | None, workflow_run_url: str | None, builder_id: str | None, provenance_digest: str | None, sbom_digest: str | None)`
  - `TraceabilityDeploymentHop(environment: str, observed_artifact_digest: str, digest_matches: bool, deployment_ref: str, deployment_url: str, deployer: str, observed_at: datetime, status_summary: dict[str, Any], probe_summary: dict[str, Any])`
  - `TraceabilityConditionHop(observation_kind: str, condition_type: str, detail: str, resolution_generation: int, detected_at: datetime, open: bool, resolution_decision: str | None)`
  - `TraceabilityObservationHop(source_system: str, observation_type: str, status: str, severity: str, summary: str, observed_at: datetime)`
  - `TraceabilityChainResponse(intent, unit, pr: TraceabilityPrHop | None, commit: list[TraceabilityCommitHop], artifact: list[TraceabilityArtifactHop], deployment: list[TraceabilityDeploymentHop], conditions: list[TraceabilityConditionHop], observations: list[TraceabilityObservationHop])`
  - `TraceabilityResponse(anchor: TraceabilityAnchorResponse, chains: list[TraceabilityChainResponse])`

> Note: `commit`/`artifact` are lists because a unit may have >1 `ReleaseArtifactBinding` (one per build). `pr` is a single optional hop (one `UnitPrBinding` per unit). `deployment`/`conditions`/`observations` are lists.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_traceability_api.py`:

```python
from datetime import UTC, datetime

from orchestrator.api.schemas import (
    TraceabilityAnchorResponse,
    TraceabilityChainResponse,
    TraceabilityDeploymentHop,
    TraceabilityIntentHop,
    TraceabilityResponse,
    TraceabilityUnitHop,
)


def test_traceability_response_is_json_serializable():
    chain = TraceabilityChainResponse(
        intent=TraceabilityIntentHop(
            revision=1,
            content_hash="sha256:x",
            source_path="intent.md",
            source_commit="a" * 40,
            registered_by="human-1",
        ),
        unit=TraceabilityUnitHop(
            id=__import__("uuid").UUID(int=1),
            unit_key="u-1",
            title="Unit 1",
            state="completed",
            authority_fingerprint="fp",
            authority_approved_by="human-1",
            authority_decision="approved",
        ),
        pr=None,
        commit=[],
        artifact=[],
        deployment=[
            TraceabilityDeploymentHop(
                environment="prod",
                observed_artifact_digest="sha256:d",
                digest_matches=True,
                deployment_ref="ref",
                deployment_url="https://x",
                deployer="deployer-1",
                observed_at=datetime(2026, 7, 25, tzinfo=UTC),
                status_summary={"code": 200},
                probe_summary={},
            )
        ],
        conditions=[],
        observations=[],
    )
    response = TraceabilityResponse(
        anchor=TraceabilityAnchorResponse(matched_on="environment", value="prod"),
        chains=[chain],
    )
    dumped = response.model_dump(mode="json")
    assert dumped["anchor"]["matched_on"] == "environment"
    assert dumped["chains"][0]["deployment"][0]["digest_matches"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/api/test_traceability_api.py::test_traceability_response_is_json_serializable -v`
Expected: FAIL with `ImportError: cannot import name 'TraceabilityResponse'`.

- [ ] **Step 3: Add the schema models**

In `src/orchestrator/api/schemas.py`, append the models listed under **Produces** above. Use the existing file's conventions: `class X(BaseModel)`, `model_config = ConfigDict(...)` only if the neighbours use it, plain typed fields. Import `Any` from `typing` and `datetime`/`uuid` if not already imported. Example for two of them (write all):

```python
class TraceabilityAnchorResponse(BaseModel):
    matched_on: str
    value: str


class TraceabilityDeploymentHop(BaseModel):
    environment: str
    observed_artifact_digest: str
    digest_matches: bool
    deployment_ref: str
    deployment_url: str
    deployer: str
    observed_at: datetime
    status_summary: dict[str, Any]
    probe_summary: dict[str, Any]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/api/test_traceability_api.py::test_traceability_response_is_json_serializable -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/api/schemas.py tests/api/test_traceability_api.py
git commit -m "feat(wsp26): traceability response schemas"
```

---

### Task 2: Anchor resolver

**Files:**
- Create: `src/orchestrator/services/traceability.py`
- Test: `tests/services/test_traceability.py` (new)

**Interfaces:**
- Produces (consumed by Tasks 3 & 4):
  - `@dataclass(frozen=True) class TraceabilityAnchor` with fields `kind: str`, `work_unit_id: uuid.UUID | None`, `revision_id: uuid.UUID | None`, `artifact_digest: str | None`, `commit: str | None`, `pr_number: int | None`, `source_repository: str | None`, `environment: str | None`, and a property `display_value: str` returning the human string for the active anchor.
  - `resolve_anchors(session: Session, anchor: TraceabilityAnchor) -> tuple[uuid.UUID, ...]` — ordered unit ids. Named anchors (`work_unit`/`revision`) raise `DomainError` when the entity does not exist; filter anchors return `()` on no match.
- Consumes: models `WorkUnit`, `WorkPackageRevision`, `ReleaseArtifactBinding`, `UnitPrBinding`, `DeploymentObservation`; `get_pr_binding`.

> The resolver assumes a **valid, exactly-one** anchor — building/validating the anchor from raw query params (and raising `DomainError` on bad input) is the route's job in Task 4.

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_traceability.py`. Reuse the existing fixture helpers (`register_revision`, `register_approved_unit`, `record_release_artifact`) — mirror `tests/services/test_release_artifacts.py`'s `completed_unit` helper for setup. Minimal first tests:

```python
import uuid

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.services.traceability import TraceabilityAnchor, resolve_anchors
from tests.services.test_release_artifacts import completed_unit, DIGEST  # reuse setup + artifact


def test_resolve_by_work_unit_id(session: Session):
    unit = completed_unit(session)
    anchor = TraceabilityAnchor(kind="work_unit", work_unit_id=unit.id)
    assert resolve_anchors(session, anchor) == (unit.id,)


def test_resolve_named_unit_missing_raises(session: Session):
    anchor = TraceabilityAnchor(kind="work_unit", work_unit_id=uuid.uuid4())
    with pytest.raises(DomainError) as exc:
        resolve_anchors(session, anchor)
    assert exc.value.code == "work_unit_not_found"


def test_resolve_by_artifact_digest_filter_empty_is_ok(session: Session):
    anchor = TraceabilityAnchor(kind="artifact_digest", artifact_digest="sha256:" + "0" * 64)
    assert resolve_anchors(session, anchor) == ()
```

> The implementer MUST also add: `test_resolve_by_revision_id` (fan-out to all units, ordered by `unit_key`), `test_resolve_by_artifact_digest` (matching a recorded binding → its unit), `test_resolve_by_commit` (matches `source_commit` OR `merge_commit`), `test_resolve_by_pr` (with and without `source_repository`; repo-less falls back to `UnitPrBinding`), `test_resolve_by_environment_picks_latest_per_unit` (two `DeploymentObservation`s for the same unit+env → only the newest `observed_at` selects the unit, and the unit appears once), and `test_resolve_named_revision_missing_raises` (`revision_not_found`). Set up artifacts via `record_release_artifact`, deployments via `record_deployment_observation`, PR bindings via `record_pr_binding`/`upsert_pr_binding` (see `services/pr_bindings.py`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/services/test_traceability.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.services.traceability'`.

- [ ] **Step 3: Write the resolver**

Create `src/orchestrator/services/traceability.py`. Module docstring must avoid bare `deploy`/`dispatch`/`merges` (see Global Constraints). Example opening:

```python
"""Bidirectional traceability query (WS-P2.6).

Resolves any node on the intent -> work unit -> PR -> commit -> artifact -> deployment ->
observation chain to the full ordered chain, answering "why is this code in production?". It
reads canonical rows only and composes the WS-P2.5 projections and the release-artifact /
deployment-observation fetchers; it never writes, never transitions, and never touches git.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import (
    DeploymentObservation,
    ReleaseArtifactBinding,
    UnitPrBinding,
    WorkPackageRevision,
    WorkUnit,
)


@dataclass(frozen=True)
class TraceabilityAnchor:
    kind: str
    work_unit_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    artifact_digest: str | None = None
    commit: str | None = None
    pr_number: int | None = None
    source_repository: str | None = None
    environment: str | None = None

    @property
    def display_value(self) -> str:
        value = {
            "work_unit": self.work_unit_id,
            "revision": self.revision_id,
            "artifact_digest": self.artifact_digest,
            "commit": self.commit,
            "pr": self.pr_number,
            "environment": self.environment,
        }[self.kind]
        return str(value)


def resolve_anchors(session: Session, anchor: TraceabilityAnchor) -> tuple[uuid.UUID, ...]:
    if anchor.kind == "work_unit":
        if session.get(WorkUnit, anchor.work_unit_id) is None:
            raise DomainError("work_unit_not_found", "work unit does not exist", None)
        return (anchor.work_unit_id,)  # type: ignore[return-value]
    if anchor.kind == "revision":
        if session.get(WorkPackageRevision, anchor.revision_id) is None:
            raise DomainError("revision_not_found", "package revision does not exist", None)
        return tuple(
            session.scalars(
                select(WorkUnit.id)
                .where(WorkUnit.work_package_revision_id == anchor.revision_id)
                .order_by(WorkUnit.unit_key)
            )
        )
    if anchor.kind == "artifact_digest":
        return _distinct_units(
            session,
            select(ReleaseArtifactBinding.work_unit_id).where(
                ReleaseArtifactBinding.artifact_digest == anchor.artifact_digest
            ),
        )
    if anchor.kind == "commit":
        return _distinct_units(
            session,
            select(ReleaseArtifactBinding.work_unit_id).where(
                or_(
                    ReleaseArtifactBinding.source_commit == anchor.commit,
                    ReleaseArtifactBinding.merge_commit == anchor.commit,
                )
            ),
        )
    if anchor.kind == "pr":
        return _resolve_pr(session, anchor)
    if anchor.kind == "environment":
        return _resolve_environment(session, anchor.environment)
    raise DomainError("traceability_anchor_invalid", f"unknown anchor kind {anchor.kind}", None)


def _distinct_units(session: Session, stmt) -> tuple[uuid.UUID, ...]:
    # Preserve first-seen order for a stable response; de-duplicate a digest/commit shared by
    # multiple bindings of the same unit.
    seen: dict[uuid.UUID, None] = {}
    for unit_id in session.scalars(stmt):
        seen.setdefault(unit_id, None)
    return tuple(seen)


def _resolve_pr(session: Session, anchor: TraceabilityAnchor) -> tuple[uuid.UUID, ...]:
    if anchor.source_repository is not None:
        return _distinct_units(
            session,
            select(ReleaseArtifactBinding.work_unit_id).where(
                ReleaseArtifactBinding.source_repository == anchor.source_repository,
                ReleaseArtifactBinding.implementation_pr_number == anchor.pr_number,
            ),
        )
    return _distinct_units(
        session,
        select(UnitPrBinding.work_unit_id).where(UnitPrBinding.pr_number == anchor.pr_number),
    )


def _resolve_environment(session: Session, environment: str | None) -> tuple[uuid.UUID, ...]:
    # "What is in this environment now" = the latest observation per unit for that environment.
    rows = session.scalars(
        select(DeploymentObservation)
        .where(DeploymentObservation.environment == environment)
        .order_by(
            DeploymentObservation.observed_at.desc(),
            DeploymentObservation.recorded_at.desc(),
            DeploymentObservation.id.desc(),
        )
    )
    seen: dict[uuid.UUID, None] = {}
    for row in rows:
        seen.setdefault(row.implementation_work_unit_id, None)
    return tuple(seen)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/services/test_traceability.py -v`
Expected: PASS (all resolver tests, including the ones you added in Step 1's note).

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/traceability.py tests/services/test_traceability.py
git commit -m "feat(wsp26): traceability anchor resolver"
```

---

### Task 3: Chain assembler + response

**Files:**
- Modify: `src/orchestrator/services/traceability.py`
- Test: `tests/services/test_traceability.py`

**Interfaces:**
- Consumes: `TraceabilityAnchor`, `resolve_anchors` (Task 2); all `Traceability*` schemas (Task 1); `evidence_pack_projection`, `list_release_artifacts`, `list_deployment_observations`, `get_pr_binding`; models `ReconciliationCondition`, `ReconciliationResolution`, `Observation`.
- Produces (consumed by Task 4):
  - `build_chain(session: Session, unit_id: uuid.UUID) -> TraceabilityChainResponse`
  - `traceability_response(session: Session, anchor: TraceabilityAnchor) -> TraceabilityResponse`

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_traceability.py`:

```python
from orchestrator.services.traceability import build_chain, traceability_response


def test_build_chain_includes_intent_unit_and_artifact(session: Session):
    unit = completed_unit(session)  # this helper records a ReleaseArtifactBinding with DIGEST
    chain = build_chain(session, unit.id)
    assert chain.unit.id == unit.id
    assert chain.intent.revision == 1
    assert any(a.artifact_digest == DIGEST for a in chain.artifact)


def test_build_chain_observation_tail_includes_conditions_and_observations(session: Session):
    unit = completed_unit(session)
    _record_condition_for(session, unit)      # helper: record a ReconciliationCondition
    _record_observation_for(session, unit)    # helper: record an Observation subject=work_unit:<id>
    chain = build_chain(session, unit.id)
    assert len(chain.conditions) == 1
    assert chain.conditions[0].open is True
    assert len(chain.observations) == 1


def test_build_chain_empty_tail_when_none(session: Session):
    unit = completed_unit(session)
    chain = build_chain(session, unit.id)
    assert chain.conditions == []
    assert chain.observations == []


def test_traceability_response_orders_chains_by_resolution(session: Session):
    unit = completed_unit(session)
    response = traceability_response(
        session, TraceabilityAnchor(kind="work_unit", work_unit_id=unit.id)
    )
    assert response.anchor.matched_on == "work_unit"
    assert [c.unit.id for c in response.chains] == [unit.id]
```

> The implementer writes `_record_condition_for` / `_record_observation_for` using `services.reconciliation.record_reconciliation_condition` (or the lower-level path the reconciliation tests use) and `services.observations.record_observation` with an `ObservationCommand` where `subject_type="work_unit"`, `subject_reference=str(unit.id)`. Also add `test_build_chain_pr_and_deployment_hops` and `test_deployment_digest_matches_flag` (record a `DeploymentObservation` whose `observed_artifact_digest == DIGEST` and assert `digest_matches is True`; a differing digest → `False`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/services/test_traceability.py -k "build_chain or traceability_response" -v`
Expected: FAIL with `ImportError: cannot import name 'build_chain'`.

- [ ] **Step 3: Write the assembler**

Append to `src/orchestrator/services/traceability.py`. Add imports for the schemas, the fetchers, and the extra models. Key logic — the observation `open` flag is a set-difference against `ReconciliationResolution.condition_id` (mirrors evidence supersession):

```python
def build_chain(session: Session, unit_id: uuid.UUID) -> TraceabilityChainResponse:
    projection = evidence_pack_projection(session, unit_id)  # raises work_unit_not_found if absent
    unit = projection["unit"]
    revision = projection["revision"]
    authority_approval = next(
        (a for a in projection["approvals"] if a.subject_type == "authority"), None
    )

    artifacts = _unwrap(list_release_artifacts(session, unit_id))
    pr_binding = get_pr_binding(session, unit_id)

    deployment_hops: list[TraceabilityDeploymentHop] = []
    for binding in artifacts:
        for obs in _unwrap(list_deployment_observations(session, binding.id)):
            deployment_hops.append(
                TraceabilityDeploymentHop(
                    environment=obs.environment,
                    observed_artifact_digest=obs.observed_artifact_digest,
                    digest_matches=obs.observed_artifact_digest == binding.artifact_digest,
                    deployment_ref=obs.deployment_ref,
                    deployment_url=obs.deployment_url,
                    deployer=obs.deployer,
                    observed_at=obs.observed_at,
                    status_summary=obs.status_summary,
                    probe_summary=obs.probe_summary,
                )
            )

    conditions = tuple(
        session.scalars(
            select(ReconciliationCondition)
            .where(ReconciliationCondition.work_unit_id == unit_id)
            .order_by(ReconciliationCondition.detected_at, ReconciliationCondition.id)
        )
    )
    resolutions = {
        row.condition_id: row
        for row in session.scalars(
            select(ReconciliationResolution).where(
                ReconciliationResolution.condition_id.in_([c.id for c in conditions])
            )
        )
    } if conditions else {}

    observations = tuple(
        session.scalars(
            select(Observation)
            .where(
                Observation.subject_type == "work_unit",
                Observation.subject_reference == str(unit_id),
            )
            .order_by(Observation.observed_at, Observation.received_at, Observation.id)
        )
    )

    return TraceabilityChainResponse(
        intent=TraceabilityIntentHop(
            revision=revision.revision,
            content_hash=revision.content_hash,
            source_path=revision.source_path,
            source_commit=revision.source_commit,
            registered_by=revision.registered_by,
        ),
        unit=TraceabilityUnitHop(
            id=unit.id,
            unit_key=unit.unit_key,
            title=unit.title,
            state=unit.state,
            authority_fingerprint=unit.authority_fingerprint,
            authority_approved_by=authority_approval.approved_by if authority_approval else None,
            authority_decision=authority_approval.decision if authority_approval else None,
        ),
        pr=(
            TraceabilityPrHop(pr_number=pr_binding.pr_number, head_sha=pr_binding.head_sha)
            if pr_binding is not None
            else None
        ),
        commit=[
            TraceabilityCommitHop(
                source_repository=b.source_repository,
                source_commit=b.source_commit,
                merge_commit=b.merge_commit,
                implementation_pr_number=b.implementation_pr_number,
            )
            for b in artifacts
        ],
        artifact=[
            TraceabilityArtifactHop(
                artifact_digest=b.artifact_digest,
                artifact_registry=b.artifact_registry,
                artifact_repository=b.artifact_repository,
                artifact_name=b.artifact_name,
                artifact_tag=b.artifact_tag,
                workflow_run_url=b.workflow_run_url,
                builder_id=b.builder_id,
                provenance_digest=b.provenance_digest,
                sbom_digest=b.sbom_digest,
            )
            for b in artifacts
        ],
        deployment=deployment_hops,
        conditions=[
            TraceabilityConditionHop(
                observation_kind=c.observation_kind,
                condition_type=c.condition_type,
                detail=c.detail,
                resolution_generation=c.resolution_generation,
                detected_at=c.detected_at,
                open=c.id not in resolutions,
                resolution_decision=(
                    resolutions[c.id].decision if c.id in resolutions else None
                ),
            )
            for c in conditions
        ],
        observations=[
            TraceabilityObservationHop(
                source_system=o.source_system,
                observation_type=o.observation_type,
                status=o.status,
                severity=o.severity,
                summary=o.summary,
                observed_at=o.observed_at,
            )
            for o in observations
        ],
    )


def _unwrap(result):
    # list_* fetchers return `tuple | DomainError`; inside build_chain the unit is known to exist
    # (evidence_pack_projection already validated it), so a DomainError here is a real bug.
    if isinstance(result, DomainError):
        raise result
    return result


def traceability_response(
    session: Session, anchor: TraceabilityAnchor
) -> TraceabilityResponse:
    unit_ids = resolve_anchors(session, anchor)
    return TraceabilityResponse(
        anchor=TraceabilityAnchorResponse(matched_on=anchor.kind, value=anchor.display_value),
        chains=[build_chain(session, unit_id) for unit_id in unit_ids],
    )
```

Add the new imports at the top of the module:

```python
from orchestrator.api.schemas import (
    TraceabilityAnchorResponse,
    TraceabilityArtifactHop,
    TraceabilityChainResponse,
    TraceabilityCommitHop,
    TraceabilityConditionHop,
    TraceabilityDeploymentHop,
    TraceabilityIntentHop,
    TraceabilityObservationHop,
    TraceabilityPrHop,
    TraceabilityResponse,
    TraceabilityUnitHop,
)
from orchestrator.persistence.models import (
    Observation,
    ReconciliationCondition,
    ReconciliationResolution,
)
from orchestrator.services.deployment_observations import list_deployment_observations
from orchestrator.services.evidence_pack import evidence_pack_projection
from orchestrator.services.pr_bindings import get_pr_binding
from orchestrator.services.release_artifacts import list_release_artifacts
```

> Watch for an import cycle: `services/traceability.py` imports from `api/schemas.py`. `release_evidence_pack.py` already imports schemas the same way, so this pattern is safe — mirror it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/services/test_traceability.py -v`
Expected: PASS (all resolver + assembler tests).

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/traceability.py tests/services/test_traceability.py
git commit -m "feat(wsp26): traceability chain assembler + response"
```

---

### Task 4: JSON route + input validation + route inventory

**Files:**
- Modify: `src/orchestrator/api/routes.py`
- Modify: `tests/architecture/test_scope_guards.py:131-154` (the GET inventory set)
- Test: `tests/api/test_traceability_api.py`

**Interfaces:**
- Consumes: `TraceabilityAnchor`, `traceability_response` (Tasks 2–3); `TraceabilityResponse` (Task 1); `ActorDep`, `SessionDep`, `DomainError`.
- Produces: `GET /api/v1/traceability` returning `TraceabilityResponse`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_traceability_api.py`. Mirror the auth/client pattern from an existing api test (e.g. `tests/api/test_consistency_api.py`) for the authed `TestClient` and header fixtures.

```python
def test_traceability_requires_auth(client):
    resp = client.get("/api/v1/traceability", params={"environment": "prod"})
    assert resp.status_code == 401


def test_traceability_no_anchor_is_400(authed_client):
    resp = authed_client.get("/api/v1/traceability")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "traceability_anchor_required"


def test_traceability_two_anchors_is_400(authed_client):
    resp = authed_client.get(
        "/api/v1/traceability", params={"environment": "prod", "commit": "a" * 40}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "traceability_anchor_ambiguous"


def test_traceability_bad_uuid_is_400_not_500(authed_client):
    resp = authed_client.get("/api/v1/traceability", params={"work_unit_id": "not-a-uuid"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_work_unit_id"


def test_traceability_bad_commit_is_400(authed_client):
    resp = authed_client.get("/api/v1/traceability", params={"commit": "xyz"})
    assert resp.status_code == 400


def test_traceability_environment_roundtrip_empty(authed_client):
    resp = authed_client.get("/api/v1/traceability", params={"environment": "prod"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["anchor"] == {"matched_on": "environment", "value": "prod"}
    assert body["chains"] == []
```

> Match the exact error-envelope shape to how `DomainError` renders in this app (check `test_api_errors.py` for whether it is `resp.json()["error"]["code"]` or `resp.json()["code"]`; adjust the asserts to the real shape). Also add a positive round-trip test that seeds a unit + artifact and asserts a non-empty chain via `work_unit_id`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/api/test_traceability_api.py -v`
Expected: FAIL — route returns 404 (not registered) / import errors.

- [ ] **Step 3: Add the route with up-front validation**

In `src/orchestrator/api/routes.py`, import `TraceabilityResponse` and the service, then add:

```python
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")  # add `import re` at top if absent


@router.get("/traceability", response_model=TraceabilityResponse)
def traceability_route(
    _actor: ActorDep,
    session: SessionDep,
    work_unit_id: str | None = None,
    revision_id: str | None = None,
    artifact_digest: str | None = None,
    commit: str | None = None,
    pr_number: int | None = None,
    source_repository: str | None = None,
    environment: str | None = None,
) -> object:
    anchor = _parse_traceability_anchor(
        work_unit_id=work_unit_id,
        revision_id=revision_id,
        artifact_digest=artifact_digest,
        commit=commit,
        pr_number=pr_number,
        source_repository=source_repository,
        environment=environment,
    )
    return traceability_response(session, anchor)


def _parse_traceability_anchor(
    *,
    work_unit_id: str | None,
    revision_id: str | None,
    artifact_digest: str | None,
    commit: str | None,
    pr_number: int | None,
    source_repository: str | None,
    environment: str | None,
) -> TraceabilityAnchor:
    provided = [
        ("work_unit", work_unit_id),
        ("revision", revision_id),
        ("artifact_digest", artifact_digest),
        ("commit", commit),
        ("pr", pr_number),
        ("environment", environment),
    ]
    active = [(kind, value) for kind, value in provided if value is not None]
    if not active:
        raise DomainError("traceability_anchor_required", "provide exactly one anchor", None)
    if len(active) > 1:
        raise DomainError(
            "traceability_anchor_ambiguous", "provide exactly one anchor", None
        )
    if source_repository is not None and pr_number is None:
        raise DomainError(
            "traceability_anchor_invalid", "source_repository requires pr_number", None
        )
    kind, _ = active[0]
    if kind == "work_unit":
        return TraceabilityAnchor(kind=kind, work_unit_id=_parse_uuid(work_unit_id, "work_unit_id"))
    if kind == "revision":
        return TraceabilityAnchor(kind=kind, revision_id=_parse_uuid(revision_id, "revision_id"))
    if kind == "commit":
        if commit is None or _COMMIT_RE.fullmatch(commit) is None:
            raise DomainError("invalid_commit", "commit must be a 40-char hex sha", None)
        return TraceabilityAnchor(kind=kind, commit=commit)
    if kind == "pr":
        if pr_number is None or pr_number <= 0:
            raise DomainError("invalid_pr_number", "pr_number must be positive", None)
        return TraceabilityAnchor(
            kind=kind, pr_number=pr_number, source_repository=source_repository
        )
    if kind == "artifact_digest":
        return TraceabilityAnchor(kind=kind, artifact_digest=artifact_digest)
    return TraceabilityAnchor(kind=kind, environment=environment)


def _parse_uuid(value: str | None, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        raise DomainError(f"invalid_{field}", f"{field} must be a UUID", None) from None
```

Confirm `import re` and `import uuid` are present at the top of `routes.py` (add if missing), and add `from orchestrator.services.traceability import TraceabilityAnchor, traceability_response` and `TraceabilityResponse` to the schema imports.

- [ ] **Step 4: Add the route to the GET inventory**

In `tests/architecture/test_scope_guards.py`, add `"/api/v1/traceability",` to the set asserted in `test_production_get_route_inventory_is_explicit` (the `assert observed == {...}` block).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/api/test_traceability_api.py tests/architecture/test_scope_guards.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/api/routes.py tests/architecture/test_scope_guards.py tests/api/test_traceability_api.py
git commit -m "feat(wsp26): GET /api/v1/traceability route + inventory"
```

---

### Task 5: Full-gate verification + scope-guard sweep

**Files:** none (verification only)

- [ ] **Step 1: Run the new module through the architecture word guards**

Run: `.venv/bin/pytest tests/architecture/test_ws32_scope_guards.py tests/architecture/test_ws33_scope_guards.py tests/architecture/test_scope_guards.py -v`
Expected: PASS. If ws32/ws33 red on `services/traceability.py`, a docstring/string used a bare `deploy`/`dispatch`/`merges` — reword (suffix it) and re-run.

- [ ] **Step 2: Run the full gate on a clean tree**

Run: `git status` (confirm only the intended files changed), then `make check`.
Expected: green. **Read the `collected N items` line** — confirm the traceability tests are in the count, not swallowed by exit-5. If `ruff format --check .` reds on a file you did not touch, that is pre-existing debt (differential) — note it, do not "fix" unrelated files.

- [ ] **Step 3: `/code-review` the diff**

Run `/code-review` over the branch diff against the standards. Address correctness and simplification findings.

- [ ] **Step 4: Commit any review fixes**

```bash
git add -A && git commit -m "polish(wsp26): review fixes"
```

---

## Self-Review (completed during authoring)

**Spec coverage:**
- Full bidirectional resolver, 6 entry keys, no migration → Task 2 (`resolve_anchors`). ✓
- "Why in production?" chain, JSON only → Tasks 3 (`build_chain`) + 4 (route). ✓
- Observation tail = `ReconciliationCondition` + `Observation` → Task 3. ✓
- `digest_matches` on deployment hop → Task 1 schema + Task 3 assembler. ✓
- Route input parsing raises `DomainError` (no 500s) → Task 4 `_parse_traceability_anchor`/`_parse_uuid`. ✓
- Route-inventory exact set → Task 4 Step 4. ✓
- ws32/ws33 word-guard hygiene → Global Constraints + Task 5 Step 1. ✓
- Named-not-found → `DomainError`; filter-empty → `chains: []` → Task 2 resolver + Task 4 test. ✓
- Clean-tree `make check`, collected-count → Task 5. ✓

**Placeholder scan:** the only non-verbatim test code is explicitly delegated with named helpers/fixtures the implementer writes (Task 2 Step 1 note, Task 3 `_record_condition_for`/`_record_observation_for`); every core function shows full code.

**Type consistency:** `TraceabilityAnchor.kind` values (`work_unit`/`revision`/`artifact_digest`/`commit`/`pr`/`environment`) are identical across the resolver switch (Task 2), the route parser (Task 4), and `display_value`/`matched_on` (Tasks 2–3). Schema names match between Task 1 (Produces), Task 3 imports, and Task 4. Fetcher signatures (`list_release_artifacts`, `list_deployment_observations`, `get_pr_binding`) match the verified source.
