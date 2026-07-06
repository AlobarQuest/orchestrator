import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityEnvelope
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, PackageAcceptanceCriterion, WorkPackageRevision
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import register_revision

_INTAKE_ACTION = "package_revision.intake_registered"
_INTAKE_SOURCE = "package_cli"
_VALID_STATUSES = frozenset({"approved", "executable"})
_VERIFICATION_MODE = "caller_attested_cli_verified"


@dataclass(frozen=True)
class AcceptanceCriterionProjection:
    ac_id: str
    condition: str
    evidence_type: str
    evidence: str
    approver: str


@dataclass(frozen=True)
class PackageIntakeCommand:
    package_id: str
    source_repository: str
    revision: int
    content_hash: str
    source_path: str
    source_commit: str
    approved_by: str
    approved_at: datetime
    approval_event_id: uuid.UUID
    approval_ledger_commit: str
    profile: str | None
    status_at_intake: str
    verification_mode: str
    verification_limitations: dict[str, Any] | list[Any] | None
    enforcement_snapshot: dict[str, Any]
    authority: AuthorityEnvelope
    registry_version: int
    acceptance_criteria: tuple[AcceptanceCriterionProjection, ...]
    idempotency_key: str
    expected_version: int


def register_package_intake(
    session: Session,
    command: PackageIntakeCommand,
    actor: ActorContext,
) -> WorkPackageRevision:
    _require_human(actor)
    if command.expected_version != 0:
        raise DomainError(
            "version_conflict",
            "package intake requires expected version 0",
            "reload",
            current_version=0,
        )
    if command.status_at_intake not in _VALID_STATUSES:
        raise DomainError(
            "package_intake_status_invalid",
            "package intake requires approved or executable status",
            None,
        )
    if command.verification_mode != _VERIFICATION_MODE:
        raise DomainError(
            "package_intake_verification_invalid",
            "package intake requires caller_attested_cli_verified verification",
            None,
        )
    acceptance_criteria = _validated_acceptance_criteria(command.acceptance_criteria)
    replay = _intake_replay(session, command, actor)
    if replay is not None:
        return replay

    enriched_snapshot = _normalize_json(
        {
            **command.enforcement_snapshot,
            "acceptance_criteria": [criterion.ac_id for criterion in acceptance_criteria],
        }
    )
    try:
        revision = register_revision(
            session,
            package_id=command.package_id,
            source_repository=command.source_repository,
            revision=command.revision,
            content_hash=command.content_hash,
            source_path=command.source_path,
            source_commit=command.source_commit,
            approved_by=command.approved_by,
            approved_at=command.approved_at,
            approval_event_id=command.approval_event_id,
            enforcement_snapshot=enriched_snapshot,
            authority=command.authority,
            registry_version=command.registry_version,
            profile=command.profile,
            status_at_intake=command.status_at_intake,
            intake_source=_INTAKE_SOURCE,
            approval_ledger_commit=command.approval_ledger_commit,
            verification_mode=command.verification_mode,
            verification_limitations=command.verification_limitations,
            actor_id=actor.actor_id,
            actor_role=actor.role,
            expected_version=command.expected_version,
        )
    except DomainError as error:
        if error.code == "revision_conflict":
            raise _package_intake_conflict() from error
        raise

    _sync_acceptance_criteria(session, revision.id, acceptance_criteria)
    _record_intake_event(session, revision.id, command, actor)
    return revision


def _require_human(actor: ActorContext) -> None:
    if not actor.actor_id or actor.role is not ActorRole.HUMAN:
        raise DomainError(
            "human_actor_required",
            "registration requires a registered human actor",
            None,
        )


def _validated_acceptance_criteria(
    acceptance_criteria: tuple[AcceptanceCriterionProjection, ...],
) -> tuple[AcceptanceCriterionProjection, ...]:
    if not acceptance_criteria:
        raise DomainError(
            "package_intake_acceptance_criteria_invalid",
            "package intake requires acceptance criteria",
            None,
        )
    observed_ids: set[str] = set()
    for criterion in acceptance_criteria:
        if criterion.ac_id in observed_ids:
            raise DomainError(
                "package_intake_acceptance_criteria_invalid",
                "package intake acceptance criteria must have unique ids",
                None,
            )
        observed_ids.add(criterion.ac_id)
    return acceptance_criteria


def _intake_replay(
    session: Session,
    command: PackageIntakeCommand,
    actor: ActorContext,
) -> WorkPackageRevision | None:
    event = session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key))
    if event is None:
        return None
    if (
        event.action != _INTAKE_ACTION
        or event.subject_type != "work_package_revision"
        or event.payload.get("command") != _command_identity(command, actor)
    ):
        raise _idempotency_conflict()
    revision = session.get(WorkPackageRevision, event.subject_id)
    if revision is None:
        raise DomainError("event_invalid", "intake event subject does not exist", None)
    return revision


def _command_identity(
    command: PackageIntakeCommand,
    actor: ActorContext,
) -> dict[str, Any]:
    return {
        "action": _INTAKE_ACTION,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "expected_version": command.expected_version,
        "package_id": command.package_id,
        "source_repository": command.source_repository,
        "revision": command.revision,
        "content_hash": command.content_hash,
        "source_path": command.source_path,
        "source_commit": command.source_commit,
        "approved_by": command.approved_by,
        "approved_at": command.approved_at.isoformat(),
        "approval_event_id": str(command.approval_event_id),
        "approval_ledger_commit": command.approval_ledger_commit,
        "profile": command.profile,
        "status_at_intake": command.status_at_intake,
        "verification_mode": command.verification_mode,
        "verification_limitations": _normalize_json(command.verification_limitations),
        "enforcement_snapshot": _normalize_json(command.enforcement_snapshot),
        "authority": command.authority.normalized(),
        "registry_version": command.registry_version,
        "acceptance_criteria": [
            {
                "ac_id": criterion.ac_id,
                "condition": criterion.condition,
                "evidence_type": criterion.evidence_type,
                "evidence": criterion.evidence,
                "approver": criterion.approver,
            }
            for criterion in command.acceptance_criteria
        ],
    }


def _sync_acceptance_criteria(
    session: Session,
    revision_id: uuid.UUID,
    acceptance_criteria: tuple[AcceptanceCriterionProjection, ...],
) -> None:
    existing = tuple(
        session.scalars(
            select(PackageAcceptanceCriterion)
            .where(PackageAcceptanceCriterion.work_package_revision_id == revision_id)
            .order_by(PackageAcceptanceCriterion.ac_id)
            .with_for_update()
        )
    )
    if existing:
        expected = tuple(
            (
                criterion.ac_id,
                criterion.condition,
                criterion.evidence_type,
                criterion.evidence,
                criterion.approver,
            )
            for criterion in sorted(acceptance_criteria, key=lambda criterion: criterion.ac_id)
        )
        observed = tuple(
            (
                criterion.ac_id,
                criterion.condition,
                criterion.evidence_type,
                criterion.evidence,
                criterion.approver,
            )
            for criterion in existing
        )
        if observed != expected:
            raise _package_intake_conflict()
        return
    for criterion in acceptance_criteria:
        session.add(
            PackageAcceptanceCriterion(
                work_package_revision_id=revision_id,
                ac_id=criterion.ac_id,
                condition=criterion.condition,
                evidence_type=criterion.evidence_type,
                evidence=criterion.evidence,
                approver=criterion.approver,
            )
        )
    session.flush()


def _record_intake_event(
    session: Session,
    revision_id: uuid.UUID,
    command: PackageIntakeCommand,
    actor: ActorContext,
) -> None:
    session.add(
        Event(
            occurred_at=TransactionClock().now(session),
            actor_id=actor.actor_id,
            action=_INTAKE_ACTION,
            subject_type="work_package_revision",
            subject_id=revision_id,
            from_state=None,
            to_state=None,
            payload={"command": _command_identity(command, actor)},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    session.flush()


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different operation",
        "use a new idempotency key",
    )


def _package_intake_conflict() -> DomainError:
    return DomainError(
        "package_intake_conflict",
        "package intake is already registered with different content",
        None,
    )


def _normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_json(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    return value
