"""A withdrawn grant stops new factory work at the claim, and on every way back to a claim.

`claim_unit` refuses before anything writes. Reclaim and release reach the same term after the
claim is released and the unit has failed, so they park the unit `failed`; requeue refuses, and
the dead-letter view says what would have to change first. A rotation unit answers to its own
check, and a run already in flight keeps its lease.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, SdsMember, SdsMemberGrant, WorkUnit
from orchestrator.services.lifecycle.claims import (
    LeaseGrant,
    authorize_retry,
    claim_unit,
    reclaim_expired_claim,
    release_expired_claim,
    renew_claim,
    requeue_unit,
)
from orchestrator.services.lifecycle.rotation_claims import NON_HOSTED_CONSTRAINT
from orchestrator.services.reporting.dead_letter import dead_letter, recovery_action
from tests.services.test_budget import _cost_event
from tests.services.test_reclaim import expire
from tests.services.test_rotation_claims import EXECUTOR_ACTOR, _claim, _untouched, seed_unit

REPOSITORY = "AlobarQuest/some-target"
NOT_GRANTED = "target_repository_not_granted"
WORKER = ActorContext("worker-1", ActorRole.WORKER)
SYSTEM = ActorContext("system", ActorRole.SYSTEM)


def _member(
    session: Session,
    *,
    grants: dict[str, str],
    state: str = "member",
    repository: str = REPOSITORY,
) -> SdsMember:
    member = SdsMember(
        repository=repository,
        owner="devon",
        state=state,
        state_changed_by="human:devon",
        state_reason="seed",
    )
    session.add(member)
    session.flush()
    for service, grant in grants.items():
        session.add(
            SdsMemberGrant(
                member_id=member.id,
                service=service,
                state=grant,
                seeded=False,
                set_by="human:devon",
                reason="seed",
            )
        )
    session.commit()
    return member


def _withdraw(session: Session, member: SdsMember, service: str = "app_changes") -> None:
    grant = session.scalar(
        select(SdsMemberGrant).where(
            SdsMemberGrant.member_id == member.id, SdsMemberGrant.service == service
        )
    )
    assert grant is not None
    grant.state = "withdrawn"
    session.commit()


def _factory_unit(
    session: Session,
    key: str,
    *,
    repository: str | None = REPOSITORY,
    change_class: str | None = None,
    max_attempts: int = 5,
) -> WorkUnit:
    constraints = {"target_repository": repository} if repository else {}
    unit = seed_unit(
        session,
        key,
        profile="software-delivery",
        fields={},
        capability="repo.edit",
        constraints=constraints,
        reach=["source_repository"],
        max_attempts=max_attempts,
    )
    if change_class is not None:
        unit.authority = {**unit.authority, "change_class": change_class}
        session.commit()
    return unit


def _claimed(session: Session, unit: WorkUnit, key: str) -> LeaseGrant:
    grant = claim_unit(session, unit.id, WORKER, key)
    assert isinstance(grant, LeaseGrant), grant
    return grant


def _lapse(session: Session, unit: WorkUnit, key: str) -> LeaseGrant:
    grant = _claimed(session, unit, key)
    expire(session, grant.claim_id)
    return grant


def _reread(session: Session, unit: WorkUnit) -> WorkUnit:
    """The committed row, through a session that shares nothing with the one that wrote it."""
    with Session(session.get_bind()) as other:
        row = other.get_one(WorkUnit, unit.id)
        other.expunge(row)
    return row


def _events(session: Session, unit: WorkUnit) -> int:
    count = session.scalar(select(func.count()).where(Event.subject_id == unit.id))
    assert count is not None
    return count


def test_a_withdrawn_grant_refuses_the_claim_and_writes_nothing(
    migrated_session: Session,
) -> None:
    member = _member(migrated_session, grants={"app_changes": "granted"})
    unit = _factory_unit(migrated_session, "withdrawn")
    _withdraw(migrated_session, member)
    before = _events(migrated_session, unit)

    refused = claim_unit(migrated_session, unit.id, WORKER, "withdrawn-1")

    assert isinstance(refused, DomainError)
    assert refused.code == NOT_GRANTED
    _untouched(migrated_session, unit)
    assert _events(migrated_session, unit) == before


def test_the_refusal_comes_before_the_budget_halt_writes(migrated_session: Session) -> None:
    unit = _factory_unit(migrated_session, "withdrawn-spent")
    _cost_event(migrated_session, unit.id, llm_calls=10)
    migrated_session.commit()

    refused = claim_unit(migrated_session, unit.id, WORKER, "withdrawn-spent-1")

    assert isinstance(refused, DomainError)
    assert refused.code == NOT_GRANTED
    _untouched(migrated_session, unit)


def test_a_replayed_claim_after_a_refusal_answers_the_refusal(migrated_session: Session) -> None:
    unit = _factory_unit(migrated_session, "replayed")

    first = claim_unit(migrated_session, unit.id, WORKER, "replayed-1")
    second = claim_unit(migrated_session, unit.id, WORKER, "replayed-1")

    assert isinstance(first, DomainError)
    assert isinstance(second, DomainError)
    assert (first.code, second.code) == (NOT_GRANTED, NOT_GRANTED)


def test_a_held_claim_replayed_after_withdrawal_returns_its_grant(
    migrated_session: Session,
) -> None:
    member = _member(migrated_session, grants={"app_changes": "granted"})
    unit = _factory_unit(migrated_session, "held-replay")
    first = _claimed(migrated_session, unit, "held-replay-1")
    _withdraw(migrated_session, member)

    replayed = claim_unit(migrated_session, unit.id, WORKER, "held-replay-1")

    assert isinstance(replayed, LeaseGrant), replayed
    assert (replayed.claim_id, replayed.attempt) == (first.claim_id, first.attempt)


def test_authorize_retry_refuses_a_withdrawn_repository(migrated_session: Session) -> None:
    member = _member(migrated_session, grants={"app_changes": "granted"})
    unit = _factory_unit(migrated_session, "retry-withdrawn", max_attempts=1)
    _lapse(migrated_session, unit, "retry-withdrawn-1")
    release_expired_claim(migrated_session, unit.id, SYSTEM, "retry-withdrawn-2")
    assert _reread(migrated_session, unit).state == WorkUnitState.FAILED
    _withdraw(migrated_session, member)
    human = ActorContext("human-1", ActorRole.HUMAN)

    refused = authorize_retry(
        migrated_session,
        unit.id,
        human,
        new_max_attempts=3,
        reason="retry",
        idempotency_key="retry-withdrawn-3",
    )

    assert isinstance(refused, DomainError)
    assert refused.code == NOT_GRANTED
    assert _reread(migrated_session, unit).max_attempts == 1


def test_the_dead_letter_view_reads_the_member_record_once(
    migrated_session: Session,
) -> None:
    _member(migrated_session, grants={"app_changes": "granted"})
    for number in range(3):
        unit = _factory_unit(migrated_session, f"report-{number}")
        unit.state = WorkUnitState.FAILED
        migrated_session.commit()
    reads: list[str] = []

    def count(_conn, _cursor, statement, *_rest) -> None:
        if "FROM sds_members" in statement:
            reads.append(statement)

    engine = migrated_session.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        entries = dead_letter(
            migrated_session,
            failure_signature_threshold=3,
            stalled_approval_seconds=3600,
            stalled_verification_seconds=3600,
        )
    finally:
        event.remove(engine, "before_cursor_execute", count)

    assert len([e for e in entries if e.source == "work_unit"]) == 3
    assert len(reads) == 1


def test_a_unit_with_no_target_repository_claims(migrated_session: Session) -> None:
    unit = _factory_unit(migrated_session, "no-target", repository=None)

    assert isinstance(claim_unit(migrated_session, unit.id, WORKER, "no-target-1"), LeaseGrant)


def test_a_bump_with_dependency_updates_but_no_app_changes_claims(
    migrated_session: Session,
) -> None:
    _member(migrated_session, grants={"dependency_updates": "granted"})
    unit = _factory_unit(migrated_session, "bump", change_class="dependency-update")

    assert isinstance(claim_unit(migrated_session, unit.id, WORKER, "bump-1"), LeaseGrant)


def test_app_work_needs_the_app_changes_grant(migrated_session: Session) -> None:
    _member(migrated_session, grants={"dependency_updates": "granted"})
    unit = _factory_unit(migrated_session, "app-work", change_class="software-delivery")

    refused = claim_unit(migrated_session, unit.id, WORKER, "app-work-1")

    assert isinstance(refused, DomainError)
    assert refused.code == NOT_GRANTED


def test_a_retired_member_refuses(migrated_session: Session) -> None:
    _member(migrated_session, grants={"app_changes": "granted"}, state="retired")
    unit = _factory_unit(migrated_session, "retired")

    refused = claim_unit(migrated_session, unit.id, WORKER, "retired-1")

    assert isinstance(refused, DomainError)
    assert refused.code == NOT_GRANTED


def test_renewal_is_not_gated_by_the_grant(migrated_session: Session) -> None:
    member = _member(migrated_session, grants={"app_changes": "granted"})
    unit = _factory_unit(migrated_session, "renewal")
    grant = _claimed(migrated_session, unit, "renewal-1")
    _withdraw(migrated_session, member)

    renewed = renew_claim(migrated_session, unit.id, WORKER, grant.attempt, grant.lease_token)

    assert isinstance(renewed, LeaseGrant), renewed


def test_reclaim_parks_a_withdrawn_unit_failed(migrated_session: Session) -> None:
    member = _member(migrated_session, grants={"app_changes": "granted"})
    unit = _factory_unit(migrated_session, "reclaim")
    lapsed = _lapse(migrated_session, unit, "reclaim-1")
    _withdraw(migrated_session, member)

    refused = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, WORKER, "reclaim-2")

    assert isinstance(refused, DomainError)
    assert refused.code == NOT_GRANTED
    stored = _reread(migrated_session, unit)
    assert (stored.state, stored.attempt_count) == (WorkUnitState.FAILED, lapsed.attempt)


def test_a_replayed_reclaim_answers_the_refusal(migrated_session: Session) -> None:
    member = _member(migrated_session, grants={"app_changes": "granted"})
    unit = _factory_unit(migrated_session, "reclaim-replay")
    _lapse(migrated_session, unit, "reclaim-replay-1")
    _withdraw(migrated_session, member)

    reclaim_expired_claim(migrated_session, unit.id, SYSTEM, WORKER, "reclaim-replay-2")
    replayed = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, WORKER, "reclaim-replay-2")

    assert isinstance(replayed, DomainError)
    assert (replayed.code, replayed.recovery) == (NOT_GRANTED, "restore_grant")


def test_release_parks_a_withdrawn_unit_failed_and_replays_the_refusal(
    migrated_session: Session,
) -> None:
    member = _member(migrated_session, grants={"app_changes": "granted"})
    unit = _factory_unit(migrated_session, "release")
    _lapse(migrated_session, unit, "release-1")
    _withdraw(migrated_session, member)

    refused = release_expired_claim(migrated_session, unit.id, SYSTEM, "release-2")
    replayed = release_expired_claim(migrated_session, unit.id, SYSTEM, "release-2")

    assert isinstance(refused, DomainError)
    assert isinstance(replayed, DomainError)
    assert (refused.code, replayed.code) == (NOT_GRANTED, NOT_GRANTED)
    assert _reread(migrated_session, unit).state == WorkUnitState.FAILED


def test_a_rotation_units_lapsed_claim_never_meets_the_grant_term(
    migrated_session: Session,
) -> None:
    """The envelope names a repository nothing grants, so only the rotation skip can pass it."""
    unit = seed_unit(
        migrated_session,
        "rotation",
        constraints={"target_repository": REPOSITORY, NON_HOSTED_CONSTRAINT: True},
    )
    grant = _claim(migrated_session, unit, EXECUTOR_ACTOR, "rotation-1")
    assert isinstance(grant, LeaseGrant), grant
    expire(migrated_session, grant.claim_id)

    released = release_expired_claim(migrated_session, unit.id, SYSTEM, "rotation-2")

    assert isinstance(released, WorkUnit), released
    assert _reread(migrated_session, unit).state == WorkUnitState.READY


def test_requeue_advice_omits_a_withdrawn_repository(migrated_session: Session) -> None:
    """The advice is checked against the route: the requeue it names refuses until the grant is
    back, and is accepted afterwards."""
    member = _member(migrated_session, grants={"app_changes": "granted"})
    unit = _factory_unit(migrated_session, "advice")
    _lapse(migrated_session, unit, "advice-1")
    _withdraw(migrated_session, member)
    release_expired_claim(migrated_session, unit.id, SYSTEM, "advice-2")

    assert recovery_action(migrated_session, unit) == (
        "Cancel this unit, or have a person restore its repository's member grant, then have "
        "the system requeue it"
    )
    refused = requeue_unit(migrated_session, unit.id, SYSTEM, reason="retry", idempotency_key="a3")
    assert isinstance(refused, DomainError)
    assert refused.code == NOT_GRANTED

    grant = migrated_session.scalar(
        select(SdsMemberGrant).where(SdsMemberGrant.member_id == member.id)
    )
    assert grant is not None
    grant.state = "granted"
    migrated_session.commit()

    assert recovery_action(migrated_session, unit) == (
        "Cancel this unit, or have the system requeue it"
    )
    requeued = requeue_unit(migrated_session, unit.id, SYSTEM, reason="retry", idempotency_key="a3")
    assert isinstance(requeued, WorkUnit), requeued


@pytest.mark.parametrize("repository", [REPOSITORY.upper(), REPOSITORY.lower()])
def test_the_repository_compares_case_folded(migrated_session: Session, repository: str) -> None:
    _member(migrated_session, grants={"app_changes": "granted"})
    unit = _factory_unit(migrated_session, "folded", repository=repository)

    assert isinstance(claim_unit(migrated_session, unit.id, WORKER, "folded-1"), LeaseGrant)
