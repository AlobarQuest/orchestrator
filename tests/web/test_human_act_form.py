"""ADR-0055 decision 3: Devon resolves a human-act dependency at /review with its fingerprints.

The tests drive the rendered form, scraping the CSRF token as a browser would, and re-read what
was stored through another session. The detail is held to a strict schema and the secret scan.
"""

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Dependency, Event, WorkUnit
from tests._support.seeding import register_unit
from tests.api.test_lifecycle_api import HUMAN, SYSTEM

FINGERPRINT = {"sha256_prefix": "0123abcd", "length": 73}


def _dependency(engine: Engine, unit: WorkUnit) -> Dependency:
    with Session(engine) as session:
        row = session.scalar(select(Dependency).where(Dependency.work_unit_id == unit.id))
        assert row is not None
        session.expunge(row)
        return row


def _form(client: TestClient, unit: WorkUnit, dependency_id: uuid.UUID) -> dict[str, str]:
    page = client.get(f"/review/units/{unit.id}", headers=HUMAN)
    assert page.status_code == 200
    form = re.search(
        rf'action="/review/dependencies/{dependency_id}/resolution">(.*?)</form>', page.text, re.S
    )
    assert form is not None, "the review page renders no form for the pending human act"
    fields = {
        name: value
        for name, value in re.findall(r'name="([a-z_]+)" value="([^"]*)"', form.group(1))
        if name in {"csrf_token", "idempotency_key", "expected_version"}
    }
    assert set(fields) == {"csrf_token", "idempotency_key", "expected_version"}
    return fields


def _post(
    client: TestClient,
    dependency_id: uuid.UUID,
    fields: dict[str, str],
    values: dict[str, str] | None = None,
    *,
    headers: dict[str, str] = HUMAN,
):
    body = {
        **fields,
        "outcome": "satisfied",
        "note": "Minted the replacement in the console and staged it.",
        "sha256_prefix": FINGERPRINT["sha256_prefix"],
        "length": str(FINGERPRINT["length"]),
        "confirm": "yes",
        **(values or {}),
    }
    return client.post(
        f"/review/dependencies/{dependency_id}/resolution",
        headers=headers,
        data=body,
        follow_redirects=False,
    )


def test_a_human_resolves_a_human_act_with_its_fingerprint(
    db_client: TestClient, review_unit: WorkUnit, migrated_engine: Engine
) -> None:
    dependency = _dependency(migrated_engine, review_unit)
    fields = _form(db_client, review_unit, dependency.id)

    response = _post(db_client, dependency.id, fields)

    assert response.status_code == 303, response.text
    stored = _dependency(migrated_engine, review_unit)
    assert (stored.status, stored.resolved_by) == ("satisfied", "devon")
    assert stored.detail == {
        "fingerprint": FINGERPRINT,
        "note": "Minted the replacement in the console and staged it.",
    }


def test_a_note_alone_is_enough(
    db_client: TestClient, review_unit: WorkUnit, migrated_engine: Engine
) -> None:
    dependency = _dependency(migrated_engine, review_unit)
    fields = _form(db_client, review_unit, dependency.id)

    response = _post(db_client, dependency.id, fields, {"sha256_prefix": "", "length": ""})

    assert response.status_code == 303, response.text
    assert _dependency(migrated_engine, review_unit).detail == {
        "note": "Minted the replacement in the console and staged it."
    }


def test_a_failed_act_is_recorded_as_failed(
    db_client: TestClient, review_unit: WorkUnit, migrated_engine: Engine
) -> None:
    dependency = _dependency(migrated_engine, review_unit)
    fields = _form(db_client, review_unit, dependency.id)

    response = _post(db_client, dependency.id, fields, {"outcome": "failed"})

    assert response.status_code == 303, response.text
    assert _dependency(migrated_engine, review_unit).status == "failed"


@pytest.mark.parametrize(
    ("values", "code"),
    [
        pytest.param({"sha256_prefix": "0123ABCD"}, "human_act_detail_invalid", id="prefix_case"),
        pytest.param({"sha256_prefix": "0123abc"}, "human_act_detail_invalid", id="prefix_short"),
        pytest.param(
            {"sha256_prefix": "0123456789abcdef0"}, "human_act_detail_invalid", id="prefix_long"
        ),
        pytest.param({"length": ""}, "human_act_detail_invalid", id="prefix_without_length"),
        pytest.param({"sha256_prefix": ""}, "human_act_detail_invalid", id="length_without_prefix"),
        pytest.param({"length": "0"}, "human_act_detail_invalid", id="length_zero"),
        pytest.param({"length": "seven"}, "human_act_detail_invalid", id="length_not_a_number"),
        pytest.param({"length": "4097"}, "human_act_detail_invalid", id="length_too_long"),
        pytest.param({"length": "\u00b2"}, "human_act_detail_invalid", id="length_superscript"),
        pytest.param({"length": "9" * 5000}, "human_act_detail_invalid", id="length_huge"),
        pytest.param({"note": "x" * 501}, "human_act_detail_invalid", id="note_too_long"),
        pytest.param({"note": "   "}, "human_act_detail_invalid", id="note_blank"),
        pytest.param(
            {"note": "header was Authorization: Bearer abc.def"},
            "human_act_detail_secret",
            id="note_holds_a_bearer",
        ),
        pytest.param(
            # The bootstrap-token shape, assembled so no token-shaped literal sits in the tree.
            {"note": "token " + "0" + "." + "1" * 8 + "-" + "2" * 27 + ".abcdefghijk"},
            "human_act_detail_secret",
            id="note_holds_a_bws_token",
        ),
        pytest.param({"outcome": "pending"}, "invalid_dependency_status", id="outcome_pending"),
    ],
)
def test_a_malformed_resolution_is_refused_and_writes_nothing(
    db_client: TestClient,
    review_unit: WorkUnit,
    migrated_engine: Engine,
    values: dict[str, str],
    code: str,
) -> None:
    dependency = _dependency(migrated_engine, review_unit)
    fields = _form(db_client, review_unit, dependency.id)

    response = _post(db_client, dependency.id, fields, values)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == code
    assert _dependency(migrated_engine, review_unit).status == "pending"


def test_a_machine_actor_may_not_use_the_form(
    db_client: TestClient, review_unit: WorkUnit, migrated_engine: Engine
) -> None:
    dependency = _dependency(migrated_engine, review_unit)
    fields = _form(db_client, review_unit, dependency.id)

    response = _post(db_client, dependency.id, fields, headers=SYSTEM)

    assert response.status_code in {401, 403}, response.text
    assert _dependency(migrated_engine, review_unit).status == "pending"


def test_a_form_without_its_csrf_token_is_refused(
    db_client: TestClient, review_unit: WorkUnit, migrated_engine: Engine
) -> None:
    dependency = _dependency(migrated_engine, review_unit)
    fields = _form(db_client, review_unit, dependency.id)

    response = _post(db_client, dependency.id, {**fields, "csrf_token": "forged"})

    assert response.status_code == 403, response.text
    assert _dependency(migrated_engine, review_unit).status == "pending"


def test_a_double_submitted_form_resolves_once(
    db_client: TestClient, review_unit: WorkUnit, migrated_engine: Engine
) -> None:
    dependency = _dependency(migrated_engine, review_unit)
    fields = _form(db_client, review_unit, dependency.id)

    first = _post(db_client, dependency.id, fields)
    second = _post(db_client, dependency.id, fields)

    assert (first.status_code, second.status_code) == (303, 303)
    with Session(migrated_engine) as session:
        events = session.scalars(
            select(Event).where(Event.idempotency_key == fields["idempotency_key"])
        ).all()
        assert len(events) == 1 and events[0].action == "dependency.resolved"


def test_a_work_unit_dependency_gets_no_form_and_is_refused(
    db_client: TestClient, review_unit: WorkUnit, migrated_engine: Engine
) -> None:
    """Its predecessor's completion resolves it; a hand resolution would bypass the ordering."""
    dependency = _dependency(migrated_engine, review_unit)
    fields = _form(db_client, review_unit, dependency.id)
    with Session(migrated_engine) as session:
        predecessor = register_unit(session, "human-act-predecessor")
        row = session.get(Dependency, dependency.id)
        assert row is not None
        row.kind = "work_unit"
        row.external_ref = None
        row.depends_on_work_unit_id = predecessor.id
        session.commit()

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    assert f"/review/dependencies/{dependency.id}/resolution" not in page.text

    response = _post(db_client, dependency.id, fields)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "dependency_not_a_human_act"
    assert _dependency(migrated_engine, review_unit).status == "pending"


def test_a_resolved_act_leaves_the_page(
    db_client: TestClient, review_unit: WorkUnit, migrated_engine: Engine
) -> None:
    dependency = _dependency(migrated_engine, review_unit)
    fields = _form(db_client, review_unit, dependency.id)
    assert _post(db_client, dependency.id, fields).status_code == 303

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert f"/review/dependencies/{dependency.id}/resolution" not in page.text


@pytest.mark.parametrize("state", ["completed", "cancelled"])
def test_a_settled_unit_offers_no_human_act(
    db_client: TestClient, review_unit: WorkUnit, migrated_engine: Engine, state: str
) -> None:
    dependency = _dependency(migrated_engine, review_unit)
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = state
        session.commit()

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert f"/review/dependencies/{dependency.id}/resolution" not in page.text


def test_a_stale_form_cannot_overwrite_a_recorded_outcome(
    db_client: TestClient, review_unit: WorkUnit, migrated_engine: Engine
) -> None:
    """Two tabs: the second form's fresh key must not turn a recorded `satisfied` into `failed`."""
    dependency = _dependency(migrated_engine, review_unit)
    first = _form(db_client, review_unit, dependency.id)
    second = _form(db_client, review_unit, dependency.id)
    assert _post(db_client, dependency.id, first).status_code == 303

    response = _post(db_client, dependency.id, second, {"outcome": "failed"})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "dependency_not_pending"
    assert _dependency(migrated_engine, review_unit).status == "satisfied"


def test_the_route_refuses_a_settled_unit(
    db_client: TestClient, review_unit: WorkUnit, migrated_engine: Engine
) -> None:
    """A form rendered before the unit settled still carries a valid token."""
    dependency = _dependency(migrated_engine, review_unit)
    fields = _form(db_client, review_unit, dependency.id)
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = "cancelled"
        session.commit()

    response = _post(db_client, dependency.id, fields)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "dependency_unit_settled"
    assert _dependency(migrated_engine, review_unit).status == "pending"
