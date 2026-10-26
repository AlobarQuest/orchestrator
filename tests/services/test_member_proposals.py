"""Staging a membership proposal: SYSTEM stages, and validation refuses what a confirm could not."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.api.schemas.membership import MemberProposalBody
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import (
    MEMBER_SERVICES,
    Event,
    SdsMember,
    SdsMemberApp,
    SdsMemberGrant,
    SdsMemberProposal,
)
from orchestrator.services.membership.proposals import (
    confirm_member_proposal,
    read_digest,
    stage_member_proposal,
    withdraw_member_proposal,
)

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
    return stage_member_proposal(
        session, actor=SYSTEM, body=proposal, idempotency_key=key, expected_version=0
    )


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
            session,
            actor=ActorContext("someone", role),
            body=body(),
            idempotency_key="k",
            expected_version=0,
        )
    assert raised.value.code == "role_forbidden"


def test_staging_requires_expected_version_zero(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, pytest.raises(DomainError) as raised:
        stage_member_proposal(
            session, actor=SYSTEM, body=body(), idempotency_key="k", expected_version=1
        )
    assert raised.value.code == "version_conflict"


def test_the_digest_ignores_the_key_and_the_version(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        first = stage(session, body(), key="a")
        second = stage(session, body(), key="b")
        assert first.payload_digest == second.payload_digest
        assert "idempotency_key" not in first.payload
        assert "expected_version" not in first.payload


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


HUMAN = ActorContext("devon", ActorRole.HUMAN)


def confirm(session: Session, row: SdsMemberProposal, actor: ActorContext = HUMAN) -> Any:
    return confirm_member_proposal(
        session, actor=actor, proposal_id=row.id, expected_digest=read_digest(session, row)
    )


def counts(session: Session) -> tuple[int, ...]:
    return tuple(
        session.scalar(select(func.count()).select_from(model)) or 0
        for model in (SdsMember, SdsMemberGrant, SdsMemberApp, Event)
    )


def grant(member: SdsMember, service: str, state: str = "granted") -> SdsMemberGrant:
    return SdsMemberGrant(
        member_id=member.id, service=service, state=state, seeded=False, set_by="devon", reason="ok"
    )


def test_human_confirmation_applies_every_change_and_records_who(migrated_engine: Engine) -> None:
    app = {"name": "web", "health_url": "https://x.test/h"}
    with Session(migrated_engine) as session:
        existing = add_member(session, "AlobarQuest/brain")
        session.add(grant(existing, "backups"))
        row = stage(
            session,
            body(
                change(
                    apps=[app],
                    grants={"backups": {"state": "granted", "reason": "nightly dump"}},
                ),
                {"repository": "AlobarQuest/brain", "reason": "new owner", "owner": "ann"},
            ),
        )
        confirmed = confirm(session, row)
        session.commit()

        assert confirmed.state == "confirmed" and confirmed.decided_by == "devon"
        created = session.scalars(
            select(SdsMember).where(SdsMember.repository == "AlobarQuest/new-app")
        ).one()
        grants = {
            g.service: g
            for g in session.scalars(select(SdsMemberGrant))
            if g.member_id == created.id
        }
        assert set(grants) == set(MEMBER_SERVICES)
        assert grants["backups"].state == "granted" and grants["backups"].set_by == "devon"
        assert grants["monitoring"].state == "not_assessed"
        assert grants["monitoring"].reason == "not assessed at creation"
        assert grants["monitoring"].set_by == "devon" and grants["monitoring"].seeded is False
        assert session.scalars(select(SdsMemberApp)).one().name == "web"
        session.refresh(existing)
        assert existing.owner == "ann"
        events = session.scalars(select(Event).where(Event.subject_type == "sds_member")).all()
        assert sorted(e.action for e in events) == ["member.changed", "member.created"]
        assert {e.actor_id for e in events} == {"devon"}
        created_event = next(e for e in events if e.action == "member.created")
        assert created_event.subject_id == created.id


def test_seeded_flag_is_recorded_on_created_grants(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        proposal = MemberProposalBody.model_validate(
            {"summary": "seed", "seeded": True, "changes": [change()]}
        )
        confirm(session, stage(session, proposal))
        assert {g.seeded for g in session.scalars(select(SdsMemberGrant))} == {True}


def test_system_cannot_confirm(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        row = stage(session, body())
        with pytest.raises(DomainError) as raised:
            confirm(session, row, SYSTEM)
        assert raised.value.code == "human_actor_required"
        assert counts(session)[0] == 0


def test_confirming_twice_is_refused(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        row = stage(session, body())
        confirm(session, row)
        with pytest.raises(DomainError) as raised:
            confirm(session, row)
        assert raised.value.code == "member_proposal_not_staged"
        assert counts(session)[0] == 1


def test_a_changed_digest_is_refused(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        row = stage(session, body())
        with pytest.raises(DomainError) as raised:
            confirm_member_proposal(
                session, actor=HUMAN, proposal_id=row.id, expected_digest="0" * 64
            )
        assert raised.value.code == "member_proposal_changed"
        assert counts(session)[0] == 0


def test_a_missing_proposal_is_not_found(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, pytest.raises(DomainError) as raised:
        withdraw_member_proposal(session, actor=HUMAN, proposal_id=uuid.uuid4(), reason="r")
    assert raised.value.code == "member_proposal_not_found"


def test_confirmation_revalidates_and_applies_nothing_on_failure(migrated_engine: Engine) -> None:
    withdrawn = {"backups": {"state": "withdrawn", "reason": "retire it"}}
    with Session(migrated_engine) as session:
        brain = add_member(session, "AlobarQuest/brain")
        other = add_member(session, "AlobarQuest/other")
        session.add(grant(brain, "backups"))
        session.flush()
        first = stage(
            session,
            body(
                {
                    "repository": "AlobarQuest/brain",
                    "reason": "r",
                    "grants": {"backups": {"state": "not_applicable", "reason": "moved"}},
                }
            ),
            key="a",
        )
        second = stage(
            session,
            body(
                {"repository": "AlobarQuest/other", "reason": "r", "owner": "ann"},
                {"repository": "AlobarQuest/brain", "reason": "r", "grants": withdrawn},
            ),
            key="b",
        )
        confirm(session, first)
        session.commit()
        before = counts(session)

        with pytest.raises(DomainError) as raised:
            confirm(session, second)
        session.rollback()

        assert raised.value.code == "grant_withdrawal_invalid"
        assert counts(session) == before
        session.refresh(other)
        assert other.owner == "devon"
        assert session.get(SdsMemberProposal, second.id).state == "staged"  # type: ignore[union-attr]


def test_withdrawal_needs_a_reason_and_writes_nothing_to_the_record(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session:
        row = stage(session, body())
        with pytest.raises(DomainError) as blank:
            withdraw_member_proposal(session, actor=HUMAN, proposal_id=row.id, reason=" ")
        assert blank.value.code == "member_text_blank"
        with pytest.raises(DomainError) as system:
            withdraw_member_proposal(session, actor=SYSTEM, proposal_id=row.id, reason="r")
        assert system.value.code == "human_actor_required"

        withdrawn = withdraw_member_proposal(
            session, actor=HUMAN, proposal_id=row.id, reason="not now"
        )

        assert withdrawn.state == "withdrawn" and withdrawn.decided_by == "devon"
        assert withdrawn.decision_reason == "not now"
        assert counts(session) == (0, 0, 0, 0)
        for decide in (
            lambda: confirm(session, row),
            lambda: withdraw_member_proposal(
                session, actor=HUMAN, proposal_id=row.id, reason="again"
            ),
        ):
            with pytest.raises(DomainError) as again:
                decide()
            assert again.value.code == "member_proposal_not_staged"


def test_a_record_that_moved_since_the_page_was_read_is_refused(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session:
        add_member(session, "AlobarQuest/brain")
        read = stage(
            session, body({"repository": "AlobarQuest/brain", "reason": "r", "owner": "ann"}), "a"
        )
        moving = stage(
            session, body({"repository": "AlobarQuest/brain", "reason": "r", "owner": "bob"}), "b"
        )
        digest = read_digest(session, read)
        confirm(session, moving)
        before = counts(session)

        with pytest.raises(DomainError) as raised:
            confirm_member_proposal(
                session, actor=HUMAN, proposal_id=read.id, expected_digest=digest
            )

        assert raised.value.code == "member_proposal_changed"
        assert counts(session) == before
        assert session.get(SdsMemberProposal, read.id).state == "staged"  # type: ignore[union-attr]


def test_confirmations_of_different_proposals_take_turns(migrated_engine: Engine) -> None:
    """B, read before A decided, waits for A's transaction; once A commits B finds the record moved
    and is refused cleanly, instead of overwriting A or meeting a unique index."""
    with Session(migrated_engine) as setup:
        brain = add_member(setup, "AlobarQuest/brain")
        setup.add(grant(brain, "backups"))
        a = stage(
            setup,
            body(
                {
                    "repository": "AlobarQuest/brain",
                    "reason": "r",
                    "grants": {"backups": {"state": "not_applicable", "reason": "moved"}},
                }
            ),
            "a",
        )
        b = stage(
            setup,
            body(
                {
                    "repository": "AlobarQuest/brain",
                    "reason": "r",
                    "grants": {"backups": {"state": "withdrawn", "reason": "retire"}},
                }
            ),
            "b",
        )
        a_id, b_id, b_digest = a.id, b.id, read_digest(setup, b)
        setup.commit()

    def confirm_b() -> str:
        with Session(migrated_engine) as second:
            try:
                confirm_member_proposal(
                    second, actor=HUMAN, proposal_id=b_id, expected_digest=b_digest
                )
            except DomainError as error:
                second.rollback()
                return error.code
            second.commit()
            return "applied"

    with Session(migrated_engine) as first, ThreadPoolExecutor(max_workers=1) as pool:
        confirm_member_proposal(
            first,
            actor=HUMAN,
            proposal_id=a_id,
            expected_digest=read_digest(first, first.get(SdsMemberProposal, a_id)),  # type: ignore[arg-type]
        )
        waiting = pool.submit(confirm_b)
        with pytest.raises(FutureTimeout):
            waiting.result(timeout=1.0)
        first.commit()
        assert waiting.result(timeout=10) == "member_proposal_changed"

    with Session(migrated_engine) as check:
        assert check.scalars(select(SdsMemberGrant.state)).one() == "not_applicable"
        assert check.get(SdsMemberProposal, b_id).state == "staged"  # type: ignore[union-attr]
