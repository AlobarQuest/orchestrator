from sqlalchemy.orm import Session

from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.services.decision_facts import (
    decision_facts_for_revision,
    decision_facts_for_unit,
)
from tests.services.test_slo_report import _build_unit

TARGETED_AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
    constraints={
        "target_repository": "AlobarQuest/change-manager",
        "mutation_commands": ["uv lock --upgrade"],
        "allowed_commands": ["uv lock --upgrade", "uv sync"],
    },
    change_class="dependency-update",
)


def test_an_undecomposed_revision_reports_affects_as_unknown(migrated_session: Session) -> None:
    # AC-021: an unknown is RENDERED as unknown, never omitted. A missing row reads as "nothing to
    # worry about"; an explicit unknown reads as "nobody knows yet", which is the truth.
    revision, _unit = _build_unit(migrated_session, "facts-undecomposed")

    facts = decision_facts_for_revision(revision)

    assert facts["affects"]["known"] is False
    assert facts["affects"]["detail"]


def test_a_unit_reports_its_target_repository_and_mutating_commands(
    migrated_session: Session,
) -> None:
    _revision, unit = _build_unit(migrated_session, "facts-targeted")
    unit.authority = TARGETED_AUTHORITY.normalized()

    facts = decision_facts_for_unit(unit)

    assert facts["affects"]["known"] is True
    assert "AlobarQuest/change-manager" in facts["affects"]["detail"]
    assert "uv lock --upgrade" in facts["affects"]["detail"]


def test_an_unmapped_change_class_reports_reversibility_as_unknown(
    migrated_session: Session,
) -> None:
    # The canonical test envelope declares no change_class at all.
    _revision, unit = _build_unit(migrated_session, "facts-unmapped-class")

    facts = decision_facts_for_unit(unit)

    assert facts["reversibility"]["known"] is False
    assert facts["reversibility"]["detail"]


def test_a_mapped_change_class_reports_a_reversibility_statement(
    migrated_session: Session,
) -> None:
    _revision, unit = _build_unit(migrated_session, "facts-mapped-class")
    unit.authority = TARGETED_AUTHORITY.normalized()

    facts = decision_facts_for_unit(unit)

    assert facts["reversibility"]["known"] is True
    assert facts["reversibility"]["detail"]


def test_a_unit_always_states_what_it_does(migrated_session: Session) -> None:
    _revision, unit = _build_unit(migrated_session, "facts-does")

    facts = decision_facts_for_unit(unit)

    assert facts["does"]["known"] is True
    assert unit.outcome in facts["does"]["detail"]


def test_a_revision_states_what_it_does_from_the_package_outcome(
    migrated_session: Session,
) -> None:
    revision, _unit = _build_unit(
        migrated_session,
        "facts-revision-outcome",
        enforcement={
            "acceptance_criteria": ["ac-1"],
            "outcome": {"what": "The tracker projection stops drifting."},
        },
    )

    facts = decision_facts_for_revision(revision)

    assert facts["does"]["known"] is True
    assert "The tracker projection stops drifting." in facts["does"]["detail"]


def test_a_revision_without_a_recorded_outcome_says_so_rather_than_omitting_it(
    migrated_session: Session,
) -> None:
    # `_build_unit`'s default snapshot carries no `outcome` block. HQ's plan asserted "what it
    # does" is "never unknown"; a revision registered without one proves otherwise, and the
    # surface must still render a row.
    revision, _unit = _build_unit(migrated_session, "facts-revision-no-outcome")

    facts = decision_facts_for_revision(revision)

    assert facts["does"]["known"] is False
    assert facts["does"]["detail"]


def test_every_fact_is_rendered_with_the_same_shape(migrated_session: Session) -> None:
    # AC-021 depends on the partial being able to loop: a fact that is a bare string on one
    # surface and a mapping on the other cannot be rendered by one component.
    revision, unit = _build_unit(migrated_session, "facts-shape")

    for facts in (decision_facts_for_unit(unit), decision_facts_for_revision(revision)):
        assert list(facts) == ["does", "affects", "reversibility"]
        for fact in facts.values():
            assert set(fact) == {"label", "known", "detail"}
            assert isinstance(fact["known"], bool)
            assert fact["label"] and fact["detail"]
