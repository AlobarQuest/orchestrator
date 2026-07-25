# Per-Release Evidence Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose a per-release Evidence Pack — every work unit's Increment-1 pack in a package revision, plus that revision's release artifact bindings and deployment observations — as structured JSON and a read-only `/review` GUI page.

**Architecture:** One assembler, `release_evidence_pack_response(session, revision_id)`, composes the existing per-unit `evidence_pack_response`/`evidence_pack_projection` and maps the revision's `ReleaseArtifactBinding` / `DeploymentObservation` rows (both already carry `work_package_revision_id`) into existing response schemas. The JSON route (`/api/v1/revisions/{revision_id}/evidence-pack`, auth-only) and the GUI route (`/review/revisions/{revision_id}/evidence-pack`, forward-auth `_human`) both call it.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.x (ORM `select`), Pydantic v2 (`model_validate`, `from_attributes`), Jinja2 templates, pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- Python 3.12+.
- **No database migration** — the pack assembles from existing rows only.
- **Compose, don't reimplement:** reuse `evidence_pack_projection` + `evidence_pack_response` per unit; do not duplicate per-unit assembly.
- **Full fidelity, no redaction, no markdown, no PR comment.** JSON is auth-only; GUI is forward-auth `_human`. Neither leaves the trust boundary, so approver identity and rationale stay present (redaction exists only for the Increment-1 markdown PR relay, which this increment does not build).
- **JSON route is authentication-only, no role gate** (`_actor: ActorDep`), matching the per-unit evidence-pack route — the runner's WORKER credential must be able to read it.
- **The new service module MUST be added to `WS53_POST_DEPLOY_PATHS`** in `tests/architecture/test_ws32_scope_guards.py` (it composes deployment observations; the scope guard forbids the bare tokens `deploy`/`dispatch` in non-allowlisted modules). `api/routes.py` and `api/schemas.py` are already in `WS42_DISPATCH_PATHS`; `web.py` is in neither and must stay clean (its route body only calls the assembler and `_render`).
- **Deterministic ordering:** units by `unit_key`; artifacts and deployments by `(recorded_at, id)`.
- **Before declaring done:** `make check` green on a **clean working tree**; read the `collected N items` count (exit 0 alone is not proof tests ran); run `ruff format` (not just `ruff check`) before every commit; then `/code-review`.
- Work happens on branch `ws-p2.5-inc2-release-evidence-pack` (already created).

---

### Task 1: Per-release assembler, schemas, and JSON route

**Files:**
- Create: `src/orchestrator/services/release_evidence_pack.py`
- Modify: `src/orchestrator/api/schemas.py` (append new response models at end of file)
- Modify: `src/orchestrator/api/routes.py` (imports; new GET route after the per-unit markdown route at ~line 557)
- Modify: `tests/architecture/test_ws32_scope_guards.py` (add the new module to `WS53_POST_DEPLOY_PATHS`)
- Modify: `tests/architecture/test_scope_guards.py` (add the new GET route to the explicit GET-route inventory)
- Test: `tests/api/test_release_evidence_pack_api.py`

**Interfaces:**
- Consumes (already exist):
  - `orchestrator.services.evidence_pack.evidence_pack_projection(session, unit_id) -> dict`
  - `orchestrator.services.evidence_pack.evidence_pack_response(projection) -> EvidencePackResponse`
  - `orchestrator.api.schemas.EvidencePackResponse`, `ReleaseArtifactResponse`, `DeploymentObservationResponse` (all with `from_attributes=True` where they map ORM rows)
  - Models `WorkPackageRevision`, `WorkUnit`, `ReleaseArtifactBinding`, `DeploymentObservation`
  - Test helpers `tests.api.test_release_artifacts_api.completed_unit`, `release_body`; `tests.api.test_deployment_observations_api.observation_body`; header dicts `AUTHORITY, HUMAN, SYSTEM, WORKER` from `tests.api.test_lifecycle_api`
- Produces (later tasks rely on these exact names):
  - `orchestrator.services.release_evidence_pack.release_evidence_pack_response(session: Session, revision_id: uuid.UUID) -> ReleaseEvidencePackResponse`
  - `orchestrator.api.schemas.ReleaseEvidencePackResponse`, `ReleaseEvidencePackRevisionResponse`
  - JSON route `GET /api/v1/revisions/{revision_id}/evidence-pack`

- [ ] **Step 1: Write the failing API test**

Create `tests/api/test_release_evidence_pack_api.py`:

```python
"""GET /revisions/{revision_id}/evidence-pack (WS-P2.5 Increment 2).

The per-release evidence pack: composes every unit's per-unit pack in a package revision with
that revision's release artifact bindings and deployment observations. Authentication-only,
like the per-unit route; full-fidelity JSON (approver identity/rationale are NOT redacted --
redaction exists only for the per-unit markdown PR relay, which this increment does not build).
"""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Adjudication, Evidence, WorkUnit
from tests.api.test_deployment_observations_api import observation_body
from tests.api.test_lifecycle_api import AUTHORITY, HUMAN, SYSTEM, WORKER
from tests.api.test_release_artifacts_api import completed_unit, release_body

DIGEST = "sha256:" + "a" * 64


def _release_with_units_artifact_and_deployment(
    db_client: TestClient, migrated_engine: Engine
) -> tuple[str, str]:
    """A revision with: a completed impl unit, a second (draft) unit, one release artifact
    binding, and one deployment observation (which itself mints a post-deploy unit). Returns
    (revision_id, impl_unit_id). An approver identity + rationale are added to the impl unit so
    the full-fidelity assertion has something to prove."""
    revision_id, impl_unit_id = completed_unit(db_client, migrated_engine, key="release-pack")

    second = db_client.post(
        f"/api/v1/revisions/{revision_id}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": "release-pack-unit-2",
            "expected_version": 0,
            "unit_key": "release-pack-unit-2",
            "title": "Second unit",
            "outcome": "second",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
        },
    )
    assert second.status_code == 201

    binding = db_client.post(
        f"/api/v1/work-units/{impl_unit_id}/release-artifacts",
        headers=SYSTEM,
        json=release_body(revision_id, key="release-pack-binding"),
    )
    assert binding.status_code == 201
    binding_id = binding.json()["id"]

    observation = db_client.post(
        f"/api/v1/release-artifacts/{binding_id}/deployment-observations",
        headers=SYSTEM,
        json=observation_body(key="release-pack-observation"),
    )
    assert observation.status_code == 201

    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, uuid.UUID(impl_unit_id))
        assert unit is not None
        evidence = Evidence(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id="ac-1",
            attempt=1,
            evidence_type="test",
            stable_ref="artifact://release-pack",
            source_revision="abc123",
            recorded_by="worker",
            event_id=uuid.uuid4(),
            idempotency_key="release-pack-evidence",
        )
        session.add(evidence)
        session.flush()
        session.add(
            Adjudication(
                work_package_revision_id=unit.work_package_revision_id,
                work_unit_id=unit.id,
                ac_id="ac-1",
                outcome="waived",
                decided_by="alice-approver",
                rationale="secret reasoning xyz",
                failed_evidence_id=evidence.id,
                risk="medium",
                follow_up="monitor",
                scope="ac-1",
                event_id=uuid.uuid4(),
            )
        )
        session.commit()

    return revision_id, impl_unit_id


def test_release_evidence_pack_composes_units_artifacts_and_deployments(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id, impl_unit_id = _release_with_units_artifact_and_deployment(
        db_client, migrated_engine
    )

    response = db_client.get(f"/api/v1/revisions/{revision_id}/evidence-pack", headers=WORKER)

    assert response.status_code == 200
    body = response.json()

    assert body["revision"]["revision"] == 1
    assert body["revision"]["work_package_id"]
    assert body["revision"]["approved_by"] == "devon"

    unit_ids = {u["work_unit"]["id"] for u in body["units"]}
    assert impl_unit_id in unit_ids
    # impl unit + second unit + the auto-minted post-deploy verification unit
    assert len(body["units"]) >= 3
    assert any(
        u["work_unit"]["title"] == "Post-deploy verification for production"
        for u in body["units"]
    )

    assert len(body["release_artifacts"]) == 1
    assert body["release_artifacts"][0]["artifact_digest"] == DIGEST

    assert len(body["deployments"]) == 1
    assert body["deployments"][0]["environment"] == "production"


def test_release_evidence_pack_json_is_full_fidelity_not_redacted(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id, impl_unit_id = _release_with_units_artifact_and_deployment(
        db_client, migrated_engine
    )

    body = db_client.get(
        f"/api/v1/revisions/{revision_id}/evidence-pack", headers=WORKER
    ).json()

    impl = next(u for u in body["units"] if u["work_unit"]["id"] == impl_unit_id)
    waiver = next(a for a in impl["adjudications"] if a["outcome"] == "waived")
    assert waiver["decided_by"] == "alice-approver"
    assert waiver["rationale"] == "secret reasoning xyz"


def test_release_evidence_pack_is_readable_by_worker_credential(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id, _ = _release_with_units_artifact_and_deployment(db_client, migrated_engine)

    response = db_client.get(f"/api/v1/revisions/{revision_id}/evidence-pack", headers=WORKER)

    assert response.status_code == 200


def test_release_evidence_pack_requires_authentication(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id, _ = _release_with_units_artifact_and_deployment(db_client, migrated_engine)

    response = db_client.get(f"/api/v1/revisions/{revision_id}/evidence-pack")

    assert response.status_code == 401


def test_release_evidence_pack_unknown_revision_is_clean_4xx_not_500(
    db_client: TestClient,
) -> None:
    response = db_client.get(f"/api/v1/revisions/{uuid.uuid4()}/evidence-pack", headers=WORKER)

    assert response.status_code != 500
    assert response.status_code in (400, 404, 409)
    assert response.json()["error"]["code"] == "revision_not_found"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/api/test_release_evidence_pack_api.py -q`
Expected: FAIL — the composition test gets `404` (route absent) so `response.status_code == 200` fails; the unknown-revision test may also fail on the error-code assertion. (If it errors during collection on a missing import, that's also a valid red — fix in the implementation steps.)

- [ ] **Step 3: Add the response schemas**

Append to the **end of** `src/orchestrator/api/schemas.py` (all referenced models — `EvidencePackResponse`, `ReleaseArtifactResponse`, `DeploymentObservationResponse` — are already defined earlier in the file; `BaseModel`, `ConfigDict`, `UUID` are already imported):

```python
class ReleaseEvidencePackRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_package_id: UUID
    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    approved_by: str
    registered_by: str


class ReleaseEvidencePackResponse(BaseModel):
    revision: ReleaseEvidencePackRevisionResponse
    units: list[EvidencePackResponse]
    release_artifacts: list[ReleaseArtifactResponse]
    deployments: list[DeploymentObservationResponse]
```

- [ ] **Step 4: Create the assembler service module**

Create `src/orchestrator/services/release_evidence_pack.py`:

```python
"""Per-release evidence-pack assembly (WS-P2.5 Increment 2).

Composes the Increment-1 per-unit evidence pack for every work unit in a package revision
with that revision's release artifact bindings and deployment observations, producing one
read-only, JSON-safe response consumed by both the ``/api`` JSON route and the ``/review``
GUI page. It reads canonical rows only; it never dispatches, deploys, or merges.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.api.schemas import (
    DeploymentObservationResponse,
    ReleaseArtifactResponse,
    ReleaseEvidencePackResponse,
    ReleaseEvidencePackRevisionResponse,
)
from orchestrator.errors import DomainError
from orchestrator.persistence.models import (
    DeploymentObservation,
    ReleaseArtifactBinding,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.evidence_pack import (
    evidence_pack_projection,
    evidence_pack_response,
)


def release_evidence_pack_response(
    session: Session, revision_id: uuid.UUID
) -> ReleaseEvidencePackResponse:
    revision = session.get(WorkPackageRevision, revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    units = tuple(
        session.scalars(
            select(WorkUnit)
            .where(WorkUnit.work_package_revision_id == revision_id)
            .order_by(WorkUnit.unit_key)
        )
    )
    artifacts = tuple(
        session.scalars(
            select(ReleaseArtifactBinding)
            .where(ReleaseArtifactBinding.work_package_revision_id == revision_id)
            .order_by(ReleaseArtifactBinding.recorded_at, ReleaseArtifactBinding.id)
        )
    )
    observations = tuple(
        session.scalars(
            select(DeploymentObservation)
            .where(DeploymentObservation.work_package_revision_id == revision_id)
            .order_by(DeploymentObservation.recorded_at, DeploymentObservation.id)
        )
    )
    return ReleaseEvidencePackResponse(
        revision=ReleaseEvidencePackRevisionResponse.model_validate(revision),
        units=[
            evidence_pack_response(evidence_pack_projection(session, unit.id))
            for unit in units
        ],
        release_artifacts=[ReleaseArtifactResponse.model_validate(row) for row in artifacts],
        deployments=[
            DeploymentObservationResponse.model_validate(row) for row in observations
        ],
    )
```

- [ ] **Step 5: Add the JSON route**

In `src/orchestrator/api/routes.py`:

1. Add `ReleaseEvidencePackResponse` to the `from orchestrator.api.schemas import (` block (line 14+), keeping alphabetical order near the other `Release*` entries.
2. Add a service import near the other `from orchestrator.services.*` imports:
   ```python
   from orchestrator.services.release_evidence_pack import release_evidence_pack_response
   ```
3. Add the route immediately after `evidence_pack_markdown_route` (after ~line 557):

```python
@router.get(
    "/revisions/{revision_id}/evidence-pack",
    response_model=ReleaseEvidencePackResponse,
)
def release_evidence_pack_route(
    revision_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    """WS-P2.5 Increment 2: the per-release evidence pack -- every unit's pack in a package
    revision, plus that revision's release artifact bindings and deployment observations.

    Authentication-only, no role gate, matching the per-unit evidence-pack route.
    """
    return release_evidence_pack_response(session, revision_id)
```

- [ ] **Step 6: Update BOTH scope-guard files**

6a. In `tests/architecture/test_ws32_scope_guards.py`, add to the `WS53_POST_DEPLOY_PATHS` set:

```python
    Path("src/orchestrator/services/release_evidence_pack.py"),
    # WS-P2.5 Inc 2: the per-release evidence pack COMPOSES deployment observations into a
    # read-only projection. It reads canonical rows; it never dispatches, deploys, or merges.
```

6b. In `tests/architecture/test_scope_guards.py`, the `test_production_get_route_inventory_is_explicit`
test asserts the set of `/api/v1` GET paths **exactly**. Add the new route to that set literal
(alphabetical placement puts it just before `/api/v1/release-artifacts/...`):

```python
        "/api/v1/revisions/{revision_id}/evidence-pack",
```

- [ ] **Step 7: Run the API test to verify it passes**

Run: `.venv/bin/python -m pytest tests/api/test_release_evidence_pack_api.py -q`
Expected: PASS (`collected 6 items`, 6 passed). Confirm the collected count is 6.

- [ ] **Step 8: Run the invariant tests the route touches**

Run: `.venv/bin/python -m pytest tests/architecture/test_ws32_scope_guards.py tests/architecture/test_scope_guards.py tests/api/test_lifecycle_api.py -q`
Expected: PASS. (`test_ws32_*` stays green because the new module is allowlisted in `WS53_POST_DEPLOY_PATHS`; `test_production_get_route_inventory_is_explicit` stays green because the new GET path was added to its set literal; `test_every_api_success_response_has_an_explicit_schema` stays green because the JSON route declares `response_model` and is not markdown — no `NON_JSON_SUCCESS_PATHS` change.)

- [ ] **Step 9: Format and commit**

```bash
.venv/bin/ruff format src/orchestrator/services/release_evidence_pack.py src/orchestrator/api/schemas.py src/orchestrator/api/routes.py tests/api/test_release_evidence_pack_api.py tests/architecture/test_ws32_scope_guards.py tests/architecture/test_scope_guards.py
.venv/bin/ruff check src/orchestrator/services/release_evidence_pack.py src/orchestrator/api/routes.py
git add src/orchestrator/services/release_evidence_pack.py src/orchestrator/api/schemas.py src/orchestrator/api/routes.py tests/api/test_release_evidence_pack_api.py tests/architecture/test_ws32_scope_guards.py tests/architecture/test_scope_guards.py
git commit -m "feat(wsp25-inc2): per-release evidence pack JSON route + assembler"
```

---

### Task 2: `/review` GUI page, template, and intake link

**Files:**
- Create: `src/orchestrator/templates/release_evidence_pack.html`
- Modify: `src/orchestrator/web.py` (import; new GUI route after `evidence_pack` at ~line 432)
- Modify: `src/orchestrator/templates/intake.html` (add a discoverability link)
- Test: `tests/web/test_release_evidence_pack.py`

**Interfaces:**
- Consumes: `orchestrator.services.release_evidence_pack.release_evidence_pack_response` (Task 1); `web.py` helpers `_human`, `_render`, `ActorDep`, `SessionDep`; the `intake.html` context variable `revision` (has `.id`).
- Produces: GUI route `GET /review/revisions/{revision_id}/evidence-pack`; template `release_evidence_pack.html` reading `pack.revision`, `pack.units[].work_unit`, `pack.release_artifacts`, `pack.deployments`.

- [ ] **Step 1: Write the failing web test**

Create `tests/web/test_release_evidence_pack.py`:

```python
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.api.test_deployment_observations_api import observation_body
from tests.api.test_lifecycle_api import HUMAN, SYSTEM, WORKER
from tests.api.test_release_artifacts_api import completed_unit, release_body

DIGEST = "sha256:" + "a" * 64


def _release(db_client: TestClient, migrated_engine: Engine) -> tuple[str, str]:
    revision_id, impl_unit_id = completed_unit(db_client, migrated_engine, key="release-web")
    binding = db_client.post(
        f"/api/v1/work-units/{impl_unit_id}/release-artifacts",
        headers=SYSTEM,
        json=release_body(revision_id, key="release-web-binding"),
    )
    assert binding.status_code == 201
    observation = db_client.post(
        f"/api/v1/release-artifacts/{binding.json()['id']}/deployment-observations",
        headers=SYSTEM,
        json=observation_body(key="release-web-observation"),
    )
    assert observation.status_code == 201
    return revision_id, impl_unit_id


def test_release_evidence_pack_page_is_read_only_and_composes(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id, impl_unit_id = _release(db_client, migrated_engine)

    page = db_client.get(f"/review/revisions/{revision_id}/evidence-pack", headers=HUMAN)

    assert page.status_code == 200
    assert "Release Evidence Pack" in page.text
    assert "Release provenance" in page.text
    assert "Work units" in page.text
    assert f"/review/units/{impl_unit_id}/evidence-pack" in page.text
    assert "Release artifacts" in page.text
    assert DIGEST in page.text
    assert "Deployments" in page.text
    assert "production" in page.text
    assert "<form" not in page.text


def test_release_evidence_pack_page_requires_human(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id, _ = _release(db_client, migrated_engine)

    response = db_client.get(f"/review/revisions/{revision_id}/evidence-pack", headers=WORKER)

    assert response.status_code != 200


def test_release_evidence_pack_page_has_no_post_route(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id, _ = _release(db_client, migrated_engine)

    response = db_client.post(f"/review/revisions/{revision_id}/evidence-pack", headers=HUMAN)

    assert response.status_code == 405


def test_release_evidence_pack_page_unknown_revision_is_not_200_or_500(
    db_client: TestClient,
) -> None:
    response = db_client.get(
        f"/review/revisions/{uuid.uuid4()}/evidence-pack", headers=HUMAN
    )

    assert response.status_code not in (200, 500)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_release_evidence_pack.py -q`
Expected: FAIL — the page GET returns `404` (route absent), so `page.status_code == 200` fails.

- [ ] **Step 3: Create the template**

Create `src/orchestrator/templates/release_evidence_pack.html` (mirrors `evidence_pack.html` markup conventions; templates are `.html` and are not scanned by the scope guard, so referencing `dispatch_summary`/`status_summary` is fine):

```html
{% extends "base.html" %}
{% block title %}Release Evidence Pack: revision {{ pack.revision.revision }}{% endblock %}
{% block content %}
<p><a href="/review/intakes/{{ pack.revision.id }}">← Package intake</a></p>
<h1>Release Evidence Pack</h1>
<p class="status">Revision {{ pack.revision.revision }} · {{ pack.revision.content_hash }}</p>

<h2>Release provenance</h2>
<dl>
  <dt>Work package</dt><dd>{{ pack.revision.work_package_id }}</dd>
  <dt>Revision</dt><dd>{{ pack.revision.revision }}</dd>
  <dt>Content hash</dt><dd>{{ pack.revision.content_hash }}</dd>
  <dt>Source path</dt><dd>{{ pack.revision.source_path }}</dd>
  <dt>Source commit</dt><dd>{{ pack.revision.source_commit }}</dd>
  <dt>Approved by</dt><dd>{{ pack.revision.approved_by }}</dd>
  <dt>Registered by</dt><dd>{{ pack.revision.registered_by }}</dd>
</dl>

<h2>Work units</h2>
<table><caption>Every unit in this release</caption>
<thead><tr><th scope="col">Title</th><th scope="col">State</th><th scope="col">Authority fingerprint</th><th scope="col">Evidence pack</th></tr></thead>
<tbody>{% for unit in pack.units %}<tr><td>{{ unit.work_unit.title }}</td><td>{{ unit.work_unit.state }}</td><td><code>{{ unit.work_unit.authority_fingerprint }}</code></td><td><a href="/review/units/{{ unit.work_unit.id }}/evidence-pack">View</a></td></tr>{% else %}<tr><td colspan="4">No units recorded.</td></tr>{% endfor %}</tbody></table>

<h2>Release artifacts</h2>
<table><caption>Immutable build artifacts bound to this release</caption>
<thead><tr><th scope="col">Digest</th><th scope="col">Registry / repository / name</th><th scope="col">PR</th><th scope="col">Source commit</th><th scope="col">Merge commit</th><th scope="col">Workflow</th><th scope="col">Builder</th></tr></thead>
<tbody>{% for row in pack.release_artifacts %}<tr><td>{{ row.artifact_digest }}</td><td>{{ row.artifact_registry }}/{{ row.artifact_repository }}/{{ row.artifact_name }}</td><td>{{ row.implementation_pr_number or "—" }}</td><td>{{ row.source_commit }}</td><td>{{ row.merge_commit }}</td><td>{{ row.workflow_run_url or "—" }}</td><td>{{ row.builder_id or "—" }}</td></tr>{% else %}<tr><td colspan="7">No release artifacts recorded.</td></tr>{% endfor %}</tbody></table>

<h2>Deployments</h2>
<table><caption>Deployment and health observations for this release</caption>
<thead><tr><th scope="col">Environment</th><th scope="col">Observed digest</th><th scope="col">Base URL</th><th scope="col">Deployer</th><th scope="col">Observed at</th><th scope="col">Status</th><th scope="col">Dispatch enabled</th></tr></thead>
<tbody>{% for row in pack.deployments %}<tr><td>{{ row.environment }}</td><td>{{ row.observed_artifact_digest }}</td><td>{{ row.base_url }}</td><td>{{ row.deployer }}</td><td>{{ row.observed_at }}</td><td>{{ row.status_summary.status }}</td><td>{{ row.dispatch_summary.dispatch_enabled }}</td></tr>{% else %}<tr><td colspan="7">No deployments recorded.</td></tr>{% endfor %}</tbody></table>
{% endblock %}
```

- [ ] **Step 4: Add the GUI route**

In `src/orchestrator/web.py`:

1. Add the import near the existing `from orchestrator.services.evidence_pack import evidence_pack_projection` line:
   ```python
   from orchestrator.services.release_evidence_pack import release_evidence_pack_response
   ```
2. Add the route immediately after the `evidence_pack` function (after ~line 432):

```python
@router.get("/revisions/{revision_id}/evidence-pack", response_class=HTMLResponse)
def release_evidence_pack(
    request: Request, revision_id: uuid.UUID, actor: ActorDep, session: SessionDep
) -> HTMLResponse:
    _human(actor)
    return _render(
        request,
        "release_evidence_pack.html",
        {"pack": release_evidence_pack_response(session, revision_id)},
    )
```

- [ ] **Step 5: Add the discoverability link to intake.html**

In `src/orchestrator/templates/intake.html`, immediately after the line
`<p class="status">{{ package.package_id }} · revision {{ revision.revision }}</p>` (line 6), add:

```html
<p><a href="/review/revisions/{{ revision.id }}/evidence-pack">View release evidence pack →</a></p>
```

- [ ] **Step 6: Run the web test to verify it passes**

Run: `.venv/bin/python -m pytest tests/web/test_release_evidence_pack.py -q`
Expected: PASS (`collected 4 items`, 4 passed). Confirm the collected count is 4.

- [ ] **Step 7: Confirm web.py stayed scope-guard clean**

Run: `.venv/bin/python -m pytest tests/architecture/test_ws32_scope_guards.py -q`
Expected: PASS. (`web.py` is not allowlisted; its new route body references only `release_evidence_pack_response` and `_render`, no bare `deploy`/`dispatch` tokens.)

- [ ] **Step 8: Format and commit**

```bash
.venv/bin/ruff format src/orchestrator/web.py tests/web/test_release_evidence_pack.py
.venv/bin/ruff check src/orchestrator/web.py
git add src/orchestrator/web.py src/orchestrator/templates/release_evidence_pack.html src/orchestrator/templates/intake.html tests/web/test_release_evidence_pack.py
git commit -m "feat(wsp25-inc2): per-release evidence pack /review GUI page + intake link"
```

- [ ] **Step 9: Full-repo gate on a clean tree**

Run: `git status` (confirm the working tree is clean — an uncommitted edit makes `make check` a false green).
Run: `make check`
Expected: PASS. **Read the `collected N items` line** in the pytest output — exit 0 alone does not prove tests ran (exit code 5 = "no tests collected" is swallowed). If `make check` reds on pre-existing `ruff format --check .` debt in files this branch never touched, confirm it also fails on `main` (a differential, not this branch's regression) before proceeding.

---

## Self-Review

**Spec coverage:**
- Per-revision delimiter, JSON route, GUI route, no PR comment → Tasks 1 & 2. ✓
- Compose per-unit `EvidencePackResponse` → assembler in Task 1 Step 4. ✓
- Dedicated `ReleaseEvidencePackRevisionResponse` with `work_package_id` + `approved_by` → Task 1 Step 3 (plus `id` for the GUI back-link). ✓
- Full-fidelity JSON (no redaction) → `test_release_evidence_pack_json_is_full_fidelity_not_redacted`. ✓
- Auth-only JSON; forward-auth GUI → `..._is_readable_by_worker_credential` / `..._requires_authentication` / `..._page_requires_human`. ✓
- Scope-guard word-allowlist for the new module (`WS53_POST_DEPLOY_PATHS`) → Task 1 Step 6a. ✓
- Explicit GET-route inventory (`test_production_get_route_inventory_is_explicit`) → Task 1 Step 6b. ✓
- JSON-schema invariant / no `NON_JSON_SUCCESS_PATHS` change → Task 1 Step 8. ✓
- Deterministic ordering → assembler `order_by` clauses. ✓
- No migration → nothing in the plan creates one. ✓
- Discoverability link → Task 2 Step 5. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows complete code. ✓

**Type consistency:** `release_evidence_pack_response(session, revision_id)` name and return type `ReleaseEvidencePackResponse` are identical across the service (Task 1 Step 4), both route imports (Task 1 Step 5, Task 2 Step 4), and the interface blocks. Schema field names (`revision`, `units`, `release_artifacts`, `deployments`; revision sub-fields `id`, `work_package_id`, `revision`, `content_hash`, `source_path`, `source_commit`, `approved_by`, `registered_by`) match the template accessors and the test assertions. ✓
