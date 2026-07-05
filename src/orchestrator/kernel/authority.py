import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

RESTRICTION = {"prohibited": 0, "requires_approval": 1, "allowed": 2}
KNOWN_FIELDS = frozenset({"capabilities", "budgets"})
KNOWN_BUDGETS = frozenset({"max_attempts", "max_llm_calls"})


@dataclass(frozen=True)
class AuthorityBudgets:
    max_attempts: int | None
    max_llm_calls: int | None

    def expands(self, old: "AuthorityBudgets") -> bool:
        return _limit_expands(old.max_attempts, self.max_attempts) or _limit_expands(
            old.max_llm_calls, self.max_llm_calls
        )


@dataclass(frozen=True)
class AuthorityEnvelope:
    capabilities: Mapping[str, str]
    budgets: AuthorityBudgets
    unknown_fields: frozenset[str] = frozenset()

    def level_for(self, capability: str) -> str:
        return self.capabilities.get(capability, "prohibited")

    def normalized(self) -> dict[str, Any]:
        return {
            "budgets": {
                "max_attempts": self.budgets.max_attempts,
                "max_llm_calls": self.budgets.max_llm_calls,
            },
            "capabilities": dict(sorted(self.capabilities.items())),
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

    return AuthorityEnvelope(
        capabilities=capabilities,
        budgets=AuthorityBudgets(
            max_attempts=_budget_value(budgets_value.get("max_attempts")),
            max_llm_calls=_budget_value(budgets_value.get("max_llm_calls")),
        ),
        unknown_fields=frozenset(unknown_fields),
    )


def authority_fingerprint(envelope: AuthorityEnvelope) -> str:
    canonical = json.dumps(
        envelope.normalized(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def is_expansion(old: AuthorityEnvelope, new: AuthorityEnvelope) -> bool:
    if new.unknown_fields:
        return True
    capabilities = set(old.capabilities) | set(new.capabilities)
    for capability in capabilities:
        old_level = RESTRICTION.get(old.level_for(capability))
        new_level = RESTRICTION.get(new.level_for(capability))
        if old_level is None or new_level is None:
            return True
        if new_level > old_level:
            return True
    return new.budgets.expands(old.budgets)


def _limit_expands(old: int | None, new: int | None) -> bool:
    if old is None:
        return False
    return new is None or new > old


def _budget_value(value: Any) -> int | None:
    return value if _is_budget_value(value) else None


def _is_budget_value(value: Any) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)
