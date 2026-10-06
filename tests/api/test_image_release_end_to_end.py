"""`image_release` end to end: its real clients, through the real FastAPI app, on the test database.

A completed unit with a confirmed landing is seeded the way the machine-activation suite seeds one;
the pass then binds it, observes the deployment and verifies the post-deploy unit that observation
mints -- each request sent by this program's own confined clients and answered by the orchestrator's
own routes, auth chain and services. Only production's identity is faked: `/health/live` answers
with the built commit, because the test app has no `ORCHESTRATOR_REVISION`.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from image_release.client import AnonymousClient, SystemClient, VerifierClient
from image_release.release import release_pass
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import DeploymentObservation, ReleaseArtifactBinding, WorkUnit
from tests.activation_sweep.conftest import git
from tests.image_release.conftest import DIGEST, release_for
from tests.services.test_machine_activation import landed_unit, record_landing

REPOSITORY = "AlobarQuest/orchestrator"
BASE_URL = "https://sds.example.invalid"


def _commit(path: Path, name: str) -> str:
    (path / name).write_text(name)
    git(path, "add", name)
    git(path, "commit", "-q", "-m", name)
    return git(path, "rev-parse", "HEAD").strip()


def _through_the_app(db_client: TestClient, built: str) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/live":
            return httpx.Response(200, json={"status": "ok", "revision": built})
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


def test_a_deploy_binds_observes_and_verifies_the_unit_its_image_carries(
    db_client: TestClient, migrated_engine: Engine, tmp_path: Path
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-q", "-b", "main")
    merge = _commit(checkout, "merge")
    built = _commit(checkout, "built")

    with Session(migrated_engine) as session:
        unit = landed_unit(session, key="orchestrator-ac-001", repository=REPOSITORY)
        record_landing(session, repository=REPOSITORY, commit=merge)
        unit_id = unit.id

    transport = _through_the_app(db_client, built)
    system = SystemClient(
        base_url=BASE_URL, credential_key_id="system-key", token="system-token", transport=transport
    )
    verifier = VerifierClient(
        base_url=BASE_URL,
        credential_key_id="verifier-key",
        token="verifier-token",
        transport=transport,
    )
    anonymous = AnonymousClient(base_url=BASE_URL, transport=transport)

    summary = release_pass(
        release_for(built),
        checkout=checkout,
        system=system,
        verifier=verifier,
        anonymous=anonymous,
        dry_run=False,
    )

    [answer] = summary["units"]
    assert answer["binding"]["outcome"] == "bound", summary
    assert answer["observation"]["outcome"] == "observed", summary
    assert answer["verification"]["outcome"] == "completed", summary
    assert summary["production"]["status"] == "healthy", summary

    with Session(migrated_engine) as session:
        [binding] = session.scalars(
            select(ReleaseArtifactBinding).where(ReleaseArtifactBinding.work_unit_id == unit_id)
        ).all()
        assert binding.kind == "container_image"
        assert binding.artifact_digest == DIGEST
        assert binding.artifact_tag == f"sha-{built}"
        [observation] = session.scalars(
            select(DeploymentObservation).where(
                DeploymentObservation.release_artifact_binding_id == binding.id
            )
        ).all()
        assert observation.kind == "container_image"
        assert observation.observed_artifact_digest == DIGEST
        post_deploy = session.get(WorkUnit, observation.post_deploy_work_unit_id)
        assert post_deploy is not None
        assert post_deploy.state == WorkUnitState.COMPLETED.value

    # A second pass over the same deploy finds everything done and re-files nothing.
    again = release_pass(
        release_for(built),
        checkout=checkout,
        system=system,
        verifier=verifier,
        anonymous=anonymous,
        dry_run=False,
    )
    [answer] = again["units"]
    assert answer["binding"]["outcome"] == "already_bound", again
    assert answer["observation"]["outcome"] == "already_observed", again
    assert answer["verification"]["outcome"] == "completed", again
