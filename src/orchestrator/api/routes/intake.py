from collections.abc import Sequence
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.api.dependencies import ActorDep, SessionDep, SettingsDep
from orchestrator.api.routes.common import ERROR_RESPONSES, require_zero_expected_version
from orchestrator.api.schemas.common import ApprovalResponse
from orchestrator.api.schemas.intake import (
    ApprovalCommand,
    ChangeRecordWorkResponse,
    DecompositionDecisionCommand,
    DecompositionProposalAcMappingResponse,
    DecompositionProposalDependencyResponse,
    DecompositionProposalRegistration,
    DecompositionProposalResponse,
    DecompositionProposalRetainedAcResponse,
    DecompositionProposalUnitResponse,
    DependencyCommand,
    DependencyResolutionCommand,
    DependencyResponse,
    FollowUpMintCommand,
    FollowUpMintResponse,
    PackageAcceptanceCriterionResponse,
    PackageIntakeRegistration,
    PackageIntakeResponse,
    ProposedUnitCommand,
    ReadinessResponse,
    RunnerBriefResponse,
    StagedIntakeResponse,
)
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.persistence.models import (
    DecompositionProposal,
    DecompositionProposalUnit,
    StagedPackageIntake,
    WorkPackageRevision,
)
from orchestrator.services.intake.change_record_work import work_for_change_record
from orchestrator.services.intake.decomposition import (
    AcMapping,
    DecompositionProposalCommand,
    ProposedDependency,
    ProposedUnit,
    RetainedAc,
    require_decomposition_revision,
    submit_decomposition_proposal,
)
from orchestrator.services.intake.follow_ups import mint_due_follow_ups
from orchestrator.services.intake.intake_reads import (
    acceptance_criteria_by_id,
    intake_authority,
    proposal_children,
    revision_acceptance_criteria,
)
from orchestrator.services.intake.package_intake import (
    AcceptanceCriterionProjection,
    PackageIntakeCommand,
    register_package_intake,
)
from orchestrator.services.intake.packages import (
    DependencySpec,
    record_approval,
    register_dependency_command,
    resolve_dependency_command,
)
from orchestrator.services.intake.runner_brief import runner_brief
from orchestrator.services.intake.staged_intake import stage_package_intake
from orchestrator.services.lifecycle.readiness import evaluate_readiness

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


def package_intake_command(body: PackageIntakeRegistration) -> PackageIntakeCommand:
    """Project a validated intake registration onto the service command.

    Shared with the `/review` browser form (`web.py`), which is the only human-reachable way to
    register an intake in production -- ADR-0006. Both entry points must build the SAME command
    from the SAME validated model, or the browser path becomes a second, laxer set of rules; this
    function is what makes "the form is a new way in, not a new set of rules" checkable rather
    than merely asserted.
    """
    return PackageIntakeCommand(
        package_id=body.package_id,
        source_repository=body.source_repository,
        revision=body.revision,
        content_hash=body.content_hash,
        source_path=body.source_path,
        source_commit=body.source_commit,
        approved_by=body.approved_by,
        approved_at=body.approved_at,
        approval_event_id=body.approval_event_id,
        approval_ledger_commit=body.approval_ledger_commit,
        profile=body.profile,
        status_at_intake=body.status_at_intake,
        verification_mode=body.verification_mode,
        verification_limitations=body.verification_limitations,
        enforcement_snapshot=body.enforcement_snapshot,
        authority=normalize_authority(body.authority),
        registry_version=body.registry_version,
        acceptance_criteria=tuple(
            AcceptanceCriterionProjection(**criterion.model_dump())
            for criterion in body.acceptance_criteria
        ),
        idempotency_key=body.idempotency_key,
        expected_version=body.expected_version,
        intake_purpose=body.intake_purpose,
        follow_up=body.follow_up,
        change_record_id=body.change_record_id,
        originating_observation_id=body.originating_observation_id,
    )


@router.post("/package-intakes", response_model=PackageIntakeResponse, status_code=201)
def create_package_intake(
    body: PackageIntakeRegistration,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    revision = register_package_intake(session, package_intake_command(body), actor)
    session.commit()
    return _package_intake_payload(session, revision)


@router.post("/staged-intakes", response_model=StagedIntakeResponse, status_code=201)
def create_staged_intake(
    body: PackageIntakeRegistration,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    """Stage an intake for a person to confirm at `/review/staged-intakes/{id}`. SYSTEM only.

    The body is validated by the same model `POST /package-intakes` uses, and stored as dumped
    with unset fields left out, so the confirm rebuilds that model exactly. Staging registers
    nothing. `stage_package_intake` refuses every role but SYSTEM.
    """
    staged = stage_package_intake(session, body.model_dump(mode="json", exclude_unset=True), actor)
    session.commit()
    return _staged_intake_payload(staged)


def _staged_intake_payload(staged: StagedPackageIntake) -> dict[str, object]:
    return {
        "id": staged.id,
        "state": staged.state,
        "idempotency_key": staged.idempotency_key,
        "package_id": staged.payload["package_id"],
        "revision": staged.payload["revision"],
        "staged_by": staged.staged_by,
        "staged_at": staged.staged_at,
        "registered_revision_id": staged.registered_revision_id,
        "review_path": f"/review/staged-intakes/{staged.id}",
    }


@router.get("/package-intakes/{revision_id}", response_model=PackageIntakeResponse)
def package_intake(
    revision_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    revision = _package_intake_revision_or_raise(session, revision_id)
    return _package_intake_payload(session, revision)


@router.post(
    "/package-intakes/{revision_id}/decomposition-proposals",
    response_model=DecompositionProposalResponse,
    status_code=201,
)
def create_decomposition_proposal(
    revision_id: UUID,
    body: DecompositionProposalRegistration,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    require_zero_expected_version(body.expected_version, "decomposition proposal submission")
    proposal = submit_decomposition_proposal(
        session,
        DecompositionProposalCommand(
            work_package_revision_id=revision_id,
            rationale=body.rationale,
            proposed_units=tuple(_proposed_unit(command) for command in body.proposed_units),
            dependencies=tuple(
                ProposedDependency(**command.model_dump()) for command in body.dependencies
            ),
            ac_mappings=tuple(AcMapping(**command.model_dump()) for command in body.ac_mappings),
            retained_acs=tuple(RetainedAc(**command.model_dump()) for command in body.retained_acs),
            idempotency_key=body.idempotency_key,
        ),
        actor,
    )
    session.commit()
    return _proposal_payloads(session, (proposal,))[proposal.id]


@router.get(
    "/package-intakes/{revision_id}/decomposition-proposals",
    response_model=list[DecompositionProposalResponse],
)
def decomposition_proposals(
    revision_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> list[dict[str, object]]:
    _package_intake_revision_or_raise(session, revision_id)
    proposals = tuple(
        session.scalars(
            select(DecompositionProposal)
            .where(DecompositionProposal.work_package_revision_id == revision_id)
            .order_by(DecompositionProposal.proposal_number)
        )
    )
    payloads = _proposal_payloads(session, proposals)
    return [payloads[proposal.id] for proposal in proposals]


@router.get(
    "/decomposition-proposals/{proposal_id}",
    response_model=DecompositionProposalResponse,
)
def decomposition_proposal(
    proposal_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    proposal = _proposal_or_raise(session, proposal_id)
    return _proposal_payloads(session, (proposal,))[proposal.id]


@router.post(
    "/decomposition-proposals/{proposal_id}/require-revision",
    response_model=DecompositionProposalResponse,
)
def require_decomposition_revision_route(
    proposal_id: UUID,
    body: DecompositionDecisionCommand,
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    require_zero_expected_version(body.expected_version, "decomposition revision request")
    proposal = require_decomposition_revision(
        session,
        proposal_id,
        actor=actor,
        reason=body.reason,
        idempotency_key=body.idempotency_key,
    )
    session.commit()
    return _proposal_payloads(session, (proposal,))[proposal.id]


@router.get("/work-units/{unit_id}/readiness", response_model=ReadinessResponse)
def readiness(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    result = evaluate_readiness(session, unit_id)
    return {
        "status": result.status,
        "reasons": [
            {"code": reason.code, "subject_id": reason.subject_id, "detail": reason.detail}
            for reason in result.reasons
        ],
    }


@router.get("/work-units/{unit_id}/runner-brief", response_model=RunnerBriefResponse)
def runner_brief_route(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return runner_brief(session, unit_id)


@router.post("/follow-ups/mint", response_model=FollowUpMintResponse)
def mint_follow_ups(
    body: FollowUpMintCommand,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    """Mint the work units whose package-declared follow-up reviews have come due.

    SYSTEM-only, externally invoked, and a pure database read plus an append-only write: no
    outbound call, no loop, nothing scheduled. The role gate lives in the service.
    """
    require_zero_expected_version(body.expected_version, "follow-up minting")
    result = mint_due_follow_ups(
        session,
        actor=actor,
        due_after_days=settings.follow_up_due_after_days,
    )
    return {
        "minted": [
            {
                "work_unit_id": row.work_unit_id,
                "work_package_revision_id": row.work_package_revision_id,
                "due_at": row.due_at,
            }
            for row in result.minted
        ],
        "skipped": [
            {"work_package_revision_id": row.work_package_revision_id, "reason": row.reason}
            for row in result.skipped
        ],
        "considered": result.considered,
    }


@router.post("/work-units/{unit_id}/approvals", response_model=ApprovalResponse)
def approval(
    unit_id: UUID,
    body: ApprovalCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = record_approval(
        session,
        unit_id=unit_id,
        actor_id=actor.actor_id,
        actor_role=actor.role,
        **body.model_dump(),
    )
    session.commit()
    return result


@router.post("/work-units/{unit_id}/dependencies", response_model=DependencyResponse)
def dependency(
    unit_id: UUID,
    body: DependencyCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    values = body.model_dump(exclude={"idempotency_key", "expected_version"})
    result = register_dependency_command(
        session,
        work_unit_id=unit_id,
        spec=DependencySpec(**values),
        actor_id=actor.actor_id,
        actor_role=actor.role,
        expected_version=body.expected_version,
        idempotency_key=body.idempotency_key,
    )
    session.commit()
    return result


@router.post("/dependencies/{dependency_id}/resolve", response_model=DependencyResponse)
def dependency_resolution(
    dependency_id: UUID,
    body: DependencyResolutionCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = resolve_dependency_command(
        session,
        dependency_id=dependency_id,
        actor_id=actor.actor_id,
        actor_role=actor.role,
        **body.model_dump(),
    )
    session.commit()
    return result


@router.get(
    "/change-records/{change_record_id}/work",
    response_model=ChangeRecordWorkResponse,
)
def change_record_work_route(
    change_record_id: int,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    """ADR-0029: what work a change record caused, and whether all of it is done.

    Authentication-only, no role gate, matching the other read surfaces. Read-only: it writes
    nothing and decides nothing about the record, which lives in another service entirely.

    A record with no revision answers 200 with an empty list and a false verdict rather than 404.
    That is the ordinary state of every record a person has approved and the carry has not yet
    reached, so a 404 would make the common case indistinguishable from a broken identifier.
    """
    answer = work_for_change_record(session, change_record_id)
    return {
        "change_record_id": answer.change_record_id,
        "revision_ids": list(answer.revision_ids),
        "units": [
            {
                "unit_id": unit.unit_id,
                "unit_key": unit.unit_key,
                "revision_id": unit.revision_id,
                "state": unit.state,
            }
            for unit in answer.units
        ],
        "all_units_completed": answer.all_units_completed,
    }


def _revision_or_raise(session: Session, revision_id: UUID) -> WorkPackageRevision:
    revision = session.get(WorkPackageRevision, revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    return revision


def _package_intake_revision_or_raise(
    session: Session,
    revision_id: UUID,
) -> WorkPackageRevision:
    revision = _revision_or_raise(session, revision_id)
    if revision.intake_source != "package_cli":
        raise DomainError(
            "package_intake_not_found",
            "package intake does not exist for this revision",
            None,
        )
    return revision


def _proposal_or_raise(session: Session, proposal_id: UUID) -> DecompositionProposal:
    proposal = session.get(DecompositionProposal, proposal_id)
    if proposal is None:
        raise DomainError(
            "decomposition_proposal_not_found",
            "decomposition proposal does not exist",
            None,
        )
    return proposal


def _package_intake_payload(
    session: Session,
    revision: WorkPackageRevision,
) -> dict[str, object]:
    acceptance_criteria = revision_acceptance_criteria(session, revision.id)
    return {
        "id": revision.id,
        "package_id": revision.work_package.package_id,
        "source_repository": revision.work_package.source_repository,
        "revision": revision.revision,
        "content_hash": revision.content_hash,
        "source_path": revision.source_path,
        "source_commit": revision.source_commit,
        "approved_by": revision.approved_by,
        "approved_at": revision.approved_at,
        "approval_event_id": revision.approval_event_id,
        "approval_ledger_commit": revision.approval_ledger_commit,
        "profile": revision.profile,
        "status_at_intake": revision.status_at_intake,
        "intake_source": revision.intake_source,
        "verification_mode": revision.verification_mode,
        "verification_limitations": revision.verification_limitations,
        "enforcement_snapshot": revision.enforcement_snapshot,
        "authority_fingerprint": revision.authority_fingerprint,
        "authority": intake_authority(session, revision.id),
        "follow_up": revision.follow_up,
        "change_record_id": revision.change_record_id,
        "originating_observation_id": revision.originating_observation_id,
        "registry_version": revision.registry_version,
        "registered_by": revision.registered_by,
        "registered_at": revision.registered_at,
        "acceptance_criteria": [
            PackageAcceptanceCriterionResponse.model_validate(criterion).model_dump(mode="json")
            for criterion in acceptance_criteria
        ],
    }


def _proposal_unit_payload(unit: DecompositionProposalUnit) -> dict[str, object]:
    payload = DecompositionProposalUnitResponse.model_validate(unit).model_dump(mode="json")
    payload["authority"] = normalize_authority(unit.authority).normalized()
    return payload


def _proposal_payloads(
    session: Session,
    proposals: Sequence[DecompositionProposal],
) -> dict[UUID, dict[str, object]]:
    if not proposals:
        return {}
    children = proposal_children(session, tuple(proposal.id for proposal in proposals))
    units_by_proposal = {
        proposal_id: [_proposal_unit_payload(unit) for unit in units]
        for proposal_id, units in children.units.items()
    }
    dependencies_by_proposal = {
        proposal_id: [
            DecompositionProposalDependencyResponse.model_validate(dependency).model_dump(
                mode="json"
            )
            for dependency in dependencies
        ]
        for proposal_id, dependencies in children.dependencies.items()
    }
    mappings_by_proposal = children.mappings
    retained_by_proposal = children.retained
    criterion_ids = {
        row.package_acceptance_criterion_id
        for rows in (*mappings_by_proposal.values(), *retained_by_proposal.values())
        for row in rows
    }
    criteria_by_id = {
        criterion_id: PackageAcceptanceCriterionResponse.model_validate(criterion)
        for criterion_id, criterion in acceptance_criteria_by_id(session, criterion_ids).items()
    }

    payloads: dict[UUID, dict[str, object]] = {}
    for proposal in proposals:
        payloads[proposal.id] = {
            "id": proposal.id,
            "work_package_revision_id": proposal.work_package_revision_id,
            "proposal_number": proposal.proposal_number,
            "state": proposal.state,
            "rationale": proposal.rationale,
            "proposed_by": proposal.proposed_by,
            "proposed_actor_role": proposal.proposed_actor_role,
            "proposed_at": proposal.proposed_at,
            "decided_by": proposal.decided_by,
            "decided_at": proposal.decided_at,
            "decision_reason": proposal.decision_reason,
            "created_work_unit_ids": proposal.created_work_unit_ids,
            "proposed_units": units_by_proposal.get(proposal.id, []),
            "dependencies": dependencies_by_proposal.get(proposal.id, []),
            "ac_mappings": [
                DecompositionProposalAcMappingResponse(
                    unit_key=mapping.unit_key,
                    package_acceptance_criterion=criteria_by_id[
                        mapping.package_acceptance_criterion_id
                    ],
                ).model_dump(mode="json")
                for mapping in mappings_by_proposal.get(proposal.id, [])
            ],
            "retained_acs": [
                DecompositionProposalRetainedAcResponse(
                    rationale=retained.rationale,
                    package_acceptance_criterion=criteria_by_id[
                        retained.package_acceptance_criterion_id
                    ],
                ).model_dump(mode="json")
                for retained in retained_by_proposal.get(proposal.id, [])
            ],
        }
    return payloads


def _proposed_unit(command: ProposedUnitCommand) -> ProposedUnit:
    return ProposedUnit(
        unit_key=command.unit_key,
        title=command.title,
        outcome=command.outcome,
        required_capability=command.required_capability,
        authority=normalize_authority(command.authority),
        authority_payload=command.authority,
        context_enrichment=command.context_enrichment,
        max_attempts=command.max_attempts,
    )
