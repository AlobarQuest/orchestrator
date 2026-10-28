"""Each population is a pure function of the member record, and a retired member is in none."""

import pytest

from sds_members.client import SERVICES, App, Member
from sds_members.populations import (
    app_subjects,
    bindable,
    granted,
    observed,
    working_copies,
)


def _grants(**states: str) -> dict[str, str]:
    return {service: states.get(service, "not_assessed") for service in SERVICES}


def _member(repository: str, state: str, **kwargs) -> Member:
    return Member(
        repository=repository,
        owner="devon",
        state=state,
        working_copy=kwargs.get("working_copy"),
        machine_local=kwargs.get("machine_local", False),
        apps=kwargs.get("apps", ()),
        grants=_grants(**kwargs.get("grants", {})),
    )


APP = App("crm", "https://crm.example/health", "main")
MEMBERS = [
    _member(
        "A/one",
        "member",
        working_copy="/w/one",
        machine_local=True,
        grants={"rollouts": "granted", "backups": "granted"},
        apps=(APP,),
    ),
    _member(
        "A/two",
        "onboarding",
        working_copy="/w/two",
        machine_local=True,
        grants={"rollouts": "remediating"},
    ),
    _member("A/three", "member", working_copy=None, grants={"backups": "granted"}),
    _member("A/four", "member", grants={"backups": "withdrawn"}),
    _member(
        "A/gone",
        "retired",
        working_copy="/w/gone",
        machine_local=True,
        grants={"rollouts": "granted", "backups": "granted"},
        apps=(APP,),
    ),
]


def test_observed_is_onboarding_and_member_repositories():
    assert observed(MEMBERS) == ["A/one", "A/two", "A/three", "A/four"]


def test_working_copies_skips_members_without_one():
    assert working_copies(MEMBERS) == ["/w/one", "/w/two"]


def test_bindable_needs_both_machine_local_and_rollouts_granted():
    assert bindable(MEMBERS) == ["/w/one"]


def test_granted_names_repositories_holding_the_service():
    assert granted(MEMBERS, "backups") == ["A/one", "A/three"]


def test_granted_excludes_a_withdrawn_grant():
    assert "A/four" not in granted(MEMBERS, "backups")


def test_granted_refuses_an_unknown_service():
    with pytest.raises(ValueError):
        granted(MEMBERS, "deploys")


def test_app_subjects_pairs_repository_and_app():
    assert app_subjects(MEMBERS) == [("A/one", APP)]


def test_retired_members_appear_in_no_population():
    everything = [
        observed(MEMBERS),
        working_copies(MEMBERS),
        bindable(MEMBERS),
        *(granted(MEMBERS, s) for s in SERVICES),
        [repo for repo, _ in app_subjects(MEMBERS)],
    ]
    assert all("A/gone" not in values and "/w/gone" not in values for values in everything)
