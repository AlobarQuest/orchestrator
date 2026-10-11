"""Bare mirrors of the public repositories the executor checks its work against.

ADR-0055 decision 1 has the executor trust git, not the orchestrator: the package revision it
works must be an ancestor of intent-packages' `main`, and the registry it compares with is read at
the registries' `main`. So it keeps its own bare clone of each repository and asks git directly.
Every repository it reads is public, so no credential is needed to fetch, and none is ever passed:
the only arguments are repository names this program fixes, commit ids and file paths.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

REPOSITORY_PATTERN: Final = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
COMMIT_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
MAIN: Final = "refs/heads/main"
GITHUB: Final = "https://github.com"
TIMEOUT_SECONDS: Final = 120.0

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]]


class MirrorError(RuntimeError):
    """git could not answer, so nothing that depends on the answer may proceed."""


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    # No prompt can block a service, and no user or system config changes what is fetched.
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/"),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    return subprocess.run(
        list(argv), capture_output=True, env=environment, timeout=TIMEOUT_SECONDS, check=False
    )


class RepoMirror:
    """One bare clone per repository under `root`, each fetched before it answers."""

    def __init__(self, root: Path, runner: Runner = _run, origin: str = GITHUB) -> None:
        self._root = root
        self._run = runner
        self._origin = origin

    def sync(self, repository: str) -> str:
        """Fetch `repository`'s `main` and return the commit it names."""
        path = self._path(repository)
        url = f"{self._origin}/{repository}.git"
        if not path.exists():
            self._clone(repository, url, path)
        remote = self._git(repository, path, "config", "--get", "remote.origin.url")
        if remote.decode(errors="replace").strip() != url:
            raise MirrorError(f"{repository}: the mirror at {path} fetches from another origin")
        self._git(repository, path, "fetch", "--quiet", "origin", f"+{MAIN}:{MAIN}")
        commit = self._git(repository, path, "rev-parse", MAIN).decode(errors="replace").strip()
        if not COMMIT_PATTERN.fullmatch(commit):
            raise MirrorError(f"{repository}: main did not resolve to a commit")
        return commit

    def show(self, repository: str, commit: str, path: str) -> bytes:
        """The bytes of `path` at `commit`."""
        _require_commit(commit)
        return self._git(repository, self._path(repository), "show", f"{commit}:{path}")

    def is_ancestor(self, repository: str, commit: str, of: str) -> bool:
        """Whether `commit` is `of` or one of its ancestors. A commit the mirror lacks is not."""
        _require_commit(commit)
        _require_commit(of)
        path = self._path(repository)
        if self._status(repository, path, "cat-file", "-e", f"{commit}^{{commit}}") != 0:
            return False
        status = self._status(repository, path, "merge-base", "--is-ancestor", commit, of)
        if status not in (0, 1):
            raise MirrorError(f"{repository}: git merge-base exited {status}")
        return status == 0

    def _clone(self, repository: str, url: str, path: Path) -> None:
        # Clone beside the mirror and rename it into place, so a clone that is interrupted leaves
        # nothing a later run would mistake for a mirror.
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.partial-{os.getpid()}")
        self._git(repository, None, "clone", "--quiet", "--bare", url, str(partial))
        partial.rename(path)

    def _path(self, repository: str) -> Path:
        if not REPOSITORY_PATTERN.fullmatch(repository):
            raise MirrorError(f"not a repository name: {repository!r}")
        return self._root / f"{repository.replace('/', '__')}.git"

    def _git(self, repository: str, path: Path | None, *arguments: str) -> bytes:
        result = self._invoke(repository, path, *arguments)
        if result.returncode != 0:
            said = result.stderr.decode(errors="replace").strip().splitlines()
            raise MirrorError(
                f"{repository}: git {arguments[0]} exited {result.returncode}"
                + (f": {said[-1]}" if said else "")
            )
        return result.stdout

    def _status(self, repository: str, path: Path, *arguments: str) -> int:
        return self._invoke(repository, path, *arguments).returncode

    def _invoke(
        self, repository: str, path: Path | None, *arguments: str
    ) -> subprocess.CompletedProcess[bytes]:
        argv = ["git", *(["-C", str(path)] if path is not None else []), *arguments]
        try:
            return self._run(argv)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MirrorError(f"{repository}: git {arguments[0]} did not run: {error}") from error


def _require_commit(commit: str) -> None:
    if not COMMIT_PATTERN.fullmatch(commit):
        raise MirrorError(f"not a full commit id: {commit!r}")
