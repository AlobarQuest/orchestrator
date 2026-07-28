"""Package-declared follow-up scheduling (WS-P2.8).

The intent package declares WHETHER an outcome should be revisited; this module owns the
orchestrator's side of that contract. `revisit_when` and `signals` are prose written for a human
and are never parsed -- the timing comes from configuration, not from the text.

The four field names mirror the intent-packages schema exactly. A fifth key is a validation
error rather than an ignored extra, because a silently-dropped key is how a declaration and its
reader drift apart.
"""

from typing import Any

from orchestrator.errors import DomainError

# The intent-packages `follow_up` block, mirrored field for field. Every key is mandatory-present;
# `revisit_when` and `owner` may be null. Registered in the cross-boundary vocabulary registry.
FOLLOW_UP_FIELDS = ("required", "revisit_when", "signals", "owner")


def _invalid(detail: str) -> DomainError:
    return DomainError(
        "follow_up_invalid",
        f"package follow_up declaration is invalid: {detail}",
        "correct the package follow_up block and re-emit the intake payload",
    )


def validate_follow_up(value: object) -> dict[str, Any] | None:
    """Return the normalized declaration, or None when the package carried none."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _invalid("it must be a mapping")
    unknown = sorted(set(value) - set(FOLLOW_UP_FIELDS))
    if unknown:
        raise _invalid(f"unknown key {unknown[0]!r}")
    missing = [field for field in FOLLOW_UP_FIELDS if field not in value]
    if missing:
        raise _invalid(f"missing required key {missing[0]!r}")
    if not isinstance(value["required"], bool):
        raise _invalid("`required` must be a boolean")
    for field in ("revisit_when", "owner"):
        if value[field] is not None and not isinstance(value[field], str):
            raise _invalid(f"`{field}` must be a string or null")
    signals = value["signals"]
    if not isinstance(signals, list) or not all(isinstance(item, str) for item in signals):
        raise _invalid("`signals` must be a list of strings")
    return {
        "required": value["required"],
        "revisit_when": value["revisit_when"],
        "signals": list(signals),
        "owner": value["owner"],
    }
