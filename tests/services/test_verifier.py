import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Adjudication,
    ApprovedDecomposition,
    DecompositionProposal,
    DecompositionProposalAcMapping,
    Event,
    Evidence,
    PackageAcceptanceCriterion,
    WorkUnit,
)
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.evidence import append_evidence
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.verifier import VerifyCommand, verify_work_unit
from tests.services.test_package_registration import AUTHORITY

NOW = datetime(2026, 7, 8, tzinfo=UTC)
HUMAN = ActorContext("human-1", ActorRole.HUMAN)
WORKER = ActorContext("worker-1", ActorRole.WORKER)
VERIFIER = ActorContext("verifier-1", ActorRole.VERIFIER)


def mapped_submitted_unit(
    session: Session,
    *,
    key: str,
    evidence_type: str = "pytest",
    ac_id: str = "ac-1",
) -> WorkUnit:
    revision = register_revision(
        session,
        package_id=f"pkg-{key}",
        source_repository="owner/repo",
        revision=1,
        content_hash=f"sha256:{key}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": [ac_id]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repository_write",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    criterion = PackageAcceptanceCriterion(
        work_package_revision_id=revision.id,
        ac_id=ac_id,
        condition=f"{ac_id} passes",
        evidence_type=evidence_type,
        evidence="recorded evidence",
        approver="verifier",
    )
    proposal = DecompositionProposal(
        work_package_revision_id=revision.id,
        proposal_number=1,
        state="approved",
        rationale="map unit ACs",
        proposed_by=HUMAN.actor_id,
        proposed_actor_role=HUMAN.role,
        decided_by=HUMAN.actor_id,
        decided_at=NOW,
        idempotency_key=f"{key}-proposal",
    )
    session.add_all([criterion, proposal])
    session.flush()
    session.add(
        ApprovedDecomposition(
            work_package_revision_id=revision.id,
            proposal_id=proposal.id,
            approved_by=HUMAN.actor_id,
            approved_at=NOW,
        )
    )
    session.add(
        DecompositionProposalAcMapping(
            proposal_id=proposal.id,
            package_acceptance_criterion_id=criterion.id,
            unit_key=unit.unit_key,
        )
    )
    unit.state = WorkUnitState.SUBMITTED
    session.commit()
    return unit


def record_worker_evidence(
    session: Session,
    unit: WorkUnit,
    *,
    ac_id: str = "ac-1",
    evidence_type: str = "pytest",
    payload: dict[str, object],
    idempotency_key: str = "worker-evidence",
) -> Evidence:
    unit.state = WorkUnitState.READY
    session.commit()
    grant = claim_unit(session, unit.id, WORKER, f"{idempotency_key}-claim")
    assert isinstance(grant, LeaseGrant)
    transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.EXECUTING,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key=f"{idempotency_key}-start",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
    )
    evidence = append_evidence(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id=ac_id,
        attempt=grant.attempt,
        actor=WORKER,
        lease_token=grant.lease_token,
        evidence_type=evidence_type,
        stable_ref=f"artifact://{idempotency_key}",
        payload=payload,
        source_revision="abc123",
        idempotency_key=idempotency_key,
    )
    assert isinstance(evidence, Evidence)
    transition_unit(
        session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.SUBMITTED,
            actor=WORKER,
            expected_version=unit.version,
            idempotency_key=f"{idempotency_key}-submit",
            attempt=grant.attempt,
            lease_token=grant.lease_token,
        ),
    )
    return evidence


def test_verifier_passes_and_completes_when_all_mapped_criteria_pass(
    migrated_session: Session,
) -> None:
    unit = mapped_submitted_unit(migrated_session, key="verify-pass")
    evidence = record_worker_evidence(
        migrated_session,
        unit,
        payload={"exit_code": 0},
    )

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-pass",
        ),
    )

    assert result.result == "completed"
    assert result.state is WorkUnitState.COMPLETED
    assert result.evaluations[0].ac_id == "ac-1"
    assert result.evaluations[0].outcome == "passed"
    adjudication = migrated_session.get(Adjudication, result.evaluations[0].adjudication_id)
    assert adjudication is not None
    assert adjudication.evidence_id == evidence.id


def test_verifier_fails_closed_for_failed_evidence(migrated_session: Session) -> None:
    unit = mapped_submitted_unit(migrated_session, key="verify-fail")
    record_worker_evidence(migrated_session, unit, payload={"exit_code": 1})

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-fail",
        ),
    )

    assert result.result == "revision_required"
    assert result.state is WorkUnitState.REVISION_REQUIRED
    assert result.evaluations[0].outcome == "failed"
    assert result.evaluations[0].finding_evidence_id is not None


def test_verifier_fails_closed_for_missing_deterministic_evidence(
    migrated_session: Session,
) -> None:
    unit = mapped_submitted_unit(migrated_session, key="verify-missing")

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-missing",
        ),
    )

    assert result.result == "revision_required"
    assert result.state is WorkUnitState.REVISION_REQUIRED
    assert result.evaluations[0].status == "failed_closed"
    assert result.evaluations[0].finding_evidence_id is not None


def test_verifier_routes_judgment_criteria_to_awaiting_review(
    migrated_session: Session,
) -> None:
    unit = mapped_submitted_unit(
        migrated_session,
        key="verify-judgment",
        evidence_type="human.review",
    )

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-judgment",
        ),
    )

    assert result.result == "awaiting_review"
    assert result.state is WorkUnitState.AWAITING_REVIEW
    assert result.evaluations[0].status == "judgment_required"
    assert result.evaluations[0].adjudication_id is None


def test_verifier_replay_does_not_duplicate_rows(migrated_session: Session) -> None:
    unit = mapped_submitted_unit(migrated_session, key="verify-replay")
    record_worker_evidence(migrated_session, unit, payload={"exit_code": 0})
    command = VerifyCommand(
        unit_id=unit.id,
        actor=VERIFIER,
        expected_version=unit.version,
        idempotency_key="verify-replay",
    )

    first = verify_work_unit(migrated_session, command)
    replay = verify_work_unit(migrated_session, command)

    transition_count = migrated_session.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.idempotency_key == "verify-replay:transition:completed")
    )
    assert replay.result == first.result
    assert replay.version == first.version
    assert replay.evaluations[0].adjudication_id == first.evaluations[0].adjudication_id
    assert transition_count == 1


def test_worker_cannot_invoke_verifier(migrated_session: Session) -> None:
    unit = mapped_submitted_unit(migrated_session, key="verify-worker")

    with pytest.raises(DomainError) as error:
        verify_work_unit(
            migrated_session,
            VerifyCommand(
                unit_id=unit.id,
                actor=WORKER,
                expected_version=unit.version,
                idempotency_key="verify-worker",
            ),
        )

    assert error.value.code == "role_forbidden"


def test_completion_guard_still_rejects_without_satisfying_adjudication(
    migrated_session: Session,
) -> None:
    unit = mapped_submitted_unit(migrated_session, key="verify-guard")

    with pytest.raises(DomainError) as error:
        transition_unit(
            migrated_session,
            TransitionCommand(
                unit_id=unit.id,
                target=WorkUnitState.COMPLETED,
                actor=VERIFIER,
                expected_version=unit.version,
                idempotency_key="verify-guard-complete",
            ),
        )

    assert error.value.code == "completion_incomplete"


def test_verifier_rejects_malformed_revision_without_persisted_criteria(
    migrated_session: Session,
) -> None:
    revision = register_revision(
        migrated_session,
        package_id="pkg-verify-missing-criteria-row",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:verify-missing-criteria-row",
        source_path="intent.md",
        source_commit="abc123",
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["ac-1"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="verify-missing-criteria-row",
        title="verify-missing-criteria-row",
        outcome="verified",
        required_capability="repository_write",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by=HUMAN.actor_id,
        approved_at=NOW,
        actor_id=HUMAN.actor_id,
        actor_role=HUMAN.role,
    )
    unit.state = WorkUnitState.SUBMITTED
    migrated_session.commit()

    with pytest.raises(DomainError) as error:
        verify_work_unit(
            migrated_session,
            VerifyCommand(
                unit_id=unit.id,
                actor=VERIFIER,
                expected_version=unit.version,
                idempotency_key="verify-missing-criteria-row",
            ),
        )

    assert error.value.code == "verification_subject_invalid"
