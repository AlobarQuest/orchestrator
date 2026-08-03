"""Command-constraint validation for the work-unit authority envelope.

One predicate, shared with the runner's own authority validation:
`allowed_commands` is required whenever `command.run` is allowed;
`mutation_commands` is required only for `change_class` "dependency-update",
where a command produces the diff; a present `mutation_commands` must always
be well-formed and a subset of `allowed_commands`. Edit-shaped work omits
`mutation_commands` honestly — the coding agent produces the diff and no
command mutates a tracked file.

The gate here may only ever be stricter than the runner's, never looser: an
envelope admitted here and refused by the runner dies mid-run with its
ordinal spent, which is how WS-P2.33 started. The shared golden fixtures
(`tests/fixtures/runner_authority_envelope*.json`, byte-identical in both
repositories) pin both directions.
"""

from dataclasses import dataclass
from typing import Any

from orchestrator.kernel.authority import AuthorityEnvelope


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


def _non_empty_string_list(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not value:
        return None
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return None
    return tuple(value)
