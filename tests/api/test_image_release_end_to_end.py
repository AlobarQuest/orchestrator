"""`image_release` end to end: its real clients, through the real FastAPI app, on the test database.

Completed units with confirmed landings are seeded the way the machine-activation suite seeds them;
the pass then binds them, observes the deployment and verifies the post-deploy units that
observation mints -- each request sent by this program's own confined clients and answered by the
orchestrator's own routes, auth chain and services. Only production's identity is faked:
`/health/live` answers with a chosen revision, because the test app has no
`ORCHESTRATOR_REVISION`, and `/health/ready` can be made to fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from image_release.client import AnonymousClient, SystemClient, VerifierClient
from image_release.release import PROBE_ATTEMPTS, release_pass
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import DeploymentObservation, ReleaseArtifactBinding, WorkUnit
from tests.activation_sweep.conftest import git
from tests.image_release.conftest import DIGEST, release_for
from tests.services.test_machine_activation import landed_unit, record_landing

REPOSITORY = "AlobarQuest/orchestrator"
BASE_URL = "https://sds.example.invalid"


@dataclass
class Estate:
    checkout: Path
    shipped: str  # carried by the image production served before the swap
    previous: str
    first: str  # two landings this deploy ships
    second: str
    built: str


def _commit(path: Path, name: str) -> str:
    (path / name).write_text(name)
    git(path, "add", name)
    git(path, "commit", "-q", "-m", name)
    return git(path, "rev-parse", "HEAD").strip()


@pytest.fixture
def estate(tmp_path: Path, migrated_engine: Engine) -> Estate:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-q", "-b", "main")
    commits = {name: _commit(checkout, name) for name in ("shipped", "previous", "first", "second")}
    built = _commit(checkout, "built")
    with Session(migrated_engine) as session:
        for number, name in enumerate(("shipped", "first", "second"), start=1):
            head = f"{number:040x}"
            landed_unit(
                session,
                key=f"orchestrator-{name}",
                repository=REPOSITORY,
                head_sha=head,
                pr_number=number,
            )
            record_landing(
                session,
                repository=REPOSITORY,
                pull_request=number,
                head_commit=head,
                commit=commits[name],
            )
    return Estate(checkout=checkout, built=built, **commits)


def _through_the_app(
    db_client: TestClient, *, served: str, ready_status: int | None = None
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/live":
            return httpx.Response(200, json={"status": "ok", "revision": served})
        if request.url.path == "/health/ready" and ready_status is not None:
            return httpx.Response(ready_status, json={"status": "unavailable"})
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() in {"authorization", "x-credential-key-id", "content-type"}
        }
        answer = db_client.request(
            request.method,
            request.url.path,
            params=request.url.params,
            content=request.content,
            headers=headers,
        )
        return httpx.Response(answer.status_code, content=answer.content)

    return httpx.MockTransport(handle)


def _pass(
    db_client: TestClient,
    estate: Estate,
    *,
    served: str | None = None,
    ready_status: int | None = None,
    sleeps: list[float] | None = None,
) -> dict[str, Any]:
    transport = _through_the_app(
        db_client, served=served or estate.built, ready_status=ready_status
    )
    return release_pass(
        release_for(estate.built, previous=estate.previous),
        checkout=estate.checkout,
        system=SystemClient(
            base_url=BASE_URL,
            credential_key_id="system-key",
            token="system-token",
            transport=transport,
        ),
        verifier=VerifierClient(
            base_url=BASE_URL,
            credential_key_id="verifier-key",
            token="verifier-token",
            transport=transport,
        ),
        anonymous=AnonymousClient(base_url=BASE_URL, transport=transport),
        dry_run=False,
        sleep=(sleeps if sleeps is not None else []).append,
    )


def _by_key(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {unit["unit_key"]: unit for unit in summary["units"]}


def _count(engine: Engine, model: Any) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_a_deploy_binds_observes_and_verifies_only_the_units_it_shipped(
    db_client: TestClient, migrated_engine: Engine, estate: Estate
) -> None:
    summary = _pass(db_client, estate)

    units = _by_key(summary)
    assert units["orchestrator-shipped"]["binding"]["outcome"] == "shipped_earlier", summary
    for key in ("orchestrator-first", "orchestrator-second"):
        assert units[key]["binding"]["outcome"] == "bound", summary
        assert units[key]["observation"]["outcome"] == "observed", summary
        assert units[key]["verification"]["outcome"] == "completed", summary
    assert summary["production"] == {
        "attempts": 1,
        "served_revision": estate.built,
        "revision_matches": True,
        "status": "healthy",
        "summary": summary["production"]["summary"],
    }

    with Session(migrated_engine) as session:
        bindings = session.scalars(select(ReleaseArtifactBinding)).all()
        assert sorted(b.merge_commit for b in bindings) == sorted([estate.first, estate.second])
        for binding in bindings:
            assert binding.kind == "container_image"
            assert binding.artifact_digest == DIGEST
            assert binding.artifact_tag == f"sha-{estate.built}"
            [observation] = session.scalars(
                select(DeploymentObservation).where(
                    DeploymentObservation.release_artifact_binding_id == binding.id
                )
            ).all()
            assert observation.observed_artifact_digest == DIGEST
            post_deploy = session.get(WorkUnit, observation.post_deploy_work_unit_id)
            assert post_deploy is not None
            assert post_deploy.state == WorkUnitState.COMPLETED.value

    # A second pass over the same deploy finds everything done and re-files nothing.
    again = _by_key(_pass(db_client, estate))
    for key in ("orchestrator-first", "orchestrator-second"):
        assert again[key]["binding"]["outcome"] == "already_bound", again
        assert again[key]["observation"]["outcome"] == "already_observed", again
        assert again[key]["verification"]["outcome"] == "completed", again
    assert _count(migrated_engine, ReleaseArtifactBinding) == 2
    assert _count(migrated_engine, DeploymentObservation) == 2


def test_a_revision_mismatch_writes_nothing(
    db_client: TestClient, migrated_engine: Engine, estate: Estate
) -> None:
    sleeps: list[float] = []

    summary = _pass(db_client, estate, served=estate.previous, sleeps=sleeps)

    assert summary["refusal"]["outcome"] == "revision_mismatch", summary
    assert summary["production"]["attempts"] == PROBE_ATTEMPTS
    assert len(sleeps) == PROBE_ATTEMPTS - 1
    assert _count(migrated_engine, ReleaseArtifactBinding) == 0
    assert _count(migrated_engine, DeploymentObservation) == 0


def test_a_degraded_production_after_every_retry_is_filed_and_needs_revision(
    db_client: TestClient, migrated_engine: Engine, estate: Estate
) -> None:
    summary = _pass(db_client, estate, ready_status=503)

    assert summary["production"]["attempts"] == PROBE_ATTEMPTS
    assert summary["production"]["status"] == "degraded"
    units = _by_key(summary)
    for key in ("orchestrator-first", "orchestrator-second"):
        assert units[key]["observation"]["outcome"] == "observed", summary
        assert units[key]["verification"]["outcome"] == "revision_required", summary
    with Session(migrated_engine) as session:
        for observation in session.scalars(select(DeploymentObservation)).all():
            post_deploy = session.get(WorkUnit, observation.post_deploy_work_unit_id)
            assert post_deploy is not None
            assert post_deploy.state == WorkUnitState.REVISION_REQUIRED.value
