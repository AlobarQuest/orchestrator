"""What policy may act on for a standing dependency update, read at two gates.

The decomposition approval (ADR-0006 amendment 2) and the merge (ADR-0025 amendment 4) each lift a
human gate for the same population: a revision a bump approval policy approved, standing, whose
reach is exactly the source repository, recognised only by dependency-update known-good patterns.
Holding those conditions here is what keeps the second gate from admitting a revision the first
would refuse. Each gate adds what only it can see.
"""

from dataclasses import dataclass
from typing import Final

from orchestrator.factory_policy import FactoryPolicy
from orchestrator.persistence.models import WorkPackageRevision
from orchestrator.reach_vocabulary import SOURCE_REPOSITORY, reach_from_snapshot
from orchestrator.services.execution.target_grant import DEPENDENCY_UPDATE
from orchestrator.services.intake.authority_gate import AuthorityGate

# The approval policy that approves a standing dependency-update revision.
_BUMP_APPROVER: Final = "policy:dependency-update@"


@dataclass(frozen=True)
class PolicyRefusal:
    """Why policy did not act for a bump: a fixed code, and what it read."""

    reason: str
    detail: tuple[str, ...] = ()


def is_bump_revision(revision: WorkPackageRevision) -> bool:
    return revision.profile == DEPENDENCY_UPDATE or revision.approved_by.startswith(_BUMP_APPROVER)


def standing_bump_refusal(revision: WorkPackageRevision) -> PolicyRefusal | None:
    """Why this revision isn't a standing bump in the inert reach; ``None`` when it is."""
    if not revision.approved_by.startswith(_BUMP_APPROVER):
        return PolicyRefusal("revision_not_approved_by_bump_policy")
    if revision.profile != DEPENDENCY_UPDATE:
        return PolicyRefusal("revision_not_dependency_update")
    snapshot = revision.enforcement_snapshot
    fields = snapshot.get("profile_fields") if isinstance(snapshot, dict) else None
    if not isinstance(fields, dict) or fields.get("standing") is not True:
        return PolicyRefusal("revision_not_standing")
    reach = reach_from_snapshot(snapshot)
    if reach != (SOURCE_REPOSITORY,):
        return PolicyRefusal("reach_not_inert", tuple(reach or ()))
    return None


def recognised_only_by_bump_patterns(gate: AuthorityGate, policy: FactoryPolicy) -> bool:
    """Did policy recognise the envelope, and only through dependency-update patterns?"""
    bump_patterns = {
        pattern.name
        for row in policy.rows.values()
        for pattern in row.known_good
        if pattern.change_class == DEPENDENCY_UPDATE
    }
    return (
        not gate.refusals and bool(gate.recognised_by) and set(gate.recognised_by) <= bump_patterns
    )
