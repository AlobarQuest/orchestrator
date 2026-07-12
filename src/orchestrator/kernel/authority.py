import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# constraints, change_class and conformance are the envelope fields dispatch routes and
# admits on (target repository, change-class allowlist, conformance of that repository).
# They must enter the fingerprint by value, or an approved fingerprint would cover an
# envelope naming a different repo, a different class, or a different conformance claim.
KNOWN_FIELDS = frozenset({"capabilities", "budgets", "constraints", "change_class", "conformance"})
KNOWN_BUDGETS = frozenset({"max_attempts", "max_llm_calls"})


@dataclass(frozen=True)
class AuthorityBudgets:
    max_attempts: int | None
    max_llm_calls: int | None


@dataclass(frozen=True)
class AuthorityEnvelope:
    capabilities: Mapping[str, str]
    budgets: AuthorityBudgets
    unknown_fields: frozenset[str] = frozenset()
    constraints: Mapping[str, Any] = field(default_factory=dict)
    change_class: str | None = None
    conformance: Mapping[str, Any] | None = None

    def level_for(self, capability: str) -> str:
        return self.capabilities.get(capability, "prohibited")

    def normalized(self) -> dict[str, Any]:
        return {
            "budgets": {
                "max_attempts": self.budgets.max_attempts,
                "max_llm_calls": self.budgets.max_llm_calls,
            },
            "capabilities": dict(sorted(self.capabilities.items())),
            "change_class": self.change_class,
            "conformance": dict(sorted(self.conformance.items()))
            if self.conformance is not None
            else None,
            "constraints": dict(sorted(self.constraints.items())),
            "unknown_fields": sorted(self.unknown_fields),
        }


def normalize_authority(value: Mapping[str, Any]) -> AuthorityEnvelope:
    capabilities_value = value.get("capabilities", {})
    budgets_value = value.get("budgets", {})
    unknown_fields = set(value).difference(KNOWN_FIELDS)
    if not isinstance(capabilities_value, Mapping):
        capabilities: dict[str, str] = {}
        unknown_fields.add("capabilities")
    else:
        capabilities = {
            str(capability): str(level) for capability, level in capabilities_value.items()
        }
    if not isinstance(budgets_value, Mapping):
        budgets_value = {}
        unknown_fields.add("budgets")
    else:
        unknown_fields.update(f"budgets.{name}" for name in set(budgets_value) - KNOWN_BUDGETS)
        for name in KNOWN_BUDGETS:
            if name in budgets_value and not _is_budget_value(budgets_value[name]):
                unknown_fields.add(f"budgets.{name}")

    constraints: dict[str, Any] = {}
    if "constraints" in value:
        constraints_value = value["constraints"]
        if isinstance(constraints_value, Mapping) and _is_canonicalizable(constraints_value):
            constraints = {str(name): item for name, item in constraints_value.items()}
        else:
            unknown_fields.add("constraints")

    change_class, change_class_ok = _optional_change_class(value)
    if not change_class_ok:
        unknown_fields.add("change_class")
    conformance, conformance_ok = _optional_conformance(value)
    if not conformance_ok:
        unknown_fields.add("conformance")

    return AuthorityEnvelope(
        capabilities=capabilities,
        budgets=AuthorityBudgets(
            max_attempts=_budget_value(budgets_value.get("max_attempts")),
            max_llm_calls=_budget_value(budgets_value.get("max_llm_calls")),
        ),
        unknown_fields=frozenset(unknown_fields),
        constraints=constraints,
        change_class=change_class,
        conformance=conformance,
    )


def authority_fingerprint(envelope: AuthorityEnvelope) -> str:
    canonical = json.dumps(
        envelope.normalized(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _budget_value(value: Any) -> int | None:
    return value if _is_budget_value(value) else None


def _is_budget_value(value: Any) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def _optional_change_class(value: Mapping[str, Any]) -> tuple[str | None, bool]:
    """Returns the parsed value and whether it was well formed.

    A null means "absent", so that normalized() — which emits these fields explicitly and
    is stored verbatim as some units' envelope — round-trips without inventing unknown
    fields. A malformed value is reported as an unknown field.

    NOTE (WS-P2.15): nothing reads `unknown_fields`. It used to feed `is_expansion()`, which
    had no production caller and is now deleted. So an unknown field is RECORDED, not acted
    on — this docstring previously claimed "every admission gate treats it as fail-closed",
    which was already false. Before adding such a gate, see
    tests/architecture/test_authority_write_once.py.
    """
    raw = value.get("change_class")
    if raw is None:
        return None, True
    if isinstance(raw, str) and raw:
        return raw, True
    return None, False


def _optional_conformance(value: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, bool]:
    raw = value.get("conformance")
    if raw is None:
        return None, True
    if isinstance(raw, Mapping) and _is_canonicalizable(raw):
        return {str(name): item for name, item in raw.items()}, True
    return None, False


def _is_canonicalizable(value: Mapping[str, Any]) -> bool:
    """Constraint values must survive the fingerprint's canonical JSON encoding.

    Anything json cannot encode would raise inside authority_fingerprint, so it is
    rejected here and surfaces as an unknown field — which is recorded, but which nothing
    currently acts on (see _optional_change_class).
    """
    try:
        json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return False
    return True
