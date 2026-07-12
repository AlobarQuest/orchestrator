"""WS-P2.1 Task 5b: the /review surface that closes a reconciliation condition.

Without this, conditions could be created but never resolved: `open_conditions()` could only ever
grow, and AC-008's "no open condition implies an illegal auto-mutation" invariant would be
violated on day one.

The tests drive the rendered form -- scraping the CSRF token exactly as an operator's browser
would -- because a recovery surface an operator cannot actually act on is not a recovery surface.
"""

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import ReconciliationCondition, WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.reconciliation import (
    ConditionCommand,
    ConditionOutcome,
    open_conditions,
    record_reconciliation_condition,
)
from tests.api.test_lifecycle_api import HUMAN, SYSTEM
from tests.services.test_dependencies import register_unit

SYSTEM_ACTOR = ActorContext("system", ActorRole.SYSTEM)


@pytest.fixture
def flagged_unit(migrated_engine: Engine) -> WorkUnit:
    """A work unit carrying one open reconciliation condition."""
    with Session(migrated_engine) as session:
        unit = register_unit(session, "resolve-route")
        session.commit()
        outcome = record_reconciliation_condition(
            session,
            ConditionCommand(
                actor=SYSTEM_ACTOR,
                work_unit_id=unit.id,
                observation_kind="github_check",
                condition_type="check_result_flip",
                key_facts={"check_name": "Quality"},
                stored_state={"conclusion": "success"},
                observed_state={"conclusion": "failure"},
                detail="Quality flipped after verification read it",
            ),
        )
        assert isinstance(outcome, ConditionOutcome)
        session.refresh(unit)
        session.expunge(unit)
        return unit


def _condition_id(session: Session, unit: WorkUnit) -> uuid.UUID:
    rows = open_conditions(session, unit.id)
    assert len(rows) == 1
    return rows[0].id


def _resolve_form(client: TestClient, unit: WorkUnit, condition_id: uuid.UUID) -> dict[str, str]:
    """Render the operator's page and scrape the form, as a real browser would."""
    page = client.get(f"/review/units/{unit.id}", headers=HUMAN)
    assert page.status_code == 200
    form = re.search(
        rf'action="/review/reconciliation/conditions/{condition_id}/resolution">(.*?)</form>',
        page.text,
        re.S,
    )
    assert form is not None, "the review page renders no resolve form for the open condition"
    token = re.search(r'name="csrf_token" value="([^"]+)"', form.group(1))
    key = re.search(r'name="idempotency_key" value="([^"]+)"', form.group(1))
    assert token is not None and key is not None
    return {"csrf_token": token.group(1), "idempotency_key": key.group(1)}


def _post(
    client: TestClient,
    condition_id: uuid.UUID,
    fields: dict[str, str],
    *,
    decision: str = "corrected",
    confirm: str | None = "yes",
):
    body = {
        **fields,
        "decision": decision,
        "rationale": "Re-ran the check; it is green.",
    }
    if confirm is not None:
        body["confirm"] = confirm
    return client.post(
        f"/review/reconciliation/conditions/{condition_id}/resolution",
        headers=HUMAN,
        data=body,
        follow_redirects=False,
    )


def test_the_review_page_surfaces_the_open_condition(
    db_client: TestClient, flagged_unit: WorkUnit
) -> None:
    page = db_client.get(f"/review/units/{flagged_unit.id}", headers=HUMAN)

    assert page.status_code == 200
    assert "Reconciliation conditions" in page.text
    assert "check_result_flip" in page.text
    assert "Quality flipped after verification read it" in page.text


def test_a_human_resolves_the_condition_and_it_leaves_the_open_set(
    db_client: TestClient, flagged_unit: WorkUnit, migrated_engine: Engine
) -> None:
    with Session(migrated_engine) as session:
        condition_id = _condition_id(session, flagged_unit)
    fields = _resolve_form(db_client, flagged_unit, condition_id)

    response = _post(db_client, condition_id, fields)

    assert response.status_code == 303
    with Session(migrated_engine) as session:
        assert open_conditions(session, flagged_unit.id) == ()
        resolved = session.get(ReconciliationCondition, condition_id)
        assert resolved is not None  # append-only: the condition row itself is never deleted


def test_resolution_requires_csrf_and_explicit_confirmation(
    db_client: TestClient, flagged_unit: WorkUnit, migrated_engine: Engine
) -> None:
    with Session(migrated_engine) as session:
        condition_id = _condition_id(session, flagged_unit)
    fields = _resolve_form(db_client, flagged_unit, condition_id)

    unconfirmed = _post(db_client, condition_id, fields, confirm=None)
    forged = _post(db_client, condition_id, {**fields, "csrf_token": "/w==.invalid"})

    assert unconfirmed.status_code == 403
    assert unconfirmed.json()["error"]["code"] == "csrf_rejected"
    assert forged.status_code == 403
    assert forged.json()["error"]["code"] == "csrf_rejected"
    with Session(migrated_engine) as session:
        assert len(open_conditions(session, flagged_unit.id)) == 1


def test_resolving_twice_is_refused(
    db_client: TestClient, flagged_unit: WorkUnit, migrated_engine: Engine
) -> None:
    with Session(migrated_engine) as session:
        condition_id = _condition_id(session, flagged_unit)
    first_fields = _resolve_form(db_client, flagged_unit, condition_id)
    assert _post(db_client, condition_id, first_fields).status_code == 303

    second = _post(
        db_client,
        condition_id,
        {**first_fields, "idempotency_key": str(uuid.uuid4())},
        decision="dismissed",
    )

    assert second.status_code == 403  # the CSRF token is bound to the original idempotency key
    # And with a freshly-minted, fully valid form the service itself still refuses:
    with Session(migrated_engine) as session:
        assert open_conditions(session, flagged_unit.id) == ()


def test_a_machine_actor_cannot_resolve(
    db_client: TestClient, flagged_unit: WorkUnit, migrated_engine: Engine
) -> None:
    """Detection never auto-resolves -- a resolution is an operator decision."""
    with Session(migrated_engine) as session:
        condition_id = _condition_id(session, flagged_unit)

    response = db_client.post(
        f"/review/reconciliation/conditions/{condition_id}/resolution",
        headers=SYSTEM,
        data={
            "decision": "accepted",
            "rationale": "auto-resolved",
            "idempotency_key": str(uuid.uuid4()),
            "csrf_token": "x",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "human_actor_required"
    with Session(migrated_engine) as session:
        assert len(open_conditions(session, flagged_unit.id)) == 1
