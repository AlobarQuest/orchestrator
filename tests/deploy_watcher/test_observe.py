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
from deploy_watcher.model import Finding
from deploy_watcher.observe import (
    MERGE_TARGETED_ANOTHER_BRANCH,
    PULL_REQUEST_MISSING,
    ROLLOUT_ABSENT,
    ROLLOUT_JOB_NOT_FOUND,
    ROLLOUT_NOT_SUCCESS,
    ROLLOUT_STUCK,
    SUPERSEDED_ROLLOUT,
    Outcome,
    Unmeasurable,
    observe,
    superseded_exception,
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
    head: str = MERGE,
    started: str = "2026-08-10T19:51:56Z",
) -> dict:
    return {
        "id": run_id,
        "run_attempt": attempt,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
        "status": status,
        "conclusion": conclusion,
        "head_sha": head,
        "run_started_at": started,
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


def reader_for(routes: dict[str, object], *, status: int = 200) -> GitHubReader:
    """A fake GitHub that keys on PATH **and the query parameters that carry meaning**.

    An earlier version keyed on the path alone, and that made two load-bearing parameters
    invisible to the entire suite: dropping `ref=` from `blob_revision` (which reads the
    workflow at HEAD instead of at the merge commit — the whole pinning mechanism) and dropping
    `head_sha=` from `runs_at_head` (which is the join between this merge and this run) both
    left every test green. Mutation controls inherited the blindness. The required parameters
    are part of the route key here, so omitting one 404s.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path
        params = dict(request.url.params)
        # ADR-0044 added `status` (and with it `branch`/`event`) to this list. Without it the
        # supersession query -- newest successful push run on the trigger branch -- and
        # `concurrent_rollout_run` are the SAME path with no head_sha, so one fixture would answer
        # both and neither read could be tested for what it actually asks.
        keyed = [
            f"{name}={params[name]}"
            for name in ("branch", "event", "head_sha", "ref", "status")
            if name in params
        ]
        if keyed:
            key = f"{key}?{'&'.join(keyed)}"
        if key not in routes:
            return httpx.Response(404, json=None)
        return httpx.Response(status, json=routes[key])

    return GitHubReader(token="fixture", transport=httpx.MockTransport(handler))


def routes(**overrides) -> dict[str, object]:
    base: dict[str, object] = {
        f"/repos/{REPO}/pulls/46": merged_pull(),
        f"/repos/{REPO}/actions/workflows/{WORKFLOW}": {"id": WORKFLOW_ID, "state": "active"},
        f"/repos/{REPO}/contents/{WORKFLOW}?ref={MERGE}": {"sha": REVISION},
        f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?head_sha={MERGE}": {
            "total_count": 1,
            "workflow_runs": [run()],
        },
        # `concurrent_rollout_run` asks the same path with no head_sha, scoped to branch+event.
        f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?branch=main&event=push": {
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
        empty = {k: v for k, v in routes().items() if f"contents/{WORKFLOW}" not in k}
        with pytest.raises(Unmeasurable, match="did not exist"):
            observe(reader_for(empty), REPO, 46, now=NOW)

    def test_runs_that_disagree_are_refused_rather_than_reduced(self):
        reader = reader_for(
            routes(
                **{
                    f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?head_sha={MERGE}": {
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
                    f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?head_sha={MERGE}": {
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
                f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?head_sha={MERGE}": {
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
                f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?head_sha={MERGE}": {
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
                f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?head_sha={MERGE}": {
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
                    f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?head_sha={MERGE}": {
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
                    f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?head_sha={MERGE}": {
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
        reader = reader_for(
            routes(**{f"/repos/{REPO}/contents/{WORKFLOW}?ref={MERGE}": {"sha": "b" * 40}})
        )
        outcome = observe(reader, REPO, 46, now=NOW)
        assert outcome.rollout is not None
        assert outcome.rollout.attestation == ATTESTS_UNKNOWN
        assert outcome.rollout.rollout_job is None

    def test_an_older_revision_is_recorded_as_unverified_never_upgraded(self):
        reader = reader_for(
            routes(**{f"/repos/{REPO}/contents/{WORKFLOW}?ref={MERGE}": {"sha": OLD_REVISION}})
        )
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


class TestReviewFixes:
    """One test per KILL from the post-implementation review. Each names what it protects."""

    def test_a_301_IS_A_REFUSAL_not_an_object_to_read_merged_off(self):
        """`follow_redirects` is False and GitHub 301s a renamed repository with a JSON body.

        With `>= 400` as the bar, that body was parsed as the pull request, `merged` read as
        absent, and the pass reported a quiet `pending` at exit 0 — every hour, forever, for a
        merge that deployed production. `read_merge` is the one reader whose malformed answer
        became silence rather than a refusal.
        """
        reader = reader_for(routes(), status=301)
        with pytest.raises(ReadError, match="301"):
            observe(reader, REPO, 46, now=NOW)

    def test_an_object_that_is_not_the_pull_request_asked_for_is_refused(self):
        wrong = {**merged_pull(), "number": 999}
        reader = reader_for(routes(**{f"/repos/{REPO}/pulls/46": wrong}))
        with pytest.raises(ReadError, match="is not that pull request"):
            observe(reader, REPO, 46, now=NOW)

    def test_registry_drift_is_a_FINDING_and_claims_no_job(self):
        """The registry naming a job the run does not have must not read as "nothing deployed".

        A skipped job IS reported, with `conclusion: "skipped"` — measured on every attempt of
        every rollout failure in this estate's history — so the absent-job branch's real
        population was drift, and it answered `no`: the value that says do not roll back,
        asserted about a rollout that may have succeeded.
        """
        renamed = {
            "total_count": 1,
            "jobs": [{"name": "Build and deploy", "conclusion": "success", "steps": []}],
        }
        reader = reader_for(
            routes(**{f"/repos/{REPO}/actions/runs/31426195637/attempts/1/jobs": renamed})
        )
        outcome = observe(reader, REPO, 46, now=NOW)
        assert [f.kind for f in outcome.findings] == [ROLLOUT_JOB_NOT_FOUND]
        assert outcome.rollout is not None
        # The job name is NOT carried: sending it would assert the watcher looked at that job.
        assert outcome.rollout.rollout_job is None
        assert outcome.rollout.trigger_step is None

    def test_a_sibling_still_running_is_pending_not_disagreement(self):
        """Two runs at one head, one unfinished, is "come back" — not exit 3 with a false name."""
        both = {
            "total_count": 2,
            "workflow_runs": [run(), run(conclusion=None, status="in_progress", run_id=999)],
        }
        reader = reader_for(
            routes(**{f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?head_sha={MERGE}": both})
        )
        soon = datetime(2026, 8, 10, 19, 55, tzinfo=UTC)
        outcome = observe(reader, REPO, 46, now=soon)
        assert outcome.findings == ()
        assert outcome.pending is not None and "still running" in outcome.pending


class TestTheQueryParametersAreLoadBEARING:
    """Both of these survived every mutation while the fake keyed on the path alone."""

    def test_the_workflow_is_read_AT_THE_MERGE_COMMIT(self):
        """`ref=` is the entire pinned-to-the-bytes mechanism. Without it the watcher reads the
        workflow at HEAD and attributes today's meaning to an old landing."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"sha": REVISION})

        reader = GitHubReader(token="f", transport=httpx.MockTransport(handler))
        reader.blob_revision(REPO, WORKFLOW, MERGE)
        assert seen == [f"https://api.github.com/repos/{REPO}/contents/{WORKFLOW}?ref={MERGE}"]

    def test_runs_are_asked_for_AT_THIS_HEAD(self):
        """`head_sha=` is the join between this merge and this run."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"total_count": 0, "workflow_runs": []})

        reader = GitHubReader(token="f", transport=httpx.MockTransport(handler))
        reader.runs_at_head(REPO, WORKFLOW, MERGE)
        assert len(seen) == 1 and f"head_sha={MERGE}" in seen[0]


# ---------------------------------------------------------------------------------------------
# ADR-0044: a failed rollout production has moved past is an EXCEPTION, not a finding.
# ---------------------------------------------------------------------------------------------

# A later commit on the trigger branch, and the successful rollout run that sits at it.
LATER_HEAD = "34ce166e499dd6c2154b05fd70e9cef19fed7e47"
OTHER_HEAD = "9f1c2e7a4b8d6053e21f7c9a4b3d8e6f05127a3b"
LATER_RUN = 34216728359
OLDER_RUN = 33598034936

_SUCCESS_QUERY = (
    f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?branch=main&event=push&status=success"
)


def failed_rollout(**overrides) -> dict[str, object]:
    """The subject of every control below: a merge whose rollout run concluded `failure`."""
    base = routes(
        **{
            f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?head_sha={MERGE}": {
                "total_count": 1,
                "workflow_runs": [run(conclusion="failure")],
            },
            f"/repos/{REPO}/actions/runs/31426195637/attempts/1/jobs": jobs(
                job_conclusion="failure", step_conclusion="failure"
            ),
        }
    )
    base.update(overrides)
    return base


def supersession(
    *,
    compare: str = "ahead",
    revision: str = REVISION,
    head: str = LATER_HEAD,
    runs_at_head: list[dict] | None = None,
    newest: list[dict] | None = None,
) -> dict[str, object]:
    """Every route the four-clause predicate reads, all four clauses satisfied by default.

    EACH NEGATIVE CONTROL BREAKS EXACTLY ONE ARGUMENT HERE and leaves the rest satisfied. That is
    not tidiness: a conjunction refuses as soon as any term does, so a fixture that fails two
    clauses cannot show which one is load-bearing, and deleting the clause under test would leave
    the mutant alive because a sibling clause refused anyway.
    """
    return {
        _SUCCESS_QUERY: {
            "total_count": 1,
            "workflow_runs": newest
            if newest is not None
            else [run(run_id=LATER_RUN, head=head, started="2026-09-08T10:40:55Z")],
        },
        f"/repos/{REPO}/compare/{MERGE}...{head}": {"status": compare},
        f"/repos/{REPO}/contents/{WORKFLOW}?ref={head}": {"sha": revision},
        f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?head_sha={head}": {
            "total_count": 1,
            "workflow_runs": runs_at_head
            if runs_at_head is not None
            else [run(run_id=LATER_RUN, head=head)],
        },
    }


class TestSupersession:
    """The four clauses, one control per clause, each derived from the same passing fixture."""

    def test_a_failed_rollout_production_has_moved_past_is_an_exception(self):
        """The positive case, and the reference every negative below is derived from."""
        outcome = observe(reader_for(failed_rollout(**supersession())), REPO, 46, now=NOW)

        assert outcome.findings == ()
        assert len(outcome.exceptions) == 1
        excused = outcome.exceptions[0]
        assert excused.kind == SUPERSEDED_ROLLOUT
        # The reader must be able to dispute it, so the run, its head and its revision are named.
        assert str(LATER_RUN) in excused.detail
        assert LATER_HEAD[:8] in excused.detail
        assert REVISION[:8] in excused.detail
        # And the underlying fact is still stated -- the exception is the same fact with a reason.
        assert "concluded failure" in excused.detail

    def test_the_observation_is_unchanged_by_the_excuse(self):
        """`_body` is untouched, so the record goes on saying the rollout failed.

        The whole point of an exception rather than a suppression: nothing is un-asserted. If this
        ever fails, the change has started editing history rather than annotating it.
        """
        excused = observe(reader_for(failed_rollout(**supersession())), REPO, 46, now=NOW)
        plain = observe(reader_for(failed_rollout()), REPO, 46, now=NOW)

        assert excused.rollout == plain.rollout
        assert excused.rollout is not None
        assert excused.rollout.run is not None
        assert excused.rollout.run.conclusion == "failure"

    def test_with_no_successful_run_on_the_branch_the_finding_stands(self):
        """Clause 1, absent. A branch whose rollout has never succeeded excuses nothing."""
        outcome = observe(
            reader_for(failed_rollout(**{_SUCCESS_QUERY: {"total_count": 0, "workflow_runs": []}})),
            REPO,
            46,
            now=NOW,
        )

        assert outcome.exceptions == ()
        assert [f.kind for f in outcome.findings] == [ROLLOUT_NOT_SUCCESS]

    def test_the_NEWEST_successful_run_decides_even_when_an_older_one_is_ahead(self):
        """Clause 1, and the reason it says NEWEST rather than any.

        "A failed, B succeeded, C failed" must supersede A and must not supersede C. Here the
        newest success sits at a head that is `identical` to this merge, while an OLDER success
        sits at a head that is genuinely ahead. A predicate that scanned for any qualifying run
        would find the older one and excuse a rollout nothing has moved past.
        """
        outcome = observe(
            reader_for(
                failed_rollout(
                    **supersession(
                        newest=[
                            run(run_id=OLDER_RUN, head=LATER_HEAD, started="2026-09-02T06:15:12Z"),
                            run(run_id=LATER_RUN, head=OTHER_HEAD, started="2026-09-08T10:40:55Z"),
                        ]
                    ),
                    **{f"/repos/{REPO}/compare/{MERGE}...{OTHER_HEAD}": {"status": "identical"}},
                )
            ),
            REPO,
            46,
            now=NOW,
        )

        assert outcome.exceptions == ()
        assert [f.kind for f in outcome.findings] == [ROLLOUT_NOT_SUCCESS]

    @pytest.mark.parametrize("status", ["identical", "diverged", "behind"])
    def test_only_a_strictly_AHEAD_head_supersedes(self, status: str):
        """Clause 2.

        `identical` is this merge's own commit, so a green run there is a disagreement for
        `_unanimous` to refuse rather than proof anything moved; `diverged` is a different line of
        history and says nothing about this build; `behind` is earlier.
        """
        outcome = observe(
            reader_for(failed_rollout(**supersession(compare=status))), REPO, 46, now=NOW
        )

        assert outcome.exceptions == ()
        assert [f.kind for f in outcome.findings] == [ROLLOUT_NOT_SUCCESS]

    @pytest.mark.parametrize("revision", [OLD_REVISION, "f" * 40])
    def test_a_weaker_attestation_at_the_superseding_head_excuses_nothing(self, revision: str):
        """Clause 3, and the clause most easily left out.

        `OLD_REVISION` is transcribed and `rollout_unverified`; the second is transcribed by
        nobody and reads `unknown`. Under either, a green run establishes that a webhook answered
        and NOT that production is serving the merged build -- so excusing a real failure with it
        would be excusing it with nothing. The excuse must be at least as strong as what would
        settle a record.
        """
        outcome = observe(
            reader_for(failed_rollout(**supersession(revision=revision))), REPO, 46, now=NOW
        )

        assert outcome.exceptions == ()
        assert [f.kind for f in outcome.findings] == [ROLLOUT_NOT_SUCCESS]

    def test_an_ambiguous_superseding_head_excuses_nothing(self):
        """Clause 4. A head carrying both a failed and a passed run does not establish a success."""
        outcome = observe(
            reader_for(
                failed_rollout(
                    **supersession(
                        runs_at_head=[
                            run(run_id=LATER_RUN, head=LATER_HEAD),
                            run(run_id=LATER_RUN + 1, head=LATER_HEAD, conclusion="failure"),
                        ]
                    )
                )
            ),
            REPO,
            46,
            now=NOW,
        )

        assert outcome.exceptions == ()
        assert [f.kind for f in outcome.findings] == [ROLLOUT_NOT_SUCCESS]

    def test_a_superseding_head_whose_own_run_failed_excuses_nothing(self):
        """Clause 4, the other half: the listing said `success` and the head's own run did not.

        Not redundant with the clause-1 filter. The two reads are of different things -- one asks
        GitHub for runs it labels successful, the other asks what is at that head -- so a
        disagreement between them is exactly the case where trusting the first alone is wrong.
        """
        outcome = observe(
            reader_for(
                failed_rollout(
                    **supersession(
                        runs_at_head=[run(run_id=LATER_RUN, head=LATER_HEAD, conclusion="failure")]
                    )
                )
            ),
            REPO,
            46,
            now=NOW,
        )

        assert outcome.exceptions == ()
        assert [f.kind for f in outcome.findings] == [ROLLOUT_NOT_SUCCESS]

    def test_an_unreadable_supersession_leaves_the_FINDING_and_is_never_incomplete(self):
        """THE FAIL DIRECTION, and the reason the predicate catches rather than propagates.

        The finding is already established when the excuse is looked for. If a `ReadError` escaped
        to `_watch_one`'s `except (Unmeasurable, ReadError)`, a GitHub hiccup would convert a
        measured failure into `incomplete` and exit 3 -- replacing an answer a reader can act on
        with one they cannot. `observe` must RETURN here, never raise.
        """
        outcome = observe(
            reader_for(failed_rollout(**{**supersession(), _SUCCESS_QUERY: ["not", "a", "dict"]})),
            REPO,
            46,
            now=NOW,
        )

        assert outcome.exceptions == ()
        assert [f.kind for f in outcome.findings] == [ROLLOUT_NOT_SUCCESS]

    def test_a_missing_supersession_route_is_also_a_finding_rather_than_a_raise(self):
        """The 404 twin of the control above, kept separate because it takes a different branch.

        An absent route means `_get` answers None and the reader raises about a workflow it cannot
        address -- a different line from the unreadable-body branch, and the one a real outage
        looks like.
        """
        outcome = observe(reader_for(failed_rollout()), REPO, 46, now=NOW)

        assert outcome.exceptions == ()
        assert [f.kind for f in outcome.findings] == [ROLLOUT_NOT_SUCCESS]

    def test_registry_drift_stays_a_FINDING_even_when_the_rollout_is_excused(self):
        """Only the rollout finding is excusable. A transcription that has drifted is live.

        The supersession says something about PRODUCTION; it says nothing about whether this
        repository's registry entry still describes its workflow, which is wrong today and wrong
        tomorrow whatever production is serving.
        """
        outcome = observe(
            reader_for(
                failed_rollout(
                    **supersession(),
                    **{
                        f"/repos/{REPO}/actions/runs/31426195637/attempts/1/jobs": {
                            "total_count": 1,
                            "jobs": [{"name": "some-other-job", "conclusion": "failure"}],
                        }
                    },
                )
            ),
            REPO,
            46,
            now=NOW,
        )

        assert [f.kind for f in outcome.findings] == [ROLLOUT_JOB_NOT_FOUND]
        assert [f.kind for f in outcome.exceptions] == [SUPERSEDED_ROLLOUT]

    def test_a_successful_rollout_never_asks_the_supersession_question(self):
        """There is no finding to excuse, so the four reads are not made at all.

        Asserted by the fixture rather than by a spy: the base `routes()` carries none of the
        supersession routes, so any read of them would 404 into a `ReadError` this path does not
        catch, and the test would fail loudly instead of silently costing four API calls per
        healthy rollout.
        """
        outcome = observe(reader_for(routes()), REPO, 46, now=NOW)

        assert outcome.findings == ()
        assert outcome.exceptions == ()


class TestTheSupersededExceptionAccessor:
    def test_it_finds_the_excuse_by_KIND_rather_than_by_position_or_emptiness(self):
        """The one place keying on `SUPERSEDED_ROLLOUT`, so its consumers need not.

        A second exception kind arriving must not change what this returns -- which is exactly
        what `outcome.exceptions[0]` and `bool(outcome.exceptions)` would both do.
        """
        excuse = Finding(SUPERSEDED_ROLLOUT, "s", "production moved on")
        other = Finding("some_other_exception_kind", "s", "unrelated")

        assert superseded_exception(Outcome("s", exceptions=(other, excuse))) is excuse
        assert superseded_exception(Outcome("s", exceptions=(other,))) is None
        assert superseded_exception(Outcome("s")) is None
