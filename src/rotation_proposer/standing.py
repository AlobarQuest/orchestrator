"""The standing rotation packages, in the checkout of the repository that authors them (ADR-0054).

A standing rotation package is authored once per credential, on the `non-software-operational`
profile, with `standing: true` and the credential's infraops registry id in `credential_id`. A
REVISION carries one rotation, named by `occurrence` -- exactly as a standing dependency-update
package's revision carries one bump in `from_version`/`to_version` (ADR-0028). This module finds the
package that stands for a credential and takes a new revision of it as far as review, and no
further.

**IT NEVER APPROVES.** No approval policy grants this profile -- intent-packages refuses it
`profile_not_policy_approvable` -- and ADR-0054 amendment 1 has Devon approve every rotation
revision by name. So the ladder below stops at `ready_for_review`, where `bump_proposer`'s climbs
on to `approve --by-policy`. Nothing in this package names the approve command at all.

**EVERY AUDITED ACT IS `bump_proposer`'s, NOT A COPY.** The lifecycle commands run through the
packages checkout's own interpreter (`bump_proposer.standing.lifecycle`), the hash fixture is
re-pinned by the same function, and the commit is published by the same `publish` -- the one place
this estate pushes a package revision, whose merge-guard exemption names this program as its second
caller. A second copy of any of them would be a second implementation of an audited path.

**THE CHECKOUT IS THE ONE `bump_proposer` USES**, read from the same variable, because it is the
same checkout: both lanes commit to it, and each refuses to start on the other's unfinished work.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from bump_proposer import standing as shared
from bump_proposer.standing import StandingError
from rotation_worker.destinations import same

PROFILE: Final = "non-software-operational"
PACKAGE_PREFIX: Final = "rotation-"

_OCCURRENCE_LINE: Final = re.compile(r"^(\s*occurrence:).*$", re.MULTILINE)
# `profile_fields.destinations`: the key at two spaces and its items, quoted or not, each with
# the continuation lines a YAML writer folds a long item onto.
_DESTINATIONS_BLOCK: Final = re.compile(
    r"^(  destinations:\n)((?:  - .*\n(?:    .*\n)*)*)", re.MULTILINE
)

# A top-level key's line; everything up to the next one, less the comments and blank lines before
# it, is that key's block.
_TOP_LEVEL_KEY: Final = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):")
# What a re-rendered body never writes: the package's identity and lifecycle, which `revise` and
# `transition` own, and its creation, which a revision keeps.
_LIFECYCLE_KEYS: Final = frozenset(
    {"schema_version", "package_id", "revision", "status", "created_by", "created_at", "supersedes"}
)

DRAFT: Final = "draft"
APPROVED: Final = "approved"
REJECTED: Final = "rejected"
# Unapproved and in front of a person: the revision a named human has yet to decide.
IN_REVIEW: Final = frozenset({"ready_for_review", "needs_clarification"})
# States `revise` is legal from in intent-packages (`lifecycle.REVISE_LEGAL_FROM`) that this
# program may revise FROM: a revision already decided, or a draft whose number is still free.
# The two IN_REVIEW states are legal there too and are deliberately absent here -- see `cli`.
REVISABLE: Final = frozenset({APPROVED, REJECTED, DRAFT})


@dataclass(frozen=True)
class RotationPackage:
    """One standing rotation package as it stands on disk right now."""

    package_id: str
    path: Path
    credential_id: str
    revision: int
    state: str
    occurrence: str
    # `profile_fields.destinations`, as listed; empty when the package lists none.
    destinations: tuple[str, ...] = ()

    def carries(self, occurrence: str) -> bool:
        return self.occurrence == occurrence

    def lists(self, destinations: Sequence[str]) -> bool:
        """Whether the package lists exactly these destinations, in any order."""
        return same(self.destinations, destinations)


def discover(root: Path) -> dict[str, RotationPackage]:
    """Every standing rotation package in the checkout, keyed on the credential it stands for.

    A credential the registry hands to the SDS with no package here gets one generated when it
    first comes due (`create`, ADR-0055 amendment 15, increment 4).
    """
    base = root / "packages"
    if not base.is_dir():
        raise StandingError(f"no packages directory in the checkout at {root}")
    found: dict[str, RotationPackage] = {}
    for package_yaml in sorted(base.glob("*/package.yaml")):
        text = package_yaml.read_text(encoding="utf-8")
        if shared.yaml_scalar(text, "profile") != PROFILE:
            continue
        if shared.yaml_scalar(text, "standing") != "true":
            # The historical rotation package (WS-P2.13) shares this profile and is one finished
            # piece of work. Only an AUTHOR's declaration makes a package a lane.
            continue
        credential_id = shared.yaml_scalar(text, "credential_id")
        revision = shared.yaml_scalar(text, "revision")
        if not credential_id or not revision or not revision.isdigit():
            continue
        package_id = package_yaml.parent.name
        if package_id != f"{PACKAGE_PREFIX}{credential_id}":
            # The name is how a person finds the package for a credential, and how this program's
            # output names it. A package standing for one credential under another's name is a
            # question about the checkout, not an answer about the work.
            raise StandingError(
                f"{package_id} stands for {credential_id}; a standing rotation package is named "
                f"{PACKAGE_PREFIX}<credential_id>"
            )
        found[credential_id] = RotationPackage(
            package_id=package_id,
            path=package_yaml.parent,
            credential_id=credential_id,
            revision=int(revision),
            state=shared.yaml_scalar(text, "status") or "",
            occurrence=shared.yaml_scalar(text, "occurrence") or "",
            destinations=_destinations(text),
        )
    return found


def _destinations(text: str) -> tuple[str, ...]:
    try:
        fields = (yaml.safe_load(text) or {}).get("profile_fields") or {}
    except yaml.YAMLError:
        return ()
    listed = fields.get("destinations") if isinstance(fields, dict) else None
    return tuple(str(d) for d in listed) if isinstance(listed, list) else ()


def write_occurrence(package: RotationPackage, occurrence: str) -> None:
    """Write the one per-rotation value, and nothing else in the document.

    QUOTED, ALWAYS: the profile requires a string, and an occurrence that ever looked like a date
    would load as one. Replaced by a function rather than a template, so no character in the value
    can be read as a back-reference.
    """
    path = package.path / "package.yaml"
    text = path.read_text(encoding="utf-8")
    text, count = _OCCURRENCE_LINE.subn(lambda match: f"{match.group(1)} '{occurrence}'", text)
    if count != 1:
        raise StandingError(f"{package.package_id}: expected one occurrence line, found {count}")
    path.write_text(text, encoding="utf-8")


def write_destinations(package: RotationPackage, destinations: Sequence[str]) -> None:
    """Write the registry's destination list over the package's, and nothing else.

    Each is single-quoted, as authored packages quote them, since a destination holds double
    quotes; one holding a single quote is refused rather than escaped.
    """
    if any("'" in destination for destination in destinations):
        raise StandingError(f"{package.package_id}: a destination holds a single quote")
    path = package.path / "package.yaml"
    text = path.read_text(encoding="utf-8")
    listed = "".join(f"  - '{destination}'\n" for destination in destinations)
    text, count = _DESTINATIONS_BLOCK.subn(lambda match: match.group(1) + listed, text)
    if count != 1:
        raise StandingError(f"{package.package_id}: expected one destinations list, found {count}")
    path.write_text(text, encoding="utf-8")


def document(package: RotationPackage) -> dict[str, Any]:
    """The package's document as it stands on disk."""
    try:
        loaded = yaml.safe_load((package.path / "package.yaml").read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise StandingError(f"{package.package_id}: package.yaml doesn't parse: {error}") from error
    if not isinstance(loaded, dict):
        raise StandingError(f"{package.package_id}: package.yaml is no mapping")
    return loaded


def _blocks(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Each top-level key's `[start, end)` line span, comments before the next key left out."""
    starts = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (match := _TOP_LEVEL_KEY.match(line))
    ]
    blocks: dict[str, tuple[int, int]] = {}
    for position, (start, key) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
            end -= 1
        blocks[key] = (start, end)
    return blocks


def _spliced(package: RotationPackage, body: Mapping[str, Any]) -> str:
    """The package's text with every top-level block but the lifecycle's replaced from `body`.

    Each block is replaced by the same dump intent-packages' `create-rotation` writes, so a comment
    it put between blocks stays. The result must parse to the file's lifecycle keys plus the body,
    or nothing is written: a block the splice misread is refused, never written."""
    text = (package.path / "package.yaml").read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    blocks = _blocks(lines)
    written = {key: value for key, value in body.items() if key not in _LIFECYCLE_KEYS}
    if set(written) != set(blocks) - _LIFECYCLE_KEYS:
        raise StandingError(f"{package.package_id}: the re-rendered body names other keys")
    for key, (start, end) in sorted(blocks.items(), key=lambda item: -item[1][0]):
        if key in written:
            dumped = yaml.safe_dump(
                {key: written[key]}, sort_keys=False, default_flow_style=False, allow_unicode=True
            )
            lines[start:end] = [dumped]
    spliced = "".join(lines)
    kept = {key: value for key, value in document(package).items() if key in _LIFECYCLE_KEYS}
    if yaml.safe_load(spliced) != {**kept, **written}:
        raise StandingError(f"{package.package_id}: the re-rendered body didn't splice cleanly")
    return spliced


def write_body(package: RotationPackage, body: Mapping[str, Any]) -> None:
    """Write a re-rendered body over every top-level key but the lifecycle's, and nothing else."""
    spliced = _spliced(package, body)
    (package.path / "package.yaml").write_text(spliced, encoding="utf-8")


def create(rendered: dict[str, Any], root: Path) -> RotationPackage:
    """Write revision 1 of a generated package, as a draft, through intent-packages, which
    validates it and begins its lineage (`create-rotation`)."""
    credential_id = str(rendered["profile_fields"]["credential_id"])
    with tempfile.TemporaryDirectory(prefix="rotation-proposer-") as staging:
        source = Path(staging) / "package.json"
        source.write_text(json.dumps(rendered), encoding="utf-8")
        shared.lifecycle(root, "create-rotation", str(source), "--out", str(root / "packages"))
    created = discover(root).get(credential_id)
    if created is None:
        raise StandingError(f"rotation-{credential_id} wasn't written")
    return created


def reread(package: RotationPackage, root: Path) -> RotationPackage:
    """The same package as it stands now. Every lifecycle command rewrites the file."""
    fresh = discover(root).get(package.credential_id)
    if fresh is None:
        raise StandingError(f"{package.package_id} vanished from the checkout mid-pass")
    return fresh


def to_review(
    package: RotationPackage,
    occurrence: str,
    destinations: Sequence[str],
    root: Path,
    *,
    body: Mapping[str, Any] | None = None,
) -> RotationPackage:
    """Take the package to a revision carrying this occurrence and the registry's destinations,
    in review -- and never past it. A `body` (`generate.reissue_body`) is written over the
    revision's wording first.

    Resumable from any point a previous pass could have stopped at, which is why the state is
    re-read between steps: a crash between the write and `transition` leaves a draft that already
    carries both and needs only transitioning. A crash before the body lands leaves a package
    the next pass still finds stating the old plan, so it passes the body again.
    """
    current = package
    if body is not None or not current.carries(occurrence) or not current.lists(destinations):
        if body is not None:
            # Before `revise`: a body that can't land spends no revision.
            _spliced(current, body)
        if current.state != DRAFT:
            # A draft has never been approved, so its revision number is still free and writing
            # over it is correct. Anything else gets a new revision.
            shared.lifecycle(root, "revise", str(current.path))
            current = reread(current, root)
        if body is not None:
            write_body(current, body)
        write_occurrence(current, occurrence)
        write_destinations(current, destinations)
        current = reread(current, root)
    if current.state == DRAFT:
        # `transition` re-snapshots the revision hash, which makes the edit part of the revision.
        shared.lifecycle(root, "transition", str(current.path), "--to", "ready_for_review")
        current = reread(current, root)
    return current


def commit(
    package: RotationPackage, root: Path, *, generated: bool = False, rerendered: bool = False
) -> str:
    """Commit and publish the revision through `bump_proposer`'s one publishing act (ADR-0033)."""
    if generated:
        made = (
            "The package is generated from the credential's registry row and its type's derived\n"
            "plan (ADR-0055 amendment 15, increment 4)."
        )
    elif rerendered:
        made = (
            "The revision re-renders the generated package for the plan its registry row now\n"
            "derives (ADR-0055 amendment 20): its criteria, prose and authority change."
        )
    else:
        made = "The revision carries the rotation and the registry's destinations."
    message = (
        f"{package.package_id} rev {package.revision}: rotate for {package.occurrence}\n\n"
        "Written by rotation-proposer (ADR-0054). infraops reports this credential due for\n"
        f"rotation. {made} The revision\n"
        "is NOT approved: no approval policy grants this profile, and a named human approves\n"
        "it before the proposer writes the work record a person then approves in\n"
        "change-manager.\n"
    )
    return shared.publish(package, package.revision, message, root)
