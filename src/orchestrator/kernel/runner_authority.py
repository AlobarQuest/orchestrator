from dataclasses import dataclass
from typing import Any

from orchestrator.kernel.authority import AuthorityEnvelope


@dataclass(frozen=True)
class AuthorityViolation:
    code: str
    message: str
    remediation: str


def dependency_update_authority_violation(
    envelope: AuthorityEnvelope,
) -> AuthorityViolation | None:
    if envelope.change_class != "dependency-update":
        return None
    if envelope.level_for("repo.edit") != "allowed":
        return None
    if envelope.level_for("command.run") != "allowed":
        return AuthorityViolation(
            "authority_command_run_required",
            "dependency-update repo.edit authority requires command.run",
            "allow command.run and declare the exact command lists",
        )
    allowed = _non_empty_string_list(envelope.constraints.get("allowed_commands"))
    if allowed is None:
        return AuthorityViolation(
            "authority_allowed_commands_invalid",
            "constraints.allowed_commands must be a non-empty list of non-empty strings",
            "declare the complete ordered command list",
        )
    mutations = _non_empty_string_list(envelope.constraints.get("mutation_commands"))
    if mutations is None:
        return AuthorityViolation(
            "authority_mutation_commands_invalid",
            "constraints.mutation_commands must be a non-empty list of non-empty strings",
            "declare the ordered commands expected to mutate the dependency",
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
