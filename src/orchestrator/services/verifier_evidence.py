import uuid
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.evidence_types import (
    NAMED_CHECK_MAX_AC_ID_LENGTH,
    NAMED_CHECK_MAX_ASSERTION_NAME_LENGTH,
    NAMED_CHECK_MAX_ASSERTION_VALUE_LENGTH,
    NAMED_CHECK_MAX_ASSERTIONS,
    NAMED_CHECK_MAX_CHECK_NAME_LENGTH,
    NAMED_CHECK_MAX_HEAD_SHA_LENGTH,
    NAMED_CHECK_MAX_IDEMPOTENCY_KEY_LENGTH,
    NAMED_CHECK_MAX_INTEGER_ABS,
    NAMED_CHECK_MAX_REFERENCE_LENGTH,
    NAMED_CHECK_MAX_REPOSITORY_LENGTH,
    NAMED_CHECK_MAX_RUN_ID_LENGTH,
    VERIFIER_NAMED_CHECK_EVIDENCE_TYPE,
)
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    DispatchRecord,
    Event,
    Evidence,
    PackageAcceptanceCriterion,
    UnitPrBinding,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.evidence import append_verifier_evidence
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.verifier_criteria import load_required_criteria

Scalar = str | int | bool
SUPPORTED_CONCLUSIONS = frozenset(
    {
        "success",
        "failure",
        "neutral",
        "skipped",
        "cancelled",
        "timed_out",
        "action_required",
    }
)
MAX_ACTOR_ID_LENGTH = 200


@dataclass(frozen=True)
class NamedCheckAssertion:
    name: str
    expected: Scalar
    observed: Scalar


@dataclass(frozen=True)
class NamedCheckEvidenceCommand:
    unit_id: uuid.UUID
    work_package_revision_id: uuid.UUID
    ac_id: str
    dispatch_id: uuid.UUID
    repository: str
    pr_number: int
    pr_url: str
    head_sha: str
    check_name: str
    conclusion: str
    run_id: str
    run_url: str
    assertions: tuple[NamedCheckAssertion, ...]
    actor: ActorContext
    expected_version: int
    idempotency_key: str


def record_named_check_evidence(
    session: Session,
    command: NamedCheckEvidenceCommand,
) -> Evidence | DomainError:
    try:
        normalized, payload = _normalize_command(command)
        if normalized.actor.role is not ActorRole.VERIFIER:
            raise DomainError(
                "role_forbidden", "only verifiers may record named-check evidence", None
            )
        replay = _replay(session, normalized, payload)
        if replay is not None:
            return replay
        unit = _load_subject(session, normalized)
        _load_criterion(session, unit, normalized)
        repository = _target_repository(unit)
        _validate_bindings(session, unit, normalized, repository)
        return append_verifier_evidence(
            session,
            work_package_revision_id=normalized.work_package_revision_id,
            work_unit_id=normalized.unit_id,
            ac_id=normalized.ac_id,
            actor=normalized.actor,
            evidence_type=VERIFIER_NAMED_CHECK_EVIDENCE_TYPE,
            stable_ref=normalized.run_url,
            payload=payload,
            source_revision=normalized.head_sha,
            idempotency_key=normalized.idempotency_key,
            expected_version=normalized.expected_version,
            attempt=unit.attempt_count,
        )
    except DomainError as error:
        session.rollback()
        return error


def _load_subject(session: Session, command: NamedCheckEvidenceCommand) -> WorkUnit:
    unit = session.scalar(select(WorkUnit).where(WorkUnit.id == command.unit_id).with_for_update())
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    if (
        not isinstance(unit.attempt_count, int)
        or isinstance(unit.attempt_count, bool)
        or unit.attempt_count <= 0
    ):
        raise DomainError(
            "named_check_binding_mismatch",
            "named check requires a positive dispatched attempt",
            None,
        )
    state = WorkUnitState(unit.state)
    if state not in {WorkUnitState.SUBMITTED, WorkUnitState.VERIFYING}:
        raise DomainError(
            "invalid_transition",
            f"{state} does not accept named-check evidence",
            "submit",
            current_state=unit.state,
            current_version=unit.version,
        )
    if unit.version != command.expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )
    if unit.work_package_revision_id != command.work_package_revision_id:
        raise DomainError(
            "evidence_subject_invalid",
            "package revision and work unit do not match",
            None,
        )
    return unit


def _load_criterion(
    session: Session,
    unit: WorkUnit,
    command: NamedCheckEvidenceCommand,
) -> PackageAcceptanceCriterion:
    revision = session.get(WorkPackageRevision, command.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)
    criterion = next(
        (
            item
            for item in load_required_criteria(session, unit, revision)
            if item.ac_id == command.ac_id
        ),
        None,
    )
    if criterion is None or criterion.evidence_type.strip().lower() != "automated_check":
        raise DomainError(
            "evidence_subject_invalid",
            "acceptance criterion is not a mapped automated check",
            None,
        )
    return criterion


def _target_repository(unit: WorkUnit) -> str:
    repository = normalize_authority(unit.authority).constraints.get("target_repository")
    if not isinstance(repository, str) or not repository.strip():
        raise DomainError(
            "named_check_binding_mismatch",
            "work unit authority has no target repository",
            None,
        )
    return repository.strip()


def _validate_bindings(
    session: Session,
    unit: WorkUnit,
    command: NamedCheckEvidenceCommand,
    repository: str,
) -> None:
    dispatch = session.get(DispatchRecord, command.dispatch_id)
    if dispatch is None or (
        dispatch.work_unit_id != unit.id
        or dispatch.work_package_revision_id != unit.work_package_revision_id
        or dispatch.runner_attempt != unit.attempt_count
        or dispatch.status != "dispatched"
        or dispatch.target_repository != repository
        or command.repository != repository
    ):
        raise DomainError(
            "named_check_binding_mismatch",
            "named check does not match the canonical dispatch",
            None,
        )
    binding = session.get(UnitPrBinding, unit.id)
    expected_url = f"https://github.com/{repository}/pull/{command.pr_number}"
    if binding is None or (
        binding.pr_number != command.pr_number
        or binding.head_sha != command.head_sha
        or binding.verification_read_attempt != unit.attempt_count
        or binding.verification_read_head_sha != command.head_sha
        or command.pr_url != expected_url
    ):
        raise DomainError(
            "named_check_binding_mismatch",
            "named check does not match the canonical pull-request binding",
            None,
        )


def _normalize_command(
    command: NamedCheckEvidenceCommand,
) -> tuple[NamedCheckEvidenceCommand, dict[str, object]]:
    if not isinstance(command, NamedCheckEvidenceCommand):
        raise _invalid()
    actor = command.actor
    if (
        not isinstance(actor, ActorContext)
        or not isinstance(actor.role, ActorRole)
        or not _bounded_text(actor.actor_id, MAX_ACTOR_ID_LENGTH)
        or not isinstance(command.unit_id, uuid.UUID)
        or not isinstance(command.work_package_revision_id, uuid.UUID)
        or not isinstance(command.dispatch_id, uuid.UUID)
        or not isinstance(command.expected_version, int)
        or isinstance(command.expected_version, bool)
        or command.expected_version < 0
        or not isinstance(command.pr_number, int)
        or isinstance(command.pr_number, bool)
        or command.pr_number <= 0
        or not _bounded_text(command.ac_id, NAMED_CHECK_MAX_AC_ID_LENGTH)
        or not _bounded_text(command.repository, NAMED_CHECK_MAX_REPOSITORY_LENGTH)
        or not _bounded_text(command.pr_url, NAMED_CHECK_MAX_REFERENCE_LENGTH)
        or not _bounded_text(command.head_sha, NAMED_CHECK_MAX_HEAD_SHA_LENGTH, minimum=7)
        or not _bounded_text(command.check_name, NAMED_CHECK_MAX_CHECK_NAME_LENGTH)
        or not _bounded_text(command.run_id, NAMED_CHECK_MAX_RUN_ID_LENGTH)
        or not _bounded_text(command.run_url, NAMED_CHECK_MAX_REFERENCE_LENGTH)
        or not _bounded_text(command.idempotency_key, NAMED_CHECK_MAX_IDEMPOTENCY_KEY_LENGTH)
        or not isinstance(command.conclusion, str)
        or command.conclusion.strip().lower() not in SUPPORTED_CONCLUSIONS
        or not isinstance(command.assertions, tuple)
        or not 0 < len(command.assertions) <= NAMED_CHECK_MAX_ASSERTIONS
    ):
        raise _invalid()
    names: set[str] = set()
    assertions: list[NamedCheckAssertion] = []
    for assertion in command.assertions:
        name = assertion.name.strip() if isinstance(assertion, NamedCheckAssertion) else ""
        if (
            not isinstance(assertion, NamedCheckAssertion)
            or not _bounded_text(name, NAMED_CHECK_MAX_ASSERTION_NAME_LENGTH)
            or name in names
            or not _valid_scalar(assertion.expected)
            or not _valid_scalar(assertion.observed)
        ):
            raise _invalid()
        names.add(name)
        assertions.append(replace(assertion, name=name))
    normalized = replace(
        command,
        ac_id=command.ac_id.strip(),
        repository=command.repository.strip(),
        pr_url=command.pr_url.strip(),
        head_sha=command.head_sha.strip(),
        check_name=command.check_name.strip(),
        conclusion=command.conclusion.strip().lower(),
        run_id=command.run_id.strip(),
        run_url=command.run_url.strip(),
        idempotency_key=command.idempotency_key.strip(),
        assertions=tuple(assertions),
    )
    payload: dict[str, object] = {
        "dispatch_id": str(normalized.dispatch_id),
        "repository": normalized.repository,
        "pr_number": normalized.pr_number,
        "pr_url": normalized.pr_url,
        "head_sha": normalized.head_sha,
        "check_name": normalized.check_name,
        "conclusion": normalized.conclusion,
        "run_id": normalized.run_id,
        "run_url": normalized.run_url,
        "assertions": [
            {
                "name": assertion.name,
                "expected": assertion.expected,
                "observed": assertion.observed,
            }
            for assertion in normalized.assertions
        ],
    }
    return normalized, payload


def _replay(
    session: Session,
    command: NamedCheckEvidenceCommand,
    payload: dict[str, object],
) -> Evidence | None:
    row = session.scalar(
        select(Evidence).where(Evidence.idempotency_key == command.idempotency_key)
    )
    event = session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key))
    if row is None and event is None:
        return None
    expected_command = {
        "ac_id": command.ac_id,
        "actor_id": command.actor.actor_id,
        "actor_role": command.actor.role,
        "attempt": row.attempt if row is not None else None,
        "evidence_type": VERIFIER_NAMED_CHECK_EVIDENCE_TYPE,
        "expected_version": command.expected_version,
        "payload": payload,
        "source_revision": command.head_sha,
        "stable_ref": command.run_url,
        "context_snapshot_id": None,
        "work_package_revision_id": str(command.work_package_revision_id),
        "work_unit_id": str(command.unit_id),
    }
    if (
        row is None
        or event is None
        or event.action != "evidence.recorded"
        or event.subject_id != row.id
        or event.payload.get("command") != expected_command
    ):
        raise DomainError(
            "idempotency_conflict",
            "idempotency key belongs to a different operation",
            "use a new idempotency key",
        )
    return row


def _bounded_text(value: object, limit: int, *, minimum: int = 1) -> bool:
    return isinstance(value, str) and minimum <= len(value.strip()) <= limit


def _valid_scalar(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and len(value) <= NAMED_CHECK_MAX_ASSERTION_VALUE_LENGTH
    return isinstance(value, bool) or (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) <= NAMED_CHECK_MAX_INTEGER_ABS
    )


def _invalid() -> DomainError:
    return DomainError("named_check_invalid", "named-check evidence is malformed", None)
