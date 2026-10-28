"""Architecture guard: one table states every bridge lane's whole outbound surface.

The bridge layer's four programs each pin their own routes in their own tests. This table states
them in one place, per service: the change-manager and orchestrator routes each lane may send, the
GitHub routes it may read, the alert-hub routes it may send, and the BWS secrets its launcher
fetches. Every cell is checked against
the lane's OWN predicates -- the functions its `ConfinedClient` asks before a request is built --
under every verb, over one corpus tried against every service column: each listed route, its
trailing-slash and suffixed near misses, and named probes per service (change-manager lifecycle
routes, orchestrator write routes no bridge lane holds, GitHub merge, listing and other-file
routes). A predicate that widens to a route in that corpus, or a row that drifts from the code,
fails here; a widening to a route outside it does not.

A lane whose GitHub reads go through another package's reader (`bump_proposer` through
`landing_ledger.github`, `change_proposer` through `deploy_watcher.github`) is pinned by naming that
import; the reader's own surface is that package's test's business.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import pytest

import alert_hub_client
from bump_proposer import change_manager as bump_change_manager
from bump_proposer import landing_policy as bump_landing_policy
from bump_proposer import orchestrator_client as bump_orchestrator
from change_proposer import change_manager as change_change_manager
from estate_clients.confined import TransportFailure
from scripts.layers import load
from sds_members import client as members_client
from tests.architecture.test_external_content_observes_only import CLASSIFICATION
from work_carrier import change_manager as carrier_change_manager
from work_carrier import orchestrator_client as carrier_orchestrator
from work_watcher import change_manager as watcher_change_manager
from work_watcher import github as watcher_github
from work_watcher import orchestrator_client as watcher_orchestrator

Route = tuple[str, str]
Permits = Callable[[str, str], bool]

PROPOSE_BEARER = "acccb346-4baa-43ec-a1d4-b4a400c048ee"
READ_BEARER = "314f276d-55ca-4ddc-a24d-b4a3013508cd"
OBSERVER_BEARER = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
SYSTEM_BEARER = "221a48d5-3f29-4898-b300-b4820140c880"
# The `sds-bridge` alert sender token (bridge Part 3). A placeholder UUID until the ship step
# replaces it in the launcher and here.
SDS_BRIDGE_ALERT_BEARER = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True)
class Edge:
    change_manager: frozenset[Route]
    orchestrator: frozenset[Route]
    github: frozenset[Route]
    alert_hub: frozenset[Route]
    bearers: frozenset[str]


EDGES: dict[str, Edge] = {
    "bump_proposer": Edge(
        change_manager=frozenset(
            {
                ("POST", "/api/work-changes"),
                ("GET", "/api/items"),
                ("GET", "/api/landing-policy"),
            }
        ),
        orchestrator=frozenset({("POST", "/api/v1/observations"), ("GET", "/api/v1/members")}),
        github=frozenset(),  # through landing_ledger.github: GITHUB_THROUGH
        # through alert_hub_client: ALERT_HUB_THROUGH
        alert_hub=frozenset({("POST", "/v1/alerts")}),
        bearers=frozenset({PROPOSE_BEARER, READ_BEARER, OBSERVER_BEARER, SDS_BRIDGE_ALERT_BEARER}),
    ),
    "change_proposer": Edge(
        change_manager=frozenset(
            {
                ("POST", "/api/deploy-changes"),
                ("POST", "/api/items/44/deploy-retirement"),
                ("GET", "/api/items"),
            }
        ),
        orchestrator=frozenset({("GET", "/api/v1/members")}),
        github=frozenset(),  # through deploy_watcher.github: GITHUB_THROUGH
        alert_hub=frozenset(),
        bearers=frozenset({PROPOSE_BEARER, OBSERVER_BEARER}),
    ),
    "work_carrier": Edge(
        change_manager=frozenset({("GET", "/api/items")}),
        orchestrator=frozenset(
            {
                ("POST", "/api/v1/package-intakes"),
                ("GET", "/api/v1/change-records/44/work"),
                ("GET", "/api/v1/members"),
            }
        ),
        github=frozenset(),
        alert_hub=frozenset(),
        bearers=frozenset({READ_BEARER, SYSTEM_BEARER, OBSERVER_BEARER}),
    ),
    "work_watcher": Edge(
        # The listing is work_carrier's reader, which work_watcher/pipeline.py imports.
        change_manager=frozenset(
            {("POST", "/api/items/44/work-retirement"), ("GET", "/api/items")}
        ),
        orchestrator=frozenset({("GET", "/api/v1/change-records/44/work")}),
        github=frozenset({("GET", "/repos/o/r/pulls/44")}),
        alert_hub=frozenset(),
        bearers=frozenset({PROPOSE_BEARER, READ_BEARER, SYSTEM_BEARER}),
    ),
}

# A lane reading GitHub through another package's reader: (its module, the reader it imports).
GITHUB_THROUGH: dict[str, tuple[str, str]] = {
    "bump_proposer": ("src/bump_proposer/cli.py", "landing_ledger.github"),
    "change_proposer": ("src/change_proposer/cli.py", "deploy_watcher.github"),
}

# A lane sending alerts through the shared client: (its module, the client it imports).
ALERT_HUB_THROUGH: dict[str, tuple[str, str]] = {
    "bump_proposer": ("src/bump_proposer/alerts.py", "alert_hub_client"),
}

LAUNCHERS: dict[str, tuple[str, ...]] = {
    "scripts/run-bump-proposer.sh": ("bump_proposer",),
    "scripts/run-change-proposer.sh": ("change_proposer",),
    "scripts/run-work-carrier.sh": ("work_carrier", "work_watcher"),
}

# Every verb, not only the two the table uses: a predicate that admits a DELETE on a write route is
# wider than its row.
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# Named probes, per service, for the routes a widening would most plausibly reach. The corpus is
# their union, and every column is tried against all of it.
CHANGE_MANAGER_PROBES = frozenset(
    {
        "/api/items/44/approve",
        "/api/items/44/claim",
        "/api/items/44/resolve",
        "/api/items/44/reactivate",
        "/api/items/44/outcome",
        "/api/items/44/handoff",
        "/api/items/44/deploy-observation",
        "/api/sync",
        "/api/deploy-policy",
        "/api/items/",
        "/api/items/44",
        "/api/items/../44/work-retirement",
    }
)
ORCHESTRATOR_PROBES = frozenset(
    {
        "/api/v1/work-units/44/pr-merge",
        "/api/v1/work-units/44/claim",
        "/api/v1/work-units/44/dispatch",
        "/api/v1/approvals",
        "/api/v1/observations",
        "/api/v1/package-intakes/44",
        "/api/v1/change-records/44/work/x",
        "/api/v1/members/44",
        "/api/v1/member-proposals",
    }
)
GITHUB_PROBES = frozenset(
    {
        "/repos/o/r/pulls/44/merge",
        "/repos/o/r/contents/other.toml",
        "/repos/o/r/pulls",
    }
)
ALERT_HUB_PROBES = frozenset(
    {
        "/v1/alerts/44",
        "/v1/alerts/44/ack",
        "/v1/sources",
        "/v1/channels",
    }
)
PROBES = CHANGE_MANAGER_PROBES | ORCHESTRATOR_PROBES | GITHUB_PROBES | ALERT_HUB_PROBES


def _listed_paths() -> frozenset[str]:
    return frozenset(
        path
        for edge in EDGES.values()
        for column in (edge.change_manager, edge.orchestrator, edge.github, edge.alert_hub)
        for _, path in column
    )


def _corpus() -> frozenset[str]:
    listed = _listed_paths()
    return listed | PROBES | {f"{path}/" for path in listed} | {f"{path}x" for path in listed}


def _captured_permits(
    module: object, call: Callable[[], object], refusal: type[Exception]
) -> Permits:
    """The `permits` a module passes its `ConfinedClient` inline, taken from the constructor call.

    Two readers build their predicate as a lambda at the call site rather than naming it. The
    recorder stands in for `ConfinedClient` on that module only, keeps the lambda, and refuses
    construction, so nothing is sent. The lambda reads the module's globals when it runs, so a
    later monkeypatch of the module still reaches it.
    """
    captured: list[Permits] = []

    def recorder(*, permits: Permits, **_: object) -> object:
        captured.append(permits)
        raise TransportFailure("captured")

    with mock.patch.object(module, "ConfinedClient", recorder):
        with pytest.raises(refusal):
            call()
    assert len(captured) == 1, f"{module!r} built no ConfinedClient"
    return captured[0]


_landing_policy_permits = _captured_permits(
    bump_landing_policy,
    lambda: bump_landing_policy.read_inert_landing("token"),
    bump_landing_policy.LandingPolicyError,
)
_alert_hub_permits = _captured_permits(
    alert_hub_client,
    lambda: alert_hub_client.KeyedAlerts(
        lambda: "token", Path("unused.json"), user_agent="edges/1"
    ).open_client(),
    alert_hub_client.AlertHubError,
)
_listing_permits = _captured_permits(
    carrier_change_manager,
    lambda: carrier_change_manager.HttpWorkRecordSource(
        base_url="https://change-mgr.example", token="token"
    ).approved_work(),
    carrier_change_manager.ChangeManagerError,
)


def _either(*predicates: Permits) -> Permits:
    return lambda method, path: any(predicate(method, path) for predicate in predicates)


# Each adapter looks its predicate up through the module at call time, so a monkeypatch of the
# module's function reaches it. `None` is a service the lane has no client for: it permits nothing.
PERMITS: dict[str, dict[str, Permits | None]] = {
    "bump_proposer": {
        "change_manager": _either(
            lambda m, p: bump_change_manager._permits(m, p),
            _landing_policy_permits,
        ),
        # The lane's own observation client and the member-record reader it imports.
        "orchestrator": _either(
            lambda m, p: bump_orchestrator._permits(m, p),
            lambda m, p: members_client._permits(members_client._READ)(m, p),
        ),
        "github": None,
        "alert_hub": _alert_hub_permits,
    },
    "change_proposer": {
        "change_manager": lambda m, p: change_change_manager._permits(m, p),
        # No client of its own: the member-record reader it imports.
        "orchestrator": lambda m, p: members_client._permits(members_client._READ)(m, p),
        "github": None,
        "alert_hub": None,
    },
    "work_carrier": {
        "change_manager": _listing_permits,
        # The carrier's own client and the member-record reader it imports: two clients, one lane.
        "orchestrator": _either(
            lambda m, p: carrier_orchestrator._permits(m, p),
            lambda m, p: members_client._permits(members_client._READ)(m, p),
        ),
        "github": None,
        "alert_hub": None,
    },
    "work_watcher": {
        "change_manager": _either(
            lambda m, p: watcher_change_manager._permits(m, p),
            _listing_permits,
        ),
        "orchestrator": lambda m, p: watcher_orchestrator._permits(m, p),
        "github": lambda m, p: watcher_github._permits(m, p),
        "alert_hub": None,
    },
}


def disagreements(lane: str) -> frozenset[tuple[str, str, str, bool]]:
    """(service, method, path, accepted) for each corpus route where the code and the row differ."""
    edge = EDGES[lane]
    rows = {
        "change_manager": edge.change_manager,
        "orchestrator": edge.orchestrator,
        "github": edge.github,
        "alert_hub": edge.alert_hub,
    }
    found: set[tuple[str, str, str, bool]] = set()
    for service, listed in rows.items():
        permits = PERMITS[lane][service]
        for method in METHODS:
            for path in _corpus():
                accepted = permits is not None and permits(method, path)
                if accepted != ((method, path) in listed):
                    found.add((service, method, path, accepted))
    return frozenset(found)


@pytest.mark.parametrize("lane", sorted(EDGES))
def test_each_lane_accepts_exactly_its_routes_among_the_corpus(lane: str) -> None:
    assert sorted(disagreements(lane)) == []


def _imports(source: str) -> set[str]:
    tree = ast.parse(Path(source).read_text(encoding="utf-8"))
    return {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }


@pytest.mark.parametrize("lane", sorted(GITHUB_THROUGH))
def test_a_lane_without_its_own_github_reader_reads_through_the_named_one(lane: str) -> None:
    source, reader = GITHUB_THROUGH[lane]
    assert EDGES[lane].github == frozenset()
    assert reader in _imports(source)


@pytest.mark.parametrize("lane", sorted(ALERT_HUB_THROUGH))
def test_a_lane_that_alerts_does_so_through_the_shared_client(lane: str) -> None:
    source, client = ALERT_HUB_THROUGH[lane]
    assert client in {name.split(".")[0] for name in _imports(source)}
    assert EDGES[lane].alert_hub == frozenset({("POST", alert_hub_client.ALERTS)})


def test_only_the_named_modules_import_the_alert_client() -> None:
    """A lane module importing `alert_hub_client` outside `ALERT_HUB_THROUGH` would send alerts
    the table doesn't state."""
    importers = {
        str(path)
        for lane in EDGES
        for path in sorted(Path("src", lane).rglob("*.py"))
        if "alert_hub_client" in {name.split(".")[0] for name in _imports(str(path))}
    }
    assert importers == {source for source, _ in ALERT_HUB_THROUGH.values()}


def test_work_watcher_lists_records_through_the_carriers_reader() -> None:
    assert "work_carrier.change_manager" in _imports("src/work_watcher/pipeline.py")


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


@pytest.mark.parametrize("launcher", sorted(LAUNCHERS))
def test_each_launcher_fetches_exactly_its_bearers(launcher: str) -> None:
    # Launcher-level: the union of its lanes' bearers, so a bearer handed to the wrong lane of a
    # shared launcher (work_carrier and work_watcher in run-work-carrier.sh) still passes.
    named = {uuid.lower() for uuid in _UUID.findall(Path(launcher).read_text(encoding="utf-8"))}
    bearers = set().union(*(EDGES[lane].bearers for lane in LAUNCHERS[launcher]))
    assert named == bearers


def test_every_lane_has_a_launcher() -> None:
    assert sorted(lane for lanes in LAUNCHERS.values() for lane in lanes) == sorted(EDGES)


@pytest.mark.parametrize("lane", sorted(EDGES))
def test_the_orchestrator_column_agrees_with_the_observe_only_classification(lane: str) -> None:
    writes = frozenset(path for method, path in EDGES[lane].orchestrator if method != "GET")
    assert writes == CLASSIFICATION[lane].writes


def _bridge_programs() -> Iterator[str]:
    for pattern in load()["bridge"].paths:
        match = re.fullmatch(r"src/([a-z_]+)/\*\*", pattern)
        if match:
            yield match.group(1)


def test_the_table_is_the_bridge_layer_population() -> None:
    assert sorted(EDGES) == sorted(_bridge_programs())


def test_control_a_widened_predicate_fails_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison is not vacuous: widen one write predicate and the table reports it."""
    original = watcher_change_manager.is_allowed_write
    monkeypatch.setattr(
        watcher_change_manager,
        "is_allowed_write",
        lambda path: path == "/api/items/44/approve" or original(path),
    )
    assert ("change_manager", "POST", "/api/items/44/approve", True) in disagreements(
        "work_watcher"
    )


def test_control_a_widened_orchestrator_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The orchestrator column is compared too: widen the carry's write predicate and it fails."""
    original = carrier_orchestrator.is_allowed_write
    monkeypatch.setattr(
        carrier_orchestrator,
        "is_allowed_write",
        lambda path: path == "/api/v1/work-units/44/pr-merge" or original(path),
    )
    assert ("orchestrator", "POST", "/api/v1/work-units/44/pr-merge", True) in disagreements(
        "work_carrier"
    )


def test_control_a_widened_alert_hub_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The alert-hub column is compared too: move the client's one route and the table fails.

    The captured predicate reads `ALERTS` from its module when it runs, so this changes the code
    under test, not the table."""
    monkeypatch.setattr(alert_hub_client, "ALERTS", "/v1/sources")
    found = disagreements("bump_proposer")
    assert ("alert_hub", "POST", "/v1/sources", True) in found
    assert ("alert_hub", "POST", "/v1/alerts", False) in found


def test_control_a_widened_github_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The GitHub column is compared too: widen the watcher's reader and it reports it."""
    original = watcher_github.is_allowed_read
    monkeypatch.setattr(
        watcher_github,
        "is_allowed_read",
        lambda path: path == "/repos/o/r/contents/other.toml" or original(path),
    )
    assert ("github", "GET", "/repos/o/r/contents/other.toml", True) in disagreements(
        "work_watcher"
    )
