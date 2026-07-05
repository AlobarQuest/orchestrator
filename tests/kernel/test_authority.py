import pytest

from orchestrator.kernel.authority import (
    AuthorityBudgets,
    AuthorityEnvelope,
    authority_fingerprint,
    is_expansion,
    normalize_authority,
)


def envelope(
    capabilities: dict[str, str],
    *,
    max_attempts: int | None = 3,
    max_llm_calls: int | None = 3,
    unknown_fields: frozenset[str] = frozenset(),
) -> AuthorityEnvelope:
    return AuthorityEnvelope(
        capabilities=capabilities,
        budgets=AuthorityBudgets(
            max_attempts=max_attempts,
            max_llm_calls=max_llm_calls,
        ),
        unknown_fields=unknown_fields,
    )


@pytest.mark.parametrize(
    ("old", "new", "expanded"),
    [
        ({"repository_write": "allowed"}, {"repository_write": "requires_approval"}, False),
        ({"repository_write": "requires_approval"}, {"repository_write": "allowed"}, True),
        ({"repository_write": "prohibited"}, {"repository_write": "allowed"}, True),
        (
            {"repository_write": "allowed"},
            {"repository_write": "allowed", "email_send": "allowed"},
            True,
        ),
    ],
)
def test_authority_expansion_is_fail_closed(
    old: dict[str, str],
    new: dict[str, str],
    expanded: bool,
) -> None:
    assert is_expansion(envelope(old), envelope(new)) is expanded


@pytest.mark.parametrize(
    ("old_limit", "new_limit", "expanded"),
    [(3, 2, False), (3, 4, True), (3, None, True)],
)
def test_authority_budget_expansion_is_fail_closed(
    old_limit: int,
    new_limit: int | None,
    expanded: bool,
) -> None:
    old = envelope({}, max_attempts=old_limit)
    new = envelope({}, max_attempts=new_limit)

    assert is_expansion(old, new) is expanded


def test_unknown_authority_fields_are_expanding() -> None:
    old = envelope({})
    new = envelope({}, unknown_fields=frozenset({"future_budget"}))

    assert is_expansion(old, new) is True


def test_authority_fingerprint_is_canonical() -> None:
    first = envelope({"repository_write": "allowed", "email_send": "prohibited"})
    second = envelope({"email_send": "prohibited", "repository_write": "allowed"})

    assert authority_fingerprint(first) == authority_fingerprint(second)


def test_invalid_capability_level_fails_closed() -> None:
    old = envelope({"repository_write": "allowed"})
    new = envelope({"repository_write": "unexpected"})

    assert is_expansion(old, new) is True


def test_invalid_normalized_budget_is_unknown_and_expanding() -> None:
    old = envelope({}, max_attempts=None)
    new = normalize_authority(
        {
            "capabilities": {},
            "budgets": {"max_attempts": "unlimited", "max_llm_calls": 3},
        }
    )

    assert new.unknown_fields == frozenset({"budgets.max_attempts"})
    assert is_expansion(old, new) is True
