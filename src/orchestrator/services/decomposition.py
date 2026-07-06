import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityEnvelope, authority_fingerprint
from orchestrator.kernel.leases import DEFAULT_MAX_ATTEMPTS
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    DecompositionProposal,
    DecompositionProposalAcMapping,
    DecompositionProposalDependency,
    DecompositionProposalRetainedAc,
    DecompositionProposalUnit,
    Event,
    PackageAcceptanceCriterion,
    WorkPackageRevision,
)
from orchestrator.services.lifecycle import ActorContext

_PROPOSAL_ACTION = "decomposition.proposed"
_PACKAGE_CLI_SOURCE = "package_cli"
_ALLOWED_ROLES = frozenset(
    {
        ActorRole.WORKER,
        ActorRole.HUMAN,
        ActorRole.SYSTEM,
        ActorRole.VERIFIER,
    }
)


@dataclass(frozen=True)
class ProposedUnit:
    unit_key: str
    title: str
    outcome: str
    required_capability: str
    authority: AuthorityEnvelope
    max_attempts: int = DEFAULT_MAX_ATTEMPTS


@dataclass(frozen=True)
class ProposedDependency:
    source_unit_key: str
    kind: str
    required_state_or_condition: str
    target_unit_key: str | None = None
    external_ref: str | None = None


@dataclass(frozen=True)
class AcMapping:
    ac_id: str
    unit_key: str


@dataclass(frozen=True)
class RetainedAc:
    ac_id: str
    rationale: str


@dataclass(frozen=True)
class DecompositionProposalCommand:
    work_package_revision_id: uuid.UUID
    rationale: str
    proposed_units: tuple[ProposedUnit, ...]
    dependencies: tuple[ProposedDependency, ...]
    ac_mappings: tuple[AcMapping, ...]
    retained_acs: tuple[RetainedAc, ...]
    idempotency_key: str


def submit_decomposition_proposal(
    session: Session,
    command: DecompositionProposalCommand,
    actor: ActorContext,
) -> DecompositionProposal:
    _require_submission_actor(actor)
    revision = session.get(
        WorkPackageRevision,
        command.work_package_revision_id,
        with_for_update=True,
    )
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    if revision.intake_source != _PACKAGE_CLI_SOURCE:
        raise DomainError(
            "revision_not_intaken",
            "decomposition proposals require a package_cli intake revision",
            None,
        )

    replay = _proposal_replay(session, command, actor)
    if replay is not None:
        return replay

    proposed_units = _validated_units(command.proposed_units)
    unit_keys = {unit.unit_key for unit in proposed_units}
    package_criteria = tuple(
        session.scalars(
            select(PackageAcceptanceCriterion)
            .where(PackageAcceptanceCriterion.work_package_revision_id == revision.id)
            .order_by(PackageAcceptanceCriterion.ac_id)
            .with_for_update()
        )
    )
    criteria_by_id = {str(criterion.id): criterion for criterion in package_criteria}
    ac_mappings = _validated_ac_mappings(command.ac_mappings, criteria_by_id, unit_keys)
    retained_acs = _validated_retained_acs(command.retained_acs, criteria_by_id)
    _validate_ac_coverage(criteria_by_id, ac_mappings, retained_acs)
    dependencies = _validated_dependencies(command.dependencies, unit_keys)

    occurred_at = TransactionClock().now(session)
    proposal = DecompositionProposal(
        work_package_revision_id=revision.id,
        proposal_number=_next_proposal_number(session, revision.id),
        state="proposed",
        rationale=command.rationale,
        proposed_by=actor.actor_id,
        proposed_actor_role=actor.role,
        proposed_at=occurred_at,
        idempotency_key=command.idempotency_key,
    )
    session.add(proposal)
    session.flush()

    for unit in proposed_units:
        session.add(
            DecompositionProposalUnit(
                proposal_id=proposal.id,
                unit_key=unit.unit_key,
                title=unit.title,
                outcome=unit.outcome,
                required_capability=unit.required_capability,
                authority=unit.authority.normalized(),
                authority_fingerprint=authority_fingerprint(unit.authority),
                max_attempts=unit.max_attempts,
            )
        )
    for dependency in dependencies:
        session.add(
            DecompositionProposalDependency(
                proposal_id=proposal.id,
                source_unit_key=dependency.source_unit_key,
                kind=dependency.kind,
                target_unit_key=dependency.target_unit_key,
                external_ref=dependency.external_ref,
                required_state_or_condition=dependency.required_state_or_condition,
            )
        )
    for ac_mapping in ac_mappings:
        session.add(
            DecompositionProposalAcMapping(
                proposal_id=proposal.id,
                package_acceptance_criterion_id=uuid.UUID(ac_mapping.ac_id),
                unit_key=ac_mapping.unit_key,
            )
        )
    for retained_ac in retained_acs:
        session.add(
            DecompositionProposalRetainedAc(
                proposal_id=proposal.id,
                package_acceptance_criterion_id=uuid.UUID(retained_ac.ac_id),
                rationale=retained_ac.rationale,
            )
        )

    session.add(
        Event(
            occurred_at=occurred_at,
            actor_id=actor.actor_id,
            action=_PROPOSAL_ACTION,
            subject_type="decomposition_proposal",
            subject_id=proposal.id,
            from_state=None,
            to_state=proposal.state,
            payload={
                "command": _command_identity(command, actor),
                "proposal_number": proposal.proposal_number,
                "work_package_revision_id": str(revision.id),
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    session.flush()
    return proposal


def _require_submission_actor(actor: ActorContext) -> None:
    if not actor.actor_id or actor.role not in _ALLOWED_ROLES:
        raise DomainError("role_forbidden", "actor may not submit decomposition proposals", None)


def _proposal_replay(
    session: Session,
    command: DecompositionProposalCommand,
    actor: ActorContext,
) -> DecompositionProposal | None:
    event = session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key))
    if event is None:
        return None
    if (
        event.action != _PROPOSAL_ACTION
        or event.subject_type != "decomposition_proposal"
        or event.payload.get("command") != _command_identity(command, actor)
    ):
        raise _idempotency_conflict()
    proposal = session.get(DecompositionProposal, event.subject_id)
    if proposal is None:
        raise DomainError("event_invalid", "proposal event subject does not exist", None)
    return proposal


def _validated_units(proposed_units: Sequence[ProposedUnit]) -> tuple[ProposedUnit, ...]:
    if not proposed_units:
        raise DomainError(
            "decomposition_proposal_units_invalid",
            "decomposition proposal requires at least one proposed unit",
            None,
        )
    observed_keys: set[str] = set()
    normalized_units: list[ProposedUnit] = []
    for unit in proposed_units:
        if not unit.unit_key:
            raise DomainError(
                "decomposition_proposal_units_invalid",
                "proposed unit keys must be non-empty",
                None,
            )
        if unit.unit_key in observed_keys:
            raise DomainError(
                "decomposition_proposal_units_invalid",
                "proposed unit keys must be unique",
                None,
            )
        observed_keys.add(unit.unit_key)
        normalized_units.append(unit)
    return tuple(normalized_units)


def _validated_ac_mappings(
    ac_mappings: Sequence[AcMapping],
    criteria_by_id: Mapping[str, PackageAcceptanceCriterion],
    unit_keys: set[str],
) -> tuple[AcMapping, ...]:
    normalized_mappings: list[AcMapping] = []
    observed_criteria: set[str] = set()
    for ac_mapping in ac_mappings:
        if ac_mapping.ac_id not in criteria_by_id:
            raise DomainError(
                "package_acceptance_criterion_not_found",
                "proposal mapping references an unknown package acceptance criterion",
                None,
            )
        if ac_mapping.unit_key not in unit_keys:
            raise DomainError(
                "decomposition_proposal_unit_not_found",
                "proposal mapping references an unknown proposed unit",
                None,
            )
        if ac_mapping.ac_id in observed_criteria:
            raise DomainError(
                "decomposition_proposal_ac_coverage_invalid",
                "each package acceptance criterion may be mapped at most once",
                None,
            )
        observed_criteria.add(ac_mapping.ac_id)
        normalized_mappings.append(ac_mapping)
    return tuple(normalized_mappings)


def _validated_retained_acs(
    retained_acs: Sequence[RetainedAc],
    criteria_by_id: Mapping[str, PackageAcceptanceCriterion],
) -> tuple[RetainedAc, ...]:
    normalized_retained: list[RetainedAc] = []
    observed_criteria: set[str] = set()
    for retained_ac in retained_acs:
        if retained_ac.ac_id not in criteria_by_id:
            raise DomainError(
                "package_acceptance_criterion_not_found",
                "retained acceptance criterion does not exist on the package revision",
                None,
            )
        if not retained_ac.rationale:
            raise DomainError(
                "decomposition_proposal_retained_ac_invalid",
                "retained acceptance criteria require a rationale",
                None,
            )
        if retained_ac.ac_id in observed_criteria:
            raise DomainError(
                "decomposition_proposal_ac_coverage_invalid",
                "each package acceptance criterion may be retained at most once",
                None,
            )
        observed_criteria.add(retained_ac.ac_id)
        normalized_retained.append(retained_ac)
    return tuple(normalized_retained)


def _validate_ac_coverage(
    criteria_by_id: Mapping[str, PackageAcceptanceCriterion],
    ac_mappings: Sequence[AcMapping],
    retained_acs: Sequence[RetainedAc],
) -> None:
    expected_ids = set(criteria_by_id)
    mapped_ids = {ac_mapping.ac_id for ac_mapping in ac_mappings}
    retained_ids = {retained_ac.ac_id for retained_ac in retained_acs}
    if mapped_ids & retained_ids:
        raise DomainError(
            "decomposition_proposal_ac_coverage_invalid",
            "a package acceptance criterion may not be both mapped and retained",
            None,
        )
    if mapped_ids | retained_ids != expected_ids:
        raise DomainError(
            "decomposition_proposal_ac_coverage_invalid",
            "mapped and retained acceptance criteria must cover the package revision exactly",
            None,
        )


def _validated_dependencies(
    dependencies: Sequence[ProposedDependency],
    unit_keys: set[str],
) -> tuple[ProposedDependency, ...]:
    normalized_dependencies: list[ProposedDependency] = []
    internal_edges: list[tuple[str, str]] = []
    for dependency in dependencies:
        if dependency.source_unit_key not in unit_keys:
            raise DomainError(
                "decomposition_proposal_unit_not_found",
                "dependency source unit does not exist in the proposal",
                None,
            )
        if dependency.target_unit_key is not None:
            if dependency.external_ref is not None:
                raise DomainError(
                    "dependency_reference_invalid",
                    "internal proposal dependencies may not include an external reference",
                    None,
                )
            if dependency.kind != "work_unit":
                raise DomainError(
                    "dependency_kind_invalid",
                    "internal proposal dependencies must use kind work_unit",
                    None,
                )
            if dependency.target_unit_key not in unit_keys:
                raise DomainError(
                    "dependency_target_not_found",
                    "dependency target does not exist in the proposal",
                    None,
                )
            internal_edges.append((dependency.source_unit_key, dependency.target_unit_key))
        else:
            if not dependency.external_ref:
                raise DomainError(
                    "dependency_reference_invalid",
                    "external proposal dependencies require exactly one external reference",
                    None,
                )
            if dependency.kind == "work_unit":
                raise DomainError(
                    "dependency_kind_invalid",
                    "external proposal dependencies may not use kind work_unit",
                    None,
                )
        normalized_dependencies.append(dependency)
    if _has_internal_dependency_cycle(internal_edges):
        raise DomainError("dependency_cycle", "internal dependency cycle detected", None)
    return tuple(normalized_dependencies)


def _has_internal_dependency_cycle(edges: Sequence[tuple[str, str]]) -> bool:
    adjacency: dict[str, set[str]] = {}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set())

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for target in adjacency.get(node, ()):
            if visit(target):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in adjacency)


def _next_proposal_number(session: Session, revision_id: uuid.UUID) -> int:
    current = session.scalar(
        select(func.max(DecompositionProposal.proposal_number)).where(
            DecompositionProposal.work_package_revision_id == revision_id
        )
    )
    return (current or 0) + 1


def _command_identity(
    command: DecompositionProposalCommand,
    actor: ActorContext,
) -> dict[str, Any]:
    return {
        "action": _PROPOSAL_ACTION,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "work_package_revision_id": str(command.work_package_revision_id),
        "rationale": command.rationale,
        "proposed_units": [
            {
                "unit_key": unit.unit_key,
                "title": unit.title,
                "outcome": unit.outcome,
                "required_capability": unit.required_capability,
                "authority": unit.authority.normalized(),
                "max_attempts": unit.max_attempts,
            }
            for unit in command.proposed_units
        ],
        "dependencies": [_dependency_identity(dependency) for dependency in command.dependencies],
        "ac_mappings": [
            {
                "ac_id": ac_mapping.ac_id,
                "unit_key": ac_mapping.unit_key,
            }
            for ac_mapping in command.ac_mappings
        ],
        "retained_acs": [
            {
                "ac_id": retained_ac.ac_id,
                "rationale": retained_ac.rationale,
            }
            for retained_ac in command.retained_acs
        ],
    }


def _dependency_identity(dependency: ProposedDependency) -> dict[str, Any]:
    return {
        "source_unit_key": dependency.source_unit_key,
        "kind": dependency.kind,
        "required_state_or_condition": dependency.required_state_or_condition,
        "target_unit_key": dependency.target_unit_key,
        "external_ref": dependency.external_ref,
    }


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different operation",
        "use a new idempotency key",
    )
