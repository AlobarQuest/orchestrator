"""The one GitHub read the lane makes, against a fake GitHub."""

from __future__ import annotations

import httpx
import pytest

from rotation_verifier.required_checks import RequiredChecksUnreadable, required_checks

PATH = "/repos/AlobarQuest/sds-canary/branches/main/protection/required_status_checks"


def _github(
    response: httpx.Response, seen: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return response

    return httpx.MockTransport(handler)


def test_contexts_are_sorted_and_the_read_is_authenticated() -> None:
    seen: list[httpx.Request] = []
    transport = _github(httpx.Response(200, json={"contexts": ["test", "Lint", "scan"]}), seen)

    names = required_checks("AlobarQuest/sds-canary", token="t0k", transport=transport)

    assert names == ("Lint", "scan", "test")
    assert seen[0].url.path == PATH
    assert seen[0].headers["Authorization"] == "Bearer t0k"


def test_a_404_is_unreadable() -> None:
    with pytest.raises(RequiredChecksUnreadable, match="404"):
        required_checks(
            "AlobarQuest/sds-canary", token="t", transport=_github(httpx.Response(404, json={}))
        )


def test_an_empty_200_returns_nothing() -> None:
    transport = _github(httpx.Response(200, json={"contexts": []}))
    assert required_checks("AlobarQuest/sds-canary", token="t", transport=transport) == ()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json=["a"]),
        httpx.Response(200, json={"checks": []}),
        httpx.Response(200, json={"contexts": [1]}),
    ],
)
def test_an_unreadable_body_is_unreadable_not_empty(response: httpx.Response) -> None:
    with pytest.raises(RequiredChecksUnreadable):
        required_checks("AlobarQuest/sds-canary", token="t", transport=_github(response))


def test_a_repository_that_is_not_owner_slash_name_never_leaves_the_process() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(RequiredChecksUnreadable, match="may not"):
        required_checks(
            "AlobarQuest/x/../../user", token="t", transport=_github(httpx.Response(200), seen)
        )
    assert seen == []
