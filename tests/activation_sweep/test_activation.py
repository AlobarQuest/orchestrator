"""The three facts, measured against a REAL uv project rather than a model of one.

The sibling suite builds real git repositories for the same reason: a fake runner would let these
tests agree with a model of `uv` instead of with `uv`, and this estate has already paid for that
once. The project here is dependency-free and takes about a second to build, so the cost of being
honest is small.

TWO SUBJECTS, DELIBERATELY. The real project is the right subject for what the FLAG means, and it
needs a uv that has the flag — this repository's CI pins one that does not, so those tests are
skipped there by a named condition rather than silently passing. A stub binary is the right
subject for what THIS MODULE promises, which is a mapping from uv's exit status to an answer, and
that runs everywhere.

EVERY FACT HAS A NEGATIVE CONTROL, because a measurement that has only ever answered `yes` is not
known to be able to answer anything else.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from activation_sweep.activation import (
    NO,
    NOT_APPLICABLE,
    YES,
    ActivationError,
    ActivationFacts,
    RepositoryFacts,
    console_entry_points_present,
    environment_matches_lock,
    repository_facts,
)


# `uv sync --check` DOES NOT EXIST BEFORE uv 0.9, and this repository's CI pins **0.5.31**
# (`quality.yml`, `astral-sh/setup-uv`). Measured 2026-08-25: `uvx uv@0.5.31 sync --help` lists
# `--frozen` and `--locked` and no `--check`, so there the flag is an unknown argument and uv
# exits 2 — which this module correctly reports as unmeasurable rather than as an answer.
#
# The lane runs on the operator machine, where uv is current, so that is where the real-tool
# tests below belong. The EXIT-CODE CONTRACT is tested everywhere instead, against a stub binary:
# what this module promises is a mapping from uv's status to an answer, and a stub is the right
# subject for that promise where a real project is the right subject for the flag's semantics.
def _uv_supports_check() -> bool:
    found = shutil.which("uv")
    if found is None:
        return False
    help_text = subprocess.run(
        [found, "sync", "--help"], capture_output=True, text=True, check=False
    ).stdout
    return "--check" in help_text


needs_modern_uv = pytest.mark.skipif(
    not _uv_supports_check(),
    reason="this uv has no `sync --check`; CI pins 0.5.31, which predates the flag",
)

PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[project.scripts]
demo-cli = "demo:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""

ENTRY_POINT = "demo-cli"


def _uv(path: Path, *args: str) -> None:
    subprocess.run(["uv", *args], cwd=str(path), check=True, capture_output=True)


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real, synchronized uv project: one declared console entry point and no dependencies."""
    path = tmp_path_factory.mktemp("uv-project")
    (path / "pyproject.toml").write_text(PYPROJECT)
    (path / "src" / "demo").mkdir(parents=True)
    (path / "src" / "demo" / "__init__.py").write_text("def main() -> None:\n    pass\n")
    _uv(path, "lock")
    _uv(path, "sync", "--frozen")
    return path


@needs_modern_uv
def test_a_synchronized_project_answers_yes_to_both_repository_facts(project: Path) -> None:
    facts = repository_facts(project)

    assert facts == RepositoryFacts(
        console_entry_points_present=YES,
        environment_matches_lock=YES,
    )


@needs_modern_uv
def test_a_missing_console_entry_point_answers_no(project: Path) -> None:
    """The negative control for the fact with a recorded failure mode: the launchers invoke by
    absolute path, and a `git pull` alone does not install a new `[project.scripts]` entry."""
    installed = project / ".venv" / "bin" / ENTRY_POINT
    installed.rename(installed.with_suffix(".moved"))
    try:
        assert console_entry_points_present(project) == NO
    finally:
        installed.with_suffix(".moved").rename(installed)
    assert console_entry_points_present(project) == YES


@needs_modern_uv
def test_an_environment_that_does_not_match_the_lock_answers_no(project: Path) -> None:
    """The negative control for the second fact, measured by really desynchronizing the
    environment and really resynchronizing it -- `uv` answers, this module only reads its
    status."""
    _uv(project, "venv", "--clear")
    try:
        assert environment_matches_lock(project) == NO
    finally:
        _uv(project, "sync", "--frozen")
    assert environment_matches_lock(project) == YES


def test_a_repository_with_no_python_manifest_answers_not_applicable(tmp_path: Path) -> None:
    """`infraops-mcp-server` is a TypeScript project with no `pyproject.toml`, no lockfile and no
    virtual environment, so two of the three questions genuinely do not apply to it. Answering
    `yes` there would be a lie and `no` would be worse."""
    assert console_entry_points_present(tmp_path) == NOT_APPLICABLE
    assert environment_matches_lock(tmp_path) == NOT_APPLICABLE


def test_a_manifest_declaring_no_entry_points_answers_not_applicable(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')

    assert console_entry_points_present(tmp_path) == NOT_APPLICABLE


def test_a_lockfile_with_no_manifest_beside_it_answers_not_applicable(tmp_path: Path) -> None:
    """Both files are required, and a lockfile alone is not a project `uv` can be asked about."""
    (tmp_path / "uv.lock").write_text("version = 1\n")

    assert environment_matches_lock(tmp_path) == NOT_APPLICABLE


def test_an_unreadable_manifest_is_unmeasurable_rather_than_a_no(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("this is not toml [[[")

    with pytest.raises(ActivationError):
        console_entry_points_present(tmp_path)


@needs_modern_uv
def test_uv_being_absent_is_unmeasurable_rather_than_a_no(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The answer is MISSING, never negative. Under `launchd` the job inherits only the PATH its
    plist names, so this is the state a scheduled pass reaches when the fallback is wrong too."""
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("activation_sweep.activation.UV_FALLBACK", Path("/nonexistent/uv"))

    with pytest.raises(ActivationError):
        environment_matches_lock(project)


def test_facts_are_assembled_from_the_shared_pair_plus_the_units_own_commit() -> None:
    facts = ActivationFacts.of(
        RepositoryFacts(console_entry_points_present=YES, environment_matches_lock=YES),
        merge_commit_present=NO,
    )

    assert facts.summary["merge_commit_present"] == NO
    assert facts.unsatisfied == ("merge_commit_present",)
    assert facts.recordable is False


def test_not_applicable_does_not_make_an_activation_unrecordable() -> None:
    """The whole reason the members are tri-state: a repository with no Python toolchain is
    activated by pulling, and nothing about that is unsatisfied."""
    facts = ActivationFacts.of(
        RepositoryFacts(
            console_entry_points_present=NOT_APPLICABLE,
            environment_matches_lock=NOT_APPLICABLE,
        ),
        merge_commit_present=YES,
    )

    assert facts.unsatisfied == ()
    assert facts.recordable is True


# ---------------------------------------------------------------------------
# The exit-code contract, against a stub binary rather than a real project. What this module
# promises is a mapping from uv's status to an answer, and that promise is testable anywhere --
# including on a runner whose uv predates the flag entirely.
# ---------------------------------------------------------------------------


def _stub_uv(path: Path, status: int) -> None:
    """A binary named `uv` that exits with `status` and writes a plausible diagnostic."""
    binary = path / "uv"
    binary.write_text(f'#!/bin/sh\necho "stub uv" >&2\nexit {status}\n')
    binary.chmod(0o755)


def _project_with(path: Path) -> Path:
    (path / "pyproject.toml").write_text(PYPROJECT)
    (path / "uv.lock").write_text("version = 1\n")
    return path


@pytest.mark.parametrize(
    ("status", "expected"),
    [(0, YES), (1, NO)],
)
def test_uvs_status_maps_to_an_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, expected: str
) -> None:
    """Exit 0 is synchronized and exit 1 is not. Both, so neither is the default."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    _stub_uv(stub_dir, status)
    monkeypatch.setenv("PATH", str(stub_dir))

    assert environment_matches_lock(_project_with(tmp_path)) == expected


def test_any_other_status_is_unmeasurable_rather_than_an_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 2 is uv FAILING rather than answering — which is what a uv predating `--check` does,
    since the flag is then an unknown argument. It must not read as either result."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    _stub_uv(stub_dir, 2)
    monkeypatch.setenv("PATH", str(stub_dir))

    with pytest.raises(ActivationError) as raised:
        environment_matches_lock(_project_with(tmp_path))

    # The status only. uv's stderr can name an index URL and its credentials.
    assert "exited 2" in str(raised.value)
    assert "stub uv" not in str(raised.value)
