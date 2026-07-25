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
        u["work_unit"]["title"] == "Post-deploy verification for production" for u in body["units"]
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

    body = db_client.get(f"/api/v1/revisions/{revision_id}/evidence-pack", headers=WORKER).json()

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
