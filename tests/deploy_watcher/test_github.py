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


# ---------------------------------------------------------------------------
# ADR-0044: choosing the newest successful push run.
# ---------------------------------------------------------------------------

_HEAD_A = "a" * 40
_HEAD_B = "b" * 40


def _runs(*items: dict) -> GitHubReader:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"workflow_runs": list(items)})

    return GitHubReader("t", transport=httpx.MockTransport(handler))


def _row(run_id: int, head: str, started: str | None) -> dict:
    return {
        "id": run_id,
        "head_sha": head,
        "status": "completed",
        "conclusion": "success",
        "run_started_at": started,
        "created_at": started,
        "updated_at": started,
    }


def test_a_run_with_no_start_time_beside_one_with_a_start_time_does_not_CRASH_the_lane() -> None:
    """GitHub's times are AWARE, so ordering an unstarted run against a started one by
    substituting a naive default raises `TypeError` -- which `superseded_by` does not catch and
    which would turn a measured finding into exit 1. The unstarted run is dropped instead: it
    cannot be the newest of anything, and losing it can only leave the finding standing."""
    reader = _runs(_row(1, _HEAD_A, None), _row(2, _HEAD_B, "2026-09-08T00:00:00Z"))
    newest = reader.newest_successful_push_run("owner/repo", ".github/workflows/ci.yml", "main")
    assert newest is not None
    assert newest.run_id == 2


def test_the_newest_is_chosen_by_START_TIME_and_not_by_the_order_github_served() -> None:
    """The stale-page measurement: GitHub served a six-day-old run first for this exact query."""
    reader = _runs(
        _row(1, _HEAD_A, "2026-09-08T00:00:00Z"), _row(2, _HEAD_B, "2026-09-02T00:00:00Z")
    )
    newest = reader.newest_successful_push_run("owner/repo", ".github/workflows/ci.yml", "main")
    assert newest is not None
    assert newest.run_id == 1
