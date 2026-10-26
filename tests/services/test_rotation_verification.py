"""`rotation_step_verdict`: which rotation step evidence proves its criterion (ADR-0055 am. 14).

The passing payloads are the drift-reporter bearer's retire unit as filed in production on
2026-10-25; every other case changes one property of one of them.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from orchestrator.services.verifier.rotation_verification import (
    ROTATION_STEP_EVIDENCE_TYPE,
    rotation_step_verdict,
)

TARGET = "the orchestrator's status ledger, as the approved key id"
FINGERPRINTS = [
    {"which": "old", "length": 64, "sha256_prefix": "3bfa95a969b123b6"},
    {"which": "new", "length": 43, "sha256_prefix": "6a4a452ace49d87c"},
]


def _criterion(step: str, unit: str | None = None) -> str:
    named = f"the {unit} unit's {step}" if unit else f"the {step}"
    return f"external: {named} step's rotation-step/1 evidence,\n  filed by the rotation executor."


def _probe(subject: str, expected: str, outcome: str, status: int) -> dict[str, Any]:
    return {
        "target": TARGET,
        "subject": subject,
        "expected": expected,
        "outcome": outcome,
        "http_status": status,
    }


BAD = _probe("known_bad", "dead", "dead", 401)
STEPS: dict[str, dict[str, Any]] = {
    "verify_consumers": {
        "format": "rotation-step/1",
        "step": "verify_consumers",
        "fingerprints": FINGERPRINTS,
        "probes": [],
        "note": "10 of 10 consumers hold or attest the new value",
    },
    "retire_old": {
        "format": "rotation-step/1",
        "step": "retire_old",
        "fingerprints": FINGERPRINTS,
        "probes": [BAD, _probe("old", "live", "live", 200)],
        "note": "confirmed the entry's previous hash is the old value's",
    },
    "confirm_dead": {
        "format": "rotation-step/1",
        "step": "confirm_dead",
        "fingerprints": FINGERPRINTS,
        "probes": [
            BAD,
            _probe("old", "dead", "dead", 401),
            BAD,
            _probe("new", "live", "live", 200),
        ],
        "note": "deleted staging 51aa86f6-3dd9-402b-94b3-b4ea00ecaa34",
    },
    "verify_new": {
        "format": "rotation-step/1",
        "step": "verify_new",
        "fingerprints": FINGERPRINTS,
        "probes": [BAD, _probe("new", "live", "live", 200)],
    },
    "store": {"format": "rotation-step/1", "step": "store", "fingerprints": FINGERPRINTS},
}


def _verdict(
    step: str,
    payload: dict[str, Any] | None = None,
    *,
    criterion: str | None = None,
    unit: str | None = "retire",
    evidence_type: str = ROTATION_STEP_EVIDENCE_TYPE,
    criterion_type: str = "external_attestation",
) -> str | None:
    return rotation_step_verdict(
        criterion_type,
        criterion if criterion is not None else _criterion(step),
        unit,
        evidence_type,
        payload if payload is not None else STEPS[step],
    )


def _with(step: str, **changes: Any) -> dict[str, Any]:
    payload = copy.deepcopy(STEPS[step])
    payload.update(changes)
    return payload


@pytest.mark.parametrize("step", sorted(STEPS))
def test_each_step_kind_passes_on_its_production_evidence(step: str) -> None:
    assert _verdict(step) is None


def test_a_criterion_naming_its_unit_passes_on_that_unit() -> None:
    assert _verdict("verify_consumers", criterion=_criterion("verify_consumers", "retire")) is None


def test_wrong_evidence_type() -> None:
    assert _verdict("store", evidence_type="external_attestation") is not None


def test_recovered_row_stays_judgment() -> None:
    reason = _verdict("store", _with("store", recovery={"by": "system"}))
    assert reason is not None and "recovered" in reason


def test_another_criterion_type_stays_judgment() -> None:
    assert _verdict("store", criterion_type="human_review") is not None


def test_a_payload_outside_the_schema_stays_judgment() -> None:
    assert _verdict("store", _with("store", format="rotation-step/2")) is not None


def test_step_mismatch() -> None:
    assert _verdict("store", criterion=_criterion("stage")) is not None


def test_unit_mismatch() -> None:
    criterion = _criterion("verify_consumers", "mint")
    assert _verdict("verify_consumers", criterion=criterion, unit="retire") is not None


def test_unparseable_ac_text() -> None:
    assert _verdict("store", criterion="external: the step's rotation-step/1 evidence") is not None


def test_same_fingerprints() -> None:
    same = [dict(FINGERPRINTS[0]), {**FINGERPRINTS[0], "which": "new"}]
    assert _verdict("store", _with("store", fingerprints=same)) is not None


def test_a_missing_fingerprint() -> None:
    assert _verdict("store", _with("store", fingerprints=FINGERPRINTS[:1])) is not None


def test_missing_control() -> None:
    payload = _with("verify_new", probes=[_probe("new", "live", "live", 200)])
    assert _verdict("verify_new", payload) is not None


def test_an_accepted_control() -> None:
    accepted = _probe("known_bad", "dead", "live", 200)
    payload = _with("verify_new", probes=[accepted, _probe("new", "live", "live", 200)])
    assert _verdict("verify_new", payload) is not None


def test_a_control_on_another_target() -> None:
    elsewhere = {**BAD, "target": "another service"}
    payload = _with("verify_new", probes=[elsewhere, _probe("new", "live", "live", 200)])
    assert _verdict("verify_new", payload) is not None


def test_indeterminate_retire_probe() -> None:
    payload = _with("retire_old", probes=[BAD, _probe("old", "live", "indeterminate", 503)])
    assert _verdict("retire_old", payload) is not None


def test_an_accepted_control_beside_a_refused_one() -> None:
    accepted = _probe("known_bad", "dead", "live", 200)
    probes = [BAD, accepted, _probe("new", "live", "live", 200)]
    assert _verdict("verify_new", _with("verify_new", probes=probes)) is not None


def test_indeterminate_probe() -> None:
    payload = _with("verify_new", probes=[BAD, _probe("new", "live", "indeterminate", 503)])
    assert _verdict("verify_new", payload) is not None


def test_verify_new_with_the_new_value_refused() -> None:
    payload = _with("verify_new", probes=[BAD, _probe("new", "live", "dead", 401)])
    assert _verdict("verify_new", payload) is not None


def test_verify_new_with_no_probe() -> None:
    assert _verdict("verify_new", _with("verify_new", probes=[])) is not None


def test_retire_old_already_dead_passes() -> None:
    payload = _with("retire_old", probes=[BAD, _probe("old", "live", "dead", 401)])
    assert _verdict("retire_old", payload) is None


def test_retire_old_probing_the_new_value() -> None:
    payload = _with("retire_old", probes=[BAD, _probe("new", "live", "live", 200)])
    assert _verdict("retire_old", payload) is not None


def test_confirm_dead_old_live() -> None:
    probes = [BAD, _probe("old", "dead", "live", 200), BAD, _probe("new", "live", "live", 200)]
    assert _verdict("confirm_dead", _with("confirm_dead", probes=probes)) is not None


def test_confirm_dead_without_the_new_value() -> None:
    payload = _with("confirm_dead", probes=[BAD, _probe("old", "dead", "dead", 401)])
    assert _verdict("confirm_dead", payload) is not None


def test_partial_coverage_on_mint_passes() -> None:
    note = "2 of 10 consumers hold or attest the new value"
    payload = _with("verify_consumers", note=note)
    criterion = _criterion("verify_consumers", "mint")
    assert _verdict("verify_consumers", payload, criterion=criterion, unit="mint") is None


def test_partial_coverage_before_revoke_stays_judgment() -> None:
    note = "1 of 2 consumers hold or attest the new value; the old value is live before the revoke"
    payload = _with("verify_consumers", note=note)
    assert _verdict("verify_consumers", payload) is not None


def test_full_coverage_before_revoke_passes() -> None:
    note = "2 of 2 consumers hold or attest the new value; the old value is live before the revoke"
    assert _verdict("verify_consumers", _with("verify_consumers", note=note)) is None


@pytest.mark.parametrize(
    "note",
    [
        "every consumer holds the new value",
        "3 of 2 consumers hold or attest the new value",
        "0 of 0 consumers hold or attest the new value",
    ],
)
def test_coverage_note_unparseable(note: str) -> None:
    assert _verdict("verify_consumers", _with("verify_consumers", note=note)) is not None
