"""GET /work-units/{unit_id}/pr-merge-admission (ADR-0020, Increment 4a).

The served surface of the composed, report-only answer. Three things are worth testing here that
the service tests cannot see.

**A `response_model` silently DROPS every key the service returns but the model does not
declare**, so "the service returns it" is never evidence "a reader receives it". The shape pin
below needs no HTTP client and cannot drift.

**The estate source is built by a dependency, and an unset URL must arrive as the empty string
the source itself refuses on** -- never as a source that silently answers. `db_client` configures
no App Brain, which is exactly that case, so this file asserts the refusal rather than mocking it
away. No network is reached: the source refuses before constructing a request.

**A unit that does not exist must be a named `DomainError`.** Only `DomainError` and
`APIAuthenticationError` have registered handlers, so anything else a route raises is a bare 500.
"""

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from orchestrator.api.schemas import PrMergeAdmissionResponse
from orchestrator.services.pr_merge_admission import MergeAdmission
from tests.api.test_lifecycle_api import AUTHORITY as BASE_AUTHORITY
from tests.api.test_lifecycle_api import HUMAN, SYSTEM, WORKER

TARGET_REPOSITORY = "AlobarQuest/intent-packages"
# The unit must name the repository it would land in, or the estate is never asked about anything
# -- which is a different refusal and would hide the term this file is here to exercise.
AUTHORITY = {**BASE_AUTHORITY, "constraints": {"target_repository": TARGET_REPOSITORY}}


def _register_ready_unit(db_client: TestClient, suffix: str) -> str:
    revision = db_client.post(
        "/api/v1/revisions",
        headers=HUMAN,
        json={
            "idempotency_key": f"{suffix}-revision",
            "expected_version": 0,
            "package_id": f"{suffix}-package",
            "source_repository": "owner/repo",
            "revision": 1,
            "content_hash": f"sha256:{suffix}",
            "source_path": "intent.md",
            "source_commit": "abc123",
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
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
            "idempotency_key": f"{suffix}-unit",
            "expected_version": 0,
            "unit_key": f"{suffix}-unit",
            "title": f"{suffix} unit",
            "outcome": "the answer is inspectable",
            "required_capability": "repo.edit",
            "authority": AUTHORITY,
            "max_attempts": 3,
            "approved_by": "devon",
            "approved_at": datetime(2026, 7, 5, tzinfo=UTC).isoformat(),
        },
    )
    assert unit.status_code == 201
    unit_id = unit.json()["id"]
    approved = db_client.post(
        f"/api/v1/work-units/{unit_id}/approvals",
        headers=HUMAN,
        json={
            "idempotency_key": f"{suffix}-authority",
            "expected_version": 1,
            "subject_type": "authority",
            "reason": "approved",
        },
    )
    assert approved.status_code == 200
    ready = db_client.post(
        f"/api/v1/work-units/{unit_id}/commands/ready",
        headers=SYSTEM,
        json={"idempotency_key": f"{suffix}-ready", "expected_version": 1},
    )
    assert ready.status_code == 200
    return unit_id


def _admission(db_client: TestClient, unit_id: str) -> dict[str, Any]:
    response = db_client.get(f"/api/v1/work-units/{unit_id}/pr-merge-admission", headers=WORKER)
    assert response.status_code == 200
    return response.json()


def test_the_response_model_declares_every_field_the_service_answers_with() -> None:
    served = {field.name for field in dataclasses.fields(MergeAdmission)}

    assert set(PrMergeAdmissionResponse.model_fields) == served


def test_the_route_serves_the_composed_answer(db_client: TestClient) -> None:
    unit_id = _register_ready_unit(db_client, "merge-admission")

    body = _admission(db_client, unit_id)

    assert set(body) == {
        "satisfied",
        "refusals",
        "target_repository",
        "pr_number",
        "verified_head_sha",
        "change_window_override_applied",
    }
    # The read surface takes no override, so this is false for every answer it can produce.
    assert body["change_window_override_applied"] is False
    assert body["satisfied"] is False
    assert body["target_repository"] == TARGET_REPOSITORY


def test_a_unit_that_never_finished_is_refused_on_named_terms(db_client: TestClient) -> None:
    """The shape every unit in production has today: no envelope grants landing, no criterion was
    decided by the verifier from its own evaluation, and there is no pull request recorded.

    The human authority approval IS present here -- this unit was made ready through the same
    approval every dispatchable unit carries -- so that term is deliberately absent from the list,
    which is what shows the others are not being reported by accident.
    """
    unit_id = _register_ready_unit(db_client, "merge-admission-terms")

    body = _admission(db_client, unit_id)

    assert {
        "work_unit_not_completed",
        "criteria_not_verifier_decided",
        "merge_capability_not_authorized",
        "pr_binding_missing",
    } <= set(body["refusals"])
    assert "authority_approval_not_bound" not in body["refusals"]


def test_an_unconfigured_estate_source_refuses_rather_than_answering(
    db_client: TestClient,
) -> None:
    """The dependency builds the real source from settings that name no App Brain, so it refuses
    on its own without a request. A source that answered here would be answering about nothing."""
    unit_id = _register_ready_unit(db_client, "merge-admission-estate")

    body = _admission(db_client, unit_id)

    assert "merge_target_estate_source_unconfigured" in body["refusals"]


def test_the_route_requires_authentication(db_client: TestClient) -> None:
    unit_id = _register_ready_unit(db_client, "merge-admission-auth")

    assert db_client.get(f"/api/v1/work-units/{unit_id}/pr-merge-admission").status_code == 401


def test_an_unknown_unit_is_a_named_domain_error(db_client: TestClient) -> None:
    unknown = uuid.uuid4()

    response = db_client.get(f"/api/v1/work-units/{unknown}/pr-merge-admission", headers=WORKER)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "work_unit_not_found"
