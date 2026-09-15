"""The target repository's own declaration, read from GitHub (ADR-0015).

Every case goes through the real client with an injected transport, so the request itself is
observable: which route was asked, with which credential, and whether anything was asked at all.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from orchestrator.services.factory_target import (
    FILENAME,
    GitHubFactoryTargetSource,
)
from orchestrator.services.github_app import GitHubAppTokenError
from work_carrier.declaration import parse as carrier_parse

REPOSITORY = "AlobarQuest/intent-packages"
CONTENTS = f"/repos/{REPOSITORY}/contents/{FILENAME}"
TRUE_FILE = 'factory_target = true\nfactory_target_reason = "a dispatch target"\n'
FALSE_FILE = 'factory_target = false\nfactory_target_reason = "maintained by hand"\n'


def _source(
    handler: Callable[[httpx.Request], httpx.Response],
    token_provider: Callable[[], str] = lambda: "installation-token",
) -> tuple[GitHubFactoryTargetSource, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return GitHubFactoryTargetSource(token_provider, transport=httpx.MockTransport(record)), seen


def _routes(responses: dict[str, httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return responses[request.url.path]

    return handler


@pytest.mark.parametrize(("body", "expected"), [(TRUE_FILE, True), (FALSE_FILE, False)])
def test_a_declaration_is_read_from_the_default_branch_with_the_installation_token(
    body: str, expected: bool
) -> None:
    source, seen = _source(_routes({CONTENTS: httpx.Response(200, text=body)}))

    answer = source.declaration_for(REPOSITORY)

    assert answer.target is expected
    assert [request.url.path for request in seen] == [CONTENTS]
    assert seen[0].method == "GET"
    assert seen[0].headers["Authorization"] == "Bearer installation-token"
    assert seen[0].headers["Accept"] == "application/vnd.github.raw"
    assert "ref" not in seen[0].url.params


def test_an_absent_file_on_a_repository_that_answers_is_not_a_target() -> None:
    source, seen = _source(
        _routes(
            {
                CONTENTS: httpx.Response(404),
                f"/repos/{REPOSITORY}": httpx.Response(200, json={"full_name": REPOSITORY}),
            }
        )
    )

    answer = source.declaration_for(REPOSITORY)

    assert answer.target is False
    assert [request.url.path for request in seen] == [CONTENTS, f"/repos/{REPOSITORY}"]


@pytest.mark.parametrize("probe_status", [404, 403, 301, 429, 502])
def test_a_404_on_a_repository_that_does_not_answer_is_not_an_absence(probe_status: int) -> None:
    """The paired control for the case above: same contents answer, different repository
    answer, and only the repository answering lets `False` stand."""
    source, _ = _source(
        _routes(
            {
                CONTENTS: httpx.Response(404),
                f"/repos/{REPOSITORY}": httpx.Response(probe_status),
            }
        )
    )

    assert source.declaration_for(REPOSITORY).target is None


@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500, 502])
def test_any_other_status_is_could_not_tell_and_never_probes(status: int) -> None:
    source, seen = _source(_routes({CONTENTS: httpx.Response(status)}))

    assert source.declaration_for(REPOSITORY).target is None
    assert [request.url.path for request in seen] == [CONTENTS]


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        httpx.InvalidURL("bad"),
        UnicodeError("label too long"),
    ],
)
def test_nothing_the_transport_raises_escapes(error: Exception) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise error

    source, _ = _source(handler)

    answer = source.declaration_for(REPOSITORY)

    assert answer.target is None
    assert "installation-token" not in answer.reason


def test_a_token_that_cannot_be_minted_is_could_not_tell_and_asks_nothing() -> None:
    def refuse() -> str:
        raise GitHubAppTokenError("app_token_status")

    source, seen = _source(_routes({}), token_provider=refuse)

    assert source.declaration_for(REPOSITORY).target is None
    assert seen == []


@pytest.mark.parametrize(
    "repository",
    ["", "AlobarQuest", "AlobarQuest/a/b", "AlobarQuest/r?x=1", "../etc", "AlobarQuest/..", "a /b"],
)
def test_a_name_that_is_not_owner_slash_repo_is_refused_before_any_request(repository: str) -> None:
    source, seen = _source(_routes({}))

    assert source.declaration_for(repository).target is None
    assert seen == []


def test_the_name_guard_admits_the_estates_real_repository_names() -> None:
    """The control for the refusals above: a guard that refused everything would pass them."""
    for repository in ("AlobarQuest/brain", "AlobarQuest/infraops-mcp-server", "a.b/c_d"):
        source, seen = _source(
            _routes(
                {f"/repos/{repository}/contents/{FILENAME}": httpx.Response(200, text=TRUE_FILE)}
            )
        )
        assert source.declaration_for(repository).target is True
        assert len(seen) == 1


DECLARATIONS = [
    TRUE_FILE,
    FALSE_FILE,
    'factory_target = true\nfactory_target_reason = ""\n',
    'factory_target = true\nfactory_target_reason = "   "\n',
    "factory_target = true\n",
    'factory_target_reason = "no answer"\n',
    'factory_target = "true"\nfactory_target_reason = "a string"\n',
    'factory_target = 1\nfactory_target_reason = "an integer"\n',
    "factory_target = true\nfactory_target_reason = 7\n",
    "factory_target = [\n",
    "",
]


def _read(text: str):
    source, _ = _source(_routes({CONTENTS: httpx.Response(200, text=text)}))
    return source.declaration_for(REPOSITORY)


@pytest.mark.parametrize("text", DECLARATIONS)
def test_the_parse_agrees_with_the_work_carriers_on_every_shape(text: str) -> None:
    """Two readers of one file in one repository. They cannot share a module across the
    orchestrator boundary, so they are held to the same answers here instead."""
    assert _read(text).target is carrier_parse(text).target


def test_the_agreement_table_covers_all_three_answers() -> None:
    """Without this, a table that happened to hold only well-formed files would agree trivially."""
    answers = {_read(text).target for text in DECLARATIONS}
    assert answers == {True, False, None}
