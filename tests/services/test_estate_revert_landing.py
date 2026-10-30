"""The SDS's revert of a bump lands through the estate lane. Landing Part 3b, Task 4.

A revert record's pull request is asked Part 3a's revert terms (recorded, current with its base,
the exact inverse) and the served `revert_landing` conditions (deploy policy 14): the author must
be one they permit, and the pace and the change window are waived as they say. A bump is asked
whether a revert is in progress in its repository. The record's class and the pull request must
agree both ways: a revert record is a revert branch the SDS opened, and a bump's record is the
update bot's branch.

Everything runs with no network.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import LandingRevert, SdsMemberHold
from orchestrator.services.landing.change_record import (
    ChangeRecordAnswer,
    LandingConditions,
    RevertLandingConditions,
)
from orchestrator.services.landing.estate_landing_admission import (
    LANDING_AUTHOR_NOT_THE_UPDATE_BOT,
    LANDING_RECORD_CLASS_MISMATCH,
    LANDING_RECORD_CLASS_UNKNOWN,
    LANDING_REVERT_AUTHOR_NOT_PERMITTED,
    EstateLandingAdmission,
    estate_landing_admission,
)
from orchestrator.services.landing.estate_pr_merge import (
    EstateMergeCommand,
    land_estate_pull_request,
)
from orchestrator.services.landing.interfaces import (
    LANDING_HEAD_NOT_CURRENT_WITH_BASE,
    LANDING_OUTSIDE_CHANGE_WINDOW,
    LANDING_PACE_EXHAUSTED,
    BranchHead,
)
from orchestrator.services.landing.revert_build import REVERT_BRANCH_PREFIX
from orchestrator.services.landing.revert_subject import (
    LANDING_REPOSITORY_REVERTING,
    LANDING_REVERT_NOT_EXACT_INVERSE,
    LANDING_REVERT_NOT_RECORDED,
)
from tests.services.change_record_doubles import FakeChangeRecordSource
from tests.services.estate_doubles import redeploying_source
from tests.services.estate_landing_doubles import (
    HEAD,
    REPOSITORY,
    FakeEstateGateway,
    approved,
    outcome_conditions,
    pull_request,
)
from tests.services.test_estate_landing_admission import (
    IN_WINDOW,
    OUT_OF_WINDOW,
    FixedClock,
    _landed_row,
)
from tests.services.test_estate_pr_merge import LANDED_COMMIT, SYSTEM, ActingGateway
from tests.services.test_member_holds import place_hold
from tests.services.test_member_proposals import grant_services
from tests.services.test_revert_hold import member_of, revert_record
from tests.services.test_revert_landing import BASE, MERGE, PARENT, _entry, _trees

ORIGINAL = 41
REVERT = 42
BRANCH = f"{REVERT_BRANCH_PREFIX}pr-{ORIGINAL}"
APP = "alobar-sds-dispatch[bot]"
REVERT_CLASS = "dependency-revert"


def v14(
    *,
    authors: frozenset[str] = frozenset({APP}),
    exempt_from_pace: bool = True,
    exempt_from_change_window: bool = True,
) -> LandingConditions:
    """Deploy policy 14 as change-manager serves it: version 13's bump terms and the revert's."""
    return replace(
        outcome_conditions(version=14),
        revert_landing=RevertLandingConditions(
            change_class=REVERT_CLASS,
            permitted_authors=authors,
            exempt_from_pace=exempt_from_pace,
            exempt_from_change_window=exempt_from_change_window,
        ),
    )


def revert_record_answer(
    *, conditions: LandingConditions | None = None, change_class: str | None = REVERT_CLASS
) -> ChangeRecordAnswer:
    return approved(
        policy_version=14,
        landing_conditions=v14() if conditions is None else conditions,
        change_class=change_class,
        pull_request_number=REVERT,
    )


def _gateway(**kwargs: Any) -> FakeEstateGateway:
    """The revert pull request, opened by the App on the record's branch, exactly inverse."""
    return FakeEstateGateway(
        pull=pull_request(
            number=REVERT,
            head_ref=kwargs.pop("head_ref", BRANCH),
            title='Revert "build(deps): bump alembic from 1.18.5 to 1.19.0"',
            author_login=kwargs.pop("author_login", APP),
            author_is_bot=kwargs.pop("author_is_bot", True),
        ),
        base_head=BranchHead(name="main", sha=BASE),
        trees=kwargs.pop("trees", _trees()),
        **kwargs,
    )


def _opened(session: Session, status: str = "revert_opened") -> None:
    row = revert_record(
        session,
        status=status,
        repository=REPOSITORY,
        pr_number=ORIGINAL,
        revert_pr_number=REVERT,
    )
    row.merge_commit_sha, row.parent_sha = MERGE, PARENT
    session.flush()


def _ask(
    session: Session,
    *,
    record: ChangeRecordAnswer | None = None,
    gateway: FakeEstateGateway | None = None,
    moment: datetime = OUT_OF_WINDOW,
    number: int = REVERT,
) -> EstateLandingAdmission:
    grant_services(session, REPOSITORY, "dependency_updates")
    return estate_landing_admission(
        session,
        REPOSITORY,
        number,
        redeploying_source(),
        FakeChangeRecordSource({(REPOSITORY, number): record or revert_record_answer()}),
        gateway or _gateway(),
        enabled=True,
        credentials_configured=True,
        clock=FixedClock(moment),
    )


def _landed_tonight(session: Session) -> None:
    """A landing in the window `IN_WINDOW` falls in: the pace is spent for this repository."""
    _landed_row(session, moment=datetime(2026, 8, 11, 6, 45, tzinfo=UTC))


def test_a_recorded_exact_revert_lands_outside_the_window_and_past_the_pace(
    migrated_session: Session,
) -> None:
    """V14 waives both for the revert: production is pinned to the build before the bump until it
    lands, so it does not wait for tonight's window. None of a bump's terms is asked either: the
    dependency is held and the author is no update bot."""
    _opened(migrated_session)
    _landed_tonight(migrated_session)
    place_hold(migrated_session, REPOSITORY, ecosystem="uv", dependency="alembic", ceiling="0")
    gateway = _gateway()

    answer = _ask(migrated_session, gateway=gateway)

    assert answer.refusals == ()
    assert answer.satisfied is True
    assert answer.head_sha == HEAD
    assert gateway.tree_reads == [PARENT, MERGE, BASE, HEAD]
    # A revert reads no rollout pin and no update-bot commit.
    assert gateway.blobs == [] and gateway.commit_reads == []


def test_a_version_that_does_not_waive_them_keeps_the_window_and_the_pace(
    migrated_session: Session,
) -> None:
    _opened(migrated_session)
    _landed_tonight(migrated_session)
    strict = v14(exempt_from_pace=False, exempt_from_change_window=False)

    outside = _ask(migrated_session, record=revert_record_answer(conditions=strict))
    inside = _ask(
        migrated_session, record=revert_record_answer(conditions=strict), moment=IN_WINDOW
    )

    assert outside.refusals == (LANDING_OUTSIDE_CHANGE_WINDOW,)
    assert LANDING_PACE_EXHAUSTED in inside.refusals and inside.satisfied is False


def test_a_revert_record_under_a_version_with_no_revert_conditions_is_refused(
    migrated_session: Session,
) -> None:
    """Version 13 serves no `revert_landing`: it admits no revert class, so the record's class is
    one this lane does not land, and none of the revert's waivers applies."""
    _opened(migrated_session)

    answer = _ask(
        migrated_session, record=revert_record_answer(conditions=outcome_conditions(version=13))
    )

    assert answer.satisfied is False
    assert LANDING_RECORD_CLASS_UNKNOWN in answer.refusals
    assert LANDING_OUTSIDE_CHANGE_WINDOW in answer.refusals


@pytest.mark.parametrize("change_class", [None, "factory-delivery"])
def test_a_record_of_a_class_this_lane_does_not_land_is_refused(
    migrated_session: Session, change_class: str | None
) -> None:
    answer = _ask(
        migrated_session,
        record=approved(change_class=change_class, landing_conditions=v14()),
        gateway=FakeEstateGateway(),
        moment=IN_WINDOW,
        number=49,
    )

    assert answer.satisfied is False
    assert LANDING_RECORD_CLASS_UNKNOWN in answer.refusals


@pytest.mark.parametrize("author", ["dependabot[bot]", "someone-else[bot]", "AlobarQuest"])
def test_a_revert_record_whose_pull_request_another_account_opened_is_refused(
    migrated_session: Session, author: str
) -> None:
    _opened(migrated_session)

    answer = _ask(migrated_session, gateway=_gateway(author_login=author))

    assert answer.satisfied is False
    assert LANDING_REVERT_AUTHOR_NOT_PERMITTED in answer.refusals


def test_a_revert_record_whose_pull_request_is_not_on_a_revert_branch_is_a_mismatch(
    migrated_session: Session,
) -> None:
    _opened(migrated_session)
    gateway = _gateway(head_ref="dependabot/uv/alembic-1.19.0")

    answer = _ask(migrated_session, gateway=gateway)

    assert answer.satisfied is False
    assert answer.refusals == (LANDING_RECORD_CLASS_MISMATCH,)
    assert gateway.tree_reads == []


@pytest.mark.parametrize("status", [None, "repointed", "revert_landed", "revert_failed"])
def test_a_revert_record_with_no_opened_revert_naming_it_is_refused(
    migrated_session: Session, status: str | None
) -> None:
    if status is not None:
        _opened(migrated_session, status)

    answer = _ask(migrated_session)

    assert answer.satisfied is False
    assert LANDING_REVERT_NOT_RECORDED in answer.refusals


def test_a_revert_no_longer_the_exact_inverse_is_refused(migrated_session: Session) -> None:
    _opened(migrated_session)
    extra = {"uv.lock": _entry("l1"), "README.md": _entry("r2"), "pyproject.toml": _entry("x")}

    answer = _ask(migrated_session, gateway=_gateway(trees=_trees(revert=extra)))

    assert answer.refusals == (LANDING_REVERT_NOT_EXACT_INVERSE,)


def test_a_revert_behind_its_base_is_refused_for_freshness_and_qualifies_to_be_brought_up(
    migrated_session: Session,
) -> None:
    _opened(migrated_session)

    answer = _ask(migrated_session, gateway=_gateway(behind=2))

    assert answer.refusals == (LANDING_HEAD_NOT_CURRENT_WITH_BASE,)
    assert answer.branch_update_qualifies is True


def test_a_bump_record_on_a_revert_branch_is_a_mismatch(migrated_session: Session) -> None:
    """The other direction: a `dependency-update` record is the update bot's, never a branch the
    SDS opened -- whoever pushed it."""
    gateway = FakeEstateGateway(
        pull=pull_request(head_ref=BRANCH, author_login="dependabot[bot]", author_is_bot=True)
    )

    answer = _ask(
        migrated_session,
        record=approved(policy_version=14, landing_conditions=v14()),
        gateway=gateway,
        moment=IN_WINDOW,
        number=49,
    )

    assert answer.satisfied is False
    assert LANDING_RECORD_CLASS_MISMATCH in answer.refusals


def test_a_bump_record_the_app_opened_is_refused_as_not_the_update_bot(
    migrated_session: Session,
) -> None:
    gateway = FakeEstateGateway(pull=pull_request(author_login=APP, author_is_bot=True))

    answer = _ask(
        migrated_session,
        record=approved(policy_version=14, landing_conditions=v14()),
        gateway=gateway,
        moment=IN_WINDOW,
        number=49,
    )

    assert answer.satisfied is False
    assert LANDING_AUTHOR_NOT_THE_UPDATE_BOT in answer.refusals


@pytest.mark.parametrize("status", ["repointed", "revert_opened"])
def test_a_bump_waits_while_a_revert_is_in_progress_in_its_repository(
    migrated_session: Session, status: str
) -> None:
    """Production is pinned (`repointed`) or the revert is open: a bump landed on top of the bad
    one would make the revert an overlap, and would redeploy past the pin's intent."""
    control = _ask(
        migrated_session,
        record=approved(policy_version=14, landing_conditions=v14()),
        gateway=FakeEstateGateway(),
        moment=IN_WINDOW,
        number=49,
    )
    assert control.satisfied, control.refusals
    _opened(migrated_session, status)

    answer = _ask(
        migrated_session,
        record=approved(policy_version=14, landing_conditions=v14()),
        gateway=FakeEstateGateway(),
        moment=IN_WINDOW,
        number=49,
    )

    assert answer.refusals == (LANDING_REPOSITORY_REVERTING,)


def test_a_revert_landed_by_the_estate_lane_is_recorded_and_holds_the_dependency(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """The estate merge path calls the same hook as the inert one: the record says landed, the
    dependency is held at the version before the bump, and the landing itself is recorded."""
    member_of(migrated_session, REPOSITORY)
    _opened(migrated_session)
    migrated_session.commit()
    grant_services(migrated_session, REPOSITORY, "dependency_updates")
    gateway = ActingGateway(
        pull=pull_request(
            number=REVERT,
            head_ref=BRANCH,
            title='Revert "build(deps): bump typer from 0.20.0 to 0.21.0"',
            author_login=APP,
        ),
        base_head=BranchHead(name="main", sha=BASE),
        trees=_trees(),
    )

    record = land_estate_pull_request(
        migrated_session,
        EstateMergeCommand(REPOSITORY, REVERT, SYSTEM, "revert-1", HEAD),
        gateway,
        redeploying_source(),
        FakeChangeRecordSource({(REPOSITORY, REVERT): revert_record_answer()}),
        enabled=True,
        credentials_configured=True,
        clock=FixedClock(OUT_OF_WINDOW),
    )

    assert record.status == "merged"
    with Session(migrated_engine) as reader:
        stored = reader.scalars(select(LandingRevert)).one()
        assert (stored.status, stored.revert_merge_sha) == ("revert_landed", LANDED_COMMIT)
        assert stored.hold_outcome == "held uv / typer at 0.20.0"
        hold = reader.scalars(select(SdsMemberHold)).one()
        assert (hold.dependency, hold.ceiling, hold.set_by) == ("typer", "0.20.0", "sds-revert")
