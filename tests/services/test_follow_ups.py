import pytest

from orchestrator.errors import DomainError
from orchestrator.services.follow_ups import validate_follow_up

VALID = {
    "required": True,
    "revisit_when": "After the next quarterly review.",
    "signals": ["A guard nobody triaged."],
    "owner": "devon",
}


def test_a_valid_declaration_round_trips() -> None:
    assert validate_follow_up(VALID) == VALID


def test_absent_declaration_is_none_not_an_error() -> None:
    assert validate_follow_up(None) is None


def test_the_fully_degenerate_declaration_is_valid() -> None:
    degenerate = {"required": False, "revisit_when": None, "signals": [], "owner": None}

    assert validate_follow_up(degenerate) == degenerate


@pytest.mark.parametrize(
    "value",
    [
        {"required": True, "revisit_when": None, "signals": []},
        {"required": True, "revisit_when": None, "signals": [], "owner": None, "extra": 1},
        {"required": "yes", "revisit_when": None, "signals": [], "owner": None},
        {"required": True, "revisit_when": 7, "signals": [], "owner": None},
        {"required": True, "revisit_when": None, "signals": "not-a-list", "owner": None},
        {"required": True, "revisit_when": None, "signals": [None], "owner": None},
        "not-a-mapping",
    ],
    ids=[
        "missing-key",
        "unknown-key",
        "required-not-bool",
        "revisit-when-not-str",
        "signals-not-list",
        "signal-item-not-str",
        "not-a-mapping",
    ],
)
def test_a_malformed_declaration_is_a_named_domain_error(value: object) -> None:
    with pytest.raises(DomainError) as caught:
        validate_follow_up(value)

    assert caught.value.code == "follow_up_invalid"
