"""Envelope validation for the work-unit authority envelope: what the runner refuses.

`runner_command_authority_violation` is the command-constraint half, shared with the
runner's own authority validation:
`allowed_commands` is required whenever `command.run` is allowed;
`mutation_commands` is required only for `change_class` "dependency-update",
where a command produces the diff; a present `mutation_commands` must always
be well-formed and a subset of `allowed_commands`. Edit-shaped work omits
`mutation_commands` honestly — the coding agent produces the diff and no
command mutates a tracked file.

`runner_envelope_field_violation` is the envelope-shape half (WS-P2.34): the runner's
model forbids extra fields outright, so a field this build does not understand is a
parse failure there rather than an ignorable extra.

The gate here may only ever be stricter than the runner's, never looser: an
envelope admitted here and refused by the runner dies mid-run with its
ordinal spent, which is how WS-P2.33 started. The shared golden fixtures
(`tests/fixtures/runner_authority_envelope*.json`, byte-identical in both
repositories) pin both directions.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from orchestrator.kernel.authority import AuthorityEnvelope

# The top-level fields the runner's own envelope model declares. NOT this repo's KNOWN_FIELDS:
# that set additionally contains `unknown_fields`, which the runner's `extra="forbid"` model
# rejects outright. Pinned across repos by the byte-identical
# `tests/fixtures/runner_envelope_contract.json`, from which the worker repo derives the same
# set out of its pydantic model directly.
RUNNER_ENVELOPE_FIELDS: Final[frozenset[str]] = frozenset(
    {"budgets", "capabilities", "change_class", "conformance", "constraints"}
)

# The levels a capability may be declared at. Unlike the capability NAMES -- where the
# orchestrator's set is a deliberate superset, because it authors work no runner performs -- this
# is exactly the runner's set. A level is not a kind of work; it is the grant itself, and
# `level_for` treats everything that is not "allowed" as "prohibited" anyway. Pinned by the same
# byte-identical fixture as the field set.
RUNNER_CAPABILITY_LEVELS: Final[frozenset[str]] = frozenset({"allowed", "prohibited"})


@dataclass(frozen=True)
class AuthorityViolation:
    code: str
    message: str
    remediation: str


def runner_command_authority_violation(
    envelope: AuthorityEnvelope,
) -> AuthorityViolation | None:
    is_dependency_update = envelope.change_class == "dependency-update"
    if (
        is_dependency_update
        and envelope.level_for("repo.edit") == "allowed"
        and envelope.level_for("command.run") != "allowed"
    ):
        return AuthorityViolation(
            "authority_command_run_required",
            "dependency-update repo.edit authority requires command.run",
            "allow command.run and declare the exact command lists",
        )
    if envelope.level_for("command.run") != "allowed":
        return None
    allowed = _non_empty_string_list(envelope.constraints.get("allowed_commands"))
    if allowed is None:
        return AuthorityViolation(
            "authority_allowed_commands_invalid",
            "constraints.allowed_commands must be a non-empty list of non-empty strings",
            "declare the complete ordered command list",
        )
    if "mutation_commands" not in envelope.constraints:
        if is_dependency_update:
            return AuthorityViolation(
                "authority_mutation_commands_invalid",
                "constraints.mutation_commands must be a non-empty list of non-empty strings",
                "declare the ordered commands expected to mutate the dependency",
            )
        return None
    mutations = _non_empty_string_list(envelope.constraints["mutation_commands"])
    if mutations is None:
        return AuthorityViolation(
            "authority_mutation_commands_invalid",
            "constraints.mutation_commands must be a non-empty list of non-empty strings",
            "declare the ordered mutating commands, or omit the key when no command mutates",
        )
    if any(command not in allowed for command in mutations):
        return AuthorityViolation(
            "authority_mutation_command_not_allowed",
            "every mutation command must also appear in constraints.allowed_commands",
            "add the mutation command to allowed_commands without changing its spelling",
        )
    return None


def runner_envelope_field_violation(
    envelope: AuthorityEnvelope,
    payload: Mapping[str, Any],
) -> AuthorityViolation | None:
    """Refuse an envelope the runner's model cannot parse.

    The runner's `AuthorityEnvelope` is `extra="forbid"`, so an unrecognised top-level key is
    not a tolerated extra: it is a pydantic `ValidationError` before `validate_authority` ever
    runs, which surfaces as a crash rather than as a named `AuthorityError`.

    TWO checks, because either alone is a fail-open.

    `payload` is the dict that will be STORED, and therefore the exact bytes the brief serves
    the runner. Its keys are compared against `RUNNER_ENVELOPE_FIELDS` -- the runner's declared
    field set, pinned across repos -- and NOT against this repo's `KNOWN_FIELDS`, which differs
    by exactly one member in the fail-open direction. `KNOWN_FIELDS` contains `unknown_fields`,
    deliberately, so that `normalized()` is a fixed point; the runner's model does not declare
    it. So an authored envelope carrying `"unknown_fields": []` -- which is precisely what
    `normalized()` emits, and therefore precisely what an operator copies out of the `/review`
    page or a proposal body into the next hand-authored breakdown -- has an EMPTY unknown-field
    set here and is fatal there. Keying this predicate on the orchestrator's own vocabulary
    would have reproduced the WS-P2.33 defect inside the function written to close it.

    `envelope.unknown_fields` is the second check and is not redundant: it also records a known
    field whose VALUE was malformed (a non-mapping `capabilities`, a budget that is not a count,
    a `constraints` the fingerprint could not encode). Those leave the top-level key set legal
    while the runner still refuses them.

    Refusing at ingress rather than only at admission is the point. Fields outside
    `KNOWN_FIELDS` contribute only their NAMES to the authority fingerprint, never their values
    -- so an envelope that reaches a human carries a field whose content nobody's approval
    attests to. That is a defect in what the approval MEANS, and it has to be caught before the
    approval exists, not after.
    """
    extra = set(payload) - RUNNER_ENVELOPE_FIELDS
    if extra:
        names = ", ".join(sorted(extra))
        return AuthorityViolation(
            "authority_unknown_fields",
            f"the authority envelope declares fields the runner's model forbids: {names}",
            "remove the field -- the runner refuses the whole envelope, before validating it",
        )
    if not envelope.unknown_fields:
        return None
    names = ", ".join(sorted(envelope.unknown_fields))
    return AuthorityViolation(
        "authority_unknown_fields",
        f"the authority envelope carries fields this build does not understand: {names}",
        "remove the field, or fix its value if the name is one the envelope defines",
    )


def runner_capability_level_violation(
    envelope: AuthorityEnvelope,
) -> AuthorityViolation | None:
    """Refuse a capability declared at a level the runner does not accept.

    The runner validates the level of EVERY entry, so one bad level anywhere kills the envelope.
    This fails OPEN without a check, which is the opposite of an unknown capability NAME:
    `level_for` compares against "allowed", so a mistyped level on a capability the work does not
    need reads as a prohibition and every orchestrator gate is satisfied.

    `requires_approval` is the shape that actually occurs -- it is the PACKAGE-authority
    vocabulary of ADR-0001, and projecting package authority into unit capabilities is left to
    the breakdown author, i.e. to a human writing JSON by hand.
    """
    for capability, level in sorted(envelope.capabilities.items()):
        if level not in RUNNER_CAPABILITY_LEVELS:
            return AuthorityViolation(
                "unknown_capability_level",
                f"capability {capability!r} is declared at level {level!r}, "
                "which the runner refuses",
                "declare one of: " + ", ".join(sorted(RUNNER_CAPABILITY_LEVELS)),
            )
    return None


def runner_authority_violation(
    envelope: AuthorityEnvelope,
    payload: Mapping[str, Any],
) -> AuthorityViolation | None:
    """Every reason the runner would refuse this envelope, in one place.

    THE composition. Four surfaces ask this question -- breakdown ingress, unit registration,
    the human authority approval (both the service and the form the `/review` page renders) and
    admission -- and before WS-P2.34 each asked a different subset of it by hand. That is the
    same second-copy failure at a smaller scale than the cross-repo one this module exists to
    close, and it is how the level and field rules ended up enforced at one surface out of four.

    Ordered most fundamental first: an envelope the runner's model cannot PARSE never reaches
    its validation, so reporting a command-list defect on it would send somebody to fix the
    wrong thing.

    Capability NAMES are deliberately NOT here. The orchestrator's accepted set is a superset --
    it authors work no runner performs -- so a name refusal belongs only on the surface that
    knows the unit is bound for a runner, which is admission.
    """
    return (
        runner_envelope_field_violation(envelope, payload)
        or runner_capability_level_violation(envelope)
        or runner_command_authority_violation(envelope)
    )


def _non_empty_string_list(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not value:
        return None
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return None
    return tuple(value)
