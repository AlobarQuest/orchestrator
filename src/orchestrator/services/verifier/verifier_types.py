import uuid
from dataclasses import dataclass
from typing import Literal

from orchestrator.kernel.states import ActorContext, WorkUnitState
from orchestrator.services.lifecycle.rotation_claims import NO_ROTATION_EXECUTORS, RotationExecutors
from orchestrator.services.verifier.verifier_evaluators import EvaluationStatus

VerificationResult = Literal["completed", "revision_required", "awaiting_review", "failed"]


@dataclass(frozen=True)
class VerifyCommand:
    unit_id: uuid.UUID
    actor: ActorContext
    expected_version: int
    idempotency_key: str
    # Whose evidence may prove a rotation unit's step (ADR-0055 amendment 14).
    rotation_executors: RotationExecutors = NO_ROTATION_EXECUTORS


@dataclass(frozen=True)
class VerifierEvaluation:
    ac_id: str
    evidence_type: str
    status: EvaluationStatus
    outcome: str | None
    evidence_id: uuid.UUID | None
    finding_evidence_id: uuid.UUID | None
    adjudication_id: uuid.UUID | None
    reason: str


@dataclass(frozen=True)
class VerifierResult:
    unit_id: uuid.UUID
    state: WorkUnitState
    version: int
    result: VerificationResult
    evaluations: tuple[VerifierEvaluation, ...]
