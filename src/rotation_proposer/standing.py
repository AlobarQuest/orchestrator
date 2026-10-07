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

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from bump_proposer import standing as shared
from bump_proposer.standing import StandingError

PROFILE: Final = "non-software-operational"
PACKAGE_PREFIX: Final = "rotation-"

_OCCURRENCE_LINE: Final = re.compile(r"^(\s*occurrence:).*$", re.MULTILINE)

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

    def carries(self, occurrence: str) -> bool:
        return self.occurrence == occurrence


def discover(root: Path) -> dict[str, RotationPackage]:
    """Every standing rotation package in the checkout, keyed on the credential it stands for.

    **SCOPE IS THE AUTHORED SET.** A credential nobody has authored a package for is outside this
    lane, and authoring one -- never this program -- is what brings it in.
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
        )
    return found


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


def reread(package: RotationPackage, root: Path) -> RotationPackage:
    """The same package as it stands now. Every lifecycle command rewrites the file."""
    fresh = discover(root).get(package.credential_id)
    if fresh is None:
        raise StandingError(f"{package.package_id} vanished from the checkout mid-pass")
    return fresh


def to_review(package: RotationPackage, occurrence: str, root: Path) -> RotationPackage:
    """Take the package to a revision carrying this occurrence, in review -- and never past it.

    Resumable from any point a previous pass could have stopped at, which is why the state is
    re-read between steps: a crash between the write and `transition` leaves a draft that already
    carries the occurrence and needs only transitioning.
    """
    current = package
    if not current.carries(occurrence):
        if current.state != DRAFT:
            # A draft has never been approved, so its revision number is still free and writing
            # over it is correct. Anything else gets a new revision.
            shared.lifecycle(root, "revise", str(current.path))
            current = reread(current, root)
        write_occurrence(current, occurrence)
        current = reread(current, root)
    if current.state == DRAFT:
        # `transition` re-snapshots the revision hash, which makes the edit part of the revision.
        shared.lifecycle(root, "transition", str(current.path), "--to", "ready_for_review")
        current = reread(current, root)
    return current


def commit(package: RotationPackage, root: Path) -> str:
    """Commit and publish the revision through `bump_proposer`'s one publishing act (ADR-0033)."""
    message = (
        f"{package.package_id} rev {package.revision}: rotate for {package.occurrence}\n\n"
        "Written by rotation-proposer (ADR-0054). infraops reports this credential due for\n"
        "rotation, so the standing package's new revision carries the rotation. The revision\n"
        "is NOT approved: no approval policy grants this profile, and a named human approves\n"
        "it before the proposer writes the work record a person then approves in\n"
        "change-manager.\n"
    )
    return shared.publish(package, package.revision, message, root)
