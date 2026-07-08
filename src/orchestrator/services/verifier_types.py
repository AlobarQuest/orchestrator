import uuid
from dataclasses import dataclass
from typing import Literal

from orchestrator.kernel.states import WorkUnitState
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.verifier_evaluators import EvaluationStatus

VerificationResult = Literal["completed", "revision_required", "awaiting_review", "failed"]


@dataclass(frozen=True)
class VerifyCommand:
    unit_id: uuid.UUID
    actor: ActorContext
    expected_version: int
    idempotency_key: str


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
