from dataclasses import replace
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.evidence_types import VERIFIER_NAMED_CHECK_EVIDENCE_TYPE
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    DispatchRecord,
    Evidence,
    PackageAcceptanceCriterion,
    UnitPrBinding,
)
from orchestrator.services.evidence import append_evidence
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.verifier import VerifyCommand, verify_work_unit
from orchestrator.services.verifier_evaluators import evaluate_criterion
from orchestrator.services.verifier_evidence import (
    NamedCheckAssertion,
    NamedCheckEvidenceCommand,
    Scalar,
    record_named_check_evidence,
)
from tests.fixtures.named_check import (
    AUTOMATED_CHECK_AUTHORITY,
    HEAD_SHA,
    PR_NUMBER,
    VERIFIER,
    WORKER,
    bind_dispatched_pull_request,
    mapped_submitted_unit,
    named_check_command,
    record_worker_evidence,
)

SYSTEM = ActorContext("system-1", ActorRole.SYSTEM)


def automated_check_unit(session: Session, key: str):
    unit = mapped_submitted_unit(
        session,
        key=key,
        evidence_type="automated_check",
        ac_id="AC-006",
        authority=AUTOMATED_CHECK_AUTHORITY,
    )
    record_worker_evidence(
        session,
        unit,
        ac_id="AC-006",
        evidence_type="runner.pr.opened",
        payload={"pr_number": PR_NUMBER, "head_sha": HEAD_SHA},
        idempotency_key=f"{key}-worker-evidence",
    )
    return unit, bind_dispatched_pull_request(session, unit)


def test_worker_cannot_submit_reserved_verifier_evidence_type(
    migrated_session: Session,
) -> None:
    unit, _dispatch = automated_check_unit(migrated_session, "reserved-worker-type")

    result = append_evidence(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="AC-006",
        attempt=unit.attempt_count,
        actor=WORKER,
        lease_token="not-an-active-lease",
        evidence_type=VERIFIER_NAMED_CHECK_EVIDENCE_TYPE,
        stable_ref="https://github.com/example/run/1",
        payload={"conclusion": "success"},
        source_revision=HEAD_SHA,
        idempotency_key="reserved-worker-type-attempt",
        expected_version=unit.version,
    )

    assert isinstance(result, DomainError)
    assert result.code == "evidence_type_reserved"


@pytest.mark.parametrize("actor", [WORKER, SYSTEM])
def test_non_verifier_cannot_record_named_check_evidence(
    migrated_session: Session,
    actor: ActorContext,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, f"forbidden-{actor.role}")

    result = record_named_check_evidence(
        migrated_session,
        replace(named_check_command(unit, dispatch), actor=actor),
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("pr_number", True),
        ("pr_number", "26"),
        ("assertions", (NamedCheckAssertion("tests_passed", 105, 10**100),)),
        ("assertions", ()),
    ],
)
def test_named_check_malformed_values_return_domain_error(
    migrated_session: Session,
    field: str,
    replacement: object,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, f"malformed-{field}-{replacement!s}")

    result = record_named_check_evidence(
        migrated_session,
        replace(named_check_command(unit, dispatch), **{field: replacement}),
    )

    assert isinstance(result, DomainError)
    assert result.code == "named_check_invalid"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("unit_id", "not-a-uuid"),
        ("work_package_revision_id", True),
        ("dispatch_id", 17),
        ("ac_id", 6),
        ("repository", None),
        ("head_sha", b"abc1234"),
        ("check_name", []),
        ("run_id", ""),
        ("run_url", " "),
        ("expected_version", True),
        ("idempotency_key", 3),
        ("assertions", [NamedCheckAssertion("tests_passed", 105, 105)]),
        ("assertions", ({"name": "tests_passed", "expected": 105, "observed": 105},)),
        (
            "assertions",
            (NamedCheckAssertion("tests_passed", cast(Scalar, 1.5), cast(Scalar, 1.5)),),
        ),
    ],
)
def test_named_check_direct_service_rejects_malformed_field_types(
    migrated_session: Session,
    field: str,
    replacement: object,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, f"malformed-direct-{field}")

    result = record_named_check_evidence(
        migrated_session,
        replace(named_check_command(unit, dispatch), **{field: replacement}),
    )

    assert isinstance(result, DomainError)
    assert result.code == "named_check_invalid"


def test_named_check_direct_service_rejects_malformed_command_object(
    migrated_session: Session,
) -> None:
    malformed = cast(NamedCheckEvidenceCommand, object())
    result = record_named_check_evidence(migrated_session, malformed)

    assert isinstance(result, DomainError)
    assert result.code == "named_check_invalid"


@pytest.mark.parametrize("actor", [None, object()])
def test_named_check_direct_service_rejects_malformed_actor(
    migrated_session: Session,
    actor: object,
) -> None:
    unit, dispatch = automated_check_unit(
        migrated_session, f"malformed-actor-{type(actor).__name__}"
    )

    result = record_named_check_evidence(
        migrated_session,
        replace(named_check_command(unit, dispatch), actor=actor),
    )

    assert isinstance(result, DomainError)
    assert result.code == "named_check_invalid"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("check_name", ""),
        ("run_id", ""),
        ("conclusion", "queued"),
        (
            "assertions",
            (
                NamedCheckAssertion("duplicate", 1, 1),
                NamedCheckAssertion("duplicate", 1, 1),
            ),
        ),
    ],
)
def test_named_check_rejects_invalid_bounded_payload(
    migrated_session: Session,
    field: str,
    replacement: object,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, f"invalid-payload-{field}")

    result = record_named_check_evidence(
        migrated_session,
        replace(named_check_command(unit, dispatch), **{field: replacement}),
    )

    assert isinstance(result, DomainError)
    assert result.code == "named_check_invalid"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("dispatch_id", None),
        ("repository", "AlobarQuest/orchestrator"),
        ("pr_number", 27),
        ("pr_url", "https://github.com/AlobarQuest/change-manager/pull/27"),
        ("head_sha", "f" * 40),
    ],
)
def test_named_check_rejects_canonical_binding_mismatch(
    migrated_session: Session,
    field: str,
    replacement: object,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, f"binding-{field}")
    command = named_check_command(unit, dispatch)
    if field == "dispatch_id":
        replacement = unit.id

    result = record_named_check_evidence(
        migrated_session,
        replace(command, **{field: replacement}),
    )

    assert isinstance(result, DomainError)
    assert result.code == "named_check_binding_mismatch"
    evidence_count = migrated_session.scalar(
        select(func.count())
        .select_from(Evidence)
        .where(Evidence.evidence_type == VERIFIER_NAMED_CHECK_EVIDENCE_TYPE)
    )
    assert evidence_count == 0


def test_named_check_rejects_missing_dispatch(migrated_session: Session) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "missing-dispatch")
    migrated_session.delete(dispatch)
    migrated_session.commit()

    result = record_named_check_evidence(migrated_session, named_check_command(unit, dispatch))

    assert isinstance(result, DomainError)
    assert result.code == "named_check_binding_mismatch"


def test_named_check_rejects_missing_pr_binding(migrated_session: Session) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "missing-pr-binding")
    binding = migrated_session.get(UnitPrBinding, unit.id)
    assert binding is not None
    migrated_session.delete(binding)
    migrated_session.commit()

    result = record_named_check_evidence(migrated_session, named_check_command(unit, dispatch))

    assert isinstance(result, DomainError)
    assert result.code == "named_check_binding_mismatch"


def test_named_check_rejects_stale_dispatched_attempt(migrated_session: Session) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "stale-dispatched-attempt")
    dispatch.runner_attempt = unit.attempt_count + 1
    migrated_session.commit()

    result = record_named_check_evidence(migrated_session, named_check_command(unit, dispatch))

    assert isinstance(result, DomainError)
    assert result.code == "named_check_binding_mismatch"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("verification_read_attempt", 2), ("verification_read_head_sha", "f" * 40)],
)
def test_named_check_rejects_stale_armed_binding(
    migrated_session: Session,
    field: str,
    replacement: object,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, f"stale-binding-{field}")
    binding = migrated_session.get(UnitPrBinding, unit.id)
    assert binding is not None
    setattr(binding, field, replacement)
    migrated_session.commit()

    result = record_named_check_evidence(migrated_session, named_check_command(unit, dispatch))

    assert isinstance(result, DomainError)
    assert result.code == "named_check_binding_mismatch"


def test_named_check_rejects_unit_without_positive_dispatched_attempt(
    migrated_session: Session,
) -> None:
    unit = mapped_submitted_unit(
        migrated_session,
        key="missing-positive-dispatch",
        evidence_type="automated_check",
        ac_id="AC-006",
        authority=AUTOMATED_CHECK_AUTHORITY,
    )
    assert unit.attempt_count == 0
    fake_dispatch = DispatchRecord(id=unit.id)

    result = record_named_check_evidence(
        migrated_session,
        named_check_command(unit, fake_dispatch),
    )

    assert isinstance(result, DomainError)
    assert result.code == "named_check_binding_mismatch"


@pytest.mark.parametrize("conclusion", ["neutral", "skipped"])
def test_named_check_non_success_conclusion_cannot_pass(
    migrated_session: Session,
    conclusion: str,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, f"conclusion-{conclusion}")
    evidence = record_named_check_evidence(
        migrated_session,
        named_check_command(unit, dispatch, conclusion=conclusion),
    )
    assert isinstance(evidence, Evidence)

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key=f"verify-conclusion-{conclusion}",
        ),
    )

    assert result.result == "revision_required"
    assert result.evaluations[0].status == "failed_closed"
    assert result.evaluations[0].outcome == "failed"


def test_named_check_explicit_failure_is_failed(migrated_session: Session) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "explicit-failure")
    evidence = record_named_check_evidence(
        migrated_session,
        named_check_command(unit, dispatch, conclusion="failure"),
    )
    assert isinstance(evidence, Evidence)

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-explicit-failure",
        ),
    )

    assert result.result == "revision_required"
    assert result.evaluations[0].status == "failed"
    assert result.evaluations[0].outcome == "failed"


def test_named_check_assertion_mismatch_fails_closed(migrated_session: Session) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "assertion-mismatch")
    evidence = record_named_check_evidence(
        migrated_session,
        named_check_command(
            unit,
            dispatch,
            assertions=(NamedCheckAssertion("tests_passed", 105, 104),),
        ),
    )
    assert isinstance(evidence, Evidence)

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-assertion-mismatch",
        ),
    )

    assert result.result == "revision_required"
    assert result.evaluations[0].status == "failed_closed"
    assert result.evaluations[0].outcome == "failed"


def test_named_check_replay_does_not_duplicate_evidence(migrated_session: Session) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "named-check-replay")
    command = named_check_command(unit, dispatch)

    first = record_named_check_evidence(migrated_session, command)
    replay = record_named_check_evidence(migrated_session, command)

    assert isinstance(first, Evidence)
    assert isinstance(replay, Evidence)
    assert replay.id == first.id
    evidence_count = migrated_session.scalar(
        select(func.count())
        .select_from(Evidence)
        .where(Evidence.idempotency_key == command.idempotency_key)
    )
    assert evidence_count == 1


def test_named_check_conflicting_idempotency_key_reuse_is_rejected(
    migrated_session: Session,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "named-check-conflict")
    command = named_check_command(unit, dispatch)
    first = record_named_check_evidence(migrated_session, command)
    assert isinstance(first, Evidence)

    conflict = record_named_check_evidence(
        migrated_session,
        replace(command, check_name="Different"),
    )

    assert isinstance(conflict, DomainError)
    assert conflict.code == "idempotency_conflict"


def test_named_check_replay_survives_lifecycle_and_version_advancement(
    migrated_session: Session,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "named-check-advanced-replay")
    command = named_check_command(unit, dispatch)
    first = record_named_check_evidence(migrated_session, command)
    assert isinstance(first, Evidence)
    verification = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-named-check-advanced-replay",
        ),
    )
    assert verification.result == "completed"

    replay = record_named_check_evidence(migrated_session, command)

    assert isinstance(replay, Evidence)
    assert replay.id == first.id


@pytest.mark.parametrize(
    "payload_change",
    [
        {"repository": "x" * 301},
        {"head_sha": "f" * 65},
        {"check_name": "x" * 201},
        {"run_id": "x" * 101},
        {"assertions": []},
        {
            "assertions": [
                {"name": f"assertion-{index}", "expected": index, "observed": index}
                for index in range(33)
            ]
        },
        {"assertions": [{"name": "x" * 101, "expected": 1, "observed": 1}]},
        {"assertions": [{"name": "value", "expected": "x" * 1025, "observed": "x" * 1025}]},
        {"assertions": [{"name": "value", "expected": 2**63, "observed": 2**63}]},
        {"assertions": [{"name": "value", "expected": True, "observed": 1}]},
        {
            "assertions": [
                {"name": "duplicate", "expected": 1, "observed": 1},
                {"name": "duplicate", "expected": 1, "observed": 1},
            ]
        },
    ],
)
def test_named_check_evaluator_revalidates_all_payload_bounds(
    migrated_session: Session,
    payload_change: dict[str, object],
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "evaluator-bounds")
    evidence = record_named_check_evidence(migrated_session, named_check_command(unit, dispatch))
    assert isinstance(evidence, Evidence)
    criterion = migrated_session.scalar(
        select(PackageAcceptanceCriterion).where(
            PackageAcceptanceCriterion.work_package_revision_id == unit.work_package_revision_id
        )
    )
    assert criterion is not None
    assert isinstance(evidence.payload, dict)
    evidence.payload = evidence.payload | payload_change

    status, outcome, _rationale = evaluate_criterion(criterion, evidence)

    assert status == "failed_closed"
    assert outcome == "failed"
