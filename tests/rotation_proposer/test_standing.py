"""The standing rotation package: discovery, the one line written, the ladder, and publishing.

The lifecycle commands are intent-packages' own and are faked here, as `bump_proposer`'s tests fake
them. Publishing is NOT faked: it runs real `git` against a temporary bare origin, so "published"
is asserted by reading the origin rather than by counting calls.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bump_proposer import standing as shared
from bump_proposer.standing import StandingError
from rotation_proposer import standing
from rotation_proposer.standing import commit, discover, to_review, write_occurrence

SHELL = """\
schema_version: 1
package_id: {package_id}
title: t
revision: {revision}
status: {status}
owner: devon
profile: {profile}
profile_fields:
  owner: devon
  operating_procedure: rotate it
  standing: {standing}
  credential_id: {credential_id}
  occurrence: {occurrence}
"""


def write_package(
    root: Path,
    credential_id: str = "openrouter-generic",
    *,
    package_id: str | None = None,
    revision: int = 1,
    status: str = "approved",
    profile: str = "non-software-operational",
    standing_flag: str = "true",
    occurrence: str = "'unassigned'",
) -> Path:
    directory = root / "packages" / (package_id or f"rotation-{credential_id}")
    directory.mkdir(parents=True)
    (directory / "package.yaml").write_text(
        SHELL.format(
            package_id=directory.name,
            revision=revision,
            status=status,
            profile=profile,
            standing=standing_flag,
            credential_id=credential_id,
            occurrence=occurrence,
        ),
        encoding="utf-8",
    )
    (directory / "lineage.yaml").write_text("current_state: x\n", encoding="utf-8")
    return directory


class FakeLifecycle:
    """intent-packages' `revise` and `transition`, as file edits, recording every call."""

    def __init__(self, root: Path):
        self.root = root
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, root: Path, *args: str) -> str:
        self.calls.append(args)
        if args[0] == "hash":
            return "a" * 64 + "\n"
        path = Path(args[1]) / "package.yaml"
        text = path.read_text()
        status = text.split("status: ")[1].split("\n")[0]
        if args[0] == "revise":
            revision = int(text.split("revision: ")[1].split("\n")[0])
            text = text.replace(f"revision: {revision}", f"revision: {revision + 1}")
            text = text.replace(f"status: {status}", "status: draft")
        elif args[0] == "transition":
            text = text.replace(f"status: {status}", f"status: {args[3]}")
        else:
            raise AssertionError(f"this program ran a lifecycle command it must not: {args}")
        path.write_text(text)
        return ""


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    fake = FakeLifecycle(tmp_path)
    monkeypatch.setattr(shared, "lifecycle", fake)
    return fake


# --- discovery ---------------------------------------------------------------------------------


def test_a_standing_rotation_package_is_keyed_on_its_credential(tmp_path) -> None:
    write_package(tmp_path, occurrence="'age-2025-01-02'")
    found = discover(tmp_path)
    assert list(found) == ["openrouter-generic"]
    package = found["openrouter-generic"]
    assert (package.package_id, package.revision, package.state) == (
        "rotation-openrouter-generic",
        1,
        "approved",
    )
    assert package.occurrence == "age-2025-01-02"


def test_a_package_not_declared_standing_is_invisible(tmp_path) -> None:
    """Kills: dropping the `standing` filter. WS-P2.13's finished rotation shares this profile."""
    write_package(tmp_path, standing_flag="false")
    assert discover(tmp_path) == {}


def test_a_package_of_another_profile_is_invisible(tmp_path) -> None:
    """Kills: dropping the profile filter, which would read a dependency-update package's
    unrelated keys as a rotation package's."""
    write_package(tmp_path, profile="dependency-update")
    assert discover(tmp_path) == {}


def test_a_package_named_for_another_credential_is_refused(tmp_path) -> None:
    write_package(tmp_path, "openai-project", package_id="rotation-openrouter-generic")
    with pytest.raises(StandingError, match="stands for openai-project"):
        discover(tmp_path)


def test_a_checkout_without_packages_is_refused(tmp_path) -> None:
    with pytest.raises(StandingError, match="no packages directory"):
        discover(tmp_path)


# --- the one line written ----------------------------------------------------------------------


def test_the_occurrence_is_written_quoted_and_nothing_else_moves(tmp_path) -> None:
    """Kills: writing it bare. `2026-10-07` unquoted loads as a YAML date and fails the profile's
    `str`, at `transition` time, after the file has been edited."""
    write_package(tmp_path)
    package = discover(tmp_path)["openrouter-generic"]
    before = (package.path / "package.yaml").read_text()
    write_occurrence(package, "requested-2026-10-07")
    after = (package.path / "package.yaml").read_text()
    assert "  occurrence: 'requested-2026-10-07'\n" in after
    assert before.replace("'unassigned'", "'requested-2026-10-07'") == after


def test_a_package_without_exactly_one_occurrence_line_is_refused(tmp_path) -> None:
    directory = write_package(tmp_path)
    package = discover(tmp_path)["openrouter-generic"]
    path = directory / "package.yaml"
    path.write_text(path.read_text().replace("  occurrence: 'unassigned'\n", ""))
    with pytest.raises(StandingError, match="expected one occurrence line, found 0"):
        write_occurrence(package, "age-2025-01-02")


# --- the ladder --------------------------------------------------------------------------------


def test_an_approved_tip_is_revised_written_and_taken_to_review_and_no_further(
    tmp_path, lifecycle
) -> None:
    """Kills: approving (the fake refuses any command but revise and transition), skipping the
    revise (it would rewrite an APPROVED revision in place), and skipping the transition."""
    write_package(tmp_path, occurrence="'age-2025-01-02'")
    package = discover(tmp_path)["openrouter-generic"]
    result = to_review(package, "requested-2026-10-07", tmp_path)
    assert [call[0] for call in lifecycle.calls] == ["revise", "transition"]
    assert lifecycle.calls[1][2:] == ("--to", "ready_for_review")
    assert (result.revision, result.state, result.occurrence) == (
        2,
        "ready_for_review",
        "requested-2026-10-07",
    )


def test_a_draft_is_written_over_without_spending_a_revision(tmp_path, lifecycle) -> None:
    write_package(tmp_path, status="draft")
    package = discover(tmp_path)["openrouter-generic"]
    result = to_review(package, "age-2025-01-02", tmp_path)
    assert [call[0] for call in lifecycle.calls] == ["transition"]
    assert (result.revision, result.occurrence) == (1, "age-2025-01-02")


def test_a_draft_already_carrying_the_occurrence_is_only_transitioned(tmp_path, lifecycle) -> None:
    """The crash between the write and the transition: resumed, not revised again."""
    write_package(tmp_path, status="draft", occurrence="'age-2025-01-02'")
    package = discover(tmp_path)["openrouter-generic"]
    to_review(package, "age-2025-01-02", tmp_path)
    assert [call[0] for call in lifecycle.calls] == ["transition"]


def test_no_call_in_the_package_passes_the_approve_command() -> None:
    """The structural half of "it never approves": no call anywhere in this program is handed the
    lifecycle's approve verb or the policy flag, so no path through it can send one. (Docstrings
    name both, which is why this reads call arguments rather than every string.)"""
    import ast

    calls = 0
    for path in Path("src/rotation_proposer").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            calls += 1
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    assert argument.value not in {"approve", "--by-policy"}, path
    assert calls > 50  # the scan saw the program


# --- publishing, against a real origin ---------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def published_checkout(tmp_path):
    """A clone of a bare origin, on `main`, level with it, holding one rotation package."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(origin), str(checkout))
    _git(checkout, "checkout", "-q", "-b", "main")
    write_package(checkout)
    fixture = checkout / "tests" / "fixtures" / "package_hashes.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("{}\n")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "init")
    _git(checkout, "push", "-q", "origin", "main")
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
        _git(checkout, "config", key, value)
    return origin, checkout


def test_a_revision_reaches_the_origin_and_the_checkout_stays_publishable(
    published_checkout, lifecycle
) -> None:
    origin, checkout = published_checkout
    shared.require_clean(checkout)
    shared.require_publishable(checkout)
    package = to_review(discover(checkout)["openrouter-generic"], "requested-2026-10-07", checkout)
    shared.snapshot_hash(package, checkout)
    sha = commit(package, checkout)

    assert _git(origin, "rev-parse", "main").strip() == sha
    shown = _git(origin, "show", f"{sha}:packages/rotation-openrouter-generic/package.yaml")
    assert "occurrence: 'requested-2026-10-07'" in shown
    assert "status: ready_for_review" in shown
    message = _git(origin, "log", "-1", "--format=%B", sha)
    assert message.startswith("rotation-openrouter-generic rev 2: rotate for requested-2026-10-07")
    assert "NOT approved" in message
    # Nothing left behind: the next pass may start here.
    shared.require_clean(checkout)
    shared.require_publishable(checkout)


def test_a_refused_publish_names_the_commit_it_stranded(published_checkout, lifecycle) -> None:
    """The origin moved under the checkout: the push is refused, and the message carries the sha
    -- the one thing git will not say afterwards -- and the next pass refuses to build on it."""
    origin, checkout = published_checkout
    other = origin.parent / "other"
    _git(origin.parent, "clone", "-q", str(origin), str(other))
    (other / "README").write_text("x\n")
    _git(other, "add", "README")
    _git(other, "commit", "-q", "-m", "elsewhere")
    _git(other, "push", "-q", "origin", "main")

    package = to_review(discover(checkout)["openrouter-generic"], "age-2025-01-02", checkout)
    shared.snapshot_hash(package, checkout)
    with pytest.raises(StandingError, match="is committed as [0-9a-f]{12} and unpublished"):
        commit(package, checkout)
    with pytest.raises(StandingError, match="carries 1 commit"):
        shared.require_publishable(checkout)


def test_the_push_literal_lives_only_in_the_shared_module() -> None:
    """The merge guard scans file TEXT, and its exemption names `bump_proposer/standing.py`. A copy
    of the push here would need an exemption of its own -- and the rot check would notice the
    shared one going unused if publishing ever moved out of it."""
    for path in Path("src/rotation_proposer").rglob("*.py"):
        assert shared.PUBLISH_COMMAND not in path.read_text(), path
    assert standing.shared is shared
