"""Architecture guard: one table states each lander's whole outbound surface.

The landing layer's two programs pin their own routes in their own tests. This table states them in
one place, per service, in the bridge table's shape (`test_bridge_edges.Edge`): the change-manager
and orchestrator routes each lander may send, the GitHub routes it may read, the alert-hub routes
it may send, and the bearers its launcher fetches. Every cell is checked against the lander's OWN
predicates -- the functions its `ConfinedClient` asks before a request is built -- under every
verb, over one corpus: each listed route, its trailing-slash and suffixed near misses, and named
probes for the routes a widening would most plausibly reach.

`inert_lander` lists open pull requests through `deploy_watcher.github`, whose predicate bounds the
verb (any GET) and not the route, so it cannot be a column: it is pinned by naming the import and
the one method `PullRequestSource` declares. Both landers alert through `lander.held_alerts`.
"""

from __future__ import annotations

import ast
import re
import typing
from collections.abc import Iterator
from pathlib import Path

import pytest

from estate_lander import change_manager as estate_change_manager
from estate_lander import orchestrator_client as estate_orchestrator
from inert_lander import orchestrator_client as inert_orchestrator
from inert_lander.cli import PullRequestSource
from scripts.layers import load
from tests.architecture.test_bridge_edges import (
    READ_BEARER,
    SDS_BRIDGE_ALERT_BEARER,
    SYSTEM_BEARER,
    Edge,
    Permits,
    _alert_hub_permits,
    _landing_policy_permits,
)

# The inert lander's GitHub token is not a BWS secret: its launcher takes it from the gh CLI.
GH_AUTH_TOKEN = "gh auth token"

_HELD_CLOSE = ("POST", "/api/v1/held-pr-close")
_ALERTS = frozenset({("POST", "/v1/alerts")})

EDGES: dict[str, Edge] = {
    "estate_lander": Edge(
        change_manager=frozenset({("GET", "/api/items")}),
        orchestrator=frozenset(
            {
                ("GET", "/api/v1/estate-pr-merge-admission"),
                ("POST", "/api/v1/estate-pr-merge"),
                ("POST", "/api/v1/estate-pr-branch-update"),
                _HELD_CLOSE,
            }
        ),
        github=frozenset(),
        alert_hub=_ALERTS,  # through lander.held_alerts
        bearers=frozenset({SYSTEM_BEARER, READ_BEARER, SDS_BRIDGE_ALERT_BEARER}),
    ),
    "inert_lander": Edge(
        change_manager=frozenset({("GET", "/api/landing-policy")}),
        orchestrator=frozenset(
            {
                ("GET", "/api/v1/inert-pr-merge-admission"),
                ("POST", "/api/v1/inert-pr-merge"),
                ("POST", "/api/v1/inert-pr-branch-update"),
                _HELD_CLOSE,
                # Landing Part 3a: the watch, and the act on a landing that went bad.
                ("GET", "/api/v1/landing-watch"),
                ("POST", "/api/v1/landing-revert"),
            }
        ),
        github=frozenset(),  # through deploy_watcher.github: GITHUB_THROUGH
        alert_hub=_ALERTS,  # through lander.held_alerts
        bearers=frozenset({SYSTEM_BEARER, READ_BEARER, SDS_BRIDGE_ALERT_BEARER, GH_AUTH_TOKEN}),
    ),
}

GITHUB_THROUGH: dict[str, tuple[str, str]] = {
    "inert_lander": ("src/inert_lander/cli.py", "deploy_watcher.github"),
}

LAUNCHERS: dict[str, str] = {
    "estate_lander": "scripts/run-estate-landing.sh",
    "inert_lander": "scripts/run-inert-landing.sh",
}

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

PROBES = frozenset(
    {
        "/api/v1/work-units/44/pr-merge",
        "/api/v1/work-units/44/dispatch",
        "/api/v1/observations",
        "/api/items/44/approve",
        "/api/deploy-changes",
    }
)

# Each adapter looks its predicate up through the module at call time, so a monkeypatch of the
# module's function reaches it. `None` is a service the lander has no client for.
PERMITS: dict[str, dict[str, Permits | None]] = {
    "estate_lander": {
        "change_manager": lambda m, p: estate_change_manager._permits(m, p),
        "orchestrator": lambda m, p: estate_orchestrator._permits(m, p),
        "github": None,
        "alert_hub": _alert_hub_permits,
    },
    "inert_lander": {
        "change_manager": _landing_policy_permits,
        "orchestrator": lambda m, p: inert_orchestrator._permits(m, p),
        "github": None,
        "alert_hub": _alert_hub_permits,
    },
}


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
def test_each_lander_accepts_exactly_its_routes_among_the_corpus(lane: str) -> None:
    assert sorted(disagreements(lane)) == []


def _imports(source: str) -> set[str]:
    tree = ast.parse(Path(source).read_text(encoding="utf-8"))
    return {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }


@pytest.mark.parametrize("lane", sorted(GITHUB_THROUGH))
def test_a_lander_without_its_own_github_reader_reads_through_the_named_one(lane: str) -> None:
    source, reader = GITHUB_THROUGH[lane]
    assert EDGES[lane].github == frozenset()
    assert reader in _imports(source)


def test_the_inert_landers_github_need_is_the_one_listing() -> None:
    """The reader permits any GET, so what bounds this lane's GitHub reads is the one method the
    protocol it depends on declares."""
    assert typing.get_protocol_members(PullRequestSource) == frozenset({"open_pull_requests"})


@pytest.mark.parametrize("lane", sorted(EDGES))
def test_each_lander_alerts_through_the_shared_held_alerts(lane: str) -> None:
    assert "lander.held_alerts" in _imports(f"src/{lane}/cli.py")
    assert "alert_hub_client" in _imports("src/lander/held_alerts.py")


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


@pytest.mark.parametrize("lane", sorted(LAUNCHERS))
def test_each_launcher_fetches_exactly_its_bearers(lane: str) -> None:
    text = Path(LAUNCHERS[lane]).read_text(encoding="utf-8")
    named = {uuid.lower() for uuid in _UUID.findall(text)}
    if GH_AUTH_TOKEN in text:
        named.add(GH_AUTH_TOKEN)
    assert named == EDGES[lane].bearers


def _lander_programs() -> Iterator[str]:
    # `src/lander/**` is the body the two programs share, not a program: it has no launcher and
    # no client of its own.
    for pattern in load()["landing"].paths:
        match = re.fullmatch(r"src/([a-z_]+_lander)/\*\*", pattern)
        if match:
            yield match.group(1)


def test_the_table_is_the_landing_layer_population() -> None:
    assert sorted(EDGES) == sorted(_lander_programs()) == sorted(LAUNCHERS)


def test_control_a_widened_predicate_fails_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison is not vacuous: widen one write predicate and the table reports it."""
    original = estate_orchestrator.is_allowed_write
    monkeypatch.setattr(
        estate_orchestrator,
        "is_allowed_write",
        lambda path: path == "/api/v1/work-units/44/pr-merge" or original(path),
    )
    assert ("orchestrator", "POST", "/api/v1/work-units/44/pr-merge", True) in disagreements(
        "estate_lander"
    )
