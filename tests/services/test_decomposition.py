import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    DecompositionProposalAcMapping,
    DecompositionProposalDependency,
    Event,
    PackageAcceptanceCriterion,
    WorkUnit,
)
from orchestrator.services.decomposition import (
    AcMapping,
    DecompositionProposalCommand,
    ProposedDependency,
    ProposedUnit,
    RetainedAc,
    submit_decomposition_proposal,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.package_intake import (
    register_package_intake,
)
from tests.services.test_package_intake import (
    AUTHORITY,
    acceptance_criterion,
    human_actor,
    intake_command,
)


def worker_actor() -> ActorContext:
    return ActorContext("worker-1", ActorRole.WORKER)


def register_intaken_revision(session: Session):
    return register_package_intake(
        session,
        intake_command(
            acceptance_criteria=(
                acceptance_criterion("AC-001"),
                acceptance_criterion("AC-002"),
            )
        ),
        human_actor(),
    )


def package_ac_ids(session: Session, revision_id: uuid.UUID) -> dict[str, uuid.UUID]:
    rows = tuple(
        session.scalars(
            select(PackageAcceptanceCriterion).where(
                PackageAcceptanceCriterion.work_package_revision_id == revision_id
            )
        )
    )
    return {row.ac_id: row.id for row in rows}


def proposal_command(revision_id: uuid.UUID, ac_ids: dict[str, uuid.UUID], **overrides: object):
    base = DecompositionProposalCommand(
        work_package_revision_id=revision_id,
        rationale="Split by independent delivery path.",
        proposed_units=(
            ProposedUnit(
                unit_key="unit-1",
                title="Implement service",
                outcome="Service persists proposals.",
                required_capability="repository_write",
                authority=AUTHORITY,
            ),
            ProposedUnit(
                unit_key="unit-2",
                title="Implement tests",
                outcome="Service is covered by focused tests.",
                required_capability="repository_write",
                authority=AUTHORITY,
            ),
        ),
        dependencies=(
            ProposedDependency(
                source_unit_key="unit-2",
                kind="work_unit",
                required_state_or_condition="completed",
                target_unit_key="unit-1",
            ),
        ),
        ac_mappings=(
            AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key="unit-1"),
        ),
        retained_acs=(
            RetainedAc(
                ac_id=str(ac_ids["AC-002"]),
                rationale="Reviewed at proposal approval time as a package-level gate.",
            ),
        ),
        idempotency_key="proposal-1",
    )
    return DecompositionProposalCommand(**{**base.__dict__, **overrides})


def test_proposal_submission_creates_no_work_units(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)

    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids),
        worker_actor(),
    )

    mappings = tuple(
        migrated_session.scalars(
            select(DecompositionProposalAcMapping).where(
                DecompositionProposalAcMapping.proposal_id == proposal.id
            )
        )
    )
    events = tuple(
        migrated_session.scalars(
            select(Event).where(Event.subject_id == proposal.id).order_by(Event.id)
        )
    )

    assert proposal.proposal_number == 1
    assert proposal.state == "proposed"
    assert migrated_session.scalar(select(func.count()).select_from(WorkUnit)) == 0
    assert len(mappings) == 1
    assert [event.action for event in events] == ["decomposition.proposed"]


def test_proposal_requires_total_ac_disposition(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)

    with pytest.raises(DomainError) as error:
        submit_decomposition_proposal(
            migrated_session,
            proposal_command(revision.id, ac_ids, retained_acs=()),
            worker_actor(),
        )

    assert error.value.code == "decomposition_proposal_ac_coverage_invalid"


def test_proposal_rejects_internal_dependency_cycle(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)

    with pytest.raises(DomainError) as error:
        submit_decomposition_proposal(
            migrated_session,
            proposal_command(
                revision.id,
                ac_ids,
                dependencies=(
                    ProposedDependency(
                        source_unit_key="unit-1",
                        kind="work_unit",
                        required_state_or_condition="completed",
                        target_unit_key="unit-2",
                    ),
                    ProposedDependency(
                        source_unit_key="unit-2",
                        kind="work_unit",
                        required_state_or_condition="completed",
                        target_unit_key="unit-1",
                    ),
                ),
            ),
            worker_actor(),
        )

    assert error.value.code == "dependency_cycle"


def test_proposal_rejects_non_intaken_revision(migrated_session: Session) -> None:
    from tests.services.test_package_registration import register_test_revision

    revision = register_test_revision(migrated_session)

    with pytest.raises(DomainError) as error:
        submit_decomposition_proposal(
            migrated_session,
            DecompositionProposalCommand(
                work_package_revision_id=revision.id,
                rationale="Split it.",
                proposed_units=(
                    ProposedUnit(
                        unit_key="unit-1",
                        title="Implement service",
                        outcome="Service exists.",
                        required_capability="repository_write",
                        authority=AUTHORITY,
                    ),
                ),
                dependencies=(),
                ac_mappings=(),
                retained_acs=(),
                idempotency_key="proposal-non-intaken",
            ),
            worker_actor(),
        )

    assert error.value.code == "revision_not_intaken"


def test_proposal_idempotency_replays_exact_command(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    command = proposal_command(revision.id, ac_ids)

    first = submit_decomposition_proposal(migrated_session, command, worker_actor())
    second = submit_decomposition_proposal(migrated_session, command, worker_actor())

    events = tuple(
        migrated_session.scalars(
            select(Event).where(Event.idempotency_key == command.idempotency_key)
        )
    )

    assert second.id == first.id
    assert second.proposal_number == 1
    assert len(events) == 1


def test_proposal_idempotency_conflict_rejected(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    command = proposal_command(revision.id, ac_ids)
    submit_decomposition_proposal(migrated_session, command, worker_actor())

    with pytest.raises(DomainError) as error:
        submit_decomposition_proposal(
            migrated_session,
            proposal_command(revision.id, ac_ids, rationale="Changed rationale."),
            worker_actor(),
        )

    assert error.value.code == "idempotency_conflict"


def test_proposal_rejects_unknown_ac_mapping(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)

    with pytest.raises(DomainError) as error:
        submit_decomposition_proposal(
            migrated_session,
            proposal_command(
                revision.id,
                ac_ids,
                ac_mappings=(AcMapping(ac_id=str(uuid.uuid4()), unit_key="unit-1"),),
                retained_acs=(
                    RetainedAc(
                        ac_id=str(ac_ids["AC-002"]),
                        rationale="Still retained.",
                    ),
                ),
            ),
            worker_actor(),
        )

    assert error.value.code == "package_acceptance_criterion_not_found"


def test_proposal_rejects_unknown_unit_dependency(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)

    with pytest.raises(DomainError) as error:
        submit_decomposition_proposal(
            migrated_session,
            proposal_command(
                revision.id,
                ac_ids,
                dependencies=(
                    ProposedDependency(
                        source_unit_key="unit-2",
                        kind="work_unit",
                        required_state_or_condition="completed",
                        target_unit_key="unit-missing",
                    ),
                ),
            ),
            worker_actor(),
        )

    assert error.value.code == "dependency_target_not_found"


def test_proposal_records_external_dependency(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)

    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(
            revision.id,
            ac_ids,
            dependencies=(
                ProposedDependency(
                    source_unit_key="unit-1",
                    kind="external_system",
                    required_state_or_condition="ci green",
                    external_ref="ci/build/123",
                ),
            ),
        ),
        worker_actor(),
    )

    dependencies = tuple(
        migrated_session.scalars(
            select(DecompositionProposalDependency).where(
                DecompositionProposalDependency.proposal_id == proposal.id
            )
        )
    )

    assert [
        (dependency.kind, dependency.target_unit_key, dependency.external_ref)
        for dependency in dependencies
    ] == [("external_system", None, "ci/build/123")]
