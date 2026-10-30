"""What an application serves, read the way `revision_watcher` reads it (Landing Part 3b, Task 4).

The estate lander confirms production serves the bump's parent before it opens the revert, and the
revert's merge commit before it records the restore. It reads each application's health route
itself, through `revision_watcher`'s reader and parser rather than a copy of them.
"""

from __future__ import annotations

import httpx
import pytest

from estate_lander.served import ServedUnreadable, served_revision, serves

URL = "https://app-brain.devonwatkins.com/api/health"
SHA = "80b620ce1799c1eeeaac83f494e6cd9a553d5503"


def _answering(status: int, body: object) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == URL
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def test_the_served_revision_is_read_from_the_health_route() -> None:
    assert served_revision(URL, transport=_answering(200, {"revision": SHA})) == SHA


def test_an_unwell_application_still_names_what_it_serves() -> None:
    """A 503 whose body names the revision is an application serving that build, unwell."""
    assert served_revision(URL, transport=_answering(503, {"revision": SHA})) == SHA


def test_an_application_that_names_no_revision_answers_none() -> None:
    assert served_revision(URL, transport=_answering(200, {"status": "ok"})) is None


def test_an_application_that_does_not_answer_is_unreadable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ServedUnreadable):
        served_revision(URL, transport=httpx.MockTransport(handler))


def test_serving_a_commit_is_an_exact_full_commit_match_case_folded() -> None:
    assert serves(SHA.upper(), SHA)
    assert not serves(SHA[:12], SHA)
    assert not serves(None, SHA)
    assert not serves("", SHA)
