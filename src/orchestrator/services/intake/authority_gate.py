"""Whether a human must still approve a work unit's authority envelope (WS-P2.18 Inc 3, ADR-0011).

Until now that requirement was unconditional in code: every unit needed a human approval bound to
its authority fingerprint, whatever the envelope said. R2 is the opposite -- gate the novel, and
pre-authorize work once we know how to do it -- and R7 is why that is not a safety reduction: the
review being lifted has been ceremony.

**The requirement is lifted by an ABSENT objection, never by a granted permission.** The artifact
can only refuse (ADR-0010), so what a known-good pattern does is withhold `authority_envelope_novel`
from an envelope it recognises completely. This module composes that answer with the two states in
which policy cannot answer at all -- an artifact that will not load, and a package whose reach
nobody declared -- and both resolve to the requirement standing, which is the behaviour that
existed before this module did.

**Nothing here writes an approval, and nothing here may.** There is no standing human credential
(ADR-0006), the graduation ledger reasons over approval evidence that is already contaminated by
construction-era agent-driven gates attributed to a person, and a record of a decision nobody made
is worse than no record at all. When the requirement is lifted, the trace left behind is a
different kind of row saying a different thing -- an event recorded where the lifted requirement is
acted on, naming the patterns and the artifact version that lifted it, and attributed to the system
actor that read them. "The gate did not fire" and "a human approved" are never the same record.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.factory_policy import load_factory_policy
from orchestrator.kernel.authority import normalize_authority
from orchestrator.persistence.models import Event, WorkPackageRevision, WorkUnit
from orchestrator.reach_vocabulary import reach_from_snapshot
from orchestrator.services.lifecycle.rotation_claims import ProposedUnitTerms, is_rotation_unit

# Policy could not be consulted at all. Distinct from every refusal the artifact itself can make,
# because it is a fault in this process rather than a statement about this unit -- and it is still
# an ask, because a policy that cannot be read has recognised nothing.
POLICY_UNREADABLE = "authority_policy_unreadable"


@dataclass(frozen=True)
class AuthorityGate:
    """Why a human approval is still required, and -- when it is not -- what said so.

    `refusals` empty is the only state in which the requirement is lifted. `recognised_by` names
    the declared patterns that recognised the envelope, one per reach member, and is what the
    record of a lifted requirement cites alongside the artifact version that held them.
    """

    refusals: tuple[str, ...]
    recognised_by: tuple[str, ...]
    policy_version: int | None
    policy_source: str | None


def human_authority_gate(
    unit: WorkUnit | ProposedUnitTerms, revision: WorkPackageRevision
) -> AuthorityGate:
    """Policy's answer for this unit, read from the artifact on this call.

    The revision is where the reach declaration lives: intake copies it verbatim into the
    enforcement snapshot, and `reach_from_snapshot` returns `None` for anything it cannot read
    whole -- so a declaration naming a member this build predates is undeclared here, not the
    recognisable part of itself.
    """
    try:
        policy = load_factory_policy()
    except DomainError:
        # The loader raises rather than returning a degraded policy, and both of its failures mean
        # the same thing here. Fail toward asking: this is the state the detector inherits the
        # whole job of the gate in, and it has nothing to inherit it with.
        return AuthorityGate((POLICY_UNREADABLE,), (), None, None)
    recognition = policy.authority_refusals(
        reach_from_snapshot(revision.enforcement_snapshot),
        normalize_authority(unit.authority),
        unit.id,
        rotation_unit=is_rotation_unit(unit, revision),
    )
    return AuthorityGate(
        refusals=recognition.refusals,
        recognised_by=recognition.recognised_by,
        policy_version=policy.version,
        policy_source=policy.source,
    )


def policy_authority_gate(unit: WorkUnit, revision: WorkPackageRevision) -> AuthorityGate | None:
    """Policy's answer at the claim, for a rotation unit no person approved; else ``None``.

    The claim is where a rotation unit's lifted requirement is acted on (ADR-0055 amendment 11):
    a rotation unit is never dispatched, so the dispatch-time authority term and its record never
    run for one. So the claim asks policy again, as dispatch does.

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
    if POLICY_UNREADABLE in gate.refusals:
        return DomainError(
            POLICY_UNREADABLE,
            "the policy artifact could not be read, so nothing recognises this unit's envelope",
            "correct the policy artifact",
        )
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
