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
    target = "the provider"
    probes = [
        {"target": target, "subject": "known_bad", "expected": "dead", "outcome": "dead"},
        {"target": target, "subject": "old", "expected": "live", "outcome": "live"},
    ]
    return {
        "probes": probes if BEFORE_REVOKE in note else [],
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
    assert 'note="deleted " + ' in source


def test_the_verifier_reads_the_executors_notes() -> None:
    def verdict(note: str) -> str | None:
        return rotation_step_verdict(
            "external_attestation", CRITERION, "verify", ROTATION_STEP_EVIDENCE_TYPE, _payload(note)
        )

    assert verdict(_note(6, 0, revoke_next=True)) is None
    assert verdict(_note(6, 4, revoke_next=False)) is None
    assert verdict(_note(6, 1, revoke_next=True)) is not None


def test_a_ping_url_s_verify_new_is_decided_by_the_verifier_and_passes_the_evidence_scan() -> None:
    """ADR-0055 amendment 21: the lookup beside its control decides the step, with no person,
    and what the executor files for it is what the orchestrator stores."""
    from orchestrator.kernel.secret_metadata import secret_metadata_path
    from orchestrator.services.verifier.rotation_evidence import (
        LONG_RUN,
        MAX_TEXT,
        RotationStepEvidence,
    )
    from tests.rotation_worker.test_lane_credentials import (
        PING_DESTINATIONS,
        PING_PLAN,
        PING_URL,
        HealthchecksStub,
        Issue,
        _ping,
    )

    issue = Issue(_ping(HealthchecksStub()), PING_DESTINATIONS, PING_URL)
    issue.run(PING_PLAN)
    (payload,) = [e.as_payload() for _, e in issue.recorded.filed if e.step == "verify_new"]
    RotationStepEvidence.model_validate(payload)
    assert secret_metadata_path({"payload": payload}, max_string=MAX_TEXT) is None
    assert LONG_RUN.search(str(payload)) is None
    criterion = (
        "external: the verify_new step's rotation-step/1 evidence, filed by the rotation executor."
    )
    verdict = rotation_step_verdict(
        "external_attestation", criterion, "store", ROTATION_STEP_EVIDENCE_TYPE, payload
    )
    assert verdict is None
    # The same step with the control found stays with a person: the verifier reads the control.
    found = {
        **payload,
        "probes": [
            dict(p, outcome="live", http_status=200) if p["subject"] == "known_bad" else p
            for p in payload["probes"]
        ],
    }
    assert (
        rotation_step_verdict(
            "external_attestation", criterion, "store", ROTATION_STEP_EVIDENCE_TYPE, found
        )
        == "a probe has no refused known-bad control on the same target"
    )
