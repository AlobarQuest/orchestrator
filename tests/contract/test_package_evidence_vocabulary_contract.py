"""The package evidence vocabulary is one set, shared with intent-packages (SDS 1.1, review 4a).

Neither repository can import the other, so each carries a byte-identical
`tests/fixtures/package_evidence_vocabulary.json` and pins it with the same `CONTRACT_SHA256`.
intent-packages holds `EVIDENCE_TYPES`, which `factory validate` enforces, equal to the fixture.
This side holds every member supported at intake and classified for the verifier exactly once. The
orchestrator also accepts types no package may declare (generated and historical criteria); those
are outside this contract, which is the package set.
"""

import hashlib
import json
from pathlib import Path

from orchestrator.services.verifier.verifier_evaluators import (
    DETERMINISTIC_TYPES,
    JUDGMENT_TYPES,
    SUPPORTED_CRITERION_EVIDENCE_TYPES,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "package_evidence_vocabulary.json"
CONTRACT_SHA256 = "04b4c0c1e4ba8ccc64eec59dccbdc3f4280fade10315a6d5bd75201f2bb81afb"


def package_evidence_types() -> tuple[str, ...]:
    return tuple(json.loads(FIXTURE.read_text(encoding="utf-8"))["evidence_types"])


def test_the_fixture_is_the_pinned_contract() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == CONTRACT_SHA256


def test_every_package_type_is_supported_and_classified_once() -> None:
    for evidence_type in package_evidence_types():
        assert evidence_type in SUPPORTED_CRITERION_EVIDENCE_TYPES, evidence_type
        assert (evidence_type in DETERMINISTIC_TYPES) != (evidence_type in JUDGMENT_TYPES), (
            evidence_type
        )
