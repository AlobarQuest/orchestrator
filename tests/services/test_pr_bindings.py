"""WS-P2.1 Task 4: the PR binding, and why its two head fields behave differently.

Arming is write-once WITHIN a verification cycle, keyed by attempt (Task 16a, approved
2026-07-11). The production writers -- the worker's PR report and the SUBMIT-time arming -- are
covered in test_pr_binding_writers.py; these tests pin the service contract underneath them.
"""

import uuid

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import UnitPrBinding
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_bindings import (
    get_pr_binding,
    record_verification_read_head,
    upsert_pr_binding,
)
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
WORKER = ActorContext("worker-1", ActorRole.WORKER)
HEAD_A = "a" * 40
HEAD_B = "b" * 40


def test_a_binding_without_an_attempt_is_refused(migrated_session: Session) -> None:
    """The submit guard requires a binding written for the CURRENT attempt; a write that omits the
    attempt would store a NULL binding_attempt that can never satisfy it. Refuse with a NAMED error
    (not an IntegrityError), including for SYSTEM -- whose repair path must supply the real attempt
    rather than strand the unit with an un-submittable binding."""
    unit = register_unit(migrated_session, "pr-binding-no-attempt")

    with pytest.raises(DomainError) as error:
        upsert_pr_binding(
            migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=7, head_sha=HEAD_A
        )

    assert error.value.code == "pr_binding_attempt_required"


def test_upsert_creates_then_updates_head_sha(migrated_session: Session) -> None:
    """A worker rebase or force-push BEFORE verification is normal and must not alarm."""
    unit = register_unit(migrated_session, "pr-binding")

    created = upsert_pr_binding(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD_A,
        attempt=1,
    )

    assert isinstance(created, UnitPrBinding)
    assert (created.pr_number, created.head_sha) == (42, HEAD_A)
    assert created.binding_attempt == 1
    assert created.verification_read_head_sha is None

    rebased = upsert_pr_binding(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD_B,
        attempt=1,
    )

    assert rebased.head_sha == HEAD_B
    assert rebased.binding_attempt == 1
    assert rebased.verification_read_head_sha is None
    binding = get_pr_binding(migrated_session, unit.id)
    assert binding is not None and binding.head_sha == HEAD_B


def test_verification_read_head_is_write_once(migrated_session: Session) -> None:
    """The alarm-arming field. Once verification has read a head, nothing may overwrite it."""
    unit = register_unit(migrated_session, "pr-binding-armed")
    upsert_pr_binding(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=7, head_sha=HEAD_A,
        attempt=1,
    )

    armed = record_verification_read_head(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, head_sha=HEAD_A, attempt=1
    )
    assert armed.verification_read_head_sha == HEAD_A

    with pytest.raises(DomainError) as error:
        record_verification_read_head(
            migrated_session, actor=SYSTEM, work_unit_id=unit.id, head_sha=HEAD_B, attempt=1
        )

    assert error.value.code == "verification_head_already_read"

    # ...but the NEXT attempt re-arms. The revision loop is a main-line path, and a head armed
    # once per UNIT would make every legitimate revision push raise pr_state_divergence.
    rearmed = record_verification_read_head(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, head_sha=HEAD_B, attempt=2
    )
    assert rearmed.verification_read_head_sha == HEAD_B
    assert rearmed.verification_read_attempt == 2


def test_a_later_worker_push_cannot_disarm_the_verification_read_head(
    migrated_session: Session,
) -> None:
    """THE point of splitting the two fields.

    If a later push could move the armed head, an external attacker's push -- observed by the
    runner -- would silently become 'the new expectation' and the post-verification divergence
    alarm could never fire.
    """
    unit = register_unit(migrated_session, "pr-binding-push-after-verify")
    upsert_pr_binding(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=7, head_sha=HEAD_A,
        attempt=1,
    )
    record_verification_read_head(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, head_sha=HEAD_A, attempt=1
    )

    upsert_pr_binding(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=7, head_sha=HEAD_B,
        attempt=1,
    )

    binding = get_pr_binding(migrated_session, unit.id)
    assert binding is not None
    assert binding.head_sha == HEAD_B  # the observed head moved
    assert binding.verification_read_head_sha == HEAD_A  # the armed head did not


def test_re_recording_the_same_verification_head_is_an_idempotent_no_op(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "pr-binding-replay")
    upsert_pr_binding(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=7, head_sha=HEAD_A,
        attempt=1,
    )

    first = record_verification_read_head(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, head_sha=HEAD_A, attempt=1
    )
    replay = record_verification_read_head(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, head_sha=HEAD_A, attempt=1
    )

    assert replay.verification_read_head_sha == first.verification_read_head_sha == HEAD_A


def test_arming_without_a_binding_is_rejected(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "pr-binding-missing")

    with pytest.raises(DomainError) as error:
        record_verification_read_head(
            migrated_session, actor=SYSTEM, work_unit_id=unit.id, head_sha=HEAD_A, attempt=1
        )

    assert error.value.code == "pr_binding_not_found"


def test_a_worker_with_no_claim_may_not_write_a_binding(migrated_session: Session) -> None:
    """A claim-holding worker IS a legitimate writer (that is the point of the route): it is the
    worker that opens the PR. What must be refused is a worker that holds no claim on the unit --
    otherwise it could overwrite another unit's expectation and silently disarm its alarm."""
    unit = register_unit(migrated_session, "pr-binding-role")

    with pytest.raises(DomainError) as error:
        upsert_pr_binding(
            migrated_session, actor=WORKER, work_unit_id=unit.id, pr_number=7, head_sha=HEAD_A
        )

    assert error.value.code == "claim_not_owned"


def test_a_binding_for_an_unknown_unit_is_rejected(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        upsert_pr_binding(
            migrated_session,
            actor=SYSTEM,
            work_unit_id=uuid.uuid4(),
            pr_number=7,
            head_sha=HEAD_A,
        )

    assert error.value.code == "work_unit_not_found"


def test_get_pr_binding_returns_none_when_absent(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "pr-binding-absent")

    assert get_pr_binding(migrated_session, unit.id) is None
