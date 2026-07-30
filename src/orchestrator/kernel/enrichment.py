"""Shape bounds for the governed-knowledge document a unit carries (WS-P2.12).

The document is authored outside this service and arrives on a decomposition
proposal. This module checks its SHAPE and SIZE only, never its membership. The
vocabulary of roads and rules belongs to the authoring side; a second copy here
would be a list to keep in sync and a guard to explain, and the first time the
two drifted the copy would be wrong rather than loud.

It grants nothing. It is reference material a worker reads, never authority, and
it must never reach the authority envelope or its fingerprint.

The three limits are independent on purpose. Record counts and per-field lengths
do not compose into a byte ceiling -- 199 near-maximal records satisfy both and
still bloat a prompt -- so the aggregate cap exists separately.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from orchestrator.errors import DomainError

SCHEMA_VERSION = 1
REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "profile",
        "change_class",
        "roads",
        "rules",
        "exemplars",
        "content_fingerprint",
        "resolved_at",
        "sources",
    }
)
MAX_BYTES = 16_384
MAX_TEXT = 4_000
RECORD_LIMITS: Mapping[str, int] = {"roads": 50, "rules": 200, "exemplars": 100}
_SCALARS = (str, int, float, bool, type(None))


def validate_enrichment(value: object) -> dict[str, Any] | None:
    """Return the bounded document, or raise DomainError.

    `None` is a unit that predates the field and is not an error. It is also not
    the same as an empty document: a class enriched with nothing must stay
    distinguishable from one never enriched at all.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _invalid("context enrichment must be a mapping")
    missing = REQUIRED_KEYS - set(value)
    if missing:
        raise _invalid(f"context enrichment is missing required keys: {sorted(missing)}")
    if value["schema_version"] != SCHEMA_VERSION:
        raise _invalid(
            f"unsupported context enrichment schema_version: {value['schema_version']!r}"
        )
    for key, limit in RECORD_LIMITS.items():
        records = value[key]
        if not isinstance(records, list):
            raise _invalid(f"context enrichment {key} must be a list")
        if len(records) > limit:
            raise _too_large(
                f"context enrichment carries {len(records)} {key}, over the limit of {limit}"
            )
        for record in records:
            _check_record(record, key)
    serialized = json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()
    if len(serialized) > MAX_BYTES:
        raise _too_large(
            f"context enrichment is {len(serialized)} bytes, over the limit of {MAX_BYTES}"
        )
    return dict(value)


def _check_record(record: object, key: str) -> None:
    if not isinstance(record, Mapping):
        raise _invalid(f"every context enrichment {key} entry must be a mapping")
    for field, field_value in record.items():
        if not isinstance(field_value, _SCALARS):
            raise _invalid(
                f"context enrichment {key} field {field!r} must be a scalar; "
                "records are flat so that nesting cannot smuggle unbounded content"
            )
        if isinstance(field_value, str) and len(field_value) > MAX_TEXT:
            raise _too_large(
                f"context enrichment {key} field {field!r} exceeds {MAX_TEXT} characters"
            )


def _invalid(message: str) -> DomainError:
    return DomainError("context_enrichment_invalid", message, None)


def _too_large(message: str) -> DomainError:
    return DomainError("context_enrichment_too_large", message, "reduce the projection")
