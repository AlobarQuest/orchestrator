"""Whether a standing bump's envelope makes exactly the bump its package names (ADR-0028 am. 1).

A known-good pattern recognises the shape of a command, never which dependency it moves, so it
would recognise `uv add 'anything>=1'` in an envelope for a package about something else. This
binds the two at the policy approval: the commands must be the ones intent-packages' generator
emits for this package's `profile_fields`, and nothing else. It is an allowlist of whole commands
per tooling, compared against values read from the revision. It parses no shell, and every form
it doesn't list is a violation, so a person decides that envelope.

Which tooling comes from the standing package's id, as `pr_merge_admission` reads it. A pattern
accepts any of the three toolings' commands, so the tooling itself has to be bound too.

`allowed_commands` is the coding agent's whole vocabulary, so it and `verify_commands` are bound
too: a second `uv add` the agent may run is a second bump, wherever the envelope lists it.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from orchestrator.services.membership.holds import (
    TOOLING_TO_DEPENDABOT,
    ecosystem_of_package_id,
)

_UV_LOCK: Final = "uv lock"
_UV_VERIFIER: Final = "uv lock --check"
_UV_SECTION: Final = (
    r"( --dev| --group [A-Za-z0-9][A-Za-z0-9_-]*| --optional [A-Za-z0-9][A-Za-z0-9_-]*)?"
)
_NPM_BUILD: Final = "npm run build"
# not-a-vocabulary: the generator's gate components for npm, in the order it emits them
_NPM_COMPONENTS: Final = ("npm ci", "npx eslint .", "npx prettier --check .", "npm test")
# not-a-vocabulary: the generator's pip manifests, compared against commands and never a wire value
_PIP_FILES: Final = ("requirements.txt", "requirements-dev.txt")


@dataclass(frozen=True)
class _Bump:
    package: str
    from_version: str
    to_version: str


def _uv_add(bump: _Bump, *, frozen: bool) -> re.Pattern[str]:
    flag = " --frozen" if frozen else ""
    requirement = re.escape(f"'{bump.package}>={bump.to_version}'")
    return re.compile(rf"uv add{flag}{_UV_SECTION} {requirement}")


def _uv_mutations(bump: _Bump, commands: Sequence[str]) -> bool:
    """One `uv add`, which locks inline; or a pin split across sections: at least two frozen
    `uv add`s, then one `uv lock`."""
    if len(commands) == 1:
        return _uv_add(bump, frozen=False).fullmatch(commands[0]) is not None
    frozen = _uv_add(bump, frozen=True)
    return (
        len(commands) > 2
        and commands[-1] == _UV_LOCK
        and all(frozen.fullmatch(command) for command in commands[:-1])
    )


def _uv_verifiers(_bump: _Bump, _mutations: Sequence[str], commands: Sequence[str]) -> bool:
    return list(commands) == [_UV_VERIFIER]


def _pip_sed(bump: _Bump, file: str) -> str:
    old, new = f"{bump.package}=={bump.from_version}", f"{bump.package}=={bump.to_version}"
    return f"sed -i 's/^{old}$/{new}/' {file}"


def _pip_mutations(bump: _Bump, commands: Sequence[str]) -> bool:
    return all(command in {_pip_sed(bump, file) for file in _PIP_FILES} for command in commands)


def _pip_verifiers(bump: _Bump, mutations: Sequence[str], commands: Sequence[str]) -> bool:
    """One grep of the primary manifest: `requirements.txt` when the bump edits it."""
    primary = _PIP_FILES[0] if _pip_sed(bump, _PIP_FILES[0]) in mutations else _PIP_FILES[1]
    return list(commands) == [f"grep -qx '{bump.package}=={bump.to_version}' {primary}"]


def _npm_mutations(bump: _Bump, commands: Sequence[str]) -> bool:
    """One exact install, then the repository's build when it has one."""
    install = f"npm install {bump.package}@{bump.to_version} --save-exact"
    return commands[0] in (install, f"{install} --save-dev") and list(commands[1:]) in (
        [],
        [_NPM_BUILD],
    )


def _npm_verifiers(bump: _Bump, _mutations: Sequence[str], commands: Sequence[str]) -> bool:
    """The gate components the repository has, in order, then the grep of the pin."""
    pin = f"""grep -q '"{bump.package}": "{bump.to_version}"' package.json"""
    components = list(commands[:-1])
    return list(commands[-1:]) == [pin] and components == [
        command for command in _NPM_COMPONENTS if command in components
    ]


_FORMS: Final = {
    TOOLING_TO_DEPENDABOT["uv"]: (_uv_mutations, _uv_verifiers),
    TOOLING_TO_DEPENDABOT["pip"]: (_pip_mutations, _pip_verifiers),
    TOOLING_TO_DEPENDABOT["npm"]: (_npm_mutations, _npm_verifiers),
}


def _text(fields: Mapping[str, Any], key: str) -> str | None:
    value = fields.get(key)
    return value if isinstance(value, str) and value else None


def _commands(constraints: Mapping[str, Any], field: str) -> tuple[str, ...] | None:
    """A non-empty list of strings, or `None`."""
    value = constraints.get(field)
    if not isinstance(value, list) or not value:
        return None
    return tuple(value) if all(isinstance(entry, str) for entry in value) else None


def bump_binding_violations(
    envelope: Mapping[str, Any], profile_fields: Mapping[str, Any], package_id: str
) -> tuple[str, ...]:
    """Why this envelope isn't the generator's for this bump; empty means it is.

    Reads a stored envelope and another repository's snapshot, so nothing here trusts a shape.
    """
    package = _text(profile_fields, "package")
    old = _text(profile_fields, "from_version")
    new = _text(profile_fields, "to_version")
    target = _text(profile_fields, "target_repo")
    if package is None or old is None or new is None or target is None:
        return ("profile_fields_unreadable",)
    constraints = envelope.get("constraints")
    if not isinstance(constraints, Mapping):
        return ("constraints_unreadable",)
    mutations = _commands(constraints, "mutation_commands")
    verifiers = _commands(constraints, "verify_commands")
    allowed = _commands(constraints, "allowed_commands")
    if mutations is None or verifiers is None:
        return ("commands_unreadable",)

    violations: list[str] = []
    repository = constraints.get("target_repository")
    if not isinstance(repository, str) or repository.casefold() != target.casefold():
        violations.append("target_repository_differs")
    ecosystem = ecosystem_of_package_id(package_id, target)
    if ecosystem is None:
        return (*violations, "tooling_unreadable")
    bump = _Bump(package, old, new)
    mutations_match, verifiers_match = _FORMS[ecosystem]
    if len(set(mutations)) != len(mutations) or not mutations_match(bump, mutations):
        violations.append("mutation_commands_differ")
    if not verifiers_match(bump, mutations, verifiers):
        violations.append("verify_commands_differ")
    if allowed != (*mutations, *verifiers):
        violations.append("allowed_commands_differ")
    return tuple(violations)
