"""The one automatic writer of the member tables: a ceiling after a landed revert. Landing Part 3a.

Persistence is asserted through a second session: a flushed-but-uncommitted row is visible to the
session that wrote it, so an in-session re-read passes under the defect it would be written to
catch.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import LandingRevert, SdsMemberHold
from orchestrator.services.membership.revert_hold import SET_BY, place
from tests.services.test_member_holds import place_hold
from tests.services.test_member_proposals import member_of

REPOSITORY = "alobarquest/brain"


def bump(**overrides: Any) -> dict[str, Any]:
    return {
        "ecosystem": "uv",
        "directory": "/",
        "dependency": "typer",
        "from_version": "0.20.0",
        "to_version": "0.21.0",
        **overrides,
    }


def revert_record(
    session: Session,
    *,
    status: str = "revert_landed",
    bumps: list[dict[str, Any]] | None = None,
    repository: str = REPOSITORY,
    pr_number: int = 41,
    revert_pr_number: int | None = 42,
) -> LandingRevert:
    row = LandingRevert(
        repository=repository,
        pr_number=pr_number,
        merge_commit_sha="m" * 40,
        parent_sha="p" * 40,
        failed_job_names=["test", "lint"],
        failed_run_id=9_000_000_001,
        status=status,
        revert_pr_number=revert_pr_number,
        bumps=[bump()] if bumps is None else bumps,
    )
    session.add(row)
    session.flush()
    return row


def _holds(engine: Engine) -> list[tuple[str, str, str, str, str, str]]:
    with Session(engine) as reader:
        return [
            (row.ecosystem, row.directory, row.dependency, row.ceiling, row.reason, row.set_by)
            for row in reader.scalars(select(SdsMemberHold).order_by(SdsMemberHold.dependency))
        ]


def test_the_hold_is_a_ceiling_at_the_from_version(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    member_of(migrated_session, REPOSITORY)
    revert = revert_record(migrated_session)

    outcome = place(migrated_session, revert)
    migrated_session.commit()

    assert _holds(migrated_engine) == [
        ("uv", "/", "typer", "0.20.0", f"Reverted {REPOSITORY}#41: test, lint failed", SET_BY)
    ]
    assert SET_BY == "sds-revert"
    assert outcome == "held uv / typer at 0.20.0"


def test_an_unknown_from_version_places_no_hold_and_says_so(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """A grouped bump's from-version is only in its pull request body, which nothing reads, and a
    ceiling the matcher can't read would hold every version with no signal that it was mistyped.
    Neither may raise: this runs inside the transaction recording a landing that happened."""
    member_of(migrated_session, REPOSITORY)
    revert = revert_record(
        migrated_session,
        bumps=[
            bump(dependency="click", from_version=None),
            bump(dependency="rich", from_version="latest"),
            bump(dependency="httpx", directory="api"),
            bump(dependency=""),
            bump(dependency="typer"),
        ],
    )

    outcome = place(migrated_session, revert)
    migrated_session.commit()

    assert [row[2] for row in _holds(migrated_engine)] == ["typer"]
    assert outcome == "; ".join(
        (
            "not held uv / click: the version before the bump is unknown",
            "not held uv / rich: the version before the bump is unknown",
            "not held uv api httpx: the directory is not a path",
            "not held uv / : the record does not name the dependency",
            "held uv / typer at 0.20.0",
        )
    )


def test_an_existing_hold_is_left_alone(migrated_session: Session, migrated_engine: Engine) -> None:
    """A person's hold is theirs: a revert never replaces its ceiling or its reason, even when the
    dependency is spelled differently (the matcher's name rule decides the identity)."""
    place_hold(
        migrated_session, REPOSITORY, ecosystem="pip", dependency="Typing_Extensions", ceiling="4.0"
    )
    revert = revert_record(
        migrated_session,
        bumps=[bump(ecosystem="pip", dependency="typing-extensions", from_version="4.12.0")],
    )

    outcome = place(migrated_session, revert)
    migrated_session.commit()

    assert _holds(migrated_engine) == [
        ("pip", "/", "Typing_Extensions", "4.0", "held for a test", "devon")
    ]
    assert outcome == "not held pip / typing-extensions: already held at 4.0"


def test_two_bumps_of_one_dependency_place_one_hold(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """A second insert on the same key would be an integrity error, which would roll back the
    landing's record."""
    member_of(migrated_session, REPOSITORY)
    revert = revert_record(migrated_session, bumps=[bump(), bump(from_version="0.19.0")])

    outcome = place(migrated_session, revert)
    migrated_session.commit()

    assert [row[3] for row in _holds(migrated_engine)] == ["0.20.0"]
    assert outcome.endswith("not held uv / typer: already held at 0.20.0")


def test_a_repository_with_no_member_places_nothing_and_says_so(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    revert = revert_record(migrated_session)

    outcome = place(migrated_session, revert)

    assert _holds(migrated_engine) == []
    assert outcome == f"nothing held: {REPOSITORY} has no member record"


def test_a_revert_that_has_not_landed_places_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    member_of(migrated_session, REPOSITORY)
    for number, status in enumerate(
        ("rerun_requested", "revert_opened", "refused_overlap", "revert_failed")
    ):
        revert = revert_record(migrated_session, status=status, pr_number=number)

        outcome = place(migrated_session, revert)

        assert outcome == f"nothing held: the revert is {status}, not landed"
    migrated_session.commit()
    assert _holds(migrated_engine) == []


def test_the_writer_never_updates_or_deletes() -> None:
    """Structural: the module constructs one hold and adds it, and assigns nothing. The writers
    guard (`tests/architecture/test_membership_writers.py`) pins the same over the whole tree."""
    import ast
    import inspect

    from orchestrator.services.membership import revert_hold
    from tests.architecture.test_membership_writers import _assigned_attributes, _writes

    tree = ast.parse(inspect.getsource(revert_hold))
    assert sorted(p.split(": ", 1)[1] for p in _writes(tree)) == [
        "constructs SdsMemberHold",
        "session.add(...)",
    ]
    assert _assigned_attributes(tree) == []


def test_the_writer_takes_the_lock_a_persons_confirmation_takes(migrated_engine: Engine) -> None:
    """The membership record's lock, held from another connection by its literal key, blocks the
    writer; the control, with nothing held, places the hold."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    with Session(migrated_engine) as setup:
        member_of(setup, REPOSITORY)
        revert_record(setup)
        setup.commit()

    with Session(migrated_engine) as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(1296192050, 0)"))  # "MBR2"
        with Session(migrated_engine) as blocked:
            blocked.execute(text("SET LOCAL lock_timeout = '1s'"))
            revert = blocked.scalars(select(LandingRevert)).one()
            with pytest.raises(OperationalError):
                place(blocked, revert)
        holder.rollback()

    with Session(migrated_engine) as free:
        free.execute(text("SET LOCAL lock_timeout = '1s'"))
        assert place(free, free.scalars(select(LandingRevert)).one()).startswith("held")
