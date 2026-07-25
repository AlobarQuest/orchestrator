"""GET /work-units/{unit_id}/evidence-pack (WS-P2.5 Increment 1).

The structured JSON twin of the `/review` evidence-pack HTML page: wires
`evidence_pack_projection` (Task 1) through `evidence_pack_response` (Task 2) to a route that is
authentication-ONLY -- no role gate -- because the runner's WORKER credential must be able to read
its own unit's evidentiary record, not just SYSTEM/VERIFIER/HUMAN actors.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.persistence.models import WorkUnit
from tests.api.test_lifecycle_api import HUMAN, WORKER
from tests.api.test_status_ledger_api import _register_ready_unit


def _unit_version(migrated_engine: Engine, unit_id: str) -> tuple[int, str]:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, uuid.UUID(unit_id))
        assert unit is not None
        return unit.version, str(unit.work_package_revision_id)


def _built_and_evidenced_unit(
    db_client: TestClient, migrated_engine: Engine, key: str
) -> tuple[str, dict[str, object]]:
    """A ready unit, claimed, with a current evidence record and a waived adjudication."""
    unit_id = _register_ready_unit(db_client, key)
    claim = db_client.post(
        f"/api/v1/work-units/{unit_id}/claim",
        headers=WORKER,
        json={"idempotency_key": f"{key}-claim", "expected_version": 2},
    )
    assert claim.status_code == 200
    lease = claim.json()

    version, revision_id = _unit_version(migrated_engine, unit_id)
    evidence = db_client.post(
        f"/api/v1/work-units/{unit_id}/evidence",
        headers=WORKER,
        json={
            "idempotency_key": f"{key}-evidence",
            "expected_version": version,
            "work_package_revision_id": revision_id,
            "ac_id": "ac-1",
            "attempt": lease["attempt"],
            "lease_token": lease["lease_token"],
            "evidence_type": "test",
            "stable_ref": "artifact://evidence-pack",
            "payload": {"exit_code": 1},
            "source_revision": "abc123",
        },
    )
    assert evidence.status_code == 200
    evidence_id = evidence.json()["id"]

    version, _ = _unit_version(migrated_engine, unit_id)
    expires_at = (datetime.now(UTC) + timedelta(days=365)).isoformat()
    adjudication = db_client.post(
        f"/api/v1/work-units/{unit_id}/adjudications",
        headers=HUMAN,
        json={
            "idempotency_key": f"{key}-waiver",
            "expected_version": version,
            "work_package_revision_id": revision_id,
            "ac_id": "ac-1",
            "outcome": "waived",
            "rationale": "accepted for release",
            "failed_evidence_id": evidence_id,
            "risk": "medium",
            "follow_up": "monitor in prod",
            "scope": "ac-1",
            "expires_at": expires_at,
        },
    )
    assert adjudication.status_code == 200

    return unit_id, lease


def test_evidence_pack_route_returns_the_structured_pack(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id, _lease = _built_and_evidenced_unit(db_client, migrated_engine, "evidence-pack-api")

    response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack", headers=WORKER)

    assert response.status_code == 200
    body = response.json()

    assert body["work_unit"]["id"] == unit_id
    assert body["work_unit"]["authority_fingerprint"]
    assert body["provenance"]["content_hash"]
    assert body["provenance"]["revision"] == 1

    evidence_entry = next(row for row in body["evidence"] if row["ac_id"] == "ac-1")
    assert evidence_entry["current"] is True
    assert evidence_entry["evidence_type"] == "test"
    assert evidence_entry["stable_ref"] == "artifact://evidence-pack"
    assert evidence_entry["supersedes"] is None

    waiver = next(row for row in body["adjudications"] if row["outcome"] == "waived")
    assert waiver["current"] is True
    assert waiver["decided_by"] == "devon"
    assert waiver["risk"] == "medium"
    assert waiver["follow_up"] == "monitor in prod"
    assert waiver["scope"] == "ac-1"
    assert waiver["failed_evidence_id"] == evidence_entry["id"]
    assert waiver["expires_at"] is not None

    assert any(
        a["subject_type"] == "authority" and a["decision"] == "approved" for a in body["approvals"]
    )
    assert body["events"], "the lifecycle transitions must be visible in the pack"
    assert body["event_publications"] == []


def test_evidence_pack_route_is_readable_by_the_worker_credential(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """Auth-only, no role gate: the constraint this whole task exists to enforce."""
    unit_id, _lease = _built_and_evidenced_unit(
        db_client, migrated_engine, "evidence-pack-api-worker"
    )

    response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack", headers=WORKER)

    assert response.status_code == 200


def test_evidence_pack_route_unknown_unit_is_clean_4xx_not_500(db_client: TestClient) -> None:
    response = db_client.get(f"/api/v1/work-units/{uuid.uuid4()}/evidence-pack", headers=WORKER)

    assert response.status_code != 500
    assert response.status_code in (400, 404, 409)
    assert response.json()["error"]["code"] == "work_unit_not_found"


def test_evidence_pack_markdown_route_returns_rendered_markdown(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id, _lease = _built_and_evidenced_unit(db_client, migrated_engine, "evidence-pack-md")

    json_response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack", headers=WORKER)
    markdown_response = db_client.get(
        f"/api/v1/work-units/{unit_id}/evidence-pack/markdown", headers=WORKER
    )

    assert markdown_response.status_code == 200
    assert markdown_response.headers["content-type"].startswith("text/markdown")

    body = markdown_response.text
    json_body = json_response.json()
    assert json_body["work_unit"]["authority_fingerprint"] in body
    assert "ac-1" in body
    assert "waived" in body
    for header in (
        "## Canonical provenance",
        "## Authority",
        "## Dependencies and claims",
        "## AC-keyed evidence",
        "## Adjudications and waiver facts",
        "## Approvals",
        "## Event publications",
        "## Event history",
    ):
        assert header in body


def test_evidence_pack_json_and_markdown_derive_from_the_same_projection(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """Both routes must reflect the SAME underlying data -- one structured source, two views."""
    unit_id, _lease = _built_and_evidenced_unit(
        db_client, migrated_engine, "evidence-pack-md-parity"
    )

    json_response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack", headers=WORKER)
    markdown_response = db_client.get(
        f"/api/v1/work-units/{unit_id}/evidence-pack/markdown", headers=WORKER
    )

    evidence_entry = next(row for row in json_response.json()["evidence"] if row["ac_id"] == "ac-1")
    assert evidence_entry["stable_ref"] in markdown_response.text


def test_evidence_pack_markdown_route_is_readable_by_the_worker_credential(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id, _lease = _built_and_evidenced_unit(
        db_client, migrated_engine, "evidence-pack-md-worker"
    )

    response = db_client.get(f"/api/v1/work-units/{unit_id}/evidence-pack/markdown", headers=WORKER)

    assert response.status_code == 200


def test_evidence_pack_markdown_route_unknown_unit_is_clean_4xx_not_500(
    db_client: TestClient,
) -> None:
    response = db_client.get(
        f"/api/v1/work-units/{uuid.uuid4()}/evidence-pack/markdown", headers=WORKER
    )

    assert response.status_code != 500
    assert response.status_code in (400, 404, 409)
    assert response.json()["error"]["code"] == "work_unit_not_found"
