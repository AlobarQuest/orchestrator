"""The durable fact this producer files before it acts (ADR-0026 decision 2, ADR-0054).

**THE OBSERVATION IS THE FACT AND THE RECORD IS A DECISION ABOUT IT.** What is observed here is that
infraops' registry says a credential is due for rotation, and why. Whether it is rotated, and how,
is the work a person approves; none of that is in any string below.

**THE REFERENCE IS THE ROTATION'S OWN IDENTITY**: the credential and the occurrence. Both are fixed
for as long as this is the same rotation -- an exposure id, a requested date, or the date the
credential last rotated -- so a second pass over an unchanged registry replays the row, and the
next rotation of the same credential is a different occurrence and takes a different row.

**NOTHING THAT MOVES IS IN IT.** Not the age in days, not the pass's clock, not the credential's
class (a registry edit could move it under a fixed reference, which is `observation_conflict`
forever). `observed_at` is the date the trigger is dated by, never when the pass ran.

**NO KEY NAMES A CREDENTIAL**, deliberately: the orchestrator's secret detector refuses any fact key
containing `credential`, `token`, `secret` or `key`, so the registry id travels as
`registry_entry`. The values are registry names and dates, never a secret.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final

from rotation_proposer.findings import Due

SOURCE_SYSTEM: Final = "rotation_proposer"
OBSERVATION_TYPE: Final = "rotation_due"
SUBJECT_TYPE: Final = "credential"
TRUST_CLASSIFICATION: Final = "delivery_system"
STATUS: Final = "observed"
SEVERITY: Final = "info"
MAX_SUMMARY: Final = 512
# `CommandBase.idempotency_key` is `max_length=200`, and exceeding it is a 422 raised before any
# service code runs. Mirrored here so the refusal names the credential rather than a field path.
MAX_IDEMPOTENCY_KEY: Final = 200

_WHY: Final = {
    "exposure": "an exposure is recorded against it",
    "requested": "a rotation was requested",
    "age": "it is older than its class allows",
}


class ObservationUncomposable(ValueError):
    """This rotation cannot be stated as an observation, so nothing about it may be acted on."""


def facts(due: Due) -> dict[str, Any]:
    return {
        "registry_entry": due.credential_id,
        "trigger": due.trigger,
        "basis": due.basis,
        "occurrence": due.occurrence,
    }


def reference_for(due: Due) -> str:
    return f"credential-rotation:{due.credential_id}:{due.occurrence}"


def summary_of(due: Due) -> str:
    """What is true, and nothing about what should happen to it."""
    return (
        f"infraops' registry reports {due.credential_id} due for rotation: {_WHY[due.trigger]} "
        f"({due.basis})"
    )[:MAX_SUMMARY]


def rotation_observation(due: Due) -> dict[str, Any]:
    stated = facts(due)
    reference = reference_for(due)
    digest = hashlib.sha256(
        json.dumps(stated, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    key = f"{reference}:{digest}"
    if len(key) > MAX_IDEMPOTENCY_KEY:
        raise ObservationUncomposable(
            f"the observation key for {due.credential_id} is {len(key)} characters, over the "
            f"{MAX_IDEMPOTENCY_KEY} the route accepts"
        )
    return {
        "idempotency_key": key,
        "expected_version": 0,
        "source_system": SOURCE_SYSTEM,
        "source_reference": reference,
        "source_url": None,
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": SUBJECT_TYPE,
        "subject_reference": due.credential_id,
        "environment": None,
        "observation_type": OBSERVATION_TYPE,
        "status": STATUS,
        "severity": SEVERITY,
        "observed_at": f"{due.dated}T00:00:00+00:00",
        "summary": summary_of(due),
        "facts": stated,
        "payload_digest": None,
    }
