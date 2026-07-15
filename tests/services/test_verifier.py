import uuid

import pytest
from sqlalchemy import Engine, event, func, select, update
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.evidence_types import VERIFIER_NAMED_CHECK_EVIDENCE_TYPE
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import (
    Adjudication,
    DispatchRecord,
    Event,
    Evidence,
    UnitPrBinding,
    WorkUnit,
)
from orchestrator.services.evidence import current_evidence
from orchestrator.services.lifecycle import TransitionCommand, transition_unit
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.verifier import VerifyCommand, verify_work_unit
from orchestrator.services.verifier_evidence import (
    record_named_check_evidence,
)
from tests.fixtures.named_check import (
    AUTHORITY,
    AUTOMATED_CHECK_AUTHORITY,
    HEAD_SHA,
    HUMAN,
    NOW,
    PR_NUMBER,
    VERIFIER,
    WORKER,
    bind_dispatched_pull_request,
    mapped_submitted_unit,
    named_check_command,
    record_worker_evidence,
)


def test_verifier_named_check_supersedes_worker_evidence_and_completes(
    migrated_session: Session,
) -> None:
    unit = mapped_submitted_unit(
        migrated_session,
        key="automated-check-pass",
        evidence_type="automated_check",
        ac_id="AC-006",
        authority=AUTOMATED_CHECK_AUTHORITY,
    )
    worker_evidence = record_worker_evidence(
        migrated_session,
        unit,
        ac_id="AC-006",
        evidence_type="runner.pr.opened",
        payload={"pr_number": PR_NUMBER, "head_sha": HEAD_SHA},
        idempotency_key="automated-check-worker-evidence",
    )
    dispatch = bind_dispatched_pull_request(migrated_session, unit)

    named_check = record_named_check_evidence(
        migrated_session,
        named_check_command(unit, dispatch),
    )
    assert isinstance(named_check, Evidence)
    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-automated-check-pass",
        ),
    )

    assert named_check.evidence_type == VERIFIER_NAMED_CHECK_EVIDENCE_TYPE
    assert named_check.supersedes_evidence_id == worker_evidence.id
    current = current_evidence(migrated_session, unit.work_package_revision_id, unit.id, "AC-006")
    assert current is not None
    assert current.id == named_check.id
    assert result.result == "completed"
    assert result.state is WorkUnitState.COMPLETED
    assert result.evaluations[0].status == "passed"
    adjudication = migrated_session.get(Adjudication, result.evaluations[0].adjudication_id)
    assert adjudication is not None
    assert adjudication.evidence_id == named_check.id


def test_verifier_named_check_fails_closed_when_pr_head_changes_after_recording(
    migrated_session: Session,
) -> None:
    unit = mapped_submitted_unit(
        migrated_session,
        key="automated-check-stale-recorded-head",
        evidence_type="automated_check",
        ac_id="AC-006",
        authority=AUTOMATED_CHECK_AUTHORITY,
    )
    record_worker_evidence(
        migrated_session,
        unit,
        ac_id="AC-006",
        evidence_type="runner.pr.opened",
        payload={"pr_number": PR_NUMBER, "head_sha": HEAD_SHA},
        idempotency_key="automated-check-stale-recorded-head-worker-evidence",
    )
    dispatch = bind_dispatched_pull_request(migrated_session, unit)
    named_check = record_named_check_evidence(
        migrated_session,
        named_check_command(unit, dispatch),
    )
    assert isinstance(named_check, Evidence)
    binding = migrated_session.get(UnitPrBinding, unit.id)
    assert binding is not None
    binding.head_sha = "f" * 40
    migrated_session.commit()

    command = VerifyCommand(
        unit_id=unit.id,
        actor=VERIFIER,
        expected_version=unit.version,
        idempotency_key="verify-automated-check-stale-recorded-head",
    )
    result = verify_work_unit(migrated_session, command)

    assert result.result == "revision_required"
    assert result.state is WorkUnitState.REVISION_REQUIRED
    assert result.evaluations[0].status == "failed_closed"
    assert result.evaluations[0].outcome == "failed"
    evidence_count = migrated_session.scalar(
        select(func.count()).select_from(Evidence).where(Evidence.work_unit_id == unit.id)
    )
    adjudication_count = migrated_session.scalar(
        select(func.count()).select_from(Adjudication).where(Adjudication.work_unit_id == unit.id)
    )

    replay = verify_work_unit(migrated_session, command)

    assert replay.result == result.result
    assert replay.state is result.state
    assert replay.evaluations == result.evaluations
    assert (
        migrated_session.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.work_unit_id == unit.id)
        )
        == evidence_count
    )
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(Adjudication)
            .where(Adjudication.work_unit_id == unit.id)
        )
        == adjudication_count
    )


def test_verifier_rejects_superseded_passing_evidence_before_final_transition(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine, expire_on_commit=False) as setup:
        unit = mapped_submitted_unit(
            setup,
            key="automated-check-superseded-during-verify",
            evidence_type="automated_check",
            ac_id="AC-006",
            authority=AUTOMATED_CHECK_AUTHORITY,
        )
        record_worker_evidence(
            setup,
            unit,
            ac_id="AC-006",
            evidence_type="runner.pr.opened",
            payload={"pr_number": PR_NUMBER, "head_sha": HEAD_SHA},
            idempotency_key="automated-check-superseded-during-verify-worker-evidence",
        )
        dispatch = bind_dispatched_pull_request(setup, unit)
        passing = record_named_check_evidence(setup, named_check_command(unit, dispatch))
        assert isinstance(passing, Evidence)
        command = VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-automated-check-superseded-during-verify",
        )

    commit_count = 0
    superseding_evidence_id: uuid.UUID | None = None

    def supersede_after_passed_adjudication(_session: Session) -> None:
        nonlocal commit_count, superseding_evidence_id
        commit_count += 1
        if commit_count != 2:
            return
        with Session(migrated_engine, expire_on_commit=False) as writer:
            current_unit = writer.get(WorkUnit, command.unit_id)
            current_dispatch = writer.get(DispatchRecord, dispatch.id)
            assert current_unit is not None
            assert current_dispatch is not None
            superseding = record_named_check_evidence(
                writer,
                named_check_command(
                    current_unit,
                    current_dispatch,
                    conclusion="failure",
                    idempotency_key="automated-check-superseding-failure",
                ),
            )
            assert isinstance(superseding, Evidence)
            assert superseding.supersedes_evidence_id == passing.id
            superseding_evidence_id = superseding.id

    with Session(migrated_engine, expire_on_commit=False) as verifier_session:
        event.listen(verifier_session, "after_commit", supersede_after_passed_adjudication)
        try:
            result = verify_work_unit(verifier_session, command)
        finally:
            event.remove(
                verifier_session,
                "after_commit",
                supersede_after_passed_adjudication,
            )

    assert superseding_evidence_id is not None
    assert result.result == "revision_required"
    assert result.state is WorkUnitState.REVISION_REQUIRED
    assert result.evaluations[0].status == "failed_closed"
    assert result.evaluations[0].outcome == "failed"
    assert result.evaluations[0].evidence_id == superseding_evidence_id


@pytest.mark.parametrize("changed_record", ["pr_binding", "dispatch"])
def test_verifier_reloads_canonical_rows_after_passed_adjudication_commit(
    migrated_engine: Engine,
    changed_record: str,
) -> None:
    with Session(migrated_engine, expire_on_commit=False) as setup:
        unit = mapped_submitted_unit(
            setup,
            key="automated-check-head-change-during-verify",
            evidence_type="automated_check",
            ac_id="AC-006",
            authority=AUTOMATED_CHECK_AUTHORITY,
        )
        record_worker_evidence(
            setup,
            unit,
            ac_id="AC-006",
            evidence_type="runner.pr.opened",
            payload={"pr_number": PR_NUMBER, "head_sha": HEAD_SHA},
            idempotency_key="automated-check-head-change-during-verify-worker-evidence",
        )
        dispatch = bind_dispatched_pull_request(setup, unit)
        named_check = record_named_check_evidence(setup, named_check_command(unit, dispatch))
        assert isinstance(named_check, Evidence)
        command = VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-automated-check-head-change-during-verify",
        )

    commit_count = 0
    binding_updated = False
    held_canonical_rows: list[object] = []

    def retain_loaded_canonical_rows(session: Session) -> None:
        held_canonical_rows[:] = [
            row
            for row in session.identity_map.values()
            if isinstance(row, (DispatchRecord, UnitPrBinding))
        ]

    def update_binding_after_passed_adjudication(_session: Session) -> None:
        nonlocal binding_updated, commit_count
        commit_count += 1
        if commit_count != 2:
            return
        with Session(migrated_engine) as updater:
            if changed_record == "pr_binding":
                updater.execute(
                    update(UnitPrBinding)
                    .where(UnitPrBinding.work_unit_id == command.unit_id)
                    .values(head_sha="f" * 40)
                )
            else:
                updater.execute(
                    update(DispatchRecord)
                    .where(DispatchRecord.id == dispatch.id)
                    .values(status="failed")
                )
            updater.commit()
        binding_updated = True

    with Session(migrated_engine, expire_on_commit=False) as verifier_session:
        event.listen(verifier_session, "before_commit", retain_loaded_canonical_rows)
        event.listen(verifier_session, "after_commit", update_binding_after_passed_adjudication)
        try:
            result = verify_work_unit(verifier_session, command)
        finally:
            event.remove(verifier_session, "before_commit", retain_loaded_canonical_rows)
            event.remove(
                verifier_session,
                "after_commit",
                update_binding_after_passed_adjudication,
            )

    assert binding_updated is True
    assert result.result == "revision_required"
    assert result.state is WorkUnitState.REVISION_REQUIRED
    assert result.evaluations[0].status == "failed_closed"
    assert result.evaluations[0].outcome == "failed"


def test_automated_check_without_verifier_named_check_remains_judgment_required(
    migrated_session: Session,
) -> None:
    unit = mapped_submitted_unit(
        migrated_session,
        key="automated-check-legacy",
        evidence_type="automated_check",
        ac_id="AC-006",
        authority=AUTOMATED_CHECK_AUTHORITY,
    )
    record_worker_evidence(
        migrated_session,
        unit,
        ac_id="AC-006",
        evidence_type="runner.pr.opened",
        payload={"pr_number": PR_NUMBER, "head_sha": HEAD_SHA},
        idempotency_key="automated-check-legacy-evidence",
    )

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-automated-check-legacy",
        ),
    )

    assert result.result == "awaiting_review"
    assert result.state is WorkUnitState.AWAITING_REVIEW
    assert result.evaluations[0].status == "judgment_required"


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
