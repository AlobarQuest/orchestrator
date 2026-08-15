"""The watcher's read-only GitHub surface.

ADR-0019 increment 5b adds `pull_request_disposition`, the read the producer's reconciliation
sweep needs: telling "closed without merging" from "still waiting" and from "landed" is what lets
a record be retired on a FACT rather than on an absence.
"""

from __future__ import annotations

import httpx
import pytest

from deploy_watcher.github import GitHubReader, ReadError

# ---------------------------------------------------------------------------
# ADR-0019 increment 5b: where a pull request ended up.
# ---------------------------------------------------------------------------


def _dispositions(body: object, status: int = 200) -> GitHubReader:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return GitHubReader("t", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"number": 42, "merged": False, "state": "closed"}, "closed_unmerged"),
        ({"number": 42, "merged": True, "state": "closed"}, "merged"),
        ({"number": 42, "merged": False, "state": "open"}, "open"),
        # GitHub reports a merged pull request as closed; `merged` decides, and it is read first.
        ({"number": 42, "merged": True, "state": "open"}, "merged"),
    ],
)
def test_the_three_dispositions_are_told_apart(body: dict, expected: str) -> None:
    assert _dispositions(body).pull_request_disposition("owner/repo", 42) == expected


def test_a_pull_request_github_does_not_have_answers_None_rather_than_closed() -> None:
    """`None` is a statement about the QUESTION, not about the subject, and a caller must not read
    it as a reason to retire anything."""
    assert _dispositions(None, status=404).pull_request_disposition("owner/repo", 42) is None


def test_an_answer_about_a_different_pull_request_is_refused() -> None:
    with pytest.raises(ReadError):
        _dispositions({"number": 43, "state": "closed"}).pull_request_disposition("owner/repo", 42)


def test_an_unrecognised_state_is_refused_rather_than_read_as_closed() -> None:
    """Reading an unknown word as closed would retire a record on a shape nobody classified."""
    with pytest.raises(ReadError):
        _dispositions({"number": 42, "merged": False, "state": "?"}).pull_request_disposition(
            "owner/repo", 42
        )
