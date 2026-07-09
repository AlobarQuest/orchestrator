from typing import Any

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
    constraints: dict[str, Any] | None = None,
) -> AuthorityEnvelope:
    return AuthorityEnvelope(
        capabilities=capabilities,
        budgets=AuthorityBudgets(
            max_attempts=max_attempts,
            max_llm_calls=max_llm_calls,
        ),
        unknown_fields=unknown_fields,
        constraints=constraints or {},
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


def test_fingerprint_covers_target_repository() -> None:
    """A human approving an authority fingerprint must be attesting to where code ships.

    Two units that differ only in their target repository must not share a fingerprint,
    or one repo's approval silently authorizes a dispatch against another.
    """
    brain = envelope(
        {"repo.edit": "allowed"},
        constraints={"target_repository": "AlobarQuest/brain"},
    )
    standards = envelope(
        {"repo.edit": "allowed"},
        constraints={"target_repository": "AlobarQuest/security-standards"},
    )

    assert authority_fingerprint(brain) != authority_fingerprint(standards)


def test_fingerprint_covers_work_unit_id() -> None:
    first = envelope({}, constraints={"work_unit_id": "11111111-1111-5111-8111-111111111111"})
    second = envelope({}, constraints={"work_unit_id": "22222222-2222-5222-8222-222222222222"})

    assert authority_fingerprint(first) != authority_fingerprint(second)


def test_fingerprint_is_canonical_across_constraint_key_order() -> None:
    first = normalize_authority(
        {
            "capabilities": {"repo.edit": "allowed"},
            "budgets": {"max_attempts": 3, "max_llm_calls": 3},
            "constraints": {"target_repository": "AlobarQuest/brain", "work_unit_id": "u-1"},
        }
    )
    second = normalize_authority(
        {
            "capabilities": {"repo.edit": "allowed"},
            "budgets": {"max_attempts": 3, "max_llm_calls": 3},
            "constraints": {"work_unit_id": "u-1", "target_repository": "AlobarQuest/brain"},
        }
    )

    assert authority_fingerprint(first) == authority_fingerprint(second)


def test_constraints_are_a_known_field() -> None:
    parsed = normalize_authority(
        {
            "capabilities": {},
            "budgets": {},
            "constraints": {"target_repository": "AlobarQuest/brain"},
        }
    )

    assert parsed.unknown_fields == frozenset()
    assert parsed.constraints == {"target_repository": "AlobarQuest/brain"}
    assert parsed.normalized()["constraints"] == {"target_repository": "AlobarQuest/brain"}


def test_ordered_constraint_lists_are_significant() -> None:
    """allowed_commands order is meaningful; it must not be canonicalized away."""
    first = envelope({}, constraints={"allowed_commands": ["make check", "make lint"]})
    second = envelope({}, constraints={"allowed_commands": ["make lint", "make check"]})

    assert authority_fingerprint(first) != authority_fingerprint(second)


@pytest.mark.parametrize("value", ["nope", 7, ["target_repository"], None])
def test_non_mapping_constraints_fail_closed(value: Any) -> None:
    parsed = normalize_authority({"capabilities": {}, "budgets": {}, "constraints": value})

    assert parsed.unknown_fields == frozenset({"constraints"})
    assert parsed.constraints == {}
    assert is_expansion(envelope({}), parsed) is True


def test_unserializable_constraints_fail_closed() -> None:
    parsed = normalize_authority(
        {"capabilities": {}, "budgets": {}, "constraints": {"bad": object()}}
    )

    assert parsed.unknown_fields == frozenset({"constraints"})
    assert parsed.constraints == {}


def test_absent_constraints_normalize_to_empty_mapping() -> None:
    parsed = normalize_authority({"capabilities": {}, "budgets": {}})

    assert parsed.unknown_fields == frozenset()
    assert parsed.constraints == {}
    assert parsed.normalized()["constraints"] == {}


def test_fingerprint_covers_change_class() -> None:
    """change_class is the dispatch allowlist key; an approved fingerprint must not
    silently cover an envelope naming a different class."""
    dependency = normalize_authority(
        {"capabilities": {}, "budgets": {}, "change_class": "dependency-update"}
    )
    infra = normalize_authority(
        {"capabilities": {}, "budgets": {}, "change_class": "infra-mutation"}
    )

    assert dependency.change_class == "dependency-update"
    assert dependency.unknown_fields == frozenset()
    assert authority_fingerprint(dependency) != authority_fingerprint(infra)


@pytest.mark.parametrize("value", ["", 7, {"a": 1}])
def test_invalid_change_class_fails_closed(value: Any) -> None:
    parsed = normalize_authority({"capabilities": {}, "budgets": {}, "change_class": value})

    assert parsed.unknown_fields == frozenset({"change_class"})
    assert parsed.change_class is None
    assert is_expansion(envelope({}), parsed) is True


def test_fingerprint_covers_conformance() -> None:
    """Conformance is attested per unit against its own target repo. Because it lives in
    the envelope, the human's authority approval attests it too — so a different
    conformance claim must produce a different fingerprint."""
    green = normalize_authority(
        {
            "capabilities": {},
            "budgets": {},
            "conformance": {
                "status": "green",
                "accepted_standards": [],
                "standards_touched": ["project"],
            },
        }
    )
    red = normalize_authority(
        {
            "capabilities": {},
            "budgets": {},
            "conformance": {
                "status": "red",
                "accepted_standards": [],
                "standards_touched": ["project"],
            },
        }
    )

    assert green.conformance == {
        "status": "green",
        "accepted_standards": [],
        "standards_touched": ["project"],
    }
    assert green.unknown_fields == frozenset()
    assert authority_fingerprint(green) != authority_fingerprint(red)


@pytest.mark.parametrize("value", ["green", 7, ["green"]])
def test_non_mapping_conformance_fails_closed(value: Any) -> None:
    parsed = normalize_authority({"capabilities": {}, "budgets": {}, "conformance": value})

    assert parsed.unknown_fields == frozenset({"conformance"})
    assert parsed.conformance is None


def test_absent_conformance_is_none() -> None:
    parsed = normalize_authority({"capabilities": {}, "budgets": {}})

    assert parsed.conformance is None
    assert parsed.normalized()["conformance"] is None


def test_normalized_round_trips_without_inventing_unknown_fields() -> None:
    """normalized() is stored verbatim as some units' envelope and re-parsed by dispatch,
    so an explicit null change_class/conformance must mean absent, not unknown."""
    original = normalize_authority(
        {"capabilities": {"repo.edit": "allowed"}, "budgets": {"max_attempts": 3}}
    )
    reparsed = normalize_authority(original.normalized())

    assert original.change_class is None
    assert reparsed.change_class is None
    assert reparsed.conformance is None
    assert "change_class" not in reparsed.unknown_fields
    assert "conformance" not in reparsed.unknown_fields
