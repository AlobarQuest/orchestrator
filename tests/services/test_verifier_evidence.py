from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import replace
from threading import Event as ThreadEvent
from typing import Any, cast

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.exc import OperationalError
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
from orchestrator.services.evidence import append_evidence, append_verifier_evidence
from orchestrator.services.github_checks import CheckObservationError
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.verifier import VerifyCommand, verify_work_unit
from orchestrator.services.verifier_evaluators import evaluate_criterion
from orchestrator.services.verifier_evidence import NamedCheckEvidenceCommand
from tests.fixtures.named_check import (
    AUTOMATED_CHECK_AUTHORITY,
    CHECK_NAME,
    HEAD_SHA,
    PR_NUMBER,
    TARGET_REPOSITORY,
    VERIFIER,
    WORKER,
    StubCheckObserver,
    bind_dispatched_pull_request,
    mapped_submitted_unit,
    named_check_command,
    observed_job,
    record_named_check,
    record_worker_evidence,
)

SYSTEM = ActorContext("system-1", ActorRole.SYSTEM)


def payload_of(evidence: Evidence) -> dict[str, Any]:
    assert evidence.payload is not None
    return evidence.payload


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


def test_worker_non_string_evidence_type_is_rejected_before_claim_validation(
    migrated_session: Session,
) -> None:
    unit, _dispatch = automated_check_unit(migrated_session, "non-string-worker-type")

    result = append_evidence(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="AC-006",
        attempt=unit.attempt_count,
        actor=WORKER,
        lease_token="not-an-active-lease",
        evidence_type=cast(str, 17),
        stable_ref="https://github.com/example/run/1",
        payload={"conclusion": "success"},
        source_revision=HEAD_SHA,
        idempotency_key="non-string-worker-type-attempt",
        expected_version=unit.version,
    )

    assert isinstance(result, DomainError)
    assert result.code == "evidence_invalid"


def test_verifier_evidence_attempt_must_match_locked_unit_attempt(
    migrated_session: Session,
) -> None:
    unit, _dispatch = automated_check_unit(migrated_session, "verifier-attempt-mismatch")

    result = append_verifier_evidence(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="AC-006",
        actor=VERIFIER,
        evidence_type=VERIFIER_NAMED_CHECK_EVIDENCE_TYPE,
        stable_ref="https://github.com/example/run/1",
        payload={"conclusion": "success"},
        source_revision=HEAD_SHA,
        idempotency_key="verifier-attempt-mismatch-command",
        expected_version=unit.version,
        attempt=unit.attempt_count + 1,
    )

    assert isinstance(result, DomainError)
    assert result.code == "evidence_invalid"


@pytest.mark.parametrize("actor", [WORKER, SYSTEM])
def test_non_verifier_cannot_record_named_check_evidence(
    migrated_session: Session,
    actor: ActorContext,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, f"forbidden-{actor.role}")

    result = record_named_check(
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
        ("expected_conclusion", None),
        ("expected_conclusion", ""),
    ],
)
def test_named_check_malformed_values_return_domain_error(
    migrated_session: Session,
    field: str,
    replacement: object,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, f"malformed-{field}-{replacement!s}")

    result = record_named_check(
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
        ("expected_version", True),
        ("idempotency_key", 3),
        ("expected_conclusion", 1),
    ],
)
def test_named_check_direct_service_rejects_malformed_field_types(
    migrated_session: Session,
    field: str,
    replacement: object,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, f"malformed-direct-{field}")

    result = record_named_check(
        migrated_session,
        replace(named_check_command(unit, dispatch), **{field: replacement}),
    )

    assert isinstance(result, DomainError)
    assert result.code == "named_check_invalid"


def test_named_check_direct_service_rejects_malformed_command_object(
    migrated_session: Session,
) -> None:
    malformed = cast(NamedCheckEvidenceCommand, object())
    result = record_named_check(migrated_session, malformed)

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

    result = record_named_check(
        migrated_session,
        replace(named_check_command(unit, dispatch), actor=actor),
    )

    assert isinstance(result, DomainError)
    assert result.code == "named_check_invalid"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("check_name", ""),
        ("check_name", " " * 4),
        ("expected_conclusion", "queued"),
        ("expected_conclusion", "SUCCEEDED"),
    ],
)
def test_named_check_rejects_invalid_bounded_payload(
    migrated_session: Session,
    field: str,
    replacement: object,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, f"invalid-payload-{field}")

    result = record_named_check(
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

    result = record_named_check(
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

    result = record_named_check(migrated_session, named_check_command(unit, dispatch))

    assert isinstance(result, DomainError)
    assert result.code == "named_check_binding_mismatch"


def test_named_check_rejects_missing_pr_binding(migrated_session: Session) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "missing-pr-binding")
    binding = migrated_session.get(UnitPrBinding, unit.id)
    assert binding is not None
    migrated_session.delete(binding)
    migrated_session.commit()

    result = record_named_check(migrated_session, named_check_command(unit, dispatch))

    assert isinstance(result, DomainError)
    assert result.code == "named_check_binding_mismatch"


def test_named_check_accepts_dispatch_ordinal_after_skipped_probe(
    migrated_session: Session,
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "dispatch-after-skipped-probe")
    assert unit.attempt_count == 1
    dispatch.runner_attempt = 2
    migrated_session.commit()
    migrated_session.add(
        DispatchRecord(
            work_unit_id=unit.id,
            work_package_revision_id=unit.work_package_revision_id,
            runner_attempt=1,
            status="skipped",
            reason_code="dispatch_disabled",
            idempotency_key="dispatch-after-skipped-probe-disabled",
            target_repository=TARGET_REPOSITORY,
            workflow_id=dispatch.workflow_id,
            workflow_ref=dispatch.workflow_ref,
            payload={},
        )
    )
    migrated_session.commit()

    evidence = record_named_check(migrated_session, named_check_command(unit, dispatch))
    assert isinstance(evidence, Evidence)

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-dispatch-after-skipped-probe",
        ),
    )

    assert result.result == "completed"


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

    result = record_named_check(migrated_session, named_check_command(unit, dispatch))

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

    result = record_named_check(
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
    evidence = record_named_check(
        migrated_session,
        named_check_command(unit, dispatch, expected_conclusion=conclusion),
        StubCheckObserver(conclusion=conclusion),
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
    evidence = record_named_check(
        migrated_session,
        named_check_command(unit, dispatch, expected_conclusion="failure"),
        StubCheckObserver(conclusion="failure"),
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


@pytest.mark.parametrize("observed", ["failure", "cancelled", "timed_out", "action_required"])
def test_a_claim_of_success_cannot_pass_when_github_says_otherwise(
    migrated_session: Session,
    observed: str,
) -> None:
    """WS-P2.20's headline. The caller claims the best case; GitHub reports the worst.

    Before this workstream the caller supplied both halves and the criterion resolved `passed`
    on its own arithmetic. Now the claim is the only thing it supplies, and the answer is the
    observation's.
    """
    unit, dispatch = automated_check_unit(migrated_session, f"false-claim-{observed}")

    evidence = record_named_check(
        migrated_session,
        named_check_command(unit, dispatch, expected_conclusion="success"),
        StubCheckObserver(conclusion=observed),
    )
    assert isinstance(evidence, Evidence)
    assert payload_of(evidence)["conclusion"] == observed
    assert payload_of(evidence)["expected_conclusion"] == "success"

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key=f"verify-false-claim-{observed}",
        ),
    )

    assert result.result == "revision_required"
    assert result.evaluations[0].status == "failed_closed"
    assert result.evaluations[0].outcome == "failed"
    assert result.evaluations[0].reason == (
        f"named check concluded {observed}; the caller claimed success"
    )


def test_the_observed_conclusion_is_never_the_one_the_caller_named(
    migrated_session: Session,
) -> None:
    """The claim reaches the record as a claim and nothing else.

    A regression that let `expected_conclusion` leak into the observed half would leave every
    other test in this file green, because they all agree with the observer.
    """
    unit, dispatch = automated_check_unit(migrated_session, "claim-is-not-the-answer")
    observer = StubCheckObserver(conclusion="failure")

    evidence = record_named_check(
        migrated_session,
        named_check_command(unit, dispatch, expected_conclusion="success"),
        observer,
    )

    assert isinstance(evidence, Evidence)
    observation = payload_of(evidence)["observation"]
    assert observation["conclusion"] == "failure"
    assert [job["conclusion"] for job in observation["jobs"]] == ["failure"]
    # And it asked about the canonical head, not some head the caller could have named.
    assert observer.calls == [
        {"repository": TARGET_REPOSITORY, "head_sha": HEAD_SHA, "check_name": CHECK_NAME}
    ]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("unavailable", "named_check_observation_unavailable"),
        ("not_found", "named_check_not_found"),
        ("not_concluded", "named_check_not_concluded"),
        ("ambiguous", "named_check_ambiguous"),
    ],
)
def test_a_refused_observation_records_no_evidence_at_all(
    migrated_session: Session,
    code: str,
    expected: str,
) -> None:
    """Fail closed means fail EMPTY: the criterion then has no verifier evidence to resolve on.

    `automated_check` with no named-check evidence evaluates `judgment_required`, so a GitHub
    the orchestrator could not read routes the criterion to a human rather than to the caller.
    """
    unit, dispatch = automated_check_unit(migrated_session, f"refusal-{code}")

    result = record_named_check(
        migrated_session,
        named_check_command(unit, dispatch),
        StubCheckObserver(error=CheckObservationError(code, "detail")),
    )

    assert isinstance(result, DomainError)
    assert result.code == expected
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.evidence_type == VERIFIER_NAMED_CHECK_EVIDENCE_TYPE)
        )
        == 0
    )
    criterion = migrated_session.scalar(
        select(PackageAcceptanceCriterion).where(
            PackageAcceptanceCriterion.work_package_revision_id == unit.work_package_revision_id
        )
    )
    assert criterion is not None
    assert evaluate_criterion(criterion, None)[0] == "judgment_required"


def test_an_unrecognised_observer_code_still_refuses(migrated_session: Session) -> None:
    """A code this module does not know is a refusal, not a fall-through to the caller's word."""
    unit, dispatch = automated_check_unit(migrated_session, "refusal-unknown-code")

    result = record_named_check(
        migrated_session,
        named_check_command(unit, dispatch),
        StubCheckObserver(error=CheckObservationError("something_new", "detail")),
    )

    assert isinstance(result, DomainError)
    assert result.code == "named_check_observation_unavailable"


def test_every_job_the_name_resolved_to_is_recorded(migrated_session: Session) -> None:
    """Two identically-named jobs on one head — the ordinary push-plus-pull-request case.

    Unanimous, so it resolves; and both are cited, newest first, so the record cannot imply
    there was one job when there were two.
    """
    unit, dispatch = automated_check_unit(migrated_session, "two-agreeing-jobs")
    older = observed_job("success", run_id="100")
    newer = observed_job("success", run_id="200")

    evidence = record_named_check(
        migrated_session,
        named_check_command(unit, dispatch),
        StubCheckObserver(jobs=(newer, older)),
    )

    assert isinstance(evidence, Evidence)
    observation = payload_of(evidence)["observation"]
    assert [job["run_id"] for job in observation["jobs"]] == ["200", "100"]
    assert payload_of(evidence)["run_id"] == "200"
    assert evidence.stable_ref == newer.run_url

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-two-agreeing-jobs",
        ),
    )
    assert result.evaluations[0].status == "passed"


@pytest.mark.parametrize(
    "corruption",
    [
        "drop_observation",
        "foreign_source",
        "other_check_name",
        "no_jobs",
        "too_many_jobs",
        "job_disagrees",
        "observation_disagrees",
        "job_unidentified",
    ],
)
def test_a_payload_the_observer_would_not_have_written_cannot_pass(
    migrated_session: Session,
    corruption: str,
) -> None:
    """The evaluator does not take the payload's own `conclusion` on trust.

    Ingestion refuses a name whose matches disagree, so a stored payload where they do is one
    that did not come from an observation — a hand-written row, or a shape some later writer
    assembled. Each case here is a payload the observer could not have produced, and none of
    them may resolve the criterion.
    """
    unit, dispatch = automated_check_unit(migrated_session, f"corrupt-{corruption}")
    evidence = record_named_check(migrated_session, named_check_command(unit, dispatch))
    assert isinstance(evidence, Evidence)
    criterion = migrated_session.scalar(
        select(PackageAcceptanceCriterion).where(
            PackageAcceptanceCriterion.work_package_revision_id == unit.work_package_revision_id
        )
    )
    assert criterion is not None
    assert evaluate_criterion(criterion, evidence)[0] == "passed"

    # In memory only: `evidence` is append-only in the database, and the subject here is what
    # the evaluator does with a payload, not whether one can be written.
    payload: dict[str, Any] = dict(payload_of(evidence))
    observation: dict[str, Any] = dict(payload["observation"])
    job: dict[str, Any] = dict(observation["jobs"][0])
    if corruption == "drop_observation":
        payload.pop("observation")
    else:
        if corruption == "foreign_source":
            observation["source"] = "operator.typed"
        if corruption == "other_check_name":
            observation["check_name"] = "Something Else"
        if corruption == "no_jobs":
            observation["jobs"] = []
        if corruption == "too_many_jobs":
            observation["jobs"] = [job] * 33
        if corruption == "job_disagrees":
            observation["jobs"] = [{**job, "conclusion": "failure"}]
        if corruption == "observation_disagrees":
            observation["conclusion"] = "failure"
        if corruption == "job_unidentified":
            observation["jobs"] = [{**job, "job_url": ""}]
        payload["observation"] = observation
    evidence.payload = payload

    status, outcome, rationale = evaluate_criterion(criterion, evidence)

    assert status == "failed_closed"
    assert outcome == "failed"
    assert rationale == "named-check conclusion was not observed"


def test_named_check_replay_does_not_duplicate_evidence(migrated_session: Session) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "named-check-replay")
    command = named_check_command(unit, dispatch)

    first = record_named_check(migrated_session, command)
    replay = record_named_check(migrated_session, command)

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
    first = record_named_check(migrated_session, command)
    assert isinstance(first, Evidence)

    conflict = record_named_check(
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
    first = record_named_check(migrated_session, command)
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

    replay = record_named_check(migrated_session, command)

    assert isinstance(replay, Evidence)
    assert replay.id == first.id


def test_named_check_locks_pr_binding_until_evidence_commit(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as setup:
        unit, dispatch = automated_check_unit(setup, "named-check-binding-lock")
        command = named_check_command(unit, dispatch)
        unit_id = unit.id

    binding_read = ThreadEvent()
    update_finished = ThreadEvent()

    def pause_after_binding_read(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "FROM unit_pr_binding" in statement:
            binding_read.set()
            assert update_finished.wait(timeout=5)

    event.listen(migrated_engine, "after_cursor_execute", pause_after_binding_read)

    def record() -> Evidence | DomainError:
        with Session(migrated_engine, expire_on_commit=False) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            return record_named_check(session, command)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(record)
            assert binding_read.wait(timeout=5)
            with Session(migrated_engine) as updater:
                updater.execute(text("SET LOCAL lock_timeout = '250ms'"))
                try:
                    updater.execute(
                        text(
                            "UPDATE unit_pr_binding "
                            "SET head_sha = :head_sha, verification_read_head_sha = :head_sha "
                            "WHERE work_unit_id = :unit_id"
                        ),
                        {"head_sha": "f" * 40, "unit_id": unit_id},
                    )
                    updater.commit()
                    update_result = "changed"
                except OperationalError:
                    updater.rollback()
                    update_result = "locked"
                finally:
                    update_finished.set()
            result = future.result(timeout=10)
    finally:
        event.remove(migrated_engine, "after_cursor_execute", pause_after_binding_read)

    assert update_result == "locked"
    assert isinstance(result, Evidence)
    assert result.source_revision == HEAD_SHA


def test_concurrent_same_key_named_check_delivery_replays_after_split_read_window(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as setup:
        unit, dispatch = automated_check_unit(setup, "named-check-concurrent-replay")
        command = named_check_command(unit, dispatch)

    replay_read_paused = ThreadEvent()
    release_replay_read = ThreadEvent()
    paused_connection: list[int] = []

    def pause_first_replay_between_evidence_and_event_reads(
        connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "FROM evidence" not in statement or paused_connection:
            return
        paused_connection.append(id(connection))
        replay_read_paused.set()
        assert release_replay_read.wait(timeout=5)

    event.listen(
        migrated_engine,
        "after_cursor_execute",
        pause_first_replay_between_evidence_and_event_reads,
    )

    def record() -> Evidence | DomainError:
        with Session(migrated_engine, expire_on_commit=False) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            return record_named_check(session, command)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            paused = executor.submit(record)
            assert replay_read_paused.wait(timeout=5)
            contender = executor.submit(record)
            try:
                contender_result = contender.result(timeout=1)
            except FutureTimeoutError:
                contender_result = None
            finally:
                release_replay_read.set()
            paused_result = paused.result(timeout=10)
            if contender_result is None:
                contender_result = contender.result(timeout=10)
    finally:
        event.remove(
            migrated_engine,
            "after_cursor_execute",
            pause_first_replay_between_evidence_and_event_reads,
        )

    assert isinstance(paused_result, Evidence)
    assert isinstance(contender_result, Evidence)
    assert paused_result.id == contender_result.id


@pytest.mark.parametrize(
    "payload_change",
    [
        {"repository": "x" * 301},
        {"head_sha": "f" * 65},
        {"check_name": "x" * 201},
        {"run_id": "x" * 101},
        {"expected_conclusion": None},
    ],
)
def test_named_check_evaluator_revalidates_all_payload_bounds(
    migrated_session: Session,
    payload_change: dict[str, object],
) -> None:
    unit, dispatch = automated_check_unit(migrated_session, "evaluator-bounds")
    evidence = record_named_check(migrated_session, named_check_command(unit, dispatch))
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
