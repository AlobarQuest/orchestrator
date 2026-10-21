"""Who may claim a rotation unit, and when (ADR-0055 decisions 1, 8 and 9, as amended).

A **rotation unit** is one whose required capability is ``operational_action`` and whose package
revision has profile ``non-software-operational`` with ``standing`` true and a ``credential_id``
set. The rotation executor does every step that moves a credential value, so its claims are
confined both ways at the server: only the configured executor identity may hold a claim on a
rotation unit, and that identity may hold a claim on nothing else. With no executor configured,
every claim on a rotation unit is refused.

A rotation unit is never reclaimed to a new owner, because the reclaim route hands the new lease
token to its SYSTEM caller and a pulling executor never receives it. A lapsed rotation claim is
released instead (``claims.release_expired_claim``) and claimed again through ``claim_unit``,
where confinement and the change window both apply.

**The window gates starts, not continuations.** A claim on a rotation unit asks the
``live_estate`` row's change window, whatever reach the package declares, unless the unit's
approved envelope marks it as touching no hosted service. It is a continuation, and isn't
windowed, exactly when the unit's previous claim was released because its lease lapsed and that
claim's holder filed evidence on that attempt. Renewal never starts work and is never windowed:
the deliberate exception to the rule that a per-claim rule covers all three lease writers.

**The claim is where a rotation unit's lifted authority requirement is acted on** (ADR-0055
amendment 11). A rotation unit is never dispatched, so the dispatch-time authority term and its
record never run for one. A claim on a rotation unit nobody approved asks policy again, as dispatch
does: a pattern that no longer recognises the envelope refuses the claim, and one that does leaves
the same ``authority.human_gate_not_required`` event dispatch writes, attributed to the claimant.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.factory_policy import load_factory_policy
from orchestrator.kernel.authority import normalize_authority
from orchestrator.persistence.models import (
    Claim,
    Event,
    Evidence,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.reach_vocabulary import LIVE_ESTATE
from orchestrator.services.intake.authority_gate import AuthorityGate, human_authority_gate

# The envelope constraint that exempts a rotation unit from the claim-time window. Only the
# literal `True` exempts; any other value, or none, leaves the unit windowed.
NON_HOSTED_CONSTRAINT = "touches_no_hosted_service"


def is_rotation_unit(unit: WorkUnit, revision: WorkPackageRevision) -> bool:
    """Decision 1's predicate, read from the unit and its package revision's snapshot."""
    if unit.required_capability != "operational_action":
        return False
    if revision.profile != "non-software-operational":
        return False
    snapshot = revision.enforcement_snapshot
    fields = snapshot.get("profile_fields") if isinstance(snapshot, dict) else None
    if not isinstance(fields, dict) or fields.get("standing") is not True:
        return False
    credential = fields.get("credential_id")
    return isinstance(credential, str) and bool(credential.strip())


def confinement_refusal(
    unit: WorkUnit,
    revision: WorkPackageRevision,
    claimant: str,
    executor: str | None,
) -> DomainError | None:
    """Why ``claimant`` may not hold a claim on ``unit``; ``None`` means confinement allows it."""
    if is_rotation_unit(unit, revision):
        if executor is None or claimant != executor:
            return DomainError(
                "rotation_claim_confined",
                "only the rotation executor may claim a rotation unit",
                None,
            )
        return None
    if executor is not None and claimant == executor:
        return DomainError(
            "rotation_executor_confined",
            "the rotation executor may claim only rotation units",
            None,
        )
    return None


def claim_window_refusal(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    now: datetime,
) -> DomainError | None:
    """Why this claim may not START now; ``None`` means the window raises no objection.

    Asked only of rotation units. A policy this process can't read refuses: unlike admission,
    no later check stands behind this one. ``load_factory_policy`` raises that refusal itself.
    """
    if not is_rotation_unit(unit, revision) or _marked_non_hosted(unit):
        return None
    if _is_continuation(session, unit):
        return None
    refusal = load_factory_policy().window_refusal((LIVE_ESTATE,), now)
    if refusal is None:
        return None
    return DomainError(
        refusal,
        "a rotation unit that touches a hosted service starts only inside the change window",
        "wait for the change window",
    )


def _marked_non_hosted(unit: WorkUnit) -> bool:
    return normalize_authority(unit.authority).constraints.get(NON_HOSTED_CONSTRAINT) is True


def _is_continuation(session: Session, unit: WorkUnit) -> bool:
    previous = session.scalar(
        select(Claim).where(Claim.work_unit_id == unit.id).order_by(Claim.attempt.desc()).limit(1)
    )
    if previous is None or previous.terminal_reason != "lease_expired":
        return False
    holder_evidence = session.scalar(
        select(Evidence.id)
        .where(
            Evidence.work_unit_id == unit.id,
            Evidence.attempt == previous.attempt,
            Evidence.recorded_by == previous.claimed_by,
        )
        .limit(1)
    )
    return holder_evidence is not None


def policy_authority_gate(unit: WorkUnit, revision: WorkPackageRevision) -> AuthorityGate | None:
    """Policy's answer for a rotation unit no person approved; ``None`` when it isn't asked.

    A unit carrying a person's approval passed the authority term on that approval, so policy is
    not consulted for it and nothing records that policy lifted anything.
    """
    if unit.authority_approval_id is not None or not is_rotation_unit(unit, revision):
        return None
    return human_authority_gate(unit, revision)


def authority_refusal(gate: AuthorityGate | None) -> DomainError | None:
    """Why the claim may not proceed on authority grounds; ``None`` means it may."""
    if gate is None or not gate.refusals:
        return None
    return DomainError(
        "authority_approval_missing",
        "no person approved this rotation unit's envelope and policy no longer recognises it",
        "approve the authority envelope",
    )


def record_gate_not_required(
    session: Session,
    unit: WorkUnit,
    gate: AuthorityGate,
    claimant: str,
    attempt: int,
    idempotency_key: str,
) -> None:
    """The trace dispatch leaves when policy lifted the requirement, written at the claim instead.

    Not an approval: an event naming the patterns, the artifact version and the fingerprint they
    recognised, with ``authority_approval_id`` left null.
    """
    session.add(
        Event(
            actor_id=claimant,
            action="authority.human_gate_not_required",
            subject_type="work_unit",
            subject_id=unit.id,
            from_state=unit.state,
            to_state=unit.state,
            payload={
                "recognised_by": list(gate.recognised_by),
                "policy_version": gate.policy_version,
                "policy_source": gate.policy_source,
                "authority_fingerprint": unit.authority_fingerprint,
                "claim_attempt": attempt,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=f"{idempotency_key}:authority-gate",
        )
    )
    session.flush()
