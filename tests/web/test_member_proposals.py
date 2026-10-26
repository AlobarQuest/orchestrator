"""`/review/member-proposals/{id}`: a person reads the change beside the record, then decides."""

import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import SdsMember, SdsMemberGrant, SdsMemberProposal
from tests.api.test_lifecycle_api import HUMAN, SYSTEM, WORKER

BODY = {
    "summary": "enroll brain",
    "changes": [
        {
            "repository": "AlobarQuest/brain",
            "reason": "bring it in",
            "state": "onboarding",
            "owner": "devon",
            "grants": {"rollouts": {"state": "granted", "reason": "ci deploys it"}},
        }
    ],
}


def _stage(client: TestClient) -> str:
    response = client.post(
        "/api/v1/member-proposals",
        headers={**SYSTEM, "Idempotency-Key": f"web-{uuid.uuid4().hex}"},
        json=BODY,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _form(page: str, proposal_id: str, action: str) -> dict[str, str]:
    form = re.search(
        rf'action="/review/member-proposals/{proposal_id}/{action}">(.*?)</form>', page, re.DOTALL
    )
    assert form is not None, f"no {action} form"
    return dict(
        re.findall(r'name="(csrf_token|idempotency_key|digest)" value="([^"]+)"', form.group(1))
    )


def test_human_confirms_from_the_page(db_client: TestClient, migrated_engine: Engine) -> None:
    proposal_id = _stage(db_client)
    page = db_client.get(f"/review/member-proposals/{proposal_id}", headers=HUMAN)
    assert page.status_code == 200

    response = db_client.post(
        f"/review/member-proposals/{proposal_id}/confirm",
        data={**_form(page.text, proposal_id, "confirm"), "confirm": "yes"},
        headers=HUMAN,
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(migrated_engine) as session:
        member = session.scalars(select(SdsMember)).one()
        assert member.repository == "AlobarQuest/brain"
        assert {g.set_by for g in session.scalars(select(SdsMemberGrant))} == {"devon"}
        row = session.get(SdsMemberProposal, uuid.UUID(proposal_id))
        assert row is not None and row.state == "confirmed"


def test_human_withdraws_from_the_page(db_client: TestClient, migrated_engine: Engine) -> None:
    proposal_id = _stage(db_client)
    page = db_client.get(f"/review/member-proposals/{proposal_id}", headers=HUMAN)

    response = db_client.post(
        f"/review/member-proposals/{proposal_id}/withdraw",
        data={**_form(page.text, proposal_id, "withdraw"), "confirm": "yes", "reason": "no"},
        headers=HUMAN,
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(migrated_engine) as session:
        assert session.scalars(select(SdsMember)).first() is None
        row = session.get(SdsMemberProposal, uuid.UUID(proposal_id))
        assert row is not None and row.state == "withdrawn" and row.decision_reason == "no"


def test_page_shows_deploys_label_for_rollouts(db_client: TestClient) -> None:
    proposal_id = _stage(db_client)
    page = db_client.get(f"/review/member-proposals/{proposal_id}", headers=HUMAN).text
    assert "<th>Deploys</th>" in page
    assert "rollouts" not in page
    assert "not_assessed → <strong>granted</strong>" in page.replace("—", "not_assessed")


def test_machine_actor_gets_403_on_the_page(db_client: TestClient) -> None:
    proposal_id = _stage(db_client)
    for headers in (SYSTEM, WORKER):
        assert (
            db_client.get(f"/review/member-proposals/{proposal_id}", headers=headers).status_code
            == 403
        )


def test_an_unknown_proposal_is_404(db_client: TestClient) -> None:
    assert (
        db_client.get(f"/review/member-proposals/{uuid.uuid4()}", headers=HUMAN).status_code == 404
    )


def test_machine_actor_post_is_refused(db_client: TestClient, migrated_engine: Engine) -> None:
    proposal_id = _stage(db_client)
    page = db_client.get(f"/review/member-proposals/{proposal_id}", headers=HUMAN)
    for headers in (SYSTEM, WORKER):
        confirm = db_client.post(
            f"/review/member-proposals/{proposal_id}/confirm",
            data={**_form(page.text, proposal_id, "confirm"), "confirm": "yes"},
            headers=headers,
            follow_redirects=False,
        )
        withdraw = db_client.post(
            f"/review/member-proposals/{proposal_id}/withdraw",
            data={**_form(page.text, proposal_id, "withdraw"), "confirm": "yes", "reason": "x"},
            headers=headers,
            follow_redirects=False,
        )
        assert confirm.status_code == 403 and withdraw.status_code == 403
    with Session(migrated_engine) as session:
        row = session.get(SdsMemberProposal, uuid.UUID(proposal_id))
        assert row is not None and row.state == "staged"
        assert session.scalars(select(SdsMember)).first() is None


def test_a_record_that_moved_after_the_page_rendered_is_refused(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    first = _stage(db_client)
    page = db_client.get(f"/review/member-proposals/{first}", headers=HUMAN)
    fields = _form(page.text, first, "confirm")
    # Another proposal creates the same repository after the first page was read.
    second = _stage(db_client)
    other = db_client.get(f"/review/member-proposals/{second}", headers=HUMAN)
    done = db_client.post(
        f"/review/member-proposals/{second}/confirm",
        data={**_form(other.text, second, "confirm"), "confirm": "yes"},
        headers=HUMAN,
        follow_redirects=False,
    )
    assert done.status_code == 303

    response = db_client.post(
        f"/review/member-proposals/{first}/confirm",
        data={**fields, "confirm": "yes"},
        headers=HUMAN,
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "member_proposal_changed"
    with Session(migrated_engine) as session:
        assert len(session.scalars(select(SdsMember)).all()) == 1
        row = session.get(SdsMemberProposal, uuid.UUID(first))
        assert row is not None and row.state == "staged"
