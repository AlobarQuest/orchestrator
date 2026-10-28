"""Holds: a version ceiling on one dependency in one member repository.

A hold is keyed `(ecosystem, directory, dependency)` within its member and spelled as Dependabot
spells it (`npm_and_yarn`, `pip`, `uv`, `docker`, `github_actions`, ...). Only a confirmed member
proposal writes one (`proposals.py`).

**A VERSION THAT CANNOT BE READ IS NEVER "BELOW THE CEILING".** `release` returns `None` for
anything it cannot read, and every caller treats `None` as held (or, for a ceiling, refuses it).

**THE DEPENDENCY NAME IS STORED AS CONFIRMED, NOT NORMALIZED.** Staging refuses padding, a
non-lowercase ecosystem and a malformed directory, but name equivalence (PEP 503 for pip and uv:
case-folded, runs of `-_.` equal; lowercase for npm) is the matcher's to apply when it compares a
bump's dependency with a hold's.

**TWO COPIES, ONE ANSWER.** The matcher below (`_exceeded`, with `_bumps_of` reading what an update
moves) is copied beside the lanes' member client, since the lanes can't import the orchestrator
and this copy can't import a lane. `tests/services/test_hold_agreement.py` runs both over one
corpus and pins the title read to the lander's `BUMP_PATTERN`. The functions stay
private-prefixed until the first admission calls them (`test_unreachable_guards.py`).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Final, Protocol

_RELEASE: Final = re.compile(r"v?(\d+(?:\.\d+)*)")
# What may follow a dot after the release: PEP 440's dotted pre-, post- and dev-release parts.
# Anything else after a dot (`1.x`, `1.2.*`) is a wildcard or a typo, never a release.
_DOTTED_SUFFIX: Final = re.compile(r"\.(?:post|dev|rc|a|b)")

# The lander's single-update title read, byte-identical to `landing_ledger.titles.BUMP_PATTERN`
# and pinned to it: `bump <name> from <a> to <b>` at the end of the title.
_BUMP_PATTERN: Final = r"\bfrom v?(\d[\d.]*) to v?(\d[\d.]*)$"
_TITLE_BUMP: Final = re.compile(_BUMP_PATTERN)

_BLOCK_HEADER: Final = "updated-dependencies:"
# not-a-vocabulary: the lines that end a YAML block, not values any system exchanges
_BLOCK_ENDS: Final = frozenset({"...", "---", ""})
_ENTRY: Final = re.compile(r"^\s*-\s+([\w-]+):\s*(.*?)\s*$")
_FIELD: Final = re.compile(r"^\s+([\w-]+):\s*(.*?)\s*$")

# not-a-vocabulary: the Dependabot ecosystems whose registry compares names by PEP 503
_PEP_503_ECOSYSTEMS: Final = frozenset({"pip", "uv"})
_PEP_503_RUN: Final = re.compile(r"[-_.]+")

# A standing package's tooling (`<repo>-<tooling>-<slug>`, intent-packages' `standing_package_id`)
# as Dependabot spells its ecosystem. Held equal to the lanes' copy by the agreement test.
TOOLING_TO_DEPENDABOT: Final = {"npm": "npm_and_yarn", "pip": "pip", "uv": "uv"}

# Tooling and dependabot.yml spellings whose Dependabot ecosystem name differs, each mapped to the
# name a person meant. A hold typed with one would match no update, so validation refuses it.
# not-a-vocabulary: refused spellings with a hint; Dependabot's names themselves are not enumerated
MISSPELLED_ECOSYSTEMS: Final = {
    "npm": "npm_and_yarn",
    "yarn": "npm_and_yarn",
    "pnpm": "npm_and_yarn",
    "github-actions": "github_actions",
}


def release(version: str) -> tuple[int, ...] | None:
    """The leading dotted numeric release of `version`, or `None` when it has none.

    A leading `v` is stripped, and anything after the release is ignored when it starts a
    pre-release, build or post-release part (`2.0.55rc1`, `1.2.3-beta.1`, `1.2.3+b5`,
    `1.2.3.post1` all read as their release). Refused, so `None`: no leading digit (`latest`,
    `^1.2`, `>=1.2`, surrounding whitespace), a release that ends in a dot or doubles one (`1.`,
    `2..0`), a wildcard (`1.x`, `1.2.*`, `1*`), anything else after a dot that is not a PEP 440
    `.post`, `.dev`, `.rc`, `.a` or `.b` part, and a PEP 440 epoch (`1!2.0`), which would otherwise
    read as release `(1,)` and compare low.
    """
    match = _RELEASE.match(version)
    if match is None:
        return None
    rest = version[match.end() :]
    if rest.startswith(("!", "*")) or (rest.startswith(".") and not _DOTTED_SUFFIX.match(rest)):
        return None
    return tuple(int(part) for part in match.group(1).split("."))


@dataclass(frozen=True)
class Bump:
    """One dependency an update moves, as Dependabot names it. `directory` is `None` when the
    update did not say which manifest it touched, and then it matches a hold on any directory."""

    ecosystem: str
    directory: str | None
    dependency: str
    to_version: str


@dataclass(frozen=True)
class HoldBreach:
    """A bump that exceeds a hold: the hold as confirmed, and the version the bump would bring."""

    repository: str
    ecosystem: str
    directory: str
    dependency: str
    ceiling: str
    version: str


class HeldDependency(Protocol):
    """What the matcher reads from a hold, whichever side's record carries it."""

    @property
    def ecosystem(self) -> str: ...
    @property
    def directory(self) -> str: ...
    @property
    def dependency(self) -> str: ...
    @property
    def ceiling(self) -> str: ...


def _exceeded(
    holds: Iterable[HeldDependency], repository: str, bumps: Iterable[Bump]
) -> tuple[HoldBreach, ...]:
    """Every (bump, hold) pair where the bump moves the held dependency beyond its ceiling.

    Empty means admit. THIS IS THE ONLY PLACE THAT DECIDES: every lane and service that honours
    holds calls it rather than comparing versions itself. A bump whose version, or a hold whose
    ceiling, `release` can't read is beyond the ceiling.
    """
    held = tuple(holds)
    return tuple(
        HoldBreach(
            repository=repository,
            ecosystem=hold.ecosystem,
            directory=hold.directory,
            dependency=hold.dependency,
            ceiling=hold.ceiling,
            version=bump.to_version,
        )
        for bump in bumps
        for hold in held
        if _same_dependency(hold, bump) and _beyond(bump.to_version, hold.ceiling)
    )


def _same_dependency(hold: HeldDependency, bump: Bump) -> bool:
    return (
        hold.ecosystem.lower() == bump.ecosystem.lower()
        and (bump.directory is None or _directory(bump.directory) == _directory(hold.directory))
        and _name(hold.ecosystem, hold.dependency) == _name(bump.ecosystem, bump.dependency)
    )


def _directory(directory: str) -> str:
    return directory.rstrip("/") or "/"


def _name(ecosystem: str, dependency: str) -> str:
    """The name as its registry compares it. PEP 503 for pip and uv; every other ecosystem is
    case-folded only, which is npm's rule and errs toward matching (so toward holding) for the
    rest."""
    if ecosystem.lower() in _PEP_503_ECOSYSTEMS:
        return _PEP_503_RUN.sub("-", dependency).lower()
    return dependency.lower()


def _beyond(version: str, ceiling: str) -> bool:
    """Is `version`'s release greater than `ceiling`'s? Releases of different length are padded
    with zeros, so `2.0.54.0` equals `2.0.54`. Either one unreadable is beyond."""
    ours, limit = release(version), release(ceiling)
    if ours is None or limit is None:
        return True
    width = max(len(ours), len(limit))
    return ours + (0,) * (width - len(ours)) > limit + (0,) * (width - len(limit))


def _dependencies_of(commit_message: str, ecosystem: str) -> tuple[Bump, ...] | None:
    """Every dependency Dependabot's `updated-dependencies` block names, or `None` when there is
    no block this can read.

    A small line parser, not a YAML library: `- key: value` opens an entry, an indented
    `key: value` adds to it, and `...`, `---` or a blank line ends the block. Anything else inside
    a block, a key twice in one entry, an entry with no `dependency-name` or a block with no
    entries makes the whole message unreadable. Every block in the message is read. Quotes around
    a value are stripped. An entry with no `dependency-version` reads as version `""`, which
    `release` can't read, so it breaches any hold on its dependency.
    """
    lines = commit_message.splitlines()
    entries: list[dict[str, str]] = []
    found = False
    index = 0
    while index < len(lines):
        if lines[index].strip() != _BLOCK_HEADER:
            index += 1
            continue
        found = True
        index += 1
        block: list[dict[str, str]] = []
        while index < len(lines) and lines[index].strip() not in _BLOCK_ENDS:
            line = lines[index]
            entry, field = _ENTRY.match(line), _FIELD.match(line)
            if entry is not None:
                block.append({entry.group(1): _unquote(entry.group(2))})
            elif field is not None and block and field.group(1) not in block[-1]:
                block[-1][field.group(1)] = _unquote(field.group(2))
            else:
                return None
            index += 1
        if not block:
            return None
        entries.extend(block)
    if not found or any(not entry.get("dependency-name") for entry in entries):
        return None
    return tuple(
        Bump(
            ecosystem=ecosystem,
            directory=entry.get("directory") or None,
            dependency=entry["dependency-name"],
            to_version=entry.get("dependency-version", ""),
        )
        for entry in entries
    )


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _bumps_of(title: str, commit_message: str, ecosystem: str) -> tuple[Bump, ...] | None:
    """What an update moves: the block's dependencies, with a single update's version taken from
    the title.

    The title is the fresh source: Dependabot rewrites a pull request in place and its trailer can
    lag (`estate_landing_admission.update_type_of`). So a single update takes its name from the
    block and its version from the title when the lander's title read matches; when it doesn't (a
    requirement range, a subdirectory suffix) the block's version stands. A grouped update's title
    names no versions, so both come from the block. `None` when the block can't be read.
    """
    bumps = _dependencies_of(commit_message, ecosystem)
    if bumps is None or len(bumps) != 1:
        return bumps
    match = _TITLE_BUMP.search(title.strip())
    if match is None:
        return bumps
    return (replace(bumps[0], to_version=match.group(2)),)


def _ecosystem_of_package_id(package_id: str, repository: str) -> str | None:
    """The Dependabot ecosystem a standing package's id names, or `None` when it names none.

    The id is `<repo name>-<tooling>-<slug>`, read exactly as the lanes' `standing_identity`
    reads it: from after the repository's own name, since a name can hold a tooling word
    (`pip-tools-npm-left-pad`). That is why the repository is an argument.
    """
    name = repository.rsplit("/", 1)[-1].lower()
    for tooling, ecosystem in TOOLING_TO_DEPENDABOT.items():
        if package_id.startswith(f"{name}-{tooling}-"):
            return ecosystem
    return None
