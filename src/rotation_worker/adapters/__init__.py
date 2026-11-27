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
    anthropic_key,
    bitbucket,
    bws_lane_token,
    bws_machine_token,
    change_manager,
    coolify_api_token,
    fake,
    github_app_key,
    github_pat,
    healthchecks_ping_url,
    openrouter,
    orchestrator_bearer,
    postgres_role,
)
from rotation_worker.contract import ContractError, Plan, Provider, Rotation, UnitPlan
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.helper_client import HelperClient
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.plans import Capabilities
from rotation_worker.readers import Healthchecks, PingUrls
from rotation_worker.registry import CHECK_FIELD, provider_entries
from rotation_worker.secret_store import SecretStore
from rotation_worker.types import CLASS_TYPES, TYPES


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
    # The canary application whose env list proves a new Coolify API token lacks read:sensitive
    # (ADR-0055 amendment 33, D-82 item 13 C).
    coolify_canary_application: str | None = None
    mirror: RepoMirror | None = None
    healthchecks: Healthchecks | None = None
    # The Management key's check listing, which a ping URL's mint reads (ADR-0055 amendment 23).
    ping_urls: PingUrls | None = None
    # The unit's approved envelope declares a host with no overlap (`checks.WITHOUT_OVERLAP_KEY`).
    host_without_overlap: bool = False
    # The credential's registry entry, for a provider that reads its own fields (Bitbucket's
    # probe account and workspace).
    credential: Mapping[str, Any] | None = None
    # The BWS organization, for a provider that lists what a BWS token can see.
    organization_id: str | None = None
    # The VPS Postgres helper, built on demand so its SSH key is read only when a step needs it.
    pg_helper: Callable[[], HelperClient] | None = None
    # Whether the unit's plan is an issue (`Plan.issue`). False is the strict path: a lane token
    # whose row names a helper target then proves its variable by the `loaded` read.
    issue: bool = False


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
    # Refuses, before a claim, a registry row this adapter couldn't act on.
    check_row: Callable[[Mapping[str, Any]], None] | None = None
    # A person may revoke the old value before the rotation runs, after a leak (ADR-0055
    # amendment 19 item 10, D-26), so the probe before the person's revoke takes either answer.
    revoke_may_come_first: bool = False


def _change_manager(context: AdapterContext) -> Provider:
    # change-manager's five bearers hold five values, so an overlap is always possible.
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


def _postgres_role(context: AdapterContext) -> Provider:
    # The type declares it has no overlap. The envelope's flag says something else: that another
    # identity shares the old value (ADR-0055 amendment 17).
    if context.host_without_overlap:
        raise ContractError("a postgres role declares no overlap itself, not by envelope")
    return postgres_role.build_provider(
        rotation=context.rotation,
        credential=context.credential,
        store=context.store,
        coolify_token_id=context.admin_secret_id,
        coolify_url=context.coolify_url,
        pg_helper=context.pg_helper,
    )


def _bws_lane_token(context: AdapterContext) -> Provider:
    credential, mirror = context.credential, context.mirror

    def siblings() -> list[str]:
        if mirror is None or credential is None:
            raise ContractError("the bws-lane-token adapter reads the registry for other lanes")
        entries = provider_entries(mirror, bws_lane_token.PROVIDER)
        return bws_lane_token.sharing_reach(entries, credential)

    return bws_lane_token.build_provider(
        rotation=context.rotation,
        credential=credential,
        store_read=context.store.read,
        coolify_token_id=context.admin_secret_id,
        coolify_url=context.coolify_url,
        organization_id=context.organization_id,
        siblings=siblings,
        pg_helper=context.pg_helper,
        issue=context.issue,
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
    postgres_role.NAME: Adapter(
        postgres_role.NAME,
        postgres_role.PLAN,
        _postgres_role,
        capabilities=TYPES[postgres_role.NAME].capabilities,
        check_row=postgres_role.check_row,
    ),
    bws_lane_token.NAME: Adapter(
        bws_lane_token.PROVIDER,
        bws_lane_token.PLAN,
        _bws_lane_token,
        revoke_settles=bws_machine_token.REVOKE_SETTLES,
        capabilities=TYPES[bws_lane_token.NAME].capabilities,
        check_row=bws_lane_token.check_row,
        revoke_may_come_first=True,
    ),
    healthchecks_ping_url.NAME: Adapter(
        healthchecks_ping_url.PROVIDER,
        healthchecks_ping_url.PLAN,
        lambda context: healthchecks_ping_url.HealthchecksPingUrlProvider(
            context.healthchecks,
            ping_urls=context.ping_urls,
            check=(context.credential or {}).get(CHECK_FIELD),
        ),
        capabilities=TYPES[healthchecks_ping_url.NAME].capabilities,
        check_row=healthchecks_ping_url.check_row,
    ),
    coolify_api_token.NAME: Adapter(
        coolify_api_token.PROVIDER,
        coolify_api_token.PLAN,
        lambda context: coolify_api_token.build_provider(
            credential=context.credential,
            coolify_url=context.coolify_url,
            canary_application=context.coolify_canary_application,
        ),
        capabilities=TYPES[coolify_api_token.NAME].capabilities,
        check_row=coolify_api_token.check_row,
        revoke_may_come_first=True,
    ),
    github_app_key.NAME: Adapter(
        github_app_key.PROVIDER,
        github_app_key.PLAN,
        lambda context: github_app_key.build_provider(
            rotation=context.rotation, credential=context.credential, store=context.store
        ),
        capabilities=TYPES[github_app_key.NAME].capabilities,
        check_row=github_app_key.check_row,
        revoke_may_come_first=True,
    ),
    anthropic_key.NAME: Adapter(
        anthropic_key.PROVIDER,
        anthropic_key.PLAN,
        lambda context: anthropic_key.build_provider(
            rotation=context.rotation, credential=context.credential, store=context.store
        ),
        capabilities=TYPES[anthropic_key.NAME].capabilities,
        check_row=anthropic_key.check_row,
        revoke_may_come_first=True,
    ),
    change_manager.ATTESTED_NAME: Adapter(
        change_manager.NAME, change_manager.ATTESTED_PLAN, _change_manager
    ),
    change_manager.DISTRIBUTED_NAME: Adapter(
        change_manager.NAME, change_manager.DISTRIBUTED_PLAN, _change_manager
    ),
}

# Each fixed-plan name, mapped to the credential type whose plan from before derivation it pins:
# it runs only on that type's rows. A type's own adapter is named by its type.
FIXED_PLAN_TYPES: Final[dict[str, str]] = {
    orchestrator_bearer.DISTRIBUTED_NAME: orchestrator_bearer.NAME,
    change_manager.ATTESTED_NAME: change_manager.NAME,
    change_manager.DISTRIBUTED_NAME: change_manager.NAME,
}


def runs_on_class(adapter_name: str, credential_class: object) -> bool:
    """Whether the adapter an envelope names may act on a registry row of `credential_class`: the
    row's class type (`CLASS_TYPES`), or a fixed-plan name of that type. Two types can share a
    provider (`github` for a PAT and an App key), so the provider alone can't tell. The canary's
    class has no type, and the `fake` adapter runs on it alone."""
    if adapter_name == fake.NAME:
        return credential_class == fake.CLASS
    row_type = CLASS_TYPES.get(str(credential_class))
    return row_type is not None and FIXED_PLAN_TYPES.get(adapter_name, adapter_name) == row_type
