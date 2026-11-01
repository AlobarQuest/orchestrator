"""Architecture guard: one table states every rotation lane's whole outbound surface.

Four programs make up the rotation layer, and each pins its own routes in its own tests. These
tables state them in one place, in the bridge table's shape (`edge_table.Edge`): the change-manager,
orchestrator, GitHub and alert-hub routes each lane may send and the bearers its launcher fetches.
`PLATFORM_EDGES` adds the executor's hosting and log reach, `PROVIDER_EDGES` each adapter's reach
into the service whose credential it rotates, and `PROCESSES` every program a lane can start.

Every cell is checked against the lane's OWN predicates under every verb, over one corpus tried
against every column: each listed route, its trailing-slash, suffixed and trailing-newline near
misses, and named probes. A predicate the program builds per instance or inline is captured from
the `ConfinedClient` constructor, closed over its sample repository or application, so the table
never holds a stand-in. A widening to a route outside the corpus doesn't fail here.

`closure` follows imports, so a lane's row covers what it borrows as well as what it owns. A module
is charged for what it imports, whether or not the lane calls it: `rotation_worker.contract`
imports `secret_store` for its protocol, so the proposer and the decomposer carry that module's
row and never run it. The AST rule for a process reads the argument lists of `run`-shaped calls, so
a runner passed under another name isn't seen.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal
from unittest import mock

import pytest

import alert_hub_client
from bump_proposer import change_manager as bump_change_manager
from bump_proposer import orchestrator_client as bump_orchestrator
from bump_proposer import standing as bump_standing
from rotation_decomposer import client as decomposer_client
from rotation_proposer import client as proposer_client
from rotation_proposer import findings
from rotation_verifier import client as verifier_client
from rotation_verifier import required_checks
from rotation_worker import coolify, github_actions, helper_client, readers, secret_store
from rotation_worker import orchestrator_client as worker_orchestrator
from rotation_worker.adapters import (
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
from tests.architecture.edge_table import (
    GH_AUTH_TOKEN,
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
)
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

EDGES: dict[str, Edge] = {
    "rotation_worker": Edge(
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
    "rotation_proposer": Edge(
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
        bearers=frozenset({PROPOSE_BEARER, OBSERVER_BEARER, SDS_BRIDGE_ALERT_BEARER}),
    ),
    "rotation_decomposer": Edge(
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
        bearers=frozenset({SYSTEM_BEARER, READ_BEARER, SDS_BRIDGE_ALERT_BEARER}),
    ),
    "rotation_verifier": Edge(
        change_manager=frozenset(),
        orchestrator=frozenset(
            {
                ("GET", "/api/v1/status-ledger"),
                ("GET", UNIT_PATH),
                ("POST", f"{UNIT_PATH}/verify"),
                ("POST", f"{UNIT_PATH}/verifier-evidence/named-check"),
            }
        ),
        github=frozenset({("GET", "/repos/o/r/branches/main/protection/required_status_checks")}),
        alert_hub=frozenset(),
        bearers=frozenset({VERIFIER_BEARER, GH_AUTH_TOKEN}),
    ),
}

# Every `Value` joins the scrub list of its process, so a sample must be a string no test's output
# contains.
_SAMPLE = Value("rotation-edges-sample-credential")
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
    "rotation_verifier": {
        "change_manager": None,
        "orchestrator": lambda m, p: verifier_client.permits(m, p),
        "github": lambda m, p: required_checks._permits(m, p),
        "alert_hub": None,
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
_STORE_MODULE_ONLY = {SECRET_STORE: frozenset({"SECURITY", "bitwarden_sdk"})}
_MIRROR = {GIT_MIRROR: frozenset({"git"})}

# Each module that can start a process, and what it starts, as its code spells the program.
# `test_each_named_program_is_the_one_it_says` reads what the names hold.
PROCESSES: dict[str, dict[str, frozenset[str]]] = {
    "rotation_worker": {
        **_MIRROR,
        SECRET_STORE: frozenset({"SECURITY", "bitwarden_sdk"}),
        "src/rotation_worker/helper_client.py": frozenset({"SSH"}),
        "src/rotation_worker/adapters/bws_machine_token.py": frozenset({"self._interpreter"}),
    },
    "rotation_proposer": {
        **_MIRROR,
        **_STORE_MODULE_ONLY,
        "src/rotation_proposer/findings.py": frozenset({"str(node_binary())"}),
        "src/bump_proposer/standing.py": frozenset({"git", "str(python)", "PUBLISH_COMMAND"}),
    },
    "rotation_decomposer": {**_MIRROR, **_STORE_MODULE_ONLY},
    "rotation_verifier": {},
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
    "rotation_verifier": {
        "src/rotation_verifier/client.py": 1,
        "src/rotation_verifier/required_checks.py": 1,
    },
}

# Everything `rotation_proposer` takes from `bump_proposer`, by module. A name added here is a
# surface shared with another lane.
BORROWED: dict[str, frozenset[str]] = {
    "bump_proposer.change_manager": frozenset(
        {"DEFAULT_BASE_URL", "ChangeManagerClient", "ChangeManagerError", "ProposalRefused"}
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
}

LAUNCHERS: dict[str, str] = {
    "rotation_proposer": "scripts/run-rotation-proposer.sh",
    "rotation_decomposer": "scripts/run-rotation-decomposer.sh",
    "rotation_verifier": "scripts/run-rotation-verifier.sh",
}

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
        f"{UNIT_PATH}/verifier-evidence/named-check",
        f"{UNIT_PATH}/verify",
        "/v1/alerts",
        "/app/installations/44/access_tokens",
        "/credentials/revoke",
        "/repos/o/r/actions/jobs/44/logs",
        "/repos/o/r/actions/runs/44/jobs",
        "/repos/o/r/actions/secrets/S",
        "/repos/o/r/actions/secrets/public-key",
        "/repos/o/r/actions/workflows/secret-fingerprint.yml/runs",
        "/repos/o/r/branches/main/protection/required_status_checks",
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
    UNIT_PATH.upper(),
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
    return near_misses(ROUTES) | PROBES | {f"{path}\n" for path in ROUTES}


def disagreements(lane: str) -> frozenset[tuple[str, str, str, bool]]:
    return route_disagreements(EDGES[lane], PERMITS[lane], _corpus(), METHODS)


def reach_disagreements(reach: Reach) -> frozenset[tuple[str, str, bool]]:
    """(method, path, accepted) for each corpus route where the clients and the row differ, and
    ("base_url", url, built) for a host one has that the other hasn't."""
    permits = either(*(client.permits for client in reach.built))
    found = {
        (method, path, permits(method, path))
        for method in METHODS
        for path in _corpus()
        if permits(method, path) != ((method, path) in reach.routes or method in reach.any_path)
    }
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


def test_the_platform_table_has_a_row_for_each_coolify_resource() -> None:
    assert {"coolify_application", "coolify_service"} <= set(PLATFORM_EDGES)


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


# The population rules read source through `Read`, so a control can widen a module without touching
# the file.
Read = Callable[[Path], str]
SRC: Final = Path("src")
SHARED_TRANSPORTS: Final = frozenset(
    {"src/estate_clients/confined.py", "src/alert_hub_client/__init__.py"}
)
NETWORK_IMPORTS: Final = frozenset(
    {"aiohttp", "alert_hub_client", "estate_clients", "http", "httpx", "requests", "socket"}
)
OS_STARTERS: Final = re.compile(r"os\.(system|popen|spawn\w*|exec\w*|posix_spawn\w*|fork\w*)")
RUNNER: Final = re.compile(r"_?run(ner)?")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imported(tree: ast.AST) -> set[str]:
    """Every dotted name an import statement names, including a `from` import's names."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{alias.name}" for alias in node.names}
    return names


def _source_of(module: str) -> Path | None:
    path = SRC.joinpath(*module.split("."))
    for candidate in (path.with_suffix(".py"), path / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def closure(lane: str, read: Read = _text) -> frozenset[Path]:
    """The lane's own modules and every first-party module they import, transitively."""
    seen: set[Path] = set()
    waiting = sorted((SRC / lane).rglob("*.py"))
    while waiting:
        path = waiting.pop()
        if path in seen:
            continue
        seen.add(path)
        for name in _imported(ast.parse(read(path))):
            parts = name.split(".")
            for end in range(1, len(parts) + 1):
                source = _source_of(".".join(parts[:end]))
                if source is not None:
                    waiting.append(source)
    return frozenset(seen)


def _talks(tree: ast.AST) -> bool:
    imported = _imported(tree)
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


def network(lane: str, read: Read = _text) -> dict[str, int]:
    """The lane's modules holding a network client, each with how many it builds."""
    found: dict[str, int] = {}
    for path in closure(lane, read):
        tree = ast.parse(read(path))
        if path.as_posix() not in SHARED_TRANSPORTS and _talks(tree):
            found[path.as_posix()] = _clients_built(tree)
    return found


def _head(
    argument: ast.expr,
    parameters: set[str],
    local: Mapping[str, ast.expr],
    functions: Mapping[str, ast.FunctionDef],
) -> frozenset[str]:
    """What an argument of a `run`-shaped call starts, as the code spells the program."""

    def follow(value: ast.expr) -> frozenset[str]:
        return _head(value, parameters, local, functions)

    match argument:
        case ast.List(elts=[ast.Constant(value=str(program)), *_]):
            return frozenset({program})
        case ast.List(elts=[first, *_]):
            return frozenset({ast.unparse(first)})
        case ast.Name(id=name) if name in parameters:
            return frozenset()  # forwarded: its caller's argument is the one that counts
        case ast.Name(id=name) if name in local:
            return follow(local[name])
        case ast.Call(func=ast.Name(id="list"), args=[inner]):
            return follow(inner)
        case ast.Call(func=ast.Attribute(attr="split", value=spelled)):
            return frozenset({ast.unparse(spelled)})
        case ast.Call(func=ast.Name(id=name)) if name in functions:
            returned = (node for node in ast.walk(functions[name]) if isinstance(node, ast.Return))
            return frozenset().union(*(follow(node.value) for node in returned if node.value))
        case _:
            return frozenset({ast.unparse(argument)})


def process_heads(text: str) -> frozenset[str]:
    """Every program the module can start: in a module importing `subprocess`, each `run`-shaped
    call's argument list; any other process-starting call by its name; and `bitwarden_sdk` if the
    module imports it. A module importing `subprocess` and starting nothing is `subprocess`."""
    tree = ast.parse(text)
    imported = {name.split(".")[0] for name in _imported(tree)}
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    heads: set[str] = set()
    for function in functions.values():
        parameters = {
            argument.arg
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
        }
        local = {
            target.id: node.value
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            spelled = ast.unparse(node.func)
            name = spelled.rsplit(".", 1)[-1]
            if OS_STARTERS.fullmatch(spelled) or (
                spelled.startswith("subprocess.") and name != "run"
            ):
                heads.add(spelled)
            elif "subprocess" in imported and RUNNER.fullmatch(name):
                for argument in node.args:
                    heads |= _head(argument, parameters, local, functions)
    if "subprocess" in imported and not heads:
        heads.add("subprocess")
    if "bitwarden_sdk" in imported:
        heads.add("bitwarden_sdk")
    return frozenset(heads)


def processes(lane: str, read: Read = _text) -> dict[str, frozenset[str]]:
    found = {path.as_posix(): process_heads(read(path)) for path in closure(lane, read)}
    return {path: heads for path, heads in found.items() if heads}


def borrowed(lane: str = "rotation_proposer", read: Read = _text) -> dict[str, frozenset[str]]:
    """Every name the lane takes from `bump_proposer`: what it imports from a module of it, and
    each attribute it reads on a module of it imported whole."""
    found: dict[str, set[str]] = {}
    for path in sorted((SRC / lane).rglob("*.py")):
        tree = ast.parse(read(path))
        whole: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "bump_proposer":
                for alias in node.names:
                    whole[alias.asname or alias.name] = f"bump_proposer.{alias.name}"
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("bump_proposer.")
            ):
                found.setdefault(node.module, set()).update(alias.name for alias in node.names)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in whole
            ):
                found.setdefault(whole[node.value.id], set()).add(node.attr)
    return {module: frozenset(names) for module, names in found.items()}


def spelled_pushes(text: str) -> frozenset[str]:
    """Each string in the module's code that would make `git` publish. Docstrings don't count."""
    tree = ast.parse(text)
    prose = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
    }
    return frozenset(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
        and (node.value == "push" or node.value.startswith("git push"))
    )


def identity_fields(names: tuple[str, ...]) -> frozenset[str]:
    return frozenset(name for name in names if re.search(r"token|secret|bearer|key_id", name))


def child_imports(script: str) -> frozenset[str]:
    return frozenset(name.split(".")[0] for name in _imported(ast.parse(script)))


@pytest.mark.parametrize("lane", sorted(EDGES))
def test_each_lane_runs_exactly_its_processes(lane: str) -> None:
    assert processes(lane) == PROCESSES[lane]


def test_each_named_program_is_the_one_it_says() -> None:
    assert secret_store.SECURITY == "/usr/bin/security"
    assert helper_client.SSH == "/usr/bin/ssh"
    assert bump_standing.PUBLISH_COMMAND == "git push origin main"
    assert {path.name for path in findings.NODE_FALLBACKS} == {"node"}


def test_only_the_publishing_command_pushes() -> None:
    pushing = {
        path.as_posix(): spelled_pushes(_text(path))
        for lane in EDGES
        for path in closure(lane)
        if spelled_pushes(_text(path))
    }
    assert pushing == {"src/bump_proposer/standing.py": frozenset({bump_standing.PUBLISH_COMMAND})}


def test_each_child_login_imports_only_the_named_sdk() -> None:
    scripts = {
        name: value
        for name, value in vars(bws_machine_token).items()
        if isinstance(value, str) and "import " in value and name != "__doc__"
    }
    assert bws_machine_token.CHILD_SDK == "bitwarden_sdk"
    assert {name: child_imports(script) for name, script in scripts.items()} == {
        "_LOGIN": {"bitwarden_sdk", "sys"},
        "_REACH": {"bitwarden_sdk", "hashlib", "sys"},
    }


@pytest.mark.parametrize("lane", sorted(LAUNCHERS))
def test_each_launcher_fetches_exactly_its_bearers(lane: str) -> None:
    text = _text(Path(LAUNCHERS[lane]))
    assert launcher_bearers(text) == EDGES[lane].bearers


def test_the_worker_names_its_identities_in_config() -> None:
    fields = tuple(field.name for field in dataclasses.fields(Config))
    assert identity_fields(fields) == EDGES["rotation_worker"].bearers


def test_the_proposer_uses_only_the_named_part_of_bump_proposer() -> None:
    assert borrowed() == BORROWED


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
        and "alert_hub_client" in {name.split(".")[0] for name in _imported(ast.parse(_text(path)))}
    }
    assert bool(importing) == (EDGES[lane].alert_hub == ALERTS)
    assert EDGES[lane].alert_hub in (ALERTS, frozenset())
    assert alert_hub_client.ALERTS == "/v1/alerts"


def _widened(path: str, addition: str) -> Read:
    """A reader of the tree in which one module carries `addition`."""

    def read(asked: Path) -> str:
        text = _text(asked)
        return f"{text}\n{addition}\n" if asked.as_posix() == path else text

    return read


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


@pytest.mark.parametrize(
    ("addition", "started"),
    [
        ('import subprocess\ndef probe():\n    subprocess.run(["curl", "x"])', "curl"),
        ('import subprocess\ndef probe():\n    subprocess.Popen(["curl"])', "subprocess.Popen"),
        ('def probe():\n    os.system("curl x")', "os.system"),
        ('import subprocess\ndef probe():\n    c = ["curl"]\n    _run(c)', "curl"),
        ("import subprocess", "subprocess"),
        ("import bitwarden_sdk", "bitwarden_sdk"),
    ],
)
def test_control_a_new_process_fails_the_table(addition: str, started: str) -> None:
    module = "src/rotation_verifier/client.py"
    found = processes("rotation_verifier", _widened(module, addition))
    assert started in found[module]
    assert found != PROCESSES["rotation_verifier"]


def test_control_a_push_spelled_in_a_string_is_seen() -> None:
    assert spelled_pushes('COMMAND = ["git", "push"]') == {"push"}
    assert spelled_pushes('"""git push origin main is prose."""') == frozenset()


def test_control_a_new_child_import_is_seen() -> None:
    assert "requests" in child_imports("import requests\nimport sys")


def test_control_a_new_launcher_bearer_fails_the_table() -> None:
    text = _text(Path(LAUNCHERS["rotation_decomposer"]))
    assert launcher_bearers(f"{text}\n# {VERIFIER_BEARER}") != EDGES["rotation_decomposer"].bearers


def test_control_a_new_config_identity_fails_the_table() -> None:
    fields = tuple(field.name for field in dataclasses.fields(Config))
    widened = identity_fields((*fields, "coolify_token_id"))
    assert widened != EDGES["rotation_worker"].bearers


def test_control_a_wider_borrowing_fails_the_table() -> None:
    addition = "def more():\n    shared.advance(None, None, None)"
    found = borrowed(read=_widened("src/rotation_proposer/standing.py", addition))
    assert "advance" in found["bump_proposer.standing"]
    assert found != BORROWED


def test_control_a_new_http_client_module_fails_the_table() -> None:
    module = "src/rotation_worker/grants.py"
    found = network("rotation_worker", _widened(module, "import httpx"))
    assert found[module] == 0
    assert found != NETWORK["rotation_worker"]


def test_control_a_second_client_in_a_module_fails_the_table() -> None:
    module = "src/rotation_worker/readers.py"
    addition = "def wide():\n    return ConfinedClient(base_url='https://x', permits=None)"
    found = network("rotation_worker", _widened(module, addition))
    assert found[module] == _WORKER_CLIENTS[module] + 1
