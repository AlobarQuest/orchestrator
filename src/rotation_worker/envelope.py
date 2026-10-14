"""What a unit's envelope says about rotation: the one place that reads its constraints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

ADAPTER_KEY: Final = "rotation_adapter"


def constraints(unit_view: Mapping[str, Any]) -> Mapping[str, Any]:
    authority = unit_view.get("authority") or {}
    found = authority.get("constraints") or {}
    return found if isinstance(found, Mapping) else {}


def is_rotation_unit(unit_view: Mapping[str, Any]) -> bool:
    """Whether a unit's envelope names a rotation adapter: the units this executor works."""
    return ADAPTER_KEY in constraints(unit_view)
