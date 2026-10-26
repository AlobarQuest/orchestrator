"""Staging a membership proposal: SYSTEM stages, and validation refuses what a confirm could not."""

from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.api.schemas.membership import MemberProposalBody
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import (
    MEMBER_SERVICES,
    SdsMember,
    SdsMemberApp,
    SdsMemberGrant,
    SdsMemberProposal,
)
from orchestrator.services.membership.proposals import stage_member_proposal

SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)


def change(repository: str = "AlobarQuest/new-app", **overrides: Any) -> dict[str, Any]:
    return {
        "repository": repository,
        "reason": "bring it in",
        "state": "onboarding",
        "owner": "devon",
        **overrides,
    }


def body(*changes: dict[str, Any], summary: str = "enroll") -> MemberProposalBody:
    return MemberProposalBody.model_validate(
        {"summary": summary, "changes": list(changes) or [change()]}
    )


def stage(session: Session, proposal: MemberProposalBody, key: str = "k1") -> SdsMemberProposal:
    return stage_member_proposal(session, actor=SYSTEM, body=proposal, idempotency_key=key)


def add_member(session: Session, repository: str = "AlobarQuest/brain", **fields: Any) -> SdsMember:
    member = SdsMember(
        repository=repository,
        owner="devon",
        state="member",
        state_changed_by="devon",
        state_reason="seed",
        **fields,
    )
    session.add(member)
    session.flush()
    return member


def refused(session: Session, proposal: MemberProposalBody) -> str:
    with pytest.raises(DomainError) as raised:
        stage(session, proposal)
    return raised.value.code


def test_system_stages_a_proposal(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        row = stage(session, body())
        session.commit()
        assert row.state == "staged"
        assert row.staged_by == "orchestrator-system"
        assert row.decided_by is None
        assert session.scalar(select(func.count()).select_from(SdsMember)) == 0
        assert session.scalar(select(func.count()).select_from(SdsMemberGrant)) == 0


@pytest.mark.parametrize(
    "role", [ActorRole.WORKER, ActorRole.VERIFIER, ActorRole.OBSERVER, ActorRole.HUMAN]
)
def test_only_system_may_stage(migrated_engine: Engine, role: ActorRole) -> None:
    with Session(migrated_engine) as session, pytest.raises(DomainError) as raised:
        stage_member_proposal(
            session, actor=ActorContext("someone", role), body=body(), idempotency_key="k"
        )
    assert raised.value.code == "role_forbidden"


def test_same_key_same_body_replays(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        first = stage(session, body())
        second = stage(session, body())
        assert second.id == first.id
        assert session.scalar(select(func.count()).select_from(SdsMemberProposal)) == 1


def test_same_key_different_body_conflicts(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        stage(session, body())
        assert refused(session, body(summary="another")) == "idempotency_conflict"


def test_a_malformed_repository_is_refused(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        assert refused(session, body(change("not-a-repo"))) == "member_repository_invalid"


def test_repositories_equal_ignoring_case_are_refused(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        proposal = body(change("A/b"), change("a/B"))
        assert refused(session, proposal) == "member_change_duplicated"


def test_creating_a_member_needs_state_and_owner(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        proposal = body({"repository": "A/new", "reason": "r", "owner": "devon"})
        assert refused(session, proposal) == "member_creation_incomplete"
        proposal = body({"repository": "A/new", "reason": "r", "state": "member"})
        assert refused(session, proposal) == "member_creation_incomplete"


@pytest.mark.parametrize(
    "overrides",
    [
        {"reason": "  "},
        {"owner": " "},
        {"grants": {"backups": {"state": "granted", "reason": " "}}},
    ],
)
def test_blank_text_is_refused(migrated_engine: Engine, overrides: dict[str, Any]) -> None:
    with Session(migrated_engine) as session:
        assert refused(session, body(change(**overrides))) == "member_text_blank"


def test_a_blank_summary_is_refused(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        assert refused(session, body(summary=" ")) == "member_text_blank"


def test_machine_local_needs_a_working_copy(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        assert refused(session, body(change(machine_local=True))) == "member_working_copy_required"


def test_machine_local_is_satisfied_by_the_existing_working_copy(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, working_copy="/w/brain")
        stage(
            session, body({"repository": "AlobarQuest/brain", "reason": "r", "machine_local": True})
        )


def test_clearing_nothing_keeps_a_stored_machine_local_valid(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, working_copy="/w/brain", machine_local=True)
        stage(session, body({"repository": "AlobarQuest/brain", "reason": "r", "owner": "ann"}))


def test_withdrawing_a_grant_that_is_not_granted_is_refused(migrated_engine: Engine) -> None:
    withdraw = {"grants": {"backups": {"state": "withdrawn", "reason": "no"}}}
    with Session(migrated_engine) as session:
        assert (
            refused(session, body(change("AlobarQuest/new-app", **withdraw)))
            == "grant_withdrawal_invalid"
        )
        member = add_member(session)
        session.add(
            SdsMemberGrant(
                member_id=member.id,
                service="backups",
                state="granted",
                seeded=False,
                set_by="devon",
                reason="ok",
            )
        )
        session.flush()
        stage(session, body({"repository": "AlobarQuest/brain", "reason": "r", **withdraw}))


def test_an_app_name_used_by_another_member_is_refused(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        other = add_member(session, "AlobarQuest/other")
        session.add(SdsMemberApp(member_id=other.id, name="web", health_url="https://x.test/h"))
        session.flush()
        app = {"name": "web", "health_url": "https://y.test/h"}
        assert refused(session, body(change(apps=[app]))) == "member_app_name_taken"
        # The member's own app is not a clash.
        stage(
            session,
            body({"repository": "AlobarQuest/other", "reason": "r", "apps": [app]}),
            key="k2",
        )


def test_an_app_name_repeated_inside_the_proposal_is_refused(migrated_engine: Engine) -> None:
    app = {"name": "web", "health_url": "https://y.test/h"}
    with Session(migrated_engine) as session:
        proposal = body(change("A/one", apps=[app]), change("A/two", apps=[app]))
        assert refused(session, proposal) == "member_app_name_taken"


def test_a_change_differing_only_in_case_targets_the_existing_member(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        add_member(session)
        stage(session, body({"repository": "alobarquest/BRAIN", "reason": "r"}))


def test_every_service_is_a_valid_grant_key() -> None:
    grants = {s: {"state": "granted", "reason": "r"} for s in MEMBER_SERVICES}
    assert len(body(change(grants=grants)).changes[0].grants) == len(MEMBER_SERVICES)
