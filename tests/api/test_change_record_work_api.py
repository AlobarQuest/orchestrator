"""GET /api/v1/change-records/{id}/work — the served surface (ADR-0029).

The service tests one directory over decide the RULE; this file exists for the two things a
service test structurally cannot see.

**A `response_model` silently DROPS every key the service returns but the model does not
declare.** This repository has shipped that defect: a field was added to a service, every
service-level assertion passed, and the HTTP body carried nothing. The consumer here is an
out-of-process program in another package, so the model is pinned to the keys the route composes
rather than trusted to have kept up.

**A record with no work must answer 200, not 404.** It is the ordinary state of every record a
person has approved and the carry has not reached, and a 404 would make the common case
indistinguishable from a broken identifier.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.api.schemas import ChangeRecordUnitResponse, ChangeRecordWorkResponse
from tests.api.test_lifecycle_api import SYSTEM
from tests.services.test_change_record_work import RECORD, _revision, _unit

WORK_PATH = "/api/v1/change-records/{record}/work"


def test_the_response_model_declares_every_key_the_route_composes() -> None:
    """Pinned without an HTTP client, so it cannot drift and cannot be skipped.

    The route builds this dict by hand; the model decides what survives to the wire. Asserting
    them equal is the only thing that makes "the service returns it" mean "the consumer gets it".
    """
    assert set(ChangeRecordWorkResponse.model_fields) == {
        "change_record_id",
        "revision_ids",
        "units",
        "all_units_completed",
    }
    assert set(ChangeRecordUnitResponse.model_fields) == {
        "unit_id",
        "unit_key",
        "revision_id",
        "state",
    }


def test_the_route_requires_authentication(db_client: TestClient) -> None:
    assert db_client.get(WORK_PATH.format(record=RECORD)).status_code == 401


def test_a_record_with_no_work_is_200_and_not_404(db_client: TestClient) -> None:
    """The ordinary not-yet-carried state. A 404 here would make the whole approved queue read
    as a set of broken identifiers."""
    response = db_client.get(WORK_PATH.format(record=RECORD), headers=SYSTEM)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "change_record_id": RECORD,
        "revision_ids": [],
        "units": [],
        "all_units_completed": False,
    }


def test_the_served_body_carries_the_units_and_the_verdict(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The evidence AND the verdict reach the wire.

    Written through a DIFFERENT session from the one the request uses, so the row is genuinely
    committed before the route reads it — the only reader that cannot see an uncommitted write.
    """
    with Session(migrated_engine) as session:
        revision = _revision(session, change_record_id=RECORD)
        unit = _unit(session, revision, "only", "completed")
        revision_id, unit_id = revision.id, unit.id

    response = db_client.get(WORK_PATH.format(record=RECORD), headers=SYSTEM)

    assert response.status_code == 200
    body = response.json()
    assert body["all_units_completed"] is True
    assert body["revision_ids"] == [str(revision_id)]
    assert body["units"] == [
        {
            "unit_id": str(unit_id),
            "unit_key": "only",
            "revision_id": str(revision_id),
            "state": "completed",
        }
    ]


def test_an_incomplete_unit_is_served_with_a_false_verdict(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The negative control. Without it the case above proves only that the route returns SOME
    body, not that the verdict it carries is computed from the units."""
    with Session(migrated_engine) as session:
        revision = _revision(session, change_record_id=RECORD)
        _unit(session, revision, "a-done", "completed")
        _unit(session, revision, "b-running", "executing")

    body = db_client.get(WORK_PATH.format(record=RECORD), headers=SYSTEM).json()

    assert body["all_units_completed"] is False
    assert sorted(u["state"] for u in body["units"]) == ["completed", "executing"]


def test_a_non_numeric_record_is_a_clean_422_and_not_a_500(db_client: TestClient) -> None:
    """The path parameter is typed, so FastAPI refuses before any service code runs. Asserted
    because only `DomainError` and `APIAuthenticationError` have handlers here — anything the
    stdlib raises from a route reaches the wire as a bare 500."""
    response = db_client.get("/api/v1/change-records/not-a-number/work", headers=SYSTEM)

    assert response.status_code == 422


def test_an_unknown_uuid_shaped_record_is_also_refused(db_client: TestClient) -> None:
    """A change record id is an integer in a FOREIGN system. A caller passing the orchestrator's
    own revision UUID is a plausible mistake and must not silently answer about nothing."""
    response = db_client.get(f"/api/v1/change-records/{uuid.uuid4()}/work", headers=SYSTEM)

    assert response.status_code == 422
