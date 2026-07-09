from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    KNOWLEDGE_PROMOTION_AUTHORITIES,
    KNOWLEDGE_PROMOTION_TARGET_BRAINS,
    KNOWLEDGE_PROMOTION_TARGET_TYPES,
    Event,
    KnowledgePromotionProposal,
    KnowledgePromotionProposalAction,
    Observation,
)
from orchestrator.services.lifecycle import ActorContext

IDEMPOTENCY_LOCK_NAMESPACE = 0x57533632
MAX_TEXT = 4000
MAX_SUMMARY = 700
MAX_JSON_BYTES = 5000


@dataclass(frozen=True)
class KnowledgePromotionProposalCommand:
    actor: ActorContext
    correlation_identity: str
    source_observation_ids: list[uuid.UUID]
    correlation_summary: str
    target_brain: str
    target_type: str
    authority: str
    proposed_payload: dict[str, Any]
    applicability: dict[str, Any]
    provenance: dict[str, Any]
    idempotency_key: str
    release_artifact_binding_id: uuid.UUID | None = None
    deployment_observation_id: uuid.UUID | None = None
    work_unit_id: uuid.UUID | None = None
    package_revision_id: uuid.UUID | None = None
    expected_version: int | None = None


@dataclass(frozen=True)
class KnowledgePromotionProposalFilters:
    target_brain: str | None = None
    target_type: str | None = None
    state: str | None = None


@dataclass(frozen=True)
class KnowledgePromotionSubmitCommand:
    actor: ActorContext
    proposal_id: uuid.UUID
    idempotency_key: str
    expected_version: int | None = None


@dataclass(frozen=True)
class BrainProposalResult:
    record_id: str
    status: str
    response: dict[str, Any]


class BrainProposalClient(Protocol):
    def submit(self, proposal: KnowledgePromotionProposal) -> BrainProposalResult: ...


class HttpBrainProposalClient:
    def __init__(
        self,
        *,
        target_urls: dict[str, str],
        credentials: dict[str, str],
        timeout_seconds: float = 10,
    ) -> None:
        self._target_urls = target_urls
        self._credentials = credentials
        self._timeout_seconds = timeout_seconds

    def submit(self, proposal: KnowledgePromotionProposal) -> BrainProposalResult:
        base_url = self._target_urls.get(proposal.target_brain)
        credential = self._credentials.get(proposal.target_brain)
        if not base_url or not credential:
            raise DomainError(
                "brain_target_unconfigured",
                "target brain proposal endpoint is not configured",
                "configure a contributor-only brain proposal credential",
            )
        endpoint = f"{base_url.rstrip('/')}/api/proposals/{proposal.target_type}s"
        payload = dict(proposal.proposed_payload)
        payload["authority"] = proposal.authority
        response = httpx.post(
            endpoint,
            headers={"x-brain-key": credential},
            json=payload,
            timeout=self._timeout_seconds,
        )
        if response.status_code >= 400:
            raise DomainError(
                "brain_proposal_failed",
                "target brain rejected the proposed knowledge record",
                f"brain returned HTTP {response.status_code}",
            )
        data = response.json()
        record = data.get(proposal.target_type)
        if not isinstance(record, dict) or record.get("status") != "proposed":
            raise DomainError(
                "brain_proposal_failed",
                "target brain did not return a proposed record",
                "verify the brain proposal endpoint",
            )
        return BrainProposalResult(
            record_id=str(record.get("id")),
            status=str(record.get("status")),
            response=_bounded_brain_response(data),
        )


def create_knowledge_promotion_proposal(
    session: Session,
    command: KnowledgePromotionProposalCommand,
) -> KnowledgePromotionProposal | DomainError:
    try:
        row = _create_knowledge_promotion_proposal(session, command)
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        session.rollback()
        return DomainError(
            "knowledge_promotion_conflict",
            "knowledge promotion proposal conflicts with existing proposal identity",
            "review the existing proposal before creating a new correlation",
        )
    except Exception:
        session.rollback()
        raise


def list_knowledge_promotion_proposals(
    session: Session,
    filters: KnowledgePromotionProposalFilters | None = None,
) -> tuple[KnowledgePromotionProposal, ...]:
    stmt = select(KnowledgePromotionProposal)
    if filters is not None:
        if filters.target_brain is not None:
            stmt = stmt.where(KnowledgePromotionProposal.target_brain == filters.target_brain)
        if filters.target_type is not None:
            stmt = stmt.where(KnowledgePromotionProposal.target_type == filters.target_type)
    rows = tuple(session.scalars(stmt.order_by(KnowledgePromotionProposal.proposed_at)))
    if filters is not None and filters.state is not None:
        return tuple(row for row in rows if proposal_state(session, row.id) == filters.state)
    return rows


def submit_knowledge_promotion_to_brain(
    session: Session,
    command: KnowledgePromotionSubmitCommand,
    client: BrainProposalClient,
) -> KnowledgePromotionProposalAction | DomainError:
    try:
        row = _submit_knowledge_promotion_to_brain(session, command, client)
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError:
        session.rollback()
        return DomainError(
            "knowledge_promotion_conflict",
            "knowledge promotion proposal action conflicts with existing history",
            "reload the proposal before retrying",
        )
    except Exception:
        session.rollback()
        raise


def proposal_state(session: Session, proposal_id: uuid.UUID) -> str:
    action = session.scalar(
        select(KnowledgePromotionProposalAction)
        .where(KnowledgePromotionProposalAction.proposal_id == proposal_id)
        .order_by(KnowledgePromotionProposalAction.action_at.desc())
        .limit(1)
    )
    return action.action if action is not None else "proposed"


def proposal_actions(
    session: Session,
    proposal_id: uuid.UUID,
) -> tuple[KnowledgePromotionProposalAction, ...]:
    return tuple(
        session.scalars(
            select(KnowledgePromotionProposalAction)
            .where(KnowledgePromotionProposalAction.proposal_id == proposal_id)
            .order_by(KnowledgePromotionProposalAction.action_at)
        )
    )


def _create_knowledge_promotion_proposal(
    session: Session,
    command: KnowledgePromotionProposalCommand,
) -> KnowledgePromotionProposal:
    _require_human(command.actor, "proposal creation")
    _validate_expected_version(command.expected_version)
    command = _normalized_command(command)
    _validate_proposal_command(command)
    _lock_idempotency_key(session, command.idempotency_key)
    observation_hashes = _source_observation_hashes(session, command.source_observation_ids)
    proposal_hash = _proposal_hash(command, observation_hashes)
    payload = _event_payload(command, observation_hashes, proposal_hash)

    existing = session.scalar(
        select(KnowledgePromotionProposal).where(
            KnowledgePromotionProposal.idempotency_key == command.idempotency_key
        )
    )
    if existing is not None:
        _validate_proposal_replay(session, existing, payload)
        return existing

    same_hash = session.scalar(
        select(KnowledgePromotionProposal).where(
            KnowledgePromotionProposal.proposal_hash == proposal_hash
        )
    )
    if same_hash is not None:
        return same_hash

    same_correlation = session.scalar(
        select(KnowledgePromotionProposal).where(
            KnowledgePromotionProposal.correlation_identity == command.correlation_identity
        )
    )
    if same_correlation is not None:
        raise DomainError(
            "knowledge_promotion_conflict",
            "correlation identity already has a different proposal",
            "record an explicit supersession model before changing promotion content",
        )

    now = TransactionClock().now(session)
    proposal_id = uuid.uuid4()
    event_id = uuid.uuid4()
    session.add(
        Event(
            id=event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="knowledge_promotion.proposed",
            subject_type="knowledge_promotion_proposal",
            subject_id=proposal_id,
            from_state=None,
            to_state="proposed",
            payload={"command": payload},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    row = KnowledgePromotionProposal(
        id=proposal_id,
        correlation_identity=command.correlation_identity,
        source_observation_ids=[str(item) for item in command.source_observation_ids],
        source_observation_hashes=observation_hashes,
        release_artifact_binding_id=command.release_artifact_binding_id,
        deployment_observation_id=command.deployment_observation_id,
        work_unit_id=command.work_unit_id,
        package_revision_id=command.package_revision_id,
        correlation_summary=command.correlation_summary,
        target_brain=command.target_brain,
        target_type=command.target_type,
        authority=command.authority,
        applicability=command.applicability,
        proposed_payload=command.proposed_payload,
        provenance=command.provenance,
        proposal_hash=proposal_hash,
        proposed_by=command.actor.actor_id,
        proposed_at=now,
        event_id=event_id,
        idempotency_key=command.idempotency_key,
    )
    session.add(row)
    session.flush()
    return row


def _submit_knowledge_promotion_to_brain(
    session: Session,
    command: KnowledgePromotionSubmitCommand,
    client: BrainProposalClient,
) -> KnowledgePromotionProposalAction:
    _require_human(command.actor, "brain submission")
    _validate_expected_version(command.expected_version)
    _lock_idempotency_key(session, command.idempotency_key)
    proposal = session.get(KnowledgePromotionProposal, command.proposal_id)
    if proposal is None:
        raise DomainError(
            "knowledge_promotion_not_found",
            "knowledge promotion proposal does not exist",
            None,
        )
    existing_by_key = session.scalar(
        select(KnowledgePromotionProposalAction).where(
            KnowledgePromotionProposalAction.idempotency_key == command.idempotency_key
        )
    )
    if existing_by_key is not None:
        if existing_by_key.proposal_id != command.proposal_id:
            raise _idempotency_conflict()
        return existing_by_key
    existing_submission = session.scalar(
        select(KnowledgePromotionProposalAction).where(
            KnowledgePromotionProposalAction.proposal_id == command.proposal_id,
            KnowledgePromotionProposalAction.action == "submitted_to_brain",
        )
    )
    if existing_submission is not None:
        return existing_submission

    result = client.submit(proposal)
    if result.status != "proposed":
        raise DomainError(
            "brain_proposal_failed",
            "target brain did not leave the record proposed",
            "brain approval must remain a separate Devon action",
        )
    now = TransactionClock().now(session)
    action_id = uuid.uuid4()
    event_id = uuid.uuid4()
    event_payload = {
        "proposal_id": str(proposal.id),
        "target_brain": proposal.target_brain,
        "target_type": proposal.target_type,
        "brain_record_id": result.record_id,
        "brain_status": result.status,
    }
    session.add(
        Event(
            id=event_id,
            occurred_at=now,
            actor_id=command.actor.actor_id,
            action="knowledge_promotion.submitted_to_brain",
            subject_type="knowledge_promotion_proposal",
            subject_id=proposal.id,
            from_state="proposed",
            to_state="submitted_to_brain",
            payload=event_payload,
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    row = KnowledgePromotionProposalAction(
        id=action_id,
        proposal_id=proposal.id,
        action="submitted_to_brain",
        brain_record_id=result.record_id,
        brain_status=result.status,
        brain_response=result.response,
        reason=None,
        action_by=command.actor.actor_id,
        action_at=now,
        event_id=event_id,
        idempotency_key=command.idempotency_key,
    )
    session.add(row)
    session.flush()
    return row


def _normalized_command(
    command: KnowledgePromotionProposalCommand,
) -> KnowledgePromotionProposalCommand:
    return KnowledgePromotionProposalCommand(
        actor=command.actor,
        correlation_identity=command.correlation_identity.strip(),
        source_observation_ids=sorted(set(command.source_observation_ids)),
        correlation_summary=command.correlation_summary.strip(),
        target_brain=command.target_brain.strip(),
        target_type=command.target_type.strip(),
        authority=command.authority.strip(),
        proposed_payload=_normalize_json(command.proposed_payload),
        applicability=_normalize_json(command.applicability),
        provenance=_normalize_json(command.provenance),
        idempotency_key=command.idempotency_key.strip(),
        release_artifact_binding_id=command.release_artifact_binding_id,
        deployment_observation_id=command.deployment_observation_id,
        work_unit_id=command.work_unit_id,
        package_revision_id=command.package_revision_id,
        expected_version=command.expected_version,
    )


def _validate_proposal_command(command: KnowledgePromotionProposalCommand) -> None:
    if not command.source_observation_ids:
        raise DomainError("knowledge_promotion_invalid", "source observations are required", None)
    if command.target_brain not in KNOWLEDGE_PROMOTION_TARGET_BRAINS:
        raise DomainError("knowledge_promotion_invalid", "target brain is not allowlisted", None)
    if command.target_type not in KNOWLEDGE_PROMOTION_TARGET_TYPES:
        raise DomainError("knowledge_promotion_invalid", "target type is not allowlisted", None)
    if command.authority not in KNOWLEDGE_PROMOTION_AUTHORITIES:
        raise DomainError("knowledge_promotion_invalid", "authority is not allowlisted", None)
    if len(command.correlation_summary) > MAX_SUMMARY:
        raise DomainError("knowledge_promotion_invalid", "correlation summary is too large", None)
    _validate_payload_shape(command.target_brain, command.target_type, command.proposed_payload)
    _validate_json(command.proposed_payload)
    _validate_json(command.applicability)
    _validate_json(command.provenance)


def _validate_payload_shape(target_brain: str, target_type: str, payload: dict[str, Any]) -> None:
    required: set[str]
    if target_type == "lesson":
        required = {"title", "content"}
    elif target_brain == "code":
        required = {"road_slug", "severity", "category", "rule", "reason"}
    else:
        required = {"severity", "category", "rule", "reason"}
    missing = [key for key in sorted(required) if not payload.get(key)]
    if missing:
        raise DomainError(
            "knowledge_promotion_invalid",
            f"proposed payload is missing required fields: {', '.join(missing)}",
            None,
        )


def _source_observation_hashes(
    session: Session,
    observation_ids: list[uuid.UUID],
) -> list[str]:
    hashes: list[str] = []
    for observation_id in observation_ids:
        observation = session.get(Observation, observation_id)
        if observation is None:
            raise DomainError(
                "observation_not_found",
                "referenced observation does not exist",
                str(observation_id),
            )
        hashes.append(observation.normalized_fact_hash)
    return hashes


def _proposal_hash(
    command: KnowledgePromotionProposalCommand,
    observation_hashes: list[str],
) -> str:
    identity = {
        "correlation_identity": command.correlation_identity,
        "source_observation_ids": [str(item) for item in command.source_observation_ids],
        "source_observation_hashes": observation_hashes,
        "target_brain": command.target_brain,
        "target_type": command.target_type,
        "authority": command.authority,
        "applicability": command.applicability,
        "proposed_payload": command.proposed_payload,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _event_payload(
    command: KnowledgePromotionProposalCommand,
    observation_hashes: list[str],
    proposal_hash: str,
) -> dict[str, Any]:
    return {
        "correlation_identity": command.correlation_identity,
        "source_observation_ids": [str(item) for item in command.source_observation_ids],
        "source_observation_hashes": observation_hashes,
        "correlation_summary": command.correlation_summary,
        "target_brain": command.target_brain,
        "target_type": command.target_type,
        "authority": command.authority,
        "applicability": command.applicability,
        "proposed_payload": command.proposed_payload,
        "provenance": command.provenance,
        "proposal_hash": proposal_hash,
    }


def _validate_proposal_replay(
    session: Session,
    row: KnowledgePromotionProposal,
    payload: dict[str, Any],
) -> None:
    event = session.get(Event, row.event_id)
    if (
        event is None
        or event.action != "knowledge_promotion.proposed"
        or event.subject_id != row.id
        or event.payload.get("command") != payload
    ):
        raise _idempotency_conflict()


def _validate_expected_version(expected_version: int | None) -> None:
    if expected_version not in {None, 0}:
        raise DomainError(
            "version_conflict",
            "knowledge promotion commands require expected version 0",
            "reload",
        )


def _require_human(actor: ActorContext, action: str) -> None:
    if not actor.actor_id or actor.role is not ActorRole.HUMAN:
        raise DomainError(
            "human_actor_required",
            f"knowledge promotion {action} requires a registered human actor",
            None,
        )


def _validate_json(value: Any) -> None:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise DomainError("knowledge_promotion_invalid", "proposal JSON is too large", None)
    _validate_json_value(value)


def _validate_json_value(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not key.strip():
                raise DomainError("knowledge_promotion_invalid", "proposal key is invalid", None)
            _validate_json_value(child)
    elif isinstance(value, list):
        for child in value:
            _validate_json_value(child)
    elif isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise DomainError("knowledge_promotion_invalid", "proposal text is too large", None)
    elif value is None or isinstance(value, bool | int | float):
        return
    else:
        raise DomainError("knowledge_promotion_invalid", "proposal value is unsupported", None)


def _normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key).strip(): _normalize_json(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_json(child) for child in value]
    if isinstance(value, str):
        return value.strip()
    return value


def _bounded_brain_response(data: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= MAX_JSON_BYTES:
        return data
    return {"truncated": True}


def _lock_idempotency_key(session: Session, idempotency_key: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:idempotency_key))"),
        {
            "namespace": IDEMPOTENCY_LOCK_NAMESPACE,
            "idempotency_key": idempotency_key,
        },
    )


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different knowledge promotion command",
        "use a new idempotency key",
    )
