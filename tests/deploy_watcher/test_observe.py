"""The watcher's reading of GitHub. No network: every response is a MockTransport fixture.

Payload shapes are transcribed from live responses for `AlobarQuest/change-manager` on
2026-08-10 -- pull request #46 (merged, run 31426195637) and #42 (open, and carrying the
test-merge commit GitHub puts on an open pull request, which is the subject of the sharpest
test here).
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from deploy_watcher.github import ForbiddenMethodError, GitHubReader, ReadError
from deploy_watcher.observe import (
    MERGE_TARGETED_ANOTHER_BRANCH,
    PULL_REQUEST_MISSING,
    ROLLOUT_ABSENT,
    ROLLOUT_NOT_SUCCESS,
    ROLLOUT_STUCK,
    Unmeasurable,
    observe,
)
from deploy_watcher.workflows import ATTESTS_REVISION, ATTESTS_UNKNOWN, ATTESTS_UNVERIFIED

REPO = "AlobarQuest/change-manager"
MERGE = "06f9268b5160d3d064f1f2e63d7f36faa2cb06df"
# GitHub's real answer for the OPEN pull request #42 on 2026-08-10: a genuine, fetchable
# test-merge commit sitting in `merge_commit_sha` of something that has not merged.
TEST_MERGE = "6a7c99a94c526edc036d5e5865750ec2b85a5e3b"
REVISION = "a47d4b187c93971a5b5915ce87a963bd4ef35e30"
OLD_REVISION = "791d5c9a7df304f8d1b69e3555ccf7a0709ce363"
WORKFLOW = ".github/workflows/deploy.yml"
WORKFLOW_ID = 295785634
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def merged_pull(sha: str = MERGE, merged: bool = True) -> dict:
    return {
        "number": 46,
        "merged": merged,
        "merged_at": "2026-08-10T19:51:00Z" if merged else None,
        "merge_commit_sha": sha,
        "base": {"ref": "main"},
    }


def run(
    conclusion: str | None = "success",
    status: str = "completed",
    attempt: int = 1,
    run_id: int = 31426195637,
) -> dict:
    return {
        "id": run_id,
        "run_attempt": attempt,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
        "status": status,
        "conclusion": conclusion,
        "run_started_at": "2026-08-10T19:51:56Z",
        "updated_at": "2026-08-10T19:55:00Z",
    }


def jobs(job_conclusion: str = "success", step_conclusion: str = "success") -> dict:
    return {
        "total_count": 2,
        "jobs": [
            {"name": "test", "conclusion": "success", "steps": []},
            {
                "name": "build-and-deploy",
                "conclusion": job_conclusion,
                "steps": [
                    {"name": "Trigger Coolify redeploy", "conclusion": step_conclusion},
                    {"name": "Verify the new revision is live", "conclusion": step_conclusion},
                ],
            },
        ],
    }


def reader_for(routes: dict[str, object]) -> GitHubReader:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path not in routes:
            return httpx.Response(404, json=None)
        return httpx.Response(200, json=routes[request.url.path])

    return GitHubReader(token="fixture", transport=httpx.MockTransport(handler))


def routes(**overrides) -> dict[str, object]:
    base: dict[str, object] = {
        f"/repos/{REPO}/pulls/46": merged_pull(),
        f"/repos/{REPO}/actions/workflows/{WORKFLOW}": {"id": WORKFLOW_ID, "state": "active"},
        f"/repos/{REPO}/contents/{WORKFLOW}": {"sha": REVISION},
        f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs": {
            "total_count": 1,
            "workflow_runs": [run()],
        },
        f"/repos/{REPO}/actions/runs/31426195637/attempts/1/jobs": jobs(),
    }
    base.update(overrides)
    return base


class TestTheJoin:
    def test_a_merged_pull_request_resolves_to_its_rollout_run(self):
        outcome = observe(reader_for(routes()), REPO, 46, now=NOW)
        assert outcome.findings == ()
        assert outcome.rollout is not None
        assert outcome.rollout.run is not None
        assert outcome.rollout.run.run_id == 31426195637
        assert outcome.rollout.attestation == ATTESTS_REVISION
        assert outcome.rollout.rollout_job == "build-and-deploy"
        assert outcome.rollout.trigger_step_conclusion == "success"

    def test_AN_OPEN_PULL_REQUEST_IS_NOT_A_LANDING(self):
        """The sharpest trap in the whole increment, and it is live right now.

        GitHub populates `merge_commit_sha` on an OPEN pull request with a throwaway test-merge
        commit — a real, fetchable object that satisfies every shape check — and item 44's own
        subject, change-manager PR #42, carries one today. A reader that trusted the sha would
        walk the whole pipeline successfully and, past the settle window, record the headline
        finding `rollout_never_ran` against a change that never merged.
        """
        reader = reader_for(
            routes(**{f"/repos/{REPO}/pulls/46": merged_pull(TEST_MERGE, merged=False)})
        )
        outcome = observe(reader, REPO, 46, now=NOW)
        assert outcome.findings == ()
        assert outcome.rollout is None
        assert outcome.pending == "the pull request has not merged"

    def test_the_reader_ITSELF_drops_the_sha_of_an_unmerged_pull_request(self):
        """Added because a mutation control survived without it.

        `observe` refuses on `merged` before it looks at the sha, so removing the reader's own
        guard reddened nothing — defence in depth working, and a control that cannot see either
        layer. Asserted here at the layer where it lives, because the reader is what a future
        second caller would use.
        """
        reader = reader_for({f"/repos/{REPO}/pulls/46": merged_pull(TEST_MERGE, merged=False)})
        merge = reader.read_merge(REPO, 46)
        assert merge is not None
        assert merge.merged is False
        assert merge.merge_commit_sha is None

    def test_a_merge_into_another_base_is_NOT_a_rollout_that_never_ran(self):
        """The second half of a review fix whose first half shipped alone.

        Review asked for `merged: true` AND `base.ref == the rollout branch`; only the first was
        built, and `Merge.base_ref` was read and consumed by nothing. A pull request merged into
        some other base is `merged: true` with a real merge commit at which no rollout run will
        ever exist — so past the settle window it flowed straight to `rollout_never_ran`, which
        is a fabricated instance of this program's headline finding. History has no such pull
        request in either repository; the guard is for the one that has not happened yet.
        """
        other = {**merged_pull(), "base": {"ref": "preview"}}
        outcome = observe(
            reader_for(routes(**{f"/repos/{REPO}/pulls/46": other})), REPO, 46, now=NOW
        )
        assert [f.kind for f in outcome.findings] == [MERGE_TARGETED_ANOTHER_BRANCH]
        assert outcome.rollout is None

    def test_a_pull_request_github_does_not_have_is_a_finding(self):
        outcome = observe(reader_for({}), REPO, 46, now=NOW)
        assert [f.kind for f in outcome.findings] == [PULL_REQUEST_MISSING]


class TestRefusalsRatherThanFindings:
    """Each of these would otherwise manufacture a finding out of a non-event."""

    def test_an_undeclared_repository_is_refused_never_guessed(self):
        with pytest.raises(Unmeasurable, match="no rollout workflow is declared"):
            observe(reader_for(routes()), "AlobarQuest/orchestrator", 46, now=NOW)

    def test_a_renamed_or_disabled_workflow_is_refused_not_reported_as_never_ran(self):
        """Zero runs at a path that no longer names the workflow is the wrong question, not an
        answer. Without this the pass reports `rollout_never_ran` for every merge in the repo."""
        for wrong in (
            {"id": 999, "state": "active"},
            {"id": WORKFLOW_ID, "state": "disabled_manually"},
        ):
            reader = reader_for(routes(**{f"/repos/{REPO}/actions/workflows/{WORKFLOW}": wrong}))
            with pytest.raises(Unmeasurable, match="is not the active workflow"):
                observe(reader, REPO, 46, now=NOW)

    def test_a_workflow_that_did_not_exist_at_the_merge_is_refused(self):
        empty = {k: v for k, v in routes().items() if not k.endswith(f"contents/{WORKFLOW}")}
        with pytest.raises(Unmeasurable, match="did not exist"):
            observe(reader_for(empty), REPO, 46, now=NOW)

    def test_runs_that_disagree_are_refused_rather_than_reduced(self):
        reader = reader_for(
            routes(
                **{
                    f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs": {
                        "total_count": 2,
                        "workflow_runs": [run(), run("failure", run_id=999)],
                    }
                }
            )
        )
        with pytest.raises(Unmeasurable, match="do not agree"):
            observe(reader, REPO, 46, now=NOW)

    def test_a_truncated_run_page_is_refused_rather_than_silently_narrowed(self):
        reader = reader_for(
            routes(
                **{
                    f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs": {
                        "total_count": 5,
                        "workflow_runs": [run()],
                    }
                }
            )
        )
        with pytest.raises(ReadError, match="reported 5 runs"):
            observe(reader, REPO, 46, now=NOW)


class TestSettleWindow:
    def _no_runs(self):
        return routes(
            **{
                f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs": {
                    "total_count": 0,
                    "workflow_runs": [],
                }
            }
        )

    def test_a_recent_merge_with_no_run_is_pending_not_absent(self):
        soon = datetime(2026, 8, 10, 19, 55, tzinfo=UTC)
        outcome = observe(reader_for(self._no_runs()), REPO, 46, now=soon)
        assert outcome.findings == ()
        assert outcome.pending is not None

    def test_an_old_merge_with_no_run_is_the_finding(self):
        outcome = observe(reader_for(self._no_runs()), REPO, 46, now=NOW)
        assert [f.kind for f in outcome.findings] == [ROLLOUT_ABSENT]
        assert outcome.rollout is not None and outcome.rollout.run is None

    def test_a_run_that_never_concludes_is_eventually_a_finding(self):
        stuck = routes(
            **{
                f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs": {
                    "total_count": 1,
                    "workflow_runs": [run(conclusion=None, status="in_progress")],
                }
            }
        )
        assert observe(
            reader_for(stuck), REPO, 46, now=datetime(2026, 8, 10, 19, 55, tzinfo=UTC)
        ).pending
        assert [f.kind for f in observe(reader_for(stuck), REPO, 46, now=NOW).findings] == [
            ROLLOUT_STUCK
        ]

    def test_a_completed_run_with_a_null_conclusion_is_not_a_verdict(self):
        """`status` and `conclusion` are separate fields and the second is documented nullable."""
        odd = routes(
            **{
                f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs": {
                    "total_count": 1,
                    "workflow_runs": [run(conclusion=None, status="completed")],
                }
            }
        )
        assert [f.kind for f in observe(reader_for(odd), REPO, 46, now=NOW).findings] == [
            ROLLOUT_STUCK
        ]


class TestTheSecondAxis:
    def test_a_failed_run_carries_the_job_and_step_that_decide_the_remedy(self):
        reader = reader_for(
            routes(
                **{
                    f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs": {
                        "total_count": 1,
                        "workflow_runs": [run("failure")],
                    },
                    f"/repos/{REPO}/actions/runs/31426195637/attempts/1/jobs": jobs(
                        job_conclusion="skipped", step_conclusion="skipped"
                    ),
                }
            )
        )
        outcome = observe(reader, REPO, 46, now=NOW)
        assert [f.kind for f in outcome.findings] == [ROLLOUT_NOT_SUCCESS]
        assert outcome.rollout is not None
        assert outcome.rollout.rollout_job_conclusion == "skipped"

    def test_the_attempt_is_addressed_not_the_run(self):
        """A re-run supersedes its predecessor, so asking about the run answers about a
        different attempt than the row being written."""
        reader = reader_for(
            routes(
                **{
                    f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs": {
                        "total_count": 1,
                        "workflow_runs": [run(attempt=3)],
                    },
                    f"/repos/{REPO}/actions/runs/31426195637/attempts/3/jobs": jobs(),
                }
            )
        )
        outcome = observe(reader, REPO, 46, now=NOW)
        assert outcome.rollout is not None and outcome.rollout.run is not None
        assert outcome.rollout.run.run_attempt == 3
        assert outcome.rollout.trigger_step_conclusion == "success"

    def test_an_unclassified_revision_reads_the_jobs_of_nothing(self):
        """No transcription means nobody said which job talks to production, so none is read."""
        reader = reader_for(routes(**{f"/repos/{REPO}/contents/{WORKFLOW}": {"sha": "b" * 40}}))
        outcome = observe(reader, REPO, 46, now=NOW)
        assert outcome.rollout is not None
        assert outcome.rollout.attestation == ATTESTS_UNKNOWN
        assert outcome.rollout.rollout_job is None

    def test_an_older_revision_is_recorded_as_unverified_never_upgraded(self):
        reader = reader_for(routes(**{f"/repos/{REPO}/contents/{WORKFLOW}": {"sha": OLD_REVISION}}))
        outcome = observe(reader, REPO, 46, now=NOW)
        assert outcome.rollout is not None
        assert outcome.rollout.attestation == ATTESTS_UNVERIFIED


class TestTheReaderOnlyEverReads:
    def test_a_relative_path_never_reaches_the_transport(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.method)
            return httpx.Response(200, json={})

        reader = GitHubReader(token="fixture", transport=httpx.MockTransport(handler))
        with pytest.raises(ForbiddenMethodError):
            reader._get("../../repos")
        assert seen == []

    def test_every_request_it_does_make_is_a_get(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.method)
            path = request.url.path
            return httpx.Response(200, json=routes().get(path, {}))

        reader = GitHubReader(token="fixture", transport=httpx.MockTransport(handler))
        reader.read_merge(REPO, 46)
        reader.blob_revision(REPO, WORKFLOW, MERGE)
        assert set(seen) == {"GET"}
