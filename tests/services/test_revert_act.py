"""Watching the inert lane's landings and acting on a bad one (Landing Part 3a).

Everything runs with no network. Landings are made by the inert lane's own act, so the watch is
asked about rows and events in the shape production writes them. Persistence is asserted through a
second session.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import Event, LandingRevert, SdsMemberHold
from orchestrator.services.landing.estate_landing import LANDING_INERT, EstateAnswer
from orchestrator.services.landing.inert_landing_admission import inert_landing_admission
from orchestrator.services.landing.inert_landing_policy import InertLandingAnswer
from orchestrator.services.landing.inert_pr_merge import InertMergeCommand, land_inert_pull_request
from orchestrator.services.landing.interfaces import (
    BranchHead,
    EstateGatewayError,
    EstatePullRequest,
    Job,
    MergeOutcome,
    Tree,
    TreeChange,
    TreeEntry,
)
from orchestrator.services.landing.revert_act import (
    LANDING_REVERT_LANE_OFF,
    LANDING_REVERT_NOT_WATCHED,
    LANDING_REVERT_RECORD_MOVED,
    LANDING_REVERT_RERUN_REFUSED,
    RevertCommand,
    act_on_landing,
    landing_watch,
)
from orchestrator.services.landing.terms import (
    LANDING_REPOSITORY_NOT_GRANTED,
    LANDING_VERSION_REVERTED,
)
from tests.services.estate_doubles import FakeEstateLandingSource
from tests.services.estate_landing_doubles import dependabot_commit, pull_request
from tests.services.inert_landing_doubles import (
    INERT_REPOSITORY,
    LANDED_COMMIT,
    SYNC_BOT,
    SYNC_BRANCH,
    UPDATE_BOT,
    ActingInertGateway,
    FakeInertPolicySource,
    rules,
)
from tests.services.test_inert_pr_merge import SYSTEM, WORKER
from tests.services.test_member_proposals import grant_services

BUMP = 41
REVERT = 42
NEXT_BUMP = 43
APP = "alobar-sds-dispatch[bot]"
TITLE = "build(deps): bump typer from 0.20.0 to 0.21.0"
BRANCH = "dependabot/uv/typer-0.21.0"
RUN = 18_000_000_001

BUMP_HEAD = "1" * 40
PARENT = "3" * 40
BASE = "b" * 40
REVERT_HEAD = "d" * 40
REVERT_MERGE = "e" * 40
NEXT_HEAD = "4" * 40


def message(title: str = TITLE, version: str = "0.21.0") -> str:
    """The update bot's commit message in its real shape: the block names the dependency, its
    version and its type, and never the directory (measured on factory-runner#103)."""
    return (
        f"{title}\n\nBumps typer.\n\n---\nupdated-dependencies:\n"
        f"- dependency-name: typer\n  dependency-version: {version}\n"
        "  dependency-type: direct:production\n...\n"
    )


def job(name: str, conclusion: str | None, *, attempt: int = 1, event: str = "push") -> Job:
    return Job(
        name=name,
        conclusion=conclusion,
        run_id=RUN if event == "push" else 7,
        attempt=attempt,
        workflow="CI",
        event=event,
    )


def _entry(sha: str) -> TreeEntry:
    return TreeEntry(mode="100644", type="blob", sha=sha)


def trees() -> dict[str, Tree]:
    """The bump moved `uv.lock`; `README.md` changed on the default branch after it landed; the
    revert restores the lock on top of that."""
    return {
        PARENT: Tree("tp", {"uv.lock": _entry("l1"), "README.md": _entry("r1")}),
        LANDED_COMMIT: Tree("tm", {"uv.lock": _entry("l2"), "README.md": _entry("r1")}),
        BASE: Tree("tb", {"uv.lock": _entry("l2"), "README.md": _entry("r2")}),
        REVERT_HEAD: Tree("th", {"uv.lock": _entry("l1"), "README.md": _entry("r2")}),
    }


class WatchGateway(ActingInertGateway):
    """The App's whole surface for this act: the pull requests by number, the jobs at each commit,
    the parent, the rerun, and the revert's writes, each recorded."""

    def __init__(
        self,
        *,
        pulls: dict[int, EstatePullRequest] | None = None,
        jobs: dict[str, tuple[Job, ...]] | None = None,
        rerun_error: EstateGatewayError | None = None,
        branch_error: EstateGatewayError | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("trees", trees())
        kwargs.setdefault("base_head", BranchHead(name="main", sha=BASE))
        kwargs.setdefault(
            "commits",
            {
                BUMP: (dependabot_commit(BUMP_HEAD, message()),),
                NEXT_BUMP: (dependabot_commit(NEXT_HEAD, message()),),
            },
        )
        super().__init__(**kwargs)
        self.pulls = pulls if pulls is not None else {BUMP: bump_pull()}
        self.jobs = jobs if jobs is not None else {}
        self._rerun_error = rerun_error
        self._branch_error = branch_error
        self.job_reads: list[str] = []
        self.reruns: list[int] = []
        self.writes: list[str] = []

    def read_pull_request(self, *, repository: str, number: int) -> EstatePullRequest:
        self.reads.append((repository, number))
        if self._read_error is not None:
            raise self._read_error
        return self.pulls[number]

    def jobs_at(self, *, repository: str, sha: str) -> tuple[Job, ...]:
        self.job_reads.append(sha)
        return self.jobs.get(sha, ())

    def parent_of(self, *, repository: str, sha: str) -> str:
        assert sha == LANDED_COMMIT
        return PARENT

    def rerun_failed_jobs(self, *, repository: str, run_id: int) -> None:
        if self._rerun_error is not None:
            raise self._rerun_error
        self.reruns.append(run_id)

    def create_tree(self, *, repository: str, base_tree: str, changes: Sequence[TreeChange]) -> str:
        self.writes.append("tree")
        return "t" * 40

    def create_commit(self, *, repository: str, message: str, tree: str, parent: str) -> str:
        self.writes.append("commit")
        return REVERT_HEAD

    def create_branch(self, *, repository: str, branch: str, sha: str) -> None:
        self.writes.append(f"branch {branch}")
        if self._branch_error is not None:
            raise self._branch_error

    def open_pull_request(
        self, *, repository: str, title: str, body: str, head: str, base: str
    ) -> int:
        self.writes.append(f"pull {title}")
        self.pulls[REVERT] = pull_request(
            number=REVERT,
            title=title,
            head_sha=REVERT_HEAD,
            head_ref=head,
            author_login=APP,
        )
        return REVERT


def bump_pull(number: int = BUMP, head_sha: str = BUMP_HEAD, **kwargs: Any) -> EstatePullRequest:
    kwargs.setdefault("title", TITLE)
    kwargs.setdefault("head_ref", BRANCH)
    return pull_request(number=number, head_sha=head_sha, **kwargs)


HEAD_GREEN = (job("test", "success", event="pull_request"),)


def failing(attempt: int = 1) -> dict[str, tuple[Job, ...]]:
    """`test` passed on the pull request and failed on the merge commit; green before the bump."""
    return {
        BUMP_HEAD: HEAD_GREEN,
        LANDED_COMMIT: (job("test", "failure", attempt=attempt),),
        PARENT: (job("test", "success"),),
    }


def land(
    session: Session,
    gateway: ActingInertGateway,
    number: int,
    head: str,
    policy: FakeInertPolicySource | None = None,
) -> None:
    """Land through the inert lane's own act, so the row and its event are production's."""
    land_inert_pull_request(
        session,
        InertMergeCommand(
            repository=INERT_REPOSITORY,
            pr_number=number,
            actor=SYSTEM,
            idempotency_key=f"land-{number}",
            expected_head_sha=head,
        ),
        gateway,
        FakeEstateLandingSource(default=EstateAnswer(LANDING_INERT)),
        policy or FakeInertPolicySource(),
        enabled=True,
        credentials_configured=True,
    )


def landed_bump(
    session: Session, policy: FakeInertPolicySource | None = None, **kwargs: Any
) -> None:
    grant_services(session, INERT_REPOSITORY, "dependency_updates", "security_patches")
    land(session, ActingInertGateway(pull=bump_pull(**kwargs)), BUMP, BUMP_HEAD, policy)


class _ReadIt:
    """Stands for "the status the record has now", the value a caller reading the watch sends."""


READ = _ReadIt()


def _current(session: Session, pr_number: int) -> str | None:
    return session.scalar(select(LandingRevert.status).where(LandingRevert.pr_number == pr_number))


def act(
    session: Session,
    gateway: WatchGateway,
    *,
    key: str = "k1",
    enabled: bool = True,
    actor: Any = SYSTEM,
    pr_number: int = BUMP,
    expected: str | None | _ReadIt = READ,
):
    return act_on_landing(
        session,
        RevertCommand(
            repository=INERT_REPOSITORY.upper(),
            pr_number=pr_number,
            actor=actor,
            idempotency_key=key,
            expected_status=_current(session, pr_number)
            if isinstance(expected, _ReadIt)
            else expected,
        ),
        gateway,
        enabled=enabled,
        credentials_configured=True,
    )


def stored(engine: Engine) -> LandingRevert | None:
    with Session(engine) as reader:
        return reader.scalar(select(LandingRevert))


# --------------------------------------------------------------------------------------------
# End to end: bad, rerun, still bad, reverted, landed, held, and never again.
# --------------------------------------------------------------------------------------------


def test_a_bad_landing_is_rerun_then_reverted_landed_held_and_never_lands_again(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(
        jobs=failing(), outcome=MergeOutcome(landed=True, commit_sha=REVERT_MERGE, status_code=200)
    )

    # It failed once: the watch asks for one rerun, and the act asks for it.
    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)
    assert (watched.verdict, watched.status) == ("flaky_retry", None)
    assert watched.failed_runs == ((RUN, 1),)
    rerun = act(migrated_session, gateway, key="rerun")
    assert (rerun.verdict, rerun.acted, rerun.status) == ("flaky_retry", True, "rerun_requested")
    assert gateway.reruns == [RUN]
    record = stored(migrated_engine)
    assert record is not None
    assert (record.parent_sha, record.rerun_attempts) == (PARENT, {str(RUN): 1})

    # Asked again before the rerun reports, nothing more happens.
    assert act(migrated_session, gateway, key="again").acted is False
    assert gateway.reruns == [RUN]

    # The rerun failed too, and the commit before the bump passed: bad, and the revert opens.
    gateway.jobs = failing(attempt=2)
    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)
    assert watched.verdict == "bad"
    opened = act(migrated_session, gateway, key="revert")
    assert (opened.status, opened.revert_pr_number) == ("revert_opened", REVERT)
    assert gateway.writes == [
        "tree",
        "commit",
        f"branch sds-revert/pr-{BUMP}",
        f'pull Revert "{TITLE}"',
    ]
    record = stored(migrated_engine)
    assert record is not None
    assert record.bumps == [
        {
            "ecosystem": "uv",
            "directory": "/",
            "dependency": "typer",
            "from_version": "0.20.0",
            "to_version": "0.21.0",
        }
    ]
    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)
    assert (watched.verdict, watched.status, watched.revert_pr_number) == (
        None,
        "revert_opened",
        REVERT,
    )

    # The revert pull request lands through the ordinary inert landing.
    land(migrated_session, gateway, REVERT, REVERT_HEAD)
    record = stored(migrated_engine)
    assert record is not None
    assert (record.status, record.revert_merge_sha) == ("revert_landed", REVERT_MERGE)
    assert record.hold_outcome == "held uv / typer at 0.20.0"
    with Session(migrated_engine) as reader:
        holds = [(h.dependency, h.ceiling) for h in reader.scalars(select(SdsMemberHold))]
    assert holds == [("typer", "0.20.0")]

    # The revert's own landing is never watched, nor even read; the reverted landing is, until
    # its window ends.
    gateway.reads.clear()
    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)
    assert (INERT_REPOSITORY, REVERT) not in gateway.reads
    assert (watched.pr_number, watched.status) == (BUMP, "revert_landed")
    assert watched.hold_outcome == "held uv / typer at 0.20.0"
    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway, key="revert-the-revert", pr_number=REVERT)
    assert raised.value.code == LANDING_REVERT_NOT_WATCHED

    # The same version, proposed again, is refused.
    gateway.pulls[NEXT_BUMP] = bump_pull(NEXT_BUMP, NEXT_HEAD)
    answer = inert_landing_admission(
        migrated_session,
        INERT_REPOSITORY,
        NEXT_BUMP,
        FakeEstateLandingSource(default=EstateAnswer(LANDING_INERT)),
        FakeInertPolicySource(),
        gateway,
        enabled=True,
        credentials_configured=True,
    )
    assert answer.satisfied is False
    assert LANDING_VERSION_REVERTED in answer.refusals


# --------------------------------------------------------------------------------------------
# The watch's population.
# --------------------------------------------------------------------------------------------


def test_a_clean_landing_is_watched_and_nothing_is_acted_on(migrated_session: Session) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs={BUMP_HEAD: HEAD_GREEN, LANDED_COMMIT: (job("test", "success"),)})

    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)
    outcome = act(migrated_session, gateway)

    assert watched.verdict == "clean"
    assert (outcome.verdict, outcome.acted, outcome.status) == ("clean", False, None)
    assert gateway.reruns == [] and gateway.writes == []
    assert migrated_session.scalar(select(LandingRevert)) is None


def test_a_landing_past_the_window_leaves_the_watch(migrated_session: Session) -> None:
    landed_bump(migrated_session)
    migrated_session.execute(
        text("UPDATE estate_pr_merge SET created_at = now() - interval '49 hours'")
    )
    gateway = WatchGateway(jobs=failing())

    assert landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True) == ()
    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway)
    assert raised.value.code == LANDING_REVERT_NOT_WATCHED
    assert gateway.job_reads == []


def _age(session: Session, hours: int) -> None:
    """Move the landing and its record `hours` into the past."""
    for table, column in (("estate_pr_merge", "created_at"), ("landing_reverts", "updated_at")):
        session.execute(
            text(f"UPDATE {table} SET {column} = now() - make_interval(hours => :hours)"),
            {"hours": hours},
        )


def test_a_landing_whose_rerun_passed_leaves_the_watch_when_its_window_closes(
    migrated_session: Session,
) -> None:
    """The common flaky path: the record stays `rerun_requested`, and nothing moves it. Watched
    forever, it would be re-judged through the App on every pass."""
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())
    act(migrated_session, gateway)
    gateway.jobs[LANDED_COMMIT] = (job("test", "success", attempt=2),)
    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)
    assert (watched.status, watched.verdict) == ("rerun_requested", "clean")
    _age(migrated_session, 49)
    gateway.job_reads.clear()

    assert landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True) == ()
    assert gateway.job_reads == []


def test_a_rerun_updated_inside_the_window_keeps_an_old_landing_watched(
    migrated_session: Session,
) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())
    act(migrated_session, gateway)
    _age(migrated_session, 72)
    migrated_session.execute(text("UPDATE landing_reverts SET updated_at = now()"))

    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)

    assert (watched.status, watched.verdict) == ("rerun_requested", "pending")


def test_an_open_revert_keeps_its_landing_watched_however_old(migrated_session: Session) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())
    act(migrated_session, gateway, key="rerun")
    gateway.jobs = failing(attempt=2)
    act(migrated_session, gateway, key="revert")
    _age(migrated_session, 72)

    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)

    assert (watched.status, watched.revert_pr_number) == ("revert_opened", REVERT)


def test_a_fork_sync_is_never_watched(migrated_session: Session) -> None:
    syncs = rules(
        permitted_authors=frozenset({UPDATE_BOT, SYNC_BOT}),
        non_ecosystem_authors=frozenset({SYNC_BOT}),
    )
    landed_bump(
        migrated_session,
        FakeInertPolicySource(InertLandingAnswer(syncs)),
        author_login=SYNC_BOT,
        head_ref=SYNC_BRANCH,
    )
    with Session(migrated_session.get_bind()) as reader:
        assert reader.scalar(text("SELECT count(*) FROM estate_pr_merge")) == 1
    gateway = WatchGateway(
        pulls={BUMP: bump_pull(author_login=SYNC_BOT, head_ref=SYNC_BRANCH)}, jobs=failing()
    )

    assert landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True) == ()
    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway)
    assert raised.value.code == LANDING_REVERT_NOT_WATCHED
    assert gateway.job_reads == []


def test_a_repository_the_record_no_longer_grants_is_never_read(
    migrated_session: Session,
) -> None:
    landed_bump(migrated_session)
    grant_services(migrated_session, INERT_REPOSITORY, "dependency_updates", state="withdrawn")
    gateway = WatchGateway(jobs=failing())

    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)
    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway)

    assert (watched.verdict, watched.detail) == ("unreadable", LANDING_REPOSITORY_NOT_GRANTED)
    assert raised.value.code == LANDING_REPOSITORY_NOT_GRANTED
    assert gateway.reads == [] and gateway.job_reads == [] and gateway.reruns == []


def test_an_unreadable_pull_request_is_reported_never_skipped(migrated_session: Session) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing(), read_error=EstateGatewayError("read_status", 502))

    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)

    assert (watched.verdict, watched.detail) == ("unreadable", "read_status (HTTP 502)")


# --------------------------------------------------------------------------------------------
# The act's bounds and its records.
# --------------------------------------------------------------------------------------------


def test_only_the_system_actor_may_act(migrated_session: Session) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())
    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway, actor=WORKER)
    assert raised.value.code == "role_forbidden"
    assert gateway.reruns == []


def test_a_lane_switched_off_acts_on_nothing(migrated_session: Session) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())
    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway, enabled=False)
    assert raised.value.code == LANDING_REVERT_LANE_OFF
    assert gateway.reruns == [] and gateway.job_reads == []


def test_a_new_failing_run_is_rerun_and_merged_into_the_record(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())
    act(migrated_session, gateway, key="first")
    other = RUN + 5
    gateway.jobs[LANDED_COMMIT] = (
        job("test", "failure", attempt=1),
        Job("lint", "failure", run_id=other, attempt=1, workflow="Lint", event="push"),
    )
    gateway.jobs[BUMP_HEAD] = (*HEAD_GREEN, Job("lint", "success", 8, 1, "Lint", "pull_request"))

    outcome = act(migrated_session, gateway, key="second")

    assert (outcome.verdict, outcome.status) == ("flaky_retry", "rerun_requested")
    assert gateway.reruns == [RUN, other]
    record = stored(migrated_engine)
    assert record is not None
    assert record.rerun_attempts == {str(RUN): 1, str(other): 1}
    assert record.failed_job_names == ["lint", "test"]


def test_a_rerun_the_platform_refuses_records_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing(), rerun_error=EstateGatewayError("rerun_status", 403))

    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway)

    assert raised.value.code == LANDING_REVERT_RERUN_REFUSED
    assert stored(migrated_engine) is None


def test_a_repeated_key_answers_from_its_event_and_acts_no_more(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())
    first = act(migrated_session, gateway, key="same")
    gateway.jobs = failing(attempt=2)

    second = act(migrated_session, gateway, key="same")

    assert second.replayed is True
    assert (second.status, second.verdict) == (first.status, first.verdict)
    assert gateway.reruns == [RUN] and gateway.writes == []
    with Session(migrated_engine) as reader:
        actions = list(reader.scalars(select(Event.action).where(Event.idempotency_key == "same")))
    assert actions == ["landing_revert.rerun_requested"]


def test_a_key_spent_by_another_act_is_refused(migrated_session: Session) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())
    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway, key="land-41:event")
    assert raised.value.code == "idempotency_conflict"


def _rerun_then_bad(session: Session, gateway: WatchGateway) -> None:
    act(session, gateway, key="rerun")
    gateway.jobs = failing(attempt=2)


def test_a_path_changed_since_the_landing_is_refused_and_says_which(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    landed_bump(migrated_session)
    moved = trees()
    moved[BASE] = Tree("tb", {"uv.lock": _entry("l3"), "README.md": _entry("r2")})
    gateway = WatchGateway(jobs=failing(), trees=moved)
    _rerun_then_bad(migrated_session, gateway)

    outcome = act(migrated_session, gateway, key="revert")

    assert outcome.status == "refused_overlap"
    assert gateway.writes == []
    record = stored(migrated_engine)
    assert record is not None
    assert record.status == "refused_overlap"
    assert record.detail == "changed on the default branch since the landing: uv.lock"


def test_a_revert_that_fails_once_opening_began_is_recorded_failed_never_retried(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(
        jobs=failing(), branch_error=EstateGatewayError("branch_create_status", 422)
    )
    _rerun_then_bad(migrated_session, gateway)

    outcome = act(migrated_session, gateway, key="revert")
    again = act(migrated_session, gateway, key="revert-again")

    assert outcome.status == "revert_failed"
    assert again.acted is False
    assert gateway.writes == ["tree", "commit", f"branch sds-revert/pr-{BUMP}"]
    record = stored(migrated_engine)
    assert record is not None
    assert record.detail == "the revert was not opened: branch_create_status (HTTP 422)"


def test_a_tree_too_large_to_read_is_recorded_failed_by_its_name(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    landed_bump(migrated_session)
    unreadable = trees()
    del unreadable[PARENT]  # the double raises `tree_response_invalid` for a tree it lacks
    gateway = WatchGateway(jobs=failing(), trees=unreadable)
    _rerun_then_bad(migrated_session, gateway)

    outcome = act(migrated_session, gateway, key="revert")

    assert outcome.status == "revert_failed"
    record = stored(migrated_engine)
    assert record is not None
    assert record.detail == "the revert could not be planned: tree_response_invalid"


def test_a_plan_read_that_can_clear_records_nothing_and_is_asked_again(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing(), base_head=None)  # repository_response_invalid
    _rerun_then_bad(migrated_session, gateway)

    outcome = act(migrated_session, gateway, key="revert")

    assert (outcome.verdict, outcome.acted) == ("unreadable", False)
    record = stored(migrated_engine)
    assert record is not None
    assert record.status == "rerun_requested"


def test_a_dependency_list_that_cannot_be_read_still_reverts_and_says_nothing_is_barred(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing(), commits={BUMP: ()})
    _rerun_then_bad(migrated_session, gateway)

    outcome = act(migrated_session, gateway, key="revert")

    assert outcome.status == "revert_opened"
    record = stored(migrated_engine)
    assert record is not None
    assert record.bumps == []
    assert record.detail is not None and "not barred" in record.detail


def test_a_record_that_moved_since_the_caller_read_it_is_refused(
    migrated_session: Session,
) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())
    act(migrated_session, gateway, key="rerun")
    gateway.jobs = failing(attempt=2)

    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway, key="stale", expected=None)

    assert raised.value.code == LANDING_REVERT_RECORD_MOVED
    assert gateway.writes == []


# --------------------------------------------------------------------------------------------
# Fix round 1.
# --------------------------------------------------------------------------------------------


def _reverted_and_landed(session: Session, title: str) -> WatchGateway:
    """Land a bump titled `title`, judge it bad after its rerun, open the revert and land it."""
    landed_bump(session, title=title)
    gateway = WatchGateway(
        pulls={BUMP: bump_pull(title=title)},
        jobs=failing(),
        outcome=MergeOutcome(landed=True, commit_sha=REVERT_MERGE, status_code=200),
    )
    act(session, gateway, key="rerun")
    gateway.jobs = failing(attempt=2)
    act(session, gateway, key="revert")
    land(session, gateway, REVERT, REVERT_HEAD)
    return gateway


@pytest.mark.parametrize(
    ("title", "directory"),
    [
        pytest.param(f"{TITLE} in /tools", "/tools", id="in-a-subdirectory"),
        pytest.param(TITLE, "/", id="at-the-root"),
    ],
)
def test_a_single_update_is_held_on_the_directory_its_title_names(
    migrated_session: Session, migrated_engine: Engine, title: str, directory: str
) -> None:
    """The update bot's block never names a directory; its title does (` in /<path>`), and an
    update without that suffix is at the root."""
    _reverted_and_landed(migrated_session, title)

    with Session(migrated_engine) as reader:
        holds = [
            (h.directory, h.dependency, h.ceiling) for h in reader.scalars(select(SdsMemberHold))
        ]
    assert holds == [(directory, "typer", "0.20.0")]


def test_a_failed_revert_stays_watched_however_old(migrated_session: Session) -> None:
    """Main may still be broken and no revert exists: it never leaves the watch on a timer, and
    reporting it reads nothing through the App."""
    landed_bump(migrated_session)
    gateway = WatchGateway(
        jobs=failing(), branch_error=EstateGatewayError("branch_create_status", 422)
    )
    act(migrated_session, gateway, key="rerun")
    gateway.jobs = failing(attempt=2)
    act(migrated_session, gateway, key="revert")
    _age(migrated_session, 72)
    gateway.reads.clear()
    gateway.job_reads.clear()

    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)

    assert watched.status == "revert_failed"
    assert gateway.reads == [] and gateway.job_reads == []


def test_the_watch_refuses_when_the_lane_is_off(migrated_session: Session) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())

    for enabled, configured in ((False, True), (True, False)):
        with pytest.raises(DomainError) as raised:
            landing_watch(
                migrated_session, gateway, enabled=enabled, credentials_configured=configured
            )
        assert raised.value.code == LANDING_REVERT_LANE_OFF
    assert gateway.reads == [] and gateway.job_reads == []


def test_a_key_spent_on_another_landing_is_refused(migrated_session: Session) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())
    act(migrated_session, gateway, key="shared")
    gateway.jobs = failing(attempt=2)

    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway, key="shared", pr_number=NEXT_BUMP, expected=None)

    assert raised.value.code == "idempotency_conflict"


# --------------------------------------------------------------------------------------------
# Final review: a landed revert stays watched while its dependency is held.
# --------------------------------------------------------------------------------------------


def test_a_landed_revert_stays_watched_while_its_hold_stands_and_leaves_when_it_is_lifted(
    migrated_session: Session,
) -> None:
    """Its alert is open while it is watched, and the hold is still in force: it must not resolve
    on a timer. Reporting it reads nothing through the App."""
    gateway = _reverted_and_landed(migrated_session, TITLE)
    _age(migrated_session, 72)
    gateway.reads.clear()
    gateway.job_reads.clear()

    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)
    assert (watched.pr_number, watched.status, watched.verdict) == (BUMP, "revert_landed", None)
    assert gateway.reads == [] and gateway.job_reads == []

    migrated_session.execute(text("DELETE FROM sds_member_holds"))
    assert landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True) == ()


def test_a_hold_a_person_placed_on_the_same_dependency_keeps_a_landed_revert_watched(
    migrated_session: Session,
) -> None:
    """The revert's own hold was not placed (the dependency was already held), and the dependency
    is held all the same, matched by the hold matcher's identity (directory `/` against `/`,
    the name case-folded)."""
    gateway = _reverted_and_landed(migrated_session, TITLE)
    migrated_session.execute(
        text("UPDATE sds_member_holds SET set_by = 'devon', dependency = 'Typer', ceiling = '0.19'")
    )
    _age(migrated_session, 72)

    [watched] = landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True)

    assert (watched.pr_number, watched.status) == (BUMP, "revert_landed")


def test_a_landed_revert_whose_hold_is_on_another_dependency_leaves_after_its_window(
    migrated_session: Session,
) -> None:
    gateway = _reverted_and_landed(migrated_session, TITLE)
    migrated_session.execute(text("UPDATE sds_member_holds SET dependency = 'click'"))
    _age(migrated_session, 72)

    assert landing_watch(migrated_session, gateway, enabled=True, credentials_configured=True) == ()
