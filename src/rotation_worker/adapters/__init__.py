"""Rotation adapters, chosen by the envelope's `rotation_adapter`: a credential type's provider
and the capabilities its plan is derived from (`plans.derive`, ADR-0055 amendment 15), or a name
that pins one fixed plan.

An adapter's provider must be the one the credential's registry entry names, so an envelope can't
point a credential at another provider's adapter. Each adapter runs on one instance of the
executor: the one whose store keeps its credentials (`store_kind`, ADR-0055 amendment 12).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from rotation_worker.adapters import (
    bitbucket,
    bws_machine_token,
    change_manager,
    fake,
    github_pat,
    openrouter,
    orchestrator_bearer,
)
from rotation_worker.contract import ContractError, Plan, Provider, Rotation, UnitPlan
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.plans import Capabilities
from rotation_worker.readers import Healthchecks
from rotation_worker.secret_store import SecretStore
from rotation_worker.types import TYPES


@dataclass(frozen=True)
class AdapterContext:
    """What building a provider may use: the executor's state directory, its BWS access, the
    UUID of this provider's admin secret, which the provider reads only when a step needs it
    (ADR-0055 decision 4), and, for a provider that is a hosted service, the approved rotation,
    how to reach the orchestrator and Coolify, and the registry and Healthchecks."""

    state_dir: Path
    store: SecretStore
    admin_secret_id: str | None
    rotation: Rotation | None = None
    orchestrator: OrchestratorClient | None = None
    orchestrator_url: str | None = None
    coolify_url: str | None = None
    orchestrator_application: str | None = None
    executor_key_id: str | None = None
    change_manager_application: str | None = None
    mirror: RepoMirror | None = None
    healthchecks: Healthchecks | None = None
    # The unit's approved envelope declares a host with no overlap (`checks.WITHOUT_OVERLAP_KEY`).
    host_without_overlap: bool = False
    # The credential's registry entry, for a provider that reads its own fields (Bitbucket's
    # probe account and workspace).
    credential: Mapping[str, Any] | None = None
    # The BWS organization, for a provider that lists what a BWS token can see.
    organization_id: str | None = None


# Why a unit can't be claimed yet, from the unit, its history and the time; `None` means now.
WaitReason = Callable[[UnitPlan, Sequence[Mapping[str, Any]], datetime], str | None]


@dataclass(frozen=True)
class Adapter:
    provider_name: str
    plan: Plan
    build: Callable[[AdapterContext], Provider]
    # The destination kind of the store this adapter's keepers live in: only the executor
    # instance whose store reads that kind takes its units.
    store_kind: str = "bws-secret"
    wait: WaitReason | None = None
    # The units that wait, unclaimed, while another rotation is mid-overlap (`overlaps.py`).
    overlap_waits: frozenset[str] = frozenset()
    # How long the old value may keep working after a person's revoke: the unit after the revoke
    # waits this long from when the revoke was seen (`revokes.py`).
    revoke_settles: timedelta = timedelta(0)
    # A credential type's: which steps its provider does by machine, from which each
    # credential's plan is derived (`plans.derive`, ADR-0055 amendment 15). `None` for a name
    # that pins one fixed plan. A type's `plan` is the one its packages approved before
    # derivation, run only for a package whose criteria still describe it.
    capabilities: Capabilities | None = None


def _change_manager(context: AdapterContext) -> Provider:
    # change-manager's four bearers hold four values, so an overlap is always possible.
    if context.host_without_overlap:
        raise ContractError("a change-manager bearer always hosts with an overlap")
    siblings, readers_mid_run = change_manager.registry_reads(
        context.rotation, context.store, context.mirror, context.healthchecks
    )
    return change_manager.build_provider(
        rotation=context.rotation,
        store=context.store,
        coolify_token_id=context.admin_secret_id,
        coolify_url=context.coolify_url,
        change_manager_application=context.change_manager_application,
        siblings=siblings,
        readers_mid_run=readers_mid_run,
    )


def _orchestrator(context: AdapterContext) -> Provider:
    return orchestrator_bearer.build_provider(
        rotation=context.rotation,
        store=context.store,
        coolify_token_id=context.admin_secret_id,
        orchestrator=context.orchestrator,
        orchestrator_url=context.orchestrator_url,
        coolify_url=context.coolify_url,
        orchestrator_application=context.orchestrator_application,
        executor_key_id=context.executor_key_id,
        without_overlap=context.host_without_overlap,
    )


ADAPTERS: Final[dict[str, Adapter]] = {
    fake.NAME: Adapter(
        fake.NAME, fake.PLAN, lambda context: fake.FakeProvider(context.state_dir / "fake.json")
    ),
    openrouter.NAME: Adapter(
        openrouter.NAME,
        openrouter.PLAN,
        lambda context: openrouter.build(context.store, context.admin_secret_id),
        capabilities=TYPES[openrouter.NAME].capabilities,
    ),
    orchestrator_bearer.NAME: Adapter(
        orchestrator_bearer.NAME,
        orchestrator_bearer.PLAN,
        _orchestrator,
        capabilities=TYPES[orchestrator_bearer.NAME].capabilities,
    ),
    orchestrator_bearer.DISTRIBUTED_NAME: Adapter(
        orchestrator_bearer.NAME, orchestrator_bearer.DISTRIBUTED_PLAN, _orchestrator
    ),
    github_pat.NAME: Adapter(
        github_pat.PROVIDER,
        github_pat.PLAN,
        lambda context: github_pat.build_provider(
            rotation=context.rotation, orchestrator=context.orchestrator
        ),
        capabilities=TYPES[github_pat.NAME].capabilities,
    ),
    bitbucket.NAME: Adapter(
        bitbucket.NAME,
        bitbucket.PLAN,
        lambda context: bitbucket.build_provider(credential=context.credential),
        capabilities=TYPES[bitbucket.NAME].capabilities,
    ),
    change_manager.NAME: Adapter(
        change_manager.NAME,
        change_manager.PLAN,
        _change_manager,
        capabilities=TYPES[change_manager.NAME].capabilities,
    ),
    bws_machine_token.NAME: Adapter(
        bws_machine_token.PROVIDER,
        bws_machine_token.PLAN,
        lambda context: bws_machine_token.BwsMachineTokenProvider(
            organization_id=context.organization_id
        ),
        store_kind=TYPES[bws_machine_token.NAME].store_kind,
        wait=bws_machine_token.wait_reason,
        overlap_waits=frozenset({"store"}),
        revoke_settles=bws_machine_token.REVOKE_SETTLES,
        capabilities=TYPES[bws_machine_token.NAME].capabilities,
    ),
    change_manager.ATTESTED_NAME: Adapter(
        change_manager.NAME, change_manager.ATTESTED_PLAN, _change_manager
    ),
    change_manager.DISTRIBUTED_NAME: Adapter(
        change_manager.NAME, change_manager.DISTRIBUTED_PLAN, _change_manager
    ),
}
