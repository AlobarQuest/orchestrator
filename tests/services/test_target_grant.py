"""Admission's grant term reads the member record, and the lanes' mapping agrees with it."""

import pytest
from sqlalchemy.orm import Session

from orchestrator.factory_policy import load_factory_policy
from orchestrator.persistence.models import SdsMember, SdsMemberGrant
from orchestrator.services.execution.target_grant import (
    MemberRecordGrants,
    service_for_change_class,
)
from sds_members.populations import service_for_change_class as lane_service_for_change_class


def test_a_dependency_update_needs_the_dependency_updates_grant() -> None:
    assert service_for_change_class("dependency-update") == "dependency_updates"


@pytest.mark.parametrize(
    "change_class", ["maintenance-remediation", "software-delivery", "repo.edit"]
)
def test_every_other_change_class_needs_the_app_changes_grant(change_class: str) -> None:
    assert service_for_change_class(change_class) == "app_changes"


def test_the_lane_and_admission_mappings_agree() -> None:
    classes = [*load_factory_policy().admission.change_classes, "repo.edit", ""]
    assert "dependency-update" in classes
    for change_class in classes:
        assert lane_service_for_change_class(change_class) == service_for_change_class(change_class)


def _seed(session: Session, repository: str, *, state: str, grant: str, service: str) -> None:
    member = SdsMember(
        repository=repository,
        owner="devon",
        state=state,
        state_changed_by="human:devon",
        state_reason="seed",
    )
    session.add(member)
    session.flush()
    session.add(
        SdsMemberGrant(
            member_id=member.id,
            service=service,
            state=grant,
            seeded=False,
            set_by="human:devon",
            reason="seed",
        )
    )
    session.commit()


def test_member_record_grants_fold_case(migrated_session: Session) -> None:
    _seed(
        migrated_session,
        "AlobarQuest/brain",
        state="member",
        grant="granted",
        service="app_changes",
    )

    grants = MemberRecordGrants(migrated_session)

    assert grants.is_granted("alobarquest/brain", "app_changes") is True
    assert grants.is_granted("AlobarQuest/brain", "dependency_updates") is False
    assert grants.is_granted("AlobarQuest/other", "app_changes") is False


@pytest.mark.parametrize(
    ("member_state", "grant_state"),
    [
        ("member", "remediating"),
        ("member", "withdrawn"),
        ("member", "not_assessed"),
        ("member", "not_applicable"),
        ("retired", "granted"),
    ],
)
def test_only_a_granted_state_on_a_live_member_counts(
    migrated_session: Session, member_state: str, grant_state: str
) -> None:
    _seed(
        migrated_session,
        "AlobarQuest/brain",
        state=member_state,
        grant=grant_state,
        service="app_changes",
    )

    assert (
        MemberRecordGrants(migrated_session).is_granted("AlobarQuest/brain", "app_changes") is False
    )
