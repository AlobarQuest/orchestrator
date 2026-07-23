"""WS-P2.16 U3 -- the EXECUTING -> SUBMITTED submit guard.

A unit whose envelope allows ``github.pr.create`` may not submit without a PR binding recorded for
the CURRENT attempt. The negative controls plant the real defect: no binding, a stale-attempt
binding (the reason the ``binding_attempt`` column exists), and the ordering that a version
conflict is surfaced before the guard is even evaluated. A unit that cannot open a PR submits
freely -- that is the load-bearing contract for non-runner submitters.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Approval
from orchestrator.services.claims import LeaseGrant, claim_unit, reclaim_expired_claim
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import register_approved_unit
from orchestrator.services.pr_bindings import (
    arm_verification_head,
    get_pr_binding,
    upsert_pr_binding,
)
from tests.services.test_package_registration import NOW, register_test_revision

WORKER = ActorContext("worker", ActorRole.WORKER)
SYSTEM = ActorContext("lease-reaper", ActorRole.SYSTEM)
HEAD = "1111111111111111111111111111111111111111"

PR_CAPABLE = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed", "github.pr.create": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)
NO_PR = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)


def _ready_unit(session: Session, key: str, authority: AuthorityEnvelope):
    revision = register_test_revision(session)
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=authority,
        max_attempts=3,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    approval = Approval(
        subject_type="authority",
        subject_id=unit.id,
        subject_revision_or_fingerprint=unit.authority_fingerprint,
        decision="approved",
        approved_by="human-1",
        reason="approved authority",
        event_id=uuid.uuid4(),
        idempotency_key=f"authority-{unit.id}",
    )
    session.add(approval)
    session.flush()
    unit.authority_approval_id = approval.id
    unit.state = WorkUnitState.READY
    session.commit()
    return unit


def _start(session: Session, unit, key: str):
    grant = claim_unit(session, unit.id, WORKER, f"{key}-claim", expected_version=unit.version)
    assert isinstance(grant, LeaseGrant), grant
    session.refresh(unit)
    transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key=f"{key}-start",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
    )
    session.refresh(unit)
    return grant


def _submit(session: Session, unit, grant, key: str, *, expected_version: int | None = None):
    return transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.SUBMITTED,
            actor=WORKER,
            expected_version=unit.version if expected_version is None else expected_version,
            idempotency_key=f"{key}-submit",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
        after=lambda db, u: arm_verification_head(db, u, actor=WORKER),
    )


def _write_binding(session: Session, unit, grant, *, attempt: int):
    upsert_pr_binding(
        session,
        actor=WORKER,
        work_unit_id=unit.id,
        pr_number=100,
        head_sha=HEAD,
        attempt=attempt,
        lease_token=grant.lease_token,
    )


def _expire(session: Session, claim_id: uuid.UUID) -> None:
    session.execute(
        text(
            "UPDATE claims SET lease_expires_at = "
            "transaction_timestamp() - interval '1 second' WHERE id = :cid"
        ),
        {"cid": claim_id},
    )
    session.commit()


def test_pr_capable_unit_cannot_submit_without_a_binding(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "guard-no-binding", PR_CAPABLE)
    grant = _start(migrated_session, unit, "guard-no-binding")

    with pytest.raises(DomainError) as error:
        _submit(migrated_session, unit, grant, "guard-no-binding")

    assert error.value.code == "pr_binding_required"
    assert WorkUnitState(unit.state) is WorkUnitState.EXECUTING


def test_pr_capable_unit_submits_with_a_current_attempt_binding(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "guard-current", PR_CAPABLE)
    grant = _start(migrated_session, unit, "guard-current")
    _write_binding(migrated_session, unit, grant, attempt=grant.attempt)

    _submit(migrated_session, unit, grant, "guard-current")

    migrated_session.refresh(unit)
    assert WorkUnitState(unit.state) is WorkUnitState.SUBMITTED


def test_a_unit_that_cannot_open_a_pr_submits_without_a_binding(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "guard-nopr", NO_PR)
    grant = _start(migrated_session, unit, "guard-nopr")

    _submit(migrated_session, unit, grant, "guard-nopr")

    migrated_session.refresh(unit)
    assert WorkUnitState(unit.state) is WorkUnitState.SUBMITTED
    assert get_pr_binding(migrated_session, unit.id) is None


def test_version_conflict_is_surfaced_before_the_binding_guard(migrated_session: Session) -> None:
    # A stale submit (wrong version) must die on version_conflict, NOT pr_binding_required -- the
    # version check precedes the guard, so a concurrent reclaim can never make a legitimate submit
    # fail the guard.
    unit = _ready_unit(migrated_session, "guard-version", PR_CAPABLE)
    grant = _start(migrated_session, unit, "guard-version")

    with pytest.raises(DomainError) as error:
        _submit(migrated_session, unit, grant, "guard-version", expected_version=unit.version + 5)

    assert error.value.code == "version_conflict"


def test_retried_submit_replays_idempotently(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "guard-replay", PR_CAPABLE)
    grant = _start(migrated_session, unit, "guard-replay")
    _write_binding(migrated_session, unit, grant, attempt=grant.attempt)
    submit_version = unit.version

    first = _submit(migrated_session, unit, grant, "guard-replay", expected_version=submit_version)
    replay = _submit(migrated_session, unit, grant, "guard-replay", expected_version=submit_version)

    assert (first.unit_id, first.version) == (replay.unit_id, replay.version)


def test_a_stale_attempt_binding_does_not_satisfy_the_guard(migrated_session: Session) -> None:
    # THE reason the binding_attempt column exists. Attempt 1 records a binding and the unit is
    # re-attempted (reclaim -> attempt 2). The old row is still present but its binding_attempt is
    # 1, so submitting on attempt 2 without a fresh binding is refused -- the divergence alarm can
    # never arm on attempt 1's stale head.
    unit = _ready_unit(migrated_session, "guard-stale", PR_CAPABLE)
    grant1 = _start(migrated_session, unit, "guard-stale")
    _write_binding(migrated_session, unit, grant1, attempt=grant1.attempt)

    _expire(migrated_session, grant1.claim_id)
    grant2 = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, WORKER, "guard-stale-reclaim")
    assert isinstance(grant2, LeaseGrant), grant2
    assert grant2.attempt == 2
    migrated_session.refresh(unit)
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key="guard-stale-start-2",
            attempt=grant2.attempt,
            lease_token=grant2.lease_token,
        ),
    )
    migrated_session.refresh(unit)

    with pytest.raises(DomainError) as error:
        _submit(migrated_session, unit, grant2, "guard-stale-2")

    assert error.value.code == "pr_binding_required"
