"""Rotation adapters: a provider and a plan each, chosen by the envelope's `rotation_adapter`.

An adapter's provider must be the one the credential's registry entry names, so an envelope can't
point a credential at another provider's adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rotation_worker.adapters import fake, openrouter
from rotation_worker.contract import Plan, Provider
from rotation_worker.secret_store import SecretStore


@dataclass(frozen=True)
class AdapterContext:
    """What building a provider may use: the executor's state directory, its BWS access, and the
    UUID of this provider's admin secret, which the provider reads only when a step needs it
    (ADR-0055 decision 4)."""

    state_dir: Path
    store: SecretStore
    admin_secret_id: str | None


@dataclass(frozen=True)
class Adapter:
    provider_name: str
    plan: Plan
    build: Callable[[AdapterContext], Provider]


ADAPTERS: Final[dict[str, Adapter]] = {
    fake.NAME: Adapter(
        fake.NAME, fake.PLAN, lambda context: fake.FakeProvider(context.state_dir / "fake.json")
    ),
    openrouter.NAME: Adapter(
        openrouter.NAME,
        openrouter.PLAN,
        lambda context: openrouter.build(context.store, context.admin_secret_id),
    ),
}
