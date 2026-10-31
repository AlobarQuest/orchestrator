"""The watch and the revert cover the factory's own merges (Landing Part 4, Task 6).

Each landing is made by the factory's own act, so the row and its event are production's. The
watch, the revert, the hold and the never-again rule are Part 3a's; what is new is where a factory
landing's facts come from, and which of its merges are watched.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import (
    Event,
    LandingRevert,
    SdsMemberHold,
    WorkPackage,
    WorkUnit,
)
from orchestrator.services.landing.inert_landing_admission import inert_landing_admission
from orchestrator.services.landing.interfaces import EstatePullRequest, MergeOutcome
from orchestrator.services.landing.pr_merge import (
    MergeCommand,
    PullRequestState,
    land_unit_pull_request,
)
from orchestrator.services.landing.pr_merge import MergeOutcome as UnitMergeOutcome
from orchestrator.services.landing.pr_merge_admission import (
    MERGE_DEPENDENCY_BUMP_UNREADABLE,
    MERGE_REPOSITORY_REVERTING,
    MERGE_VERSION_REVERTED,
)
from orchestrator.services.landing.revert_act import (
    LANDING_REVERT_LANE_OFF,
    LANDING_REVERT_NOT_WATCHED,
    RevertCommand,
    act_on_landing,
    landing_watch,
)
from orchestrator.services.landing.terms import (
    LANDING_REPOSITORY_NOT_GRANTED,
    LANDING_VERSION_REVERTED,
)
from tests.services.change_record_doubles import approved_record_source, no_record_source
from tests.services.estate_doubles import inert_source, redeploying_source
from tests.services.estate_landing_doubles import dependabot_commit, pull_request
from tests.services.inert_landing_doubles import (
    INERT_REPOSITORY,
    LANDED_COMMIT,
    FakeInertPolicySource,
)
from tests.services.test_member_proposals import grant_services
from tests.services.test_pr_merge import FakeGateway
from tests.services.test_pr_merge_admission import HEAD, IN_WINDOW, SYSTEM, FixedClock, _revision
from tests.services.test_pr_merge_admission import _approve_authority as approve_authority
from tests.services.test_pr_merge_bump_policy import REPOSITORY, STANDING_FIELDS, _answer, _bump
from tests.services.test_revert_act import (
    APP,
    BUMP,
    REVERT,
    REVERT_HEAD,
    REVERT_MERGE,
    WatchGateway,
    bump_pull,
    failing,
    job,
    land,
    landed_bump,
    lanes,
)

FACTORY_PR = 7
TITLE = "chore(deps): bump httpx2 to 2.16.0"
BRANCH = "factory/run-1"
NPM_FIELDS = {**STANDING_FIELDS, "package": "left-pad", "to_version": "2.16.0"}


def factory_pull() -> EstatePullRequest:
    return pull_request(
        number=FACTORY_PR, title=TITLE, head_sha=HEAD, head_ref=BRANCH, author_login=APP
    )


def merged(session: Session, *, key: str = "factory-bump", source: Any = None, **kwargs: Any):
    """The factory lands a standing bump, through its own act, into the given estate answer."""
    unit = _bump(session, key, **kwargs)
    return _landed(session, unit, source or inert_source())


def _landed(session: Session, unit: WorkUnit, source: Any):
    return land_unit_pull_request(
        session,
        MergeCommand(
            unit_id=unit.id,
            actor=SYSTEM,
            idempotency_key=f"merge-{unit.id}",
            expected_version=unit.version,
        ),
        FakeGateway(outcome=UnitMergeOutcome(True, LANDED_COMMIT, 200)),
        source,
        approved_record_source(REPOSITORY, FACTORY_PR),
        clock=FixedClock(IN_WINDOW),
        bump_merge_enabled=True,
    )


def gateway_for(**kwargs: Any) -> WatchGateway:
    kwargs.setdefault("pulls", {FACTORY_PR: factory_pull()})
    return WatchGateway(
        jobs=factory_failing(),
        outcome=MergeOutcome(landed=True, commit_sha=REVERT_MERGE, status_code=200),
        **kwargs,
    )


def factory_failing(attempt: int = 1) -> dict[str, Any]:
    """As Part 3a's `failing`, at the factory pull request's own head."""
    jobs = dict(failing(attempt))
    jobs[HEAD] = jobs.pop("1" * 40)
    return jobs


def act(session: Session, gateway: WatchGateway, *, key: str, pr_number: int = FACTORY_PR):
    status = session.scalar(
        select(LandingRevert.status).where(LandingRevert.pr_number == pr_number)
    )
    return act_on_landing(
        session,
        RevertCommand(
            repository=REPOSITORY,
            pr_number=pr_number,
            actor=SYSTEM,
            idempotency_key=key,
            expected_status=status,
        ),
        **lanes(gateway),
    )


def reverted(session: Session, gateway: WatchGateway) -> None:
    """Rerun, still bad, revert opened, revert landed: the whole sequence."""
    act(session, gateway, key="rerun")
    gateway.jobs = factory_failing(attempt=2)
    act(session, gateway, key="revert")
    land(session, gateway, REVERT, REVERT_HEAD)


def record_of(engine: Engine) -> LandingRevert | None:
    with Session(engine) as reader:
        return reader.scalar(select(LandingRevert))


def test_a_factory_merged_bump_that_fails_on_main_is_reverted_and_held(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    merged(migrated_session)
    grant_services(migrated_session, INERT_REPOSITORY, "security_patches")
    gateway = gateway_for()

    # Merged, and the run failed: one rerun.
    [watched] = landing_watch(migrated_session, **lanes(gateway), only_lane="inert")
    assert (watched.repository, watched.pr_number) == (INERT_REPOSITORY, FACTORY_PR)
    assert (watched.verdict, watched.lane) == ("flaky_retry", "inert")
    assert watched.merge_commit_sha == LANDED_COMMIT
    assert act(migrated_session, gateway, key="rerun").status == "rerun_requested"

    # The rerun failed too, and the parent passed: the revert opens, on what the unit said.
    gateway.jobs = factory_failing(attempt=2)
    [watched] = landing_watch(migrated_session, **lanes(gateway))
    assert watched.verdict == "bad"
    opened = act(migrated_session, gateway, key="revert")
    assert (opened.status, opened.revert_pr_number) == ("revert_opened", REVERT)
    record = record_of(migrated_engine)
    assert record is not None
    assert record.repository == INERT_REPOSITORY
    assert record.bumps == [
        {
            "ecosystem": "uv",
            "directory": "/",
            "dependency": "httpx2",
            "from_version": "2.13.1",
            "to_version": "2.16.0",
        }
    ]
    assert record.detail is None
    assert gateway.writes[-1] == f'pull Revert "{TITLE}"'
    assert gateway.writes[2] == f"branch sds-revert/pr-{FACTORY_PR}"

    # It lands through the inert lane: the hold, in that transaction.
    land(migrated_session, gateway, REVERT, REVERT_HEAD)
    record = record_of(migrated_engine)
    assert record is not None
    assert (record.status, record.revert_merge_sha) == ("revert_landed", REVERT_MERGE)
    assert record.hold_outcome == "held uv / httpx2 at 2.13.1"
    with Session(migrated_engine) as reader:
        holds = [
            (h.ecosystem, h.directory, h.dependency, h.ceiling, h.set_by)
            for h in reader.scalars(select(SdsMemberHold))
        ]
    assert holds == [("uv", "/", "httpx2", "2.13.1", "sds-revert")]

    # What the alert reads: the record stays in the watch, past its window, while it is held.
    migrated_session.execute(
        text(
            "UPDATE unit_pr_merge SET created_at = now() - interval '5 days';"
            "UPDATE estate_pr_merge SET created_at = now() - interval '5 days'"
        )
    )
    migrated_session.execute(
        text("UPDATE landing_reverts SET updated_at = now() - interval '5 days'")
    )
    migrated_session.commit()
    [watched] = landing_watch(migrated_session, **lanes(gateway))
    assert (watched.pr_number, watched.status) == (FACTORY_PR, "revert_landed")
    assert watched.hold_outcome == "held uv / httpx2 at 2.13.1"


def test_a_factory_merge_of_another_change_class_is_not_watched(
    migrated_session: Session,
) -> None:
    unit = _bump(migrated_session, "feature", change_class="app-change", profile=None)
    grant_services(migrated_session, REPOSITORY, "dependency_updates", "app_changes")
    approve_authority(migrated_session, unit)
    assert _landed(migrated_session, unit, inert_source()).status == "merged"
    gateway = gateway_for()

    assert landing_watch(migrated_session, **lanes(gateway)) == ()
    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway, key="k")
    assert raised.value.code == LANDING_REVERT_NOT_WATCHED


def test_a_factory_merge_declared_a_bump_by_its_profile_alone_is_watched(
    migrated_session: Session,
) -> None:
    """A unit can't leave the watch by declaring only one of the two, as with the hold term."""
    unit = _bump(migrated_session, "profile-only", change_class="app-change")
    grant_services(migrated_session, REPOSITORY, "dependency_updates", "app_changes")
    approve_authority(migrated_session, unit)
    _landed(migrated_session, unit, inert_source())

    [watched] = landing_watch(migrated_session, **lanes(gateway_for()))
    assert watched.pr_number == FACTORY_PR


def test_a_factory_merge_declared_a_bump_by_its_envelope_alone_is_watched(
    migrated_session: Session,
) -> None:
    unit = _bump(migrated_session, "envelope-only", profile=None)
    approve_authority(migrated_session, unit)
    _landed(migrated_session, unit, inert_source())

    [watched] = landing_watch(migrated_session, **lanes(gateway_for()))
    assert watched.pr_number == FACTORY_PR


def test_an_act_finds_the_factory_merge_of_its_own_repository(
    migrated_session: Session,
) -> None:
    """One pull request number in two repositories: another repository's record stays apart."""
    merged(migrated_session)
    other = "alobarquest/elsewhere"
    grant_services(migrated_session, other, "dependency_updates")

    with pytest.raises(DomainError) as raised:
        act_on_landing(
            migrated_session,
            RevertCommand(
                repository=other,
                pr_number=FACTORY_PR,
                actor=SYSTEM,
                idempotency_key="k",
                expected_status=None,
            ),
            **lanes(gateway_for()),
        )
    assert raised.value.code == LANDING_REVERT_NOT_WATCHED


def test_the_watch_runs_oldest_first_across_both_tables(migrated_session: Session) -> None:
    merged(migrated_session)
    landed_bump(migrated_session)
    gateway = gateway_for()
    gateway.pulls[BUMP] = bump_pull()
    migrated_session.execute(
        text("UPDATE unit_pr_merge SET created_at = now() - interval '1 hour'")
    )
    migrated_session.commit()

    numbers = [row.pr_number for row in landing_watch(migrated_session, **lanes(gateway))]
    assert numbers == [FACTORY_PR, BUMP]

    migrated_session.execute(
        text("UPDATE unit_pr_merge SET created_at = now() + interval '1 hour'")
    )
    migrated_session.commit()
    numbers = [row.pr_number for row in landing_watch(migrated_session, **lanes(gateway))]
    assert numbers == [BUMP, FACTORY_PR]


def test_a_factory_merge_in_a_deploying_repository_is_not_watched(
    migrated_session: Session,
) -> None:
    unit = _bump(migrated_session, "deploying")
    approve_authority(migrated_session, unit)
    assert _landed(migrated_session, unit, redeploying_source()).status == "merged"
    gateway = gateway_for()

    assert landing_watch(migrated_session, **lanes(gateway)) == ()
    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway, key="k")
    assert raised.value.code == LANDING_REVERT_NOT_WATCHED


def test_a_merge_the_factory_did_not_make_is_not_watched(migrated_session: Session) -> None:
    """`already_merged` is somebody else's landing, and `refused` is none."""
    unit = _bump(migrated_session, "someone-else")
    record = land_unit_pull_request(
        migrated_session,
        MergeCommand(
            unit_id=unit.id, actor=SYSTEM, idempotency_key="k", expected_version=unit.version
        ),
        FakeGateway(
            state=PullRequestState(base_ref="main", default_branch="main", open=False, landed=True)
        ),
        inert_source(),
        no_record_source(),
        clock=FixedClock(IN_WINDOW),
        bump_merge_enabled=True,
    )
    assert record.status == "already_merged"
    assert landing_watch(migrated_session, **lanes(gateway_for())) == ()


def test_a_factory_merge_leaves_the_watch_with_its_window(migrated_session: Session) -> None:
    merged(migrated_session)
    migrated_session.execute(
        text("UPDATE unit_pr_merge SET created_at = now() - interval '49 hours'")
    )
    migrated_session.commit()
    assert landing_watch(migrated_session, **lanes(gateway_for())) == ()


def test_a_factory_landing_is_under_the_grant_term(migrated_session: Session) -> None:
    merged(migrated_session)
    grant_services(migrated_session, REPOSITORY, "dependency_updates", state="withdrawn")
    gateway = gateway_for()

    [watched] = landing_watch(migrated_session, **lanes(gateway))
    assert (watched.verdict, watched.detail) == ("unreadable", LANDING_REPOSITORY_NOT_GRANTED)
    assert gateway.job_reads == []


def test_a_factory_landing_is_under_the_inert_lanes_switch(migrated_session: Session) -> None:
    merged(migrated_session)
    gateway = gateway_for()

    [watched] = landing_watch(migrated_session, **lanes(gateway, inert=False))
    assert (watched.verdict, watched.detail) == ("unreadable", LANDING_REVERT_LANE_OFF)
    assert gateway.job_reads == []


def test_the_estate_lanes_watch_reads_nothing_for_a_factory_landing(
    migrated_session: Session,
) -> None:
    merged(migrated_session)
    gateway = gateway_for()

    assert landing_watch(migrated_session, **lanes(gateway), only_lane="estate") == ()
    assert gateway.job_reads == [] and gateway.reads == []


def test_a_factory_landing_is_judged_without_reading_its_pull_request(
    migrated_session: Session,
) -> None:
    """Its subject is its record, so the update bot's author check is never asked of it."""
    merged(migrated_session)
    gateway = gateway_for()

    [watched] = landing_watch(migrated_session, **lanes(gateway))
    assert watched.verdict == "flaky_retry"
    assert gateway.reads == []


def test_a_clean_factory_landing_acts_on_nothing(migrated_session: Session) -> None:
    merged(migrated_session)
    gateway = gateway_for()
    gateway.jobs = {HEAD: (job("test", "success", event="pull_request"),), LANDED_COMMIT: ()}
    gateway.jobs[LANDED_COMMIT] = (job("test", "success"),)

    [watched] = landing_watch(migrated_session, **lanes(gateway))
    assert watched.verdict == "clean"
    assert act(migrated_session, gateway, key="k").acted is False


def test_a_bump_a_revision_cannot_name_is_reverted_and_says_nothing_is_held(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    fields = {key: value for key, value in STANDING_FIELDS.items() if key != "package"}
    unit = _bump(migrated_session, "nameless", fields=fields)
    approve_authority(migrated_session, unit)
    _landed(migrated_session, unit, inert_source())
    gateway = gateway_for()
    act(migrated_session, gateway, key="rerun")
    gateway.jobs = factory_failing(attempt=2)

    assert act(migrated_session, gateway, key="revert").status == "revert_opened"
    record = record_of(migrated_engine)
    assert record is not None
    assert record.bumps == []
    assert "names no bump" in (record.detail or "")


def test_a_from_version_that_is_not_a_string_reads_as_none_and_holds_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    grant_services(migrated_session, INERT_REPOSITORY, "security_patches")
    unit = _bump(migrated_session, "number", fields={**STANDING_FIELDS, "from_version": 2.13})
    approve_authority(migrated_session, unit)
    _landed(migrated_session, unit, inert_source())
    gateway = gateway_for()
    reverted(migrated_session, gateway)

    record = record_of(migrated_engine)
    assert record is not None
    assert record.bumps[0]["from_version"] is None
    assert record.hold_outcome == "not held uv / httpx2: the version before the bump is unknown"


def test_the_reverted_version_never_lands_again_from_the_update_bot(
    migrated_session: Session,
) -> None:
    merged(migrated_session)
    grant_services(migrated_session, INERT_REPOSITORY, "security_patches")
    gateway = gateway_for()
    reverted(migrated_session, gateway)
    migrated_session.execute(text("DELETE FROM sds_member_holds"))
    migrated_session.commit()

    title = "build(deps): bump httpx2 from 2.13.1 to 2.16.0"
    number = 50
    head = "5" * 40
    gateway.pulls[number] = pull_request(
        number=number, title=title, head_sha=head, head_ref="dependabot/uv/httpx2-2.16.0"
    )
    gateway._commits[number] = (
        dependabot_commit(
            head,
            f"{title}\n\n---\nupdated-dependencies:\n- dependency-name: httpx2\n"
            "  dependency-version: 2.16.0\n  dependency-type: direct:production\n...\n",
        ),
    )
    answer = inert_landing_admission(
        migrated_session,
        INERT_REPOSITORY,
        number,
        inert_source(),
        FakeInertPolicySource(),
        gateway,
        enabled=True,
        credentials_configured=True,
    )
    assert LANDING_VERSION_REVERTED in answer.refusals


def test_a_landed_revert_is_not_watched_and_not_reverted(migrated_session: Session) -> None:
    merged(migrated_session)
    grant_services(migrated_session, INERT_REPOSITORY, "security_patches")
    gateway = gateway_for()
    reverted(migrated_session, gateway)

    assert [row.pr_number for row in landing_watch(migrated_session, **lanes(gateway))] == [
        FACTORY_PR
    ]
    with pytest.raises(DomainError) as raised:
        act(migrated_session, gateway, key="revert-the-revert", pr_number=REVERT)
    assert raised.value.code == LANDING_REVERT_NOT_WATCHED


# --------------------------------------------------------------------------------------------
# The factory's merge honours the never-again rule and the revert in progress.
# --------------------------------------------------------------------------------------------


def reverted_record(session: Session, *, status: str = "revert_landed", to_version: str) -> None:
    session.add(
        LandingRevert(
            repository=INERT_REPOSITORY,
            pr_number=99,
            merge_commit_sha="9" * 40,
            parent_sha="8" * 40,
            failed_job_names=["test"],
            failed_run_id=1,
            status=status,
            bumps=[
                {
                    "ecosystem": "uv",
                    "directory": "/",
                    "dependency": "HTTPX2",
                    "from_version": "2.13.1",
                    "to_version": to_version,
                }
            ],
        )
    )
    session.commit()


@pytest.mark.parametrize(
    ("reverted_version", "refused"),
    [
        ("2.16.0", True),  # the floor is the version reverted
        ("2.16", True),  # the same release, spelled shorter
        ("2.17.0", True),  # the floor is below what was reverted, so the resolver may bring it
        ("2.15.0", False),  # the floor is above it, so it can't
        ("not-a-version", True),
    ],
)
def test_a_uv_floor_that_can_bring_a_reverted_version_is_refused(
    migrated_session: Session, reverted_version: str, refused: bool
) -> None:
    reverted_record(migrated_session, to_version=reverted_version)
    unit = _bump(migrated_session, "uv")

    assert (MERGE_VERSION_REVERTED in _answer(migrated_session, unit).refusals) is refused


@pytest.mark.parametrize(
    ("reverted_version", "refused"),
    [("2.16.0", True), ("2.16", True), ("2.17.0", False), ("2.15.0", False)],
)
def test_an_exact_bump_is_refused_only_on_the_version_reverted(
    migrated_session: Session, reverted_version: str, refused: bool
) -> None:
    migrated_session.add(
        LandingRevert(
            repository=INERT_REPOSITORY,
            pr_number=99,
            merge_commit_sha="9" * 40,
            parent_sha="8" * 40,
            failed_job_names=["test"],
            failed_run_id=1,
            status="revert_landed",
            bumps=[
                {
                    "ecosystem": "npm_and_yarn",
                    "directory": "/",
                    "dependency": "left-pad",
                    "from_version": "2.13.1",
                    "to_version": reverted_version,
                }
            ],
        )
    )
    migrated_session.commit()
    unit = _npm_bump(migrated_session)

    assert (MERGE_VERSION_REVERTED in _answer(migrated_session, unit).refusals) is refused


def _npm_bump(session: Session) -> WorkUnit:
    """A unit whose package id names npm; only that spelling differs from the uv unit."""
    unit = _bump(session, "npm", fields=NPM_FIELDS)
    package = session.get(WorkPackage, _revision(session, unit).work_package_id)
    assert package is not None
    package.package_id = "orchestrator-npm-left-pad"
    session.commit()
    return unit


def test_another_dependency_reverted_refuses_nothing(migrated_session: Session) -> None:
    migrated_session.add(
        LandingRevert(
            repository=INERT_REPOSITORY,
            pr_number=99,
            merge_commit_sha="9" * 40,
            parent_sha="8" * 40,
            failed_job_names=["test"],
            failed_run_id=1,
            status="revert_landed",
            bumps=[
                {
                    "ecosystem": "uv",
                    "directory": "/",
                    "dependency": "other",
                    "from_version": "1.0",
                    "to_version": "2.16.0",
                }
            ],
        )
    )
    migrated_session.commit()
    unit = _bump(migrated_session, "uv")

    assert _answer(migrated_session, unit).satisfied is True


def test_a_revert_that_never_landed_bars_no_version(migrated_session: Session) -> None:
    reverted_record(migrated_session, status="refused_overlap", to_version="2.16.0")
    unit = _bump(migrated_session, "uv")

    assert _answer(migrated_session, unit).satisfied is True


def test_a_bump_the_revision_cannot_name_refuses_beside_a_reverted_version(
    migrated_session: Session,
) -> None:
    reverted_record(migrated_session, to_version="2.16.0")
    fields = {key: value for key, value in STANDING_FIELDS.items() if key != "package"}
    unit = _bump(migrated_session, "nameless", fields=fields)

    assert MERGE_DEPENDENCY_BUMP_UNREADABLE in _answer(migrated_session, unit).refusals


def test_a_revert_in_progress_holds_the_factorys_merge(migrated_session: Session) -> None:
    reverted_record(migrated_session, status="revert_opened", to_version="1.0")
    unit = _bump(migrated_session, "uv")

    assert _answer(migrated_session, unit).refusals == (MERGE_REPOSITORY_REVERTING,)


def test_a_revert_in_progress_does_not_hold_another_class(migrated_session: Session) -> None:
    reverted_record(migrated_session, status="revert_opened", to_version="1.0")
    unit = _bump(migrated_session, "feature", change_class="app-change", profile=None)

    assert MERGE_REPOSITORY_REVERTING not in _answer(migrated_session, unit).refusals


def test_the_merge_records_whether_landing_was_inert(migrated_session: Session) -> None:
    """The fact the watch selects on: the estate's answer at the act, as an estate row's action
    says which lane landed it."""
    record = merged(migrated_session)
    deploying = _bump(migrated_session, "deploying")
    approve_authority(migrated_session, deploying)
    other = _landed(migrated_session, deploying, redeploying_source())

    answers = [migrated_session.get(Event, row.event_id) for row in (record, other)]
    assert [event.payload["landing_inert"] for event in answers if event is not None] == [
        True,
        False,
    ]
