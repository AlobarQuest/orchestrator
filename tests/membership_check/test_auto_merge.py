"""A workflow that arms auto-merge is recognised, and a rollout workflow is not.

The arming fixtures are the auto-merge workflows the estate retired; the control is the rollout
workflows, which merge nothing. The reader is driven through a mock transport, so nothing reaches
GitHub.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from membership_check.auto_merge import (
    AUTO_MERGE_QUERY,
    GRAPHQL,
    AutoMergeState,
    GitHubAutoMergeSource,
    arming,
    is_allowed,
    is_allowed_query,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RETIRED = sorted((FIXTURES / "auto-merge-rules").glob("*.yml"))
ROLLOUTS = sorted((FIXTURES / "deploy-workflows").glob("*.yml"))


def test_the_fixture_sets_are_not_empty():
    assert RETIRED and ROLLOUTS


@pytest.mark.parametrize("path", RETIRED, ids=lambda p: p.stem[:12])
def test_every_retired_auto_merge_rule_is_arming(path: Path):
    assert arming(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", ROLLOUTS, ids=lambda p: p.stem[:12])
def test_no_rollout_workflow_is_arming(path: Path):
    assert not arming(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "line",
    [
        'run: gh pr merge "$PR_URL" --auto --squash',
        "run: gh pr merge --squash --auto",
        'run: gh pr merge --auto --squash "$PR_URL"',
        'run: |\n  gh pr merge "$PR_URL" \\\n    --squash --auto',
        'run: |\r\n  gh pr merge "$PR_URL" \\\r\n    --squash --auto\r\n',
        "run: gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: $i) { a } }'",
        "uses: peter-evans/enable-pull-request-automerge@v3",
    ],
)
def test_an_arming_command_in_any_flag_order_is_arming(line: str):
    assert arming(line)


@pytest.mark.parametrize(
    "line",
    [
        "run: gh pr merge --disable-auto",
        'run: gh pr merge "$PR_URL" --squash',
        "run: gh pr view --auto",
        "run: gh pr merge --squash\nrun: echo --auto",
        "run: gh pr merge --autodelete",
    ],
)
def test_a_merge_without_a_whole_auto_flag_is_not_arming(line: str):
    assert not arming(line)


REPO = "/repos/A/one"
LISTING = f"{REPO}/contents/.github/workflows"
TOKEN = "placeholder-not-a-token"


def _served(setting: object) -> httpx.Response:
    return httpx.Response(200, json={"data": {"repository": {"autoMergeAllowed": setting}}})


OFF = _served(False)
ON = _served(True)


def _source(routes: dict[str, httpx.Response]) -> tuple[GitHubAutoMergeSource, list[str]]:
    """A reader over `routes`, keyed by path; `GRAPHQL` answers the one POST.

    Every request it sees is checked against the reader's own guards, and the POST's body against
    the fixed query, so a test that passes has sent only what the guards admit.
    """
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert "placeholder" in request.headers["Authorization"]
        if request.method == "POST":
            assert is_allowed_query(request.url.path, json.loads(request.content))
        else:
            assert request.method == "GET" and is_allowed(request.url.path)
        seen.append(f"{request.method} {request.url.path}")
        return routes.get(request.url.path, httpx.Response(404))

    return GitHubAutoMergeSource(token=TOKEN, transport=httpx.MockTransport(handle)), seen


def _entry(name: str, kind: str = "file") -> dict[str, str]:
    return {"name": name, "type": kind, "path": f".github/workflows/{name}"}


def test_a_repository_with_auto_merge_off_and_quiet_workflows_reads_clean():
    source, seen = _source(
        {
            REPO: httpx.Response(200, json={}),
            GRAPHQL: OFF,
            LISTING: httpx.Response(200, json=[_entry("ci.yml"), _entry("nested", "dir")]),
            f"{LISTING}/ci.yml": httpx.Response(200, text="run: make check\n"),
        }
    )
    assert source.state("A/one") == AutoMergeState(False, ())
    assert seen[:2] == [f"GET {REPO}", f"POST {GRAPHQL}"]


def test_an_arming_workflow_is_named():
    source, _ = _source(
        {
            REPO: httpx.Response(200, json={}),
            GRAPHQL: ON,
            LISTING: httpx.Response(200, json=[_entry("ci.yml"), _entry("dependabot-auto.yml")]),
            f"{LISTING}/ci.yml": httpx.Response(200, text="run: make check\n"),
            f"{LISTING}/dependabot-auto.yml": httpx.Response(
                200, text="run: gh pr merge --auto --squash\n"
            ),
        }
    )
    state = source.state("A/one")
    assert state.allowed is True
    assert state.arming_workflows == (".github/workflows/dependabot-auto.yml",)


def test_no_workflows_directory_is_no_arming_workflow():
    source, _ = _source({REPO: httpx.Response(200, json={}), GRAPHQL: OFF})
    assert source.state("A/one") == AutoMergeState(False, ())


def test_the_rest_setting_is_never_read_on_any_host():
    """Only GraphQL answers the setting: a REST `allow_auto_merge` beside an unserved GraphQL
    field is still unread, so the two hosts' passes ask the same thing."""
    source, _ = _source(
        {
            REPO: httpx.Response(200, json={"allow_auto_merge": False}),
            GRAPHQL: httpx.Response(200, json={"data": {"repository": {}}}),
            LISTING: httpx.Response(200, json=[]),
        }
    )
    state = source.state("A/one")
    assert state.allowed is None and state.arming_workflows == ()


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (httpx.Response(200, json={"data": {"repository": {}}}), "answered 200 without data"),
        (httpx.Response(200, json={"data": {"repository": None}}), "answered 200 without data"),
        (httpx.Response(200, json={"data": None}), "answered 200 without data"),
        (_served("true"), "answered 200 without data"),
        (_served(1), "answered 200 without data"),
        (_served(None), "answered 200 without data"),
        (httpx.Response(200, json=[True]), "answered 200 with no JSON object"),
        (httpx.Response(200, text="true"), "answered 200 with no JSON object"),
        (httpx.Response(200, text="<html>"), "answered 200 with no JSON"),
        (
            httpx.Response(201, json={"data": {"repository": {"autoMergeAllowed": False}}}),
            "answered 201",
        ),
        (httpx.Response(204), "answered 204"),
        (
            httpx.Response(
                200, json={"data": {"repository": {"autoMergeAllowed": False}}, "errors": []}
            ),
            "answered 200 with errors: a malformed errors list",
        ),
        (
            httpx.Response(
                200, json={"data": {"repository": {"autoMergeAllowed": False}}, "errors": None}
            ),
            "answered 200 with errors: a malformed errors list",
        ),
        (httpx.Response(403), "answered 403"),
        (httpx.Response(401), "answered 401"),
        (httpx.Response(502), "answered 502"),
        (
            httpx.Response(
                200,
                json={
                    "data": {"repository": None},
                    "errors": [
                        {"type": "NOT_FOUND", "message": "secret-echo", "path": ["repository"]},
                        {"type": "FORBIDDEN", "message": "secret-echo"},
                    ],
                },
            ),
            "answered 200 with errors: NOT_FOUND, FORBIDDEN",
        ),
        (
            # Errors beside a served value still fail closed.
            httpx.Response(
                200,
                json={
                    "data": {"repository": {"autoMergeAllowed": False}},
                    "errors": [{"type": "secret-echo lower case"}, {"message": "secret-echo"}],
                },
            ),
            "with errors: an error with no type, an error with no type",
        ),
        (
            httpx.Response(200, json={"data": None, "errors": "secret-echo"}),
            "with errors: a malformed errors list",
        ),
    ],
)
def test_a_setting_that_is_not_a_served_boolean_is_unread_with_its_reason(
    answer: httpx.Response, reason: str
):
    source, _ = _source(
        {REPO: httpx.Response(200, json={}), GRAPHQL: answer, LISTING: httpx.Response(200, json=[])}
    )
    state = source.state("A/one")
    assert state.allowed is None and state.arming_workflows == ()
    assert state.detail.startswith("A/one: autoMergeAllowed was not served (POST /graphql ")
    assert reason in state.detail
    assert "secret-echo" not in state.detail and TOKEN not in state.detail


def test_an_unserved_setting_and_a_failed_listing_keep_both_reasons():
    source, _ = _source(
        {
            REPO: httpx.Response(200, json={}),
            GRAPHQL: httpx.Response(403),
            LISTING: httpx.Response(503),
        }
    )
    state = source.state("A/one")
    assert state.allowed is None and state.arming_workflows is None
    assert "POST /graphql answered 403" in state.detail and "503" in state.detail


def test_an_unreadable_repository_reads_nothing_else():
    source, seen = _source({REPO: httpx.Response(503)})
    state = source.state("A/one")
    assert state.allowed is None and state.arming_workflows is None
    assert seen == [f"GET {REPO}"]


@pytest.mark.parametrize("status", [403, 429, 500])
def test_a_failed_listing_is_unread_not_empty(status: int):
    source, _ = _source(
        {
            REPO: httpx.Response(200, json={}),
            GRAPHQL: OFF,
            LISTING: httpx.Response(status),
        }
    )
    assert source.state("A/one").arming_workflows is None


def test_a_failed_file_read_is_unread_not_quiet():
    source, _ = _source(
        {
            REPO: httpx.Response(200, json={}),
            GRAPHQL: OFF,
            LISTING: httpx.Response(200, json=[_entry("ci.yml")]),
        }
    )
    assert source.state("A/one").arming_workflows is None


def test_a_listed_name_that_would_leave_the_directory_is_refused_unsent():
    source, seen = _source(
        {
            REPO: httpx.Response(200, json={}),
            GRAPHQL: OFF,
            LISTING: httpx.Response(200, json=[_entry("../../../x")]),
        }
    )
    assert source.state("A/one").arming_workflows is None
    assert all(is_allowed(entry.split(" ", 1)[1]) for entry in seen if entry.startswith("GET "))


@pytest.mark.parametrize("slug", ["../..", "o/r?x=1", "o", "o/r/s", "o/r\n"])
def test_a_slug_that_composes_another_route_is_refused_unsent(slug: str):
    source, seen = _source({})
    state = source.state(slug)
    assert state == AutoMergeState(None, None, state.detail)
    assert seen == []


def test_a_transport_fault_is_unread():
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    source = GitHubAutoMergeSource(token=TOKEN, transport=httpx.MockTransport(fail))
    state = source.state("A/one")
    assert state.allowed is None and state.arming_workflows is None
    assert "ConnectError" in state.detail


def test_only_the_three_routes_are_allowed():
    assert is_allowed("/repos/A/one")
    assert is_allowed("/repos/A/one/contents/.github/workflows")
    assert is_allowed("/repos/A/one/contents/.github/workflows/ci.yml")
    for forbidden in (
        "/repos/A/one/pulls",
        "/repos/A/one/contents/.github/workflows/sub/ci.yml",
        "/repos/A/one/contents/README.md",
        "/user/repos",
        "/repos/A",
    ):
        assert not is_allowed(forbidden), forbidden


def test_a_listing_that_is_not_a_list_is_unread():
    source, _ = _source(
        {
            REPO: httpx.Response(200, json={}),
            GRAPHQL: OFF,
            LISTING: httpx.Response(200, content=json.dumps({"name": "x"}).encode()),
        }
    )
    assert source.state("A/one").arming_workflows is None


def _query(**variables: object) -> dict[str, object]:
    return {"query": AUTO_MERGE_QUERY, "variables": {"owner": "A", "name": "one", **variables}}


def test_only_the_fixed_query_is_allowed():
    assert is_allowed_query(GRAPHQL, _query())
    assert is_allowed_query(GRAPHQL, _query(owner="A-b", name="c.d_e"))
    refused: list[tuple[str, object]] = [
        ("/graphql/", _query()),
        ("/graphqlx", _query()),
        ("/repos/A/one", _query()),
        (GRAPHQL, {**_query(), "query": AUTO_MERGE_QUERY + " "}),
        (
            GRAPHQL,
            {**_query(), "query": "mutation { enablePullRequestAutoMerge(input: {}) { a } }"},
        ),
        (GRAPHQL, {**_query(), "operationName": "x"}),
        (GRAPHQL, {"query": AUTO_MERGE_QUERY}),
        (GRAPHQL, _query(extra="x")),
        (GRAPHQL, {"query": AUTO_MERGE_QUERY, "variables": {"owner": "A"}}),
        (GRAPHQL, _query(name="one/two")),
        (GRAPHQL, _query(name="")),
        (GRAPHQL, _query(name="one\n")),
        (GRAPHQL, _query(owner=7)),
        (GRAPHQL, [AUTO_MERGE_QUERY]),
        (GRAPHQL, None),
    ]
    for path, body in refused:
        assert not is_allowed_query(path, body), (path, body)


def test_a_query_the_guard_refuses_is_never_sent(monkeypatch: pytest.MonkeyPatch):
    """The guard is consulted on the built request, before it is sent."""
    import membership_check.auto_merge as module

    monkeypatch.setattr(module, "is_allowed_query", lambda path, body: False)
    source, seen = _source(
        {REPO: httpx.Response(200, json={}), GRAPHQL: OFF, LISTING: httpx.Response(200, json=[])}
    )
    state = source.state("A/one")
    assert state.allowed is None
    assert "is not a query this reader may send" in state.detail
    assert f"POST {GRAPHQL}" not in seen
