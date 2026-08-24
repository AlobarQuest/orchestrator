"""Hermetic git repositories, so every measurement in these tests is made against a real tree.

A fake `git` runner would let the tests agree with a model of git rather than with git, which is
the failure this estate has already recorded once: a mutation set can only question the model its
tests already hold. These are real repositories with a real remote, built in a temporary
directory, and the whole suite of them takes about a second.

The remote is a bare repository on disk, while `origin`'s URL READS as a GitHub URL -- git's own
`url.<base>.insteadOf` rewrites it on the way out. That is what lets one fixture exercise both
halves at once: `repository_of` sees the GitHub URL it has to parse, and `git fetch` reaches a
local path and touches no network.

The environment used to BUILD the repositories is scrubbed of the operator's own git
configuration. A global `commit.gpgsign`, a template directory, or a `core.hooksPath` would
otherwise reach in and make these tests pass or fail for reasons unrelated to the code.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# The migrated-database fixtures, re-exported the way every sibling suite does it. Only
# `test_replay.py` uses them: it is the one place the record's bytes meet the real ingestion
# service, the real CHECK constraints and the real conflict branch.
from tests.persistence.conftest import migrated_engine, migrated_session  # noqa: F401

ORIGIN_URL = "https://github.com/AlobarQuest/example.git"

GIT_ENVIRONMENT = {
    "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
    "HOME": "/nonexistent",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_AUTHOR_DATE": "2026-08-24T06:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-08-24T06:00:00+00:00",
    "GIT_TERMINAL_PROMPT": "0",
}


def git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=True,
        env=GIT_ENVIRONMENT,
    )
    return completed.stdout


def _run(*args: str) -> None:
    subprocess.run(list(args), check=True, capture_output=True, env=GIT_ENVIRONMENT)


class Estate:
    """One origin, one working copy under measurement, and one clone that can move the origin."""

    def __init__(self, root: Path) -> None:
        self.origin = root / "origin.git"
        self.local = root / "local"
        self.other = root / "other"
        _run("git", "init", "--bare", "-b", "main", str(self.origin))
        self._clone(self.local)
        (self.local / "README.md").write_text("one\n")
        git(self.local, "add", "README.md")
        git(self.local, "commit", "-m", "first")
        git(self.local, "push", "-u", "origin", "main")
        self._clone(self.other)

    def _clone(self, target: Path) -> None:
        _run("git", "clone", str(self.origin), str(target))
        git(target, "remote", "set-url", "origin", ORIGIN_URL)
        git(target, "config", f"url.{self.origin}.insteadOf", ORIGIN_URL)
        git(target, "config", "user.email", "test@example.invalid")

    def land_upstream(self, subject: str = "second") -> str:
        """Put one more commit on the origin, so `local` falls behind by one.

        The file content is made unique per call rather than taken from the subject: two landings
        carrying the same subject would leave the tree unchanged and git would refuse the commit.
        """
        self._landings = getattr(self, "_landings", 0) + 1
        (self.other / "README.md").write_text(f"landing {self._landings}\n")
        git(self.other, "add", "README.md")
        git(self.other, "commit", "-m", subject)
        git(self.other, "push", "origin", "main")
        return git(self.other, "rev-parse", "HEAD").strip()

    def commit_locally(self, subject: str = "unpushed") -> None:
        """A local commit that was never pushed, so `local` is AHEAD of the origin."""
        (self.local / "local-only.txt").write_text(f"{subject}\n")
        git(self.local, "add", "local-only.txt")
        git(self.local, "commit", "-m", subject)

    def modify_tracked(self) -> None:
        (self.local / "README.md").write_text("edited but not committed\n")

    def restore_tracked(self) -> None:
        (self.local / "README.md").write_text("one\n")

    def add_untracked(self, count: int) -> None:
        for index in range(count):
            (self.local / f"artifact-{index}.txt").write_text("cron output\n")


@pytest.fixture
def estate(tmp_path: Path) -> Estate:
    return Estate(tmp_path)
