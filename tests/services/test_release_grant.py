"""Release binding and observation answer to the `rollouts` grant on the unit's own repository."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import SdsMember, WorkUnit
from orchestrator.services.release.release_grant import (
    RELEASE_REPOSITORY_MISMATCH,
    RELEASE_REPOSITORY_NOT_GRANTED,
    repository_release_refusal,
    unit_release_refusal,
)
from tests._support.seeding import grant_service, set_grant_state

GRANTED = "AlobarQuest/demo"
SERVICE = "rollouts"


def _unit(repository: str | None) -> WorkUnit:
    constraints = {} if repository is None else {"target_repository": repository}
    return WorkUnit(authority={"constraints": constraints})


def _code(error: object) -> str | None:
    return getattr(error, "code", None)


def test_a_granted_repository_is_not_refused(migrated_session: Session) -> None:
    grant_service(migrated_session, GRANTED, SERVICE)
    assert repository_release_refusal(migrated_session, GRANTED) is None


def test_the_grant_is_case_folded(migrated_session: Session) -> None:
    grant_service(migrated_session, "AlobarQuest/Demo", SERVICE)
    assert repository_release_refusal(migrated_session, "alobarquest/demo") is None


def test_another_service_s_grant_does_not_count(migrated_session: Session) -> None:
    grant_service(migrated_session, GRANTED, "app_changes")
    refusal = repository_release_refusal(migrated_session, GRANTED)
    assert _code(refusal) == RELEASE_REPOSITORY_NOT_GRANTED == "release_repository_not_granted"


def test_a_withdrawn_grant_is_refused(migrated_session: Session) -> None:
    grant_service(migrated_session, GRANTED, SERVICE)
    set_grant_state(migrated_session, GRANTED, SERVICE, "withdrawn")
    assert _code(repository_release_refusal(migrated_session, GRANTED)) == (
        RELEASE_REPOSITORY_NOT_GRANTED
    )


def test_a_retired_member_is_refused(migrated_session: Session) -> None:
    grant_service(migrated_session, GRANTED, SERVICE)
    member = migrated_session.scalar(
        select(SdsMember).where(func.lower(SdsMember.repository) == GRANTED.lower())
    )
    assert member is not None
    member.state = "retired"
    migrated_session.commit()
    assert _code(repository_release_refusal(migrated_session, GRANTED)) == (
        RELEASE_REPOSITORY_NOT_GRANTED
    )


def test_a_unit_s_own_repository_is_asked(migrated_session: Session) -> None:
    grant_service(migrated_session, GRANTED, SERVICE)
    assert unit_release_refusal(migrated_session, _unit(GRANTED), GRANTED) is None


def test_an_ungranted_unit_repository_is_refused(migrated_session: Session) -> None:
    refusal = unit_release_refusal(migrated_session, _unit(GRANTED), GRANTED)
    assert _code(refusal) == RELEASE_REPOSITORY_NOT_GRANTED


def test_a_body_naming_another_repository_is_a_mismatch(migrated_session: Session) -> None:
    grant_service(migrated_session, "AlobarQuest/b", SERVICE)
    refusal = unit_release_refusal(migrated_session, _unit("AlobarQuest/a"), "AlobarQuest/b")
    assert _code(refusal) == RELEASE_REPOSITORY_MISMATCH == "release_repository_mismatch"


def test_a_unit_naming_no_repository_is_a_mismatch(migrated_session: Session) -> None:
    grant_service(migrated_session, GRANTED, SERVICE)
    refusal = unit_release_refusal(migrated_session, _unit(None), GRANTED)
    assert _code(refusal) == RELEASE_REPOSITORY_MISMATCH


def test_the_body_spelling_may_differ_in_case(migrated_session: Session) -> None:
    grant_service(migrated_session, "AlobarQuest/Demo", SERVICE)
    assert (
        unit_release_refusal(migrated_session, _unit("AlobarQuest/Demo"), " alobarquest/demo ")
        is None
    )
