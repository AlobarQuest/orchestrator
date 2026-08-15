from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class ReadinessStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"
    NOT_AUTHORIZED = "not_authorized"


@dataclass(frozen=True)
class ReadinessReason:
    code: str
    subject_id: UUID | None
    detail: str


@dataclass(frozen=True)
class ReadinessDecision:
    status: ReadinessStatus
    reasons: tuple[ReadinessReason, ...]


@dataclass(frozen=True)
class DependencyReadiness:
    dependency_id: UUID
    status: str
    detail: str


@dataclass(frozen=True)
class ReadinessFacts:
    revision_approved: bool
    decomposition_approved: bool
    authority_approved: bool
    dependencies: tuple[DependencyReadiness, ...]
    # Whether policy recognised this unit's envelope as a declared known-good pattern, so no human
    # approval is required of it (WS-P2.18, ADR-0011). A SEPARATE fact from `authority_approved`
    # and never folded into it: nobody approved this unit, and readiness must not report that
    # somebody did. Defaults to the state in which the requirement stands, so a caller that has
    # not been taught about policy asks for the approval, as every caller did before it existed.
    authority_recognised_by_policy: bool = False


def evaluate_readiness_facts(facts: ReadinessFacts) -> ReadinessDecision:
    authorization_reasons = _authorization_reasons(facts)
    if authorization_reasons:
        return ReadinessDecision(ReadinessStatus.NOT_AUTHORIZED, authorization_reasons)

    blocked_reasons = tuple(
        ReadinessReason(
            code=f"dependency_{dependency.status}",
            subject_id=dependency.dependency_id,
            detail=dependency.detail,
        )
        for dependency in sorted(facts.dependencies, key=lambda item: item.dependency_id.int)
        if dependency.status != "satisfied"
    )
    if blocked_reasons:
        return ReadinessDecision(ReadinessStatus.BLOCKED, blocked_reasons)
    return ReadinessDecision(ReadinessStatus.READY, ())


def _authorization_reasons(facts: ReadinessFacts) -> tuple[ReadinessReason, ...]:
    reasons: list[ReadinessReason] = []
    if not facts.revision_approved:
        reasons.append(
            ReadinessReason("revision_not_approved", None, "package revision is not approved")
        )
    if not facts.decomposition_approved:
        reasons.append(
            ReadinessReason(
                "decomposition_not_approved",
                None,
                "work-unit decomposition is not approved",
            )
        )
    if not (facts.authority_approved or facts.authority_recognised_by_policy):
        reasons.append(
            ReadinessReason(
                "authority_not_approved",
                None,
                "no exact authority approval is recorded",
            )
        )
    return tuple(reasons)
