"""The one writer of a dependency's resolution fields.

Two paths resolve a dependency: a SYSTEM or human command (`intake.packages`), and a predecessor's
completion (`lifecycle`, ADR-0055 decision 3). Both set the fields here, so a rule about what a
resolution may record holds for both. Never commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from orchestrator.errors import DomainError
from orchestrator.persistence.models import Dependency

# not-a-vocabulary: the two resolutions the `ck_dependencies_status` CHECK allows besides pending.
RESOLVED_STATUSES = frozenset({"satisfied", "failed"})


def apply_resolution(
    dependency: Dependency,
    *,
    status: str,
    resolved_by: str,
    resolved_at: datetime,
    resolution_event_id: uuid.UUID,
    detail: Mapping[str, Any],
) -> None:
    if status not in RESOLVED_STATUSES:
        raise DomainError(
            "invalid_dependency_status",
            "resolution must be satisfied or failed",
            None,
        )
    dependency.status = status
    dependency.resolved_by = resolved_by
    dependency.resolved_at = resolved_at
    dependency.resolution_event_id = resolution_event_id
    dependency.detail = normalize_json(detail)


def normalize_json(value: Any) -> Any:
    """Mappings with sorted keys, all the way down, so a stored document has one byte form."""
    if isinstance(value, Mapping):
        return {key: normalize_json(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [normalize_json(item) for item in value]
    return value
