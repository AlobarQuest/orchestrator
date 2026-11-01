"""Architecture guard: one test states what the Verification layer reaches.

* The orchestrator's two GitHub reads and the App token they carry (`OBSERVE`).
* The verifier lane's routes, its URLs and the bearers both launchers fetch (`LANE`).
* That the lane names checks only in the shape decision 2 trusts: sorted, stopping at the first
  that didn't succeed, and never reaching `/verify` with a check unfiled and unanswered. The server
  keeps trusting the VERIFIER identity on which check is named, so these are what that rests on.

Each row is held to the code, not to a second statement of it: the client's own predicate, the
launcher's text, the module's own constants.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

import alert_hub_client
from orchestrator.api.routes import verifier as verifier_route
from orchestrator.services.verifier import github_checks
from orchestrator.services.verifier.github_checks import GitHubActionsCheckObserver
from rotation_verifier import cli as lane_cli
from rotation_verifier import client as lane_client
from rotation_verifier import required_checks
from rotation_verifier.cli import run_pass
from tests.architecture.edge_table import (
    DEADMAN,
    GH_AUTH_TOKEN,
    SDS_BRIDGE_ALERT_BEARER,
    Edge,
    Permits,
    ServiceEdge,
    built_clients,
    launcher_bearers,
    near_misses,
    route_disagreements,
)
from tests.architecture.test_bridge_edges import _alert_hub_permits
from tests.architecture.test_rotation_edges import borrowed, network, processes
from tests.rotation_verifier.test_factory_units import (
    NOT_CONCLUDED,
    UNIT,
    Orchestrator,
    _app_change,
    _client,
    _filed,
    _required,
)

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
LANE_NAME = "rotation_verifier"

# ---- Orchestrator to GitHub ---------------------------------------------------------------------

APP_TOKEN_SOURCE = "token_provider_for"
SOURCE = Path("src/orchestrator/services/verifier/github_checks.py")
ROUTE_SOURCE = Path("src/orchestrator/api/routes/verifier.py")
RUNS = "/repos/AlobarQuest/brain/actions/runs"
JOBS = f"{RUNS}/44/jobs"
DOTTED_RUNS = "/repos/AlobarQuest/.github/actions/runs"
OBSERVE = ServiceEdge(
    base_url="https://api.github.com",
    routes=frozenset({("GET", path) for path in (RUNS, JOBS, DOTTED_RUNS)}),
    credential=APP_TOKEN_SOURCE,
)
OBSERVE_PROBES = frozenset(
    {
        f"{RUNS}/44",
        f"{RUNS}/44/logs",
        f"{RUNS}/x/jobs",
        f"{RUNS}/44/jobs/55",
        f"{RUNS}/44/rerun",
        "/repos/AlobarQuest/brain/commits/abc/check-runs",
        "/repos/AlobarQuest/brain/actions/workflows/w.yml/dispatches",
        "/repos/AlobarQuest/brain/pulls/44/merge",
        "/repos/AlobarQuest/../actions/runs",
        "/repos/../brain/actions/runs",
        "/repos/AlobarQuest/brain/extra/actions/runs",
        "/app/installations/1/access_tokens",
    }
)


def _observe_client() -> tuple[str, Permits]:
    [client] = built_clients(
        github_checks,
        lambda: GitHubActionsCheckObserver(lambda: "token").observe(
            repository="AlobarQuest/brain", head_sha="a" * 40, check_name="Quality"
        ),
    )
    return client.base_url, client.permits


def observe_disagreements() -> frozenset[tuple[str, str, str, bool]]:
    edge = Edge(
        change_manager=frozenset(),
        orchestrator=frozenset(),
        github=OBSERVE.routes,
        alert_hub=frozenset(),
        bearers=frozenset(),
    )
    permits = {
        "change_manager": None,
        "orchestrator": None,
        "github": _observe_client()[1],
        "alert_hub": None,
    }
    corpus = near_misses({path for _, path in OBSERVE.routes}) | OBSERVE_PROBES
    return route_disagreements(edge, permits, corpus, METHODS)


def test_the_orchestrator_sends_github_exactly_its_two_reads() -> None:
    assert _observe_client()[0] == OBSERVE.base_url
    assert sorted(observe_disagreements()) == []


def test_control_a_widened_observe_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = github_checks._permits
    extra = "/repos/AlobarQuest/brain/pulls/44/merge"
    monkeypatch.setattr(github_checks, "_permits", lambda m, p: p == extra or original(m, p))

    assert ("github", "POST", extra, True) in observe_disagreements()


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _names(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def _calls(tree: ast.AST, name: str) -> Iterator[ast.Call]:
    return (node for node in ast.walk(tree) if isinstance(node, ast.Call) and _names(node) == name)


def test_github_checks_has_one_client_one_request_and_no_redirects() -> None:
    tree = _tree(SOURCE)
    [request] = _calls(tree, "request")
    assert isinstance(request.args[0], ast.Constant)
    assert request.args[0].value == "GET"
    [client] = _calls(tree, "ConfinedClient")
    assert "follow_redirects" not in {keyword.arg for keyword in client.keywords}
    httpx_uses = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "httpx"
    }
    assert httpx_uses == {"BaseTransport"}


def test_the_observer_is_built_once_on_the_app_token_provider() -> None:
    built = [
        path
        for path in Path("src/orchestrator").rglob("*.py")
        for _ in _calls(_tree(path), "GitHubActionsCheckObserver")
    ]
    assert built == [ROUTE_SOURCE]
    [observer] = _calls(_tree(ROUTE_SOURCE), "GitHubActionsCheckObserver")
    assert isinstance(observer.args[0], ast.Call)
    assert _names(observer.args[0]) == OBSERVE.credential
    assert getattr(verifier_route, OBSERVE.credential) is not None


def test_each_read_carries_the_app_token() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(
            200, json={"workflow_runs": [{"id": 44, "html_url": "https://github.com/r"}]}
        )

    observer = GitHubActionsCheckObserver(lambda: "minted", transport=httpx.MockTransport(handler))
    with pytest.raises(github_checks.CheckObservationError):
        observer.observe(repository="o/r", head_sha="a" * 40, check_name="Quality")

    assert [request.headers["Authorization"] for request in sent] == ["Bearer minted"] * 2


# ---- The verifier lane --------------------------------------------------------------------------

UNIT_ID = "0b5bd8e1-6d0c-4e8a-9a0d-1f0b4d8f6a11"
UNIT_PATH = f"/api/v1/work-units/{UNIT_ID}"
LEDGER = "/api/v1/status-ledger"
MEMBERS = "/api/v1/members"
VERIFY = f"{UNIT_PATH}/verify"
NAMED_CHECK = f"{UNIT_PATH}/verifier-evidence/named-check"
REQUIRED = "/repos/o/r/branches/main/protection/required_status_checks"
# The orchestrator-verifier VERIFIER bearer, which `image_release` shares.
VERIFIER_BEARER = "660d5846-abcb-4751-be86-b483012899eb"
LANE = Edge(
    change_manager=frozenset(),
    orchestrator=frozenset(
        {
            ("GET", LEDGER),
            ("GET", MEMBERS),
            ("GET", UNIT_PATH),
            ("POST", VERIFY),
            ("POST", NAMED_CHECK),
        }
    ),
    github=frozenset({("GET", REQUIRED)}),
    alert_hub=frozenset({("POST", "/v1/alerts")}),
    bearers=DEADMAN | {VERIFIER_BEARER, GH_AUTH_TOKEN, SDS_BRIDGE_ALERT_BEARER},
)
LAUNCHERS = ("scripts/run-rotation-verifier.sh", "scripts/run-unit-verifier.sh")
LANE_PROBES = frozenset(
    {
        f"{UNIT_PATH}/adjudications",
        f"{UNIT_PATH}/evidence",
        f"{UNIT_PATH}/claim",
        f"{UNIT_PATH}/commands/fail",
        f"{UNIT_PATH}/verifier-evidence",
        "/api/v1/member-proposals",
        "/api/v1/execution-status",
        "/repos/o/r/branches/main/protection",
        "/repos/o/r/branches/dev/protection/required_status_checks",
        "/repos/o/r/pulls/44/merge",
    }
)
PERMITS: dict[str, Permits | None] = {
    "change_manager": None,
    "orchestrator": lambda m, p: lane_client.permits(m, p),
    "github": lambda m, p: required_checks._permits(m, p),
    "alert_hub": _alert_hub_permits,
}


def lane_disagreements() -> frozenset[tuple[str, str, str, bool]]:
    routes = {path for column in LANE.columns().values() for _, path in column}
    return route_disagreements(LANE, PERMITS, near_misses(routes) | LANE_PROBES, METHODS)


def test_the_lane_accepts_exactly_its_routes_among_the_corpus() -> None:
    assert sorted(lane_disagreements()) == []


def test_control_a_widened_lane_predicate_fails_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = lane_client.permits
    extra = f"{UNIT_PATH}/adjudications"
    monkeypatch.setattr(lane_client, "permits", lambda m, p: p == extra or original(m, p))

    assert ("orchestrator", "POST", extra, True) in lane_disagreements()


@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_each_launcher_fetches_exactly_the_lane_s_bearers(launcher: str) -> None:
    assert launcher_bearers(Path(launcher)) == LANE.bearers


def test_the_lane_s_urls_are_the_ones_its_code_holds() -> None:
    assert lane_cli.ORCHESTRATOR_URL == "https://sds.alobar.net"
    assert required_checks.GITHUB_API == "https://api.github.com"
    assert alert_hub_client.BASE_URL == "https://alerts.alobar.net"


def test_the_lane_builds_exactly_its_clients() -> None:
    assert network(LANE_NAME) == {
        "src/rotation_verifier/alerts.py": 0,
        "src/rotation_verifier/cli.py": 0,
        "src/rotation_verifier/client.py": 1,
        "src/rotation_verifier/required_checks.py": 1,
        "src/sds_members/client.py": 1,
    }


def test_the_lane_starts_no_process() -> None:
    assert processes(LANE_NAME) == {}


def test_the_lane_uses_only_the_named_part_of_its_siblings() -> None:
    assert borrowed(LANE_NAME) == {
        "alert_hub_client": frozenset({"Alert", "AlertHubError", "KeyedAlerts", "alert_key"}),
        "estate_clients.confined": frozenset(
            {"ConfinedClient", "TransportFailure", "base_url_problem", "error_code"}
        ),
        "sds_members.client": frozenset({"Member", "MembersUnreadable", "parse_members"}),
        "sds_members.populations": frozenset({"granted", "service_for_change_class"}),
    }


# ---- What decision 2 rests on -------------------------------------------------------------------


def test_a_non_bump_files_in_sorted_order_and_stops_at_the_first_failure() -> None:
    orchestrator = Orchestrator(
        {UNIT: _app_change()},
        checks={"b-check": _filed("failure"), "c-check": _filed("success")},
    )

    run_pass(_client(orchestrator), read_required_checks=_required("c-check", "b-check", "a-check"))

    assert [c["check_name"] for c in orchestrator.filed] == ["a-check", "b-check"]
    assert orchestrator.verified == [UNIT]


def test_every_check_the_repository_requires_is_filed_before_a_pass() -> None:
    orchestrator = Orchestrator({UNIT: _app_change()})

    run_pass(_client(orchestrator), read_required_checks=_required("c-check", "a-check", "b-check"))

    assert [c["check_name"] for c in orchestrator.filed] == ["a-check", "b-check", "c-check"]


def test_a_check_that_has_not_answered_never_reaches_verify() -> None:
    orchestrator = Orchestrator(
        {UNIT: _app_change()},
        checks={"b-check": httpx.Response(409, json=NOT_CONCLUDED)},
    )

    run_pass(_client(orchestrator), read_required_checks=_required("c-check", "b-check", "a-check"))

    assert [c["check_name"] for c in orchestrator.filed] == ["a-check", "b-check"]
    assert orchestrator.verified == []
