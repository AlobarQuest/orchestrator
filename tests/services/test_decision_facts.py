from sqlalchemy.orm import Session

from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.reach_vocabulary import REACH_VOCABULARY
from orchestrator.services.decision_facts import (
    REVERSIBILITY_BY_CHANGE_CLASS,
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

# The WS-P2.13 credential rotation, as its envelope actually reads. No repository, no mutating
# command -- and a plain answer to "what does this touch" under three other names.
OPERATIONAL_AUTHORITY = AuthorityEnvelope(
    capabilities={"secret.rotate": "requires_approval"},
    budgets=AuthorityBudgets(max_attempts=1, max_llm_calls=4),
    constraints={
        "credential": "bws-machine-account:8ba33ccd-0000-4000-8000-000000000000",
        "irreversible_actions": [],
        "secret_value_handling": (
            "Devon alone handles the token value, at the Bitwarden console and his own terminal"
        ),
    },
    change_class="non-software-operational",
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
    revision, unit = _build_unit(migrated_session, "facts-targeted")
    unit.authority = TARGETED_AUTHORITY.normalized()

    facts = decision_facts_for_unit(unit, revision)

    assert facts["affects"]["known"] is True
    assert "AlobarQuest/change-manager" in facts["affects"]["detail"]
    assert "uv lock --upgrade" in facts["affects"]["detail"]


def test_operational_constraints_are_reported_rather_than_reported_unknown(
    migrated_session: Session,
) -> None:
    # Observed on the WS-P2.13 rotation unit, 2026-07-31: the envelope named the credential it
    # rotates and whether the action is irreversible, three sections below a surface that said
    # "Not known." The projection only looked for repository-shaped keys, so for the class where
    # blast radius matters most it discarded an answer already on file.
    revision, unit = _build_unit(migrated_session, "facts-operational")
    unit.authority = OPERATIONAL_AUTHORITY.normalized()

    affects = decision_facts_for_unit(unit, revision)["affects"]

    assert affects["known"] is True
    assert "bws-machine-account" in affects["detail"]
    assert "irreversible_actions" in affects["detail"]
    assert "Devon alone handles the token value" in affects["detail"]
    # The negative: nothing about a repository, because this envelope is not about one. Saying
    # "no target repository is named" over work that was never repository work is the noise that
    # made the real answer easy to miss.
    assert "repository" not in affects["detail"].lower()


def test_a_constraint_the_projection_has_never_heard_of_is_still_reported(
    migrated_session: Session,
) -> None:
    # The rule is "report what the envelope declares", not "report the operational keys today's
    # packages happen to use" -- a second fixed key list would repeat the defect for the next
    # profile. A key invented here, that no profile uses, must still reach the surface.
    revision, unit = _build_unit(migrated_session, "facts-unheard-of-constraint")
    unit.authority = AuthorityEnvelope(
        capabilities={"repo.edit": "allowed"},
        budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
        constraints={"blast_radius_nobody_has_declared_yet": "the postal service of Liechtenstein"},
    ).normalized()

    affects = decision_facts_for_unit(unit, revision)["affects"]

    assert affects["known"] is True
    assert "blast_radius_nobody_has_declared_yet" in affects["detail"]
    assert "the postal service of Liechtenstein" in affects["detail"]


def test_an_empty_constraints_block_is_still_an_explicit_unknown(
    migrated_session: Session,
) -> None:
    # The unknown is reserved for an envelope that declares nothing. AC-021: it is RENDERED, never
    # omitted, and its detail says why.
    revision, unit = _build_unit(migrated_session, "facts-no-constraints")

    affects = decision_facts_for_unit(unit, revision)["affects"]

    assert affects["known"] is False
    assert affects["detail"]


def test_a_repo_targeted_unit_still_reports_its_repository_and_mutating_commands(
    migrated_session: Session,
) -> None:
    # Regression guard, not a failing-first control: the repository-shaped answer is what
    # dependency-update work depends on, and widening the projection must not dilute it.
    revision, unit = _build_unit(migrated_session, "facts-still-targeted")
    unit.authority = TARGETED_AUTHORITY.normalized()

    affects = decision_facts_for_unit(unit, revision)["affects"]

    assert affects["known"] is True
    assert "AlobarQuest/change-manager" in affects["detail"]
    assert "uv lock --upgrade" in affects["detail"]
    # And the third constraint that envelope declares, which the repository-shaped projection
    # dropped on the floor.
    assert "allowed_commands" in affects["detail"]


def test_a_repo_shaped_envelope_missing_half_the_pair_still_states_the_negative(
    migrated_session: Session,
) -> None:
    # A unit that names a repository but authorizes no mutating command is repository work with
    # nothing to change, and saying so is the point. The negative is dropped only for an envelope
    # that is not repository-shaped at all.
    revision, unit = _build_unit(migrated_session, "facts-half-repo")
    unit.authority = AuthorityEnvelope(
        capabilities={"repo.edit": "allowed"},
        budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
        constraints={"target_repository": "AlobarQuest/orchestrator"},
    ).normalized()

    affects = decision_facts_for_unit(unit, revision)["affects"]

    assert affects["known"] is True
    assert "no mutating command is authorized" in affects["detail"]


def test_an_unmapped_change_class_reports_reversibility_as_unknown(
    migrated_session: Session,
) -> None:
    # The canonical test envelope declares no change_class at all.
    revision, unit = _build_unit(migrated_session, "facts-unmapped-class")

    facts = decision_facts_for_unit(unit, revision)

    assert facts["reversibility"]["known"] is False
    assert facts["reversibility"]["detail"]


def test_a_mapped_change_class_reports_a_reversibility_statement(
    migrated_session: Session,
) -> None:
    revision, unit = _build_unit(migrated_session, "facts-mapped-class")
    unit.authority = TARGETED_AUTHORITY.normalized()

    facts = decision_facts_for_unit(unit, revision)

    assert facts["reversibility"]["known"] is True
    assert facts["reversibility"]["detail"]


def test_a_declared_rollback_plan_wins_over_the_class_statement(
    migrated_session: Session,
) -> None:
    # The author wrote a plan, and three of the five profiles REQUIRE one. Rendering a generic
    # sentence about the change class instead is strictly worse information: the plan is a
    # commitment about this package, the class statement is editorial prose about a category.
    revision, unit = _build_unit(
        migrated_session,
        "facts-declared-plan",
        enforcement={
            "acceptance_criteria": ["ac-1"],
            "profile_fields": {"rollback_plan": "Revert the branch and re-run the migration down."},
        },
    )
    unit.authority = TARGETED_AUTHORITY.normalized()

    facts = decision_facts_for_unit(unit, revision)

    assert facts["reversibility"]["known"] is True
    detail = facts["reversibility"]["detail"]
    assert "Revert the branch and re-run the migration down." in detail
    # The class statement must not ALSO be rendered: two answers to one question, one of which is
    # a guess, is what this task removes.
    assert REVERSIBILITY_BY_CHANGE_CLASS["dependency-update"] not in detail


def test_the_declared_plan_and_the_class_statement_are_distinguishable(
    migrated_session: Session,
) -> None:
    # A reader must be able to tell which of the two they are looking at. Only one is a commitment
    # someone made about this package; if they read identically, the distinction is not on the page.
    declared_revision, declared_unit = _build_unit(
        migrated_session,
        "facts-source-declared",
        enforcement={
            "acceptance_criteria": ["ac-1"],
            "profile_fields": {"rollback_plan": "Revert the branch."},
        },
    )
    declared_unit.authority = TARGETED_AUTHORITY.normalized()
    class_revision, class_unit = _build_unit(migrated_session, "facts-source-class")
    class_unit.authority = TARGETED_AUTHORITY.normalized()

    declared = decision_facts_for_unit(declared_unit, declared_revision)["reversibility"]["detail"]
    from_class = decision_facts_for_unit(class_unit, class_revision)["reversibility"]["detail"]

    assert "declared" in declared.lower()
    assert "no rollback plan is declared" in from_class.lower()


def test_the_class_statement_is_used_when_no_plan_is_declared(
    migrated_session: Session,
) -> None:
    # `dependency-update` and `non-software-operational` declare no `rollback_plan` at all, so the
    # class statement remains the correct fallback rather than dead code.
    revision, unit = _build_unit(migrated_session, "facts-no-declared-plan")
    unit.authority = TARGETED_AUTHORITY.normalized()

    facts = decision_facts_for_unit(unit, revision)

    assert facts["reversibility"]["known"] is True
    assert REVERSIBILITY_BY_CHANGE_CLASS["dependency-update"] in facts["reversibility"]["detail"]


def test_a_blank_rollback_plan_falls_back_rather_than_rendering_nothing(
    migrated_session: Session,
) -> None:
    # The profile validators reject an empty string at authoring time, but the snapshot is data
    # copied verbatim from another repository -- treat it as untrusted and fall back, rather than
    # render "the author's plan is: " followed by nothing.
    revision, unit = _build_unit(
        migrated_session,
        "facts-blank-plan",
        enforcement={"acceptance_criteria": ["ac-1"], "profile_fields": {"rollback_plan": "   "}},
    )
    unit.authority = TARGETED_AUTHORITY.normalized()

    facts = decision_facts_for_unit(unit, revision)

    assert facts["reversibility"]["known"] is True
    assert REVERSIBILITY_BY_CHANGE_CLASS["dependency-update"] in facts["reversibility"]["detail"]


def test_a_declared_plan_answers_reversibility_at_intake_too(
    migrated_session: Session,
) -> None:
    # A package revision has no authority ENVELOPE and so no change class -- before this, intake
    # could only ever render the explicit unknown. The plan the author wrote is in the snapshot
    # from the moment of intake, which is the earliest gate that asks the question.
    revision, _unit = _build_unit(
        migrated_session,
        "facts-intake-plan",
        enforcement={
            "acceptance_criteria": ["ac-1"],
            "profile_fields": {"rollback_plan": "Restore the previous compose file."},
        },
    )

    facts = decision_facts_for_revision(revision)

    assert facts["reversibility"]["known"] is True
    assert "Restore the previous compose file." in facts["reversibility"]["detail"]


def test_a_unit_always_states_what_it_does(migrated_session: Session) -> None:
    revision, unit = _build_unit(migrated_session, "facts-does")

    facts = decision_facts_for_unit(unit, revision)

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

    for facts in (decision_facts_for_unit(unit, revision), decision_facts_for_revision(revision)):
        assert list(facts) == ["does", "affects", "reversibility"]
        for fact in facts.values():
            assert set(fact) == {"label", "known", "detail"}
            assert isinstance(fact["known"], bool)
            assert fact["label"] and fact["detail"]


def test_a_declared_reach_answers_what_it_affects_before_any_unit_exists(
    migrated_session: Session,
) -> None:
    # The intake gate's "what it affects" was previously ALWAYS unknown -- no unit exists, so no
    # target repository has been chosen. A package that declared its reach has already answered.
    revision, _unit = _build_unit(
        migrated_session,
        "facts-reach-intake",
        enforcement={"acceptance_criteria": ["ac-1"], "reach": ["live_estate"]},
    )

    facts = decision_facts_for_revision(revision)

    assert facts["affects"]["known"] is True
    assert REACH_VOCABULARY["live_estate"] in facts["affects"]["detail"]


def test_a_unit_leads_with_its_declared_reach_and_keeps_the_envelope_specifics(
    migrated_session: Session,
) -> None:
    # Reach does not replace the envelope read-back: they are the same question at different
    # resolutions, and the classification leads because it is what survives being skimmed.
    revision, unit = _build_unit(
        migrated_session,
        "facts-reach-unit",
        enforcement={
            "acceptance_criteria": ["ac-1"],
            "reach": ["source_repository", "live_estate"],
        },
    )
    unit.authority = TARGETED_AUTHORITY.normalized()

    detail = decision_facts_for_unit(unit, revision)["affects"]["detail"]

    assert detail.startswith("This work reaches ")
    assert REACH_VOCABULARY["live_estate"] in detail
    assert "AlobarQuest/change-manager" in detail


def test_a_package_that_declared_no_reach_reads_exactly_as_it_did_before(
    migrated_session: Session,
) -> None:
    # 14 packages predate the field and can never be edited. Absence must keep rendering the
    # explicit unknown, never a permissive "reaches nothing".
    revision, unit = _build_unit(migrated_session, "facts-reach-absent")
    unit.authority = OPERATIONAL_AUTHORITY.normalized()

    assert decision_facts_for_revision(revision)["affects"]["known"] is False
    assert "This work reaches" not in decision_facts_for_unit(unit, revision)["affects"]["detail"]
