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
from estate_lander import image_repoint
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

# The landers' GitHub token is not a BWS secret: each launcher takes it from the gh CLI.
GH_AUTH_TOKEN = "gh auth token"
# Landing Part 3b. The estate lander's Coolify token (BWS `COOLIFY_TOKEN_SDS_ESTATE_LANDER`).
COOLIFY_TOKEN = "f9f59233-0794-4ea8-9806-b4f3011e6147"

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
                # Landing Part 3b: the watch, and the three acts on one of this lane's landings.
                ("GET", "/api/v1/landing-watch"),
                ("POST", "/api/v1/landing-revert"),
                ("POST", "/api/v1/landing-repoint"),
                ("POST", "/api/v1/landing-pinned"),
                ("POST", "/api/v1/landing-restored"),
            }
        ),
        github=frozenset(),  # through deploy_watcher.github: GITHUB_THROUGH
        alert_hub=_ALERTS,  # through lander.held_alerts
        bearers=frozenset(
            {SYSTEM_BEARER, READ_BEARER, SDS_BRIDGE_ALERT_BEARER, GH_AUTH_TOKEN, COOLIFY_TOKEN}
        ),
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
    "estate_lander": ("src/estate_lander/cli.py", "deploy_watcher.github"),
}

# Landing Part 3b. The estate lander's two clients of services outside the estate's own: the
# hosting platform, confined to one repository's applications (shown here with `u1` allowed), and
# the registry. Each cell is checked against the client's own predicate over a corpus.
PLATFORM_ALLOWED_UUID = "u1"
PLATFORM_EDGES: dict[str, tuple[Permits, frozenset[tuple[str, str]]]] = {
    "coolify": (
        lambda m, p: image_repoint._permits(frozenset({PLATFORM_ALLOWED_UUID}), m, p),
        frozenset(
            {
                ("GET", "/api/v1/applications"),
                ("GET", "/api/v1/applications/u1"),
                ("PATCH", "/api/v1/applications/u1"),
                ("POST", "/api/v1/applications/u1/start"),
            }
        ),
    ),
    "ghcr": (
        lambda m, p: image_repoint._registry_permits(m, p),
        frozenset(
            {
                ("GET", "/token"),
                ("HEAD", "/v2/alobarquest/brain/manifests/latest"),
            }
        ),
    ),
}
# The registry's one read names a tag, so its row is a pattern rather than a path: any tag of one
# of this estate's images. The corpus's suffixed near miss of a listed tag is another tag.
_MANIFEST = re.compile(
    r"/v2/alobarquest/[a-z0-9][a-z0-9._-]*/manifests/[A-Za-z0-9_][A-Za-z0-9_.-]*"
)
PLATFORM_PROBES = frozenset(
    {
        "/api/v1/applications/u2",
        "/api/v1/applications/u1/restart",
        "/api/v1/applications/u1/stop",
        "/api/v1/applications/u1/envs",
        "/api/v1/deploy",
        "/api/v1/servers",
        "/v2/someone-else/brain/manifests/latest",
        "/v2/alobarquest/brain/blobs/latest",
    }
)

LAUNCHERS: dict[str, str] = {
    "estate_lander": "scripts/run-estate-landing.sh",
    "inert_lander": "scripts/run-inert-landing.sh",
}

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")

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


def platform_disagreements(service: str) -> frozenset[tuple[str, str, bool]]:
    permits, listed = PLATFORM_EDGES[service]
    paths = {path for _, path in listed} | PLATFORM_PROBES
    corpus = paths | {f"{path}/" for path in paths} | {f"{path}x" for path in paths}
    return frozenset(
        (method, path, permits(method, path))
        for method in METHODS
        for path in corpus
        if permits(method, path) != _listed(service, listed, method, path)
    )


def _listed(service: str, listed: frozenset[tuple[str, str]], method: str, path: str) -> bool:
    if service == "ghcr" and method == "HEAD" and _MANIFEST.fullmatch(path):
        return True
    return (method, path) in listed


@pytest.mark.parametrize("service", sorted(PLATFORM_EDGES))
def test_the_estate_landers_platform_and_registry_reach_exactly_their_routes(service: str) -> None:
    assert sorted(platform_disagreements(service)) == []


def test_control_a_widened_platform_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = image_repoint._permits
    monkeypatch.setattr(
        image_repoint,
        "_permits",
        lambda allowed, m, p: p.endswith("/restart") or original(allowed, m, p),
    )

    assert ("POST", "/api/v1/applications/u1/restart", True) in platform_disagreements("coolify")


def test_the_estate_landers_github_reads_are_the_listing_and_the_compare() -> None:
    """The reader permits any GET, so what bounds this lane's GitHub reads is the methods its
    program calls on it: the open pull requests a revert is in, and whether production serves a
    commit at or after the revert's."""
    tree = ast.parse(Path("src/estate_lander/cli.py").read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "reader"
    }
    assert called == {"open_pull_requests", "compare_status"}


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
