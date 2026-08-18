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
from orchestrator.persistence.repositories import PackageRepository
from orchestrator.reach_vocabulary import carry_reach, validate_reach
from orchestrator.services.follow_ups import validate_follow_up
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import register_revision
from orchestrator.services.verifier_evaluators import SUPPORTED_CRITERION_EVIDENCE_TYPES

_INTAKE_ACTION = "package_revision.intake_registered"
_INTAKE_SOURCE = "package_cli"
_PROTOCOL_FIXTURE_SOURCE = "protocol_fixture"
_VALID_STATUSES = frozenset({"approved"})
_VALID_PROTOCOL_FIXTURE_STATUSES = frozenset({"closed"})
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
    approval_event_id: str
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
    intake_purpose: str = "executable"
    follow_up: dict[str, Any] | None = None
    # ADR-0026: the change-manager record a human approved to cause this work. Optional,
    # because most intakes have no originating record and every intake before ADR-0026 had
    # none. Recorded on trust: change-manager is the authority on its own records, the carry
    # verifies the locator against the real package checkout before it prepares a payload, and
    # a human gate that could not be completed while a foreign service was unreachable would be
    # a worse failure than the one this would prevent. `EstatePrMerge.change_record_id` is the
    # same trade against the same service -- the permission, written down at the moment it was
    # exercised.
    change_record_id: int | None = None


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
    intake_source = _intake_source(command)
    if isinstance(intake_source, DomainError):
        raise intake_source
    status_error = _status_error(command)
    if status_error is not None:
        raise status_error
    if command.intake_purpose == "protocol_fixture":
        fixture_error = _protocol_fixture_error(command)
        if fixture_error is not None:
            raise fixture_error
    if command.verification_mode != _VERIFICATION_MODE:
        raise DomainError(
            "package_intake_verification_invalid",
            "package intake requires caller_attested_cli_verified verification",
            None,
        )
    follow_up = validate_follow_up(command.follow_up)
    reach = validate_reach(command.enforcement_snapshot.get("reach"))
    acceptance_criteria = _validated_acceptance_criteria(command.acceptance_criteria)
    PackageRepository(session).lock_package_intake(command.package_id)
    replay = _intake_replay(session, command, actor)
    if replay is not None:
        return replay

    enriched_snapshot = _normalize_json(
        carry_reach(
            {
                **command.enforcement_snapshot,
                "acceptance_criteria": [criterion.ac_id for criterion in acceptance_criteria],
            },
            reach,
        )
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
            intake_source=intake_source,
            approval_ledger_commit=command.approval_ledger_commit,
            verification_mode=command.verification_mode,
            verification_limitations=command.verification_limitations,
            follow_up=follow_up,
            change_record_id=command.change_record_id,
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


def _status_error(command: PackageIntakeCommand) -> DomainError | None:
    if command.intake_purpose == "executable" and command.status_at_intake not in _VALID_STATUSES:
        raise DomainError(
            "package_intake_status_invalid",
            "package intake requires approved status",
            None,
        )
    if (
        command.intake_purpose == "protocol_fixture"
        and command.status_at_intake not in _VALID_PROTOCOL_FIXTURE_STATUSES
    ):
        return DomainError(
            "package_intake_status_invalid",
            "protocol fixture intake requires closed status",
            None,
        )
    return None


def _intake_source(command: PackageIntakeCommand) -> str | DomainError:
    if command.intake_purpose == "executable":
        return _INTAKE_SOURCE
    if command.intake_purpose == "protocol_fixture":
        return _PROTOCOL_FIXTURE_SOURCE
    return DomainError(
        "package_intake_purpose_invalid",
        "package intake purpose is invalid",
        None,
    )


def _protocol_fixture_error(command: PackageIntakeCommand) -> DomainError | None:
    limitations = command.verification_limitations
    if not isinstance(limitations, dict) or limitations.get("protocol_fixture_only") is not True:
        return DomainError(
            "package_intake_verification_invalid",
            "protocol fixture intake requires protocol_fixture_only verification limitation",
            None,
        )
    return None


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
        # The verifier keys its evaluation on `criterion.evidence_type` (normalized the same way).
        # An unknown type here would fall through to `judgment_required` at verify time,
        # indistinguishable from a typo -- so reject it at the gate with a named error instead.
        if criterion.evidence_type.strip().lower() not in SUPPORTED_CRITERION_EVIDENCE_TYPES:
            raise DomainError(
                "unknown_evidence_type",
                f"acceptance criterion {criterion.ac_id} declares an unknown "
                f"evidence_type {criterion.evidence_type!r}",
                "declare one of the supported criterion evidence types",
            )
    return acceptance_criteria


def _intake_replay(
    session: Session,
    command: PackageIntakeCommand,
    actor: ActorContext,
) -> WorkPackageRevision | None:
    event = session.scalar(select(Event).where(Event.idempotency_key == command.idempotency_key))
    if event is None:
        return None
    expected = _command_identity(command, actor)
    observed = event.payload.get("command")
    if (
        event.action != _INTAKE_ACTION
        or event.subject_type != "work_package_revision"
        or (observed != expected and not _legacy_identity_matches(observed, expected, command))
    ):
        raise _idempotency_conflict()
    revision = session.get(WorkPackageRevision, event.subject_id)
    if revision is None:
        raise DomainError("event_invalid", "intake event subject does not exist", None)
    return revision


def _legacy_identity_matches(
    observed: object,
    expected: dict[str, Any],
    command: PackageIntakeCommand,
) -> bool:
    """True when `observed` is `expected` minus exactly the keys a known legacy shape lacks.

    Two independent legacy dimensions, each handled only when the OBSERVED (stored) event
    actually lacks the key -- never unconditionally, or an event that legitimately carries the
    key would mismatch because `legacy` would then lack a key `observed` still has:

    - `follow_up` (WS-P2.8) applies to EVERY intake_purpose. Both the executable and
      protocol_fixture lanes could have been registered before follow_up existed, so an event
      from either lane may be missing only that key.
    - `intake_purpose` (pre-existing) only ever applied to the executable lane: protocol_fixture
      intake did not exist before intake_purpose did, so a protocol_fixture event has always
      carried it and never needs this exemption. The `verification_limitations` normalization
      that goes with it is scoped the same way, for the same reason.
    - `change_record_id` (ADR-0026) applies to every intake_purpose, like `follow_up`, and for
      the same reason: both lanes could have been registered before the key existed.
    """
    if not isinstance(observed, dict):
        return False
    legacy = dict(expected)
    if command.follow_up is None and "follow_up" not in observed:
        legacy.pop("follow_up", None)
    if command.change_record_id is None and "change_record_id" not in observed:
        legacy.pop("change_record_id", None)
    if command.intake_purpose == "executable" and "intake_purpose" not in observed:
        legacy.pop("intake_purpose", None)
        expected_limitations = legacy.get("verification_limitations")
        if isinstance(expected_limitations, dict):
            expected_limitations = dict(expected_limitations)
            expected_limitations.pop("protocol_fixture_only", None)
            legacy["verification_limitations"] = expected_limitations
    return observed == legacy


def _command_identity(
    command: PackageIntakeCommand,
    actor: ActorContext,
) -> dict[str, Any]:
    return {
        "action": _INTAKE_ACTION,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "expected_version": command.expected_version,
        "intake_purpose": command.intake_purpose,
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
        "follow_up": _normalize_json(command.follow_up),
        # ADR-0026. IN the identity, deliberately: two intakes of one package revision that name
        # different change records are two different registrations, and leaving it out would
        # make the second a silent replay of the first -- which defeats recording a cause at all.
        # It therefore needs the legacy exemption below, exactly as `follow_up` does.
        "change_record_id": command.change_record_id,
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
