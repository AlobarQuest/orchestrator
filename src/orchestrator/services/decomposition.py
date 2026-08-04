import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.capability_vocabulary import validate_unit_capabilities
from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import (
    AuthorityEnvelope,
    authority_fingerprint,
    normalize_authority,
    runner_payload,
)
from orchestrator.kernel.enrichment import validate_enrichment
from orchestrator.kernel.leases import DEFAULT_MAX_ATTEMPTS
from orchestrator.kernel.runner_authority import runner_authority_violation
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    ApprovedDecomposition,
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
from orchestrator.services.packages import (
    DependencySpec,
    register_approved_unit,
    register_dependency_with_event,
)

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
    authority_payload: Mapping[str, Any] | None = None
    context_enrichment: Mapping[str, Any] | None = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS


@dataclass(frozen=True)
class ValidatedProposedUnit:
    unit: ProposedUnit
    authority_payload: dict[str, Any]


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
    unit_keys = {validated.unit.unit_key for validated in proposed_units}
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

    for validated in proposed_units:
        unit = validated.unit
        # The runner refuses an envelope that does not name its own work unit, and the
        # author cannot know the UUID. It is derivable here because _proposal_unit_id is
        # deterministic in the now-flushed proposal id, so stamping at proposal time keeps
        # the approved envelope identical to the one the runner will be served.
        stamped = _stamped_authority_payload(
            validated.authority_payload,
            _proposal_unit_id(proposal.id, unit.unit_key),
        )
        session.add(
            DecompositionProposalUnit(
                proposal_id=proposal.id,
                unit_key=unit.unit_key,
                title=unit.title,
                outcome=unit.outcome,
                required_capability=unit.required_capability,
                authority=stamped,
                authority_fingerprint=authority_fingerprint(normalize_authority(stamped)),
                context_enrichment=validate_enrichment(unit.context_enrichment),
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


def approve_decomposition_proposal(
    session: Session,
    proposal_id: uuid.UUID,
    *,
    actor: ActorContext,
    reason: str,
    idempotency_key: str,
) -> DecompositionProposal:
    _require_decision_actor(actor)
    _require_decision_reason(reason)
    replay = _decision_replay(
        session,
        proposal_id=proposal_id,
        action="decomposition.approved",
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay

    proposal = _proposal_for_decision(session, proposal_id)
    revision = session.get(
        WorkPackageRevision,
        proposal.work_package_revision_id,
        with_for_update=True,
    )
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    _require_proposed_state(proposal)

    proposal_units = tuple(
        session.scalars(
            select(DecompositionProposalUnit)
            .where(DecompositionProposalUnit.proposal_id == proposal.id)
            .order_by(DecompositionProposalUnit.unit_key)
            .with_for_update()
        )
    )
    proposal_dependencies = tuple(
        session.scalars(
            select(DecompositionProposalDependency)
            .where(DecompositionProposalDependency.proposal_id == proposal.id)
            .order_by(
                DecompositionProposalDependency.source_unit_key,
                DecompositionProposalDependency.target_unit_key,
                DecompositionProposalDependency.external_ref,
            )
            .with_for_update()
        )
    )
    _revalidate_ac_disposition(
        session,
        revision.id,
        proposal.id,
        {unit.unit_key for unit in proposal_units},
    )

    existing_approval = session.scalar(
        select(ApprovedDecomposition)
        .where(
            ApprovedDecomposition.work_package_revision_id == revision.id,
            ApprovedDecomposition.superseded_at.is_(None),
        )
        .with_for_update()
    )
    if existing_approval is not None:
        raise DomainError(
            "decomposition_already_approved",
            "an active approved decomposition already exists for this revision",
            None,
        )

    decided_at = TransactionClock().now(session)
    approved = ApprovedDecomposition(
        work_package_revision_id=revision.id,
        proposal_id=proposal.id,
        approved_by=actor.actor_id,
        approved_at=decided_at,
    )
    session.add(approved)
    session.flush()
    created_work_unit_ids: dict[str, str] = {}
    units_by_key: dict[str, uuid.UUID] = {}
    for proposal_unit in proposal_units:
        unit = register_approved_unit(
            session,
            revision_id=revision.id,
            unit_id=_proposal_unit_id(proposal.id, proposal_unit.unit_key),
            unit_key=proposal_unit.unit_key,
            title=proposal_unit.title,
            outcome=proposal_unit.outcome,
            required_capability=proposal_unit.required_capability,
            authority=normalize_authority(proposal_unit.authority),
            authority_payload=proposal_unit.authority,
            context_enrichment=proposal_unit.context_enrichment,
            max_attempts=proposal_unit.max_attempts,
            approved_by=actor.actor_id,
            approved_at=decided_at,
            actor_id=actor.actor_id,
            actor_role=actor.role,
            activation_source="approved_decomposition",
            approved_decomposition_id=approved.id,
            idempotency_key=_derived_idempotency_key(
                idempotency_key, f"unit:{proposal_unit.unit_key}"
            ),
        )
        created_work_unit_ids[proposal_unit.unit_key] = str(unit.id)
        units_by_key[proposal_unit.unit_key] = unit.id

    for proposal_dependency in proposal_dependencies:
        register_dependency_with_event(
            session,
            work_unit_id=units_by_key[proposal_dependency.source_unit_key],
            spec=_dependency_spec(proposal_dependency, units_by_key),
            actor_id=actor.actor_id,
            actor_role=actor.role,
            idempotency_key=_dependency_idempotency_key(idempotency_key, proposal_dependency),
        )

    proposal.state = "approved"
    proposal.decided_by = actor.actor_id
    proposal.decided_at = decided_at
    proposal.decision_reason = reason
    proposal.created_work_unit_ids = created_work_unit_ids
    session.flush()
    _record_decision_event(
        session,
        proposal=proposal,
        actor=actor,
        action="decomposition.approved",
        from_state="proposed",
        reason=reason,
        idempotency_key=idempotency_key,
        payload={
            "approved_decomposition_id": str(approved.id),
            "created_work_unit_ids": created_work_unit_ids,
        },
        occurred_at=decided_at,
    )
    return proposal


def reject_decomposition_proposal(
    session: Session,
    proposal_id: uuid.UUID,
    *,
    actor: ActorContext,
    reason: str,
    idempotency_key: str,
) -> DecompositionProposal:
    return _decide_decomposition_proposal(
        session,
        proposal_id,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
        action="decomposition.rejected",
        to_state="rejected",
    )


def require_decomposition_revision(
    session: Session,
    proposal_id: uuid.UUID,
    *,
    actor: ActorContext,
    reason: str,
    idempotency_key: str,
) -> DecompositionProposal:
    return _decide_decomposition_proposal(
        session,
        proposal_id,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
        action="decomposition.revision_required",
        to_state="revision_required",
    )


def _require_submission_actor(actor: ActorContext) -> None:
    if not actor.actor_id or actor.role not in _ALLOWED_ROLES:
        raise DomainError("role_forbidden", "actor may not submit decomposition proposals", None)


def _require_decision_actor(actor: ActorContext) -> None:
    if not actor.actor_id or actor.role is not ActorRole.HUMAN:
        raise DomainError(
            "human_actor_required",
            "decomposition decisions require a registered human actor",
            None,
        )


def _require_decision_reason(reason: str) -> None:
    if not reason.strip():
        raise DomainError(
            "decision_reason_required",
            "decomposition decisions require a non-empty reason",
            None,
        )


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


def _decision_replay(
    session: Session,
    *,
    proposal_id: uuid.UUID,
    action: str,
    actor: ActorContext,
    reason: str,
    idempotency_key: str,
) -> DecompositionProposal | None:
    event = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    if event is None:
        return None
    if (
        event.action != action
        or event.subject_type != "decomposition_proposal"
        or event.subject_id != proposal_id
        or event.payload.get("command") != _decision_identity(action, proposal_id, actor, reason)
    ):
        raise _idempotency_conflict()
    proposal = session.get(DecompositionProposal, proposal_id)
    if proposal is None:
        raise DomainError("event_invalid", "proposal event subject does not exist", None)
    return proposal


def _validated_units(
    proposed_units: Sequence[ProposedUnit],
) -> tuple[ValidatedProposedUnit, ...]:
    if not proposed_units:
        raise DomainError(
            "decomposition_proposal_units_invalid",
            "decomposition proposal requires at least one proposed unit",
            None,
        )
    observed_keys: set[str] = set()
    validated_units: list[ValidatedProposedUnit] = []
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
        payload = _validate_unit_constraints(unit)
        validate_enrichment(unit.context_enrichment)
        observed_keys.add(unit.unit_key)
        validated_units.append(ValidatedProposedUnit(unit=unit, authority_payload=payload))
    return tuple(validated_units)


def _validate_unit_constraints(unit: ProposedUnit) -> dict[str, Any]:
    payload = _authority_payload(unit)
    envelope = normalize_authority(payload)
    validate_unit_capabilities(unit.required_capability, envelope.capabilities)
    constraints = payload.get("constraints", {})
    if not isinstance(constraints, Mapping):
        raise DomainError(
            "authority_constraints_invalid",
            "proposed unit authority constraints must be a mapping",
            None,
        )
    if "work_unit_id" in constraints:
        raise DomainError(
            "authority_work_unit_id_forbidden",
            "constraints.work_unit_id is assigned by the orchestrator at proposal time",
            "omit constraints.work_unit_id from the proposed authority envelope",
        )
    # change_class is load-bearing in the runner's command validation (WS-P2.33), and
    # normalize_authority reads a malformed value as absent — which would waive the
    # dependency-update mutation requirement here while the runner refuses the raw payload.
    change_class = payload.get("change_class")
    if change_class is not None and (not isinstance(change_class, str) or not change_class.strip()):
        raise DomainError(
            "authority_change_class_invalid",
            "authority change_class must be a non-empty string when present",
            "declare change_class as a non-empty string, or omit it",
        )
    # normalized() emits an explicit conformance=None for envelopes that omit it, so an
    # absent claim and a null claim are the same thing: nothing to validate, and dispatch
    # will fail closed on conformance_missing.
    conformance = payload.get("conformance")
    if conformance is not None:
        _validate_unit_conformance(conformance)
    violation = runner_authority_violation(envelope, payload)
    if violation is not None:
        raise DomainError(violation.code, violation.message, violation.remediation)
    return payload


def _validate_unit_conformance(conformance: Any) -> None:
    """Conformance attests the unit's own target repository, so it must be shaped before it
    is fingerprinted — an unparseable claim would otherwise be approved as an unknown field."""
    if not isinstance(conformance, Mapping):
        raise DomainError(
            "authority_conformance_invalid",
            "proposed unit authority conformance must be a mapping",
            None,
        )
    missing = {"status", "standards_touched", "accepted_standards"} - set(conformance)
    if missing:
        raise DomainError(
            "authority_conformance_invalid",
            f"conformance is missing required keys: {', '.join(sorted(missing))}",
            None,
        )
    if not isinstance(conformance["status"], str) or not conformance["status"]:
        raise DomainError(
            "authority_conformance_invalid",
            "conformance.status must be a non-empty string",
            None,
        )
    for name in ("standards_touched", "accepted_standards"):
        value = conformance[name]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise DomainError(
                "authority_conformance_invalid",
                f"conformance.{name} must be a list of strings",
                None,
            )


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
                "authority": _authority_payload(unit),
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


def _decision_identity(
    action: str,
    proposal_id: uuid.UUID,
    actor: ActorContext,
    reason: str,
) -> dict[str, Any]:
    return {
        "action": action,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "proposal_id": str(proposal_id),
        "reason": reason,
    }


def _dependency_identity(dependency: ProposedDependency) -> dict[str, Any]:
    return {
        "source_unit_key": dependency.source_unit_key,
        "kind": dependency.kind,
        "required_state_or_condition": dependency.required_state_or_condition,
        "target_unit_key": dependency.target_unit_key,
        "external_ref": dependency.external_ref,
    }


def _authority_payload(unit: ProposedUnit) -> dict[str, Any]:
    payload = unit.authority_payload
    if payload is None:
        return runner_payload(unit.authority)
    return {key: payload[key] for key in sorted(payload)}


def _stamped_authority_payload(payload: Mapping[str, Any], unit_id: uuid.UUID) -> dict[str, Any]:
    constraints = dict(payload.get("constraints", {}))
    constraints["work_unit_id"] = str(unit_id)
    stamped = {**payload, "constraints": dict(sorted(constraints.items()))}
    return {key: stamped[key] for key in sorted(stamped)}


def _decide_decomposition_proposal(
    session: Session,
    proposal_id: uuid.UUID,
    *,
    actor: ActorContext,
    reason: str,
    idempotency_key: str,
    action: str,
    to_state: str,
) -> DecompositionProposal:
    _require_decision_actor(actor)
    _require_decision_reason(reason)
    replay = _decision_replay(
        session,
        proposal_id=proposal_id,
        action=action,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    proposal = _proposal_for_decision(session, proposal_id)
    _require_proposed_state(proposal)
    decided_at = TransactionClock().now(session)
    proposal.state = to_state
    proposal.decided_by = actor.actor_id
    proposal.decided_at = decided_at
    proposal.decision_reason = reason
    session.flush()
    _record_decision_event(
        session,
        proposal=proposal,
        actor=actor,
        action=action,
        from_state="proposed",
        reason=reason,
        idempotency_key=idempotency_key,
        payload={},
        occurred_at=decided_at,
    )
    return proposal


def _proposal_for_decision(session: Session, proposal_id: uuid.UUID) -> DecompositionProposal:
    proposal = session.get(DecompositionProposal, proposal_id, with_for_update=True)
    if proposal is None:
        raise DomainError(
            "decomposition_proposal_not_found",
            "decomposition proposal does not exist",
            None,
        )
    return proposal


def _require_proposed_state(proposal: DecompositionProposal) -> None:
    if proposal.state != "proposed":
        raise DomainError(
            "decomposition_proposal_state_invalid",
            "decomposition proposal is not awaiting decision",
            "reload",
            current_state=proposal.state,
        )


def _revalidate_ac_disposition(
    session: Session,
    revision_id: uuid.UUID,
    proposal_id: uuid.UUID,
    unit_keys: set[str],
) -> None:
    package_criteria = tuple(
        session.scalars(
            select(PackageAcceptanceCriterion)
            .where(PackageAcceptanceCriterion.work_package_revision_id == revision_id)
            .order_by(PackageAcceptanceCriterion.ac_id)
            .with_for_update()
        )
    )
    criteria_by_id = {str(criterion.id): criterion for criterion in package_criteria}
    ac_mappings = tuple(
        session.scalars(
            select(DecompositionProposalAcMapping)
            .where(DecompositionProposalAcMapping.proposal_id == proposal_id)
            .order_by(
                DecompositionProposalAcMapping.package_acceptance_criterion_id,
                DecompositionProposalAcMapping.unit_key,
            )
            .with_for_update()
        )
    )
    retained_acs = tuple(
        session.scalars(
            select(DecompositionProposalRetainedAc)
            .where(DecompositionProposalRetainedAc.proposal_id == proposal_id)
            .order_by(DecompositionProposalRetainedAc.package_acceptance_criterion_id)
            .with_for_update()
        )
    )
    validated_mappings = _validated_ac_mappings(
        tuple(
            AcMapping(ac_id=str(mapping.package_acceptance_criterion_id), unit_key=mapping.unit_key)
            for mapping in ac_mappings
        ),
        criteria_by_id,
        unit_keys,
    )
    validated_retained = _validated_retained_acs(
        tuple(
            RetainedAc(
                ac_id=str(retained.package_acceptance_criterion_id),
                rationale=retained.rationale,
            )
            for retained in retained_acs
        ),
        criteria_by_id,
    )
    _validate_ac_coverage(criteria_by_id, validated_mappings, validated_retained)


def _proposal_unit_id(proposal_id: uuid.UUID, unit_key: str) -> uuid.UUID:
    return uuid.uuid5(proposal_id, unit_key)


def _derived_idempotency_key(idempotency_key: str, suffix: str) -> str:
    return f"{idempotency_key}:{suffix}"


def _dependency_idempotency_key(
    idempotency_key: str,
    dependency: DecompositionProposalDependency,
) -> str:
    reference = dependency.target_unit_key or dependency.external_ref or "missing"
    return _derived_idempotency_key(
        idempotency_key,
        f"dependency:{dependency.source_unit_key}:{dependency.kind}:{reference}",
    )


def _dependency_spec(
    dependency: DecompositionProposalDependency,
    units_by_key: Mapping[str, uuid.UUID],
) -> DependencySpec:
    if dependency.target_unit_key is not None:
        return DependencySpec.work_unit(
            units_by_key[dependency.target_unit_key],
            dependency.required_state_or_condition,
        )
    assert dependency.external_ref is not None
    return DependencySpec.external(
        dependency.external_ref,
        dependency.required_state_or_condition,
    )


def _record_decision_event(
    session: Session,
    *,
    proposal: DecompositionProposal,
    actor: ActorContext,
    action: str,
    from_state: str,
    reason: str,
    idempotency_key: str,
    payload: Mapping[str, Any],
    occurred_at: Any,
) -> None:
    session.add(
        Event(
            occurred_at=occurred_at,
            actor_id=actor.actor_id,
            action=action,
            subject_type="decomposition_proposal",
            subject_id=proposal.id,
            from_state=from_state,
            to_state=proposal.state,
            payload={
                "command": _decision_identity(action, proposal.id, actor, reason),
                **payload,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=idempotency_key,
        )
    )
    session.flush()


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different operation",
        "use a new idempotency key",
    )
