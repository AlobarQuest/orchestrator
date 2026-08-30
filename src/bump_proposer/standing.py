"""The standing packages, in a checkout of the repository that authors them (ADR-0028).

A standing package is authored once per (repository, ecosystem, dependency) and carries the
bump in a REVISION. This module is everything this producer does to one: find the package that
covers a bump, read what its tip revision currently carries, and -- when that is not this bump
-- write the two version fields and take the revision through the audited lifecycle.

**IT SHELLS OUT, AND THAT IS THE POINT.** The lifecycle operations live in
`AlobarQuest/intent-packages`, one repository over, and an approval there requires both a
hash-bound ledger entry and a `package.approved` event in the tamper-evident chain. Reproducing
any of that here would be a second implementation of an audited path -- the thing the chain
exists to make impossible. The carry does the same for the same reason.

**THE INTERPRETER IS RESOLVED FROM THE CHECKOUT, not from PATH.** Which copy of the lifecycle
code runs decides what is written into that checkout's ledger, so it must be that checkout's
own, and an absent one is a named refusal rather than whatever else happens to be installed.

**IT COMMITS, AND IT REFUSES TO START ON A DIRTY TREE.** Committing is not tidiness: the
orchestrator's own intake payload records `source_commit` as the checkout's `git HEAD`, so a
revision left uncommitted is registered against a commit that does not contain it -- a
provenance claim that is simply untrue. And the hash fixture the authoring repository pins its
packages with moves with every revision, so a tree left dirty fails that repository's own gate.
Refusing a dirty tree is what stops this program sweeping somebody else's work-in-progress into
a commit it wrote the message for.

**AND IT PUBLISHES WHAT IT COMMITS (ADR-0033).** Leaving the branch for a person to move was
right while a person invoked this producer and could move it in the same breath; scheduling it
severed the two, and nobody is told to push. The three costs of stopping half-way are each
measured in that decision -- the revision gets no CI at all, the `source_commit` the intake
records names a commit only one machine holds, and local `main` drifts from `origin` under
every other lane that reads this checkout. Publishing surrenders no gate, because that
repository's default branch takes a direct push and reports its required checks afterwards
whoever performs it. The exemption this needs from the repository-wide merge guard is taken
openly, in that guard's own register, and is named in ADR-0033 rather than implied here.

**IT WRITES EXACTLY TWO LINES OF YAML**, by targeted line replacement rather than by re-dumping
the document -- the technique `intent_packages.operations.set_revision_in_file` already uses,
and for the same reason: everything else in a standing package is hand-authored prose, and a
round trip through a YAML dumper would reflow all of it and lose every comment.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from landing_ledger.titles import Bump

DEFAULT_CHECKOUT: Final = Path.home() / "Projects" / "intent-packages"
PACKAGES_REPOSITORY: Final = "AlobarQuest/intent-packages"
PROFILE: Final = "dependency-update"
HASH_FIXTURE: Final = Path("tests") / "fixtures" / "package_hashes.json"
COMMAND_TIMEOUT_SECONDS: Final = 120.0

# THE ACT, SPELLED THE WAY THE GUARD SCANS FOR IT, and a STRING rather than a list of four
# words on purpose. `MERGE_EXEMPT_PATHS` in `tests/architecture/test_wsp21_invariant_scan.py`
# names this file, and the rot check beside it removes an entry whose file no longer contains
# one of `MERGE_ACTIONS`. That scan reads this file's TEXT, where a list literal matches
# nothing -- so writing the command as `["git", "push", ...]` would pass the guard without an
# exemption, and would then have the exemption withdrawn as unneeded, leaving the act in place
# with nothing watching it. Not by rewording, which ADR-0033 forbids, but by tokenisation,
# which amounts to the same evasion. Written this way the bytes the guard scans and the command
# that runs are ONE value, so neither can outlive the other.
PUBLISH_COMMAND: Final = "git push origin main"

# The placeholder both version fields hold in a standing package nobody has filled in. It is
# the SAME string in both on purpose: the approval policy refuses a revision whose two versions
# are equal, so an unfilled shell cannot be approved into a revision describing no work.
UNASSIGNED: Final = "unassigned"

_FROM_LINE: Final = re.compile(r"^(\s*from_version:).*$", re.MULTILINE)
_TO_LINE: Final = re.compile(r"^(\s*to_version:).*$", re.MULTILINE)

_APPROVED: Final = "approved"
_DRAFT: Final = "draft"


class StandingError(Exception):
    """The checkout cannot be used, or a lifecycle command refused. Always a finding."""


@dataclass(frozen=True)
class StandingPackage:
    """One standing package as it stands on disk right now."""

    package_id: str
    path: Path
    target_repository: str
    dependency: str
    revision: int
    state: str
    from_version: str
    to_version: str

    def carries(self, bump: Bump) -> bool:
        return self.from_version == bump.from_version and self.to_version == bump.to_version

    @property
    def approved(self) -> bool:
        return self.state == _APPROVED


def checkout_root() -> Path:
    return Path(os.environ.get("BUMP_PROPOSER_PACKAGES_CHECKOUT") or DEFAULT_CHECKOUT)


def _interpreter(root: Path) -> Path:
    override = os.environ.get("BUMP_PROPOSER_PACKAGES_PYTHON")
    return Path(override) if override else root / ".venv" / "bin" / "python"


def _yaml_scalar(text: str, key: str) -> str | None:
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


def discover(root: Path | None = None) -> dict[tuple[str, str], StandingPackage]:
    """Every standing dependency-update package in the checkout, keyed on what it covers.

    **SCOPE IS THE AUTHORED SET**, exactly as the deploy producer's scope is the transcribed
    set. A bump whose (repository, dependency) nobody has authored a package for is out of this
    lane, and adding it to the lane is authoring one -- not editing a list here.
    """
    root = root or checkout_root()
    base = root / "packages"
    if not base.is_dir():
        raise StandingError(f"no packages directory in the checkout at {root}")
    found: dict[tuple[str, str], StandingPackage] = {}
    for package_yaml in sorted(base.glob("*/package.yaml")):
        text = package_yaml.read_text(encoding="utf-8")
        if _yaml_scalar(text, "profile") != PROFILE:
            continue
        target = _yaml_scalar(text, "target_repo")
        dependency = _yaml_scalar(text, "package")
        revision = _yaml_scalar(text, "revision")
        if _yaml_scalar(text, "standing") != "true":
            # ADR-0028: only a package whose AUTHOR declared it standing is a lane. Every
            # dependency-update package in that repository declares this same profile and a
            # target repository, so without this the eight historical packages -- each naming
            # one finished bump -- are matched and revised. Measured, not imagined.
            continue
        if not target or not dependency or not revision or not revision.isdigit():
            continue
        key = (target.lower(), dependency)
        package = StandingPackage(
            package_id=package_yaml.parent.name,
            path=package_yaml.parent,
            target_repository=target,
            dependency=dependency,
            revision=int(revision),
            state=_yaml_scalar(text, "status") or "",
            from_version=_yaml_scalar(text, "from_version") or "",
            to_version=_yaml_scalar(text, "to_version") or "",
        )
        if key in found:
            # Two packages claiming one bump is a question about the checkout, not an answer
            # about the work: whichever this pass happened to pick would be arbitrary.
            raise StandingError(
                f"two standing packages cover {target} {dependency}: "
                f"{found[key].package_id} and {package.package_id}"
            )
        found[key] = package
    return found


# `Sequence`, not `list`: `PUBLISH_COMMAND.split()` yields a `list[LiteralString]`, and
# `list` is invariant so it refuses one. Widening the parameter to the covariant type is
# pyright's own suggestion and accepts every existing caller unchanged.
def _run(root: Path, command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        raise StandingError(f"{command[0]} is not on this machine") from error
    except subprocess.TimeoutExpired as error:
        raise StandingError(f"{' '.join(command[:3])} did not finish in time") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise StandingError(
            f"{' '.join(command[1:4])} refused: {detail[-1] if detail else 'no output'}"
        )
    return completed.stdout


def _lifecycle(root: Path, *args: str) -> str:
    python = _interpreter(root)
    if not python.is_file():
        raise StandingError(f"the packages checkout has no interpreter at {python}")
    return _run(root, [str(python), "-m", "intent_packages", *args])


def require_clean(root: Path) -> None:
    """Refuse to act on a checkout carrying changes this program did not make."""
    dirty = _run(root, ["git", "status", "--porcelain"]).strip()
    if dirty:
        raise StandingError(
            f"the packages checkout at {root} has uncommitted changes; "
            "this program commits what it writes and will not commit somebody else's"
        )


def require_published(root: Path) -> None:
    """Refuse to act on a checkout carrying a commit this program wrote and could not publish.

    **THE SIBLING OF `require_clean`, AND IT EXISTS BECAUSE THE REPLAY PATH SKIPS THE PUSH.**
    A pass whose publish is refused has already written and committed an approved revision --
    and a non-fast-forward refusal is the ORDINARY way that happens, since anything landing in
    the authoring repository leaves this checkout behind until somebody reconciles it. The next
    pass then finds the package carrying the bump and approved, skips straight to proposing the
    record, and never reaches the publishing step again: the commit would sit local forever
    while the lane reported a clean run. ADR-0033 says a failed publish is a finding rather
    than a warning, and a finding that is reported once and then goes quiet is neither.

    So the residue is refused here instead, at the top of the pass, which also stops this
    program stacking a second revision on top of one it could not publish.

    **IT NEEDS NO FETCH, AND MUST NOT MAKE ONE.** `origin/main` is the remote-tracking ref: a
    successful push advances it and a refused push does not, so it answers exactly this
    question from what is already on disk. Going to the network would make this program's
    refusal depend on somebody else's landings rather than on its own unfinished act.
    """
    ahead = _run(root, ["git", "rev-list", "--count", "origin/main..HEAD"]).strip()
    if not ahead.isdigit():
        raise StandingError(f"the checkout at {root} would not say how far ahead of origin it is")
    if int(ahead) > 0:
        raise StandingError(
            f"the packages checkout at {root} carries {ahead} commit(s) origin does not; "
            "this program publishes what it commits and will not write another revision on "
            "top of one it could not publish"
        )


def write_versions(package: StandingPackage, bump: Bump) -> None:
    """Write the two per-bump values, and nothing else in the document."""
    path = package.path / "package.yaml"
    text = path.read_text(encoding="utf-8")
    # QUOTED, ALWAYS. `from_version: 1.13` is a YAML float and `to_version: 7` is an int,
    # and the package schema requires strings -- so an unquoted write produces a revision that
    # fails its own repository's validator, at approve time, after the file has been edited.
    text, from_count = _FROM_LINE.subn(rf"\1 '{bump.from_version}'", text)
    text, to_count = _TO_LINE.subn(rf"\1 '{bump.to_version}'", text)
    if from_count != 1 or to_count != 1:
        raise StandingError(
            f"{package.package_id}: expected one from_version and one to_version line, "
            f"found {from_count} and {to_count}"
        )
    path.write_text(text, encoding="utf-8")


def reread(package: StandingPackage, root: Path | None = None) -> StandingPackage:
    """The same package as it stands now. Every lifecycle command rewrites the file."""
    packages = discover(root)
    fresh = packages.get((package.target_repository.lower(), package.dependency))
    if fresh is None:
        raise StandingError(f"{package.package_id} vanished from the checkout mid-pass")
    return fresh


def advance(package: StandingPackage, bump: Bump, root: Path) -> StandingPackage:
    """Take the standing package to an APPROVED revision carrying this bump.

    Resumable from any point a previous pass could have stopped at, which is why the state is
    re-read between steps rather than assumed: a crash between `transition` and `approve` leaves
    a revision that already carries the right bump and needs only approving, and revising it
    again would spend a revision number to reach the state it is already in.
    """
    current = package
    if not current.carries(bump):
        if current.state != _DRAFT:
            # `revise` is the only way back to an unapproved revision, and it is legal from
            # every pre-execution state. A draft revision has never been approved, so its
            # number is still free and reusing it is correct rather than thrifty.
            _lifecycle(root, "revise", str(current.path))
            current = reread(current, root)
        write_versions(current, bump)
        current = reread(current, root)
    if current.state == _DRAFT:
        # `transition` re-snapshots the revision hash, which is what makes the edit above part
        # of the revision rather than drift against it.
        _lifecycle(root, "transition", str(current.path), "--to", "ready_for_review")
        current = reread(current, root)
    _lifecycle(root, "approve", str(current.path), "--by-policy")
    return reread(current, root)


def snapshot_hash(package: StandingPackage, root: Path) -> None:
    """Re-pin the authoring repository's own hash fixture for this package.

    A revision moves the package hash by design, and that repository asserts every package's
    hash in a test. Leaving it stale would red its gate on a change this program made, so the
    fixture moves in the same commit -- which is exactly what a person doing this by hand does.
    """
    digest = _lifecycle(root, "hash", str(package.path)).strip()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise StandingError(f"{package.package_id}: the package hash was unreadable")
    fixture = root / HASH_FIXTURE
    try:
        data: dict[str, Any] = json.loads(fixture.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StandingError(f"the package hash fixture is unreadable: {error}") from None
    data[package.package_id] = digest
    fixture.write_text(json.dumps(dict(sorted(data.items())), indent=2) + "\n", encoding="utf-8")


def commit(package: StandingPackage, bump: Bump, root: Path) -> str:
    """Commit the revision and the fixture, and publish them to the default branch (ADR-0033).

    **PUBLISHING IS THE SAME ACT FINISHED, NOT A NEW ONE.** The commit this writes was always
    destined for that branch; the only question ADR-0033 settled is who performs the last step
    and when, and the answer had been "nobody, for an unbounded time" since this producer was
    scheduled. The module docstring carries the reasoning.

    **A REFUSED PUBLISH NAMES THE COMMIT IT STRANDED.** It raises like every other refusal here,
    so the pass reports it as a finding -- but the commit exists by then, and the next pass
    replays past the step that made it, so the sha is the one thing a reader needs and the one
    thing `git` will not say afterwards. `require_published` is what stops that finding going
    quiet on the following pass.
    """
    paths = [
        str((package.path / "package.yaml").relative_to(root)),
        str((package.path / "lineage.yaml").relative_to(root)),
        str(HASH_FIXTURE),
    ]
    _run(root, ["git", "add", *paths])
    message = (
        f"{package.package_id} rev {package.revision}: "
        f"{package.dependency} {bump.from_version} to {bump.to_version}\n\n"
        "Written by bump-proposer (ADR-0028). The auto-merge cascade refuses this bump\n"
        "(ADR-0016), so it becomes factory work: the standing package's revision carries\n"
        "it, and the revision is approved by conformance to approval-policy.toml rather\n"
        "than by a named human. A person still approves the work record this producer\n"
        "writes in change-manager, and the decomposition and the authority envelope after\n"
        "that.\n"
    )
    _run(root, ["git", "commit", "-m", message])
    head = _run(root, ["git", "rev-parse", "HEAD"]).strip()
    try:
        _run(root, PUBLISH_COMMAND.split())
    except StandingError as error:
        raise StandingError(
            f"{package.package_id} rev {package.revision} is committed as {head[:12]} "
            f"and unpublished: {error}"
        ) from None
    return head
