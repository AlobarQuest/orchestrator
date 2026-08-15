"""Read-only GitHub. Every verb is GET, and that is enforced before the transport.

The Checks API is deliberately not used. The `Alobar SDS Dispatch` App carries no `checks`
permission, so `/commits/{sha}/check-runs` answers 403 -- but that is not the reason here: this
program's third-party dependencies are confined to `httpx` and `typer` (pinned by its isolation
test), which rules out the JWT assertion an App token needs. It authenticates with a plain token
and reads workflow RUNS, which is the same surface `services/github_checks.py` settled on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from deploy_watcher.model import Merge, Run, is_sha

GITHUB_API_URL = "https://api.github.com"
USER_AGENT = "deploy-watcher/1 (+AlobarQuest/orchestrator)"
TIMEOUT_SECONDS = 30.0
PAGE_SIZE = 100


class ReadError(Exception):
    """GitHub could not be asked. The answer is missing, not clean."""


class ForbiddenMethodError(ReadError):
    """The reader was asked for something other than a GET of an absolute path."""


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GitHubReader:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = GITHUB_API_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": USER_AGENT,
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, **params: Any) -> Any:
        if not path.startswith("/"):
            raise ForbiddenMethodError(f"the reader may not fetch {path}")
        try:
            response = self._client.request("GET", path, params=params or None)
        except httpx.HTTPError as error:
            # The exception type only. An httpx error carries the request, and a diagnostic that
            # prints what it was given is how a token reaches a transcript.
            raise ReadError(
                f"github is unreachable for GET {path}: {type(error).__name__}"
            ) from error
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            # ANY other status, not just >= 400. `follow_redirects` is False by default, and
            # GitHub answers 301 with a JSON body for a repository that has been renamed or
            # transferred -- a body with no `merged` key, which `read_merge` would read as "not
            # merged" and report as a quiet `pending`, at exit 0, every hour, forever. The one
            # reader in this program whose malformed answer becomes silence rather than a
            # refusal is the front door.
            raise ReadError(f"github rejected GET {path}: {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise ReadError(
                f"github answered GET {path} with something that is not JSON"
            ) from error

    # -- the three reads this program needs ------------------------------------------------

    def read_merge(self, repository: str, number: int) -> Merge | None:
        """What GitHub says about one pull request. None means GitHub has no such pull request."""
        body = self._get(f"/repos/{repository}/pulls/{number}")
        if body is None:
            return None
        if not isinstance(body, dict):
            raise ReadError(f"pull request {repository}#{number} is not an object")
        if body.get("number") != number:
            # Believe an object only when it says it is the one that was asked for. Everything
            # downstream keys on this pull request having merged.
            raise ReadError(f"the answer to {repository}#{number} is not that pull request")
        merged = bool(body.get("merged"))
        sha = body.get("merge_commit_sha")
        base = body.get("base")
        return Merge(
            repository=repository,
            number=number,
            merged=merged,
            # Only meaningful once merged. Carried as None otherwise so no caller can reach for
            # the test-merge commit GitHub leaves in this field on an open pull request.
            merge_commit_sha=str(sha) if merged and is_sha(sha) else None,
            base_ref=str(base.get("ref")) if isinstance(base, dict) and base.get("ref") else None,
            merged_at=_parse_time(body.get("merged_at")),
        )

    def pull_request_disposition(self, repository: str, number: int) -> str | None:
        """Where one pull request ended up: `open`, `merged`, or `closed_unmerged`.

        ADR-0019 increment 5b. The producer needs to tell "closed without merging" from "still
        waiting" and from "landed", because only the first means the change a record stands for
        can never happen -- and a record retired on anything weaker would be retired on absence
        rather than on a fact.

        `None` means GitHub HAS NO SUCH PULL REQUEST, which is deliberately not a fourth
        disposition: it is a statement about the question rather than about the subject, and the
        caller must not read it as a reason to retire anything. A read that fails raises, for the
        same reason.
        """
        body = self._get(f"/repos/{repository}/pulls/{number}")
        if body is None:
            return None
        if not isinstance(body, dict):
            raise ReadError(f"pull request {repository}#{number} is not an object")
        if body.get("number") != number:
            raise ReadError(f"the answer to {repository}#{number} is not that pull request")
        if bool(body.get("merged")):
            return "merged"
        # `state` is `open` or `closed`; anything else is a shape this program does not know, and
        # reading it as closed would retire a record on an unrecognised word.
        state = body.get("state")
        if state == "open":
            return "open"
        if state == "closed":
            return "closed_unmerged"
        raise ReadError(f"pull request {repository}#{number} reports an unreadable state")

    def commit_message(self, repository: str, sha: str) -> str | None:
        """The message of one commit. `None` means the commit carries no message text.

        Read for the trailers a landing carries (ADR-0022): factory-runner writes `SDS-Unit:` into
        its commit MESSAGE, and a squash carries the branch's messages through into the landing
        commit. That is the CLAIM -- an assertion by the thing whose compliance is in question --
        so it selects which work unit to ask the orchestrator about and is evidence of nothing on
        its own. `deploy_watcher/units.py` carries the verification.

        Whether the trailer survives at all is a repository SETTING rather than a property of the
        merge: `squash_merge_commit_message` governs whether the landing commit carries the
        branch's messages, and every ledger repository is `COMMIT_MESSAGES` today. A setting change
        makes the claim absent, which is why an absent claim is the ordinary answer downstream and
        never a finding.

        **A 404 RAISES HERE, where its two siblings above return None, and the asymmetry is the
        point.** They are asked about a pull request NUMBER a change record supplied, so "GitHub
        has no such pull request" is a real answer about a subject that may not exist. This is
        asked about a merge commit GitHub ITSELF named one hop earlier, when it said that pull
        request had merged -- so an absent commit is a question that could not be answered, and
        collapsing it into "no trailer" would skip the unit observation forever while the pass
        reported success.
        """
        body = self._get(f"/repos/{repository}/commits/{sha}")
        if body is None:
            raise ReadError(f"github has no commit {repository}@{sha[:8]}")
        if not isinstance(body, dict):
            raise ReadError(f"commit {repository}@{sha[:8]} is not an object")
        commit = body.get("commit")
        if not isinstance(commit, dict):
            raise ReadError(f"commit {repository}@{sha[:8]} carries no commit object")
        message = commit.get("message")
        return str(message) if isinstance(message, str) else None

    def blob_revision(self, repository: str, path: str, ref: str) -> str | None:
        """The blob sha of a file AS IT WAS at a commit. None if the file did not exist there.

        Read from GitHub rather than computed: the sha is what GitHub itself serves for those
        bytes, so the pin cannot drift from the thing it pins by a hashing disagreement.
        """
        body = self._get(f"/repos/{repository}/contents/{path}", ref=ref)
        if body is None:
            return None
        if not isinstance(body, dict):
            # A directory answers with a list. Whatever this is, it is not the file.
            return None
        sha = body.get("sha")
        return str(sha) if is_sha(sha) else None

    def open_pull_requests(self, repository: str) -> list[dict[str, Any]]:
        """Open pull requests, projected to the four fields a caller may key on.

        Projected rather than returned whole for the reason the estate learned from an MCP tool
        that returned secrets: a response passed through untouched is a response whose every
        future field arrives in a transcript. Four fields, named here, and nothing else escapes.

        `author` is the login, and a bot is identified by `user.type == "Bot"` rather than by a
        `[bot]` suffix on the login -- the suffix is a display convention a machine account can
        lack, and the landing ledger already carries that mistake as a known defect.
        """
        body = self._get(f"/repos/{repository}/pulls", state="open", per_page=100)
        if not isinstance(body, list):
            return []
        pulls: list[dict[str, Any]] = []
        for item in body:
            if not isinstance(item, dict):
                continue
            user = item.get("user")
            user = user if isinstance(user, dict) else {}
            base = item.get("base")
            base = base if isinstance(base, dict) else {}
            pulls.append(
                {
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "author": user.get("login"),
                    "is_bot": str(user.get("type", "")).lower() == "bot",
                    "draft": bool(item.get("draft")),
                    "base_ref": base.get("ref"),
                }
            )
        return pulls

    def workflow_is_addressable(
        self, repository: str, workflow_path: str, workflow_id: int
    ) -> bool:
        """Does the path still name the workflow the registry meant, and is it active?

        A renamed path, or a workflow someone disabled, answers a run query with ZERO runs — and
        zero runs reads as "the rollout never ran", which is a finding, when the truth is that
        the question was asked of the wrong thing. Checking identity turns that into an
        unmeasurable pass instead of a fabricated one.
        """
        body = self._get(
            f"/repos/{repository}/actions/workflows/{workflow_path.replace('/', '%2F')}"
        )
        if not isinstance(body, dict):
            return False
        return body.get("id") == workflow_id and body.get("state") == "active"

    def rollout_step(
        self, repository: str, run_id: int, attempt: int, job_name: str, step_name: str
    ) -> tuple[str | None, str | None] | None:
        """How the rollout job and its trigger step concluded on ONE attempt of a run.

        Returns `(job_conclusion, step_conclusion)` when the named job IS in the run, and
        **`None` when it is not** — which is registry drift, not evidence about production.
        Collapsing the two into a bare `(None, None)` was a real defect: a skipped job is
        reported by GitHub with `conclusion: "skipped"` (measured on all three of this estate's
        rollout failures, on every attempt), so the only population reaching a `job is None`
        branch was a registry that named a job the run does not have — and the caller answered
        `no`, "nothing was deployed, do not roll back", for a rollout that may well have
        succeeded.

        The specific attempt is addressed rather than the run, because a re-run supersedes its
        predecessor and asking `/runs/{id}/jobs` after one would answer about a different
        attempt than the row being written or re-checked.

        This is the whole reason to read jobs at all: a run conclusion cannot distinguish "the
        tests failed and nothing was deployed" from "production was deployed and is broken", and
        those two want opposite remedies.
        """
        body = self._get(
            f"/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs",
            per_page=PAGE_SIZE,
        )
        if not isinstance(body, dict):
            raise ReadError(f"the jobs of run {run_id} attempt {attempt} are unreadable")
        jobs = body.get("jobs")
        if not isinstance(jobs, list):
            raise ReadError(f"run {run_id} attempt {attempt} answered without jobs")
        total = body.get("total_count")
        if isinstance(total, int) and total > len(jobs):
            raise ReadError(
                f"run {run_id} attempt {attempt} reported {total} jobs, returned {len(jobs)}"
            )
        for job in jobs:
            if not isinstance(job, dict) or job.get("name") != job_name:
                continue
            job_conclusion = job.get("conclusion")
            steps = job.get("steps")
            step_conclusion = None
            if isinstance(steps, list):
                for step in steps:
                    if isinstance(step, dict) and step.get("name") == step_name:
                        step_conclusion = step.get("conclusion")
                        break
            return (
                str(job_conclusion) if job_conclusion else None,
                str(step_conclusion) if step_conclusion else None,
            )
        return None

    def concurrent_rollout_run(self, repository: str, workflow_path: str, run: Run) -> int | None:
        """The id of another rollout run that was in flight while this one was, if any.

        Both repositories deploy a FLOATING image tag (`:main`, `:latest`) and neither workflow
        declares a `concurrency` group. change-manager's verify step then polls production for
        ten minutes waiting to see its own commit — so if a second merge lands and its image wins
        the pull, the first run polls to its deadline and exits 1. That is a lost race, not a
        broken deployment, and rolling back on it would revert a build that is fine. Merges 108
        and 131 seconds apart have already happened here, before auto-merge; opening the lane
        makes back-to-back the ordinary case.
        """
        if run.started_at is None or run.concluded_at is None:
            return None
        encoded = workflow_path.replace("/", "%2F")
        body = self._get(
            f"/repos/{repository}/actions/workflows/{encoded}/runs",
            branch="main",
            event="push",
            per_page=PAGE_SIZE,
        )
        if not isinstance(body, dict) or not isinstance(body.get("workflow_runs"), list):
            raise ReadError(f"{repository} answered the concurrency query unreadably")
        for item in body["workflow_runs"]:
            if not isinstance(item, dict) or item.get("id") == run.run_id:
                continue
            started = _parse_time(item.get("run_started_at") or item.get("created_at"))
            if started is None:
                continue
            # Started after this run did, and before this run stopped: the two overlapped, so
            # each was racing the other's image into the same floating tag.
            if run.started_at < started < run.concluded_at:
                return int(item["id"])
        return None

    def runs_at_head(self, repository: str, workflow_path: str, head_sha: str) -> list[Run]:
        """Every run of one workflow at one commit, newest attempt as GitHub reports it.

        Scoped to the workflow FILE rather than to all runs at the head, because a head carries
        the rollout run, the quality run, and anything else the repository fires.
        """
        # The workflow is addressed by path -- an id would need a second lookup and would break
        # the moment a workflow is deleted and recreated.
        encoded = workflow_path.replace("/", "%2F")
        body = self._get(
            f"/repos/{repository}/actions/workflows/{encoded}/runs",
            head_sha=head_sha,
            per_page=PAGE_SIZE,
        )
        if body is None:
            # The workflow file does not exist on the default branch. That is not "no runs" --
            # it is "this question cannot be asked here".
            raise ReadError(f"{repository} has no workflow {workflow_path}")
        if not isinstance(body, dict):
            raise ReadError(f"{repository} answered the run query with something unreadable")
        items = body.get("workflow_runs")
        if not isinstance(items, list):
            raise ReadError(f"{repository} answered the run query without workflow_runs")
        total = body.get("total_count")
        if isinstance(total, int) and total > len(items):
            # Refuse rather than silently narrow. A truncated answer that reads as complete is
            # how "no run found" gets manufactured.
            raise ReadError(
                f"{repository} reported {total} runs at {head_sha[:8]} and returned {len(items)}"
            )
        return [_run(item) for item in items if isinstance(item, dict)]

    def merged_pull_numbers(self, repository: str, *, pages: int) -> list[int]:
        """Closed pull requests against the repository, newest first. Backfill's subject list."""
        numbers: list[int] = []
        for page in range(1, pages + 1):
            body = self._get(
                f"/repos/{repository}/pulls",
                state="closed",
                per_page=PAGE_SIZE,
                page=page,
                sort="updated",
                direction="desc",
            )
            if not isinstance(body, list) or not body:
                break
            for item in body:
                if isinstance(item, dict) and item.get("merged_at") and item.get("number"):
                    numbers.append(int(item["number"]))
        return numbers


def _run(item: dict[str, Any]) -> Run:
    run_id = item.get("id")
    if not isinstance(run_id, int):
        raise ReadError("a workflow run has no id")
    conclusion = item.get("conclusion")
    return Run(
        run_id=run_id,
        # A re-run supersedes its earlier attempt and GitHub reports the latest here, so the
        # attempt number is part of the observation's identity rather than a detail.
        run_attempt=int(item.get("run_attempt") or 1),
        run_url=str(item.get("html_url") or ""),
        status=str(item.get("status") or ""),
        conclusion=str(conclusion) if conclusion else None,
        started_at=_parse_time(item.get("run_started_at") or item.get("created_at")),
        concluded_at=_parse_time(item.get("updated_at")),
    )
