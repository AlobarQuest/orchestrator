"""What the REAL gateway makes of what GitHub sends back.

The rest of this lane runs against an injected double, which is what lets the whole admission
cascade be exercised with no network -- and it means the parse between the wire and that double's
shape has, until now, had nothing looking at it. That parse is where a silent failure lives: a
truncated page or a swallowed error does not raise, it just answers with fewer runs, and fewer
runs is the direction that loses a FAILING one and reports a head as having reached no verdict.

Covered here: the workflow-run read, and (Landing Part 3a) the jobs-at-a-commit read and the
rerun. The three older reads are left as they are rather than retro-fitted: this file exists
because a new parse arrived, not to open a gap nobody was standing in.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from orchestrator.services.landing.estate_pr_merge import GitHubEstatePullRequests
from orchestrator.services.landing.interfaces import (
    EstateGatewayError,
    HeadCheckRun,
    Job,
)

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


# --- Landing Part 3a: the jobs at a commit, and one rerun of a run's failed jobs. ---

MERGE = "2222222222222222222222222222222222222222"
API = f"https://api.github.com/repos/{REPOSITORY}/actions"


class _Router:
    """Answers each GET by its exact URL; an unlisted URL is a test bug, not a 404."""

    def __init__(self, routes: dict[str, tuple[int, Any]]) -> None:
        self._routes = routes
        self.urls: list[str] = []

    def __call__(self, url: str, **_: Any) -> httpx.Response:
        self.urls.append(url)
        status, body = self._routes[url]
        return httpx.Response(status, json=body)


def _runs_url(sha: str, page: int = 1) -> str:
    return f"{API}/runs?head_sha={sha}&per_page=100&page={page}"


def _jobs_url(run_id: int, page: int = 1) -> str:
    return f"{API}/runs/{run_id}/jobs?filter=all&per_page=100&page={page}"


def _run(run_id: int, workflow: str = "CI", event: str = "push") -> dict[str, Any]:
    return {"id": run_id, "name": workflow, "event": event}


def _job_row(name: str, conclusion: str | None, attempt: int) -> dict[str, Any]:
    return {"name": name, "conclusion": conclusion, "run_attempt": attempt}


def _jobs_gateway(
    monkeypatch: pytest.MonkeyPatch, routes: dict[str, tuple[int, Any]]
) -> tuple[GitHubEstatePullRequests, _Router]:
    router = _Router(routes)
    monkeypatch.setattr(httpx, "get", router)
    return GitHubEstatePullRequests(lambda: "token"), router


def test_jobs_at_reads_every_run_at_the_commit_and_keeps_each_jobs_latest_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`filter=all` and the reduction here, rather than the platform's `latest`: what `latest`
    lists after a rerun of failed jobs only is not something this side has measured, and keeping
    the highest `run_attempt` per job is right under either answer."""
    gateway, _ = _jobs_gateway(
        monkeypatch,
        {
            _runs_url(MERGE): (
                200,
                {
                    "total_count": 2,
                    "workflow_runs": [_run(11), _run(12, "Dependabot Updates", "dynamic")],
                },
            ),
            _jobs_url(11): (
                200,
                {
                    "total_count": 3,
                    "jobs": [
                        _job_row("test", "failure", 1),
                        _job_row("test", "success", 2),
                        _job_row("lint", "success", 1),
                    ],
                },
            ),
            _jobs_url(12): (200, {"total_count": 1, "jobs": [_job_row("build", None, 1)]}),
        },
    )

    assert set(gateway.jobs_at(repository=REPOSITORY, sha=MERGE)) == {
        Job(name="test", conclusion="success", run_id=11, attempt=2, workflow="CI", event="push"),
        Job(name="lint", conclusion="success", run_id=11, attempt=1, workflow="CI", event="push"),
        Job(
            name="build",
            conclusion=None,
            run_id=12,
            attempt=1,
            workflow="Dependabot Updates",
            event="dynamic",
        ),
    }


def test_jobs_at_follows_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A matrix run carries more jobs than one default page, and a dropped page drops a failure."""
    first = [_job_row(f"test ({n})", "success", 1) for n in range(100)]
    gateway, router = _jobs_gateway(
        monkeypatch,
        {
            _runs_url(MERGE): (200, {"total_count": 1, "workflow_runs": [_run(11)]}),
            _jobs_url(11): (200, {"total_count": 101, "jobs": first}),
            _jobs_url(11, page=2): (
                200,
                {"total_count": 101, "jobs": [_job_row("test (100)", "failure", 1)]},
            ),
        },
    )

    jobs = gateway.jobs_at(repository=REPOSITORY, sha=MERGE)

    assert len(jobs) == 101
    assert (
        Job(
            name="test (100)",
            conclusion="failure",
            run_id=11,
            attempt=1,
            workflow="CI",
            event="push",
        )
        in jobs
    )
    assert _jobs_url(11, page=2) in router.urls


@pytest.mark.parametrize(
    "routes",
    [
        pytest.param({_runs_url(MERGE): (404, {"message": "Not Found"})}, id="runs 404"),
        pytest.param(
            {_runs_url(MERGE): (200, {"total_count": 1, "workflow_runs": [{}]})},
            id="a run with no id",
        ),
        pytest.param(
            {
                _runs_url(MERGE): (200, {"total_count": 1, "workflow_runs": [_run(11)]}),
                _jobs_url(11): (404, {"message": "Not Found"}),
            },
            id="jobs 404",
        ),
        pytest.param(
            {
                _runs_url(MERGE): (
                    200,
                    {"total_count": 1, "workflow_runs": [{"id": 11, "name": "CI"}]},
                )
            },
            id="a run with no event",
        ),
        pytest.param(
            {
                _runs_url(MERGE): (
                    200,
                    {"total_count": 1, "workflow_runs": [{"id": 11, "event": "push"}]},
                )
            },
            id="a run with no workflow name",
        ),
        pytest.param(
            {
                _runs_url(MERGE): (200, {"total_count": 1, "workflow_runs": [_run(11)]}),
                _jobs_url(11): (200, {"total_count": 1, "jobs": [{"conclusion": "success"}]}),
            },
            id="a job with no name",
        ),
        pytest.param(
            {
                _runs_url(MERGE): (200, {"total_count": 1, "workflow_runs": [_run(11)]}),
                _jobs_url(11): (200, {"total_count": 1, "jobs": [{"name": "test"}]}),
            },
            id="a job with no attempt",
        ),
        pytest.param(
            {
                _runs_url(MERGE): (200, {"total_count": 1, "workflow_runs": [_run(11)]}),
                _jobs_url(11): (200, {"total_count": 2, "jobs": [_job_row("test", None, 1)]}),
            },
            id="fewer jobs than the total says",
        ),
    ],
)
def test_jobs_at_raises_rather_than_answering_short(
    monkeypatch: pytest.MonkeyPatch, routes: dict[str, tuple[int, Any]]
) -> None:
    """A short list of jobs is how a failing one goes unseen and a bad landing reads clean."""
    gateway, _ = _jobs_gateway(monkeypatch, routes)

    with pytest.raises(EstateGatewayError):
        gateway.jobs_at(repository=REPOSITORY, sha=MERGE)


def test_jobs_at_turns_a_malformed_host_into_a_gateway_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(url: str, **_: Any) -> httpx.Response:
        raise UnicodeError("label empty or too long")

    monkeypatch.setattr(httpx, "get", explode)

    with pytest.raises(EstateGatewayError):
        GitHubEstatePullRequests(lambda: "token").jobs_at(repository=REPOSITORY, sha=MERGE)


class _Poster:
    def __init__(self, status_code: int = 201, error: Exception | None = None) -> None:
        self._status_code = status_code
        self._error = error
        self.urls: list[str] = []

    def __call__(self, url: str, **_: Any) -> httpx.Response:
        self.urls.append(url)
        if self._error is not None:
            raise self._error
        return httpx.Response(self._status_code, json={})


def test_a_rerun_names_the_run_and_accepts_201(monkeypatch: pytest.MonkeyPatch) -> None:
    """The platform answers a rerun with 201 Created, not 200."""
    poster = _Poster()
    monkeypatch.setattr(httpx, "post", poster)

    GitHubEstatePullRequests(lambda: "token").rerun_failed_jobs(
        repository=REPOSITORY, run_id=18_000_000_001
    )

    assert poster.urls == [f"{API}/runs/18000000001/rerun-failed-jobs"]


@pytest.mark.parametrize(
    "poster",
    [
        pytest.param(_Poster(status_code=403), id="refused"),
        pytest.param(_Poster(status_code=200), id="not created"),
        pytest.param(_Poster(error=httpx.ConnectTimeout("slow")), id="timeout"),
        pytest.param(_Poster(error=UnicodeError("bad host")), id="malformed host"),
    ],
)
def test_a_rerun_that_did_not_happen_raises(
    monkeypatch: pytest.MonkeyPatch, poster: _Poster
) -> None:
    monkeypatch.setattr(httpx, "post", poster)

    with pytest.raises(EstateGatewayError):
        GitHubEstatePullRequests(lambda: "token").rerun_failed_jobs(
            repository=REPOSITORY, run_id=18_000_000_001
        )


def _commit_gateway(
    monkeypatch: pytest.MonkeyPatch, status: int, body: Any
) -> tuple[GitHubEstatePullRequests, _Router]:
    return _jobs_gateway(
        monkeypatch, {f"https://api.github.com/repos/{REPOSITORY}/commits/{MERGE}": (status, body)}
    )


def test_parent_of_a_squash_merge_is_its_single_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, _ = _commit_gateway(monkeypatch, 200, {"sha": MERGE, "parents": [{"sha": HEAD}]})

    assert gateway.parent_of(repository=REPOSITORY, sha=MERGE) == HEAD


@pytest.mark.parametrize(
    ("status", "body"),
    [
        pytest.param(200, {"sha": MERGE, "parents": []}, id="a root commit"),
        pytest.param(
            200, {"sha": MERGE, "parents": [{"sha": HEAD}, {"sha": MERGE}]}, id="a merge commit"
        ),
        pytest.param(200, {"sha": MERGE}, id="no parents key"),
        pytest.param(200, {"sha": MERGE, "parents": [{}]}, id="a parent with no sha"),
        pytest.param(404, {"message": "Not Found"}, id="no such commit"),
        pytest.param(502, {"message": "Bad Gateway"}, id="the remote failed"),
    ],
)
def test_parent_of_refuses_anything_but_exactly_one_parent(
    monkeypatch: pytest.MonkeyPatch, status: int, body: Any
) -> None:
    """A squash merge has one parent. Zero or two means this is not the commit the lane landed, and
    reverting to either answer would be reverting to a guess."""
    gateway, _ = _commit_gateway(monkeypatch, status, body)

    with pytest.raises(EstateGatewayError):
        gateway.parent_of(repository=REPOSITORY, sha=MERGE)
