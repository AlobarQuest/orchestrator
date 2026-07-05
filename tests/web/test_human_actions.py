import re
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import AuthConfig
from orchestrator.identity.registry import RegistryAdapter
from orchestrator.kernel.states import WorkUnitState
from orchestrator.main import create_app
from orchestrator.persistence.models import Approval, Event, WorkUnit
from tests.api.test_lifecycle_api import HUMAN, WORKER


def test_detail_has_human_actions_but_no_worker_or_creation_controls(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert page.status_code == 200
    assert "Review outcome" in page.text
    assert "Cancel work unit" in page.text
    assert "Authorize retry" in page.text
    assert "Claim work" not in page.text
    assert "Create work unit" not in page.text
    assert "<label" in page.text


def test_review_form_uses_post_redirect_get(db_client: TestClient, review_unit: WorkUnit) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    form = re.search(
        rf'action="/review/units/{review_unit.id}/review">(.*?)</form>',
        page.text,
    )
    assert form is not None
    token = re.search(r'name="csrf_token" value="([^"]+)"', form.group(1))
    idempotency = re.search(r'name="idempotency_key" value="([^"]+)"', form.group(1))
    assert token is not None and idempotency is not None

    response = db_client.post(
        f"/review/units/{review_unit.id}/review",
        headers=HUMAN,
        data={
            "csrf_token": token.group(1),
            "idempotency_key": idempotency.group(1),
            "expected_version": str(review_unit.version),
            "outcome": "revision_required",
            "reason": "Needs another pass",
            "confirm": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/review/units/{review_unit.id}"

    replay = db_client.post(
        f"/review/units/{review_unit.id}/review",
        headers=HUMAN,
        data={
            "csrf_token": token.group(1),
            "idempotency_key": idempotency.group(1),
            "expected_version": str(review_unit.version),
            "outcome": "revision_required",
            "reason": "Needs another pass",
            "confirm": "yes",
        },
        follow_redirects=False,
    )
    assert replay.status_code == 303
    detail = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    pack = db_client.get(f"/review/units/{review_unit.id}/evidence-pack", headers=HUMAN)
    assert "Needs another pass" in detail.text
    assert "Needs another pass" in pack.text


def _form(page: str, unit_id: object, action: str) -> tuple[str, str]:
    form = re.search(rf'action="/review/units/{unit_id}/{action}">(.*?)</form>', page)
    assert form is not None
    token = re.search(r'name="csrf_token" value="([^"]+)"', form.group(1))
    key = re.search(r'name="idempotency_key" value="([^"]+)"', form.group(1))
    assert token is not None and key is not None
    return token.group(1), key.group(1)


def test_inactive_human_and_worker_posts_are_denied(
    auth_config: AuthConfig,
    db_client: TestClient,
    review_unit: WorkUnit,
) -> None:
    inactive_registry = RegistryAdapter(
        {
            "schema": "orchestrator-actor-bundle/v1",
            "source_revision": "0123456789abcdef0123456789abcdef01234567",
            "actors": [
                {
                    "agent_id": "devon",
                    "version": 1,
                    "status": "retired",
                    "runtime": "human",
                    "authority_profile": "human-operator-v1",
                }
            ],
        }
    )
    inactive = TestClient(create_app(replace(auth_config, registry=inactive_registry)))
    assert inactive.get("/review", headers=HUMAN).status_code == 401
    assert (
        inactive.post(
            f"/review/units/{review_unit.id}/cancel",
            headers=HUMAN,
            data={"reason": "denied", "expected_version": str(review_unit.version)},
        ).status_code
        == 401
    )

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form(page.text, review_unit.id, "cancel")
    denied = db_client.post(
        f"/review/units/{review_unit.id}/cancel",
        headers=WORKER,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(review_unit.version),
            "reason": "worker denied",
            "confirm": "yes",
        },
    )
    assert denied.status_code == 403


def test_stale_version_form_returns_conflict(db_client: TestClient, review_unit: WorkUnit) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form(page.text, review_unit.id, "review")
    response = db_client.post(
        f"/review/units/{review_unit.id}/review",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(review_unit.version - 1),
            "outcome": "revision_required",
            "reason": "stale",
            "confirm": "yes",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"
    assert response.json()["error"]["current_version"] == review_unit.version


def test_approval_replay_converges_to_one_row_and_event(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = WorkUnitState.AWAITING_APPROVAL
        session.commit()
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form(page.text, review_unit.id, "approval")
    data = {
        "csrf_token": token,
        "idempotency_key": key,
        "expected_version": str(review_unit.version),
        "reason": "exact replay",
        "confirm": "yes",
    }
    first = db_client.post(
        f"/review/units/{review_unit.id}/approval",
        headers=HUMAN,
        data=data,
        follow_redirects=False,
    )
    replay = db_client.post(
        f"/review/units/{review_unit.id}/approval",
        headers=HUMAN,
        data=data,
        follow_redirects=False,
    )
    assert first.status_code == replay.status_code == 303
    with Session(migrated_engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Approval).where(Approval.idempotency_key == key)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.idempotency_key == key)
            )
            == 1
        )


def test_cancel_effect_persists_reason_and_renders(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = WorkUnitState.FAILED
        session.commit()
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form(page.text, review_unit.id, "cancel")
    response = db_client.post(
        f"/review/units/{review_unit.id}/cancel",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(review_unit.version),
            "reason": "Portfolio priority changed",
            "confirm": "yes",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        event = session.scalar(select(Event).where(Event.idempotency_key == key))
        assert unit is not None and unit.state == WorkUnitState.CANCELLED
        assert event is not None and event.payload["reason"] == "Portfolio priority changed"
    detail = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    pack = db_client.get(f"/review/units/{review_unit.id}/evidence-pack", headers=HUMAN)
    assert "Portfolio priority changed" in detail.text
    assert "Portfolio priority changed" in pack.text


def test_retry_form_applies_canonical_budget_and_ready_effect(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.state = WorkUnitState.FAILED
        unit.attempt_count = unit.max_attempts
        old_max = unit.max_attempts
        session.commit()
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)
    token, key = _form(page.text, review_unit.id, "retry")
    response = db_client.post(
        f"/review/units/{review_unit.id}/retry",
        headers=HUMAN,
        data={
            "csrf_token": token,
            "idempotency_key": key,
            "expected_version": str(review_unit.version),
            "new_max_attempts": str(old_max + 1),
            "reason": "One bounded retry",
            "confirm": "yes",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        assert unit.state == WorkUnitState.READY
        assert unit.max_attempts == old_max + 1
