"""Every `scripts/install-*-launchd.sh` refuses to run from a linked worktree, before it writes.

An installer writes its own REPO_ROOT into the plist verbatim, so installing from a build worktree
pins the LaunchAgent to a path that is deleted at teardown, after which the job fails on every run
with nothing reporting it. Until 2026-09-27 six installers carried the refusal inline and five did
not -- two of those five with a header comment claiming it. The refusal now lives once, in
`scripts/sds-install.sh`, and this module holds every installer to calling it in time.

The behavioural cases never run a real installer from a real tree: each copies one installer and
the shared helper into a throwaway repository, puts a `launchctl` stub on PATH that only records
it was called, and points HOME at a temporary directory, so nothing can reach this machine's
LaunchAgents.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path("scripts")
HELPER = SCRIPTS / "sds-install.sh"
SOURCE_LINE = 'source "$REPO_ROOT/scripts/sds-install.sh"'
CALL_LINE = 'sds_refuse_linked_worktree "$REPO_ROOT"'
# The acts an installer performs once it has decided to proceed. The refusal must precede all.
WRITES = re.compile(r"^\s*(mkdir|sed|launchctl|plutil)\b")

_LAUNCHCTL_STUB = """#!/bin/sh
echo "launchctl $*" >> "$LAUNCHCTL_LOG"
"""


def installers() -> list[Path]:
    """Found by glob, so a new installer is held to this without editing the module."""
    return sorted(SCRIPTS.glob("install-*-launchd.sh"))


def test_the_installer_population_is_what_was_counted() -> None:
    assert len(installers()) >= 11


@pytest.mark.parametrize("installer", installers(), ids=lambda p: p.name)
def test_every_installer_refuses_a_linked_worktree_before_it_writes(installer: Path) -> None:
    lines = installer.read_text().splitlines()
    assert SOURCE_LINE in lines, f"{installer} does not source scripts/sds-install.sh"
    assert CALL_LINE in lines, f"{installer} never calls sds_refuse_linked_worktree"
    call = lines.index(CALL_LINE)
    assert lines.index(SOURCE_LINE) < call
    root = next(i for i, line in enumerate(lines) if line.startswith("REPO_ROOT="))
    assert root < call
    first_write = next(i for i, line in enumerate(lines) if WRITES.match(line))
    assert call < first_write, f"{installer} writes at line {first_write + 1} before refusing"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def trees(tmp_path: Path) -> tuple[Path, Path]:
    """A throwaway main tree and a linked worktree of it."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    (main / "README").write_text("x\n")
    _git(main, "add", "README")
    _git(main, "commit", "-q", "-m", "init")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", str(linked), "-b", "side")
    return main, linked


def _run_installer(installer: Path, root: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    (root / "scripts").mkdir(exist_ok=True)
    shutil.copy(installer, root / "scripts" / installer.name)
    shutil.copy(HELPER, root / "scripts" / HELPER.name)
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    stub = stubs / "launchctl"
    stub.write_text(_LAUNCHCTL_STUB)
    stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(
        ["/bin/bash", str(root / "scripts" / installer.name)],
        env={
            "PATH": f"{stubs}:/usr/bin:/bin",
            "HOME": str(home),
            "LAUNCHCTL_LOG": str(tmp_path / "launchctl.log"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("installer", installers(), ids=lambda p: p.name)
def test_every_installer_run_from_a_linked_worktree_refuses_and_touches_nothing(
    installer: Path, trees: tuple[Path, Path], tmp_path: Path
) -> None:
    _main, linked = trees
    result = _run_installer(installer, linked, tmp_path)
    assert result.returncode == 1
    assert "is a linked worktree. Install from the main tree." in result.stderr
    assert not (tmp_path / "launchctl.log").exists()
    assert not (tmp_path / "home" / "Library").exists()


@pytest.mark.parametrize("installer", installers(), ids=lambda p: p.name)
def test_control_the_same_installer_in_a_main_tree_gets_past_the_refusal(
    installer: Path, trees: tuple[Path, Path], tmp_path: Path
) -> None:
    """Without this, a guard that refused EVERY tree would pass the case above."""
    main, _linked = trees
    result = _run_installer(installer, main, tmp_path)
    assert "linked worktree" not in result.stderr
    # It stops at the next precondition instead: the throwaway tree has no venv.
    assert result.returncode == 1
    assert ".venv/bin/" in result.stderr
    assert not (tmp_path / "launchctl.log").exists()
