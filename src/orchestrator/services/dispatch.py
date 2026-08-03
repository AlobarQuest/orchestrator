import hashlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.clock import Clock
from orchestrator.errors import DomainError
from orchestrator.factory_policy import OUTSIDE_CHANGE_WINDOW
from orchestrator.kernel.authority import AuthorityEnvelope, normalize_authority
from orchestrator.kernel.runner_authority import runner_command_authority_violation
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import DispatchRecord, Event, WorkPackageRevision, WorkUnit
from orchestrator.services.authority_gate import AuthorityGate, human_authority_gate
from orchestrator.services.estate_landing import EstateLandingSource
from orchestrator.services.github_app import GitHubAppTokenError
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.reach_admission import (
    change_window_refusal,
    estate_refusal,
    reach_admission_refusal,
)

ORCHESTRATOR_URL = "https://sds.alobar.net"


@dataclass(frozen=True)
class DispatchSettings:
    enabled: bool
    allowed_change_classes: frozenset[str]
    enabled_capabilities: frozenset[str]
    allowed_target_repositories: frozenset[str]
    workflow_id: str
    workflow_ref: str
    # Whether the App credentials are configured — never the credentials themselves, so no
    # secret can reach a log, a repr, or a dispatch payload through this object.
    github_app_configured: bool
    failure_signature_threshold: int = 3
    orchestrator_url: str = ORCHESTRATOR_URL


@dataclass(frozen=True)
class DispatchCommand:
    unit_id: uuid.UUID
    runner_attempt: int
    actor: ActorContext
    idempotency_key: str
    expected_version: int | None = None


@dataclass(frozen=True)
class AdmissionDecision:
    reason: str | None
    target_repository: str
    # The known-good patterns this admission relied on in place of a human authority approval.
    # Non-empty only when admission actually reached the authority term and passed it on policy:
    # a decision blocked before that term never consulted it, so citing it would record a
    # suppression that was never used.
    authority_recognised_by: tuple[str, ...] = ()


class WorkflowDispatcher(Protocol):
    def dispatch_workflow(
        self,
        *,
        repository: str,
        workflow_id: str,
        ref: str,
        inputs: Mapping[str, str],
    ) -> Mapping[str, str | None]: ...


class GitHubDispatchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GitHubActionsDispatcher:
    def __init__(self, token_provider: Callable[[], str], *, timeout: float = 10.0) -> None:
        self._token_provider = token_provider
        self._timeout = timeout

    def dispatch_workflow(
        self,
        *,
        repository: str,
        workflow_id: str,
        ref: str,
        inputs: Mapping[str, str],
    ) -> Mapping[str, str | None]:
        # A bare file name is unchanged by quoting; a path becomes one segment rather than
        # three, so a misconfigured workflow_id fails loudly rather than as a bare 404.
        quoted = quote(workflow_id, safe="")
        url = f"https://api.github.com/repos/{repository}/actions/workflows/{quoted}/dispatches"
        # Minting happens inside the error envelope: a credential failure must land in a
        # DispatchRecord like any other, not escape as a 500 that records nothing.
        try:
            token = self._token_provider()
        except GitHubAppTokenError as error:
            raise GitHubDispatchError("app_token_mint", error.code) from error
        try:
            response = httpx.post(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"ref": ref, "inputs": dict(inputs)},
                timeout=self._timeout,
            )
        except httpx.RequestError as error:
            raise GitHubDispatchError("request_error", error.__class__.__name__) from error
        if response.status_code not in {200, 201, 202, 204}:
            raise GitHubDispatchError("github_api", f"status:{response.status_code}")
        return {"workflow_run_id": None, "workflow_run_url": None}


def dispatch_work_unit(
    session: Session,
    command: DispatchCommand,
    settings: DispatchSettings,
    dispatcher: WorkflowDispatcher,
    landing_source: EstateLandingSource,
    clock: Clock | None = None,
) -> DispatchRecord:
    try:
        record = _dispatch_work_unit(session, command, settings, dispatcher, landing_source, clock)
        session.commit()
        return record
    except Exception:
        session.rollback()
        raise


def _dispatch_work_unit(
    session: Session,
    command: DispatchCommand,
    settings: DispatchSettings,
    dispatcher: WorkflowDispatcher,
    landing_source: EstateLandingSource,
    clock: Clock | None,
) -> DispatchRecord:
    _authorize_dispatch_actor(command.actor)
    if command.runner_attempt <= 0:
        raise DomainError("dispatch_attempt_invalid", "runner attempt must be positive", None)

    existing = session.scalar(
        select(DispatchRecord).where(DispatchRecord.idempotency_key == command.idempotency_key)
    )
    if existing is not None:
        _validate_idempotent_record(session, existing, command, settings)
        return existing

    unit = session.scalar(select(WorkUnit).where(WorkUnit.id == command.unit_id).with_for_update())
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    if command.expected_version is not None and unit.version != command.expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )
    existing_attempt = session.scalar(
        select(DispatchRecord)
        .where(
            DispatchRecord.work_unit_id == unit.id,
            DispatchRecord.runner_attempt == command.runner_attempt,
        )
        .with_for_update()
    )
    if existing_attempt is not None:
        return existing_attempt
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)

    gate = human_authority_gate(unit, revision)
    admission = _blocked_reason(
        unit,
        revision,
        settings,
        gate,
        reach_admission_refusal(session, revision),
        change_window_refusal(session, revision, clock),
        landing_source,
    )
    repository = admission.target_repository
    if admission.authority_recognised_by:
        _record_authority_gate_not_required(session, command, unit, gate)
    if admission.reason is not None:
        return _record_dispatch(
            session,
            command,
            unit,
            revision,
            settings,
            repository=repository,
            status="skipped" if _is_skipped_reason(admission.reason) else "blocked",
            reason_code=admission.reason,
            payload=_payload(unit, settings, repository),
        )

    payload = _payload(unit, settings, repository)
    try:
        result = dispatcher.dispatch_workflow(
            repository=repository,
            workflow_id=settings.workflow_id,
            ref=settings.workflow_ref,
            inputs=payload["workflow_dispatch"]["inputs"],
        )
    except GitHubDispatchError as error:
        signature = failure_signature("workflow_dispatch", error.code, error.message)
        status = (
            "blocked"
            if circuit_open(
                signature_failure_count(session, unit.id, signature) + 1,
                settings.failure_signature_threshold,
            )
            else "failed"
        )
        return _record_dispatch(
            session,
            command,
            unit,
            revision,
            settings,
            repository=repository,
            status=status,
            reason_code="failure_signature_circuit_open" if status == "blocked" else error.code,
            failure_signature=signature,
            payload=payload | {"failure_code": error.code},
        )

    return _record_dispatch(
        session,
        command,
        unit,
        revision,
        settings,
        repository=repository,
        status="dispatched",
        payload=payload,
        github_run_id=result.get("workflow_run_id"),
        github_run_url=result.get("workflow_run_url"),
    )


def failure_signature(stage: str, code: str, message: str) -> str:
    normalized = " ".join(message.lower().split())
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"{stage}:{code}:{digest}"


def _authorize_dispatch_actor(actor: ActorContext) -> None:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError("role_forbidden", "only the orchestrator system actor may dispatch", None)


def _validate_idempotent_record(
    session: Session,
    record: DispatchRecord,
    command: DispatchCommand,
    settings: DispatchSettings,
) -> None:
    # The routed repository is a property of the unit, so compare the record against the
    # repository this command's unit resolves to — never against a process-wide setting,
    # which would make a legitimate replay look like a conflict.
    unit = session.get(WorkUnit, command.unit_id)
    expected_repository = (
        _target_repository(normalize_authority(unit.authority)) if unit is not None else None
    )
    if (
        record.work_unit_id != command.unit_id
        or record.runner_attempt != command.runner_attempt
        or (expected_repository is not None and record.target_repository != expected_repository)
        or record.workflow_id != settings.workflow_id
        or record.workflow_ref != settings.workflow_ref
    ):
        raise DomainError(
            "idempotency_conflict",
            "idempotency key belongs to a different dispatch",
            "use a new idempotency key",
        )


def _blocked_reason(
    unit: WorkUnit,
    revision: WorkPackageRevision,
    settings: DispatchSettings,
    gate: AuthorityGate,
    reach_refusal: str | None,
    window_refusal: str | None,
    landing_source: EstateLandingSource,
) -> AdmissionDecision:
    """Why this unit may not run, in the order the terms are cheapest to answer.

    The human-authority term is the one WS-P2.18 made conditional: an envelope a declared
    known-good pattern recognises passes it without an approval (ADR-0011). Nothing else moves --
    the hard off-switch is still the first term and policy cannot see it, so no pattern can widen
    what it allows.

    The reach term (Increment 4) sits ABOVE the human-authority one because approving cannot fix
    it: a declaration nobody wrote is a property of the package, and a person clicking approve
    would be attesting to an envelope whose blast radius is still unstated. Reporting the authority
    term first would send an operator to a form that cannot help.

    The change window (Increment 5) sits BELOW every one of them, for the mirror of that reason: it
    is the only term that clears without anybody doing anything, so reporting it ahead of a
    standing defect would hide the thing somebody has to fix behind the thing that fixes itself.

    The estate term (WS-P2.28) sits DIRECTLY BELOW the reach term because it is the same kind of
    defect one step further on: the first says nobody stated what this work touches, the second
    says what they stated is contradicted by what the estate records. Checking a declaration
    nobody made is meaningless, so the order between those two is forced. It stays ABOVE the
    human-authority term for the reason that term is already ordered there -- approving cannot fix
    a declaration, and a person clicking approve would be attesting to a blast radius the estate
    disagrees with.

    It is the one term that is EVALUATED here rather than handed in already answered, and that is
    not a stylistic difference. It is the only term whose input costs a network round-trip, so
    evaluating it in place is what keeps a closed off-switch reporting `dispatch_disabled` rather
    than whatever App Brain happened to say -- and it is the only point that holds the target
    repository without normalizing the envelope a second time, which this module deliberately does
    exactly once.
    """
    envelope = normalize_authority(unit.authority)
    target_repository = _target_repository(envelope)
    if not settings.enabled:
        return AdmissionDecision("dispatch_disabled", target_repository)
    if not settings.github_app_configured:
        return AdmissionDecision("github_app_credentials_missing", target_repository)
    if unit.state != "ready":
        return AdmissionDecision("work_unit_not_ready", target_repository)
    if reach_refusal is not None:
        return AdmissionDecision(reach_refusal, target_repository)
    estate_reason = estate_refusal(revision, target_repository, landing_source)
    if estate_reason is not None:
        return AdmissionDecision(estate_reason, target_repository)
    if unit.authority_approval_id is None and gate.refusals:
        return AdmissionDecision("authority_approval_missing", target_repository)
    return AdmissionDecision(
        _envelope_reason(unit, settings, envelope, target_repository) or window_refusal,
        target_repository,
        # Cited only where it was USED: a unit carrying a human's approval passed the term on that
        # approval, whatever policy would also have said about it.
        () if unit.authority_approval_id is not None else gate.recognised_by,
    )


def _envelope_reason(
    unit: WorkUnit,
    settings: DispatchSettings,
    envelope: AuthorityEnvelope,
    target_repository: str,
) -> str | None:
    """The terms the envelope itself decides, for a unit already past the gates above."""
    if unit.required_capability not in settings.enabled_capabilities:
        return "capability_not_enabled"
    change_class = _change_class(envelope, unit.required_capability)
    if change_class not in settings.allowed_change_classes:
        return "change_class_not_allowed"
    if envelope.level_for(unit.required_capability) != "allowed":
        return "capability_not_authorized"
    if not target_repository:
        return "target_repository_missing"
    if target_repository not in settings.allowed_target_repositories:
        return "target_repository_not_allowed"
    return _authority_violation_reason(envelope) or _conformance_blocked_reason(envelope)


def _is_skipped_reason(reason: str) -> bool:
    # A window refusal is SKIPPED rather than BLOCKED, alongside the off-switch and for the same
    # reason: nothing is wrong with the unit and nobody has to act. Recording it as blocked would
    # put a nightly recurrence into the surfaces an operator reads for units that need attention.
    return reason in {"dispatch_disabled", OUTSIDE_CHANGE_WINDOW} or reason in {
        "authority_command_run_required",
        "authority_allowed_commands_invalid",
        "authority_mutation_commands_invalid",
        "authority_mutation_command_not_allowed",
    }


def _authority_violation_reason(envelope: AuthorityEnvelope) -> str | None:
    violation = runner_command_authority_violation(envelope)
    return violation.code if violation is not None else None


def _change_class(envelope: AuthorityEnvelope, required_capability: str) -> str:
    return envelope.change_class or required_capability


def _target_repository(envelope: AuthorityEnvelope) -> str:
    """Routing is a property of the unit, never of the process.

    The runner refuses to act unless the workflow it runs in IS the unit's target repo,
    so a process-global target would misroute every fan-out unit rather than fail.
    """
    repository = envelope.constraints.get("target_repository")
    return repository if isinstance(repository, str) else ""


def _conformance_blocked_reason(envelope: AuthorityEnvelope) -> str | None:
    """Conformance is attested per unit, against that unit's own target repository.

    The package revision's enforcement snapshot cannot carry this: it is written once at
    intake, before decomposition has chosen the target repositories, so one snapshot could
    not honestly describe a fan-out across several repos. The unit's envelope can, and
    because the envelope is fingerprinted, the human's authority approval attests it.

    `accepted_standards` must come from a real waiver source (project-standards
    `exceptions:` frontmatter, security-standards `.security-scan-allow.toml`) — never
    echoed from `standards_touched`, or the subset branch below admits everything.
    """
    conformance = envelope.conformance
    if conformance is None:
        return "conformance_missing"
    touched = _string_set(conformance.get("standards_touched"))
    accepted = _string_set(conformance.get("accepted_standards"))
    status = conformance.get("status")
    if status == "green":
        return None
    if touched and touched <= accepted:
        return None
    return "conformance_not_green"


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def signature_failure_count(session: Session, unit_id: uuid.UUID, signature: str) -> int:
    """Failures ALREADY on disk for this (unit, failure signature). At rest -- no `+ 1`."""
    return (
        session.scalar(
            select(func.count())
            .select_from(DispatchRecord)
            .where(
                DispatchRecord.work_unit_id == unit_id,
                DispatchRecord.failure_signature == signature,
                DispatchRecord.status.in_(("failed", "blocked")),
            )
        )
        or 0
    )


def circuit_open(count: int, threshold: int) -> bool:
    """One predicate, two call sites with different tenses.

    Dispatch is PROSPECTIVE: it is about to write the failure it is judging, so it passes
    `count + 1`. A read-only view (the AC-005 dead-letter surface) reads what is already there,
    so it passes `count`. Putting the `+ 1` inside this function would show that view a breaker
    open one failure early; putting it at both call sites would double-count. It lives at the
    dispatch call site, and only there.
    """
    return count >= threshold


def _next_runner_attempt(session: Session, unit: WorkUnit) -> int:
    latest = session.scalar(
        select(DispatchRecord.runner_attempt)
        .where(DispatchRecord.work_unit_id == unit.id)
        .order_by(DispatchRecord.runner_attempt.desc())
        .limit(1)
    )
    return max(unit.attempt_count, latest or 0) + 1


def _payload(unit: WorkUnit, settings: DispatchSettings, repository: str) -> dict[str, Any]:
    # Only inputs the caller workflow declares: GitHub rejects the whole dispatch with 422
    # `Unexpected inputs provided` otherwise. The callback URL is not one of them — the
    # caller workflow passes it to the reusable workflow itself.
    inputs = {"work_unit_id": str(unit.id)}
    return {
        "workflow_dispatch": {
            "repository": repository,
            "workflow_id": settings.workflow_id,
            "ref": settings.workflow_ref,
            "inputs": inputs,
            "orchestrator_url": settings.orchestrator_url,
        },
        "provenance_required": {
            "trailers": ["SDS-Unit", "SDS-Package-Rev"],
            "sds_unit": str(unit.id),
            "sds_package_rev": str(unit.work_package_revision_id),
        },
    }


def _record_authority_gate_not_required(
    session: Session,
    command: DispatchCommand,
    unit: WorkUnit,
    gate: AuthorityGate,
) -> None:
    """The trace left when the human-authority requirement was lifted by policy (ADR-0011).

    Deliberately NOT an approval. It is an event, it names the system actor that read the artifact
    rather than a person, it says the requirement did not apply rather than that anybody agreed to
    anything, and `unit.authority_approval_id` stays null -- so no query for a human's approval can
    ever return it. What it carries is what a later reader needs in order to re-derive the
    decision: the patterns that recognised the envelope, the artifact version and file they were
    read from, and the fingerprint of the envelope they recognised. The artifact is editable, so
    citing the version is not decoration: it is the difference between a record of what was
    decided and a guess made from whatever the file says later.
    """
    event = Event(
        actor_id=command.actor.actor_id,
        action="authority.human_gate_not_required",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=unit.state,
        to_state=unit.state,
        payload={
            "recognised_by": list(gate.recognised_by),
            "policy_version": gate.policy_version,
            "policy_source": gate.policy_source,
            "authority_fingerprint": unit.authority_fingerprint,
            "runner_attempt": command.runner_attempt,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=f"{command.idempotency_key}:authority-gate",
    )
    session.add(event)
    session.flush()


def _record_dispatch(
    session: Session,
    command: DispatchCommand,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    settings: DispatchSettings,
    *,
    repository: str,
    status: str,
    payload: Mapping[str, Any],
    reason_code: str | None = None,
    failure_signature: str | None = None,
    github_run_id: str | None = None,
    github_run_url: str | None = None,
) -> DispatchRecord:
    record = DispatchRecord(
        work_unit_id=unit.id,
        work_package_revision_id=revision.id,
        runner_attempt=command.runner_attempt,
        status=status,
        reason_code=reason_code,
        idempotency_key=command.idempotency_key,
        target_repository=repository,
        workflow_id=settings.workflow_id,
        workflow_ref=settings.workflow_ref,
        github_run_id=github_run_id,
        github_run_url=github_run_url,
        failure_signature=failure_signature,
        payload=dict(payload),
    )
    session.add(record)
    session.flush()
    event = Event(
        actor_id=command.actor.actor_id,
        action=f"dispatch.{status}",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=unit.state,
        to_state=unit.state,
        payload={
            "dispatch_record_id": str(record.id),
            "runner_attempt": command.runner_attempt,
            "reason_code": reason_code,
            "target_repository": repository,
            "workflow_id": settings.workflow_id,
            "workflow_ref": settings.workflow_ref,
            "github_run_id": github_run_id,
            "failure_signature": failure_signature,
        },
        correlation_id=uuid.uuid4(),
        idempotency_key=f"{command.idempotency_key}:event",
    )
    session.add(event)
    session.flush()
    record.event_id = event.id
    session.flush()
    return record
