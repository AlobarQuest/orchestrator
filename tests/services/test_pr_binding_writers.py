"""WS-P2.1 Task 16a: the PR binding's PRODUCTION writers.

Before this, `upsert_pr_binding` and `record_verification_read_head` had no caller outside their
own unit tests. Nothing in production ever wrote a `unit_pr_binding` row -- so the reconciliation
runner, which discovers PRs to poll FROM those rows, never pushed a github_pr observation for any
unit, and AC-001/AC-002 detection was never reached. Not blind: silent. The `skipped_correlations`
counter that is supposed to make fail-open visible never even incremented.

These tests pin the two writers that close that hole:

* the WORKER reports its PR through a public route, proving it holds the unit's claim;
* SUBMIT arms the divergence alarm on the head being handed over -- write-once within the
  verification cycle, re-armed by the next attempt.
"""

import uuid

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.services.claims import claim_unit
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.pr_bindings import (
    arm_verification_head,
    get_pr_binding,
    upsert_pr_binding,
)
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("orchestrator", ActorRole.SYSTEM)
WORKER = ActorContext("worker", ActorRole.WORKER)
OTHER_WORKER = ActorContext("worker-2", ActorRole.WORKER)
VERIFIER = ActorContext("verifier", ActorRole.VERIFIER)

HEAD_1 = "1111111111111111111111111111111111111111"
HEAD_2 = "2222222222222222222222222222222222222222"


def _claimed_unit(session: Session, key: str):
    """A unit a worker actually holds -- state, claim, lease and all."""
    unit = register_unit(session, key)
    unit.state = WorkUnitState.READY
    session.flush()
    grant = claim_unit(session, unit.id, WORKER, f"{key}-claim", expected_version=unit.version)
    assert not isinstance(grant, DomainError), grant
    session.refresh(unit)
    return unit, grant


def _submit(session: Session, unit, grant, *, key: str, actor: ActorContext = WORKER) -> None:
    transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=actor,
            expected_version=unit.version,
            idempotency_key=f"{key}-start",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
    )
    session.refresh(unit)
    transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.SUBMITTED,
            actor=actor,
            expected_version=unit.version,
            idempotency_key=f"{key}-submit",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
        after=lambda db, u: arm_verification_head(db, u, actor=actor),
    )
    session.refresh(unit)


def test_a_worker_holding_the_claim_may_report_its_pr(migrated_session: Session) -> None:
    """Asserts the row PERSISTS, not merely that the call returned an object.

    The first version of this writer only flushed. The response looked correct -- the ORM hands
    back the instance it is holding -- while the row was discarded when the session closed, so
    the binding table stayed empty and every alarm downstream of it was dead. An in-session
    assertion cannot see that. Expiring the identity map and re-reading can.
    """
    unit, grant = _claimed_unit(migrated_session, "binding-worker")

    upsert_pr_binding(
        migrated_session,
        actor=WORKER,
        work_unit_id=unit.id,
        pr_number=42,
        head_sha=HEAD_1,
        attempt=grant.attempt,
        lease_token=grant.lease_token,
    )

    migrated_session.expire_all()
    binding = get_pr_binding(migrated_session, unit.id)
    assert binding is not None
    assert (binding.pr_number, binding.head_sha) == (42, HEAD_1)
    assert binding.verification_read_head_sha is None


def test_a_worker_without_the_claim_may_NOT_write_the_expectation(
    migrated_session: Session,
) -> None:
    """The expectation is the ONLY thing divergence is measured against. A worker that could
    overwrite a unit it does not hold could silently disarm the alarm on that unit."""
    unit, grant = _claimed_unit(migrated_session, "binding-thief")

    with pytest.raises(DomainError) as error:
        upsert_pr_binding(
            migrated_session,
            actor=OTHER_WORKER,
            work_unit_id=unit.id,
            pr_number=42,
            head_sha=HEAD_1,
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        )
    assert error.value.code == "claim_not_owned"


def test_a_worker_presenting_no_lease_is_refused(migrated_session: Session) -> None:
    unit, _grant = _claimed_unit(migrated_session, "binding-noleash")

    with pytest.raises(DomainError) as error:
        upsert_pr_binding(
            migrated_session, actor=WORKER, work_unit_id=unit.id, pr_number=42, head_sha=HEAD_1
        )
    assert error.value.code == "claim_not_owned"


def test_a_rebase_moves_the_head_and_raises_nothing(migrated_session: Session) -> None:
    unit, grant = _claimed_unit(migrated_session, "binding-rebase")
    for sha in (HEAD_1, HEAD_2):
        upsert_pr_binding(
            migrated_session,
            actor=WORKER,
            work_unit_id=unit.id,
            pr_number=42,
            head_sha=sha,
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        )

    binding = get_pr_binding(migrated_session, unit.id)
    assert binding is not None
    assert binding.head_sha == HEAD_2
    assert binding.verification_read_head_sha is None, "iterating must not arm the alarm"


def test_SUBMIT_arms_the_alarm_on_the_head_being_handed_over(migrated_session: Session) -> None:
    """THE test for the writer that was missing. Arming is a side effect of submitting, in the
    same transaction -- not something an operator has to remember to do."""
    unit, grant = _claimed_unit(migrated_session, "binding-submit")
    upsert_pr_binding(
        migrated_session,
        actor=WORKER,
        work_unit_id=unit.id,
        pr_number=42,
        head_sha=HEAD_1,
        attempt=grant.attempt,
        lease_token=grant.lease_token,
    )

    _submit(migrated_session, unit, grant, key="binding-submit")

    binding = get_pr_binding(migrated_session, unit.id)
    assert binding is not None
    assert binding.verification_read_head_sha == HEAD_1
    assert binding.verification_read_attempt == unit.attempt_count


def test_a_unit_with_no_pull_request_can_still_submit(migrated_session: Session) -> None:
    """Not every work unit opens a PR. Arming must be a no-op for those, not a wall."""
    unit, grant = _claimed_unit(migrated_session, "binding-nopr")

    _submit(migrated_session, unit, grant, key="binding-nopr")

    assert WorkUnitState(unit.state) is WorkUnitState.SUBMITTED
    assert get_pr_binding(migrated_session, unit.id) is None


def test_a_push_after_submit_cannot_move_the_armed_head(migrated_session: Session) -> None:
    """The anti-tamper property. Once the head is handed over for adjudication, a later push
    moves `head_sha` -- and leaves the armed head alone, so the divergence stays visible."""
    unit, grant = _claimed_unit(migrated_session, "binding-tamper")
    upsert_pr_binding(
        migrated_session,
        actor=WORKER,
        work_unit_id=unit.id,
        pr_number=42,
        head_sha=HEAD_1,
        attempt=grant.attempt,
        lease_token=grant.lease_token,
    )
    _submit(migrated_session, unit, grant, key="binding-tamper")

    upsert_pr_binding(
        migrated_session,
        actor=SYSTEM,
        work_unit_id=unit.id,
        pr_number=42,
        head_sha=HEAD_2,
    )

    binding = get_pr_binding(migrated_session, unit.id)
    assert binding is not None
    assert binding.head_sha == HEAD_2, "reality moved"
    assert binding.verification_read_head_sha == HEAD_1, "the expectation did not"


def test_the_next_attempt_RE_ARMS_on_its_own_head(migrated_session: Session) -> None:
    """The revision loop is a main-line path, not an anomaly.

    Write-once per UNIT would keep attempt 1's head armed forever, so every legitimate revision
    push would raise pr_state_divergence until the unit completed -- and the operator would learn
    the alarm means "revision in progress". Write-once per CYCLE lets the next honest attempt
    re-arm while still refusing to move the head inside a live cycle.
    """
    unit, grant = _claimed_unit(migrated_session, "binding-revision")
    upsert_pr_binding(
        migrated_session,
        actor=WORKER,
        work_unit_id=unit.id,
        pr_number=42,
        head_sha=HEAD_1,
        attempt=grant.attempt,
        lease_token=grant.lease_token,
    )
    _submit(migrated_session, unit, grant, key="binding-revision")
    first_attempt = unit.attempt_count

    # Verification sends it back; the worker pushes a fix and submits again.
    for target, key in (
        (WorkUnitState.VERIFYING, "verify"),
        (WorkUnitState.REVISION_REQUIRED, "revise"),
    ):
        transition_unit(
            migrated_session,
            TransitionCommand(
                unit_id=unit.id,
                target=target,
                actor=VERIFIER,
                expected_version=unit.version,
                idempotency_key=f"binding-revision-{key}",
            ),
        )
        migrated_session.refresh(unit)

    unit.state = WorkUnitState.READY
    migrated_session.flush()
    grant2 = claim_unit(
        migrated_session, unit.id, WORKER, "binding-revision-claim-2", expected_version=unit.version
    )
    assert not isinstance(grant2, DomainError), grant2
    migrated_session.refresh(unit)
    assert unit.attempt_count > first_attempt

    upsert_pr_binding(
        migrated_session,
        actor=WORKER,
        work_unit_id=unit.id,
        pr_number=42,
        head_sha=HEAD_2,
        attempt=grant2.attempt,
        lease_token=grant2.lease_token,
    )
    _submit(migrated_session, unit, grant2, key="binding-revision-2")

    binding = get_pr_binding(migrated_session, unit.id)
    assert binding is not None
    assert binding.verification_read_head_sha == HEAD_2, "the revision push must re-arm"
    assert binding.verification_read_attempt == unit.attempt_count


def test_arming_is_refused_to_a_role_that_is_neither_worker_nor_system(
    migrated_session: Session,
) -> None:
    unit, grant = _claimed_unit(migrated_session, "binding-role")
    upsert_pr_binding(
        migrated_session,
        actor=WORKER,
        work_unit_id=unit.id,
        pr_number=42,
        head_sha=HEAD_1,
        attempt=grant.attempt,
        lease_token=grant.lease_token,
    )

    with pytest.raises(DomainError) as error:
        arm_verification_head(migrated_session, unit, actor=VERIFIER)
    assert error.value.code == "role_forbidden"


def test_writing_a_binding_for_a_unit_that_does_not_exist_is_refused(
    migrated_session: Session,
) -> None:
    with pytest.raises(DomainError) as error:
        upsert_pr_binding(
            migrated_session, actor=SYSTEM, work_unit_id=uuid.uuid4(), pr_number=1, head_sha=HEAD_1
        )
    assert error.value.code == "work_unit_not_found"
