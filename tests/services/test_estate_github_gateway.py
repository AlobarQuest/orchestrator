"""What the REAL gateway makes of what GitHub sends back.

The rest of this lane runs against an injected double, which is what lets the whole admission
cascade be exercised with no network -- and it means the parse between the wire and that double's
shape has, until now, had nothing looking at it. That parse is where a silent failure lives: a
truncated page or a swallowed error does not raise, it just answers with fewer runs, and fewer
runs is the direction that loses a FAILING one and reports a head as having reached no verdict.

Only the workflow-run read is covered here. The three older reads are left as they are rather than
retro-fitted: this file exists because a new parse arrived, not to open a gap nobody was standing
in.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from orchestrator.services.estate_landing_admission import EstateGatewayError, HeadCheckRun
from orchestrator.services.estate_pr_merge import GitHubEstatePullRequests

REPOSITORY = "alobarquest/change-manager"
HEAD = "e7e984b24978d0d41b40d4c400fc194da4b99a2b"


class _Recorder:
    def __init__(self, body: Any, status_code: int = 200) -> None:
        self._body = body
        self._status_code = status_code
        self.urls: list[str] = []

    def __call__(self, url: str, **_: Any) -> httpx.Response:
        self.urls.append(url)
        return httpx.Response(self._status_code, json=self._body)


def _gateway(
    monkeypatch: pytest.MonkeyPatch, body: Any, status_code: int = 200
) -> tuple[GitHubEstatePullRequests, _Recorder]:
    recorder = _Recorder(body, status_code)
    monkeypatch.setattr(httpx, "get", recorder)
    return GitHubEstatePullRequests(lambda: "token"), recorder


def _runs(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"total_count": len(rows), "workflow_runs": list(rows)}


def test_the_read_names_the_head_and_asks_for_a_full_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`per_page` is not decoration. One head legitimately carries several runs -- a workflow fires
    on both the push and the pull request, more than one workflow may watch a branch, and a re-run
    adds another. Every one of this estate's three live subjects carried two or three. A default
    page truncates silently, and truncation drops runs, which is how a failing one goes unseen."""
    gateway, recorder = _gateway(monkeypatch, _runs())

    gateway.head_check_runs(repository=REPOSITORY, head_sha=HEAD)

    assert recorder.urls == [
        f"https://api.github.com/repos/{REPOSITORY}/actions/runs?head_sha={HEAD}&per_page=100"
    ]


def test_every_row_is_carried_with_its_status_and_conclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`change-manager#61`'s shape as the platform served it on 2026-08-22."""
    gateway, _ = _gateway(
        monkeypatch,
        _runs(
            {"status": "completed", "conclusion": "success"},
            {"status": "completed", "conclusion": "cancelled"},
        ),
    )

    assert gateway.head_check_runs(repository=REPOSITORY, head_sha=HEAD) == (
        HeadCheckRun(status="completed", conclusion="success"),
        HeadCheckRun(status="completed", conclusion="cancelled"),
    )


def test_a_run_still_going_carries_no_conclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    """The platform sends `null`, and the cascade reads the absence of a conclusion as the run not
    having finished -- so it must survive the parse as `None` rather than as the string."""
    gateway, _ = _gateway(monkeypatch, _runs({"status": "in_progress", "conclusion": None}))

    assert gateway.head_check_runs(repository=REPOSITORY, head_sha=HEAD) == (
        HeadCheckRun(status="in_progress", conclusion=None),
    )


def test_a_head_with_no_runs_answers_an_empty_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinct from every error below: the platform answered, and the answer is that nothing ran.
    The cascade reads that as no verdict, which is true."""
    gateway, _ = _gateway(monkeypatch, _runs())

    assert gateway.head_check_runs(repository=REPOSITORY, head_sha=HEAD) == ()


@pytest.mark.parametrize(
    "body",
    [
        pytest.param([], id="a list where an object was expected"),
        pytest.param({"total_count": 0}, id="no workflow_runs key"),
        pytest.param({"workflow_runs": {}}, id="workflow_runs is not a list"),
        pytest.param({"workflow_runs": ["not-an-object"]}, id="a row that is not an object"),
        pytest.param({"workflow_runs": [{"conclusion": "success"}]}, id="a row with no status"),
        pytest.param({"workflow_runs": [{"status": 7}]}, id="a status that is not a string"),
    ],
)
def test_an_answer_that_cannot_be_read_RAISES_rather_than_answering_short(
    monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    """THE POLARITY OF THE PARSE, and it is the whole reason this file exists.

    Skipping a row this side cannot read would answer with a SHORTER list, and the caller cannot
    tell a short list from a complete one -- a head whose failing run was the unreadable row would
    read as having reached no verdict, and the lane would offer to freshen it. Refusing the whole
    answer turns that into `landing_checks_verdict_unreadable`, which refuses.
    """
    gateway, _ = _gateway(monkeypatch, body)

    with pytest.raises(EstateGatewayError):
        gateway.head_check_runs(repository=REPOSITORY, head_sha=HEAD)


def test_a_repository_the_remote_does_not_know_is_an_error_and_not_an_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_get` turns a 404 into `None`, which is right for a FILE that may legitimately be absent
    and wrong here: a repository that cannot be found has not told us its head reached no verdict.
    """
    gateway, _ = _gateway(monkeypatch, {"message": "Not Found"}, status_code=404)

    with pytest.raises(EstateGatewayError):
        gateway.head_check_runs(repository=REPOSITORY, head_sha=HEAD)
