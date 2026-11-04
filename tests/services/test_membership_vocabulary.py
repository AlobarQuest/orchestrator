"""The proposal vocabulary is a second copy of the model's CHECK vocabulary; drift must fail CI,
not surface as a bare 500 when a confirmed change hits the database CHECK."""

from typing import get_args

from orchestrator.persistence.models import GRANT_STATES, MEMBER_SERVICES, MEMBER_STATES
from orchestrator.services.membership.changes import GrantState, MemberService, MemberState


def test_member_states_match_the_model() -> None:
    assert get_args(MemberState) == MEMBER_STATES


def test_grant_states_match_the_model() -> None:
    assert get_args(GrantState) == GRANT_STATES


def test_member_services_match_the_model() -> None:
    assert get_args(MemberService) == MEMBER_SERVICES


def test_the_deploys_service_is_spelled_rollouts_everywhere() -> None:
    """The word guards refuse the other spelling in code, so the wire value is `rollouts` in every
    vocabulary and constant that names the service."""
    from orchestrator.services.release.release_grant import RELEASE_GRANT_SERVICE
    from sds_members.client import SERVICES

    for vocabulary in (MEMBER_SERVICES, get_args(MemberService), SERVICES):
        assert "rollouts" in vocabulary
        assert not [name for name in vocabulary if "deploy" in name]
    assert RELEASE_GRANT_SERVICE == "rollouts"
