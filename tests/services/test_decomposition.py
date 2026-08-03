import uuid
from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import authority_fingerprint, normalize_authority
from orchestrator.kernel.runner_authority import runner_command_authority_violation
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    ApprovedDecomposition,
    DecompositionProposal,
    DecompositionProposalAcMapping,
    DecompositionProposalDependency,
    DecompositionProposalUnit,
    Dependency,
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
    approve_decomposition_proposal,
    reject_decomposition_proposal,
    require_decomposition_revision,
    submit_decomposition_proposal,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.package_intake import (
    register_package_intake,
)
from orchestrator.services.packages import register_approved_unit
from tests.services.test_package_intake import (
    AUTHORITY,
    NOW,
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
                required_capability="repo.edit",
                authority=AUTHORITY,
            ),
            ProposedUnit(
                unit_key="unit-2",
                title="Implement tests",
                outcome="Service is covered by focused tests.",
                required_capability="repo.edit",
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
        ac_mappings=(AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key="unit-1"),),
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


def test_proposal_rejects_invalid_dependency_update_authority_before_persistence(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    invalid_payload = {
        "capabilities": {"repo.edit": "allowed", "command.run": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "change_class": "dependency-update",
        "constraints": {"allowed_commands": ["uv sync --locked"]},
    }
    violation = runner_command_authority_violation(normalize_authority(invalid_payload))
    assert violation is not None

    with pytest.raises(DomainError) as error:
        submit_decomposition_proposal(
            migrated_session,
            proposal_command(
                revision.id,
                ac_ids,
                proposed_units=(
                    ProposedUnit(
                        unit_key="unit-1",
                        title="Update dependency",
                        outcome="Dependency is updated.",
                        required_capability="repo.edit",
                        authority=AUTHORITY,
                        authority_payload=invalid_payload,
                    ),
                ),
                dependencies=(),
                ac_mappings=(AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key="unit-1"),),
            ),
            worker_actor(),
        )

    assert error.value.code == violation.code
    assert migrated_session.scalar(select(func.count()).select_from(DecompositionProposal)) == 0


def test_proposal_persists_the_same_authority_payload_it_validates(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    valid_payload = {
        "capabilities": {"repo.edit": "allowed", "command.run": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "change_class": "dependency-update",
        "constraints": {
            "allowed_commands": ["uv sync --locked"],
            "mutation_commands": ["uv sync --locked"],
        },
    }
    changed_payload = {**valid_payload, "constraints": {"allowed_commands": ["uv sync --locked"]}}

    class ChangingPayload(Mapping[str, object]):
        reads = 0

        def __iter__(self) -> Iterator[str]:
            type(self).reads += 1
            return iter(valid_payload if type(self).reads == 1 else changed_payload)

        def __len__(self) -> int:
            return len(valid_payload)

        def __getitem__(self, key: str) -> object:
            payload = valid_payload if type(self).reads == 1 else changed_payload
            return payload[key]

    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(
            revision.id,
            ac_ids,
            proposed_units=(
                ProposedUnit(
                    unit_key="unit-1",
                    title="Update dependency",
                    outcome="Dependency is updated.",
                    required_capability="repo.edit",
                    authority=AUTHORITY,
                    authority_payload=ChangingPayload(),
                ),
            ),
            dependencies=(),
            ac_mappings=(AcMapping(ac_id=str(ac_ids["AC-001"]), unit_key="unit-1"),),
        ),
        worker_actor(),
    )

    persisted = migrated_session.scalar(
        select(DecompositionProposalUnit).where(
            DecompositionProposalUnit.proposal_id == proposal.id
        )
    )
    assert persisted is not None
    assert persisted.authority["constraints"]["mutation_commands"] == ["uv sync --locked"]


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
                        required_capability="repo.edit",
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


def test_proposal_idempotency_conflicts_when_raw_unit_authority_differs(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    raw_authority = {
        "capabilities": {"repo.edit": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {
            "target_repository": "owner/repo-a",
            "allowed_commands": ["make check"],
        },
    }
    conflicting_raw_authority = {
        "capabilities": {"repo.edit": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {
            "target_repository": "owner/repo-b",
            "allowed_commands": ["make check"],
        },
    }
    command = proposal_command(
        revision.id,
        ac_ids,
        proposed_units=(
            ProposedUnit(
                unit_key="unit-1",
                title="Implement service",
                outcome="Service persists proposals.",
                required_capability="repo.edit",
                authority=normalize_authority(raw_authority),
                authority_payload=raw_authority,
                max_attempts=3,
            ),
            ProposedUnit(
                unit_key="unit-2",
                title="Implement tests",
                outcome="Service is covered by focused tests.",
                required_capability="repo.edit",
                authority=AUTHORITY,
            ),
        ),
        idempotency_key="proposal-raw-authority",
    )

    first = submit_decomposition_proposal(migrated_session, command, worker_actor())
    replay = submit_decomposition_proposal(migrated_session, command, worker_actor())

    # Constraint values are covered by the authority fingerprint, so these two envelopes
    # are no longer normalization-identical. Raw-payload replay identity is still what
    # catches differences the fingerprint cannot see (unknown field *values*).
    assert normalize_authority(raw_authority) != normalize_authority(conflicting_raw_authority)
    assert replay.id == first.id

    with pytest.raises(DomainError) as error:
        submit_decomposition_proposal(
            migrated_session,
            proposal_command(
                revision.id,
                ac_ids,
                proposed_units=(
                    ProposedUnit(
                        unit_key="unit-1",
                        title="Implement service",
                        outcome="Service persists proposals.",
                        required_capability="repo.edit",
                        authority=normalize_authority(conflicting_raw_authority),
                        authority_payload=conflicting_raw_authority,
                        max_attempts=3,
                    ),
                    ProposedUnit(
                        unit_key="unit-2",
                        title="Implement tests",
                        outcome="Service is covered by focused tests.",
                        required_capability="repo.edit",
                        authority=AUTHORITY,
                    ),
                ),
                idempotency_key="proposal-raw-authority",
            ),
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


def test_worker_cannot_approve_decomposition(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids),
        worker_actor(),
    )

    with pytest.raises(DomainError) as error:
        approve_decomposition_proposal(
            migrated_session,
            proposal.id,
            actor=worker_actor(),
            reason="Ship it.",
            idempotency_key="proposal-approve-worker",
        )

    assert error.value.code == "human_actor_required"


def test_human_approval_creates_draft_units_and_dependencies(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(
            revision.id,
            ac_ids,
            dependencies=(
                ProposedDependency(
                    source_unit_key="unit-2",
                    kind="work_unit",
                    required_state_or_condition="completed",
                    target_unit_key="unit-1",
                ),
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

    approved = approve_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Approved for draft activation.",
        idempotency_key="proposal-approve-1",
    )

    units = tuple(
        migrated_session.scalars(
            select(WorkUnit)
            .where(WorkUnit.work_package_revision_id == revision.id)
            .order_by(WorkUnit.unit_key)
        )
    )
    dependencies = tuple(
        migrated_session.scalars(
            select(Dependency).order_by(
                Dependency.work_unit_id,
                Dependency.depends_on_work_unit_id,
                Dependency.external_ref,
            )
        )
    )
    activated = migrated_session.scalar(
        select(ApprovedDecomposition).where(ApprovedDecomposition.proposal_id == proposal.id)
    )
    dependency_events = tuple(
        migrated_session.scalars(
            select(Event).where(Event.action == "dependency.registered").order_by(Event.id)
        )
    )
    approval_events = tuple(
        migrated_session.scalars(
            select(Event).where(Event.action == "decomposition.approved").order_by(Event.id)
        )
    )

    assert approved.id == proposal.id
    assert approved.state == "approved"
    assert approved.decided_by == "human-1"
    assert approved.decision_reason == "Approved for draft activation."
    assert approved.created_work_unit_ids == {unit.unit_key: str(unit.id) for unit in units}
    assert [unit.state for unit in units] == ["draft", "draft"]
    assert [unit.decomposition_approved_by for unit in units] == ["human-1", "human-1"]
    dependencies_by_kind = {dependency.kind: dependency for dependency in dependencies}
    assert (
        dependencies_by_kind["external_system"].work_unit_id,
        dependencies_by_kind["external_system"].depends_on_work_unit_id,
        dependencies_by_kind["external_system"].external_ref,
    ) == (units[0].id, None, "ci/build/123")
    assert (
        dependencies_by_kind["work_unit"].work_unit_id,
        dependencies_by_kind["work_unit"].depends_on_work_unit_id,
        dependencies_by_kind["work_unit"].external_ref,
    ) == (units[1].id, units[0].id, None)
    assert activated is not None
    assert activated.work_package_revision_id == revision.id
    assert activated.approved_by == "human-1"
    assert [event.actor_id for event in dependency_events] == ["human-1", "human-1"]
    assert {event.subject_id for event in dependency_events} == {units[0].id, units[1].id}
    assert len(approval_events) == 1
    assert approval_events[0].payload["created_work_unit_ids"] == approved.created_work_unit_ids


def test_package_cli_revision_rejects_forged_activation_source(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)

    with pytest.raises(DomainError) as error:
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="unit-forged",
            title="Forged unit",
            outcome="Should not be created.",
            required_capability="repo.edit",
            authority=AUTHORITY,
            approved_by="human-1",
            approved_at=NOW,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            activation_source="approved_decomposition",
        )

    assert error.value.code == "decomposition_approval_required"


def test_package_cli_revision_rejects_forged_approved_decomposition_id(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)

    with pytest.raises(DomainError) as error:
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="unit-forged",
            title="Forged unit",
            outcome="Should not be created.",
            required_capability="repo.edit",
            authority=AUTHORITY,
            approved_by="human-1",
            approved_at=NOW,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            activation_source="approved_decomposition",
            approved_decomposition_id=uuid.uuid4(),
        )

    assert error.value.code == "decomposition_approval_required"


def test_package_cli_revision_rejects_extra_unit_for_active_approved_decomposition(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids),
        worker_actor(),
    )
    approve_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Approve the proposed units.",
        idempotency_key="proposal-approve-extra-unit-guard",
    )
    approved = migrated_session.scalar(
        select(ApprovedDecomposition).where(ApprovedDecomposition.proposal_id == proposal.id)
    )
    assert approved is not None

    with pytest.raises(DomainError) as error:
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="unit-extra",
            title="Extra unit",
            outcome="This unit was not approved.",
            required_capability="repo.edit",
            authority=AUTHORITY,
            approved_by="human-1",
            approved_at=approved.approved_at,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            activation_source="approved_decomposition",
            approved_decomposition_id=approved.id,
        )

    assert error.value.code == "decomposition_approval_required"


def test_approved_decomposition_preserves_raw_unit_authority_payload(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    raw_authority = {
        "capabilities": {
            "repo.read": "allowed",
            "repo.edit": "allowed",
            "command.run": "allowed",
        },
        "budgets": {"max_attempts": 2, "max_llm_calls": 5},
        "constraints": {
            "target_repository": "AlobarQuest/orchestrator",
            "allowed_commands": ["make check"],
        },
    }
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(
            revision.id,
            ac_ids,
            proposed_units=(
                ProposedUnit(
                    unit_key="unit-1",
                    title="Implement service",
                    outcome="Service persists proposals.",
                    required_capability="repo.edit",
                    authority=normalize_authority(raw_authority),
                    authority_payload=raw_authority,
                    max_attempts=2,
                ),
                ProposedUnit(
                    unit_key="unit-2",
                    title="Implement tests",
                    outcome="Service is covered by focused tests.",
                    required_capability="repo.edit",
                    authority=AUTHORITY,
                ),
            ),
        ),
        worker_actor(),
    )

    approve_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Approve the proposed units.",
        idempotency_key="proposal-approve-raw-authority",
    )
    unit = migrated_session.scalar(select(WorkUnit).where(WorkUnit.unit_key == "unit-1"))

    assert unit is not None
    # The author's raw payload survives verbatim (it is not replaced by normalized()),
    # carrying only the server-owned work_unit_id stamp.
    assert unit.authority == {
        **raw_authority,
        "constraints": {**raw_authority["constraints"], "work_unit_id": str(unit.id)},
    }
    assert unit.authority_fingerprint == authority_fingerprint(normalize_authority(unit.authority))


def test_second_approval_is_rejected(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    first = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids),
        worker_actor(),
    )
    second = submit_decomposition_proposal(
        migrated_session,
        proposal_command(
            revision.id,
            ac_ids,
            rationale="Alternative split.",
            idempotency_key="proposal-2",
        ),
        worker_actor(),
    )
    approve_decomposition_proposal(
        migrated_session,
        first.id,
        actor=human_actor(),
        reason="Approve the first plan.",
        idempotency_key="proposal-approve-first",
    )

    with pytest.raises(DomainError) as error:
        approve_decomposition_proposal(
            migrated_session,
            second.id,
            actor=human_actor(),
            reason="Approve the second plan.",
            idempotency_key="proposal-approve-second",
        )

    assert error.value.code == "decomposition_already_approved"
    assert migrated_session.get(DecompositionProposal, second.id) is not None
    assert migrated_session.scalar(select(func.count()).select_from(WorkUnit)) == 2


def test_reject_decomposition_records_decision_without_units(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids),
        worker_actor(),
    )

    rejected = reject_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Missing split rationale.",
        idempotency_key="proposal-reject-1",
    )
    events = tuple(
        migrated_session.scalars(
            select(Event).where(Event.subject_id == proposal.id).order_by(Event.id)
        )
    )

    assert rejected.state == "rejected"
    assert rejected.decided_by == "human-1"
    assert rejected.decision_reason == "Missing split rationale."
    assert rejected.created_work_unit_ids is None
    assert migrated_session.scalar(select(func.count()).select_from(WorkUnit)) == 0
    assert {event.action for event in events} == {
        "decomposition.proposed",
        "decomposition.rejected",
    }


def test_reject_decomposition_idempotency_replays_and_conflicts(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids),
        worker_actor(),
    )

    first = reject_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Missing split rationale.",
        idempotency_key="proposal-reject-idempotent",
    )
    second = reject_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Missing split rationale.",
        idempotency_key="proposal-reject-idempotent",
    )

    assert second.id == first.id
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.idempotency_key == "proposal-reject-idempotent")
        )
        == 1
    )

    with pytest.raises(DomainError) as error:
        reject_decomposition_proposal(
            migrated_session,
            proposal.id,
            actor=human_actor(),
            reason="Different reason.",
            idempotency_key="proposal-reject-idempotent",
        )

    assert error.value.code == "idempotency_conflict"


def test_require_revision_records_decision_without_units(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids),
        worker_actor(),
    )

    revised = require_decomposition_revision(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Please break out the dependency chain more clearly.",
        idempotency_key="proposal-revision-1",
    )
    events = tuple(
        migrated_session.scalars(
            select(Event).where(Event.subject_id == proposal.id).order_by(Event.id)
        )
    )

    assert revised.state == "revision_required"
    assert revised.decided_by == "human-1"
    assert revised.decision_reason == "Please break out the dependency chain more clearly."
    assert revised.created_work_unit_ids is None
    assert migrated_session.scalar(select(func.count()).select_from(WorkUnit)) == 0
    assert {event.action for event in events} == {
        "decomposition.proposed",
        "decomposition.revision_required",
    }


def test_require_revision_idempotency_replays_and_conflicts(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids),
        worker_actor(),
    )

    first = require_decomposition_revision(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Please break out dependencies.",
        idempotency_key="proposal-revision-idempotent",
    )
    second = require_decomposition_revision(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Please break out dependencies.",
        idempotency_key="proposal-revision-idempotent",
    )

    assert second.id == first.id
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.idempotency_key == "proposal-revision-idempotent")
        )
        == 1
    )

    with pytest.raises(DomainError) as error:
        require_decomposition_revision(
            migrated_session,
            proposal.id,
            actor=human_actor(),
            reason="Different reason.",
            idempotency_key="proposal-revision-idempotent",
        )

    assert error.value.code == "idempotency_conflict"


def test_approval_idempotency_replays_without_duplicate_units(
    migrated_session: Session,
) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids),
        worker_actor(),
    )

    first = approve_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Approved for draft activation.",
        idempotency_key="proposal-approve-idempotent",
    )
    second = approve_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Approved for draft activation.",
        idempotency_key="proposal-approve-idempotent",
    )

    assert second.id == first.id
    assert migrated_session.scalar(select(func.count()).select_from(WorkUnit)) == 2
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.idempotency_key == "proposal-approve-idempotent")
        )
        == 1
    )


def test_rejects_approval_when_proposal_not_proposed(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids),
        worker_actor(),
    )
    reject_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Need a revision before approval.",
        idempotency_key="proposal-reject-before-approval",
    )

    with pytest.raises(DomainError) as error:
        approve_decomposition_proposal(
            migrated_session,
            proposal.id,
            actor=human_actor(),
            reason="Approve anyway.",
            idempotency_key="proposal-approve-after-reject",
        )

    assert error.value.code == "decomposition_proposal_state_invalid"


def _proposal_units(
    session: Session, proposal_id: uuid.UUID
) -> dict[str, DecompositionProposalUnit]:
    rows = session.scalars(
        select(DecompositionProposalUnit).where(
            DecompositionProposalUnit.proposal_id == proposal_id
        )
    )
    return {row.unit_key: row for row in rows}


@pytest.mark.parametrize(
    ("constraints", "code"),
    [
        ({}, "authority_allowed_commands_invalid"),
        ({"allowed_commands": ["make check"]}, "authority_mutation_commands_invalid"),
        (
            {
                "allowed_commands": ["uv sync", "make check"],
                "mutation_commands": ["uv lock"],
            },
            "authority_mutation_command_not_allowed",
        ),
    ],
)
def test_dependency_update_authority_rejects_non_executable_contract(
    constraints: dict[str, object], code: str
) -> None:
    envelope = normalize_authority(
        {
            "change_class": "dependency-update",
            "capabilities": {"repo.edit": "allowed", "command.run": "allowed"},
            "constraints": constraints,
        }
    )

    violation = runner_command_authority_violation(envelope)

    assert violation is not None
    assert violation.code == code


@pytest.mark.parametrize(
    ("constraints", "code"),
    [
        (
            {"allowed_commands": [""], "mutation_commands": ["uv lock"]},
            "authority_allowed_commands_invalid",
        ),
        (
            {"allowed_commands": ["uv lock"], "mutation_commands": ["  "]},
            "authority_mutation_commands_invalid",
        ),
        (
            {"allowed_commands": [1], "mutation_commands": ["uv lock"]},
            "authority_allowed_commands_invalid",
        ),
        (
            {"allowed_commands": ["uv lock"], "mutation_commands": [True]},
            "authority_mutation_commands_invalid",
        ),
        (
            {"allowed_commands": {"command": "uv lock"}, "mutation_commands": ["uv lock"]},
            "authority_allowed_commands_invalid",
        ),
        (
            {"allowed_commands": ["uv lock"], "mutation_commands": ("uv lock",)},
            "authority_mutation_commands_invalid",
        ),
    ],
)
def test_dependency_update_authority_rejects_invalid_command_entries(
    constraints: dict[str, object],
    code: str,
) -> None:
    envelope = normalize_authority(
        {
            "change_class": "dependency-update",
            "capabilities": {"repo.edit": "allowed", "command.run": "allowed"},
            "constraints": constraints,
        }
    )

    violation = runner_command_authority_violation(envelope)

    assert violation is not None
    assert violation.code == code


def test_dependency_update_authority_requires_command_run() -> None:
    envelope = normalize_authority(
        {
            "change_class": "dependency-update",
            "capabilities": {"repo.edit": "allowed"},
            "constraints": {},
        }
    )

    violation = runner_command_authority_violation(envelope)

    assert violation is not None
    assert violation.code == "authority_command_run_required"


@pytest.mark.parametrize(
    ("change_class", "repo_edit_level"),
    [("repository-change", "allowed"), ("dependency-update", "prohibited")],
)
def test_dependency_update_authority_ignores_out_of_scope_envelopes(
    change_class: str,
    repo_edit_level: str,
) -> None:
    """Without command.run there is nothing for the command contract to bind."""
    envelope = normalize_authority(
        {
            "change_class": change_class,
            "capabilities": {"repo.edit": repo_edit_level},
            "constraints": {},
        }
    )

    assert runner_command_authority_violation(envelope) is None


def test_command_authority_requires_allowed_commands_for_every_change_class() -> None:
    """WS-P2.33: command.run without allowed_commands dies at the runner whatever
    the change class, so admission refuses it for every class, not only
    dependency-update."""
    envelope = normalize_authority(
        {
            "change_class": "maintenance-remediation",
            "capabilities": {"repo.edit": "allowed", "command.run": "allowed"},
            "constraints": {},
        }
    )

    violation = runner_command_authority_violation(envelope)

    assert violation is not None
    assert violation.code == "authority_allowed_commands_invalid"


def test_edit_shaped_envelope_needs_no_mutation_commands() -> None:
    """WS-P2.33: the coding agent produces the diff; the honest envelope omits
    mutation_commands and admission accepts the omission."""
    envelope = normalize_authority(
        {
            "change_class": "maintenance-remediation",
            "capabilities": {"repo.edit": "allowed", "command.run": "allowed"},
            "constraints": {"allowed_commands": ["uv sync", "make check"]},
        }
    )

    assert runner_command_authority_violation(envelope) is None


def test_dependency_update_authority_accepts_ordered_valid_command_lists() -> None:
    envelope = normalize_authority(
        {
            "change_class": "dependency-update",
            "capabilities": {"repo.edit": "allowed", "command.run": "allowed"},
            "constraints": {
                "allowed_commands": ["uv add httpx", "uv sync --locked", "uv run make check"],
                "mutation_commands": ["uv add httpx"],
            },
        }
    )

    assert runner_command_authority_violation(envelope) is None


def _fanout_units() -> tuple[ProposedUnit, ...]:
    def unit(unit_key: str, repository: str) -> ProposedUnit:
        payload = {
            "capabilities": {"repo.edit": "allowed"},
            "budgets": {"max_attempts": 3, "max_llm_calls": 4},
            "constraints": {
                "target_repository": repository,
                "allowed_commands": ["make check"],
            },
        }
        return ProposedUnit(
            unit_key=unit_key,
            title=f"Bump dependency in {repository}",
            outcome="Dependency updated and checks green.",
            required_capability="repo.edit",
            authority=normalize_authority(payload),
            authority_payload=payload,
        )

    return (unit("unit-1", "AlobarQuest/brain"), unit("unit-2", "AlobarQuest/security-standards"))


def test_proposal_stamps_each_unit_authority_with_its_future_work_unit_id(
    migrated_session: Session,
) -> None:
    """The runner refuses an envelope that does not name its own unit, and an author
    cannot know the UUID. The orchestrator must stamp it at propose time, so the
    approver reviews the same envelope the runner will receive."""
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)

    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids, proposed_units=_fanout_units(), dependencies=()),
        worker_actor(),
    )

    units = _proposal_units(migrated_session, proposal.id)
    for unit_key, row in units.items():
        expected = str(uuid.uuid5(proposal.id, unit_key))
        assert row.authority["constraints"]["work_unit_id"] == expected
        # stamping must not disturb the author's other constraints
        assert row.authority["constraints"]["allowed_commands"] == ["make check"]

    # each unit binds to a different repo AND a different id -> distinct fingerprints
    assert units["unit-1"].authority_fingerprint != units["unit-2"].authority_fingerprint


def test_approved_unit_authority_names_its_own_id(migrated_session: Session) -> None:
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    proposal = submit_decomposition_proposal(
        migrated_session,
        proposal_command(revision.id, ac_ids, proposed_units=_fanout_units(), dependencies=()),
        worker_actor(),
    )
    proposal_units = _proposal_units(migrated_session, proposal.id)

    approve_decomposition_proposal(
        migrated_session,
        proposal.id,
        actor=human_actor(),
        reason="Approved for draft activation.",
        idempotency_key="proposal-approve-fanout",
    )

    units = tuple(
        migrated_session.scalars(
            select(WorkUnit).where(WorkUnit.work_package_revision_id == revision.id)
        )
    )
    assert len(units) == 2
    for unit in units:
        assert unit.authority["constraints"]["work_unit_id"] == str(unit.id)
        # the fingerprint the human approves is the one the proposal displayed
        assert unit.authority_fingerprint == proposal_units[unit.unit_key].authority_fingerprint

    repositories = {unit.authority["constraints"]["target_repository"] for unit in units}
    assert repositories == {"AlobarQuest/brain", "AlobarQuest/security-standards"}


def test_proposal_rejects_author_supplied_work_unit_id(migrated_session: Session) -> None:
    """The work_unit_id constraint is server-owned; an author cannot know it, so a
    supplied value is always a mistake and must never be silently overwritten."""
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    payload = {
        "capabilities": {"repo.edit": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {
            "target_repository": "AlobarQuest/brain",
            "work_unit_id": str(uuid.uuid4()),
        },
    }

    with pytest.raises(DomainError) as error:
        submit_decomposition_proposal(
            migrated_session,
            proposal_command(
                revision.id,
                ac_ids,
                dependencies=(),
                proposed_units=(
                    ProposedUnit(
                        unit_key="unit-1",
                        title="Bump dependency",
                        outcome="Dependency updated.",
                        required_capability="repo.edit",
                        authority=normalize_authority(payload),
                        authority_payload=payload,
                    ),
                    ProposedUnit(
                        unit_key="unit-2",
                        title="Implement tests",
                        outcome="Covered by tests.",
                        required_capability="repo.edit",
                        authority=AUTHORITY,
                    ),
                ),
            ),
            worker_actor(),
        )

    assert error.value.code == "authority_work_unit_id_forbidden"


def test_proposal_rejects_malformed_conformance(migrated_session: Session) -> None:
    """Conformance is fingerprinted, so a malformed claim must be rejected at proposal
    time rather than approved as an opaque unknown field."""
    revision = register_intaken_revision(migrated_session)
    ac_ids = package_ac_ids(migrated_session, revision.id)
    payload = {
        "capabilities": {"repo.edit": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {"target_repository": "AlobarQuest/brain"},
        "conformance": {"status": "green", "standards_touched": ["project"]},
    }

    with pytest.raises(DomainError) as error:
        submit_decomposition_proposal(
            migrated_session,
            proposal_command(
                revision.id,
                ac_ids,
                dependencies=(),
                proposed_units=(
                    ProposedUnit(
                        unit_key="unit-1",
                        title="Bump dependency",
                        outcome="Dependency updated.",
                        required_capability="repo.edit",
                        authority=normalize_authority(payload),
                        authority_payload=payload,
                    ),
                    ProposedUnit(
                        unit_key="unit-2",
                        title="Implement tests",
                        outcome="Covered by tests.",
                        required_capability="repo.edit",
                        authority=AUTHORITY,
                    ),
                ),
            ),
            worker_actor(),
        )

    assert error.value.code == "authority_conformance_invalid"
    assert "accepted_standards" in error.value.message
