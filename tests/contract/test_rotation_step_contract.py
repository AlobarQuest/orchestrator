"""The verifier reads two things the rotation executor writes; both are held equal here.

`rotation_verification` judges a rotation step by the executor's evidence type and by the
coverage note its verify_consumers step writes. A change to either on the executor's side would
silently hand every rotation unit back to a person, or, for the note, let the before-revoke rule
read nothing.
"""

from __future__ import annotations

import inspect

from orchestrator.services.verifier.rotation_verification import (
    ROTATION_STEP_EVIDENCE_TYPE,
    rotation_step_verdict,
)
from rotation_worker import contract, worker

COVERAGE = "{verified - len(missing)} of {verified} consumers hold or attest the new value"
BEFORE_REVOKE = "; the old value is live before the revoke"
CRITERION = (
    "external: the verify_consumers step's rotation-step/1 evidence, "
    "filed by the rotation executor."
)


def _payload(note: str) -> dict[str, object]:
    return {
        "format": "rotation-step/1",
        "step": "verify_consumers",
        "fingerprints": [
            {"which": "old", "length": 64, "sha256_prefix": "3bfa95a969b123b6"},
            {"which": "new", "length": 43, "sha256_prefix": "6a4a452ace49d87c"},
        ],
        "note": note,
    }


def _note(verified: int, missing: int, *, revoke_next: bool) -> str:
    note = f"{verified - missing} of {verified} consumers hold or attest the new value"
    return note + BEFORE_REVOKE if revoke_next else note


def test_the_evidence_type_is_the_executors() -> None:
    assert worker.EVIDENCE_TYPE == ROTATION_STEP_EVIDENCE_TYPE


def test_the_executor_still_writes_the_coverage_note_the_verifier_reads() -> None:
    source = inspect.getsource(contract)
    assert COVERAGE in source
    assert BEFORE_REVOKE in source


def test_the_verifier_reads_the_executors_notes() -> None:
    def verdict(note: str) -> str | None:
        return rotation_step_verdict(
            "external_attestation", CRITERION, "verify", ROTATION_STEP_EVIDENCE_TYPE, _payload(note)
        )

    assert verdict(_note(6, 0, revoke_next=True)) is None
    assert verdict(_note(6, 4, revoke_next=False)) is None
    assert verdict(_note(6, 1, revoke_next=True)) is not None
