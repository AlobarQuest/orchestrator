"""The sync workflow's rebuild step, run as written over real throwaway repositories.

Upstream is served from a local directory through `url.<base>.insteadOf`, and the fork's review
script is a stand-in whose `lock` writes a lockfile the way the real one does, so the step's own
shell is what runs.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from fork_sync.onboard import render

UPSTREAM = "upstream-owner/tool"
STAND_IN = """import pathlib, subprocess, sys
if sys.argv[1] == "lock":
    pathlib.Path("package-lock.json").write_text("resolved for " + sys.argv[2] + "\\n")
    subprocess.run(["git", "add", "--force", "package-lock.json"], check=True)
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, files: Mapping[str, str], message: str) -> None:
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _rebuild_step() -> str:
    workflow = yaml.safe_load(render("npm")[".github/workflows/upstream-sync.yml"])
    [step] = [
        s for s in workflow["jobs"]["sync"]["steps"] if s.get("name") == "Rebuild the sync branch"
    ]
    return step["run"]


@pytest.fixture
def origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The hosted fork: `main` is upstream's v1, its `.github/`, and a lockfile only it has."""
    hosted = tmp_path / "github"
    upstream = hosted / f"{UPSTREAM}.git"
    upstream.mkdir(parents=True)
    _git(upstream, "init", "-q", "-b", "trunk")
    _commit(upstream, {"package.json": '{"version": "1.0.0"}\n'}, "v1")
    _git(upstream, "tag", "v1")
    _commit(upstream, {"package.json": '{"version": "2.0.0"}\n'}, "v2")
    _git(upstream, "tag", "-a", "v2", "-m", "v2")

    config = tmp_path / "gitconfig"
    config.write_text(f'[url "{hosted.as_uri()}/"]\n\tinsteadOf = https://github.com/\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", "--branch", "v1", str(upstream), str(seed))
    _git(seed, "switch", "-q", "-c", "main")
    _commit(
        seed,
        {
            ".github/scripts/fork_sync_review.py": STAND_IN,
            ".github/fork-sync.toml": f'upstream = "{UPSTREAM}"\n',
            "package-lock.json": "resolved for v1\n",
        },
        "baseline",
    )
    _git(seed, "push", "-q", str(origin), "main")
    return origin


def _run_rebuild(origin: Path, tag: str) -> str:
    """One scheduled run: a fresh full checkout of the fork, as `actions/checkout` makes."""
    checkout = origin.parent / f"run-{len(list(origin.parent.glob('run-*')))}"
    _git(origin.parent, "clone", "-q", str(origin), str(checkout))
    env = {**os.environ, "UPSTREAM": UPSTREAM, "TAG": tag}
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", _rebuild_step()],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_a_sync_over_a_lockfile_only_the_fork_has_merges_cleanly(origin: Path) -> None:
    _run_rebuild(origin, "v2")

    # Exit 0 is a clean merge; the add/add conflict on the lockfile exits 1 and raises here.
    _git(origin, "merge-tree", "--write-tree", "main", "upstream-sync")
    assert _git(origin, "show", "upstream-sync:package-lock.json") == "resolved for v2"
    assert _git(origin, "show", "upstream-sync:package.json") == '{"version": "2.0.0"}'
    assert _git(origin, "rev-parse", "upstream-sync^1") == _git(
        origin.parent / "github" / f"{UPSTREAM}.git", "rev-parse", "v2^{commit}"
    )
    assert _git(origin, "rev-parse", "upstream-sync^2") == _git(origin, "rev-parse", "main")


def test_a_rebuild_of_the_same_tree_is_not_pushed_until_main_moves(origin: Path) -> None:
    _run_rebuild(origin, "v2")
    published = _git(origin, "rev-parse", "upstream-sync")

    assert "already holds this tree" in _run_rebuild(origin, "v2")
    assert _git(origin, "rev-parse", "upstream-sync") == published

    # main moves without changing the tree a sync builds: the branch must take it as a parent
    # again, or the pull request would no longer contain main.
    seed = origin.parent / "seed"
    _git(seed, "commit", "-q", "--allow-empty", "-m", "unrelated")
    _git(seed, "push", "-q", str(origin), "main")
    _run_rebuild(origin, "v2")
    _git(origin, "merge-base", "--is-ancestor", "main", "upstream-sync")
