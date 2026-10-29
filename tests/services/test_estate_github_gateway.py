"""What the REAL gateway makes of what GitHub sends back.

The rest of this lane runs against an injected double, which is what lets the whole admission
cascade be exercised with no network -- and it means the parse between the wire and that double's
shape has, until now, had nothing looking at it. That parse is where a silent failure lives: a
truncated page or a swallowed error does not raise, it just answers with fewer runs, and fewer
runs is the direction that loses a FAILING one and reports a head as having reached no verdict.

Covered here: the workflow-run read, and (Landing Part 3a) the jobs-at-a-commit read, the
rerun, and the git data reads and writes that build a revert. The three older reads are left as
they are rather than retro-fitted: this file exists because a new parse arrived, not to open a gap
nobody was standing in.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from orchestrator.services.landing.estate_pr_merge import GitHubEstatePullRequests
from orchestrator.services.landing.interfaces import (
    BranchHead,
    EstateGatewayError,
    HeadCheckRun,
    Job,
    Tree,
    TreeChange,
    TreeEntry,
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


# Landing Part 3a: building a revert through the git data API.
REPO_API = f"https://api.github.com/repos/{REPOSITORY}"
TREE_SHA = "5555555555555555555555555555555555555555"


def _tree_routes(tree_body: Any, tree_status: int = 200) -> dict[str, tuple[int, Any]]:
    return {
        f"{REPO_API}/git/commits/{MERGE}": (200, {"sha": MERGE, "tree": {"sha": TREE_SHA}}),
        f"{REPO_API}/git/trees/{TREE_SHA}?recursive=1": (tree_status, tree_body),
    }


def _row(path: str, mode: str, kind: str, sha: str) -> dict[str, Any]:
    return {"path": path, "mode": mode, "type": kind, "sha": sha}


def test_tree_reads_the_commits_tree_and_keeps_every_leaf_with_its_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The commit is read first: given a commit, the trees endpoint echoes the commit's sha, and a
    new tree's base must be a tree. Directory rows are dropped; modes and types survive."""
    gateway, router = _jobs_gateway(
        monkeypatch,
        _tree_routes(
            {
                "sha": TREE_SHA,
                "truncated": False,
                "tree": [
                    _row("scripts", "040000", "tree", "1" * 40),
                    _row("scripts/run.sh", "100755", "blob", "2" * 40),
                    _row("vendor/lib", "160000", "commit", "3" * 40),
                    _row("link", "120000", "blob", "4" * 40),
                ],
            }
        ),
    )

    tree = gateway.tree(repository=REPOSITORY, commit_sha=MERGE)

    assert router.urls == list(_tree_routes({}).keys())
    assert tree == Tree(
        sha=TREE_SHA,
        entries={
            "scripts/run.sh": TreeEntry("100755", "blob", "2" * 40),
            "vendor/lib": TreeEntry("160000", "commit", "3" * 40),
            "link": TreeEntry("120000", "blob", "4" * 40),
        },
    )


@pytest.mark.parametrize(
    ("status", "body"),
    [
        pytest.param(200, {"sha": TREE_SHA, "truncated": True, "tree": []}, id="truncated"),
        pytest.param(200, {"sha": TREE_SHA, "tree": []}, id="no truncated flag"),
        pytest.param(200, {"sha": TREE_SHA, "truncated": False}, id="no rows"),
        pytest.param(
            200,
            {"sha": TREE_SHA, "truncated": False, "tree": [{"path": "a", "type": "blob"}]},
            id="a row with no sha",
        ),
        pytest.param(404, {"message": "Not Found"}, id="no such tree"),
        pytest.param(502, {"message": "Bad Gateway"}, id="the remote failed"),
    ],
)
def test_a_tree_that_cannot_be_read_whole_raises(
    monkeypatch: pytest.MonkeyPatch, status: int, body: Any
) -> None:
    """A tree missing entries would read a path the bump touched as untouched."""
    gateway, _ = _jobs_gateway(monkeypatch, _tree_routes(body, status))

    with pytest.raises(EstateGatewayError):
        gateway.tree(repository=REPOSITORY, commit_sha=MERGE)


def test_tree_raises_on_a_commit_it_cannot_read(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, _ = _jobs_gateway(
        monkeypatch, {f"{REPO_API}/git/commits/{MERGE}": (404, {"message": "Not Found"})}
    )

    with pytest.raises(EstateGatewayError):
        gateway.tree(repository=REPOSITORY, commit_sha=MERGE)


def test_default_branch_head_reads_the_name_then_its_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, _ = _jobs_gateway(
        monkeypatch,
        {
            REPO_API: (200, {"default_branch": "main"}),
            f"{REPO_API}/git/ref/heads/main": (200, {"object": {"sha": HEAD}}),
        },
    )

    assert gateway.default_branch_head(repository=REPOSITORY) == BranchHead(name="main", sha=HEAD)


@pytest.mark.parametrize(
    "routes",
    [
        pytest.param({REPO_API: (404, {})}, id="no such repository"),
        pytest.param({REPO_API: (200, {"default_branch": ""})}, id="no default branch"),
        pytest.param(
            {
                REPO_API: (200, {"default_branch": "main"}),
                f"{REPO_API}/git/ref/heads/main": (404, {}),
            },
            id="no such ref",
        ),
    ],
)
def test_default_branch_head_raises_when_unreadable(
    monkeypatch: pytest.MonkeyPatch, routes: dict[str, tuple[int, Any]]
) -> None:
    gateway, _ = _jobs_gateway(monkeypatch, routes)

    with pytest.raises(EstateGatewayError):
        gateway.default_branch_head(repository=REPOSITORY)


class _Writer:
    """Answers every POST with one status and body; records each URL and payload."""

    def __init__(self, status_code: int, body: Any) -> None:
        self._status_code = status_code
        self._body = body
        self.sent: list[tuple[str, Any]] = []

    def __call__(self, url: str, *, json: Any, **_: Any) -> httpx.Response:
        self.sent.append((url, json))
        return httpx.Response(self._status_code, json=self._body)


def _writer(
    monkeypatch: pytest.MonkeyPatch, status_code: int, body: Any
) -> tuple[GitHubEstatePullRequests, _Writer]:
    writer = _Writer(status_code, body)
    monkeypatch.setattr(httpx, "post", writer)
    return GitHubEstatePullRequests(lambda: "token"), writer


def test_create_tree_sends_each_change_and_a_null_sha_for_a_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, writer = _writer(monkeypatch, 201, {"sha": "new-tree"})

    answer = gateway.create_tree(
        repository=REPOSITORY,
        base_tree=TREE_SHA,
        changes=(
            TreeChange(path="run.sh", mode="100755", type="blob", sha="2" * 40),
            TreeChange(path="added.txt", mode="100644", type="blob", sha=None),
        ),
    )

    assert answer == "new-tree"
    assert writer.sent == [
        (
            f"{REPO_API}/git/trees",
            {
                "base_tree": TREE_SHA,
                "tree": [
                    {"path": "run.sh", "mode": "100755", "type": "blob", "sha": "2" * 40},
                    {"path": "added.txt", "mode": "100644", "type": "blob", "sha": None},
                ],
            },
        )
    ]


def test_create_commit_names_one_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, writer = _writer(monkeypatch, 201, {"sha": MERGE})

    assert (
        gateway.create_commit(repository=REPOSITORY, message="m", tree=TREE_SHA, parent=HEAD)
        == MERGE
    )
    assert writer.sent == [
        (f"{REPO_API}/git/commits", {"message": "m", "tree": TREE_SHA, "parents": [HEAD]})
    ]


def test_create_branch_creates_a_full_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, writer = _writer(monkeypatch, 201, {"ref": "refs/heads/sds-revert/pr-12"})

    gateway.create_branch(repository=REPOSITORY, branch="sds-revert/pr-12", sha=MERGE)

    assert writer.sent == [
        (f"{REPO_API}/git/refs", {"ref": "refs/heads/sds-revert/pr-12", "sha": MERGE})
    ]


def test_open_pull_request_answers_its_number(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, writer = _writer(monkeypatch, 201, {"number": 77})

    number = gateway.open_pull_request(
        repository=REPOSITORY, title="t", body="b", head="sds-revert/pr-12", base="main"
    )

    assert number == 77
    assert writer.sent == [
        (
            f"{REPO_API}/pulls",
            {"title": "t", "body": "b", "head": "sds-revert/pr-12", "base": "main"},
        )
    ]


@pytest.mark.parametrize(
    ("call", "body"),
    [
        pytest.param(
            lambda g: g.create_tree(repository=REPOSITORY, base_tree=TREE_SHA, changes=()),
            {"sha": "x"},
            id="tree",
        ),
        pytest.param(
            lambda g: g.create_commit(repository=REPOSITORY, message="m", tree="t", parent=HEAD),
            {"sha": "x"},
            id="commit",
        ),
        pytest.param(
            lambda g: g.create_branch(repository=REPOSITORY, branch="b", sha=MERGE),
            {},
            id="branch",
        ),
        pytest.param(
            lambda g: g.open_pull_request(
                repository=REPOSITORY, title="t", body="b", head="h", base="main"
            ),
            {"number": 1},
            id="pull request",
        ),
    ],
)
@pytest.mark.parametrize("status", [200, 422])
def test_a_write_answered_with_anything_but_201_raises(
    monkeypatch: pytest.MonkeyPatch, call: Any, body: Any, status: int
) -> None:
    """422 is how the platform refuses a branch that already exists: never treated as created."""
    gateway, _ = _writer(monkeypatch, status, body)

    with pytest.raises(EstateGatewayError):
        call(gateway)


@pytest.mark.parametrize(
    ("call", "body"),
    [
        pytest.param(
            lambda g: g.create_tree(repository=REPOSITORY, base_tree=TREE_SHA, changes=()),
            {},
            id="tree with no sha",
        ),
        pytest.param(
            lambda g: g.create_commit(repository=REPOSITORY, message="m", tree="t", parent=HEAD),
            {"sha": ""},
            id="commit with an empty sha",
        ),
        pytest.param(
            lambda g: g.open_pull_request(
                repository=REPOSITORY, title="t", body="b", head="h", base="main"
            ),
            {"number": True},
            id="pull request with no number",
        ),
    ],
)
def test_a_created_object_that_names_nothing_raises(
    monkeypatch: pytest.MonkeyPatch, call: Any, body: Any
) -> None:
    gateway, _ = _writer(monkeypatch, 201, body)

    with pytest.raises(EstateGatewayError):
        call(gateway)


def test_a_write_to_a_malformed_host_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(url: str, **_: Any) -> httpx.Response:
        raise UnicodeError("label empty or too long")

    monkeypatch.setattr(httpx, "post", explode)

    with pytest.raises(EstateGatewayError):
        GitHubEstatePullRequests(lambda: "token").create_branch(
            repository=REPOSITORY, branch="b", sha=MERGE
        )
