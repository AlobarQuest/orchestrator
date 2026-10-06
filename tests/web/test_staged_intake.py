"""`/review/staged-intakes/{id}`: the decision first, one button, and the ordinary registration.

ADR-0006 amendment 1. The CLI stages a payload as SYSTEM; this page is where a live human
session decides, and the decision facts are rendered BEFORE the button rather than after the
registration they are meant to inform.
"""

import re
import uuid
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.api.routes.common import get_landing_source
from orchestrator.persistence.models import Event, StagedPackageIntake, WorkPackageRevision
from orchestrator.services.landing.estate_landing import LANDING_REDEPLOYS, EstateAnswer
from tests.api.test_lifecycle_api import HUMAN, SYSTEM, WORKER
from tests.api.test_package_intake_api import intake_payload

BUMP_SNAPSHOT = {
    "title": "Bump httpx",
    "outcome": {"what": "httpx is current in change-manager."},
    "scope": {"in": ["lockfile"]},
    "dependencies": [],
    "applicable_standards": ["STD-1"],
    "profile_fields": {
        "target_repo": "AlobarQuest/change-manager",
        "package": "httpx",
        "from_version": "0.27.0",
        "to_version": "0.28.1",
    },
}


class _Landing:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def landing_for(self, github_repo: str) -> EstateAnswer:
        self.asked.append(github_repo)
        return EstateAnswer(LANDING_REDEPLOYS)


def _stage(client: TestClient, **overrides: Any) -> str:
    body = intake_payload(
        **{
            "idempotency_key": f"staged-web-{uuid.uuid4().hex}",
            "content_hash": f"sha256:{uuid.uuid4().hex}",
            "profile": "dependency-update",
            "enforcement_snapshot": BUMP_SNAPSHOT,
            **overrides,
        }
    )
    response = client.post("/api/v1/staged-intakes", headers=SYSTEM, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _confirm_fields(page: str, staged_id: str) -> dict[str, str]:
    form = re.search(
        rf'action="/review/staged-intakes/{staged_id}/confirm">(.*?)</form>', page, re.DOTALL
    )
    assert form is not None, "the staged page renders no confirm form"
    return dict(re.findall(r'name="(csrf_token|idempotency_key)" value="([^"]+)"', form.group(1)))


def _press(client: TestClient, staged_id: str, fields: dict[str, str], **overrides: str):
    return client.post(
        f"/review/staged-intakes/{staged_id}/confirm",
        data={**fields, "confirm": "yes", **overrides},
        headers=HUMAN,
        follow_redirects=False,
    )


def _decision_section(page: str) -> str:
    match = re.search(r'<section class="decision"[\s\S]*?</section>', page)
    assert match is not None, "no decision surface was rendered"
    return match.group(0)


def test_the_page_puts_the_decision_before_the_button_and_the_button_before_the_content(
    db_client: TestClient,
) -> None:
    landing = _Landing()
    cast(FastAPI, db_client.app).dependency_overrides[get_landing_source] = lambda: landing
    staged_id = _stage(db_client)

    page = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN)

    assert page.status_code == 200
    surface = _decision_section(page.text)
    assert "httpx is current in change-manager." in surface
    assert "updates httpx from 0.27.0 to 0.28.1" in surface
    assert "for a dependency-update change in general" in surface
    assert "redeploys a running service" in surface
    assert landing.asked == ["AlobarQuest/change-manager"]
    decision = page.text.index('<section class="decision"')
    button = page.text.index("Register this intake")
    content = page.text.index("What was verified, and by whom")
    assert decision < button < content
    assert "Assigned when the intake is registered" in page.text


def test_pressing_the_button_registers_the_intake_as_the_person(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    staged_id = _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN)

    response = _press(db_client, staged_id, _confirm_fields(page.text, staged_id))

    assert response.status_code == 303
    with Session(migrated_engine) as session:
        staged = session.get(StagedPackageIntake, uuid.UUID(staged_id))
        assert staged is not None and staged.state == "registered"
        assert response.headers["location"] == (f"/review/intakes/{staged.registered_revision_id}")
        revision = session.get(WorkPackageRevision, staged.registered_revision_id)
        assert revision is not None and revision.registered_by == "devon"
        event = session.scalars(select(Event).where(Event.subject_id == revision.id)).one()
        assert event.idempotency_key == staged.idempotency_key
        assert event.payload["staged_intake_id"] == staged_id


def test_a_second_press_lands_on_the_same_revision(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    staged_id = _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN)
    fields = _confirm_fields(page.text, staged_id)

    first = _press(db_client, staged_id, fields)
    second = _press(db_client, staged_id, fields)

    assert first.status_code == second.status_code == 303
    assert first.headers["location"] == second.headers["location"]
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(WorkPackageRevision)) == 1
        assert session.scalar(select(func.count()).select_from(Event)) == 1


def test_a_registered_row_offers_no_button_and_links_its_revision(
    db_client: TestClient,
) -> None:
    staged_id = _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN)
    location = _press(db_client, staged_id, _confirm_fields(page.text, staged_id)).headers[
        "location"
    ]

    again = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN)

    assert "Register this intake" not in again.text
    assert f'href="{location}"' in again.text


def test_the_staged_and_registered_pages_show_the_same_decision(
    db_client: TestClient,
) -> None:
    staged_id = _stage(
        db_client, enforcement_snapshot={**BUMP_SNAPSHOT, "reach": ["source_repository"]}
    )
    page = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN)
    location = _press(db_client, staged_id, _confirm_fields(page.text, staged_id)).headers[
        "location"
    ]

    registered = db_client.get(location, headers=HUMAN)

    assert _decision_section(page.text) == _decision_section(registered.text)


def test_a_token_for_one_staged_row_cannot_confirm_another(db_client: TestClient) -> None:
    first, second = _stage(db_client), _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{first}", headers=HUMAN)

    response = _press(db_client, second, _confirm_fields(page.text, first))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_rejected"


def test_a_tampered_idempotency_key_is_refused(db_client: TestClient) -> None:
    staged_id = _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN)

    response = _press(
        db_client, staged_id, _confirm_fields(page.text, staged_id), idempotency_key="other"
    )

    assert response.status_code == 403


def test_an_unconfirmed_press_is_refused(db_client: TestClient) -> None:
    staged_id = _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN)

    response = _press(db_client, staged_id, _confirm_fields(page.text, staged_id), confirm="")

    assert response.status_code == 403


def test_a_machine_can_neither_read_nor_confirm_the_page(db_client: TestClient) -> None:
    staged_id = _stage(db_client)

    for headers in (SYSTEM, WORKER):
        assert (
            db_client.get(f"/review/staged-intakes/{staged_id}", headers=headers).status_code == 403
        )
        response = db_client.post(
            f"/review/staged-intakes/{staged_id}/confirm",
            data={"confirm": "yes"},
            headers=headers,
            follow_redirects=False,
        )
        assert response.status_code == 403


def test_an_unknown_staged_row_is_not_found(db_client: TestClient) -> None:
    assert db_client.get(f"/review/staged-intakes/{uuid.uuid4()}", headers=HUMAN).status_code == 404


def test_a_staged_intake_is_the_first_thing_on_the_queue(db_client: TestClient) -> None:
    registered, waiting = _stage(db_client), _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{registered}", headers=HUMAN)
    _press(db_client, registered, _confirm_fields(page.text, registered))

    queue = db_client.get("/review", headers=HUMAN).text

    assert f"/review/staged-intakes/{waiting}" in queue
    assert f"/review/staged-intakes/{registered}" not in queue
    assert queue.index("Staged intakes awaiting your confirmation") < queue.index(
        "Packages with no breakdown in progress"
    )


# --- withdraw --------------------------------------------------------------------------------


def _withdraw_fields(page: str, staged_id: str) -> dict[str, str]:
    form = re.search(
        rf'action="/review/staged-intakes/{staged_id}/withdraw">(.*?)</form>', page, re.DOTALL
    )
    assert form is not None, "the staged page renders no withdraw form"
    return dict(re.findall(r'name="(csrf_token|idempotency_key)" value="([^"]+)"', form.group(1)))


def _withdraw(client: TestClient, staged_id: str, fields: dict[str, str], **overrides: str):
    return client.post(
        f"/review/staged-intakes/{staged_id}/withdraw",
        data={**fields, "reason": "the bump is superseded", "confirm": "yes", **overrides},
        headers=HUMAN,
        follow_redirects=False,
    )


def test_withdrawing_stamps_the_row_and_takes_it_off_the_queue(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    staged_id = _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN)

    response = _withdraw(db_client, staged_id, _withdraw_fields(page.text, staged_id))

    assert response.status_code == 303
    assert response.headers["location"] == f"/review/staged-intakes/{staged_id}"
    with Session(migrated_engine) as session:
        staged = session.get(StagedPackageIntake, uuid.UUID(staged_id))
        assert staged is not None and staged.state == "withdrawn"
        assert (staged.withdrawn_by, staged.withdrawal_reason) == (
            "devon",
            "the bump is superseded",
        )
    after = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN).text
    assert "Withdrawn by <code>devon</code>" in after
    assert "the bump is superseded" in after
    assert "Register this intake" not in after and "Withdraw this intake" not in after
    assert f"/review/staged-intakes/{staged_id}" not in db_client.get("/review", headers=HUMAN).text


def test_a_withdrawn_row_refuses_the_confirm(db_client: TestClient) -> None:
    staged_id = _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN).text
    _withdraw(db_client, staged_id, _withdraw_fields(page, staged_id))

    response = _press(db_client, staged_id, _confirm_fields(page, staged_id))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "staged_intake_not_confirmable"


def test_each_token_does_only_its_own_action(db_client: TestClient) -> None:
    staged_id = _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN).text

    assert _withdraw(db_client, staged_id, _confirm_fields(page, staged_id)).status_code == 403
    assert _press(db_client, staged_id, _withdraw_fields(page, staged_id)).status_code == 403


def test_a_withdraw_token_for_one_row_cannot_withdraw_another(db_client: TestClient) -> None:
    first, second = _stage(db_client), _stage(db_client)
    page = db_client.get(f"/review/staged-intakes/{first}", headers=HUMAN).text

    assert _withdraw(db_client, second, _withdraw_fields(page, first)).status_code == 403


def test_a_withdraw_needs_a_reason_and_a_confirmation(db_client: TestClient) -> None:
    staged_id = _stage(db_client)
    fields = _withdraw_fields(
        db_client.get(f"/review/staged-intakes/{staged_id}", headers=HUMAN).text, staged_id
    )

    assert _withdraw(db_client, staged_id, fields, reason="").status_code == 422
    assert _withdraw(db_client, staged_id, fields, confirm="").status_code == 403


def test_a_machine_cannot_withdraw(db_client: TestClient) -> None:
    staged_id = _stage(db_client)

    for headers in (SYSTEM, WORKER):
        response = db_client.post(
            f"/review/staged-intakes/{staged_id}/withdraw",
            data={"reason": "r", "confirm": "yes"},
            headers=headers,
            follow_redirects=False,
        )
        assert response.status_code == 403
