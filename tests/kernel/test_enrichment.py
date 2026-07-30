"""Ingress bounds for the governed-knowledge document a unit carries (WS-P2.12).

Every rejection is a DomainError. Only DomainError and APIAuthenticationError have
registered handlers in main.py, so anything else escaping a route is a bare 500 --
a shape this repo has already paid for twice.
"""

from typing import Any

import pytest

from orchestrator.errors import DomainError
from orchestrator.kernel.enrichment import validate_enrichment


def _document(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "profile": "software-delivery",
        "change_class": "software-delivery",
        "roads": [],
        "rules": [],
        "exemplars": [],
        "content_fingerprint": "sha256:" + "0" * 64,
        "resolved_at": "2026-07-30T00:00:00+00:00",
        "sources": [],
    }
    return {**base, **overrides}


def _rules(count: int, text: str = "r") -> list[dict[str, Any]]:
    return [
        {
            "brain": "code",
            "road_slug": "error-logging",
            "id": index,
            "category": "logging",
            "severity": "BLOCK",
            "authority": "informational",
            "rule": text,
            "reason": "because",
        }
        for index in range(count)
    ]


def test_an_empty_document_is_valid() -> None:
    """Empty by content is a legitimate projection, not a malformed one.

    dependency-update ships exactly this today, and it must not be rejected as
    though it were absent.
    """
    document = validate_enrichment(_document())

    assert document is not None
    assert document["roads"] == []


def test_none_passes_through_for_units_that_predate_enrichment() -> None:
    assert validate_enrichment(None) is None


def test_a_populated_document_round_trips() -> None:
    document = validate_enrichment(_document(rules=_rules(3)))

    assert document is not None
    assert len(document["rules"]) == 3


def test_a_missing_key_is_rejected() -> None:
    payload = _document()
    del payload["change_class"]

    with pytest.raises(DomainError) as error:
        validate_enrichment(payload)

    assert error.value.code == "context_enrichment_invalid"


def test_an_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(DomainError) as error:
        validate_enrichment(_document(schema_version=2))

    assert error.value.code == "context_enrichment_invalid"


def test_a_non_mapping_is_rejected() -> None:
    with pytest.raises(DomainError) as error:
        validate_enrichment(["not", "a", "document"])

    assert error.value.code == "context_enrichment_invalid"


def test_a_non_list_record_collection_is_rejected() -> None:
    with pytest.raises(DomainError) as error:
        validate_enrichment(_document(rules={"not": "a list"}))

    assert error.value.code == "context_enrichment_invalid"


def test_a_non_mapping_record_is_rejected() -> None:
    with pytest.raises(DomainError) as error:
        validate_enrichment(_document(rules=["bare string"]))

    assert error.value.code == "context_enrichment_invalid"


def test_a_nested_structure_inside_a_record_is_rejected() -> None:
    """Records are flat. Nesting is how unbounded content would arrive."""
    nested = _rules(1)
    nested[0]["check"] = {"kind": "forbidden_pattern", "pattern": ".*"}

    with pytest.raises(DomainError) as error:
        validate_enrichment(_document(rules=nested))

    assert error.value.code == "context_enrichment_invalid"


def test_too_many_rules_is_rejected() -> None:
    with pytest.raises(DomainError) as error:
        validate_enrichment(_document(rules=_rules(201)))

    assert error.value.code == "context_enrichment_too_large"


def test_an_overlong_field_is_rejected() -> None:
    with pytest.raises(DomainError) as error:
        validate_enrichment(_document(rules=_rules(1, text="x" * 4001)))

    assert error.value.code == "context_enrichment_too_large"


def test_an_oversized_document_is_rejected() -> None:
    """Under every per-record limit and still too big in aggregate.

    The count and per-field caps do not compose into a byte cap, so the byte cap
    has to exist separately or 199 near-maximal rules would sail through.
    """
    with pytest.raises(DomainError) as error:
        validate_enrichment(_document(rules=_rules(150, text="x" * 500)))

    assert error.value.code == "context_enrichment_too_large"


def test_the_real_software_delivery_document_fits() -> None:
    """The live projection is 7849 bytes; the cap must not be set below reality."""
    assert validate_enrichment(_document(rules=_rules(13, text="x" * 400))) is not None
