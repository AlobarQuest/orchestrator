"""Ask GitHub how a named job concluded on a pull-request head.

Named-check evidence used to carry both what the caller expected and what it claimed to have
seen, so an acceptance criterion resolved on two values one actor typed. This module supplies
the second value from GitHub, using the same App installation token the orchestrator already
mints for its outbound calls.

The token cannot use the Checks API. The installation carries `actions: write` and
`metadata: read` and no `checks` permission at all, so
`GET /repos/{repository}/commits/{sha}/check-runs` answers 403 for it (measured, 2026-08-02).
The Actions API answers the same question for every check this estate produces, because they
are all GitHub Actions jobs: a job carries the name and the conclusion that the Checks API
would report for it. A check run published by some other application is invisible here. That
is the boundary of what this can see, and outside it the observation refuses rather than
guesses.

Every failure is a refusal. Nothing here may fall back to what the caller said, because the
caller's word is the thing this module exists to stop trusting.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from orchestrator.kernel.evidence_types import NAMED_CHECK_MAX_OBSERVED_JOBS
from orchestrator.services.github_app import GITHUB_API_URL, GitHubAppTokenError

OBSERVE_TIMEOUT_SECONDS = 10.0

# GitHub pages these collections. A page that does not hold the whole answer is refused rather
# than silently truncated, so the cap can never quietly narrow what was looked at.
PAGE_SIZE = 100


class CheckObservationError(Exception):
    """A refusal to observe, carrying a reason code and no response body."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}:{detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ObservedJob:
    run_id: str
    run_url: str
    job_id: str
    job_url: str
    conclusion: str


@dataclass(frozen=True)
class CheckObservation:
    """What GitHub says a named job concluded, and every job the name resolved to.

    `conclusion` is the single value every match agreed on. A name that resolves to jobs which
    disagree has no observed conclusion and is refused before this is built.
    """

    check_name: str
    conclusion: str
    jobs: tuple[ObservedJob, ...]


class CheckObserver(Protocol):
    def observe(
        self,
        *,
        repository: str,
        head_sha: str,
        check_name: str,
    ) -> CheckObservation: ...


class GitHubActionsCheckObserver:
    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        timeout: float = OBSERVE_TIMEOUT_SECONDS,
    ) -> None:
        self._token_provider = token_provider
        self._timeout = timeout

    def observe(
        self,
        *,
        repository: str,
        head_sha: str,
        check_name: str,
    ) -> CheckObservation:
        token = self._token()
        base = f"{GITHUB_API_URL}/repos/{repository}/actions/runs"
        runs = self._collection(
            token,
            base,
            {"head_sha": head_sha, "per_page": PAGE_SIZE},
            "workflow_runs",
        )
        jobs: list[ObservedJob] = []
        for run in runs:
            run_id, run_url = _run_identity(run)
            # `filter=latest` is GitHub's default and the right one: a re-run supersedes its
            # earlier attempt, and the superseded attempt is not what the head reports now.
            for job in self._collection(
                token,
                f"{base}/{run_id}/jobs",
                {"per_page": PAGE_SIZE, "filter": "latest"},
                "jobs",
            ):
                observed = _observed_job(job, run_id, run_url, check_name)
                if observed is not None:
                    jobs.append(observed)
        return _resolve(check_name, jobs)

    def _token(self) -> str:
        try:
            return self._token_provider()
        except GitHubAppTokenError as error:
            raise CheckObservationError("unavailable", f"app_token_mint:{error.code}") from error

    def _collection(
        self,
        token: str,
        url: str,
        params: Mapping[str, str | int],
        key: str,
    ) -> Sequence[Any]:
        try:
            response = httpx.get(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                params=dict(params),
                timeout=self._timeout,
            )
        except httpx.RequestError as error:
            raise CheckObservationError(
                "unavailable", f"request_error:{error.__class__.__name__}"
            ) from error
        if response.status_code != 200:
            # The status code only. An error body from GitHub is unaudited text that would end
            # up in an evidence-adjacent error message.
            raise CheckObservationError("unavailable", f"status:{response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise CheckObservationError("unavailable", f"{key}_unreadable") from error
        if not isinstance(payload, dict):
            raise CheckObservationError("unavailable", f"{key}_unreadable")
        items = payload.get(key)
        if not isinstance(items, list):
            raise CheckObservationError("unavailable", f"{key}_missing")
        total = payload.get("total_count")
        if isinstance(total, int) and not isinstance(total, bool) and total > len(items):
            raise CheckObservationError("unavailable", f"{key}_truncated")
        return items


def _run_identity(run: object) -> tuple[str, str]:
    if not isinstance(run, dict):
        raise CheckObservationError("unavailable", "workflow_run_unreadable")
    run_id = run.get("id")
    run_url = run.get("html_url")
    if (
        not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or run_id <= 0
        or not isinstance(run_url, str)
        or not run_url.strip()
    ):
        raise CheckObservationError("unavailable", "workflow_run_unreadable")
    return str(run_id), run_url.strip()


def _observed_job(
    job: object,
    run_id: str,
    run_url: str,
    check_name: str,
) -> ObservedJob | None:
    if not isinstance(job, dict):
        raise CheckObservationError("unavailable", "job_unreadable")
    name = job.get("name")
    if not isinstance(name, str) or name.strip() != check_name:
        return None
    job_id = job.get("id")
    job_url = job.get("html_url")
    if (
        not isinstance(job_id, int)
        or isinstance(job_id, bool)
        or job_id <= 0
        or not isinstance(job_url, str)
        or not job_url.strip()
    ):
        raise CheckObservationError("unavailable", "job_unreadable")
    conclusion = job.get("conclusion")
    if not isinstance(conclusion, str) or not conclusion.strip():
        # A job GitHub has not concluded has no answer to give. Saying so is materially
        # different from saying the name matched nothing: the caller should wait, not rename.
        raise CheckObservationError("not_concluded", f"job:{job_id}")
    return ObservedJob(
        run_id=run_id,
        run_url=run_url,
        job_id=str(job_id),
        job_url=job_url.strip(),
        conclusion=conclusion.strip().lower(),
    )


def _resolve(check_name: str, jobs: list[ObservedJob]) -> CheckObservation:
    """Reduce the matches to one answer, or refuse.

    A name resolving to more than one job is the ordinary case, not a pathology: a workflow that
    runs on both a branch push and its pull request puts two identically-named jobs on one head.
    Requiring UNANIMITY keeps that case working while refusing the case that matters — one match
    green and another red, where picking either the newest or the first would let a genuinely
    failing check resolve a criterion.
    """
    if not jobs:
        raise CheckObservationError("not_found", check_name)
    if len(jobs) > NAMED_CHECK_MAX_OBSERVED_JOBS:
        raise CheckObservationError("ambiguous", f"matches:{len(jobs)}")
    conclusions = {job.conclusion for job in jobs}
    if len(conclusions) != 1:
        raise CheckObservationError("ambiguous", ",".join(sorted(conclusions)))
    # The answer is unanimous; only the citation is a choice. Newest first, and every match is
    # carried, so the record names all of them rather than implying there was one.
    ordered = sorted(jobs, key=lambda job: (int(job.run_id), int(job.job_id)), reverse=True)
    return CheckObservation(
        check_name=check_name,
        conclusion=ordered[0].conclusion,
        jobs=tuple(ordered),
    )
