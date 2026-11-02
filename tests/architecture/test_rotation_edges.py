"""Architecture guard: one table states every rotation lane's whole outbound surface.

Three programs make up the rotation layer, and each pins its own routes in its own tests. These
tables state them in one place, in the bridge table's shape (`edge_table.Edge`): the change-manager,
orchestrator, GitHub and alert-hub routes each lane may send and the bearers its launcher fetches.
`PLATFORM_EDGES` adds the executor's hosting and log reach, `PROVIDER_EDGES` each adapter's reach
into the service whose credential it rotates, and `PROCESSES` every program a lane can start.

Every cell is checked against the lane's OWN predicates under every verb, over one corpus tried
against every column: each path in `ROUTES`, its near misses, and named probes. A predicate the
program builds per instance or inline is captured from the `ConfinedClient` constructor, closed
over its sample repository or application, so the table never holds a stand-in. A widening to a
route outside the corpus doesn't fail here.

`closure` follows imports, so a lane's row covers what it borrows as well as what it owns. A module
is charged for what it imports, whether or not the lane calls it: `rotation_worker.contract`
imports `secret_store` for its protocol, so the proposer and the decomposer carry that module's
row and never run it. `process_scan` says what its rule for a process can't see.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
import sys
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal
from unittest import mock

import pytest

import alert_hub_client
from bump_proposer import change_manager as bump_change_manager
from bump_proposer import orchestrator_client as bump_orchestrator
from bump_proposer import standing as bump_standing
from estate_clients.confined import has_control_character
from rotation_decomposer import cli as decomposer_cli
from rotation_decomposer import client as decomposer_client
from rotation_proposer import client as proposer_client
from rotation_proposer import findings
from rotation_worker import coolify, github_actions, helper_client, readers, secret_store
from rotation_worker import orchestrator_client as worker_orchestrator
from rotation_worker.adapters import (
    ADAPTERS,
    bitbucket,
    bws_machine_token,
    change_manager,
    github_pat,
    openrouter,
    orchestrator_bearer,
    postgres_role,
)
from rotation_worker.config import Config
from rotation_worker.contract import ContractError, Rotation
from rotation_worker.types import TYPES
from rotation_worker.values import Value
from scripts.layers import load
from sds_members import client as members_client
from tests.architecture.code_terms import docstring_nodes
from tests.architecture.edge_table import (
    DEADMAN,
    OBSERVER_BEARER,
    PROPOSE_BEARER,
    READ_BEARER,
    SDS_BRIDGE_ALERT_BEARER,
    SYSTEM_BEARER,
    BuiltClient,
    Edge,
    Permits,
    Route,
    built_clients,
    captured_permits,
    either,
    launcher_bearers,
    near_misses,
    route_disagreements,
    sendable,
)
from tests.architecture.import_scan import file_imports
from tests.architecture.process_scan import process_heads
from tests.architecture.test_bridge_edges import PROBES as BRIDGE_PROBES
from tests.architecture.test_bridge_edges import _alert_hub_permits

UNIT = "0b5bd8e1-6d0c-4e8a-9a0d-1f0b4d8f6a11"
REVISION = "7f3a2c10-52de-4a11-8c55-90ab12cd34ef"
UNIT_PATH = f"/api/v1/work-units/{UNIT}"
INTAKE_PATH = f"/api/v1/package-intakes/{REVISION}"
PROPOSALS_PATH = f"{INTAKE_PATH}/decomposition-proposals"
# The orchestrator-verifier VERIFIER bearer.
VERIFIER_BEARER = "660d5846-abcb-4751-be86-b483012899eb"
MEMBERS = ("GET", "/api/v1/members")
ALERTS = frozenset({("POST", "/v1/alerts")})
WORK_RECORD = ("GET", "/api/v1/change-records/44/work")

# The orchestrator URL the worker uses is its `Config.orchestrator_url`, with no constant to read.
CONFIGURED = "from config"
CHANGE_MANAGER_URL = "https://change-mgr.alobar.net"
ORCHESTRATOR_LIVE_URL = "https://sds.alobar.net"
GITHUB_URL = "https://api.github.com"
ALERT_HUB_URL = "https://alerts.alobar.net"


@dataclass(frozen=True)
class RotationEdge(Edge):
    """An `Edge` with the base URL of each column that has routes."""

    base_urls: Mapping[str, str] = field(default_factory=dict)


EDGES: dict[str, RotationEdge] = {
    "rotation_worker": RotationEdge(
        change_manager=frozenset(),  # its one probe of change-manager is a PROVIDER_EDGES row
        orchestrator=frozenset(
            {
                ("GET", "/api/v1/status-ledger"),
                ("GET", "/api/v1/m2m-identities"),
                ("GET", "/api/v1/held-credentials"),
                ("GET", "/api/v1/observations"),
                MEMBERS,
                ("GET", UNIT_PATH),
                ("GET", f"{UNIT_PATH}/history"),
                ("GET", f"{UNIT_PATH}/evidence"),
                ("GET", INTAKE_PATH),
                ("POST", f"{UNIT_PATH}/claim"),
                ("POST", f"{UNIT_PATH}/renew"),
                ("POST", f"{UNIT_PATH}/evidence"),
                ("POST", f"{UNIT_PATH}/commands/start"),
                ("POST", f"{UNIT_PATH}/commands/submit"),
                ("POST", f"{UNIT_PATH}/commands/fail"),
            }
        ),
        # The rotation App, for the one repository and secret a destination names.
        github=frozenset(
            {
                ("GET", "/repos/o/r/installation"),
                ("POST", "/app/installations/44/access_tokens"),
                ("GET", "/repos/o/r/actions/secrets/public-key"),
                ("GET", "/repos/o/r/actions/secrets/S"),
                ("PUT", "/repos/o/r/actions/secrets/S"),
                ("GET", "/repos/o/r/actions/workflows/secret-fingerprint.yml/runs"),
                ("GET", "/repos/o/r/actions/runs/44/jobs"),
                ("GET", "/repos/o/r/actions/jobs/44/logs"),
            }
        ),
        alert_hub=ALERTS,  # through notify.py
        base_urls={
            "orchestrator": CONFIGURED,
            "github": GITHUB_URL,
            "alert_hub": ALERT_HUB_URL,
        },
        # It has no launcher: these are the `Config` fields naming what it reads from its store.
        bearers=frozenset(
            {
                "admin_secrets",
                "alert_hub_token_id",
                "bearer_secret_id",
                "credential_key_id",
                "github_app_key_id",
                "healthchecks_key_id",
                "inbox_secret_id",
                "inbox_token_id",
                "pg_helper_key_id",
                "root_token_file",
            }
        ),
    ),
    "rotation_proposer": RotationEdge(
        # Through bump_proposer.change_manager, so every route of that client.
        change_manager=frozenset({("POST", "/api/work-changes"), ("GET", "/api/items")}),
        orchestrator=frozenset(
            {
                ("POST", "/api/v1/observations"),  # through bump_proposer.orchestrator_client
                MEMBERS,  # through sds_members
                WORK_RECORD,
                ("GET", f"{UNIT_PATH}/history"),
            }
        ),
        github=frozenset(),
        alert_hub=ALERTS,  # through alerts.py
        bearers=DEADMAN | {PROPOSE_BEARER, OBSERVER_BEARER, SDS_BRIDGE_ALERT_BEARER},
        base_urls={
            "change_manager": CHANGE_MANAGER_URL,
            "orchestrator": ORCHESTRATOR_LIVE_URL,
            "alert_hub": ALERT_HUB_URL,
        },
    ),
    "rotation_decomposer": RotationEdge(
        change_manager=frozenset({("GET", "/api/items")}),
        orchestrator=frozenset(
            {
                MEMBERS,
                WORK_RECORD,
                ("GET", INTAKE_PATH),
                ("GET", PROPOSALS_PATH),
                ("POST", PROPOSALS_PATH),
            }
        ),
        github=frozenset(),
        alert_hub=ALERTS,  # through alerts.py
        bearers=DEADMAN | {SYSTEM_BEARER, READ_BEARER, SDS_BRIDGE_ALERT_BEARER},
        base_urls={
            "change_manager": CHANGE_MANAGER_URL,
            "orchestrator": ORCHESTRATOR_LIVE_URL,
            "alert_hub": ALERT_HUB_URL,
        },
    ),
}

# Every `Value` joins the scrub list of its process, so a sample must be a string no test's output
# contains.
_SAMPLE = Value("rotation-edges-sample-credential")


def _alert_hub_url() -> str:
    return alert_hub_client.BASE_URL


# Each URL as its code holds it, read when the test runs.
BASE_URL_READS: dict[str, dict[str, Callable[[], str]]] = {
    "rotation_worker": {
        "orchestrator": lambda: CONFIGURED,
        "github": lambda: github_actions.API,
        "alert_hub": _alert_hub_url,
    },
    "rotation_proposer": {
        "change_manager": lambda: bump_change_manager.DEFAULT_BASE_URL,
        "orchestrator": lambda: members_client.DEFAULT_BASE_URL,
        "alert_hub": _alert_hub_url,
    },
    "rotation_decomposer": {
        "change_manager": lambda: decomposer_cli.CHANGE_MANAGER_URL,
        "orchestrator": lambda: decomposer_cli.ORCHESTRATOR_URL,
        "alert_hub": _alert_hub_url,
    },
}

_app = mock.Mock(spec=github_actions.RotationApp)
_app.token.return_value = _SAMPLE
_LOG_HOST_SAMPLE = "https://productionresultssa1.blob.core.windows.net"

_app_permits = captured_permits(
    github_actions,
    lambda: github_actions.RotationApp("1", lambda: _SAMPLE)._client({}, "o/r"),
)
_secret_permits = captured_permits(
    github_actions,
    lambda: github_actions.ActionsSecrets(_app).write("o/r", "S", _SAMPLE),
)
_report_permits = captured_permits(
    github_actions,
    lambda: github_actions.FingerprintReports(_app).latest("o/r", "S"),
)

# Each adapter looks its predicate up through the module at call time, so a monkeypatch of the
# module's function reaches it. `None` is a service the lane has no client for.
PERMITS: dict[str, dict[str, Permits | None]] = {
    "rotation_worker": {
        "change_manager": None,
        "orchestrator": lambda m, p: worker_orchestrator.permits(m, p),
        "github": either(_app_permits, _secret_permits, _report_permits),
        "alert_hub": _alert_hub_permits,
    },
    "rotation_proposer": {
        "change_manager": lambda m, p: bump_change_manager._permits(m, p),
        "orchestrator": either(
            lambda m, p: bump_orchestrator._permits(m, p),
            lambda m, p: members_client._permits(members_client._READ)(m, p),
            lambda m, p: proposer_client.permits(m, p),
        ),
        "github": None,
        "alert_hub": _alert_hub_permits,
    },
    "rotation_decomposer": {
        "change_manager": lambda m, p: decomposer_client.change_manager_permits(m, p),
        "orchestrator": lambda m, p: decomposer_client.orchestrator_permits(m, p),
        "github": None,
        "alert_hub": _alert_hub_permits,
    },
}


@dataclass(frozen=True)
class Reach:
    """A reach into one service: the clients the code built, what the table says they may send,
    and the verbs permitted on every path where the service's path can't be known beforehand."""

    built: tuple[BuiltClient, ...]
    base_urls: frozenset[str]
    routes: frozenset[Route]
    any_path: frozenset[str] = frozenset()


COOLIFY_URL = "http://100.64.0.1:8000"
_COOLIFY_ROUTES = "/api/v1/{kind}/u1/envs", "/api/v1/{kind}/u1/restart"


def _coolify(kind: str, resource: Literal["application", "service"]) -> Reach:
    envs, restart = (route.format(kind=kind) for route in _COOLIFY_ROUTES)
    built = built_clients(
        coolify,
        lambda: coolify.CoolifyClient(COOLIFY_URL, _SAMPLE, resource, "u1"),
    )
    return Reach(
        tuple(built), frozenset({COOLIFY_URL}), frozenset({("PATCH", envs), ("POST", restart)})
    )


PLATFORM_EDGES: dict[str, Reach] = {
    "coolify_application": _coolify("applications", "application"),
    "coolify_service": _coolify("services", "service"),
    "healthchecks": Reach(
        tuple(built_clients(readers, lambda: readers.Healthchecks(lambda: _SAMPLE)._connect())),
        frozenset({"https://healthcheck.alobar.net"}),
        frozenset({("GET", "/api/v3/checks/")}),
    ),
    # The signed log URL GitHub redirects to: its path can't be known, so the host is the bound
    # (`test_the_log_host_is_the_bound_on_the_log_reach`) and the client carries no credential.
    "azure_log_host": Reach(
        tuple(
            built_clients(
                github_actions,
                lambda: github_actions.FingerprintReports(_app)._fetch_log(
                    f"{_LOG_HOST_SAMPLE}/logs?sig=1"
                ),
            )
        ),
        frozenset({_LOG_HOST_SAMPLE}),
        frozenset(),
        any_path=frozenset({"GET"}),
    ),
}

ORCHESTRATOR_URL = "https://sds.example"
HEALTH_HOST = "https://id.example"
_store = mock.Mock()


def _rotation(*destinations: str) -> Rotation:
    return Rotation("credential", "occurrence", "keeper", "work", destinations)


def _hosted(kind: str, resource: str, key: str, last: str) -> str:
    return f'{kind} instance="prod" resource_type="{resource}" uuid="u1" key="{key}" {last}'


_change_manager_variable, _change_manager_entry = next(iter(change_manager.VARIABLES.items()))

# One row per credential type: what its adapter sends to the service the credential belongs to.
# Its writes to a hosted service are the Coolify rows above.
PROVIDER_EDGES: dict[str, Reach] = {
    "openrouter": Reach(
        tuple(built_clients(openrouter, lambda: openrouter.build(_store, "management-key"))),
        frozenset({"https://openrouter.ai/api/v1"}),
        frozenset(
            {
                ("GET", "/key"),
                ("GET", "/models"),
                ("GET", f"/keys/{'a' * 64}"),
                ("POST", "/chat/completions"),
                ("POST", "/keys"),
                ("DELETE", f"/keys/{'a' * 64}"),
            }
        ),
    ),
    "orchestrator": Reach(
        tuple(
            built_clients(
                orchestrator_bearer,
                lambda: orchestrator_bearer.build_provider(
                    rotation=_rotation(
                        _hosted(
                            orchestrator_bearer.HOSTED_KIND,
                            "application",
                            orchestrator_bearer.VARIABLE,
                            'entry="another-key"',
                        )
                    ),
                    orchestrator=mock.Mock(),
                    orchestrator_url=ORCHESTRATOR_URL,
                    store=_store,
                    coolify_token_id="coolify-token",
                    coolify_url=COOLIFY_URL,
                    orchestrator_application="u1",
                    executor_key_id="own-key",
                ),
            )
        ),
        frozenset({ORCHESTRATOR_URL}),
        frozenset({("GET", "/api/v1/status-ledger")}),
    ),
    "github-pat": Reach(
        tuple(
            built_clients(
                github_pat,
                lambda: github_pat.build_provider(rotation=_rotation(), orchestrator=mock.Mock()),
            )
        ),
        frozenset({"https://api.github.com"}),
        frozenset({("GET", "/user"), ("POST", "/credentials/revoke")}),
    ),
    "bitbucket": Reach(
        tuple(
            built_clients(
                bitbucket,
                lambda: bitbucket.build_provider(
                    credential={"probe_email": "a@example.com", "probe_workspace": "w"}
                ),
            )
        ),
        frozenset({"https://api.bitbucket.org"}),
        frozenset({("GET", "/2.0/repositories/w")}),
    ),
    "change-manager": Reach(
        tuple(
            built_clients(
                change_manager,
                lambda: change_manager.build_provider(
                    rotation=_rotation(
                        _hosted(
                            change_manager.HOSTED_KIND,
                            "application",
                            _change_manager_variable,
                            f'entry="{_change_manager_entry}"',
                        )
                    ),
                    store=_store,
                    coolify_token_id="coolify-token",
                    coolify_url=COOLIFY_URL,
                    change_manager_application="u1",
                    siblings=list,
                    readers_mid_run=list,
                ),
            )
        ),
        frozenset({"https://change-mgr.alobar.net"}),
        frozenset({("GET", "/api/landing-policy")}),
    ),
    # No client: a fresh login in a child process (`PROCESSES`).
    "bws-machine-token": Reach((), frozenset(), frozenset()),
    "postgres-role": Reach(
        tuple(
            built_clients(
                postgres_role,
                lambda: postgres_role.build_provider(
                    rotation=_rotation(
                        _hosted(postgres_role.HOSTED_KIND, "service", "K", "redeploy=true")
                    ),
                    credential={"helper_target": "t", "health_url": f"{HEALTH_HOST}/health"},
                    store=_store,
                    coolify_token_id="coolify-token",
                    coolify_url=COOLIFY_URL,
                    pg_helper=mock.Mock(),
                ),
            )
        ),
        frozenset({HEALTH_HOST}),
        frozenset({("GET", "/health")}),
    ),
}

GIT_MIRROR = "src/rotation_worker/git_mirror.py"
SECRET_STORE = "src/rotation_worker/secret_store.py"
# Imported through `rotation_worker.contract` for its protocol: these lanes never construct a store.
_STORE_HEADS = frozenset({"SECURITY", "subprocess.run as a value", "bitwarden_sdk"})
_STORE_MODULE_ONLY = {SECRET_STORE: _STORE_HEADS}
_MIRROR = {GIT_MIRROR: frozenset({"git"})}

# Each module that can start a process, and what it starts, as its code spells the program.
# `test_each_named_program_is_the_one_it_says` reads what the names hold.
PROCESSES: dict[str, dict[str, frozenset[str]]] = {
    "rotation_worker": {
        **_MIRROR,
        SECRET_STORE: _STORE_HEADS,
        "src/rotation_worker/helper_client.py": frozenset({"SSH", "subprocess.run as a value"}),
        "src/rotation_worker/adapters/bws_machine_token.py": frozenset({"self._interpreter"}),
    },
    "rotation_proposer": {
        **_MIRROR,
        **_STORE_MODULE_ONLY,
        "src/rotation_proposer/findings.py": frozenset({"str(node_binary())"}),
        "src/bump_proposer/standing.py": frozenset({"git", "str(python)", "PUBLISH_COMMAND"}),
    },
    "rotation_decomposer": {**_MIRROR, **_STORE_MODULE_ONLY},
}

# Each module a lane can reach that holds a network client, with how many `ConfinedClient`s it
# builds. The two shared transports, `estate_clients` and `alert_hub_client`, are every lane's.
_WORKER_CLIENTS = {
    "src/rotation_worker/orchestrator_client.py": 1,
    "src/rotation_worker/coolify.py": 1,
    "src/rotation_worker/github_actions.py": 4,
    "src/rotation_worker/readers.py": 1,
    "src/rotation_worker/notify.py": 0,
    "src/rotation_worker/adapters/openrouter.py": 1,
    "src/rotation_worker/adapters/orchestrator_bearer.py": 1,
    "src/rotation_worker/adapters/github_pat.py": 2,
    "src/rotation_worker/adapters/bitbucket.py": 1,
    "src/rotation_worker/adapters/change_manager.py": 1,
    "src/rotation_worker/adapters/postgres_role.py": 1,
}
NETWORK: dict[str, dict[str, int]] = {
    "rotation_worker": _WORKER_CLIENTS,
    "rotation_proposer": {
        "src/rotation_proposer/client.py": 1,
        "src/rotation_proposer/alerts.py": 0,
        "src/bump_proposer/change_manager.py": 1,
        "src/bump_proposer/orchestrator_client.py": 1,
        "src/sds_members/client.py": 1,
    },
    "rotation_decomposer": {
        "src/rotation_decomposer/client.py": 1,
        "src/rotation_decomposer/alerts.py": 0,
    },
}

AS_A_VALUE = "as a value"
# Everything each lane takes from another program, by module. A name added here is a surface
# shared with that program.
BORROWED: dict[str, dict[str, frozenset[str]]] = {
    "rotation_decomposer": {
        "alert_hub_client": frozenset(
            {
                "Alert",
                "AlertHubError",
                "FirstSightings",
                "KeyedAlerts",
                "StateUnwritable",
                "alert_key",
            }
        ),
        "estate_clients.confined": frozenset(
            {"ConfinedClient", "TransportFailure", "base_url_problem", "error_code"}
        ),
        "rotation_worker.contract": frozenset({"ContractError", "Plan", "UnitPlan"}),
        "rotation_worker.destinations": frozenset({"DestinationError", "same"}),
        "rotation_worker.envelope": frozenset({"NON_HOSTED_KEY"}),
        "rotation_worker.git_mirror": frozenset(
            {"MirrorError", "RepoMirror", "default_mirror_root"}
        ),
        "rotation_worker.grants": frozenset({"GrantUnread", "rotation_granted"}),
        "rotation_worker.plans": frozenset({"derive", "select_plan", "writes_hosted_service"}),
        "rotation_worker.registry": frozenset({"RegistryEntry", "RegistryError", "credentials"}),
        "rotation_worker.types": frozenset({"CLASS_TYPES", "TYPES"}),
    },
    "rotation_proposer": {
        "alert_hub_client": frozenset(
            {
                "Alert",
                "AlertHubError",
                "FirstSightings",
                "KeyedAlerts",
                "StateUnwritable",
                "alert_key",
            }
        ),
        "bump_proposer.change_manager": frozenset(
            {"ChangeManagerClient", "ChangeManagerError", "DEFAULT_BASE_URL", "ProposalRefused"}
        ),
        "bump_proposer.orchestrator_client": frozenset(
            {
                "ObservationCredentialError",
                "ObservationWriteError",
                "OrchestratorClient",
                "UnusableEndpointError",
                "open_client",
            }
        ),
        "bump_proposer.standing": frozenset(
            {
                "PACKAGES_REPOSITORY",
                "StandingError",
                "checkout_root",
                "lifecycle",
                "publish",
                "require_clean",
                "require_publishable",
                "snapshot_hash",
                "yaml_scalar",
            }
        ),
        "estate_clients.confined": frozenset(
            {"ConfinedClient", "TransportFailure", "base_url_problem", "error_code"}
        ),
        "rotation_worker.contract": frozenset(
            {"ContractError", "HELD_KIND", "HumanAct", "Plan", "StepKind", "UnitPlan"}
        ),
        "rotation_worker.destinations": frozenset(
            {"DestinationError", "HOSTED_KINDS", "parse", "same"}
        ),
        "rotation_worker.git_mirror": frozenset(
            {"MirrorError", "RepoMirror", "default_mirror_root"}
        ),
        "rotation_worker.plans": frozenset({"criteria_steps", "derive"}),
        "rotation_worker.registry": frozenset(
            {"RegistryEntry", "RegistryError", "credentials", "standing_credential"}
        ),
        "rotation_worker.types": frozenset({"CLASS_TYPES", "TYPES"}),
        "sds_members.client": frozenset({"MembersUnreadable", "read_members"}),
        "sds_members.populations": frozenset({"granted"}),
    },
    "rotation_worker": {
        "alert_hub_client": frozenset(
            {
                "ALERTS",
                "Alert",
                "AlertHubError",
                "FirstSightings",
                "KeyedAlerts",
                "StateUnwritable",
                "TokenUnreadable",
                "alert_key",
            }
        ),
        "estate_clients.confined": frozenset(
            {
                "ConfinedClient",
                "TransportFailure",
                "base_url_problem",
                "error_code",
                "error_detail",
                "tailnet_or_https_problem",
            }
        ),
    },
}

LAUNCHERS: dict[str, str] = {
    "rotation_proposer": "scripts/run-rotation-proposer.sh",
    "rotation_decomposer": "scripts/run-rotation-decomposer.sh",
}

# Every `Config` field is an identity (`EDGES["rotation_worker"].bearers`) or one of these.
NOT_IDENTITIES = frozenset(
    {
        "change_manager_application",
        "coolify_url",
        "github_app_id",
        "keepers_project_id",
        "orchestrator_application",
        "orchestrator_url",
        "organization_id",
        "pg_helper_host",
        "pg_helper_known_host",
        "source_revision",
        "state_dir",
        "store",
        "work_project_id",
    }
)
# Every provider an adapter names either has an entry in `Config.admin_secrets` or has none: the
# secret its adapter needs to mint or revoke, or the Coolify token a hosted adapter writes with.
ADMIN_SECRET_PROVIDERS = frozenset(
    {"openrouter", "orchestrator", "change-manager", "postgres-role"}
)
NO_ADMIN_SECRET = frozenset({"bitbucket", "bitwarden-sm", "fake", "github"})

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")

# Every listed path, stated apart from the rows. A corpus built from the rows alone loses a dropped
# row's path with the row, and the code's acceptance of it goes unseen.
ROUTES = frozenset(
    {
        "/api/items",
        "/api/landing-policy",
        "/api/work-changes",
        "/api/v1/change-records/44/work",
        "/api/v1/held-credentials",
        "/api/v1/m2m-identities",
        "/api/v1/members",
        "/api/v1/observations",
        "/api/v1/status-ledger",
        INTAKE_PATH,
        PROPOSALS_PATH,
        UNIT_PATH,
        f"{UNIT_PATH}/claim",
        f"{UNIT_PATH}/commands/fail",
        f"{UNIT_PATH}/commands/start",
        f"{UNIT_PATH}/commands/submit",
        f"{UNIT_PATH}/evidence",
        f"{UNIT_PATH}/history",
        f"{UNIT_PATH}/renew",
        "/v1/alerts",
        "/app/installations/44/access_tokens",
        "/credentials/revoke",
        "/repos/o/r/actions/jobs/44/logs",
        "/repos/o/r/actions/runs/44/jobs",
        "/repos/o/r/actions/secrets/S",
        "/repos/o/r/actions/secrets/public-key",
        "/repos/o/r/actions/workflows/secret-fingerprint.yml/runs",
        "/repos/o/r/installation",
        "/user",
        "/2.0/repositories/w",
        "/api/v1/applications/u1/envs",
        "/api/v1/applications/u1/restart",
        "/api/v1/services/u1/envs",
        "/api/v1/services/u1/restart",
        "/api/v3/checks/",
        "/health",
        "/chat/completions",
        "/key",
        "/keys",
        f"/keys/{'a' * 64}",
        "/models",
    }
)

PROBES = BRIDGE_PROBES | {
    f"{UNIT_PATH}/runner-brief",
    f"{UNIT_PATH}/approvals",
    f"{UNIT_PATH}/adjudications",
    f"{UNIT_PATH}/dependencies",
    f"{UNIT_PATH}/commands/complete",
    f"{UNIT_PATH}/commands/cancel",
    f"{UNIT_PATH}/reclaim-expired-claim",
    f"{UNIT_PATH}/release-expired-claim",
    f"/api/v1/work-units/{UNIT.upper()}",
    "/api/v1/work-units/not-a-uuid",
    "/api/v1/dependencies/x/resolve",
    "/api/v1/change-records/44",
    "/review",
    "/repos/o/other/actions/secrets/S",
    "/repos/o/r/actions/secrets/OTHER",
    "/repos/o/r/actions/secrets",
    "/repos/o/r/actions/runs/44/logs",
    "/repos/o/r/actions/workflows/other.yml/runs",
    "/repos/o/other/installation",
    "/repos/o/other/actions/jobs/44/logs",
    "/repos/o/r/branches/main/protection",
    "/repos/o/r/branches/other/protection/required_status_checks",
    "/app/installations/44",
    "/app",
    "/user/repos",
    "/credentials",
    "/2.0/repositories/other",
    "/2.0/user",
    "/keys/short",
    "/credits",
    "/api/v1/applications/u2/envs",
    "/api/v1/applications/u2/restart",
    "/api/v1/services/u2/envs",
    "/api/v1/applications/u1",
    "/api/v1/applications/u1/stop",
    "/api/v1/services/u1/stop",
    "/api/v1/deploy",
    "/api/v3/checks/x/pause",
    "/api/v3/pings/",
    "/health/x",
}


def _reach_paths() -> frozenset[str]:
    return frozenset(
        path
        for reach in (*PLATFORM_EDGES.values(), *PROVIDER_EDGES.values())
        for _, path in reach.routes
    )


def _listed_paths() -> frozenset[str]:
    return _reach_paths() | {
        path for edge in EDGES.values() for column in edge.columns().values() for _, path in column
    }


def _corpus() -> frozenset[str]:
    return near_misses(ROUTES) | PROBES


def disagreements(lane: str) -> frozenset[tuple[str, str, str, bool]]:
    return route_disagreements(EDGES[lane], PERMITS[lane], _corpus(), METHODS)


def reach_disagreements(reach: Reach) -> frozenset[tuple[str, str, bool]]:
    """(method, path, accepted) for each corpus route where the clients and the row differ, and
    ("base_url", url, built) for a host one has that the other hasn't."""
    permits = either(*(client.permits for client in reach.built))
    found: set[tuple[str, str, bool]] = set()
    for method in METHODS:
        for path in _corpus():
            accepted = sendable(permits, method, path)
            listed = (method, path) in reach.routes or (
                method in reach.any_path and not has_control_character(path)
            )
            if accepted != listed:
                found.add((method, path, accepted))
    built = {client.base_url for client in reach.built}
    found |= {("base_url", url, url in built) for url in built ^ reach.base_urls}
    return frozenset(found)


def test_every_listed_route_is_in_the_corpus() -> None:
    assert _listed_paths() == ROUTES


@pytest.mark.parametrize("lane", sorted(EDGES))
def test_each_lane_accepts_exactly_its_routes_among_the_corpus(lane: str) -> None:
    assert sorted(disagreements(lane)) == []


@pytest.mark.parametrize("service", sorted(PLATFORM_EDGES))
def test_each_platform_reach_is_exactly_its_routes(service: str) -> None:
    assert sorted(reach_disagreements(PLATFORM_EDGES[service]), key=repr) == []


@pytest.mark.parametrize("adapter", sorted(PROVIDER_EDGES))
def test_each_provider_reach_is_exactly_its_routes(adapter: str) -> None:
    assert sorted(reach_disagreements(PROVIDER_EDGES[adapter]), key=repr) == []


def test_each_column_states_the_base_url_its_code_holds() -> None:
    for lane, edge in EDGES.items():
        assert set(edge.base_urls) == {name for name, routes in edge.columns().items() if routes}
        held = {service: read() for service, read in BASE_URL_READS[lane].items()}
        assert held == edge.base_urls, lane


def test_the_platform_table_has_a_row_for_each_coolify_resource() -> None:
    resources = typing.get_args(
        typing.get_type_hints(coolify.CoolifyClient.__init__)["resource_type"]
    )
    assert {f"coolify_{resource}" for resource in resources} == {
        row for row in PLATFORM_EDGES if row.startswith("coolify_")
    }


def test_a_client_built_for_one_repository_refuses_another() -> None:
    other = captured_permits(
        github_actions,
        lambda: github_actions.ActionsSecrets(_app).write("o/other", "S", _SAMPLE),
    )
    assert other("PUT", "/repos/o/other/actions/secrets/S")
    assert not other("PUT", "/repos/o/r/actions/secrets/S")


@pytest.mark.parametrize(
    "location",
    [
        "https://attacker.example/logs",
        "https://productionresultssa1.blob.core.windows.net.attacker.example/logs",
        "http://productionresultssa1.blob.core.windows.net/logs",
        "",
    ],
)
def test_the_log_host_is_the_bound_on_the_log_reach(location: str) -> None:
    with pytest.raises(ContractError, match="somewhere unexpected"):
        github_actions.FingerprintReports(_app)._fetch_log(location)


def test_the_log_client_sends_no_credential() -> None:
    recorded: list[dict[str, object]] = []

    def recorder(**arguments: object) -> object:
        recorded.append(arguments)
        raise ContractError("recorded")

    with mock.patch.object(github_actions, "ConfinedClient", recorder):
        with pytest.raises(ContractError, match="recorded"):
            github_actions.FingerprintReports(_app)._fetch_log(f"{_LOG_HOST_SAMPLE}/logs")
    assert [call["headers"] for call in recorded] == [{}]


# Each form a process can be started in: (what it is, the module, what the scan must report).
FORMS: Final = [
    (
        "async forwarder",
        "import subprocess\n"
        "async def launch(argv):\n"
        "    subprocess.run(argv)\n"
        "async def f():\n"
        '    await launch(["curl"])',
        "curl",
    ),
    (
        "lambda parameter shadowing a module name",
        'import subprocess\nargv = ["ok"]\ng = lambda argv: subprocess.run(argv)',
        "argv",
    ),
    (
        "dotted module alias",
        "import asyncio.subprocess as a\n"
        "async def f():\n"
        '    await a.create_subprocess_exec("curl")',
        "asyncio.subprocess.create_subprocess_exec",
    ),
    (
        "forwarder nothing here calls",
        "import subprocess\ndef launch(argv):\n    subprocess.run(argv)",
        "launch(forwarded)",
    ),
    (
        "async def",
        'import subprocess\nasync def f():\n    subprocess.run(["curl"])',
        "curl",
    ),
    (
        "module level",
        'import subprocess\nsubprocess.run(["curl"])',
        "curl",
    ),
    (
        "lambda",
        'import subprocess\ng = lambda: subprocess.run(["curl"])',
        "curl",
    ),
    (
        "same-name methods",
        "import subprocess\n"
        "class A:\n"
        "    def go(self):\n"
        '        subprocess.run(["curl"])\n'
        "class B:\n"
        "    def go(self):\n"
        "        pass",
        "curl",
    ),
    (
        "args keyword",
        'import subprocess\ndef f():\n    subprocess.run(args=["curl"])',
        "curl",
    ),
    (
        "forwarding helper",
        "import subprocess\n"
        "def launch(argv):\n"
        "    subprocess.run(argv)\n"
        "def f():\n"
        '    launch(["curl"])',
        "curl",
    ),
    (
        "forwarding method",
        "import subprocess\n"
        "class A:\n"
        "    def launch(self, argv):\n"
        "        subprocess.run(argv)\n"
        "    def f(self):\n"
        '        self.launch(["curl"])',
        "curl",
    ),
    (
        "two-hop forward",
        "import subprocess\n"
        "def a(x):\n"
        "    subprocess.run(x)\n"
        "def b(y):\n"
        "    a(y)\n"
        "def f():\n"
        '    b(["curl"])',
        "curl",
    ),
    (
        "rebound parameter",
        'import subprocess\ndef f(argv):\n    argv = ["curl"]\n    subprocess.run(argv)',
        "curl",
    ),
    (
        "from import Popen",
        'from subprocess import Popen\ndef f():\n    Popen(["curl"])',
        "subprocess.Popen",
    ),
    (
        "aliased module",
        'import subprocess as sp\ndef f():\n    sp.run(["curl"])',
        "curl",
    ),
    (
        "aliased from",
        'from subprocess import run as go\ndef f():\n    go(["curl"])',
        "curl",
    ),
    (
        "check_output",
        'import subprocess\ndef f():\n    subprocess.check_output(["curl"])',
        "subprocess.check_output",
    ),
    (
        "asyncio",
        'import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec("curl")',
        "asyncio.create_subprocess_exec",
    ),
    (
        "from asyncio",
        'from asyncio import create_subprocess_shell as s\nasync def f():\n    await s("curl")',
        "asyncio.create_subprocess_shell",
    ),
    (
        "pty",
        'import pty\ndef f():\n    pty.spawn(["curl"])',
        "import pty",
    ),
    (
        "multiprocessing",
        "from multiprocessing import Process\ndef f():\n    Process(target=print).start()",
        "import multiprocessing",
    ),
    (
        "aliased os",
        'import os as o\ndef f():\n    o.system("curl")',
        "os.system",
    ),
    (
        "from os",
        'from os import execv\ndef f():\n    execv("/c", [])',
        "os.execv",
    ),
    (
        "unresolved argv",
        "import subprocess\ndef f():\n    subprocess.run(build())",
        "build()",
    ),
    (
        "starter as a value",
        "import subprocess\nGO = subprocess.run",
        "subprocess.run as a value",
    ),
    (
        "module as a value",
        'import subprocess\nGO = getattr(subprocess, "run")',
        "subprocess as a value",
    ),
    (
        "dynamic import",
        'import importlib\nGO = importlib.import_module("subprocess")',
        "import importlib",
    ),
    (
        "dunder import",
        'GO = __import__("subprocess")',
        "__import__",
    ),
    (
        "exec",
        'exec("import subprocess")',
        "exec",
    ),
]

SRC: Final = Path("src")
SHARED_TRANSPORTS: Final = frozenset(
    {"src/estate_clients/confined.py", "src/alert_hub_client/__init__.py"}
)
NETWORK_IMPORTS: Final = frozenset(
    {"aiohttp", "alert_hub_client", "estate_clients", "http", "httpx", "requests", "socket"}
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _source_of(module: str, root: Path) -> Path | None:
    path = root.joinpath(*module.split("."))
    for candidate in (path.with_suffix(".py"), path / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def closure(lane: str, root: Path = SRC) -> frozenset[Path]:
    """The lane's own modules and every first-party module they import, transitively."""
    seen: set[Path] = set()
    waiting = sorted((root / lane).rglob("*.py"))
    while waiting:
        path = waiting.pop()
        if path in seen:
            continue
        seen.add(path)
        for found in file_imports(path, root, names=True):
            parts = found.module.split(".")
            for end in range(1, len(parts) + 1):
                source = _source_of(".".join(parts[:end]), root)
                if source is not None:
                    waiting.append(source)
    return frozenset(seen)


def _talks(path: Path, root: Path) -> bool:
    imported = {found.module for found in file_imports(path, root, names=True)}
    return bool(
        {name.split(".")[0] for name in imported} & NETWORK_IMPORTS or "urllib.request" in imported
    )


def _clients_built(tree: ast.AST) -> int:
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ConfinedClient"
        for node in ast.walk(tree)
    )


def network(lane: str, root: Path = SRC) -> dict[str, int]:
    """The lane's modules holding a network client, each with how many it builds."""
    return {
        path.as_posix(): _clients_built(ast.parse(_text(path)))
        for path in closure(lane, root)
        if path.as_posix() not in SHARED_TRANSPORTS and _talks(path, root)
    }


def processes(lane: str, root: Path = SRC) -> dict[str, frozenset[str]]:
    found = {path.as_posix(): process_heads(_text(path)) for path in closure(lane, root)}
    return {path: heads for path, heads in found.items() if heads}


def _binding_names(tree: ast.Module, root: Path, lane: str) -> dict[str, str]:
    """Each local name bound to a first-party module of another program: the module it names."""
    whole: dict[str, str] = {}

    def foreign(module: str) -> bool:
        top = module.split(".")[0]
        return top != lane and _source_of(top, root) is not None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if foreign(alias.name):
                    whole[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and foreign(node.module):
            for alias in node.names:
                dotted = f"{node.module}.{alias.name}"
                if _source_of(dotted, root) is not None:
                    whole[alias.asname or alias.name] = dotted
    return whole


def borrowed(lane: str, root: Path = SRC) -> dict[str, frozenset[str]]:
    """Every name the lane takes from another program's modules, by module.

    A module imported whole is charged the attributes read on it. Used any other way, passed as a
    value or handed to `getattr`, it is charged `AS_A_VALUE`, which no row should need.
    """
    found: dict[str, set[str]] = {}
    for path in sorted((root / lane).rglob("*.py")):
        tree = ast.parse(_text(path))
        whole = _binding_names(tree, root, lane)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top != lane and _source_of(top, root) is not None:
                    names = {alias.name for alias in node.names}
                    names = {
                        name for name in names if _source_of(f"{node.module}.{name}", root) is None
                    }
                    if names:
                        found.setdefault(node.module, set()).update(names)
        read: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and ast.unparse(node.value) in whole:
                found.setdefault(whole[ast.unparse(node.value)], set()).add(node.attr)
                read |= {id(inner) for inner in ast.walk(node.value)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and id(node) not in read:
                for name, module in whole.items():
                    if name.split(".")[0] == node.id:
                        found.setdefault(module, set()).add(AS_A_VALUE)
    return {module: frozenset(names) for module, names in found.items()}


def spelled_pushes(text: str) -> frozenset[str]:
    """Each string in the module's code that would make `git` publish. Docstrings don't count."""
    tree = ast.parse(text)
    prose = docstring_nodes(tree)
    return frozenset(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
        and (node.value == "push" or node.value.startswith("git push"))
    )


def child_imports(script: str, tmp_path: Path) -> frozenset[str]:
    written = tmp_path / "child.py"
    written.write_text(script)
    return frozenset(found.top for found in file_imports(written))


@pytest.mark.parametrize("lane", sorted(EDGES))
def test_each_lane_runs_exactly_its_processes(lane: str) -> None:
    assert processes(lane) == PROCESSES[lane]


def test_each_named_program_is_the_one_it_says(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert secret_store.SECURITY == "/usr/bin/security"
    assert helper_client.SSH == "/usr/bin/ssh"
    assert bump_standing.PUBLISH_COMMAND == "git push origin main"

    monkeypatch.setattr(findings.shutil, "which", lambda name: f"/found/{name}")
    assert findings.node_binary() == Path("/found/node")
    assert {path.name for path in findings.NODE_FALLBACKS} == {"node"}

    # `self._interpreter`: the running interpreter, isolated, on one of the two scripts.
    default = inspect.signature(bws_machine_token.BwsMachineTokenProvider).parameters["interpreter"]
    assert default.default == sys.executable
    run = mock.Mock(return_value=mock.Mock(stdout="live"))
    monkeypatch.setattr(bws_machine_token.subprocess, "run", run)
    bws_machine_token.BwsMachineTokenProvider().probe(_SAMPLE)
    assert run.call_args.args[0] == [sys.executable, "-I", "-c", bws_machine_token._LOGIN]

    # `str(python)`: the packages checkout's own interpreter, running `intent_packages` alone.
    monkeypatch.delenv("BUMP_PROPOSER_PACKAGES_PYTHON", raising=False)
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    sent = mock.Mock(return_value="")
    monkeypatch.setattr(bump_standing, "_run", sent)
    bump_standing.lifecycle(tmp_path, "revise")
    sent.assert_called_once_with(tmp_path, [str(python), "-m", "intent_packages", "revise"])


def test_only_the_publishing_command_pushes() -> None:
    pushing = {
        path.as_posix(): spelled_pushes(_text(path))
        for lane in EDGES
        for path in closure(lane)
        if spelled_pushes(_text(path))
    }
    assert pushing == {"src/bump_proposer/standing.py": frozenset({bump_standing.PUBLISH_COMMAND})}


def test_each_child_login_imports_only_the_named_sdk(tmp_path: Path) -> None:
    scripts = {
        name: value
        for name, value in vars(bws_machine_token).items()
        if isinstance(value, str) and "import " in value and name != "__doc__"
    }
    assert bws_machine_token.CHILD_SDK == "bitwarden_sdk"
    assert {name: child_imports(script, tmp_path) for name, script in scripts.items()} == {
        "_LOGIN": {"bitwarden_sdk", "sys"},
        "_REACH": {"bitwarden_sdk", "hashlib", "sys"},
    }


@pytest.mark.parametrize("lane", sorted(LAUNCHERS))
def test_each_launcher_fetches_exactly_its_bearers(lane: str) -> None:
    assert launcher_bearers(Path(LAUNCHERS[lane])) == EDGES[lane].bearers


def unplaced(fields: set[str]) -> set[str]:
    """Every `Config` field in neither partition, and every name in both or in neither's place."""
    identities = EDGES["rotation_worker"].bearers
    return (fields ^ (identities | NOT_IDENTITIES)) | (identities & NOT_IDENTITIES)


def test_the_worker_names_its_identities_in_config() -> None:
    assert unplaced({field.name for field in dataclasses.fields(Config)}) == set()


def test_every_provider_either_has_an_admin_secret_or_has_none() -> None:
    providers = {adapter.provider_name for adapter in ADAPTERS.values()}
    assert providers == ADMIN_SECRET_PROVIDERS | NO_ADMIN_SECRET
    assert not ADMIN_SECRET_PROVIDERS & NO_ADMIN_SECRET


@pytest.mark.parametrize("lane", sorted(EDGES))
def test_each_lane_uses_only_the_named_part_of_its_siblings(lane: str) -> None:
    assert borrowed(lane) == BORROWED[lane]


def _programs() -> set[str]:
    return {
        match.group(1)
        for pattern in load()["rotation"].paths
        if (match := re.fullmatch(r"src/(rotation_[a-z]+)/.*", pattern))
    }


def test_the_table_is_the_layer_population() -> None:
    assert sorted(EDGES) == sorted(PERMITS) == sorted(PROCESSES) == sorted(NETWORK)
    assert sorted(EDGES) == sorted(_programs())
    assert sorted(PROVIDER_EDGES) == sorted(TYPES)
    assert sorted(LAUNCHERS) == sorted(set(EDGES) - {"rotation_worker"})
    for lane in EDGES:
        assert network(lane) == NETWORK[lane], lane


@pytest.mark.parametrize("lane", sorted(EDGES))
def test_a_lane_with_an_alert_column_sends_through_the_shared_client(lane: str) -> None:
    importing = {
        path.as_posix()
        for path in closure(lane)
        if path.as_posix() not in SHARED_TRANSPORTS
        and "alert_hub_client" in {found.top for found in file_imports(path, SRC)}
    }
    assert bool(importing) == (EDGES[lane].alert_hub == ALERTS)
    assert EDGES[lane].alert_hub in (ALERTS, frozenset())
    assert alert_hub_client.ALERTS == "/v1/alerts"


def _tree(root: Path, modules: Mapping[str, str]) -> Path:
    for name, text in modules.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_control_a_widened_predicate_fails_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison is not vacuous: widen the worker's client and the table reports it."""
    original = worker_orchestrator.permits
    monkeypatch.setattr(
        worker_orchestrator,
        "permits",
        lambda m, p: p == f"{UNIT_PATH}/runner-brief" or original(m, p),
    )
    assert ("orchestrator", "GET", f"{UNIT_PATH}/runner-brief", True) in disagreements(
        "rotation_worker"
    )


def test_control_a_widening_in_the_borrowed_client_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = bump_change_manager.is_allowed_write
    monkeypatch.setattr(
        bump_change_manager,
        "is_allowed_write",
        lambda path: path == "/api/items/44/approve" or original(path),
    )
    assert ("change_manager", "POST", "/api/items/44/approve", True) in disagreements(
        "rotation_proposer"
    )


def test_control_a_moved_platform_route_fails_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readers, "CHECKS", "/api/v3/pings/")
    found = reach_disagreements(PLATFORM_EDGES["healthchecks"])
    assert ("GET", "/api/v3/pings/", True) in found
    assert ("GET", "/api/v3/checks/", False) in found


def test_control_a_moved_provider_route_fails_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_pat, "PROBE_PATH", "/user/repos")
    found = reach_disagreements(PROVIDER_EDGES["github-pat"])
    assert ("GET", "/user/repos", True) in found
    assert ("GET", "/user", False) in found


def test_control_a_second_host_fails_the_table() -> None:
    widened = dataclasses.replace(
        PROVIDER_EDGES["bitbucket"],
        built=(
            *PROVIDER_EDGES["bitbucket"].built,
            BuiltClient("https://attacker.example", lambda m, p: False),
        ),
    )
    assert ("base_url", "https://attacker.example", True) in reach_disagreements(widened)


@pytest.mark.parametrize(("name", "source", "started"), FORMS)
def test_control_every_way_of_starting_a_process_is_seen(
    name: str, source: str, started: str
) -> None:
    assert started in process_heads(source), name


def test_control_a_new_process_reaches_the_lane_through_its_imports(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "lane/__init__.py": "from other import helper\n",
            "other/__init__.py": "",
            "other/helper.py": 'import subprocess\nsubprocess.run(["curl"])\n',
        },
    )
    assert processes("lane", root) == {(root / "other/helper.py").as_posix(): {"curl"}}


def test_control_a_push_spelled_in_a_string_is_seen() -> None:
    assert spelled_pushes('COMMAND = ["git", "push"]') == {"push"}
    assert spelled_pushes('"""git push origin main is prose."""') == frozenset()


def test_control_a_new_child_import_is_seen(tmp_path: Path) -> None:
    assert "requests" in child_imports("import requests\nimport sys", tmp_path)


def test_control_a_secret_in_a_sourced_script_fails_the_table(tmp_path: Path) -> None:
    (tmp_path / "helper.sh").write_text(f'SECRET="{VERIFIER_BEARER}"\n')
    (tmp_path / "middle.sh").write_text('source "$(dirname "$0")/helper.sh"\n')
    launcher = tmp_path / "run.sh"
    launcher.write_text('#!/bin/bash\n# source "$REPO_ROOT/scripts/nothing.sh"\n. "middle.sh"\n')
    assert launcher_bearers(launcher) == {VERIFIER_BEARER}


def test_control_a_source_it_cannot_place_fails(tmp_path: Path) -> None:
    launcher = tmp_path / "run.sh"
    launcher.write_text('source "$SOMEWHERE"\n')
    with pytest.raises(AssertionError, match="SOMEWHERE"):
        launcher_bearers(launcher)


def test_control_a_new_config_identity_fails_the_table() -> None:
    fields = {field.name for field in dataclasses.fields(Config)}
    assert unplaced(fields | {"coolify_token_id"}) == {"coolify_token_id"}


@pytest.mark.parametrize(
    ("source", "module", "name"),
    [
        ("from other.mod import more\n", "other.mod", "more"),
        ("from other import mod as m\nm.more()\n", "other.mod", "more"),
        ("import other.mod\nother.mod.more()\n", "other.mod", "more"),
        ("import other.mod as m\nm.more()\n", "other.mod", "more"),
        ('from other import mod\ngetattr(mod, "more")\n', "other.mod", AS_A_VALUE),
        ("from other import mod\nprint(mod)\n", "other.mod", AS_A_VALUE),
        ("import other.mod\nprint(other.mod)\n", "other.mod", AS_A_VALUE),
    ],
)
def test_control_a_wider_borrowing_is_seen(
    tmp_path: Path, source: str, module: str, name: str
) -> None:
    root = _tree(tmp_path, {"lane/a.py": source, "other/__init__.py": "", "other/mod.py": ""})
    assert name in borrowed("lane", root)[module]


def test_control_a_new_http_client_module_fails_the_table(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"lane/a.py": "import httpx\n"})
    assert network("lane", root) == {(root / "lane/a.py").as_posix(): 0}


def test_control_a_second_client_in_a_module_fails_the_table(tmp_path: Path) -> None:
    text = "import httpx\nConfinedClient()\nConfinedClient()\n"
    root = _tree(tmp_path, {"lane/a.py": text})
    assert network("lane", root) == {(root / "lane/a.py").as_posix(): 2}


def test_control_a_moved_base_url_fails_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_actions, "API", "https://ghe.example")
    with pytest.raises(AssertionError):
        test_each_column_states_the_base_url_its_code_holds()
