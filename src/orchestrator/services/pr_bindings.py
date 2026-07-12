"""A work unit's pull-request head, and the head its verification cycle was armed on.

The two fields behave differently on purpose (WS-P2.1 design 1.6):

* `head_sha` is MUTABLE and worker-written. A rebase or force-push while the worker is still
  iterating is normal and must never raise a divergence alarm.
* `verification_read_head_sha` is the ALARM-ARMING field. It is set when the worker SUBMITS --
  the moment it hands the head over to be adjudicated -- and is write-once WITHIN that
  verification cycle, keyed by `verification_read_attempt`.

Collapsing the two would make the head-change alarm undecidable. Freeze the head at PR-open and
every legitimate rebase false-alarms; let it track every push and an attacker's push silently
becomes "the new expectation", so no divergence can ever fire.

Two decisions here are not obvious and are load-bearing:

**Why arming happens at SUBMIT, not at verification.** Verification adjudicates EVIDENCE, not a
git tree -- this orchestrator is push-only and makes no outbound call, so it never reads GitHub.
The head that verification effectively acts on is therefore the head the worker submitted its
evidence at. Arming later, at verify time, would re-read `head_sha` from the binding: if someone
pushed between submit and verify, that push would be silently adopted AS the expectation, and
the divergence could never fire. Arming at submit is the only point where the value means what
the alarm needs it to mean.

**Why write-once is per CYCLE, not per unit.** The revision loop -- REVISION_REQUIRED -> READY ->
worker pushes a fix -> SUBMITTED -> verified again -- is a designed main-line path. A head armed
once per unit would keep attempt 1's head forever, so every legitimate revision push would raise
`pr_state_divergence` until the unit completed. The operator would learn the alarm means
"revision in progress" and stop trusting it, which destroys the signal AC-001 exists to give.
Keying write-once on the attempt keeps the anti-tamper property -- nothing can move the armed
head inside a live cycle -- while letting the next honest attempt re-arm. (Approved by Devon,
2026-07-11; the design text said "write-once" without contemplating the revision loop.)
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import UnitPrBinding, WorkUnit
from orchestrator.services.claims import validate_active_claim
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
    attempt: int | None = None,
    lease_token: str | None = None,
) -> UnitPrBinding:
    """Record the unit's current PR head. Never touches the armed head.

    A WORKER must prove it holds the unit's live claim, exactly as it must to record evidence.
    Without that, any worker could rewrite any unit's expectation -- and the expectation is the
    only thing divergence is measured against, so overwriting it silently disarms the alarm.
    SYSTEM may write without a claim: it is the operator's repair path.
    """
    unit = _require_unit(session, work_unit_id)
    _authorize_write(session, unit, actor, attempt, lease_token)
    binding = _locked_binding(session, work_unit_id)
    now = TransactionClock().now(session)
    if binding is None:
        binding = UnitPrBinding(
            work_unit_id=work_unit_id,
            pr_number=pr_number,
            head_sha=head_sha,
            verification_read_head_sha=None,
            verification_read_attempt=None,
            updated_at=now,
        )
        session.add(binding)
    else:
        binding.pr_number = pr_number
        binding.head_sha = head_sha
        binding.updated_at = now
    session.flush()
    return binding


def arm_verification_head(session: Session, unit: WorkUnit, *, actor: ActorContext) -> None:
    """Arm the divergence alarm on the head being submitted. Called inside the SUBMIT transaction.

    A unit with no PR binding is a no-op: not every work unit opens a pull request, and those
    must still be able to submit.
    """
    binding = _locked_binding(session, unit.id)
    if binding is None:
        return
    record_verification_read_head(
        session,
        actor=actor,
        work_unit_id=unit.id,
        head_sha=binding.head_sha,
        attempt=unit.attempt_count,
    )


def record_verification_read_head(
    session: Session,
    *,
    actor: ActorContext,
    work_unit_id: uuid.UUID,
    head_sha: str,
    attempt: int,
) -> UnitPrBinding:
    """Arm the alarm at the head handed over for adjudication. WRITE-ONCE PER ATTEMPT.

    The row is taken FOR UPDATE first, so two concurrent submits cannot both observe NULL and
    both write. Re-arming the identical sha in the same cycle replays. A DIFFERENT sha in the
    same cycle is refused: the head moved after it was handed over, which is precisely the
    tampering this field exists to make visible, and letting the write through would erase the
    evidence of it. A later attempt re-arms freely -- that is the revision loop working.
    """
    # Defence in depth. The SUBMIT path has already proved the worker holds the claim, and only
    # WORKER edges reach SUBMITTED -- but arming is the one write that decides whether the alarm
    # can fire at all, so it states its own rule rather than inheriting one from its caller.
    if actor.role not in {ActorRole.SYSTEM, ActorRole.WORKER}:
        raise DomainError(
            "role_forbidden",
            "only a worker submitting, or the system actor, may arm the divergence alarm",
            None,
        )
    binding = _locked_binding(session, work_unit_id)
    if binding is None:
        raise DomainError(
            "pr_binding_not_found",
            "work unit has no PR binding to arm",
            "record the PR binding before submitting",
        )
    armed = binding.verification_read_head_sha
    if armed is not None and binding.verification_read_attempt == attempt:
        if armed == head_sha:
            return binding
        raise DomainError(
            "verification_head_already_read",
            "the submitted head was already armed for this attempt",
            None,
        )
    binding.verification_read_head_sha = head_sha
    binding.verification_read_attempt = attempt
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


def _authorize_write(
    session: Session,
    unit: WorkUnit,
    actor: ActorContext,
    attempt: int | None,
    lease_token: str | None,
) -> None:
    if actor.role is ActorRole.SYSTEM:
        return
    if actor.role is not ActorRole.WORKER:
        raise DomainError(
            "role_forbidden",
            "only a claim-holding worker or the system actor may write a PR binding",
            None,
        )
    if attempt is None or lease_token is None:
        raise DomainError(
            "claim_not_owned",
            "a worker must present its attempt and lease token to write a PR binding",
            None,
        )
    validate_active_claim(session, unit, actor, attempt, lease_token)
