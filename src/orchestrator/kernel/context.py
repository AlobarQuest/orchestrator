import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

REQUIRED_CONTEXT_FIELDS = (
    "code_standards_version",
    "security_standards_version",
    "project_standards_version",
    "agent_id",
    "authority_profile",
    "runtime_name",
    "runtime_version",
    "skill_bundle_id",
    "skill_bundle_version",
    "capabilities",
)
VERSION_PATTERN = re.compile(r"\d+(?:\.\d+)*")
AUTHORITY_PROFILE_RANK = {
    "verifier-v1": 0,
    "agent-queue-v1": 1,
    "interactive-dev-v1": 2,
    "human-operator-v1": 3,
    "system-v1": 4,
}


@dataclass(frozen=True)
class ContextDecision:
    classification: str
    decision: str
    reasons: tuple[str, ...]


def normalize_standing_context(value: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for field in REQUIRED_CONTEXT_FIELDS:
        if field == "capabilities":
            normalized[field] = _normalize_capabilities(value.get(field))
            continue
        field_value = value.get(field)
        normalized[field] = field_value if isinstance(field_value, str) else ""
    return normalized


def context_fingerprint(context: Mapping[str, object]) -> str:
    canonical = json.dumps(
        normalize_standing_context(context),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def classify_context_update(
    previous: Mapping[str, object] | None,
    current: Mapping[str, object],
    required: Mapping[str, object],
    allowed_capabilities: set[str],
) -> ContextDecision:
    current_normalized = normalize_standing_context(current)
    required_normalized = normalize_standing_context(required)
    previous_normalized = (
        normalize_standing_context(previous) if previous is not None else None
    )

    missing_fields = tuple(
        field for field in REQUIRED_CONTEXT_FIELDS if _is_missing_field(current, current_normalized, field)
    )
    if missing_fields:
        return ContextDecision(
            classification="missing_required",
            decision="rejected",
            reasons=tuple(f"missing:{field}" for field in missing_fields),
        )

    stale_reasons = _stale_reasons(current_normalized, required_normalized)
    if stale_reasons:
        return ContextDecision(
            classification="stale",
            decision="rejected",
            reasons=stale_reasons,
        )

    if previous_normalized is not None and (
        context_fingerprint(previous_normalized) == context_fingerprint(current_normalized)
    ):
        return ContextDecision(
            classification="accepted",
            decision="accepted",
            reasons=("unchanged",),
        )

    expansion_reasons = _authority_expansion_reasons(
        previous_normalized=previous_normalized,
        current_normalized=current_normalized,
        required_normalized=required_normalized,
        allowed_capabilities=allowed_capabilities,
    )
    if expansion_reasons:
        return ContextDecision(
            classification="authority_expanding",
            decision="requires_approval",
            reasons=expansion_reasons,
        )

    same_scope_reasons: list[str] = []
    if previous_normalized is not None:
        previous_capabilities = set(previous_normalized["capabilities"])
        current_capabilities = set(current_normalized["capabilities"])
        if current_capabilities < previous_capabilities:
            same_scope_reasons.append("capabilities_narrowed")
        elif _standard_versions_changed(previous_normalized, current_normalized):
            same_scope_reasons.append("standards_newer_or_equal")

    if not same_scope_reasons:
        same_scope_reasons.append("same_scope")

    return ContextDecision(
        classification="same_scope",
        decision="accepted",
        reasons=tuple(same_scope_reasons),
    )


def _normalize_capabilities(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return sorted({str(item) for item in value if str(item)})


def _is_missing_field(
    raw_current: Mapping[str, object],
    normalized_current: Mapping[str, object],
    field: str,
) -> bool:
    if field not in raw_current:
        return True
    value = normalized_current[field]
    if field == "capabilities":
        return not isinstance(value, list)
    return value == ""


def _stale_reasons(
    current_normalized: Mapping[str, object],
    required_normalized: Mapping[str, object],
) -> tuple[str, ...]:
    reasons: list[str] = []
    for field in (
        "code_standards_version",
        "security_standards_version",
        "project_standards_version",
        "runtime_version",
        "skill_bundle_version",
    ):
        if _version_is_stale(
            str(current_normalized[field]),
            str(required_normalized[field]),
        ):
            reasons.append(f"stale:{field}")
    for field in ("agent_id", "runtime_name", "skill_bundle_id"):
        if current_normalized[field] != required_normalized[field]:
            reasons.append(f"mismatch:{field}")
    return tuple(reasons)


def _version_is_stale(current: str, required: str) -> bool:
    if current == required:
        return False
    current_parts = _parse_version(current)
    required_parts = _parse_version(required)
    if current_parts is None or required_parts is None:
        return True
    return current_parts < required_parts


def _parse_version(value: str) -> tuple[int, ...] | None:
    if VERSION_PATTERN.fullmatch(value) is None:
        return None
    parts = tuple(int(part) for part in value.split("."))
    trimmed = parts
    while len(trimmed) > 1 and trimmed[-1] == 0:
        trimmed = trimmed[:-1]
    return trimmed


def _authority_expansion_reasons(
    *,
    previous_normalized: Mapping[str, object] | None,
    current_normalized: Mapping[str, object],
    required_normalized: Mapping[str, object],
    allowed_capabilities: set[str],
) -> tuple[str, ...]:
    reasons: list[str] = []
    current_capabilities = set(current_normalized["capabilities"])
    baseline_capabilities = (
        set(previous_normalized["capabilities"])
        if previous_normalized is not None
        else set(required_normalized["capabilities"])
    )
    if not current_capabilities.issubset(allowed_capabilities):
        reasons.append("capabilities_expanded")
    elif current_capabilities > baseline_capabilities:
        reasons.append("capabilities_expanded")

    baseline_profile = (
        str(previous_normalized["authority_profile"])
        if previous_normalized is not None
        else str(required_normalized["authority_profile"])
    )
    current_profile = str(current_normalized["authority_profile"])
    if _authority_profile_expands(baseline_profile, current_profile):
        reasons.append("authority_profile_expanded")
    return tuple(reasons)


def _authority_profile_expands(previous: str, current: str) -> bool:
    if current == previous:
        return False
    previous_rank = AUTHORITY_PROFILE_RANK.get(previous)
    current_rank = AUTHORITY_PROFILE_RANK.get(current)
    if previous_rank is None or current_rank is None:
        return True
    return current_rank > previous_rank


def _standard_versions_changed(
    previous_normalized: Mapping[str, object],
    current_normalized: Mapping[str, object],
) -> bool:
    for field in (
        "code_standards_version",
        "security_standards_version",
        "project_standards_version",
    ):
        if previous_normalized[field] != current_normalized[field]:
            return True
    return False
