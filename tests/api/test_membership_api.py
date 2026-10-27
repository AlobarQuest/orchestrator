"""`POST /api/v1/member-proposals`: SYSTEM stages a membership proposal for a person to confirm."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.api.schemas.membership import (
    GrantView,
    MemberProposalResponse,
    MembersResponse,
    MemberView,
)
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import (
    MEMBER_SERVICES,
    SdsMember,
    SdsMemberApp,
    SdsMemberGrant,
    SdsMemberProposal,
)
from orchestrator.services.membership.proposals import confirm_member_proposal, read_digest
from tests.api.test_lifecycle_api import HUMAN, SYSTEM, VERIFIER, WORKER

OBSERVER = {"Authorization": "Bearer observer-token", "X-Credential-Key-Id": "observer-key"}

BODY = {
    "summary": "enroll brain",
    "changes": [
        {"repository": "AlobarQuest/brain", "reason": "r", "state": "onboarding", "owner": "devon"}
    ],
}


def _post(client: TestClient, headers: dict[str, str], key: str | None = None) -> object:
    body = {**BODY, "idempotency_key": key or f"m-{uuid.uuid4().hex}", "expected_version": 0}
    return client.post("/api/v1/member-proposals", headers=headers, json=body)


def test_the_response_model_declares_exactly_the_pinned_fields() -> None:
    assert set(MemberProposalResponse.model_fields) == {
        "id",
        "state",
        "review_path",
        "staged_by",
        "staged_at",
    }


def test_system_stages_through_the_api(db_client: TestClient, migrated_engine: Engine) -> None:
    response = _post(db_client, SYSTEM)

    assert response.status_code == 201, response.text  # type: ignore[attr-defined]
    served = response.json()  # type: ignore[attr-defined]
    assert served["state"] == "staged"
    assert served["review_path"] == f"/review/member-proposals/{served['id']}"
    with Session(migrated_engine) as session:
        assert session.get(SdsMemberProposal, uuid.UUID(served["id"])) is not None
        assert session.scalar(select(func.count()).select_from(SdsMember)) == 0


def test_observer_cannot_stage(db_client: TestClient) -> None:
    for headers in (OBSERVER, WORKER, VERIFIER, HUMAN):
        response = _post(db_client, headers)
        assert response.status_code == 403  # type: ignore[attr-defined]
        assert response.json()["error"]["code"] == "role_forbidden"  # type: ignore[attr-defined]


def test_a_missing_idempotency_key_is_refused(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/member-proposals", headers=SYSTEM, json={**BODY, "expected_version": 0}
    )
    assert response.status_code == 422


def test_a_refused_body_surfaces_its_code(db_client: TestClient) -> None:
    bad = {"summary": "s", "changes": [{"repository": "nope", "reason": "r"}]}
    response = db_client.post(
        "/api/v1/member-proposals",
        headers=SYSTEM,
        json={**bad, "idempotency_key": "bad", "expected_version": 0},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "member_repository_invalid"


def _seed_two_members(engine: Engine) -> None:
    with Session(engine) as session:
        for repository, state in (("Zed/zeta", "retired"), ("AlobarQuest/alpha", "member")):
            member = SdsMember(
                repository=repository,
                owner="devon",
                state=state,
                state_changed_by="human:devon",
                state_reason="seed",
            )
            session.add(member)
            session.flush()
            session.add(
                SdsMemberGrant(
                    member_id=member.id,
                    service="rollouts",
                    state="granted",
                    seeded=True,
                    set_by="human:devon",
                    reason="watched",
                )
            )
        alpha = session.scalar(select(SdsMember).where(SdsMember.repository == "AlobarQuest/alpha"))
        assert alpha is not None
        session.add(SdsMemberApp(member_id=alpha.id, name="alpha-web", health_url="https://a/h"))
        session.commit()


def test_observer_reads_every_member_with_seven_grants(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    _seed_two_members(migrated_engine)

    response = db_client.get("/api/v1/members", headers=OBSERVER)

    assert response.status_code == 200, response.text
    members = response.json()["members"]
    assert [m["repository"] for m in members] == ["AlobarQuest/alpha", "Zed/zeta"]
    assert members[1]["state"] == "retired"
    alpha = members[0]
    assert set(alpha) == {
        "repository",
        "owner",
        "state",
        "working_copy",
        "machine_local",
        "apps",
        "grants",
    }
    assert alpha["apps"] == [{"name": "alpha-web", "health_url": "https://a/h", "branch": "main"}]
    assert list(alpha["grants"]) == list(MEMBER_SERVICES)
    assert set(alpha["grants"]["rollouts"]) == {"state", "seeded", "set_by", "set_at", "reason"}
    assert alpha["grants"]["rollouts"]["state"] == "granted"
    assert alpha["grants"]["rollouts"]["seeded"] is True
    assert alpha["grants"]["backups"]["state"] == "not_assessed"


def test_the_member_views_declare_exactly_the_pinned_fields() -> None:
    assert set(MemberView.model_fields) == {
        "repository",
        "owner",
        "state",
        "working_copy",
        "machine_local",
        "apps",
        "grants",
    }
    assert set(GrantView.model_fields) == {"state", "seeded", "set_by", "set_at", "reason"}
    assert set(MembersResponse.model_fields) == {"members"}


def test_reading_members_needs_authentication(db_client: TestClient) -> None:
    assert db_client.get("/api/v1/members").status_code == 401


def test_a_nonzero_expected_version_is_refused(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/member-proposals",
        headers=SYSTEM,
        json={**BODY, "idempotency_key": "v", "expected_version": 3},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"


def test_a_health_url_round_trips_unchanged_through_confirm_and_read(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    app = {"name": "web", "health_url": "https://x.test", "branch": "main"}
    change = {**BODY["changes"][0], "apps": [app]}
    staged = db_client.post(
        "/api/v1/member-proposals",
        json={**BODY, "changes": [change], "idempotency_key": "k-url", "expected_version": 0},
        headers=SYSTEM,
    )
    assert staged.status_code == 201, staged.text
    with Session(migrated_engine) as session:
        proposal = session.get(SdsMemberProposal, uuid.UUID(staged.json()["id"]))
        assert proposal is not None
        confirm_member_proposal(
            session,
            actor=ActorContext("devon", ActorRole.HUMAN),
            proposal_id=proposal.id,
            expected_digest=read_digest(session, proposal),
        )
        session.commit()

    members = db_client.get("/api/v1/members", headers=OBSERVER).json()["members"]

    assert members[0]["apps"] == [app]
