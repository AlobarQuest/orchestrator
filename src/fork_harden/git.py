"""git plumbing over a bare cache of one fork. Upstream's tree is never checked out.

Every file is read with `cat-file` and every change written with `hash-object`, into a bare
repository, so no checkout ever runs a filter, a hook or an attribute from upstream's tree, and
nothing of it is on disk as files a program could execute. Replace objects are disabled, so a
`refs/replace/` ref fetched from the fork can't substitute one commit for another. The same
precautions as the fork-sync review, which reads the head's objects the same way (ADR-0056
decision 4).

THE PUSH NEVER FORCES. GitHub then refuses one that doesn't fast-forward the branch, and that
refusal is the backstop for the rule that `hardened` only moves forward: the tool installer's
serving clone pulls it with `--ff-only`.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

GIT_TIMEOUT_SECONDS: Final = 300
NO_REPLACE: Final = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"}


class GitError(RuntimeError):
    """A git command failed. Its message carries git's own stderr, truncated."""


@dataclass(frozen=True)
class Entry:
    mode: str
    kind: str
    sha: str
    path: str


@dataclass(frozen=True)
class Change:
    status: str
    old_mode: str
    new_mode: str
    path: str


@dataclass(frozen=True)
class Repo:
    """A bare cache at `root` whose `origin` is the fork."""

    root: Path

    def git(
        self,
        *args: str,
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
    ) -> bytes:
        argv = ["git", "-C", str(self.root), "-c", "core.hooksPath=/dev/null", *args]
        result = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            env={**os.environ, **NO_REPLACE, **(env or {})},
        )
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip()[:300]
            raise GitError(f"git {' '.join(args[:2])} exited {result.returncode}: {detail}")
        return result.stdout

    def text(self, *args: str) -> str:
        return self.git(*args).decode().strip()


def open_cache(root: Path, remote: str) -> Repo:
    """The bare cache at `root`, created when missing, with `origin` set to `remote`."""
    if not (root / "HEAD").exists():
        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", str(root)], check=True, timeout=60)
    repo = Repo(root)
    if remote != _remote_url(repo):
        repo.git("config", "remote.origin.url", remote)
    return repo


def _remote_url(repo: Repo) -> str | None:
    try:
        return repo.text("config", "--get", "remote.origin.url")
    except GitError:
        return None


def remote_heads(repo: Repo, *branches: str) -> dict[str, str]:
    """`branch -> sha` for each branch the fork has; a branch it lacks is absent."""
    refs = [f"refs/heads/{branch}" for branch in branches]
    heads: dict[str, str] = {}
    for line in repo.text("ls-remote", "origin", *refs).splitlines():
        sha, ref = line.split("\t")
        heads[ref.removeprefix("refs/heads/")] = sha
    return heads


def fetch(repo: Repo, *shas: str) -> None:
    """Fetch exactly these commits, never a branch name that could move between reads."""
    repo.git("fetch", "-q", "--no-tags", "origin", *shas)


def tree_of(repo: Repo, commit: str) -> str:
    return repo.text("rev-parse", f"{commit}^{{tree}}")


def is_ancestor(repo: Repo, ancestor: str, descendant: str) -> bool:
    try:
        repo.git("merge-base", "--is-ancestor", ancestor, descendant)
    except GitError:
        return False
    return True


def entries(repo: Repo, tree: str) -> list[Entry]:
    out = repo.git("ls-tree", "-r", "-z", "--full-tree", tree).decode()
    found: list[Entry] = []
    for record in filter(None, out.split("\0")):
        meta, path = record.split("\t", 1)
        mode, kind, sha = meta.split(" ")
        found.append(Entry(mode, kind, sha, path))
    return found


def blob(repo: Repo, sha: str) -> bytes:
    return repo.git("cat-file", "blob", sha)


def write_blob(repo: Repo, content: bytes) -> str:
    return repo.git("hash-object", "-w", "--no-filters", "--stdin", stdin=content).decode().strip()


def tree_with(repo: Repo, base_tree: str, replaced: dict[str, tuple[str, str]]) -> str:
    """`base_tree` with each `path -> (mode, blob sha)` replaced, through a private index."""
    with tempfile.TemporaryDirectory() as scratch:
        index = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
        repo.git("read-tree", base_tree, env=index)
        if replaced:
            info = "".join(
                f"{mode} {sha}\t{path}\0" for path, (mode, sha) in sorted(replaced.items())
            )
            repo.git("update-index", "-z", "--index-info", stdin=info.encode(), env=index)
        return repo.git("write-tree", env=index).decode().strip()


def changes(repo: Repo, old_tree: str, new_tree: str) -> list[Change]:
    """Every path that differs between two trees, renames not detected."""
    out = repo.git("diff-tree", "-r", "-z", "--no-renames", old_tree, new_tree).decode()
    fields = out.split("\0")
    found: list[Change] = []
    for index in range(0, len(fields) - 1, 2):
        old_mode, new_mode, _, _, status = fields[index].lstrip(":").split(" ")
        found.append(Change(status, old_mode, new_mode, fields[index + 1]))
    return found


def commit(
    repo: Repo, tree: str, parents: list[str], message: str, identity: dict[str, str]
) -> str:
    argv = ["commit-tree", tree]
    for parent in parents:
        argv += ["-p", parent]
    return repo.git(*argv, stdin=message.encode(), env=identity).decode().strip()


PUSH_REMOTE: Final = "origin"


def push(repo: Repo, commit_sha: str, branch: str) -> None:
    """Publish `commit_sha` as `branch`. Never forced: a push that isn't a fast-forward fails."""
    repo.git("push", "-q", PUSH_REMOTE, f"{commit_sha}:refs/heads/{branch}")
