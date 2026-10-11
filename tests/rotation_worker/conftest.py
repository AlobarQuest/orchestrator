"""Throwaway origin repositories, so the mirror is tested against real git, never a stub."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rotation_worker.git_mirror import RepoMirror

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
}


def git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *arguments], capture_output=True, text=True, env=ENV, check=True
    )
    return result.stdout.strip()


class Origins:
    """`<root>/<owner>/<name>.git` working repositories, served to the mirror as file URLs."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def url(self) -> str:
        return f"file://{self.root}"

    def commit(self, repository: str, files: dict[str, str], branch: str = "main") -> str:
        path = self.root / f"{repository}.git"
        if not path.exists():
            path.mkdir(parents=True)
            git(path, "init", "--quiet", "--initial-branch", branch)
        else:
            git(path, "checkout", "--quiet", "-B", branch)
        for name, text in files.items():
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(text)
        git(path, "add", "-A")
        git(path, "commit", "--quiet", "--allow-empty", "-m", "change")
        return git(path, "rev-parse", "HEAD")


@pytest.fixture
def origins(tmp_path: Path) -> Origins:
    return Origins(tmp_path / "origins")


@pytest.fixture
def mirror(tmp_path: Path, origins: Origins) -> RepoMirror:
    return RepoMirror(tmp_path / "mirrors", origin=origins.url)
