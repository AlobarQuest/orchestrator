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
