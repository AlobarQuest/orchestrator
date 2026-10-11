from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from rotation_worker.git_mirror import MirrorError, RepoMirror
from tests.rotation_worker.conftest import Origins


def test_sync_clones_then_follows_main(mirror: RepoMirror, origins: Origins) -> None:
    first = origins.commit("owner/repo", {"a.txt": "one"})
    assert mirror.sync("owner/repo") == first
    second = origins.commit("owner/repo", {"a.txt": "two"})
    assert mirror.sync("owner/repo") == second
    assert mirror.show("owner/repo", first, "a.txt") == b"one"
    assert mirror.show("owner/repo", second, "a.txt") == b"two"


def test_sync_reads_main_not_another_branch(mirror: RepoMirror, origins: Origins) -> None:
    main = origins.commit("owner/repo", {"a.txt": "main"})
    origins.commit("owner/repo", {"a.txt": "feature"}, branch="feature")
    assert mirror.sync("owner/repo") == main


@pytest.mark.parametrize("name", ["owner", "../escape/x", "owner/repo/extra", "owner/re po", ""])
def test_a_malformed_repository_name_is_refused_before_git_runs(tmp_path: Path, name: str) -> None:
    calls: list[Sequence[str]] = []

    def recording(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    with pytest.raises(MirrorError, match="not a repository name"):
        RepoMirror(tmp_path, runner=recording).sync(name)
    assert calls == []


@pytest.mark.parametrize("commit", ["main", "HEAD", "abc", "A" * 40, "0" * 39])
def test_show_takes_only_a_full_commit_id(
    mirror: RepoMirror, origins: Origins, commit: str
) -> None:
    origins.commit("owner/repo", {"a.txt": "x"})
    mirror.sync("owner/repo")
    with pytest.raises(MirrorError):
        mirror.show("owner/repo", commit, "a.txt")


def test_a_missing_repository_or_file_is_an_error(mirror: RepoMirror, origins: Origins) -> None:
    with pytest.raises(MirrorError):
        mirror.sync("owner/absent")
    commit = origins.commit("owner/repo", {"a.txt": "x"})
    mirror.sync("owner/repo")
    with pytest.raises(MirrorError):
        mirror.show("owner/repo", commit, "absent.txt")


def test_git_that_cannot_run_is_an_error(tmp_path: Path) -> None:
    def broken(_argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("git")

    with pytest.raises(MirrorError, match="did not run"):
        RepoMirror(tmp_path, runner=broken).sync("owner/repo")


def test_a_main_that_is_not_a_commit_is_an_error(tmp_path: Path) -> None:
    def answers(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        said = b"https://github.com/owner/repo.git\n" if "config" in argv else b"not-a-commit\n"
        return subprocess.CompletedProcess(argv, 0, stdout=said, stderr=b"")

    (tmp_path / "owner__repo.git").mkdir()
    with pytest.raises(MirrorError, match="did not resolve"):
        RepoMirror(tmp_path, runner=answers).sync("owner/repo")


def test_a_mirror_of_another_origin_is_refused(
    tmp_path: Path, mirror: RepoMirror, origins: Origins
) -> None:
    origins.commit("owner/repo", {"a.txt": "x"})
    mirror.sync("owner/repo")
    elsewhere = RepoMirror(tmp_path / "mirrors", origin=f"file://{tmp_path}/elsewhere")
    with pytest.raises(MirrorError, match="another origin"):
        elsewhere.sync("owner/repo")


def test_an_interrupted_clone_leaves_no_mirror_behind(tmp_path: Path, origins: Origins) -> None:
    origins.commit("owner/repo", {"a.txt": "x"})

    def dies_while_cloning(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        Path(argv[-1]).mkdir(parents=True)  # what a killed `git clone` leaves behind
        raise subprocess.TimeoutExpired(list(argv), 1)

    root = tmp_path / "mirrors"
    with pytest.raises(MirrorError, match="did not run"):
        RepoMirror(root, runner=dies_while_cloning, origin=origins.url).sync("owner/repo")
    assert not (root / "owner__repo.git").exists()
    assert RepoMirror(root, origin=origins.url).sync("owner/repo")


def test_a_failure_says_which_repository_and_what_git_said(
    mirror: RepoMirror, origins: Origins
) -> None:
    commit = origins.commit("owner/repo", {"a.txt": "x"})
    mirror.sync("owner/repo")
    with pytest.raises(MirrorError, match=r"owner/repo: git show exited 128: .*absent\.txt"):
        mirror.show("owner/repo", commit, "absent.txt")


def test_a_later_commit_is_not_an_ancestor_of_an_earlier_one(
    mirror: RepoMirror, origins: Origins
) -> None:
    first = origins.commit("owner/repo", {"a.txt": "one"})
    second = origins.commit("owner/repo", {"a.txt": "two"})
    mirror.sync("owner/repo")
    assert mirror.is_ancestor("owner/repo", first, second)
    assert mirror.is_ancestor("owner/repo", second, second)
    assert not mirror.is_ancestor("owner/repo", second, first)
    assert not mirror.is_ancestor("owner/repo", "0" * 40, second)
