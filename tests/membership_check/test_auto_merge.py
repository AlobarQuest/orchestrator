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
    AutoMergeState,
    GitHubAutoMergeSource,
    arming,
    is_allowed,
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


REPO = "/repos/A/one"
LISTING = f"{REPO}/contents/.github/workflows"


def _source(routes: dict[str, httpx.Response]) -> tuple[GitHubAutoMergeSource, list[str]]:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        seen.append(request.url.path)
        return routes.get(request.url.path, httpx.Response(404))

    return GitHubAutoMergeSource(token="t", transport=httpx.MockTransport(handle)), seen


def _entry(name: str, kind: str = "file") -> dict[str, str]:
    return {"name": name, "type": kind, "path": f".github/workflows/{name}"}


def test_a_repository_with_auto_merge_off_and_quiet_workflows_reads_clean():
    source, seen = _source(
        {
            REPO: httpx.Response(200, json={"allow_auto_merge": False}),
            LISTING: httpx.Response(200, json=[_entry("ci.yml"), _entry("nested", "dir")]),
            f"{LISTING}/ci.yml": httpx.Response(200, text="run: make check\n"),
        }
    )
    assert source.state("A/one") == AutoMergeState(False, ())
    assert all(is_allowed(path) for path in seen)


def test_an_arming_workflow_is_named():
    source, _ = _source(
        {
            REPO: httpx.Response(200, json={"allow_auto_merge": True}),
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
    source, _ = _source({REPO: httpx.Response(200, json={"allow_auto_merge": False})})
    assert source.state("A/one") == AutoMergeState(False, ())


def test_a_setting_the_credential_is_not_shown_is_unread_not_off():
    source, _ = _source({REPO: httpx.Response(200, json={}), LISTING: httpx.Response(200, json=[])})
    state = source.state("A/one")
    assert state.allowed is None and state.arming_workflows == ()


def test_an_unreadable_repository_reads_nothing_else():
    source, seen = _source({REPO: httpx.Response(503)})
    state = source.state("A/one")
    assert state.allowed is None and state.arming_workflows is None
    assert seen == [REPO]


@pytest.mark.parametrize("status", [403, 429, 500])
def test_a_failed_listing_is_unread_not_empty(status: int):
    source, _ = _source(
        {
            REPO: httpx.Response(200, json={"allow_auto_merge": False}),
            LISTING: httpx.Response(status),
        }
    )
    assert source.state("A/one").arming_workflows is None


def test_a_failed_file_read_is_unread_not_quiet():
    source, _ = _source(
        {
            REPO: httpx.Response(200, json={"allow_auto_merge": False}),
            LISTING: httpx.Response(200, json=[_entry("ci.yml")]),
        }
    )
    assert source.state("A/one").arming_workflows is None


def test_a_listed_name_that_would_leave_the_directory_is_refused_unsent():
    source, seen = _source(
        {
            REPO: httpx.Response(200, json={"allow_auto_merge": False}),
            LISTING: httpx.Response(200, json=[_entry("../../../x")]),
        }
    )
    assert source.state("A/one").arming_workflows is None
    assert all(is_allowed(path) for path in seen)


@pytest.mark.parametrize("slug", ["../..", "o/r?x=1", "o", "o/r/s", "o/r\n"])
def test_a_slug_that_composes_another_route_is_refused_unsent(slug: str):
    source, seen = _source({})
    state = source.state(slug)
    assert state == AutoMergeState(None, None, state.detail)
    assert seen == []


def test_a_transport_fault_is_unread():
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    source = GitHubAutoMergeSource(token="t", transport=httpx.MockTransport(fail))
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
            REPO: httpx.Response(200, json={"allow_auto_merge": False}),
            LISTING: httpx.Response(200, content=json.dumps({"name": "x"}).encode()),
        }
    )
    assert source.state("A/one").arming_workflows is None
