"""Architecture guard: one test states what the Execution layer reaches.

* The orchestrator's one GitHub route and the App token it carries (`DISPATCH`).
* `execution_watch`'s routes, its two services' URLs and the bearers its launcher fetches (`EDGES`).
* The member record as the only source of a grant, for dispatch and for the claim.
* The routes factory-runner calls (`RUNNER_ROUTES`), each one served here.
* Every fixture pinned with factory-runner (`PINS`).

Each row is held to the code, not to a second statement of it: the client's own predicate, the
launcher's text, the served OpenAPI schema, the pin's own constant. The factory-runner half compares
with a checkout when one is on disk (`FACTORY_RUNNER_DIR`, else `~/Projects/factory-runner`), and
CI, which has none, skips only that half. A checkout behind a paired change fails here until it's
pulled, which is the comparison working.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
from collections.abc import Iterable, Iterator
from functools import cache
from pathlib import Path
from typing import Any

import httpx
import pytest

import alert_hub_client
from execution_watch import cli as watch_cli
from execution_watch import client as watch_client
from orchestrator.api.routes.lifecycle import COMMAND_TARGETS
from orchestrator.main import create_app
from orchestrator.services.execution import dispatch as dispatch_module
from orchestrator.services.execution import target_grant
from orchestrator.services.execution.dispatch import GitHubActionsDispatcher
from scripts.layers import load
from tests.architecture.edge_table import (
    DEADMAN,
    OBSERVER_BEARER,
    SDS_BRIDGE_ALERT_BEARER,
    Edge,
    Permits,
    Pin,
    ServiceEdge,
    built_clients,
    launcher_bearers,
    near_misses,
    route_disagreements,
)
from tests.architecture.test_bridge_edges import _alert_hub_permits

SOURCE = Path("src/orchestrator")
DISPATCH_SOURCE = SOURCE / "services/execution/dispatch.py"
GRANT_SOURCE = SOURCE / "services/execution/target_grant.py"
ROUTE_SOURCE = SOURCE / "api/routes/execution.py"
CLAIM_SOURCE = SOURCE / "services/lifecycle/claims.py"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")

# ---- Orchestrator to GitHub ---------------------------------------------------------------------

# The App installation token: its one source, by the function that hands it to the dispatcher.
APP_TOKEN_SOURCE = "token_provider_for"
DISPATCH_PATH = "/repos/AlobarQuest/brain/actions/workflows/factory-runner-pilot.yml/dispatches"
DISPATCH = ServiceEdge(
    base_url="https://api.github.com",
    routes=frozenset({("POST", DISPATCH_PATH)}),
    credential=APP_TOKEN_SOURCE,
)
DISPATCH_PATHS = frozenset({DISPATCH_PATH})
DISPATCH_PROBES = frozenset(
    {
        "/app/installations/1/access_tokens",
        "/repos/AlobarQuest/brain/actions/runs",
        "/repos/AlobarQuest/brain/actions/workflows/a/b/dispatches",
        "/repos/AlobarQuest/brain/actions/workflows/../dispatches",
        "/repos/AlobarQuest/brain/dispatches",
        "/repos/AlobarQuest/brain/merges",
        "/repos/AlobarQuest/brain/pulls/44/merge",
        "/repos/AlobarQuest/brain/extra/actions/workflows/w.yml/dispatches",
        "/repos/AlobarQuest/../actions/workflows/w.yml/dispatches",
    }
)


def _dispatch(dispatcher: GitHubActionsDispatcher) -> object:
    return dispatcher.dispatch_workflow(
        repository="AlobarQuest/brain",
        workflow_id="factory-runner-pilot.yml",
        ref="main",
        inputs={"work_unit_id": "u1"},
    )


def _dispatch_client() -> tuple[str, Permits]:
    [client] = built_clients(
        dispatch_module, lambda: _dispatch(GitHubActionsDispatcher(lambda: "token"))
    )
    return client.base_url, client.permits


def dispatch_disagreements() -> frozenset[tuple[str, str, str, bool]]:
    edge = Edge(
        change_manager=frozenset(),
        orchestrator=frozenset(),
        github=DISPATCH.routes,
        alert_hub=frozenset(),
        bearers=frozenset(),
    )
    permits = {
        "change_manager": None,
        "orchestrator": None,
        "github": _dispatch_client()[1],
        "alert_hub": None,
    }
    return route_disagreements(
        edge, permits, near_misses(DISPATCH_PATHS) | DISPATCH_PROBES, METHODS
    )


def test_every_dispatch_route_is_in_the_corpus() -> None:
    assert {path for _, path in DISPATCH.routes} == DISPATCH_PATHS


def test_the_orchestrator_sends_github_exactly_the_one_route() -> None:
    assert _dispatch_client()[0] == DISPATCH.base_url
    assert sorted(dispatch_disagreements()) == []


def test_control_a_widened_dispatch_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = dispatch_module._permits
    monkeypatch.setattr(
        dispatch_module,
        "_permits",
        lambda m, p: p == "/repos/AlobarQuest/brain/merges" or original(m, p),
    )

    assert (
        "github",
        "POST",
        "/repos/AlobarQuest/brain/merges",
        True,
    ) in dispatch_disagreements()


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    """Each call of `name`, whether written bare or as an attribute."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and name == (node.func.id if isinstance(node.func, ast.Name) else _attribute(node.func))
    ]


def _attribute(func: ast.expr) -> str | None:
    return func.attr if isinstance(func, ast.Attribute) else None


def test_dispatch_py_has_one_client_and_one_request() -> None:
    """A second client, or a send that goes around the client, never reaches the predicate above."""
    tree = _tree(DISPATCH_SOURCE)
    [request] = _calls(tree, "request")
    assert isinstance(request.args[0], ast.Constant)
    assert request.args[0].value == "POST"
    assert len(_calls(tree, "ConfinedClient")) == 1
    httpx_uses = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "httpx"
    }
    assert httpx_uses == {"BaseTransport"}


def test_the_one_route_carries_the_app_token() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(204)

    _dispatch(GitHubActionsDispatcher(lambda: "minted", transport=httpx.MockTransport(handler)))

    assert sent[0].headers["Authorization"] == "Bearer minted"


def _sources() -> Iterator[Path]:
    return SOURCE.rglob("*.py")


def test_the_dispatcher_is_built_once_on_the_app_token_provider() -> None:
    built = [path for path in _sources() for _ in _calls(_tree(path), "GitHubActionsDispatcher")]
    assert built == [ROUTE_SOURCE]

    tree = _tree(ROUTE_SOURCE)
    [dispatcher] = _calls(tree, "GitHubActionsDispatcher")
    assert isinstance(dispatcher.args[0], ast.Name)
    [assigned] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == dispatcher.args[0].id
            for target in node.targets
        )
    ]
    assert isinstance(assigned.value, ast.Call)
    assert isinstance(assigned.value.func, ast.Name)
    assert assigned.value.func.id == DISPATCH.credential


# ---- execution_watch ----------------------------------------------------------------------------

STATUS_PATH = "/api/v1/execution-status"
EDGES: dict[str, Edge] = {
    "execution_watch": Edge(
        change_manager=frozenset(),
        orchestrator=frozenset({("GET", STATUS_PATH)}),
        github=frozenset(),
        alert_hub=frozenset({("POST", "/v1/alerts")}),
        bearers=DEADMAN | {OBSERVER_BEARER, SDS_BRIDGE_ALERT_BEARER},
    ),
}
LAUNCHERS: dict[str, str] = {"execution_watch": "scripts/run-execution-watch.sh"}
BASE_URLS: dict[str, dict[str, str]] = {
    "execution_watch": {
        "orchestrator": "https://sds.alobar.net",
        "alert_hub": "https://alerts.alobar.net",
    },
}
ROUTES = frozenset({STATUS_PATH, "/v1/alerts"})
PROBES = frozenset(
    {
        "/api/v1/dead-letter",
        "/api/v1/in-flight-units",
        "/api/v1/observations",
        "/api/v1/work-units/44/dispatch",
        "/api/v1/work-units/44/commands/fail",
    }
)
PERMITS: dict[str, dict[str, Permits | None]] = {
    "execution_watch": {
        "change_manager": None,
        "orchestrator": lambda m, p: watch_client.permits(m, p),
        "github": None,
        "alert_hub": _alert_hub_permits,
    },
}


def watch_disagreements(lane: str) -> frozenset[tuple[str, str, str, bool]]:
    return route_disagreements(EDGES[lane], PERMITS[lane], near_misses(ROUTES) | PROBES, METHODS)


def test_every_lane_route_is_in_the_corpus() -> None:
    listed = {
        path for edge in EDGES.values() for column in edge.columns().values() for _, path in column
    }
    assert listed == ROUTES


@pytest.mark.parametrize("lane", sorted(EDGES))
def test_each_lane_accepts_exactly_its_routes_among_the_corpus(lane: str) -> None:
    assert sorted(watch_disagreements(lane)) == []


def test_control_a_widened_watch_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = watch_client.permits
    monkeypatch.setattr(
        watch_client,
        "permits",
        lambda m, p: p == "/api/v1/dead-letter" or original(m, p),
    )

    assert ("orchestrator", "GET", "/api/v1/dead-letter", True) in watch_disagreements(
        "execution_watch"
    )


def test_the_watch_client_asks_the_predicate_the_table_reads() -> None:
    built = built_clients(
        watch_client,
        lambda: watch_client.Orchestrator(
            base_url=watch_cli.ORCHESTRATOR_URL, credential_key_id="k", token="t"
        ).execution_status(),
    )

    [client] = built
    assert client.permits is watch_client.permits
    assert client.base_url == BASE_URLS["execution_watch"]["orchestrator"]


def test_the_watch_alerts_through_the_shared_client_at_its_url() -> None:
    assert "alert_hub_client" in {
        node.module
        for node in ast.walk(_tree(Path("src/execution_watch/cli.py")))
        if isinstance(node, ast.ImportFrom)
    }
    assert alert_hub_client.BASE_URL == BASE_URLS["execution_watch"]["alert_hub"]


@pytest.mark.parametrize("lane", sorted(LAUNCHERS))
def test_each_launcher_fetches_exactly_its_bearers(lane: str) -> None:
    assert launcher_bearers(Path(LAUNCHERS[lane])) == EDGES[lane].bearers


def test_the_table_is_the_execution_layer_population() -> None:
    programs = sorted(
        match.group(1)
        for pattern in load()["execution"].paths
        if (match := re.fullmatch(r"src/([a-z_]+)/\*\*", pattern))
    )
    assert programs == sorted(EDGES) == sorted(LAUNCHERS)


# ---- The member-record read ---------------------------------------------------------------------

GRANT_READER = "MemberRecordGrants"


def test_the_member_record_is_the_only_grant_source() -> None:
    implementers = {
        (path, node.name)
        for path in _sources()
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(item, ast.FunctionDef) and item.name == "is_granted" for item in node.body
        )
    }
    assert implementers - {(GRANT_SOURCE, "TargetGrantSource")} == {(GRANT_SOURCE, GRANT_READER)}


def test_the_grant_reader_is_a_read_of_the_record_and_nothing_else() -> None:
    imported = {
        name.name
        for node in ast.walk(_tree(GRANT_SOURCE))
        if isinstance(node, ast.Import)
        for name in node.names
    }
    assert "httpx" not in imported
    assert _calls(_tree(GRANT_SOURCE), "list_members")


def test_dispatch_is_handed_the_member_record() -> None:
    callers = [path for path in _sources() for _ in _calls(_tree(path), "dispatch_work_unit")]
    assert callers == [ROUTE_SOURCE]

    [call] = _calls(_tree(ROUTE_SOURCE), "dispatch_work_unit")
    [source] = [keyword.value for keyword in call.keywords if keyword.arg == "grant_source"]
    assert isinstance(source, ast.Call)
    assert isinstance(source.func, ast.Name)
    assert source.func.id == GRANT_READER


def test_the_claim_passes_no_other_grant_source() -> None:
    """Every call in `claims.py` leaves the reader to the default, or forwards the caller's."""
    for call in _calls(_tree(CLAIM_SOURCE), "unit_grant_refusal"):
        extra = call.args[3:]
        assert all(isinstance(argument, ast.Name) and argument.id == "grants" for argument in extra)
        assert not call.keywords


def test_the_default_grant_source_is_the_member_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, str]] = []

    class Recorder:
        def __init__(self, session: object) -> None:
            pass

        def is_granted(self, repository: str, service: str) -> bool:
            asked.append((repository, service))
            return True

    monkeypatch.setattr(target_grant, GRANT_READER, Recorder)
    monkeypatch.setattr(target_grant, "is_rotation_unit", lambda _unit, _revision: False)
    monkeypatch.setattr(
        target_grant,
        "normalize_authority",
        lambda _authority: type(
            "Envelope", (), {"target_repository": "AlobarQuest/brain", "change_class": None}
        ),
    )
    unit: Any = type("Unit", (), {"authority": {}, "required_capability": "repo.edit"})
    nothing: Any = None

    assert target_grant.unit_grant_refusal(nothing, unit, nothing) is None
    assert asked == [("AlobarQuest/brain", target_grant.APP_CHANGES_SERVICE)]


# ---- Routes the runner calls --------------------------------------------------------------------

_UNIT = "/api/v1/work-units/{unit_id}"
RUNNER_ROUTES = frozenset(
    {
        ("GET", f"{_UNIT}/runner-brief"),
        ("GET", f"{_UNIT}/evidence-pack/markdown"),
        ("POST", f"{_UNIT}/claim"),
        ("POST", f"{_UNIT}/renew"),
        ("POST", f"{_UNIT}/reclaim-expired-claim"),
        ("POST", f"{_UNIT}/commands/start"),
        ("POST", f"{_UNIT}/commands/submit"),
        ("POST", f"{_UNIT}/commands/fail"),
        ("GET", f"{_UNIT}/evidence"),
        ("POST", f"{_UNIT}/evidence"),
        ("POST", f"{_UNIT}/pr-binding"),
        ("POST", f"{_UNIT}/cost-actuals"),
    }
)
_COMMAND_PREFIX = f"{_UNIT}/commands/"


@cache
def _openapi_paths() -> dict[str, Any]:
    return create_app().openapi()["paths"]


def _served(method: str, path: str) -> bool:
    """Whether the application serves the route. A command is one route and a served name."""
    paths = _openapi_paths()
    if path.startswith(_COMMAND_PREFIX):
        name = path.removeprefix(_COMMAND_PREFIX)
        return name in COMMAND_TARGETS and method.lower() in paths[f"{_COMMAND_PREFIX}{{command}}"]
    return method.lower() in paths.get(path, {})


@pytest.mark.parametrize(("method", "path"), sorted(RUNNER_ROUTES))
def test_each_route_the_runner_calls_is_served(method: str, path: str) -> None:
    assert _served(method, path)


def test_control_a_route_nobody_serves_is_not_served() -> None:
    assert not _served("POST", f"{_UNIT}/runner-brief")
    assert not _served("POST", f"{_COMMAND_PREFIX}explode")


# ---- Pins ---------------------------------------------------------------------------------------


def _raw(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sorted_keys(content: bytes) -> str:
    return _raw(json.dumps(json.loads(content), sort_keys=True).encode())


def _compact(content: bytes) -> str:
    return _raw(json.dumps(json.loads(content), sort_keys=True, separators=(",", ":")).encode())


_ENVELOPE_TEST = "tests/contract/test_runner_envelope_contract.py"
_RUNNER_ENVELOPE_TEST = "tests/test_orchestrator_envelope_contract.py"
PINS: tuple[Pin, ...] = (
    Pin(
        "orchestrator_command_contract.json",
        "tests/contract/test_runner_command_contract.py",
        "CONTRACT_SHA256",
        _raw,
        "tests/test_orchestrator_command_contract.py",
        "CONTRACT_SHA256",
    ),
    Pin(
        "runner_authority_envelope.json",
        _ENVELOPE_TEST,
        "CONTRACT_SHA256",
        _compact,
        _RUNNER_ENVELOPE_TEST,
        "CONTRACT_SHA256",
    ),
    Pin(
        "runner_authority_envelope_edit.json",
        _ENVELOPE_TEST,
        "CONTRACT_SHA256_EDIT",
        _compact,
        _RUNNER_ENVELOPE_TEST,
        "CONTRACT_SHA256_EDIT",
    ),
    Pin(
        "runner_authority_envelope_verify.json",
        _ENVELOPE_TEST,
        "CONTRACT_SHA256_VERIFY",
        _compact,
        _RUNNER_ENVELOPE_TEST,
        "CONTRACT_SHA256_VERIFY",
    ),
    Pin(
        "runner_envelope_contract.json",
        _ENVELOPE_TEST,
        "CONTRACT_SHA256_SURFACE",
        _sorted_keys,
        _RUNNER_ENVELOPE_TEST,
        "CONTRACT_SHA256_SURFACE",
    ),
    Pin(
        "runner_brief.json",
        "tests/contract/test_runner_brief_contract.py",
        "CONTRACT_SHA256",
        _compact,
        "tests/test_runner_brief_contract.py",
        "CONTRACT_SHA256",
    ),
    Pin(
        "runner_cost_actuals.json",
        "tests/contract/test_cost_actuals_contract.py",
        "COST_ACTUALS_CONTRACT_SHA256",
        _compact,
        "tests/test_cost_actuals_contract.py",
        "COST_ACTUALS_CONTRACT_SHA256",
    ),
)
FIXTURES = Path("tests/fixtures")


def _constant(test: Path, name: str) -> str | None:
    """The module-level string `name` in a test file, or None."""
    for node in _tree(test).body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    return None


def _compared_in_a_test(test: Path, name: str) -> bool:
    return any(
        isinstance(function, ast.FunctionDef)
        and function.name.startswith("test_")
        and any(
            isinstance(node, ast.Compare)
            and any(isinstance(item, ast.Name) and item.id == name for item in ast.walk(node))
            for node in ast.walk(function)
        )
        for function in _tree(test).body
    )


def test_the_table_lists_every_fixture_named_for_the_runner() -> None:
    named = {path.name for path in FIXTURES.glob("runner_*.json")}
    assert {pin.fixture for pin in PINS} == named | {"orchestrator_command_contract.json"}


@pytest.mark.parametrize("pin", PINS, ids=lambda pin: pin.fixture)
def test_each_pin_holds_on_this_side(pin: Pin) -> None:
    test = Path(pin.test)
    assert pin.fixture in test.read_text(encoding="utf-8")
    assert _constant(test, pin.constant) == pin.digest((FIXTURES / pin.fixture).read_bytes())
    assert _compared_in_a_test(test, pin.constant)


def pin_disagreements(checkout: Path, pins: Iterable[Pin] = PINS) -> list[str]:
    """Each way the factory-runner checkout differs from the table and this side's fixtures."""
    listed = list(pins)
    found: list[str] = []
    theirs = checkout / "tests/fixtures"
    held = {path.name for path in theirs.glob("*.json")}
    found += [f"{name}: not in the table" for name in sorted(held - {p.fixture for p in listed})]
    for pin in listed:
        fixture = theirs / pin.fixture
        if not fixture.is_file():
            found.append(f"{pin.fixture}: absent from factory-runner")
        elif fixture.read_bytes() != (FIXTURES / pin.fixture).read_bytes():
            found.append(f"{pin.fixture}: bytes differ")
        test = checkout / pin.counterpart_test
        if not test.is_file():
            found.append(f"{pin.fixture}: {pin.counterpart_test} is absent")
        elif _constant(test, pin.counterpart_constant) != _constant(Path(pin.test), pin.constant):
            found.append(f"{pin.fixture}: {pin.counterpart_constant} differs")
    return found


RUNNER_CLIENT = "src/factory_runner/client.py"


def _route_text(path: ast.expr) -> str | None:
    if isinstance(path, ast.Constant):
        return str(path.value)
    if not isinstance(path, ast.JoinedStr):
        return None
    parts = [
        f"{{{ast.unparse(part.value)}}}"
        if isinstance(part, ast.FormattedValue)
        else str(part.value)
        for part in path.values
        if isinstance(part, ast.FormattedValue | ast.Constant)
    ]
    return "".join(parts)


def runner_routes(checkout: Path) -> frozenset[tuple[str, str]]:
    """Every route factory-runner's client names, with each named command spelled out."""
    tree = _tree(checkout / RUNNER_CLIENT)
    routes: set[tuple[str, str]] = set()
    commands = {
        str(call.args[1].value)
        for call in _calls(tree, "command")
        if len(call.args) > 1 and isinstance(call.args[1], ast.Constant)
    }
    for call in _calls(tree, "_request"):
        if not isinstance(call.args[0], ast.Constant):
            continue  # The transport's own retry passes its parameters along.
        method, path = str(call.args[0].value), _route_text(call.args[1])
        assert path is not None, ast.unparse(call)
        if path.endswith("/{command}"):
            routes |= {(method, path.replace("{command}", name)) for name in commands}
        else:
            routes.add((method, path))
    return frozenset(routes)


def runner_sources_outside_the_client(checkout: Path) -> list[str]:
    package = checkout / "src/factory_runner"
    return sorted(
        str(path.relative_to(checkout))
        for path in package.rglob("*.py")
        if path != checkout / RUNNER_CLIENT and "/api/v1" in path.read_text(encoding="utf-8")
    )


def runner_checkout() -> Path | None:
    candidate = Path(
        os.environ.get("FACTORY_RUNNER_DIR") or Path.home() / "Projects/factory-runner"
    )
    return candidate if candidate.is_dir() else None


@pytest.fixture
def checkout() -> Path:
    found = runner_checkout()
    if found is None:
        pytest.skip(
            "no factory-runner checkout on disk (FACTORY_RUNNER_DIR or ~/Projects/factory-runner): "
            "the table's local half ran, the comparison did not"
        )
    return found


def test_each_pin_matches_the_checkout(checkout: Path) -> None:
    assert pin_disagreements(checkout) == []


def test_the_runner_calls_exactly_the_listed_routes(checkout: Path) -> None:
    assert sorted(runner_routes(checkout)) == sorted(RUNNER_ROUTES)
    assert runner_sources_outside_the_client(checkout) == []


def _faithful_checkout(root: Path) -> Path:
    """A stand-in checkout that agrees with every pin."""
    (root / "tests/fixtures").mkdir(parents=True)
    for pin in PINS:
        shutil.copy(FIXTURES / pin.fixture, root / "tests/fixtures" / pin.fixture)
        test = root / pin.counterpart_test
        value = _constant(Path(pin.test), pin.constant)
        with test.open("a", encoding="utf-8") as written:
            written.write(f'{pin.counterpart_constant} = "{value}"\n')
    return root


def test_control_a_faithful_checkout_agrees(tmp_path: Path) -> None:
    assert pin_disagreements(_faithful_checkout(tmp_path)) == []


def test_control_a_fixture_missing_from_the_checkout_fails(tmp_path: Path) -> None:
    checkout = _faithful_checkout(tmp_path)
    (checkout / "tests/fixtures/runner_brief.json").unlink()

    assert pin_disagreements(checkout) == ["runner_brief.json: absent from factory-runner"]


def test_control_a_fixture_differing_by_a_byte_fails(tmp_path: Path) -> None:
    checkout = _faithful_checkout(tmp_path)
    with (checkout / "tests/fixtures/runner_brief.json").open("a") as written:
        written.write("\n")

    assert pin_disagreements(checkout) == ["runner_brief.json: bytes differ"]


def test_control_a_fixture_the_table_does_not_list_fails(tmp_path: Path) -> None:
    checkout = _faithful_checkout(tmp_path)
    (checkout / "tests/fixtures/new_contract.json").write_text("{}")

    assert pin_disagreements(checkout) == ["new_contract.json: not in the table"]


def test_control_a_counterpart_constant_that_differs_fails(tmp_path: Path) -> None:
    checkout = _faithful_checkout(tmp_path)
    test = checkout / "tests/test_cost_actuals_contract.py"
    test.write_text('COST_ACTUALS_CONTRACT_SHA256 = "0"\n')

    assert pin_disagreements(checkout) == [
        "runner_cost_actuals.json: COST_ACTUALS_CONTRACT_SHA256 differs"
    ]


def test_control_with_no_checkout_the_comparison_has_nothing_to_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FACTORY_RUNNER_DIR", str(tmp_path / "absent"))

    assert runner_checkout() is None


def test_the_named_checkout_wins_over_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FACTORY_RUNNER_DIR", str(tmp_path))

    assert runner_checkout() == tmp_path
