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

from dataclasses import dataclass

from orchestrator.errors import DomainError
from orchestrator.factory_policy import load_factory_policy
from orchestrator.kernel.authority import normalize_authority
from orchestrator.persistence.models import WorkPackageRevision, WorkUnit
from orchestrator.reach_vocabulary import reach_from_snapshot

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


def human_authority_gate(unit: WorkUnit, revision: WorkPackageRevision) -> AuthorityGate:
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
    )
    return AuthorityGate(
        refusals=recognition.refusals,
        recognised_by=recognition.recognised_by,
        policy_version=policy.version,
        policy_source=policy.source,
    )
