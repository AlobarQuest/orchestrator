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

    # _human(actor) raises DomainError("human_actor_required") -> 403; assert the exact status so
    # a future regression to 500/redirect (still != 200) can't pass silently.
    assert response.status_code == 403


def test_release_evidence_pack_page_has_no_post_route(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision_id, _ = _release(db_client, migrated_engine)

    response = db_client.post(f"/review/revisions/{revision_id}/evidence-pack", headers=HUMAN)

    assert response.status_code == 405


def test_release_evidence_pack_page_unknown_revision_is_not_200_or_500(
    db_client: TestClient,
) -> None:
    response = db_client.get(f"/review/revisions/{uuid.uuid4()}/evidence-pack", headers=HUMAN)

    assert response.status_code not in (200, 500)
