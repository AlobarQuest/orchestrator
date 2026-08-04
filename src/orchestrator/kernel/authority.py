import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# constraints, change_class and conformance are the envelope fields dispatch routes and
# admits on (target repository, change-class allowlist, conformance of that repository).
# They must enter the fingerprint by value, or an approved fingerprint would cover an
# envelope naming a different repo, a different class, or a different conformance claim.
#
# `unknown_fields` is here because normalized() EMITS it, and normalized() is what gets stored
# as the envelope. Without it in this set, normalize_authority(env.normalized()) reports the key
# `unknown_fields` as itself an unknown field -- so normalized() was never a fixed point, EVERY
# stored envelope grew a self-referential unknown field on re-read (dispatch and the runner brief
# both re-normalize the stored column), and the re-derived fingerprint disagreed with the one that
# was minted. Adding it here costs nothing: a raw authored envelope has no `unknown_fields` key,
# so its unknown-field set is empty either way and its fingerprint is byte-identical. Verified --
# this must stay true, because rewriting fingerprints would invalidate the approval ledger.
KNOWN_FIELDS = frozenset(
    {"capabilities", "budgets", "constraints", "change_class", "conformance", "unknown_fields"}
)
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


def runner_payload(envelope: AuthorityEnvelope) -> dict[str, Any]:
    """`normalized()` minus the key the runner's model forbids.

    `normalized()` emits `unknown_fields` on purpose -- without it, re-normalizing a stored
    envelope would report the key as itself unknown and every fingerprint would drift on re-read.
    But the runner's `AuthorityEnvelope` is `extra="forbid"` and does not declare it, so the
    normalized form is not a payload any runner can parse. Anything stored through this helper
    has already been refused by `runner_envelope_field_violation` if its unknown-field set is
    non-empty, so dropping an empty list here loses no record.

    Does NOT affect the fingerprint: `authority_fingerprint` reads the envelope, never the
    stored payload.
    """
    return {name: item for name, item in envelope.normalized().items() if name != "unknown_fields"}


def normalize_authority(value: Mapping[str, Any]) -> AuthorityEnvelope:
    capabilities_value = value.get("capabilities", {})
    budgets_value = value.get("budgets", {})
    unknown_fields = set(value).difference(KNOWN_FIELDS)
    # A STORED envelope carries its unknown fields as names inside `unknown_fields`, not as
    # top-level keys -- normalized() records the names and drops the values on purpose (an
    # unknown field must never contribute a value to the fingerprint). Reading only the KEYS
    # back would therefore LOSE the record entirely: a fail-closed marker that does not survive
    # being stored is not fail-closed. Union both, so normalized() is a true fixed point whether
    # or not the envelope had unknown fields.
    unknown_fields.update(_recorded_unknown_fields(value))
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


def _recorded_unknown_fields(value: Mapping[str, Any]) -> set[str]:
    """The unknown-field names a previously-normalized envelope recorded.

    Malformed (not a list of strings) is itself an unknown field -- fail closed rather than
    silently dropping the record.
    """
    recorded = value.get("unknown_fields")
    if recorded is None:
        return set()
    if isinstance(recorded, list) and all(isinstance(name, str) for name in recorded):
        return set(recorded)
    return {"unknown_fields"}


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

    NOTE (WS-P2.34): `unknown_fields` IS read, by two consumers, and this note used to say
    nothing read it — true when WS-P2.15 deleted `is_expansion()`, false since WS-P2.18 Inc 3
    gave `factory_policy._recognises` a clause on it. It is now also the whole basis of
    `runner_envelope_field_violation`, which refuses such an envelope at both unit-ingress
    paths: the runner's model is `extra="forbid"`, so a field this build does not understand
    is a parse failure there, and a field outside `KNOWN_FIELDS` contributes only its NAME to
    the fingerprint, so an approval of it attests to nothing about its value.

    Units the orchestrator MINTS for itself are constructed directly and never traverse those
    gates, which is why two historical release-verification units still carry a recorded
    unknown field. Before adding a gate that reaches them, see
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
