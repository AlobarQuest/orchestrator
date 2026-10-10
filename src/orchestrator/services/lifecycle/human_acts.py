"""What a human records when resolving a human-act dependency at `/review` (ADR-0055 decision 3).

A rotation's human acts (a console mint, a hosted write, an attestation) are dependencies, not
units, and Devon resolves each at `/review` with the fingerprints the act produced. The detail he
records is stored on the dependency row and in its event, so it is held to a strict schema and to
the observation secret scan: a fingerprint is a sha256 prefix and a length, never a value. The scan
refuses a note holding an `Authorization: Bearer` header or the BWS token shape; it cannot
recognise every other token format, so the form says to record fingerprints only.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from orchestrator.errors import DomainError
from orchestrator.kernel.secret_metadata import secret_metadata_path

# A note longer than this is where a pasted log or response arrives; the scan treats it as one.
MAX_NOTE = 500


class ValueFingerprint(BaseModel):
    """A value's sha256 prefix and length: enough to tell two values apart, never to recover one."""

    model_config = ConfigDict(extra="forbid", strict=True)

    sha256_prefix: str = Field(pattern=r"^[0-9a-f]{8,16}$")
    length: int = Field(ge=1, le=4096)


class HumanActDetail(BaseModel):
    """The detail of a human-act dependency's resolution."""

    model_config = ConfigDict(extra="forbid", strict=True)

    note: str = Field(min_length=1, max_length=MAX_NOTE)
    fingerprint: ValueFingerprint | None = None


def human_act_detail(note: str, sha256_prefix: str | None, length: int | None) -> dict[str, Any]:
    """The validated detail as stored, or a `DomainError` naming what was refused.

    The fingerprint's two fields come together or not at all.
    """
    if (sha256_prefix is None) != (length is None):
        raise DomainError(
            "human_act_detail_invalid",
            "a fingerprint needs both its sha256 prefix and its length",
            None,
        )
    fingerprint = (
        None
        if sha256_prefix is None or length is None
        else {"sha256_prefix": sha256_prefix, "length": length}
    )
    try:
        detail = HumanActDetail.model_validate(
            {"note": note.strip(), "fingerprint": fingerprint}
        ).model_dump(exclude_none=True)
    except ValidationError as error:
        raise DomainError(
            "human_act_detail_invalid", "the resolution detail is not well formed", None
        ) from error
    if secret_metadata_path(detail, max_string=MAX_NOTE) is not None:
        raise DomainError(
            "human_act_detail_secret",
            "the resolution detail looks like it contains a secret; record fingerprints only",
            None,
        )
    return detail
