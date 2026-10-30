"""A deploying landing judged bad: production pinned to the parent, then reverted, then restored.

Landing Part 3b, Task 4, the orchestrator's half. The estate lander points each application at the
image of the bump's parent and asks this side to record it (`record_repoint`), which judges the
landing again and records `repointed` with the images; the revert opens only from `repointed`
(`act_on_landing`); once the revert has landed and production serves it again, the lander asks for
`restored` (`record_restored`). Nothing here reads or writes the hosting platform.

Everything runs with no network. Persistence is asserted through a second session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import Event, LandingRevert
from orchestrator.services.landing.interfaces import Tree
from orchestrator.services.landing.revert_act import (
    LANDING_PIN_NOT_REPOINTED,
    LANDING_PIN_UNCONFIRMED,
    LANDING_REPOINT_IMAGES_INVALID,
    LANDING_REPOINT_NOT_BAD,
    LANDING_REPOINT_NOT_SERVING,
    LANDING_RESTORE_NOT_LANDED,
    LANDING_REVERT_LANE_OFF,
    LANDING_REVERT_NEEDS_REPOINT,
    LANDING_REVERT_RECORD_MOVED,
    PinnedCommand,
    RepointCommand,
    RestoreCommand,
    RevertCommand,
    act_on_landing,
    landing_watch,
    record_pinned,
    record_repoint,
    record_restored,
)
from orchestrator.services.landing.revert_record import (
    opened_reverts,
    revert_in_progress,
    reverted_versions,
)
from tests.services.estate_landing_doubles import dependabot_commit
from tests.services.inert_landing_doubles import INERT_REPOSITORY
from tests.services.test_revert_act import (
    BASE,
    BEFORE_79,
    BRAIN,
    BRAIN_BUMP,
    BRAIN_HEAD,
    BRAIN_TITLE,
    BUMP,
    MERGE_79,
    REVERT,
    REVERT_HEAD,
    SYSTEM,
    BrainGateway,
    WatchGateway,
    _current,
    _entry,
    everywhere,
    failing,
    heard,
    landed_bump,
    landed_on_brain,
    lanes,
    message,
    stored,
)

DIGEST = "sha256:" + "a" * 64
APP_UUIDS = ("app-uuid-1", "app-uuid-2", "app-uuid-3", "app-uuid-4")


def pinned(tag: str = BEFORE_79, **entry: str) -> dict[str, dict[str, str]]:
    """What the lander pins: each application's uuid to the parent's tag and digest, and the
    moving tag it had before, which the restore puts back."""
    return {
        uuid: {"tag": tag, "digest": DIGEST, "restore_tag": "latest", **entry} for uuid in APP_UUIDS
    }


def brain_gateway(**kwargs: Any) -> BrainGateway:
    """Brain's App surface with the trees a revert of #79 is planned from."""
    kwargs.setdefault(
        "trees",
        {
            BEFORE_79: _tree(lock="l1", readme="r1"),
            MERGE_79: _tree(lock="l2", readme="r1"),
            BASE: _tree(lock="l2", readme="r2"),
            REVERT_HEAD: _tree(lock="l1", readme="r2"),
        },
    )
    kwargs.setdefault(
        "commits",
        {BRAIN_BUMP: (dependabot_commit(BRAIN_HEAD, message(BRAIN_TITLE, "2.1.1")),)},
    )
    return BrainGateway(**kwargs)


def _tree(*, lock: str, readme: str) -> Tree:
    return Tree(f"t{lock}{readme}", {"uv.lock": _entry(lock), "README.md": _entry(readme)})


def stuck_on_the_parent(session: Session) -> None:
    """Brain #79's shape: every brain on the build before the bump for over two hours, the watcher
    reporting."""
    landed_on_brain(session)
    everywhere(session, BEFORE_79, minutes_ago=140)
    heard(session, minutes_ago=10)
    # Committed: a refused act rolls back its whole transaction.
    session.commit()


def repoint(
    session: Session,
    gateway: BrainGateway,
    *,
    images: dict[str, Any] | None = None,
    key: str = "repoint-1",
    expected: str | None = None,
    pr_number: int = BRAIN_BUMP,
    repository: str = BRAIN,
):
    return record_repoint(
        session,
        RepointCommand(
            repository=repository,
            pr_number=pr_number,
            actor=SYSTEM,
            idempotency_key=key,
            expected_status=expected,
            pinned_images=pinned() if images is None else images,
        ),
        **lanes(gateway),
    )


def restore(session: Session, gateway: BrainGateway, *, key: str = "restore-1"):
    return record_restored(
        session,
        RestoreCommand(
            repository=BRAIN,
            pr_number=BRAIN_BUMP,
            actor=SYSTEM,
            idempotency_key=key,
            expected_status=_current(session, BRAIN_BUMP) or "absent",
        ),
        **lanes(gateway),
    )


def open_revert(session: Session, gateway: BrainGateway, *, key: str = "open-1"):
    return act_on_landing(
        session,
        RevertCommand(
            repository=BRAIN,
            pr_number=BRAIN_BUMP,
            actor=SYSTEM,
            idempotency_key=key,
            expected_status=_current(session, BRAIN_BUMP),
        ),
        **lanes(gateway),
    )


def test_a_bad_deploying_landing_is_recorded_repointed_with_the_images_it_pinned(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    stuck_on_the_parent(migrated_session)
    gateway = brain_gateway()

    outcome = repoint(migrated_session, gateway)

    assert (outcome.verdict, outcome.acted, outcome.status) == ("bad", True, "repointed")
    record = stored(migrated_engine)
    assert record is not None
    assert (record.status, record.parent_sha, record.merge_commit_sha) == (
        "repointed",
        BEFORE_79,
        MERGE_79,
    )
    assert record.pinned_images == pinned()
    assert record.revert_pr_number is None and record.restored_at is None
    assert "served" in (record.detail or "")
    # Nothing is written through the App: production is the lander's to point.
    assert gateway.writes == [] and gateway.reruns == []


def test_the_watch_reports_a_repointed_landing_by_its_record_with_its_images(
    migrated_session: Session,
) -> None:
    stuck_on_the_parent(migrated_session)
    gateway = brain_gateway()
    [before] = landing_watch(migrated_session, **lanes(gateway))
    repoint(migrated_session, gateway)

    [after] = landing_watch(migrated_session, **lanes(gateway))

    assert (before.verdict, before.parent_sha, before.lane) == ("bad", BEFORE_79, "estate")
    assert before.pinned_images is None
    assert (after.verdict, after.status, after.parent_sha) == (None, "repointed", BEFORE_79)
    assert after.pinned_images == pinned()


def test_a_repointed_landing_holds_every_other_bump_in_its_repository(
    migrated_session: Session,
) -> None:
    stuck_on_the_parent(migrated_session)
    repoint(migrated_session, brain_gateway())

    assert revert_in_progress(migrated_session, BRAIN) is True


def test_a_landing_that_is_not_bad_is_never_repointed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Production serves the merge commit: whatever the lander saw, this side judges again."""
    landed_on_brain(migrated_session)
    everywhere(migrated_session, MERGE_79, minutes_ago=140)
    heard(migrated_session, minutes_ago=10)

    with pytest.raises(DomainError) as raised:
        repoint(migrated_session, brain_gateway())

    assert raised.value.code == LANDING_REPOINT_NOT_BAD
    assert stored(migrated_engine) is None


@pytest.mark.parametrize(
    "images",
    [
        {},
        pinned(tag=MERGE_79),
        pinned(tag="latest"),
        pinned(digest="sha256:short"),
        pinned(digest="a" * 64),
        pinned(restore_tag=""),
        pinned(restore_tag=BEFORE_79),
        {"app-uuid-1": {"tag": BEFORE_79, "digest": DIGEST}},
        {"": pinned()["app-uuid-1"]},
        {"app-uuid-1": "not an object"},
    ],
)
def test_a_repoint_naming_anything_but_the_parents_image_is_refused(
    migrated_session: Session, migrated_engine: Engine, images: dict[str, Any]
) -> None:
    stuck_on_the_parent(migrated_session)

    with pytest.raises(DomainError) as raised:
        repoint(migrated_session, brain_gateway(), images=images)

    assert raised.value.code == LANDING_REPOINT_IMAGES_INVALID
    assert stored(migrated_engine) is None


def test_an_inert_landing_is_never_repointed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    landed_bump(migrated_session)
    gateway = WatchGateway(jobs=failing())

    with pytest.raises(DomainError) as raised:
        record_repoint(
            migrated_session,
            RepointCommand(
                repository=INERT_REPOSITORY,
                pr_number=BUMP,
                actor=SYSTEM,
                idempotency_key="repoint-inert",
                expected_status=None,
                pinned_images=pinned(),
            ),
            **lanes(gateway),
        )

    assert raised.value.code == LANDING_REPOINT_NOT_SERVING
    assert stored(migrated_engine) is None


def test_a_repoint_from_a_status_the_caller_did_not_read_is_refused(
    migrated_session: Session,
) -> None:
    stuck_on_the_parent(migrated_session)

    with pytest.raises(DomainError) as raised:
        repoint(migrated_session, brain_gateway(), expected="rerun_requested")

    assert raised.value.code == LANDING_REVERT_RECORD_MOVED


def test_a_repeated_repoint_key_answers_from_its_event(migrated_session: Session) -> None:
    stuck_on_the_parent(migrated_session)
    gateway = brain_gateway()
    first = repoint(migrated_session, gateway)

    again = repoint(migrated_session, gateway, expected="repointed")

    assert again.replayed is True and again.status == first.status == "repointed"
    events = migrated_session.scalars(
        select(Event.id).where(Event.action == "landing_revert.repointed")
    ).all()
    assert len(events) == 1


def test_the_revert_of_a_deploying_landing_opens_only_from_repointed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Before the re-point the act still refuses; after it, it opens the revert straight from the
    record, without judging again (production now serves the parent by the SDS's own act)."""
    stuck_on_the_parent(migrated_session)
    gateway = brain_gateway()
    with pytest.raises(DomainError) as raised:
        open_revert(migrated_session, gateway, key="too-early")
    assert raised.value.code == LANDING_REVERT_NEEDS_REPOINT
    repoint(migrated_session, gateway)
    # Fix round 4: not from an unconfirmed pin, whoever asks.
    with pytest.raises(DomainError) as unconfirmed:
        open_revert(migrated_session, gateway, key="unconfirmed")
    assert unconfirmed.value.code == LANDING_PIN_UNCONFIRMED
    assert not any(write.startswith("pull") for write in gateway.writes)
    confirm(migrated_session)

    opened = open_revert(migrated_session, gateway)

    assert (opened.acted, opened.status, opened.revert_pr_number) == (
        True,
        "revert_opened",
        REVERT,
    ), opened.detail
    record = stored(migrated_engine)
    assert record is not None
    assert (record.status, record.revert_pr_number) == ("revert_opened", REVERT)
    assert record.pinned_images == {
        uuid: {**image, "pinned": "yes"} for uuid, image in pinned().items()
    }
    assert gateway.writes[-1].startswith("pull Revert")
    # Listed for `change_proposer` once the act's transaction commits.
    assert [(r.repository, r.revert_pr_number) for r in opened_reverts(migrated_session)] == [
        (BRAIN, REVERT)
    ]


def _landed_revert(session: Session, engine: Engine) -> None:
    stuck_on_the_parent(session)
    gateway = brain_gateway()
    repoint(session, gateway)
    confirm(session)
    open_revert(session, gateway)
    row = session.scalar(select(LandingRevert))
    assert row is not None
    row.status, row.revert_merge_sha = "revert_landed", "f" * 40
    session.commit()


def test_a_landed_revert_is_recorded_restored(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _landed_revert(migrated_session, migrated_engine)

    outcome = restore(migrated_session, brain_gateway())

    assert (outcome.acted, outcome.status) == (True, "restored")
    record = stored(migrated_engine)
    assert record is not None and record.status == "restored"
    assert record.restored_at is not None
    assert record.restored_at <= datetime.now(UTC)
    # Restored is landed: the reverted version still never lands again, and nothing is reverting.
    assert reverted_versions(migrated_session, BRAIN) != ()
    assert revert_in_progress(migrated_session, BRAIN) is False


@pytest.mark.parametrize("status", ["revert_opened"])
def test_nothing_is_restored_before_its_revert_has_landed(
    migrated_session: Session, migrated_engine: Engine, status: str
) -> None:
    stuck_on_the_parent(migrated_session)
    repoint(migrated_session, brain_gateway())
    row = migrated_session.scalar(select(LandingRevert))
    assert row is not None
    row.status = status
    migrated_session.commit()

    with pytest.raises(DomainError) as raised:
        restore(migrated_session, brain_gateway())

    assert raised.value.code == LANDING_RESTORE_NOT_LANDED
    record = stored(migrated_engine)
    assert record is not None and record.status == status


def test_a_pinned_landing_stays_watched_until_it_is_restored(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """However old, and whatever its status, a record that pinned production stays in the watch
    until it is restored: the lander raises `landing-production-pinned` from it."""
    stuck_on_the_parent(migrated_session)
    repoint(migrated_session, brain_gateway())
    row = migrated_session.scalar(select(LandingRevert))
    assert row is not None
    row.status = "revert_landed"
    row.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    migrated_session.commit()
    migrated_session.execute(
        text("UPDATE estate_pr_merge SET created_at = now() - interval '30 days'")
    )
    migrated_session.commit()

    [watched] = landing_watch(migrated_session, **lanes(brain_gateway()))
    assert (watched.status, watched.pinned_images) == ("revert_landed", pinned())

    restore(migrated_session, brain_gateway())
    [after] = landing_watch(migrated_session, **lanes(brain_gateway()))
    # Restored: reported by its status for the window after it changed, then gone.
    assert after.status == "restored"
    row = migrated_session.scalar(select(LandingRevert))
    assert row is not None
    row.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    migrated_session.commit()
    assert landing_watch(migrated_session, **lanes(brain_gateway())) == ()


def test_a_repeated_restore_key_answers_from_its_event(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    _landed_revert(migrated_session, migrated_engine)
    first = restore(migrated_session, brain_gateway())

    again = restore(migrated_session, brain_gateway())

    assert again.replayed is True and again.status == first.status == "restored"
    with pytest.raises(DomainError) as raised:
        restore(migrated_session, brain_gateway(), key="restore-2")
    assert raised.value.code == LANDING_RESTORE_NOT_LANDED


def test_a_rerun_record_whose_landing_is_no_longer_bad_is_never_repointed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """The record names the parent, so only the judgment made here stands between a caller and
    a re-point: production now serves the merge commit, so nothing is recorded."""
    landed_on_brain(migrated_session)
    everywhere(migrated_session, MERGE_79, minutes_ago=140)
    heard(migrated_session, minutes_ago=10)
    migrated_session.add(
        LandingRevert(
            repository=BRAIN,
            pr_number=BRAIN_BUMP,
            merge_commit_sha=MERGE_79,
            parent_sha=BEFORE_79,
            failed_job_names=["test"],
            failed_run_id=1,
            rerun_attempts={"1": 1},
            status="rerun_requested",
            bumps=[],
        )
    )
    migrated_session.commit()

    with pytest.raises(DomainError) as raised:
        repoint(migrated_session, brain_gateway(), expected="rerun_requested")

    assert raised.value.code == LANDING_REPOINT_NOT_BAD
    record = stored(migrated_engine)
    assert record is not None and record.status == "rerun_requested"


def test_a_landing_already_repointed_is_not_repointed_again(migrated_session: Session) -> None:
    stuck_on_the_parent(migrated_session)
    repoint(migrated_session, brain_gateway())

    with pytest.raises(DomainError) as raised:
        repoint(migrated_session, brain_gateway(), key="repoint-2", expected="repointed")

    assert raised.value.code == LANDING_REVERT_RECORD_MOVED


# --------------------------------------------------------------------------------------------
# Fix round 1: a pinned record that cannot be reverted has a way out, and holds bumps meanwhile.
# --------------------------------------------------------------------------------------------


def _pinned_at(session: Session, status: str, *, confirmed: bool = True) -> None:
    stuck_on_the_parent(session)
    repoint(session, brain_gateway())
    if confirmed:
        confirm(session, key=f"pinned-{status}")
    row = session.scalar(select(LandingRevert))
    assert row is not None
    row.status = status
    row.bumps = [
        {
            "ecosystem": "uv",
            "directory": "/",
            "dependency": "sqlalchemy",
            "from_version": "2.0.54",
            "to_version": "2.1.1",
        }
    ]
    session.commit()


@pytest.mark.parametrize("status", ["revert_failed", "refused_overlap"])
def test_a_person_restoring_a_pinned_record_that_never_reverted_clears_it(
    migrated_session: Session, migrated_engine: Engine, status: str
) -> None:
    """Nothing was reverted, so a person put the moving tags back by hand: the lander asks for
    `restored`, which is accepted from the status it read. It is not a landed revert, so the
    version it would have taken out is not barred, and nothing reverts here any more."""
    _pinned_at(migrated_session, status)

    outcome = restore(migrated_session, brain_gateway())

    assert (outcome.acted, outcome.status) == (True, "restored")
    record = stored(migrated_engine)
    assert record is not None and record.status == "restored" and record.restored_at
    assert reverted_versions(migrated_session, BRAIN) == ()
    assert revert_in_progress(migrated_session, BRAIN) is False


@pytest.mark.parametrize("status", ["repointed", "revert_opened", "revert_failed"])
def test_a_restore_names_the_status_it_read(migrated_session: Session, status: str) -> None:
    _pinned_at(migrated_session, status)

    with pytest.raises(DomainError) as raised:
        record_restored(
            migrated_session,
            RestoreCommand(BRAIN, BRAIN_BUMP, SYSTEM, "restore-x", expected_status="revert_landed"),
            **lanes(brain_gateway()),
        )

    assert raised.value.code == LANDING_REVERT_RECORD_MOVED


@pytest.mark.parametrize(
    "status", ["repointed", "revert_opened", "refused_overlap", "revert_failed", "revert_landed"]
)
def test_every_bump_waits_while_production_is_pinned(
    migrated_session: Session, status: str
) -> None:
    """A bump landed behind the pin is never served, and would move the base under the revert."""
    _pinned_at(migrated_session, status)

    assert revert_in_progress(migrated_session, BRAIN) is True

    row = migrated_session.scalar(select(LandingRevert))
    assert row is not None
    row.status = "restored"
    migrated_session.commit()
    assert revert_in_progress(migrated_session, BRAIN) is False


def test_a_hand_restore_before_the_revert_opens_hands_the_landing_to_a_person(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Restored from `repointed`: the detail says so, bumps resume, nothing was reverted, and the
    revert is never opened afterwards (opening needs `repointed`)."""
    _pinned_at(migrated_session, "repointed")

    outcome = restore(migrated_session, brain_gateway())

    assert (outcome.acted, outcome.status) == (True, "restored")
    record = stored(migrated_engine)
    assert record is not None and record.status == "restored"
    assert record.detail == "restored by hand before the revert opened"
    assert reverted_versions(migrated_session, BRAIN) == ()
    assert revert_in_progress(migrated_session, BRAIN) is False
    gateway = brain_gateway()
    later = open_revert(migrated_session, gateway, key="open-after")
    assert later.acted is False and gateway.writes == []


# --------------------------------------------------------------------------------------------
# Fix round 3: the lander confirms the pin once every tag is set.
# --------------------------------------------------------------------------------------------


def confirm(session: Session, *, key: str = "pinned-1", expected: str = "repointed"):
    return record_pinned(
        session,
        PinnedCommand(BRAIN, BRAIN_BUMP, SYSTEM, key, expected_status=expected),
        **lanes(brain_gateway()),
    )


def test_a_confirmed_pin_marks_every_image_pinned(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    stuck_on_the_parent(migrated_session)
    repoint(migrated_session, brain_gateway())

    outcome = confirm(migrated_session)

    assert (outcome.acted, outcome.status) == (True, "repointed")
    record = stored(migrated_engine)
    assert record is not None
    assert record.pinned_images == {
        uuid: {**image, "pinned": "yes"} for uuid, image in pinned().items()
    }


def test_a_re_point_never_carries_a_confirmation_the_caller_wrote(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    stuck_on_the_parent(migrated_session)

    repoint(migrated_session, brain_gateway(), images=pinned(pinned="yes"))

    record = stored(migrated_engine)
    assert record is not None and record.pinned_images == pinned()


@pytest.mark.parametrize("status", ["revert_opened", "revert_landed", "restored"])
def test_only_a_repointed_record_is_confirmed(migrated_session: Session, status: str) -> None:
    _pinned_at(migrated_session, status)

    with pytest.raises(DomainError) as raised:
        confirm(migrated_session, expected=status)

    assert raised.value.code == LANDING_PIN_NOT_REPOINTED


def test_a_confirmation_names_the_status_it_read(migrated_session: Session) -> None:
    _pinned_at(migrated_session, "repointed")

    with pytest.raises(DomainError) as raised:
        confirm(migrated_session, expected="revert_opened")

    assert raised.value.code == LANDING_REVERT_RECORD_MOVED


def test_a_repeated_confirmation_key_answers_from_its_event(migrated_session: Session) -> None:
    _pinned_at(migrated_session, "repointed")
    confirm(migrated_session)

    again = confirm(migrated_session)

    assert again.replayed is True


# --------------------------------------------------------------------------------------------
# Fix round 4.
# --------------------------------------------------------------------------------------------


def test_a_take_over_restore_needs_a_confirmed_pin(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Any SYSTEM caller asking `restored` of an unconfirmed `repointed` record is refused: its
    tags on their restore tags are a pin that never happened. The control is confirmed."""
    _pinned_at(migrated_session, "repointed", confirmed=False)

    with pytest.raises(DomainError) as raised:
        restore(migrated_session, brain_gateway())

    assert raised.value.code == LANDING_PIN_UNCONFIRMED
    record = stored(migrated_engine)
    assert record is not None and record.status == "repointed"
    confirm(migrated_session)
    assert restore(migrated_session, brain_gateway(), key="restore-2").status == "restored"


@pytest.mark.parametrize("status", ["repointed", "revert_landed"])
def test_an_estate_landing_past_its_rerun_is_unreadable_while_its_lane_is_off(
    migrated_session: Session, status: str
) -> None:
    """Switched off, the estate lane's records are served with the lane-off refusal as their
    verdict, so the lander takes no step on them; the record, and its pins, are still served."""
    _pinned_at(migrated_session, status)
    if status == "revert_landed":
        row = migrated_session.scalar(select(LandingRevert))
        assert row is not None
        row.revert_merge_sha = "f" * 40
        migrated_session.commit()

    [watched] = landing_watch(migrated_session, **lanes(brain_gateway(), estate=False))

    assert (watched.verdict, watched.detail) == ("unreadable", LANDING_REVERT_LANE_OFF)
    assert (watched.status, watched.lane) == (status, "estate")
    assert watched.pinned_images is not None


def test_an_inert_landing_past_its_rerun_is_still_reported_by_its_status_when_its_lane_is_off(
    migrated_session: Session,
) -> None:
    """Part 3a's behaviour, kept: the inert lander takes no step on a record past its rerun (its
    revert lands through admission, which the switch refuses), so the row reports its status."""
    landed_bump(migrated_session)
    migrated_session.add(
        LandingRevert(
            repository=INERT_REPOSITORY,
            pr_number=BUMP,
            merge_commit_sha="m" * 40,
            parent_sha="p" * 40,
            failed_job_names=["test"],
            failed_run_id=1,
            status="revert_opened",
            revert_pr_number=REVERT,
            bumps=[],
        )
    )
    migrated_session.commit()

    [watched] = landing_watch(migrated_session, **lanes(WatchGateway(), inert=False))

    assert (watched.verdict, watched.status) == (None, "revert_opened")


def test_a_pin_marked_on_only_some_images_is_unconfirmed(migrated_session: Session) -> None:
    _pinned_at(migrated_session, "repointed", confirmed=False)
    row = migrated_session.scalar(select(LandingRevert))
    assert row is not None
    images = dict(row.pinned_images or {})
    first = sorted(images)[0]
    images[first] = {**images[first], "pinned": "yes"}
    row.pinned_images = images
    migrated_session.commit()

    with pytest.raises(DomainError) as raised:
        open_revert(migrated_session, brain_gateway())

    assert raised.value.code == LANDING_PIN_UNCONFIRMED
