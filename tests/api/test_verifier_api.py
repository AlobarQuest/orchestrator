import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.api.routes import get_check_observer
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import PackageAcceptanceCriterion, WorkUnit
from tests.api.test_lifecycle_api import AUTHORITY, HUMAN, VERIFIER, WORKER
from tests.fixtures.named_check import (
    AUTOMATED_CHECK_AUTHORITY,
    CHECK_NAME,
    HEAD_SHA,
    PR_NUMBER,
    PR_URL,
    TARGET_REPOSITORY,
    StubCheckObserver,
    bind_dispatched_pull_request,
    mapped_submitted_unit,
    record_worker_evidence,
)


def _named_check_api_subject(engine: Engine, key: str) -> dict[str, object]:
    with Session(engine) as session:
        unit = mapped_submitted_unit(
            session,
            key=key,
            evidence_type="automated_check",
            ac_id="AC-006",
            authority=AUTOMATED_CHECK_AUTHORITY,
        )
        worker_evidence = record_worker_evidence(
            session,
            unit,
            ac_id="AC-006",
            evidence_type="runner.pr.opened",
            payload={"pr_number": PR_NUMBER, "head_sha": HEAD_SHA},
            idempotency_key=f"{key}-worker-evidence",
        )
        dispatch = bind_dispatched_pull_request(session, unit)
        return {
            "unit_id": str(unit.id),
            "revision_id": str(unit.work_package_revision_id),
            "version": unit.version,
            "attempt": unit.attempt_count,
            "worker_evidence_id": str(worker_evidence.id),
            "dispatch_id": str(dispatch.id),
        }


def _named_check_api_command(subject: dict[str, object], key: str) -> dict[str, object]:
    return {
        "idempotency_key": key,
        "expected_version": subject["version"],
        "work_package_revision_id": subject["revision_id"],
        "ac_id": "AC-006",
        "dispatch_id": subject["dispatch_id"],
        "repository": TARGET_REPOSITORY,
        "pr_number": PR_NUMBER,
        "pr_url": PR_URL,
        "head_sha": HEAD_SHA,
        "check_name": CHECK_NAME,
        "expected_conclusion": "success",
    }


def substitute_github(client: TestClient, observer: StubCheckObserver) -> None:
    """Point the route at a GitHub the test controls.

    Safe without teardown: `db_client` builds a fresh app per test.
    """
    client.app.dependency_overrides[get_check_observer] = lambda: observer  # type: ignore[union-attr]


def test_verify_api_declares_route(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "/api/v1/work-units/{unit_id}/verify" in document["paths"]
    assert "VerifyCommandModel" in document["components"]["schemas"]
    assert "VerifyResponse" in document["components"]["schemas"]


def test_openapi_declares_verifier_named_check_evidence_route(client: TestClient) -> None:
    document = client.get("/openapi.json").json()

    assert "/api/v1/work-units/{unit_id}/verifier-evidence/named-check" in document["paths"]
    schema = document["components"]["schemas"]["VerifierNamedCheckEvidenceCommandModel"]

    assert "expected_conclusion" in schema["properties"]
    # WS-P2.20. The wire contract is where a caller would reach for these, so this is where
    # their absence has to be pinned: the orchestrator reads them from GitHub instead.
    assert not {"conclusion", "run_id", "run_url", "assertions"} & set(schema["properties"])
    assert "NamedCheckAssertionModel" not in document["components"]["schemas"]


def test_worker_cannot_record_verifier_named_check_evidence(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    subject = _named_check_api_subject(migrated_engine, "named-check-api-worker")

    response = db_client.post(
        f"/api/v1/work-units/{subject['unit_id']}/verifier-evidence/named-check",
        headers=WORKER,
        json=_named_check_api_command(subject, "named-check-api-worker-command"),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_verifier_named_check_api_supersedes_and_replays(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    subject = _named_check_api_subject(migrated_engine, "named-check-api-success")
    command = _named_check_api_command(subject, "named-check-api-success-command")
    path = f"/api/v1/work-units/{subject['unit_id']}/verifier-evidence/named-check"
    substitute_github(db_client, StubCheckObserver())

    first = db_client.post(path, headers=VERIFIER, json=command)
    replay = db_client.post(path, headers=VERIFIER, json=command)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["evidence_type"] == "verifier.github.named_check"
    assert first.json()["attempt"] == subject["attempt"]
    assert first.json()["supersedes_evidence_id"] == subject["worker_evidence_id"]


def test_verifier_named_check_api_rejects_extra_top_level_field(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    subject = _named_check_api_subject(migrated_engine, "named-check-api-extra-field")
    path = f"/api/v1/work-units/{subject['unit_id']}/verifier-evidence/named-check"
    command = _named_check_api_command(subject, "named-check-api-extra-field-command")
    command["unexpected"] = "value"

    response = db_client.post(path, headers=VERIFIER, json=command)

    assert response.status_code == 422


def test_verifier_named_check_api_rejects_a_caller_supplied_conclusion(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    """The old field name is not merely ignored — sending it is refused.

    `extra="forbid"` makes this a 422 rather than a silently dropped key, so a caller that has
    not been updated learns so immediately instead of believing it reported a result.
    """
    subject = _named_check_api_subject(migrated_engine, "named-check-api-caller-conclusion")
    path = f"/api/v1/work-units/{subject['unit_id']}/verifier-evidence/named-check"
    command = _named_check_api_command(subject, "named-check-api-caller-conclusion-command")
    command["conclusion"] = "success"

    response = db_client.post(path, headers=VERIFIER, json=command)

    assert response.status_code == 422


def test_verifier_named_check_api_fails_closed_with_no_app_credentials(
    db_client: TestClient,
    migrated_engine: Engine,
) -> None:
    """No stubbing: the route builds its real observer, which has no App to mint a token from.

    This is the whole-stack shape of §3.2 — an orchestrator that cannot ask GitHub records
    nothing and says so, rather than falling back to what the caller claimed.
    """
    subject = _named_check_api_subject(migrated_engine, "named-check-api-no-credentials")
    path = f"/api/v1/work-units/{subject['unit_id']}/verifier-evidence/named-check"
    command = _named_check_api_command(subject, "named-check-api-no-credentials-command")

    response = db_client.post(path, headers=VERIFIER, json=command)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "named_check_observation_unavailable"


def test_worker_cannot_call_verify_api(db_client: TestClient) -> None:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": "verify-api-revision",
            "expected_version": 0,
            "package_id": "verify-api",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": "sha256:verify-api",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
            "approval_event_id": str(uuid.uuid4()),
            "enforcement_snapshot": {"acceptance_criteria": ["ac-1"]},
            "authority": AUTHORITY,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201
    unit = db_client.post(
        f"/api/v1/revisions/{revision.json()['id']}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": "verify-api-unit",
            "expected_version": 0,
            "unit_key": "verify-api-unit",
            "title": "Verify API unit",
            "outcome": "verified",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201

    response = db_client.post(
        f"/api/v1/work-units/{unit.json()['id']}/verify",
        headers=WORKER,
        json={"idempotency_key": "verify-api-worker", "expected_version": 1},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_verifier_can_call_verify_api(db_client: TestClient, migrated_engine: Engine) -> None:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": "verify-api-judgment-revision",
            "expected_version": 0,
            "package_id": "verify-api-judgment",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": "sha256:verify-api-judgment",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
            "approval_event_id": str(uuid.uuid4()),
            "enforcement_snapshot": {"acceptance_criteria": ["ac-1"]},
            "authority": AUTHORITY,
            "registry_version": 1,
        },
    )
    assert revision.status_code == 201
    unit = db_client.post(
        f"/api/v1/revisions/{revision.json()['id']}/work-units",
        headers=HUMAN,
        json={
            "idempotency_key": "verify-api-judgment-unit",
            "expected_version": 0,
            "unit_key": "verify-api-judgment-unit",
            "title": "Verify API judgment unit",
            "outcome": "verified",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 8, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    revision_id = revision.json()["id"]
    with Session(migrated_engine) as session:
        stored_unit = session.get(WorkUnit, unit_id)
        assert stored_unit is not None
        stored_unit.state = WorkUnitState.SUBMITTED
        session.add(
            PackageAcceptanceCriterion(
                work_package_revision_id=revision_id,
                ac_id="ac-1",
                condition="manual review complete",
                evidence_type="human.review",
                evidence="review evidence",
                approver="verifier",
            )
        )
        session.commit()

    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/verify",
        headers=VERIFIER,
        json={"idempotency_key": "verify-api-judgment", "expected_version": 1},
    )

    assert response.status_code == 200
    assert response.json()["unit_id"] == unit_id
    assert response.json()["result"] == "awaiting_review"
    assert response.json()["state"] == "awaiting_review"
