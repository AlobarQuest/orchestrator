from typing import TypedDict, Unpack

from orchestrator.kernel.context import (
    ContextDecision,
    classify_context_update,
    context_fingerprint,
    normalize_standing_context,
)


class ContextOverrides(TypedDict, total=False):
    code_standards_version: str
    security_standards_version: str
    project_standards_version: str
    agent_id: str
    authority_profile: str
    runtime_name: str
    runtime_version: str
    skill_bundle_id: str
    skill_bundle_version: str
    capabilities: list[str]


def required_context(**overrides: Unpack[ContextOverrides]) -> dict[str, object]:
    return valid_context(**overrides)


def valid_context(
    *,
    code_standards_version: str = "1.0",
    security_standards_version: str = "1.0",
    project_standards_version: str = "1.0",
    agent_id: str = "ws-3-3-agent",
    authority_profile: str = "agent-queue-v1",
    runtime_name: str = "codex",
    runtime_version: str = "1.0",
    skill_bundle_id: str = "ws-3.3-protocol-smoke-runtime-semantics",
    skill_bundle_version: str = "1",
    capabilities: list[str] | None = None,
) -> dict[str, object]:
    return {
        "code_standards_version": code_standards_version,
        "security_standards_version": security_standards_version,
        "project_standards_version": project_standards_version,
        "agent_id": agent_id,
        "authority_profile": authority_profile,
        "runtime_name": runtime_name,
        "runtime_version": runtime_version,
        "skill_bundle_id": skill_bundle_id,
        "skill_bundle_version": skill_bundle_version,
        "capabilities": list(["repository_read"] if capabilities is None else capabilities),
    }


def test_missing_required_field_is_rejected() -> None:
    current = valid_context()
    current.pop("runtime_name")

    decision = classify_context_update(
        previous=None,
        current=current,
        required=required_context(),
        allowed_capabilities={"repository_read"},
    )

    assert decision == ContextDecision(
        classification="missing_required",
        decision="rejected",
        reasons=("missing:runtime_name",),
    )


def test_unchanged_context_is_accepted() -> None:
    previous = valid_context()
    current = valid_context()

    decision = classify_context_update(
        previous=previous,
        current=current,
        required=required_context(),
        allowed_capabilities={"repository_read"},
    )

    assert decision == ContextDecision(
        classification="accepted",
        decision="accepted",
        reasons=("unchanged",),
    )


def test_same_scope_newer_standard_version_is_accepted() -> None:
    previous = valid_context(code_standards_version="1.0")
    current = valid_context(code_standards_version="1.1")

    decision = classify_context_update(
        previous=previous,
        current=current,
        required=required_context(code_standards_version="1.0"),
        allowed_capabilities={"repository_read"},
    )

    assert decision == ContextDecision(
        classification="same_scope",
        decision="accepted",
        reasons=("standards_changed_within_floor",),
    )


def test_narrower_capability_set_is_accepted() -> None:
    previous = valid_context(capabilities=["repository_read", "repository_write"])
    current = valid_context(capabilities=["repository_read"])

    decision = classify_context_update(
        previous=previous,
        current=current,
        required=required_context(capabilities=["repository_read"]),
        allowed_capabilities={"repository_read", "repository_write"},
    )

    assert decision == ContextDecision(
        classification="same_scope",
        decision="accepted",
        reasons=("capabilities_narrowed",),
    )


def test_added_capability_requires_approval() -> None:
    previous = valid_context(capabilities=["repository_read"])
    current = valid_context(capabilities=["repository_read", "repository_write"])

    decision = classify_context_update(
        previous=previous,
        current=current,
        required=required_context(),
        allowed_capabilities={"repository_read"},
    )

    assert decision.classification == "authority_expanding"
    assert decision.decision == "requires_approval"
    assert "capabilities_expanded" in decision.reasons


def test_broader_authority_profile_requires_approval() -> None:
    previous = valid_context(authority_profile="agent-queue-v1")
    current = valid_context(authority_profile="human-operator-v1")

    decision = classify_context_update(
        previous=previous,
        current=current,
        required=required_context(authority_profile="agent-queue-v1"),
        allowed_capabilities={"repository_read"},
    )

    assert decision.classification == "authority_expanding"
    assert decision.decision == "requires_approval"
    assert "authority_profile_expanded" in decision.reasons


def test_dropping_required_capability_is_rejected() -> None:
    previous = valid_context(capabilities=["repository_read"])
    current = valid_context(capabilities=[])

    decision = classify_context_update(
        previous=previous,
        current=current,
        required=required_context(capabilities=["repository_read"]),
        allowed_capabilities={"repository_read"},
    )

    assert decision == ContextDecision(
        classification="missing_required",
        decision="rejected",
        reasons=("missing:capabilities",),
    )


def test_lower_than_required_authority_profile_is_rejected() -> None:
    previous = valid_context(authority_profile="agent-queue-v1")
    current = valid_context(authority_profile="verifier-v1")

    decision = classify_context_update(
        previous=previous,
        current=current,
        required=required_context(authority_profile="agent-queue-v1"),
        allowed_capabilities={"repository_read"},
    )

    assert decision == ContextDecision(
        classification="missing_required",
        decision="rejected",
        reasons=("missing:authority_profile",),
    )


def test_malformed_capabilities_is_rejected() -> None:
    current = valid_context()
    current["capabilities"] = "repository_read"

    decision = classify_context_update(
        previous=None,
        current=current,
        required=required_context(),
        allowed_capabilities={"repository_read"},
    )

    assert decision == ContextDecision(
        classification="missing_required",
        decision="rejected",
        reasons=("missing:capabilities",),
    )


def test_capabilities_with_non_string_members_is_rejected() -> None:
    current = valid_context()
    current["capabilities"] = ["repository_read", 1, None]

    decision = classify_context_update(
        previous=None,
        current=current,
        required=required_context(),
        allowed_capabilities={"repository_read"},
    )

    assert decision == ContextDecision(
        classification="missing_required",
        decision="rejected",
        reasons=("missing:capabilities",),
    )


def test_context_fingerprint_is_deterministic_across_key_order() -> None:
    first = normalize_standing_context(valid_context())
    second = normalize_standing_context(
        {
            "skill_bundle_version": "1",
            "skill_bundle_id": "ws-3.3-protocol-smoke-runtime-semantics",
            "runtime_version": "1.0",
            "runtime_name": "codex",
            "authority_profile": "agent-queue-v1",
            "agent_id": "ws-3-3-agent",
            "project_standards_version": "1.0",
            "security_standards_version": "1.0",
            "code_standards_version": "1.0",
            "capabilities": ["repository_read"],
        }
    )

    assert context_fingerprint(first) == context_fingerprint(second)
