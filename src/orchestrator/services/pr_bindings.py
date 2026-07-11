"""A work unit's pull-request head, and the head verification actually read.

The two fields behave differently on purpose (WS-P2.1 design 1.6):

* `head_sha` is MUTABLE and worker-written. A rebase or force-push before verification is
  normal iteration and must never raise a divergence alarm.
* `verification_read_head_sha` is the ALARM-ARMING field and is WRITE-ONCE. It is set when
  verification reads a head and is never updated -- so a later worker push moves `head_sha`
  while leaving the armed head intact.

Collapsing the two would make AC-001's head-change alarm undecidable. Freeze the head at
PR-open and every legitimate rebase false-alarms; let it track every push and an external
attacker's push silently becomes "the new expectation", so no divergence can ever fire.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import UnitPrBinding, WorkUnit
from orchestrator.services.lifecycle import ActorContext


def get_pr_binding(session: Session, work_unit_id: uuid.UUID) -> UnitPrBinding | None:
    return session.get(UnitPrBinding, work_unit_id)


def upsert_pr_binding(
    session: Session,
    *,
    actor: ActorContext,
    work_unit_id: uuid.UUID,
    pr_number: int,
    head_sha: str,
) -> UnitPrBinding:
    """Record the unit's current PR head. Never touches `verification_read_head_sha`."""
    _authorize(actor)
    _require_unit(session, work_unit_id)
    binding = _locked_binding(session, work_unit_id)
    now = TransactionClock().now(session)
    if binding is None:
        binding = UnitPrBinding(
            work_unit_id=work_unit_id,
            pr_number=pr_number,
            head_sha=head_sha,
            verification_read_head_sha=None,
            updated_at=now,
        )
        session.add(binding)
    else:
        binding.pr_number = pr_number
        binding.head_sha = head_sha
        binding.updated_at = now
    session.flush()
    return binding


def record_verification_read_head(
    session: Session,
    *,
    actor: ActorContext,
    work_unit_id: uuid.UUID,
    head_sha: str,
) -> UnitPrBinding:
    """Arm the divergence alarm at the head verification actually read. WRITE-ONCE.

    The row is taken FOR UPDATE first, so two concurrent verifications cannot both observe NULL
    and both write. Re-recording the identical sha replays; a different sha is refused.
    """
    _authorize(actor)
    binding = _locked_binding(session, work_unit_id)
    if binding is None:
        raise DomainError(
            "pr_binding_not_found",
            "work unit has no PR binding to arm",
            "record the PR binding before verification reads its head",
        )
    existing = binding.verification_read_head_sha
    if existing is not None:
        if existing == head_sha:
            return binding
        raise DomainError(
            "verification_head_already_read",
            "verification has already read a head for this work unit",
            None,
        )
    binding.verification_read_head_sha = head_sha
    binding.updated_at = TransactionClock().now(session)
    session.flush()
    return binding


def _locked_binding(session: Session, work_unit_id: uuid.UUID) -> UnitPrBinding | None:
    return session.scalar(
        select(UnitPrBinding).where(UnitPrBinding.work_unit_id == work_unit_id).with_for_update()
    )


def _require_unit(session: Session, work_unit_id: uuid.UUID) -> WorkUnit:
    unit = session.get(WorkUnit, work_unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    return unit


def _authorize(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may write a PR binding",
            None,
        )
