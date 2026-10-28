"""What a standing dependency-update package covers, read from a checkout of its repository.

Two lanes name the same bump: the bump proposer, which creates and revises the package, and the
work watcher, which alerts when the work built from it fails. Both must key that alert on the same
(repository, ecosystem, dependency), so the reading lives here, once, and neither lane imports the
other (bridge Part 3, Ruling 5).

A package states no ecosystem field; its id does (`<repo name>-<ecosystem>-<slug>`, intent-packages'
`standing_package_id`). The slug is lossy, so the dependency is read from the package's own
`package` field and never reconstructed from the id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PROFILE: Final = "dependency-update"
# The tooling names intent-packages' `create-standing` has a profile for.
TOOLING_NAMES: Final = ("npm", "pip", "uv")
PACKAGES_DIRECTORY: Final = "packages"
DEFAULT_CHECKOUT: Final = Path.home() / "Projects" / "intent-packages"


class PackageUnreadable(Exception):
    """The package file could not be read: the checkout or the package is not there."""


@dataclass(frozen=True)
class BumpIdentity:
    repository: str
    ecosystem: str
    dependency: str


def yaml_scalar(text: str, key: str) -> str | None:
    """One top-level-ish scalar, read without a YAML parser.

    This program's third-party surface is `httpx` and nothing else -- the isolation test says
    so -- and pulling in a YAML library to read four values from a file whose shape is fixed
    would widen it for no gain. The four values read here are all plain unquoted scalars in a
    document this producer and `factory create` are the only writers of.
    """
    match = re.search(rf"^\s*{re.escape(key)}:\s*(\S.*?)\s*$", text, re.MULTILINE)
    if match is None:
        return None
    value = match.group(1)
    # QUOTES ARE STRIPPED, and they are not decoration. A version like `1.13` is a FLOAT in
    # YAML and `4` is an INT, so every version this repository's packages carry that could be
    # read as a number is written quoted -- and a reader that kept the quotes would compare
    # `'1.13'` against `1.13` and conclude the standing package carries a different bump on
    # every pass, revising it forever.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def ecosystem_of(package_id: str, target_repository: str) -> str | None:
    """The tooling ecosystem a package id names, or None when it names none."""
    name = target_repository.rsplit("/", 1)[-1].lower()
    for tooling in TOOLING_NAMES:
        if package_id.startswith(f"{name}-{tooling}-"):
            return tooling
    return None


def read_identity(root: Path, package_id: str) -> BumpIdentity | None:
    """The bump a standing dependency-update package covers.

    None means the package is definitely not one: another profile, not standing, missing fields,
    or an id that names no tooling. A file that cannot be read raises `PackageUnreadable`, which is
    a different answer: the caller knows nothing about this package.
    """
    if Path(package_id).name != package_id or package_id in {"", ".", ".."}:
        return None
    try:
        text = (root / PACKAGES_DIRECTORY / package_id / "package.yaml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise PackageUnreadable(type(error).__name__) from None
    if yaml_scalar(text, "profile") != PROFILE or yaml_scalar(text, "standing") != "true":
        return None
    repository = yaml_scalar(text, "target_repo")
    dependency = yaml_scalar(text, "package")
    if not repository or not dependency:
        return None
    ecosystem = ecosystem_of(package_id, repository)
    if ecosystem is None:
        return None
    return BumpIdentity(repository, ecosystem, dependency)
