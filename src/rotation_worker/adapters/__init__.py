"""Rotation adapters: a provider and a plan each, chosen by the envelope's `rotation_adapter`.

An adapter's provider must be the one the credential's registry entry names, so an envelope can't
point a credential at another provider's adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rotation_worker.adapters import fake, openrouter, orchestrator_bearer
from rotation_worker.contract import Plan, Provider, Rotation
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.secret_store import SecretStore


@dataclass(frozen=True)
class AdapterContext:
    """What building a provider may use: the executor's state directory, its BWS access, the
    UUID of this provider's admin secret, which the provider reads only when a step needs it
    (ADR-0055 decision 4), and, for a provider that is a hosted service, the approved rotation
    and how to reach the orchestrator and Coolify."""

    state_dir: Path
    store: SecretStore
    admin_secret_id: str | None
    rotation: Rotation | None = None
    orchestrator: OrchestratorClient | None = None
    orchestrator_url: str | None = None
    coolify_url: str | None = None
    orchestrator_application: str | None = None
    executor_key_id: str | None = None


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
    orchestrator_bearer.NAME: Adapter(
        orchestrator_bearer.NAME,
        orchestrator_bearer.PLAN,
        lambda context: orchestrator_bearer.build_provider(
            rotation=context.rotation,
            store=context.store,
            coolify_token_id=context.admin_secret_id,
            orchestrator=context.orchestrator,
            orchestrator_url=context.orchestrator_url,
            coolify_url=context.coolify_url,
            orchestrator_application=context.orchestrator_application,
            executor_key_id=context.executor_key_id,
        ),
    ),
}
