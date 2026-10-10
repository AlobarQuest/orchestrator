"""What a rotation unit's evidence may hold (ADR-0055 decision 9).

Rotation state lives in the orchestrator as evidence on the units: fingerprints and probe results
per step, never a value. Evidence rows are append-only, so a value written there can never be
removed; the payload is therefore held to a strict schema and to the observation secret scan
before anything is stored, and only a structured payload is accepted (no free `stable_ref`).

Only rotation units are held to this. Other producers' existing payloads use keys the scan
refuses (factory-runner sends `body`), so a scan of every unit's evidence would refuse them.

`RotationStepEvidence` is a contract with the rotation executor (increment 4), which files this
evidence. Its JSON schema is pinned by `tests/fixtures/rotation_evidence_schema.json`; a change to
the model changes the fixture, and the executor's copy must change with it. Every field name
avoids the scan's key-name parts (`SECRET_KEY_PARTS`), and no step name uses a word the
`src/orchestrator/` word guards refuse.
"""

from __future__ import annotations

import re
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from orchestrator.errors import DomainError
from orchestrator.kernel.secret_metadata import secret_metadata_path
from orchestrator.persistence.models import WorkPackageRevision, WorkUnit
from orchestrator.services.lifecycle.human_acts import ValueFingerprint
from orchestrator.services.lifecycle.rotation_claims import is_rotation_unit

# A free-text field longer than this is where a pasted log or response arrives.
MAX_TEXT = 500
# The shared detector knows two value shapes. Credentials this feature rotates (provider keys,
# PATs, API tokens) are long unbroken runs of these characters, while everything this schema
# legitimately carries breaks them up: a fingerprint is at most 16 characters, and a UUID's
# hyphens split it into runs of 12 or fewer. So any 32-character run is refused as a value.
LONG_RUN = re.compile(r"[A-Za-z0-9_+/=]{32,}")

StepKind = Literal[
    "mint",
    "stage",
    "verify_new",
    "store",
    "distribute",
    "verify_consumers",
    "retire_old",
    "confirm_dead",
]


class StepFingerprint(ValueFingerprint):
    """Which value a fingerprint is of: the one being replaced, or its replacement."""

    which: Literal["old", "new"]


class ProbeResult(BaseModel):
    """One probe and its answer. A probe counts only beside its known-bad control (decision 3)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    target: str = Field(min_length=1, max_length=200)
    subject: Literal["old", "new", "known_bad"]
    expected: Literal["live", "dead"]
    http_status: int | None = Field(default=None, ge=100, le=599)
    outcome: Literal["live", "dead", "indeterminate"]


class RotationStepEvidence(BaseModel):
    """The payload of one rotation step's evidence.

    A row recovered by SYSTEM after a lapsed lease is validated before the orchestrator adds its
    own `recovery` key, so a reader validates a stored payload without that key.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["rotation-step/1"]
    step: StepKind
    destination: str | None = Field(default=None, min_length=1, max_length=200)
    fingerprints: list[StepFingerprint] = Field(default_factory=list, max_length=4)
    probes: list[ProbeResult] = Field(default_factory=list, max_length=16)
    note: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)


# The contract's version, read from the model so the two cannot disagree. A change a conforming
# executor would fail on bumps it.
EVIDENCE_FORMAT: str = get_args(RotationStepEvidence.model_fields["format"].annotation)[0]


def check_rotation_evidence(
    unit: WorkUnit,
    revision: WorkPackageRevision,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    evidence_type: str,
    source_revision: str,
) -> None:
    """On a rotation unit, refuse evidence that isn't a well-formed, secret-free payload.

    Any other unit's evidence is not checked here. Every stored string is scanned, the evidence
    type and source revision included, because each lands in an append-only row.
    """
    if not is_rotation_unit(unit, revision):
        return
    if stable_ref is not None or payload is None:
        raise DomainError(
            "rotation_evidence_invalid",
            "rotation evidence is a structured payload and carries no stable reference",
            None,
        )
    try:
        RotationStepEvidence.model_validate(payload)
    except ValidationError as error:
        # Locations and kinds only, never the input: the refused value may be the secret.
        where = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['type']}"
            for item in error.errors(include_input=False, include_url=False)
        )
        raise DomainError(
            "rotation_evidence_invalid",
            f"rotation evidence must be a {EVIDENCE_FORMAT} payload ({where})",
            None,
        ) from error
    stored = {
        "payload": payload,
        "evidence_type": evidence_type,
        "source_revision": source_revision,
    }
    if secret_metadata_path(stored, max_string=MAX_TEXT) is not None or _has_long_run(stored):
        raise DomainError(
            "rotation_evidence_secret",
            "rotation evidence looks like it contains a secret; record fingerprints only",
            None,
        )


def _has_long_run(value: object) -> bool:
    if isinstance(value, dict):
        return any(_has_long_run(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_long_run(child) for child in value)
    return isinstance(value, str) and LONG_RUN.search(value) is not None
