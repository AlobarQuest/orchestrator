import uuid

from orchestrator.kernel.readiness import (
    DependencyReadiness,
    ReadinessFacts,
    ReadinessStatus,
    evaluate_readiness_facts,
)


def facts(
    *,
    revision_approved: bool = True,
    decomposition_approved: bool = True,
    authority_approved: bool = True,
    authority_recognised_by_policy: bool = False,
    dependencies: tuple[DependencyReadiness, ...] = (),
) -> ReadinessFacts:
    return ReadinessFacts(
        revision_approved=revision_approved,
        decomposition_approved=decomposition_approved,
        authority_approved=authority_approved,
        authority_recognised_by_policy=authority_recognised_by_policy,
        dependencies=dependencies,
    )


def test_unapproved_revision_and_decomposition_are_not_authorized() -> None:
    decision = evaluate_readiness_facts(
        facts(revision_approved=False, decomposition_approved=False)
    )

    assert decision.status is ReadinessStatus.NOT_AUTHORIZED
    assert [reason.code for reason in decision.reasons] == [
        "revision_not_approved",
        "decomposition_not_approved",
    ]


def test_pending_dependency_is_blocked() -> None:
    dependency_id = uuid.uuid4()

    decision = evaluate_readiness_facts(
        facts(
            dependencies=(
                DependencyReadiness(
                    dependency_id=dependency_id,
                    status="pending",
                    detail="waiting for unit-1",
                ),
            )
        )
    )

    assert decision.status is ReadinessStatus.BLOCKED
    assert decision.reasons[0].subject_id == dependency_id


def test_authority_mismatch_is_not_authorized_before_dependency_block() -> None:
    decision = evaluate_readiness_facts(
        facts(
            authority_approved=False,
            dependencies=(
                DependencyReadiness(
                    dependency_id=uuid.uuid4(),
                    status="pending",
                    detail="waiting",
                ),
            ),
        )
    )

    assert decision.status is ReadinessStatus.NOT_AUTHORIZED
    assert [reason.code for reason in decision.reasons] == ["authority_not_approved"]


def test_satisfied_dependencies_and_authority_are_ready() -> None:
    decision = evaluate_readiness_facts(
        facts(
            dependencies=(
                DependencyReadiness(
                    dependency_id=uuid.uuid4(),
                    status="satisfied",
                    detail="complete",
                ),
            )
        )
    )

    assert decision.status is ReadinessStatus.READY
    assert decision.reasons == ()


def test_reasons_are_deterministic() -> None:
    first_id = uuid.UUID(int=1)
    second_id = uuid.UUID(int=2)
    dependencies = (
        DependencyReadiness(second_id, "failed", "second"),
        DependencyReadiness(first_id, "pending", "first"),
    )

    decision = evaluate_readiness_facts(facts(dependencies=dependencies))

    assert [reason.subject_id for reason in decision.reasons] == [first_id, second_id]


def test_a_unit_nobody_approved_is_authorized_when_policy_recognised_its_envelope() -> None:
    """WS-P2.18 Increment 3. The two facts are SEPARATE and either satisfies the requirement.

    They are kept apart deliberately: nobody approved this unit, and folding the second fact into
    the first would have readiness report that somebody did.
    """
    recognised = evaluate_readiness_facts(
        facts(authority_approved=False, authority_recognised_by_policy=True)
    )

    assert recognised.status is ReadinessStatus.READY


def test_neither_fact_means_the_requirement_stands() -> None:
    # The control, and the default: a caller that has not been taught about policy gets the
    # behaviour every caller had before policy existed.
    decision = evaluate_readiness_facts(facts(authority_approved=False))

    assert decision.status is ReadinessStatus.NOT_AUTHORIZED
    assert [reason.code for reason in decision.reasons] == ["authority_not_approved"]
