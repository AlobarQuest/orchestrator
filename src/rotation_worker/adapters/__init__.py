"""Rotation adapters: a provider and a plan each, chosen by the envelope's `rotation_adapter`.

An adapter's provider must be the one the credential's registry entry names, so an envelope can't
point a credential at another provider's adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rotation_worker.adapters import fake
from rotation_worker.contract import Plan, Provider


@dataclass(frozen=True)
class Adapter:
    provider_name: str
    plan: Plan
    # Builds the provider from the executor's state directory.
    build: Callable[[Path], Provider]


ADAPTERS: Final[dict[str, Adapter]] = {
    fake.NAME: Adapter(fake.NAME, fake.PLAN, lambda state: fake.FakeProvider(state / "fake.json")),
}
