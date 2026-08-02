import uuid
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.evidence_types import (
    NAMED_CHECK_MAX_AC_ID_LENGTH,
    NAMED_CHECK_MAX_CHECK_NAME_LENGTH,
    NAMED_CHECK_MAX_HEAD_SHA_LENGTH,
    NAMED_CHECK_MAX_IDEMPOTENCY_KEY_LENGTH,
    NAMED_CHECK_MAX_REFERENCE_LENGTH,
    NAMED_CHECK_MAX_REPOSITORY_LENGTH,
    NAMED_CHECK_OBSERVATION_SOURCE,
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
from orchestrator.services.evidence import (
    append_verifier_evidence,
    lock_evidence_idempotency_key,
)
from orchestrator.services.github_checks import (
    CheckObservation,
    CheckObservationError,
    CheckObserver,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.verifier_criteria import load_required_criteria

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

# Why the orchestrator would not record an observation. Every one of these leaves NO evidence
# row, which is the fail-closed outcome: `automated_check` with no verifier evidence evaluates
# to `judgment_required`, so the criterion falls to the human floor WS-P2.17 already built
# rather than to the caller's word.
# not-a-vocabulary: refusal wording keyed by this module's own observer codes; nothing outside
# the observer produces these keys and nothing outside this module reads them.
OBSERVATION_REFUSALS = {
    "unavailable": (
        "named_check_observation_unavailable",
        "GitHub could not be asked how the named check concluded",
        "retry once GitHub is reachable",
    ),
    "not_found": (
        "named_check_not_found",
        "no job of that name has run on the armed head",
        "name a job the workflow actually defines",
    ),
    "not_concluded": (
        "named_check_not_concluded",
        "the named check has not finished",
        "record the evidence once the check concludes",
    ),
    "ambiguous": (
        "named_check_ambiguous",
        "the name resolves to jobs that do not agree",
        "make the check name resolve to one answer on this head",
    ),
}


@dataclass(frozen=True)
class NamedCheckEvidenceCommand:
    """What the caller may say about a named check.

    `check_name` and `expected_conclusion` are its CLAIMS — which check to look at, and what it
    believes that check said. Everything else is a restatement of canonical state that
    `_validate_bindings` refuses on mismatch. There is deliberately no field for what the check
    actually concluded: that arrives from GitHub, and letting a caller supply it is the defect
    this command shape exists to remove.
    """

    unit_id: uuid.UUID
    work_package_revision_id: uuid.UUID
    ac_id: str
    dispatch_id: uuid.UUID
    repository: str
    pr_number: int
    pr_url: str
    head_sha: str
    check_name: str
    expected_conclusion: str
    actor: ActorContext
    expected_version: int
    idempotency_key: str


def record_named_check_evidence(
    session: Session,
    command: NamedCheckEvidenceCommand,
    observer: CheckObserver,
) -> Evidence | DomainError:
    try:
        normalized, claim = _normalize_command(command)
        if normalized.actor.role is not ActorRole.VERIFIER:
            raise DomainError(
                "role_forbidden", "only verifiers may record named-check evidence", None
            )
        lock_evidence_idempotency_key(session, normalized.idempotency_key)
        # Ahead of the observation, deliberately. A replay must return what was recorded, not
        # ask GitHub again: a check re-run between the two calls would otherwise turn a replay
        # into a conflict over a value the caller never supplied.
        replay = _replay(session, normalized, claim)
        if replay is not None:
            session.commit()
            return replay
        unit = _load_subject(session, normalized)
        _load_criterion(session, unit, normalized)
        repository = _target_repository(unit)
        binding = _validate_bindings(session, unit, normalized, repository)
        # The armed head, not the caller's — the two are equal or `_validate_bindings` has
        # already refused, and reading canon keeps it that way if that check ever moves.
        observation = _observe(observer, repository, binding.head_sha, normalized.check_name)
        payload = _payload(claim, observation)
        return append_verifier_evidence(
            session,
            work_package_revision_id=normalized.work_package_revision_id,
            work_unit_id=normalized.unit_id,
            ac_id=normalized.ac_id,
            actor=normalized.actor,
            evidence_type=VERIFIER_NAMED_CHECK_EVIDENCE_TYPE,
            stable_ref=observation.jobs[0].run_url,
            payload=payload,
            source_revision=binding.head_sha,
            idempotency_key=normalized.idempotency_key,
            expected_version=normalized.expected_version,
            attempt=unit.attempt_count,
        )
    except DomainError as error:
        session.rollback()
        return error


def _observe(
    observer: CheckObserver,
    repository: str,
    head_sha: str,
    check_name: str,
) -> CheckObservation:
    try:
        return observer.observe(
            repository=repository,
            head_sha=head_sha,
            check_name=check_name,
        )
    except CheckObservationError as error:
        code, message, recovery = OBSERVATION_REFUSALS.get(
            error.code,
            OBSERVATION_REFUSALS["unavailable"],
        )
        raise DomainError(code, message, recovery) from error


def _payload(claim: dict[str, object], observation: CheckObservation) -> dict[str, object]:
    """The caller's claim, then what GitHub said — with the two kept visibly apart.

    `conclusion`, `run_id` and `run_url` stay at the top level because the evaluator, the
    evidence `stable_ref` and every stored payload already read them there; only where they come
    from has changed. The citation is the newest match; `observation.jobs` carries every one, so
    a payload can never imply there was a single job when there were several.
    """
    newest = observation.jobs[0]
    return {
        **claim,
        "conclusion": observation.conclusion,
        "run_id": newest.run_id,
        "run_url": newest.run_url,
        "observation": {
            "source": NAMED_CHECK_OBSERVATION_SOURCE,
            "check_name": observation.check_name,
            "conclusion": observation.conclusion,
            "jobs": [
                {
                    "run_id": job.run_id,
                    "run_url": job.run_url,
                    "job_id": job.job_id,
                    "job_url": job.job_url,
                    "conclusion": job.conclusion,
                }
                for job in observation.jobs
            ],
        },
    }


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
) -> UnitPrBinding:
    dispatch = session.get(DispatchRecord, command.dispatch_id)
    # Skipped dispatch decisions consume runner ordinals without creating claim attempts.
    # The exact dispatch row and the PR binding prove those independent parts of the chain.
    if dispatch is None or (
        dispatch.work_unit_id != unit.id
        or dispatch.work_package_revision_id != unit.work_package_revision_id
        or dispatch.status != "dispatched"
        or dispatch.target_repository != repository
        or command.repository != repository
    ):
        raise DomainError(
            "named_check_binding_mismatch",
            "named check does not match the canonical dispatch",
            None,
        )
    binding = session.scalar(
        select(UnitPrBinding).where(UnitPrBinding.work_unit_id == unit.id).with_for_update()
    )
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
    return binding


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
        or not _bounded_text(command.idempotency_key, NAMED_CHECK_MAX_IDEMPOTENCY_KEY_LENGTH)
        or not isinstance(command.expected_conclusion, str)
        or command.expected_conclusion.strip().lower() not in SUPPORTED_CONCLUSIONS
    ):
        raise _invalid()
    normalized = replace(
        command,
        ac_id=command.ac_id.strip(),
        repository=command.repository.strip(),
        pr_url=command.pr_url.strip(),
        head_sha=command.head_sha.strip(),
        check_name=command.check_name.strip(),
        expected_conclusion=command.expected_conclusion.strip().lower(),
        idempotency_key=command.idempotency_key.strip(),
    )
    claim: dict[str, object] = {
        "dispatch_id": str(normalized.dispatch_id),
        "repository": normalized.repository,
        "pr_number": normalized.pr_number,
        "pr_url": normalized.pr_url,
        "head_sha": normalized.head_sha,
        "check_name": normalized.check_name,
        "expected_conclusion": normalized.expected_conclusion,
    }
    return normalized, claim


def _replay(
    session: Session,
    command: NamedCheckEvidenceCommand,
    claim: dict[str, object],
) -> Evidence | None:
    """Is this key a replay of the same CLAIM?

    Sameness is judged on what the caller supplied, never on the observed half of the stored
    payload — the observation is not an input, and a check re-run between two identical calls
    must not turn the second into `idempotency_conflict`.
    """
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
        "source_revision": command.head_sha,
        "context_snapshot_id": None,
        "work_package_revision_id": str(command.work_package_revision_id),
        "work_unit_id": str(command.unit_id),
    }
    recorded = event.payload.get("command") if event is not None else None
    recorded_payload = recorded.get("payload") if isinstance(recorded, dict) else None
    if (
        row is None
        or event is None
        or event.action != "evidence.recorded"
        or event.subject_id != row.id
        or not isinstance(recorded, dict)
        or not isinstance(recorded_payload, dict)
        or {key: recorded_payload.get(key) for key in claim} != claim
        or {key: recorded.get(key) for key in expected_command} != expected_command
    ):
        raise DomainError(
            "idempotency_conflict",
            "idempotency key belongs to a different operation",
            "use a new idempotency key",
        )
    return row


def _bounded_text(value: object, limit: int, *, minimum: int = 1) -> bool:
    return isinstance(value, str) and minimum <= len(value.strip()) <= limit


def _invalid() -> DomainError:
    return DomainError("named_check_invalid", "named-check evidence is malformed", None)
