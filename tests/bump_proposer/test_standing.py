"""The standing package half: discovery, the two lines this producer writes, and the ladder.

The lifecycle commands themselves are not re-implemented here and must not be: they live in
another repository behind an audited chain. What is tested is everything around them -- which
package a bump belongs to, what is written into it, and in what order the ladder is climbed,
including from every point a crashed pass could have stopped at.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bump_proposer import standing
from bump_proposer.standing import (
    StandingError,
    StandingPackage,
    advance,
    discover,
    write_versions,
)
from landing_ledger.titles import Bump

BUMP = Bump(from_version="3.25.76", to_version="4.4.3", kind="semver-major")

SHELL = """\
schema_version: 1
package_id: {package_id}
title: t
revision: {revision}
status: {status}
profile: dependency-update
profile_fields:
  target_repo: AlobarQuest/infraops-mcp-server
  package: {dependency}
  from_version: {from_version}
  to_version: {to_version}
  standing: {standing}
"""


def _write(
    root,
    package_id,
    *,
    dependency="zod",
    revision=1,
    status="draft",
    from_version="unassigned",
    to_version="unassigned",
    standing_flag="true",
):
    directory = root / "packages" / package_id
    directory.mkdir(parents=True)
    (directory / "package.yaml").write_text(
        SHELL.format(
            package_id=package_id,
            revision=revision,
            status=status,
            dependency=dependency,
            from_version=from_version,
            to_version=to_version,
            standing=standing_flag,
        ),
        encoding="utf-8",
    )
    return directory


def test_discovery_keys_a_package_on_its_repository_and_dependency(tmp_path) -> None:
    _write(tmp_path, "infraops-mcp-server-npm-zod")
    found = discover(tmp_path)
    assert set(found) == {("alobarquest/infraops-mcp-server", "zod")}


def test_a_package_not_declared_standing_is_invisible(tmp_path) -> None:
    """Kills: dropping the `standing` filter in `discover`.

    THE HISTORICAL POPULATION IS THE SUBJECT. Every dependency-update package in the authoring
    repository declares this same profile and a target repository, and eight of them name one
    finished bump each. Without this filter the producer matches one of those and revises it.
    """
    _write(
        tmp_path,
        "orchestrator-httpx2-bump",
        dependency="httpx2",
        status="approved",
        from_version="2.7.0",
        to_version="2.9.1",
        standing_flag="false",
    )
    assert discover(tmp_path) == {}


def test_a_package_with_no_standing_key_at_all_is_invisible(tmp_path) -> None:
    directory = _write(tmp_path, "old-package", standing_flag="true")
    path = directory / "package.yaml"
    path.write_text(path.read_text().replace("  standing: true\n", ""), encoding="utf-8")
    assert discover(tmp_path) == {}


def test_quoted_versions_are_read_without_their_quotes(tmp_path) -> None:
    """Kills: `_yaml_scalar` returning the quotes.

    `1.13` is a YAML FLOAT and `7` is an INT, so every version that could be read as a number
    is written quoted. A reader keeping the quotes compares `'1.13'` against `1.13`, concludes
    the package carries a different bump on every pass, and revises it forever.
    """
    _write(tmp_path, "p", from_version="'1.13'", to_version="'1.18.5'")
    package = discover(tmp_path)[("alobarquest/infraops-mcp-server", "zod")]
    assert (package.from_version, package.to_version) == ("1.13", "1.18.5")
    assert package.carries(Bump(from_version="1.13", to_version="1.18.5", kind="semver-minor"))


def test_two_packages_covering_one_bump_is_refused(tmp_path) -> None:
    _write(tmp_path, "a")
    _write(tmp_path, "b")
    with pytest.raises(StandingError, match="two standing packages cover"):
        discover(tmp_path)


def test_the_written_versions_are_quoted(tmp_path) -> None:
    """Kills: writing them bare.

    An unquoted `to_version: 7` is an int and an unquoted `from_version: 1.13` is a float, and
    the package schema requires strings -- so the failure is the authoring repository's own
    validator refusing a revision this producer has already edited into place.
    """
    _write(tmp_path, "p")
    package = discover(tmp_path)[("alobarquest/infraops-mcp-server", "zod")]
    write_versions(package, Bump(from_version="1.13", to_version="7", kind="semver-major"))
    text = (package.path / "package.yaml").read_text()
    assert "  from_version: '1.13'" in text
    assert "  to_version: '7'" in text


def test_writing_a_document_with_no_version_lines_is_refused(tmp_path) -> None:
    """Kills: dropping the substitution count check. A silent no-op here approves a revision
    still carrying the previous bump."""
    _write(tmp_path, "p")
    package = discover(tmp_path)[("alobarquest/infraops-mcp-server", "zod")]
    (package.path / "package.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    with pytest.raises(StandingError, match="expected one from_version"):
        write_versions(package, BUMP)


# --- the ladder -------------------------------------------------------------------


class _Ladder:
    """Records the lifecycle commands and moves the on-disk state the way they would."""

    def __init__(self, tmp_path, package):
        self.calls: list[tuple[str, ...]] = []
        self.tmp_path = tmp_path
        self.package = package

    def lifecycle(self, root, *args):
        self.calls.append(args)
        verb = args[0]
        path = self.package.path / "package.yaml"
        text = path.read_text()
        if verb == "revise":
            revision = int(text.split("revision: ")[1].split("\n")[0])
            text = text.replace(f"revision: {revision}", f"revision: {revision + 1}")
            text = text.replace(f"status: {self._status(text)}", "status: draft")
        elif verb == "transition":
            text = text.replace(f"status: {self._status(text)}", "status: ready_for_review")
        elif verb == "approve":
            text = text.replace(f"status: {self._status(text)}", "status: approved")
        path.write_text(text, encoding="utf-8")
        return ""

    @staticmethod
    def _status(text: str) -> str:
        return text.split("status: ")[1].split("\n")[0]


@pytest.fixture
def ladder(tmp_path, monkeypatch):
    _write(tmp_path, "p")
    package = discover(tmp_path)[("alobarquest/infraops-mcp-server", "zod")]
    rig = _Ladder(tmp_path, package)
    monkeypatch.setattr(standing, "_lifecycle", rig.lifecycle)
    monkeypatch.setattr(
        standing,
        "reread",
        lambda pkg, root=None: discover(rig.tmp_path)[
            (pkg.target_repository.lower(), pkg.dependency)
        ],
    )
    return rig


def _verbs(rig):
    return [call[0] for call in rig.calls]


def test_a_fresh_shell_is_filled_transitioned_and_approved_at_revision_one(ladder) -> None:
    """Kills: revising a draft revision.

    A draft revision has never been approved, so its number is still free. Spending a new one
    would leave a gap in the ledger for no reason.
    """
    final = advance(ladder.package, BUMP, ladder.tmp_path)
    assert _verbs(ladder) == ["transition", "approve"]
    assert final.revision == 1 and final.approved and final.carries(BUMP)


def test_an_approved_package_carrying_another_bump_is_revised(tmp_path, monkeypatch) -> None:
    """Kills: skipping `revise` for an approved tip -- which would edit an approved revision
    under its own hash, the exact drift `revise` exists to resolve."""
    _write(tmp_path, "p", status="approved", from_version="'1.0.0'", to_version="'2.0.0'")
    package = discover(tmp_path)[("alobarquest/infraops-mcp-server", "zod")]
    rig = _Ladder(tmp_path, package)
    monkeypatch.setattr(standing, "_lifecycle", rig.lifecycle)
    monkeypatch.setattr(
        standing,
        "reread",
        lambda pkg, root=None: discover(tmp_path)[(pkg.target_repository.lower(), pkg.dependency)],
    )
    final = advance(package, BUMP, tmp_path)
    assert _verbs(rig) == ["revise", "transition", "approve"]
    assert final.revision == 2 and final.approved and final.carries(BUMP)


def test_a_pass_that_crashed_after_transition_only_approves(tmp_path, monkeypatch) -> None:
    """Resumability, and it is not decoration: every step here is a subprocess against another
    repository's ledger, so a pass can stop between any two of them."""
    _write(tmp_path, "p", status="ready_for_review", from_version="'3.25.76'", to_version="'4.4.3'")
    package = discover(tmp_path)[("alobarquest/infraops-mcp-server", "zod")]
    rig = _Ladder(tmp_path, package)
    monkeypatch.setattr(standing, "_lifecycle", rig.lifecycle)
    monkeypatch.setattr(
        standing,
        "reread",
        lambda pkg, root=None: discover(tmp_path)[(pkg.target_repository.lower(), pkg.dependency)],
    )
    final = advance(package, BUMP, tmp_path)
    assert _verbs(rig) == ["approve"]
    assert final.revision == 1


def test_the_transition_is_never_skipped(ladder) -> None:
    """Kills: dropping the transition. It is what re-snapshots the revision hash, so without it
    the approval is bound to a hash the edited document no longer has."""
    advance(ladder.package, BUMP, ladder.tmp_path)
    assert "transition" in _verbs(ladder)
    assert _verbs(ladder).index("transition") < _verbs(ladder).index("approve")


def test_the_approval_is_always_by_policy(ladder) -> None:
    advance(ladder.package, BUMP, ladder.tmp_path)
    approve = next(call for call in ladder.calls if call[0] == "approve")
    assert "--by-policy" in approve


# --- the hash fixture -------------------------------------------------------------


def test_the_hash_fixture_moves_with_the_revision(tmp_path, monkeypatch) -> None:
    """Kills: skipping the snapshot. A revision moves the package hash by design, and the
    authoring repository asserts every package's hash in a test -- so a pass that revised
    without re-pinning reds that repository's gate on a change this program made."""
    _write(tmp_path, "p")
    fixture = tmp_path / "tests" / "fixtures"
    fixture.mkdir(parents=True)
    (fixture / "package_hashes.json").write_text(json.dumps({"other": "b" * 64}) + "\n")
    package = discover(tmp_path)[("alobarquest/infraops-mcp-server", "zod")]
    monkeypatch.setattr(standing, "_lifecycle", lambda root, *args: "a" * 64 + "\n")

    standing.snapshot_hash(package, tmp_path)

    data = json.loads((fixture / "package_hashes.json").read_text())
    assert data == {"other": "b" * 64, "p": "a" * 64}


def test_an_unreadable_hash_is_refused(tmp_path, monkeypatch) -> None:
    _write(tmp_path, "p")
    package = discover(tmp_path)[("alobarquest/infraops-mcp-server", "zod")]
    monkeypatch.setattr(standing, "_lifecycle", lambda root, *args: "not-a-hash\n")
    with pytest.raises(StandingError, match="package hash was unreadable"):
        standing.snapshot_hash(package, tmp_path)


def test_a_dirty_checkout_is_refused(tmp_path, monkeypatch) -> None:
    """Kills: accepting a dirty tree. This program commits what it writes, and a tree carrying
    somebody else's work-in-progress would have it swept into a commit whose message says it is
    a package revision."""
    monkeypatch.setattr(standing, "_run", lambda root, command: " M some/file\n")
    with pytest.raises(StandingError, match="uncommitted changes"):
        standing.require_clean(tmp_path)


def test_a_clean_checkout_is_accepted(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(standing, "_run", lambda root, command: "\n")
    standing.require_clean(tmp_path)


def test_the_interpreter_comes_from_the_checkout(tmp_path) -> None:
    """Which copy of the lifecycle code runs decides what is written into that checkout's
    ledger, so an absent one is a named refusal rather than whatever is on PATH."""
    with pytest.raises(StandingError, match="no interpreter at"):
        standing._lifecycle(tmp_path, "hash", "packages/p")


def test_a_standing_package_reports_what_it_carries() -> None:
    package = StandingPackage(
        package_id="p",
        path=Path("p"),
        target_repository="r",
        dependency="zod",
        revision=2,
        state="approved",
        from_version="3.25.76",
        to_version="4.4.3",
    )
    assert package.carries(BUMP) and package.approved
    assert not package.carries(
        Bump(from_version="3.25.76", to_version="4.5.0", kind="semver-major")
    )


# --- publishing (ADR-0033) --------------------------------------------------------


def _package(tmp_path):
    _write(tmp_path, "p")
    return discover(tmp_path)[("alobarquest/infraops-mcp-server", "zod")]


def test_the_revision_is_published_once_it_is_committed(tmp_path, monkeypatch) -> None:
    """Kills: committing and stopping, which is what this did before ADR-0033.

    ORDERED, not merely counted. Publishing before the commit would push the branch as it
    already stood and leave the revision behind, while the pass reported a sha for it.
    """
    package = _package(tmp_path)
    ran: list[tuple[str, ...]] = []

    def fake_run(root, command):
        ran.append(tuple(command))
        return "d" * 40 + "\n" if command[1] == "rev-parse" else ""

    monkeypatch.setattr(standing, "_run", fake_run)

    assert standing.commit(package, BUMP, tmp_path) == "d" * 40
    assert [call[1] for call in ran] == ["add", "commit", "rev-parse", "push"]
    assert ran[-1] == ("git", "push", "origin", "main")


def test_a_refused_publish_names_the_commit_it_stranded(tmp_path, monkeypatch) -> None:
    """Kills: swallowing a refused publish, and reporting one without its sha.

    The commit exists by the time the publish is refused, and the next pass replays past the
    step that made it -- so this message is the only place that sha is ever said.
    """
    package = _package(tmp_path)

    def fake_run(root, command):
        if command[1] == "push":
            raise StandingError("push origin main refused: non-fast-forward")
        return "d" * 40 + "\n" if command[1] == "rev-parse" else ""

    monkeypatch.setattr(standing, "_run", fake_run)

    with pytest.raises(StandingError, match="dddddddddddd and unpublished"):
        standing.commit(package, BUMP, tmp_path)


def test_a_checkout_carrying_an_unpublished_commit_is_refused(tmp_path, monkeypatch) -> None:
    """Kills: writing a further revision on top of one that could not be published."""
    monkeypatch.setattr(standing, "_run", lambda root, command: "1\n")
    with pytest.raises(StandingError, match="carries 1 commit"):
        standing.require_published(tmp_path)


def test_a_checkout_level_with_origin_is_accepted(tmp_path, monkeypatch) -> None:
    """The other direction: a guard that refused every checkout would stop the lane dead."""
    monkeypatch.setattr(standing, "_run", lambda root, command: "0\n")
    standing.require_published(tmp_path)


def test_an_unreadable_distance_from_origin_is_refused(tmp_path, monkeypatch) -> None:
    """Fail-closed. A count that cannot be read is not a count of zero, and reading it as one
    would wave through exactly the residue this refusal exists for."""
    monkeypatch.setattr(standing, "_run", lambda root, command: "\n")
    with pytest.raises(StandingError, match="how far ahead"):
        standing.require_published(tmp_path)


def test_the_distance_is_read_from_the_tracking_ref_without_a_fetch(tmp_path, monkeypatch) -> None:
    """Kills: fetching first, or asking a different question.

    A successful push advances `origin/main` and a refused one does not, so the answer is
    already on disk. Going to the network would make this program's refusal depend on somebody
    else's landings rather than on its own unfinished act.
    """
    seen: list[tuple[str, ...]] = []

    def fake_run(root, command):
        seen.append(tuple(command))
        return "0\n"

    monkeypatch.setattr(standing, "_run", fake_run)
    standing.require_published(tmp_path)

    assert seen == [("git", "rev-list", "--count", "origin/main..HEAD")]
