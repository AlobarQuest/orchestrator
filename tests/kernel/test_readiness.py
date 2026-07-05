import uuid

from orchestrator.kernel.readiness import (
    DependencyReadiness,
    ReadinessFacts,
    ReadinessStatus,
    evaluate_readiness_facts,
)


def facts(**overrides: object) -> ReadinessFacts:
    values: dict[str, object] = {
        "revision_approved": True,
        "decomposition_approved": True,
        "authority_approved": True,
        "dependencies": (),
    }
    values.update(overrides)
    return ReadinessFacts(**values)  # type: ignore[arg-type]


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
