"""`scripts/run-rotation-proposer.sh` refuses a linked worktree before it reads anything.

A pass commits to and publishes from the packages checkout every lane shares, so one started from a
build worktree would run unmerged code against it. Like the installer tests, nothing here runs the
real launcher in a real tree: it is copied, with its helpers, into a throwaway repository and its
linked worktree, with HOME pointed at a temporary directory.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path("scripts/run-rotation-proposer.sh")
HELPERS = ("sds-install.sh", "sds-deadman.sh", "sds-bws.sh")


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


def _run(root: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    (root / "scripts").mkdir(exist_ok=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    # `--dry-run` keeps the dead-man switch from arming, so no case here reaches the Keychain.
    return subprocess.run(
        ["/bin/bash", str(root / "scripts" / LAUNCHER.name), "--dry-run"],
        env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_refusal_comes_before_any_credential_or_switch() -> None:
    lines = LAUNCHER.read_text().splitlines()
    call = lines.index('sds_refuse_linked_worktree "$REPO_ROOT"')
    assert lines.index('source "$REPO_ROOT/scripts/sds-install.sh"') < call
    for later in ("sds_deadman_arm ", "sds_bws_value ", "find-generic-password", ".venv/bin/"):
        first = next(i for i, line in enumerate(lines) if later in line and "#" not in line[:2])
        assert call < first, later


def test_run_from_a_linked_worktree_it_refuses(trees, tmp_path) -> None:
    _main, linked = trees
    result = _run(linked, tmp_path)
    assert result.returncode == 1
    assert "is a linked worktree" in result.stderr


def test_control_from_a_main_tree_it_gets_past_the_refusal(trees, tmp_path) -> None:
    """Without this, a launcher that refused EVERY tree would pass the case above."""
    main, _linked = trees
    result = _run(main, tmp_path)
    assert "linked worktree" not in result.stderr
    assert result.returncode == 1
    assert "no rotation-proposer" in result.stderr
