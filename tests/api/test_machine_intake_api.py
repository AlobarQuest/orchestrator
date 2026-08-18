"""The four ADR-0027 guard proofs, at the wire. The spec's claim is about HTTP.

"A named `DomainError`, not a 500" is a statement about what a caller receives, and only
`DomainError` and `APIAuthenticationError` have registered handlers -- so a refusal raised as
anything else reaches the caller as a bare 500 that no client can classify. The code is read
from where the handler actually puts it: NESTED under `error`, never top-level.

**THE STATUS IS ASSERTED, NOT MERELY "NOT 500".** The first draft of this file checked only that
each refusal was not a 500, and that looseness let a real regression through: splitting intake's
role refusal out of `human_actor_required` moved it out of `main.py`'s 403 set, so a worker
credential silently began receiving 409 -- which that handler's own comment says tells a caller
to change its request rather than that it may not make one. A test that cannot see a status
change cannot protect a status contract. The two codes are deliberately different statuses:
`intake_registrar_invalid` is 403 because that actor may not register at all, and
`intake_change_record_required` is 409 because that actor may, and the request is what is wrong.
"""

import uuid

from fastapi.testclient import TestClient

from tests.api.test_package_intake_api import HUMAN, intake_payload

SYSTEM = {"Authorization": "Bearer system-token", "X-Credential-Key-Id": "system-key"}
WORKER = {"Authorization": "Bearer fixture-token", "X-Credential-Key-Id": "worker-key"}
VERIFIER = {"Authorization": "Bearer verifier-token", "X-Credential-Key-Id": "verifier-key"}
OBSERVER = {"Authorization": "Bearer observer-token", "X-Credential-Key-Id": "observer-key"}

CHANGE_RECORD = 8801


def _code(response) -> str:
    body = response.json()
    error = body.get("error") if isinstance(body, dict) else None
    return error.get("code", "") if isinstance(error, dict) else ""


def test_the_system_actor_registers_an_intake_naming_its_change_record(
    db_client: TestClient,
) -> None:
    response = db_client.post(
        "/api/v1/package-intakes",
        headers=SYSTEM,
        json=intake_payload(change_record_id=CHANGE_RECORD),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["change_record_id"] == CHANGE_RECORD


def test_a_machine_intake_without_a_change_record_is_a_named_refusal(
    db_client: TestClient,
) -> None:
    response = db_client.post("/api/v1/package-intakes", headers=SYSTEM, json=intake_payload())

    assert response.status_code == 409, response.text
    assert _code(response) == "intake_change_record_required"


def test_a_human_intake_without_a_change_record_still_succeeds(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/package-intakes", headers=HUMAN, json=intake_payload())

    assert response.status_code == 201, response.text
    assert response.json()["change_record_id"] is None


def test_a_worker_credential_may_not_register_an_intake(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/package-intakes",
        headers=WORKER,
        json=intake_payload(change_record_id=CHANGE_RECORD),
    )

    assert response.status_code == 403, response.text
    assert _code(response) == "intake_registrar_invalid"


def test_a_verifier_credential_may_not_register_an_intake(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/package-intakes",
        headers=VERIFIER,
        json=intake_payload(change_record_id=CHANGE_RECORD),
    )

    assert response.status_code == 403, response.text
    assert _code(response) == "intake_registrar_invalid"


def test_an_observer_credential_is_refused_before_the_service(db_client: TestClient) -> None:
    """Two independent refusals, and this one fires first. `_confine_observer` is keyed on the
    route template, so an observer never reaches `register_package_intake` at all -- which is
    why the code here is the confinement's, not the registrar guard's."""
    response = db_client.post(
        "/api/v1/package-intakes",
        headers=OBSERVER,
        json=intake_payload(change_record_id=CHANGE_RECORD),
    )

    assert response.status_code == 403, response.text
    assert _code(response) == "role_forbidden"


def test_the_bootstrap_revision_lane_still_refuses_the_system_actor(
    db_client: TestClient,
) -> None:
    """`POST /api/v1/revisions` is unchanged: human-only on a machine-only router, so no
    principal reaches it. ADR-0027 admitted a machine to intake, not to the WS-3.1 lane."""
    response = db_client.post(
        "/api/v1/revisions",
        headers=SYSTEM,
        json={
            "package_id": "pkg-ws32",
            "source_repository": "owner/repo",
            "revision": 9,
            "content_hash": "sha256:manual",
            "source_path": "/tmp/pkg-ws32",
            "source_commit": "abc123",
            "approved_by": "human-1",
            "approved_at": "2026-07-05T00:00:00+00:00",
            "approval_event_id": str(uuid.UUID(int=2)),
            "enforcement_snapshot": {"title": "Manual revision"},
            "authority": {
                "capabilities": {"repo.edit": "allowed"},
                "budgets": {"max_attempts": 3, "max_llm_calls": 4},
            },
            "registry_version": 1,
            "idempotency_key": "machine-bootstrap-revision",
            "expected_version": 0,
        },
    )

    assert response.status_code == 403, response.text
    assert _code(response) == "human_actor_required"


def test_a_machine_intake_replays_rather_than_registering_twice(db_client: TestClient) -> None:
    """What makes scheduling the carrier safe: the payload's idempotency key is derived from
    the record, so a second pass over an unchanged queue is a replay, not a second intake."""
    payload = intake_payload(change_record_id=CHANGE_RECORD)
    first = db_client.post("/api/v1/package-intakes", headers=SYSTEM, json=payload)
    second = db_client.post("/api/v1/package-intakes", headers=SYSTEM, json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]
