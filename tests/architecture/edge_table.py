"""What the edge tables share: a row's shape, the bearers, and how a row is held to the code.

`test_bridge_edges.py`, `test_landing_edges.py` and `test_rotation_edges.py` each state one layer's
outbound surface. Each row is compared with the predicates the lane's `ConfinedClient` really asks,
so the comparison and the capture of an inline predicate live here, once.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple
from unittest import mock

Route = tuple[str, str]
Permits = Callable[[str, str], bool]

PROPOSE_BEARER = "acccb346-4baa-43ec-a1d4-b4a400c048ee"
READ_BEARER = "314f276d-55ca-4ddc-a24d-b4a3013508cd"
OBSERVER_BEARER = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
SYSTEM_BEARER = "221a48d5-3f29-4898-b300-b4820140c880"
# The `sds-bridge` alert sender token (bridge Part 3).
SDS_BRIDGE_ALERT_BEARER = "b415d03b-f92d-46b1-8a9d-b4f10103df49"
# The landers' and the rotation verifier's GitHub token is not a BWS secret: each launcher takes
# it from the gh CLI.
GH_AUTH_TOKEN = "gh auth token"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


@dataclass(frozen=True)
class Edge:
    change_manager: frozenset[Route]
    orchestrator: frozenset[Route]
    github: frozenset[Route]
    alert_hub: frozenset[Route]
    bearers: frozenset[str]

    def columns(self) -> dict[str, frozenset[Route]]:
        return {
            "change_manager": self.change_manager,
            "orchestrator": self.orchestrator,
            "github": self.github,
            "alert_hub": self.alert_hub,
        }


class BuiltClient(NamedTuple):
    base_url: str
    permits: Permits


class _Built(BaseException):
    """Ends the capture at a client's first request. Not an `Exception`: no program catches it."""


def built_clients(module: object, call: Callable[[], object]) -> list[BuiltClient]:
    """Every `ConfinedClient` the module builds during `call`, in order, with what it was given.

    Some readers build their predicate as a lambda at the call site rather than naming it, and some
    close over the instance's own repository or application. The recorder stands in for
    `ConfinedClient` on that module only, keeps the real predicate, and stops the call at the first
    request, so nothing is sent. A lambda reads its module's globals when it runs, so a later
    monkeypatch of the module still reaches it.
    """
    built: list[BuiltClient] = []

    def recorder(*, base_url: str, permits: Permits, **_: object) -> object:
        built.append(BuiltClient(base_url, permits))
        client = mock.MagicMock()
        client.request.side_effect = _Built
        return client

    with mock.patch.object(module, "ConfinedClient", recorder), contextlib.suppress(_Built):
        call()
    assert built, f"{module!r} built no ConfinedClient"
    return built


def captured_permits(module: object, call: Callable[[], object]) -> Permits:
    """The predicate of the one `ConfinedClient` the module builds during `call`."""
    [client] = built_clients(module, call)
    return client.permits


def either(*predicates: Permits) -> Permits:
    return lambda method, path: any(predicate(method, path) for predicate in predicates)


def near_misses(paths: Iterable[str]) -> frozenset[str]:
    """Each path, and its trailing-slash and suffixed near misses."""
    listed = frozenset(paths)
    return listed | {f"{path}/" for path in listed} | {f"{path}x" for path in listed}


def route_disagreements(
    edge: Edge,
    permits: Mapping[str, Permits | None],
    corpus: Collection[str],
    methods: Iterable[str],
) -> frozenset[tuple[str, str, str, bool]]:
    """(service, method, path, accepted) for each corpus route where the code and the row differ.

    `None` is a service the lane has no client for: it permits nothing.
    """
    found: set[tuple[str, str, str, bool]] = set()
    for service, listed in edge.columns().items():
        predicate = permits[service]
        for method in methods:
            for path in corpus:
                accepted = predicate is not None and predicate(method, path)
                if accepted != ((method, path) in listed):
                    found.add((service, method, path, accepted))
    return frozenset(found)


def launcher_uuids(text: str) -> frozenset[str]:
    """The BWS secrets a launcher names."""
    return frozenset(uuid.lower() for uuid in _UUID.findall(text))


def launcher_bearers(text: str) -> frozenset[str]:
    """The BWS secrets a launcher names, and its `gh` login if it reads one."""
    return launcher_uuids(text) | ({GH_AUTH_TOKEN} if GH_AUTH_TOKEN in text else set())
