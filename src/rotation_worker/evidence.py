"""A step's evidence: a `rotation-step/1` payload, the contract the orchestrator enforces at ingest.

The orchestrator holds every rotation evidence row to `RotationStepEvidence`
(`tests/fixtures/rotation_evidence_schema.json` pins it): a step kind, at most one destination,
fingerprints of the old and new values, and probe answers. No field carries a value, and the
orchestrator refuses any 32-character unbroken run anywhere in the row, including the source
revision, which is why that is a short commit prefix here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

from rotation_worker.values import Fingerprint

EVIDENCE_FORMAT: Final = "rotation-step/1"
MAX_DESTINATION: Final = 200
MAX_NOTE: Final = 500

Which = Literal["old", "new"]
Subject = Literal["old", "new", "known_bad"]
State = Literal["live", "dead"]
Outcome = Literal["live", "dead", "indeterminate"]


@dataclass(frozen=True)
class ProbeAnswer:
    target: str
    subject: Subject
    expected: State
    http_status: int | None
    outcome: Outcome

    def as_payload(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "subject": self.subject,
            "expected": self.expected,
            "http_status": self.http_status,
            "outcome": self.outcome,
        }


@dataclass(frozen=True)
class StepEvidence:
    step: str
    destination: str | None = None
    fingerprints: tuple[tuple[Which, Fingerprint], ...] = ()
    probes: tuple[ProbeAnswer, ...] = field(default=())
    note: str | None = None

    def __post_init__(self) -> None:
        if self.destination is not None and len(self.destination) > MAX_DESTINATION:
            raise ValueError("a destination longer than rotation-step/1 allows")
        if self.note is not None and not 1 <= len(self.note) <= MAX_NOTE:
            raise ValueError("a note must be 1 to 500 characters")

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "format": EVIDENCE_FORMAT,
            "step": self.step,
            "fingerprints": [
                {"which": which, "sha256_prefix": print_.sha256_prefix, "length": print_.length}
                for which, print_ in self.fingerprints
            ],
            "probes": [probe.as_payload() for probe in self.probes],
        }
        if self.destination is not None:
            payload["destination"] = self.destination
        if self.note is not None:
            payload["note"] = self.note
        return payload
