"""The verifier's rotation branch never reaches past a rotation unit (ADR-0055 amendment 14).

`external_attestation` keeps its human floor; only a rotation unit's own executor's step evidence
decides one. These cases put a perfect rotation-step payload, filed by the configured executor's
identity, on a unit that is not a rotation unit: the criterion must still go to a person. The
rotation unit's own path is exercised end to end in `tests/api/test_rotation_worker_flow.py`.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.services.lifecycle.rotation_claims import RotationExecutors
from orchestrator.services.verifier.rotation_verification import ROTATION_STEP_EVIDENCE_TYPE
from orchestrator.services.verifier.verifier import VerifyCommand, verify_work_unit
from tests.fixtures.named_check import (
    VERIFIER,
    WORKER,
    mapped_submitted_unit,
    record_worker_evidence,
)

CRITERION = "external: the store step's rotation-step/1 evidence, filed by the rotation executor."
STORE = {
    "format": "rotation-step/1",
    "step": "store",
    "fingerprints": [
        {"which": "old", "length": 64, "sha256_prefix": "3bfa95a969b123b6"},
        {"which": "new", "length": 43, "sha256_prefix": "6a4a452ace49d87c"},
    ],
}


def test_non_rotation_unit_stays_judgment(migrated_session: Session) -> None:
    unit = mapped_submitted_unit(
        migrated_session,
        key="not-a-rotation",
        evidence_type="external_attestation",
        evidence=CRITERION,
    )
    record_worker_evidence(
        migrated_session,
        unit,
        evidence_type=ROTATION_STEP_EVIDENCE_TYPE,
        payload=STORE,
        idempotency_key="not-a-rotation-evidence",
    )

    result = verify_work_unit(
        migrated_session,
        VerifyCommand(
            unit_id=unit.id,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="verify-not-a-rotation",
            rotation_executors=RotationExecutors(vm=WORKER.actor_id),
        ),
    )

    assert result.state is WorkUnitState.AWAITING_REVIEW
    assert [e.status for e in result.evaluations] == ["judgment_required"]
    assert result.evaluations[0].adjudication_id is None
