"""WS-P2.16 U4 -- the criterion evidence_type vocabulary, declared and validated at intake.

Two safety wins, both behaviour-preserving at verify time:

* The package (intent-packages) evidence vocabulary is now DECLARED in the verifier's sets, so a
  criterion evidence_type that lands on ``judgment_required`` does so *because we said so*, and a
  typo does not -- it is a named error at intake.
* Assertion D pins ``DETERMINISTIC_TYPES`` to its resolvers, so promoting a type to deterministic
  later without an evaluator reds the suite instead of silently returning ``judgment_required``.
"""

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.persistence.models import PackageAcceptanceCriterion
from orchestrator.services.package_intake import register_package_intake
from orchestrator.services.verifier_evaluators import (
    DETERMINISTIC_PERMITTED_TYPES,
    DETERMINISTIC_TYPES,
    EVALUATORS,
    HUMAN_FLOOR_TYPES,
    JUDGMENT_TYPES,
    SPECIAL_CASE_TYPES,
    SUPPORTED_CRITERION_EVIDENCE_TYPES,
    evaluate_criterion,
    floor_for,
)
from tests.services.test_package_intake import acceptance_criterion, human_actor, intake_command


def test_every_deterministic_type_resolves_to_an_evaluator_or_a_declared_special_case() -> None:
    # Assertion D (no silent sink). Negative control: add a member to DETERMINISTIC_TYPES with no
    # EVALUATORS entry and not in SPECIAL_CASE_TYPES -> this reds. That is what makes the deferred
    # deterministic-evaluator workstream safe to attempt: an unmapped deterministic type cannot
    # quietly fall through to judgment_required.
    unresolved = DETERMINISTIC_TYPES - set(EVALUATORS) - SPECIAL_CASE_TYPES
    assert unresolved == set(), f"deterministic types with no resolver: {sorted(unresolved)}"
    # A special case must actually be deterministic, or it documents a type that does not exist.
    assert SPECIAL_CASE_TYPES <= DETERMINISTIC_TYPES


def test_an_unknown_criterion_type_floors_to_human() -> None:
    # R1 fail-closed: forgetting to classify a type must produce a gate that fires too often
    # (recoverable), never one that does not fire (not recoverable).
    assert floor_for("automated_tset") == "human"
    assert floor_for("") == "human"


def test_human_floor_types_and_deterministic_permitted_types_are_disjoint() -> None:
    assert HUMAN_FLOOR_TYPES & DETERMINISTIC_PERMITTED_TYPES == frozenset()


def test_every_supported_criterion_type_has_exactly_one_floor() -> None:
    covered = HUMAN_FLOOR_TYPES | DETERMINISTIC_PERMITTED_TYPES
    assert SUPPORTED_CRITERION_EVIDENCE_TYPES <= covered, (
        f"unclassified: {sorted(SUPPORTED_CRITERION_EVIDENCE_TYPES - covered)}"
    )


def test_floor_is_case_and_whitespace_insensitive() -> None:
    assert floor_for("  Human_Review  ") == "human"


def test_automated_test_still_requires_judgment_unchanged() -> None:
    # The no-regression control: declaring `automated_test` in JUDGMENT_TYPES must not change its
    # evaluation. It was judgment_required by fall-through before; it is judgment_required by
    # membership now. Byte-identical outcome -- U4 ships no evaluator.
    criterion = PackageAcceptanceCriterion(ac_id="AC-001", evidence_type="automated_test")
    status, outcome, _ = evaluate_criterion(criterion, None)
    assert (status, outcome) == ("judgment_required", None)
    assert "automated_test" in JUDGMENT_TYPES


def test_intake_rejects_an_unknown_criterion_evidence_type(migrated_session: Session) -> None:
    typo = acceptance_criterion("AC-001")
    typo = type(typo)(**{**typo.__dict__, "evidence_type": "automated_tset"})

    with pytest.raises(DomainError) as error:
        register_package_intake(
            migrated_session,
            intake_command(acceptance_criteria=(typo,)),
            human_actor(),
        )

    assert error.value.code == "unknown_evidence_type"
    assert "automated_tset" in error.value.message


def test_intake_accepts_the_legal_package_evidence_vocabulary(migrated_session: Session) -> None:
    legal_types = (
        "automated_test",
        "automated_check",
        "human_review",
        "external_attestation",
        "observation",
    )
    criteria = tuple(
        type(acceptance_criterion())(
            **{**acceptance_criterion(f"AC-{i:03d}").__dict__, "evidence_type": evidence_type}
        )
        for i, evidence_type in enumerate(legal_types)
    )

    revision = register_package_intake(
        migrated_session,
        intake_command(acceptance_criteria=criteria),
        human_actor(),
    )

    assert revision.id is not None
