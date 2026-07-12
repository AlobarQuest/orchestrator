"""WS-P2.1 Task 9: lease-expired evidence recovery (AC-004).

THE hazard this task exists to avoid. Recovery MUST bypass `_store_evidence` -- its
`_validate_attempt` rejects a SYSTEM actor, a released claim, and an expired lease, which is the
entire scenario. But `_store_evidence`'s `evidence_already_exists` check is the ONLY code
preventing a second supersession head, and two heads make `_terminal` raise: the AC can then
never be adjudicated, no further evidence can be written, and `evidence` is append-only so the
row can never be repaired. One naive recovery call would wedge the unit so it can NEVER complete.

So recovery resolves the current head under the locks and SUPERSEDES it. It never forks the chain.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Claim, Evidence, WorkUnit
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.evidence import append_evidence, current_evidence, recover_evidence
from orchestrator.services.lifecycle import ActorContext
from tests.services.test_claims import worker

SYSTEM = ActorContext("system", ActorRole.SYSTEM)


def recovery_kwargs(unit: WorkUnit, attempt: int, key: str) -> dict[str, Any]:
    return {
        "work_package_revision_id": unit.work_package_revision_id,
        "work_unit_id": unit.id,
        "ac_id": "ac-1",
        "attempt": attempt,
        "actor": SYSTEM,
        "evidence_type": "test",
        "stable_ref": "artifact://recovered-result",
        "payload": {"exit_code": 0},
        "source_revision": "abc123",
        "idempotency_key": key,
    }


def expire(session: Session, claim_id: uuid.UUID) -> None:
    """Age the lease past its expiry. Fixture setup standing in for elapsed wall clock."""
    claim = session.get(Claim, claim_id)
    assert claim is not None
    claim.lease_expires_at = claim.acquired_at
    session.commit()


def expired_claim(session: Session, unit: WorkUnit) -> LeaseGrant:
    grant = claim_unit(session, unit.id, worker(), f"claim-{unit.id}")
    assert isinstance(grant, LeaseGrant)
    expire(session, grant.claim_id)
    session.expire_all()
    return grant


def heads(session: Session, unit: WorkUnit) -> list[Evidence]:
    return list(
        session.scalars(
            select(Evidence).where(
                Evidence.work_unit_id == unit.id,
                Evidence.ac_id == "ac-1",
                Evidence.supersedes_evidence_id.is_(None),
            )
        )
    )


# --- the wedge guard -----------------------------------------------------------------------


def test_recovering_twice_never_forks_the_chain(migrated_session: Session, ready_unit) -> None:
    """Two heads would wedge the unit forever. The second recovery must SUPERSEDE the first."""
    grant = expired_claim(migrated_session, ready_unit)

    first = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-1")
    )
    second = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-2")
    )

    assert isinstance(first, Evidence)
    assert isinstance(second, Evidence)
    assert second.supersedes_evidence_id == first.id
    assert len(heads(migrated_session, ready_unit)) == 1
    # _terminal still resolves -- the unit is NOT wedged.
    terminal = current_evidence(
        migrated_session, ready_unit.work_package_revision_id, ready_unit.id, "ac-1"
    )
    assert isinstance(terminal, Evidence)
    assert terminal.id == second.id


def test_recovery_supersedes_an_existing_head(migrated_session: Session, ready_unit) -> None:
    """The worker had already submitted partial evidence before its lease lapsed."""
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-head")
    assert isinstance(grant, LeaseGrant)
    existing = append_evidence(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        attempt=grant.attempt,
        actor=worker(),
        lease_token=grant.lease_token,
        evidence_type="test",
        stable_ref="artifact://partial",
        payload=None,
        source_revision="abc123",
        idempotency_key="evidence-head",
    )
    assert isinstance(existing, Evidence)
    expire(migrated_session, grant.claim_id)
    migrated_session.expire_all()

    recovered = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-head")
    )

    assert isinstance(recovered, Evidence)
    assert recovered.supersedes_evidence_id == existing.id
    assert len(heads(migrated_session, ready_unit)) == 1
    terminal = current_evidence(
        migrated_session, ready_unit.work_package_revision_id, ready_unit.id, "ac-1"
    )
    assert terminal is not None and terminal.id == recovered.id


def test_a_second_head_is_structurally_impossible(migrated_session: Session, ready_unit) -> None:
    """Defense in depth: even a direct INSERT that bypasses every service cannot fork the chain."""
    grant = expired_claim(migrated_session, ready_unit)
    first = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-x")
    )
    assert isinstance(first, Evidence)

    migrated_session.add(
        Evidence(
            id=uuid.uuid4(),
            work_package_revision_id=ready_unit.work_package_revision_id,
            work_unit_id=ready_unit.id,
            ac_id="ac-1",
            attempt=grant.attempt,
            evidence_type="test",
            stable_ref="artifact://second-head",
            payload=None,
            source_revision="abc123",
            recorded_by="system",
            event_id=uuid.uuid4(),
            idempotency_key="second-head",
            supersedes_evidence_id=None,
        )
    )
    with pytest.raises(IntegrityError) as error:
        migrated_session.flush()

    assert "uq_evidence_unsuperseded_head" in str(error.value)
    migrated_session.rollback()


def test_a_duplicate_delivery_replays(migrated_session: Session, ready_unit) -> None:
    grant = expired_claim(migrated_session, ready_unit)

    first = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-r")
    )
    replay = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-r")
    )

    assert isinstance(first, Evidence)
    assert isinstance(replay, Evidence)
    assert replay.id == first.id
    assert (
        len(
            list(
                migrated_session.scalars(
                    select(Evidence).where(Evidence.work_unit_id == ready_unit.id)
                )
            )
        )
        == 1
    )


# --- preconditions -------------------------------------------------------------------------


def test_recovery_refuses_a_live_lease(migrated_session: Session, ready_unit) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-live")
    assert isinstance(grant, LeaseGrant)

    result = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-live")
    )

    assert isinstance(result, DomainError)
    assert result.code == "lease_not_expired"


def test_recovery_refuses_the_expired_worker_itself(migrated_session: Session, ready_unit) -> None:
    """Never the expired worker. Letting it self-serve past its lease would re-open exactly the
    hole the lease exists to close."""
    grant = expired_claim(migrated_session, ready_unit)
    command: dict[str, Any] = recovery_kwargs(ready_unit, grant.attempt, "rec-worker") | {
        "actor": worker()
    }

    result = recover_evidence(migrated_session, **command)

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_recovery_refuses_a_completed_unit(migrated_session: Session, ready_unit) -> None:
    grant = expired_claim(migrated_session, ready_unit)
    ready_unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()

    result = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-completed")
    )

    assert isinstance(result, DomainError)
    assert result.code == "recovery_not_allowed"


# --- what AC-004 actually promises -----------------------------------------------------------


def test_recovery_releases_the_claim_and_system_fails_without_minting_a_new_attempt(
    migrated_session: Session, ready_unit
) -> None:
    """The AC's real scenario: the lease lapsed just before submit and nothing reclaimed it.

    Recovery is the releaser -- and it must NOT mint a new attempt, or it would silently spend
    the unit's attempt budget on a run that never happened.
    """
    grant = expired_claim(migrated_session, ready_unit)

    recovered = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-release")
    )

    assert isinstance(recovered, Evidence)
    migrated_session.expire_all()
    claim = migrated_session.get(Claim, grant.claim_id)
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    assert claim is not None and claim.released_at is not None
    assert claim.terminal_reason == "lease_expired"
    assert unit is not None and unit.state == WorkUnitState.FAILED
    assert unit.attempt_count == 1  # NO new attempt was minted
    assert recovered.attempt == grant.attempt
    assert recovered.payload is not None
    assert recovered.payload["recovery"]["reason"] == "recovered_from_expired_lease"
    assert recovered.payload["recovery"]["claim_id"] == str(grant.claim_id)


def test_recovery_admits_after_a_reclaim_already_released_the_claim(
    migrated_session: Session, ready_unit
) -> None:
    """Reclaim and recovery are disjoint by precondition, and neither double-releases."""
    grant = expired_claim(migrated_session, ready_unit)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    claim.released_at = claim.lease_expires_at
    claim.terminal_reason = "lease_expired"
    ready_unit.state = WorkUnitState.FAILED
    migrated_session.commit()

    recovered = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-reclaimed")
    )

    assert isinstance(recovered, Evidence)
    assert len(heads(migrated_session, ready_unit)) == 1
