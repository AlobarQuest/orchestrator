"""What the orchestrator does with GitHub's answer about a named job.

Every case here is about a `CheckObservation` being produced or refused. The rule the whole
module serves is that there is no third option — nothing falls back to what a caller said, so a
GitHub that is unreachable, unreadable, silent, or self-contradictory must raise.
"""

from typing import Any

import httpx
import pytest

from orchestrator.services.github_app import GitHubAppTokenError
from orchestrator.services.verifier import github_checks
from orchestrator.services.verifier.github_checks import (
    CheckObservationError,
    GitHubActionsCheckObserver,
)

REPOSITORY = "AlobarQuest/change-manager"
HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
CHECK_NAME = "Lint, type-check, and test"
TOKEN = "ghs_installationtoken"


class FakeGitHub:
    """Answers the runs route and the jobs route, and records what it was asked."""

    def __init__(
        self,
        *,
        runs: object,
        jobs: dict[str, object] | None = None,
        runs_status: int = 200,
        jobs_status: int = 200,
    ) -> None:
        self.runs = runs
        self.jobs = jobs or {}
        self.runs_status = runs_status
        self.jobs_status = jobs_status
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path.endswith("/jobs"):
            run_id = path.rsplit("/", 2)[-2]
            return _answer(self.jobs_status, self.jobs.get(run_id, {"jobs": []}))
        return _answer(self.runs_status, self.runs)


def _answer(status: int, payload: object) -> httpx.Response:
    if isinstance(payload, Exception):
        return httpx.Response(status, content=b"not json")
    return httpx.Response(status, json=payload)


def run(run_id: int) -> dict[str, object]:
    return {
        "id": run_id,
        "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
    }


def job(conclusion: str | None, *, name: str = CHECK_NAME, job_id: int = 5) -> dict[str, object]:
    return {
        "id": job_id,
        "name": name,
        "conclusion": conclusion,
        "html_url": f"https://github.com/{REPOSITORY}/actions/jobs/{job_id}",
    }


def runs(*ids: int) -> dict[str, object]:
    return {"total_count": len(ids), "workflow_runs": [run(identifier) for identifier in ids]}


def jobs(*entries: dict[str, object]) -> dict[str, object]:
    return {"total_count": len(entries), "jobs": list(entries)}


def _observer(handler: Any) -> GitHubActionsCheckObserver:
    return GitHubActionsCheckObserver(lambda: TOKEN, transport=httpx.MockTransport(handler))


def observe(github: Any, repository: str = REPOSITORY) -> Any:
    return _observer(github).observe(
        repository=repository,
        head_sha=HEAD_SHA,
        check_name=CHECK_NAME,
    )


def test_a_concluded_job_is_observed() -> None:
    github = FakeGitHub(runs=runs(11), jobs={"11": jobs(job("success"))})

    observation = observe(github)

    assert observation.conclusion == "success"
    assert observation.check_name == CHECK_NAME
    assert len(observation.jobs) == 1
    assert observation.jobs[0].run_id == "11"
    assert observation.jobs[0].job_id == "5"
    # It asked about the head it was given, and it authenticated as the App.
    assert github.calls[0].url.params["head_sha"] == HEAD_SHA
    assert github.calls[0].headers["Authorization"] == f"Bearer {TOKEN}"


def test_only_the_named_job_is_looked_at() -> None:
    """A failing job of another name on the same run must not decide this criterion."""
    github = FakeGitHub(
        runs=runs(11),
        jobs={"11": jobs(job("failure", name="Runner brief compatibility"), job("success"))},
    )

    observation = observe(github)

    assert observation.conclusion == "success"
    assert len(observation.jobs) == 1


def test_two_agreeing_jobs_resolve_and_are_both_carried() -> None:
    """The push-plus-pull-request case, which is the template default in this estate."""
    github = FakeGitHub(
        runs=runs(11, 22),
        jobs={"11": jobs(job("success", job_id=1)), "22": jobs(job("success", job_id=2))},
    )

    observation = observe(github)

    assert observation.conclusion == "success"
    assert [item.run_id for item in observation.jobs] == ["22", "11"]


def test_two_disagreeing_jobs_are_refused() -> None:
    """One green and one red on the same head. Neither newest-wins nor first-wins is honest."""
    github = FakeGitHub(
        runs=runs(11, 22),
        jobs={"11": jobs(job("success", job_id=1)), "22": jobs(job("failure", job_id=2))},
    )

    with pytest.raises(CheckObservationError) as excinfo:
        observe(github)

    assert excinfo.value.code == "ambiguous"
    assert excinfo.value.detail == "failure,success"


def test_a_name_that_matches_nothing_is_refused() -> None:
    github = FakeGitHub(runs=runs(11), jobs={"11": jobs(job("success", name="Something Else"))})

    with pytest.raises(CheckObservationError) as excinfo:
        observe(github)

    assert excinfo.value.code == "not_found"


def test_a_head_with_no_runs_at_all_is_refused() -> None:
    github = FakeGitHub(runs=runs())

    with pytest.raises(CheckObservationError) as excinfo:
        observe(github)

    assert excinfo.value.code == "not_found"


def test_a_job_still_running_is_refused_as_unconcluded() -> None:
    """Distinct from not-found: the caller should wait, not rename."""
    github = FakeGitHub(runs=runs(11), jobs={"11": jobs(job(None))})

    with pytest.raises(CheckObservationError) as excinfo:
        observe(github)

    assert excinfo.value.code == "not_concluded"


def test_more_matches_than_the_bound_are_refused() -> None:
    identifiers = tuple(range(1, github_checks.NAMED_CHECK_MAX_OBSERVED_JOBS + 2))
    github = FakeGitHub(
        runs=runs(*identifiers),
        jobs={str(i): jobs(job("success", job_id=i)) for i in identifiers},
    )

    with pytest.raises(CheckObservationError) as excinfo:
        observe(github)

    assert excinfo.value.code == "ambiguous"


@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_a_non_200_is_refused_and_carries_only_the_status(
    status: int,
) -> None:
    """403 is the measured answer for the Checks API; it must not become a soft pass."""
    github = FakeGitHub(runs={"secret": "an unaudited error body"}, runs_status=status)

    with pytest.raises(CheckObservationError) as excinfo:
        observe(github)

    assert excinfo.value.code == "unavailable"
    assert excinfo.value.detail == f"status:{status}"
    assert "unaudited" not in str(excinfo.value)


def test_an_unreachable_github_is_refused() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(CheckObservationError) as excinfo:
        observe(explode)

    assert excinfo.value.code == "unavailable"
    assert excinfo.value.detail == "request_error:ConnectError"


def test_a_host_that_can_t_be_encoded_is_a_refusal_not_a_bare_500() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise UnicodeError("label too long")

    with pytest.raises(CheckObservationError) as excinfo:
        observe(explode)

    assert excinfo.value.detail == "request_error:UnicodeError"


@pytest.mark.parametrize(
    "repository",
    ["not-a-repository", "o/r/extra", "o/..", "../r", "o/r?x=1", "o/r#x", "o /r", ""],
)
def test_a_malformed_repository_is_a_refusal_and_nothing_is_sent(repository: str) -> None:
    github = FakeGitHub(runs=runs(11))

    with pytest.raises(CheckObservationError) as excinfo:
        observe(github, repository)

    assert excinfo.value.code == "unavailable"
    assert excinfo.value.detail == "route_refused"
    assert github.calls == []


def test_every_request_names_itself_pins_the_api_version_and_carries_the_token() -> None:
    github = FakeGitHub(runs=runs(11), jobs={"11": jobs(job("success"))})

    observe(github)

    assert len(github.calls) == 2
    for request in github.calls:
        assert (
            request.headers["User-Agent"] == "orchestrator-verifier/1 (+AlobarQuest/orchestrator)"
        )
        assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
        assert request.headers["Accept"] == "application/vnd.github+json"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert [dict(request.url.params) for request in github.calls] == [
        {"head_sha": HEAD_SHA, "per_page": "100"},
        {"per_page": "100", "filter": "latest"},
    ]


def test_each_observation_mints_its_own_token() -> None:
    tokens = iter(["first", "second"])
    github = FakeGitHub(runs=runs(11), jobs={"11": jobs(job("success"))})
    observer = GitHubActionsCheckObserver(
        lambda: next(tokens), transport=httpx.MockTransport(github)
    )

    for _ in range(2):
        observer.observe(repository=REPOSITORY, head_sha=HEAD_SHA, check_name=CHECK_NAME)

    assert [r.headers["Authorization"] for r in github.calls] == [
        "Bearer first",
        "Bearer first",
        "Bearer second",
        "Bearer second",
    ]


def test_a_redirect_is_refused_not_followed() -> None:
    def moved(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.example/runs"})

    calls: list[httpx.Request] = []

    with pytest.raises(CheckObservationError) as excinfo:
        observe(moved)

    assert excinfo.value.detail == "status:302"
    assert len(calls) == 1


def test_a_token_that_cannot_be_minted_is_refused() -> None:
    """No App credentials is the state of every environment that has not configured one."""

    def no_token() -> str:
        raise GitHubAppTokenError("app_credentials_missing")

    with pytest.raises(CheckObservationError) as excinfo:
        GitHubActionsCheckObserver(no_token).observe(
            repository=REPOSITORY,
            head_sha=HEAD_SHA,
            check_name=CHECK_NAME,
        )

    assert excinfo.value.code == "unavailable"
    assert excinfo.value.detail == "app_token_mint:app_credentials_missing"


@pytest.mark.parametrize(
    "runs_payload",
    [
        {"total_count": 2, "workflow_runs": [run(11)]},
        {"workflow_runs": "not a list"},
        {"nothing": "useful"},
        [],
        ValueError("not json"),
    ],
)
def test_an_answer_that_is_not_the_whole_answer_is_refused(
    runs_payload: object,
) -> None:
    """Including the truncation case, which is the one that would fail SILENTLY.

    A page holding fewer runs than GitHub says exist could hide the very run that disagrees, so
    a partial page is refused rather than treated as the complete set.
    """
    github = FakeGitHub(runs=runs_payload)

    with pytest.raises(CheckObservationError) as excinfo:
        observe(github)

    assert excinfo.value.code == "unavailable"


def test_a_truncated_jobs_page_is_refused() -> None:
    github = FakeGitHub(
        runs=runs(11),
        jobs={"11": {"total_count": 9, "jobs": [job("success")]}},
    )

    with pytest.raises(CheckObservationError) as excinfo:
        observe(github)

    assert excinfo.value.code == "unavailable"
    assert excinfo.value.detail == "jobs_truncated"


@pytest.mark.parametrize(
    "broken",
    [
        {"id": "eleven", "html_url": "https://example.invalid/run"},
        {"id": 11},
        {"id": 0, "html_url": "https://example.invalid/run"},
        "not a mapping",
    ],
)
def test_an_unidentifiable_run_is_refused(
    broken: object,
) -> None:
    github = FakeGitHub(runs={"total_count": 1, "workflow_runs": [broken]})

    with pytest.raises(CheckObservationError) as excinfo:
        observe(github)

    assert excinfo.value.code == "unavailable"
    assert excinfo.value.detail == "workflow_run_unreadable"


@pytest.mark.parametrize(
    "broken",
    [
        {"id": 0, "name": CHECK_NAME, "conclusion": "success", "html_url": "https://x.invalid"},
        {"id": 5, "name": CHECK_NAME, "conclusion": "success"},
        "not a mapping",
    ],
)
def test_an_unidentifiable_matching_job_is_refused(
    broken: object,
) -> None:
    github = FakeGitHub(runs=runs(11), jobs={"11": {"total_count": 1, "jobs": [broken]}})

    with pytest.raises(CheckObservationError) as excinfo:
        observe(github)

    assert excinfo.value.code == "unavailable"
    assert excinfo.value.detail == "job_unreadable"
