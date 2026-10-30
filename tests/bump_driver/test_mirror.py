"""The mirror runs git and nothing else, on a repository the member record names."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from bump_driver.mirror import MirrorFailed, MirrorRefused, Mirrors

REPOSITORY = "AlobarQuest/example"
TOKEN = "not-a-real-token-value"
ENV = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": "/nonexistent"}


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """`AlobarQuest/example.git` under a directory a `file://` base URL names."""
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    (source / "pyproject.toml").write_text("one\n")
    _git(source, "add", "-A")
    _git(source, "commit", "-q", "-m", "one")
    served = tmp_path / "served" / "AlobarQuest"
    served.mkdir(parents=True)
    _git(served, "clone", "-q", "--bare", str(source), "example.git")
    return source


def _mirrors(tmp_path: Path, **kwargs: Any) -> Mirrors:
    return Mirrors(
        tmp_path / "mirrors",
        {"alobarquest/example": REPOSITORY},
        token=TOKEN,
        base_url=f"file://{tmp_path}/served/",
        **{"environ": ENV, **kwargs},
    )


def _push(origin: Path, tmp_path: Path, text: str) -> str:
    (origin / "pyproject.toml").write_text(text)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", text.strip())
    _git(origin, "push", "-q", str(tmp_path / "served/AlobarQuest/example.git"), "main")
    return _git(origin, "rev-parse", "HEAD")


def test_a_first_read_clones_shallow_at_main(origin: Path, tmp_path: Path) -> None:
    path = _mirrors(tmp_path).fresh(REPOSITORY)
    assert path == tmp_path / "mirrors/AlobarQuest/example"
    assert (path / "pyproject.toml").read_text() == "one\n"
    assert _git(path, "rev-parse", "--is-shallow-repository") == "true"


def test_a_dirty_mirror_is_reset_to_origin_main(origin: Path, tmp_path: Path) -> None:
    mirrors = _mirrors(tmp_path)
    path = mirrors.fresh(REPOSITORY)
    (path / "pyproject.toml").write_text("dirty\n")
    (path / "stray.txt").write_text("stray\n")
    head = _push(origin, tmp_path, "two\n")
    mirrors.fresh(REPOSITORY)
    assert (path / "pyproject.toml").read_text() == "two\n"
    assert not (path / "stray.txt").exists()
    assert _git(path, "rev-parse", "HEAD") == head
    assert _git(path, "status", "--porcelain") == ""


def test_a_mirror_satisfies_the_generators_currency_check(origin: Path, tmp_path: Path) -> None:
    """`assert_checkout_current`: `git fetch origin main`, HEAD equal to origin/main, clean."""
    path = _mirrors(tmp_path).fresh(REPOSITORY)
    _git(path, "fetch", "--quiet", "origin", "main")
    assert _git(path, "rev-parse", "HEAD") == _git(path, "rev-parse", "origin/main")
    assert _git(path, "status", "--porcelain") == ""


def test_a_planted_hook_does_not_run(origin: Path, tmp_path: Path) -> None:
    mirrors = _mirrors(tmp_path)
    path = mirrors.fresh(REPOSITORY)
    marker = tmp_path / "hook-ran"
    hook = path / ".git/hooks/post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    _push(origin, tmp_path, "two\n")
    mirrors.fresh(REPOSITORY)
    assert not marker.exists()


def test_a_repository_outside_the_member_record_is_refused_before_any_command(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    mirrors = _mirrors(tmp_path, run=run)
    with pytest.raises(MirrorRefused):
        mirrors.fresh("AlobarQuest/not-a-member")
    with pytest.raises(MirrorRefused):
        mirrors.fresh("AlobarQuest/example/../../evil")
    assert calls == []


def test_every_command_is_git_and_only_clone_fetch_reset_clean(
    origin: Path, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.run(argv, **kwargs)  # noqa: PLW1510 - check is the caller's

    mirrors = _mirrors(tmp_path, run=run)
    mirrors.fresh(REPOSITORY)
    mirrors.fresh(REPOSITORY)
    assert {argv[0] for argv in calls} == {"git"}
    verbs = [
        next(a for a in argv[1:] if a in {"clone", "fetch", "reset", "clean"}) for argv in calls
    ]
    assert verbs == ["clone", "fetch", "reset", "clean"]
    assert not any(TOKEN in " ".join(argv) for argv in calls)


def test_the_token_reaches_no_argument_and_no_config(origin: Path, tmp_path: Path) -> None:
    envs: list[dict[str, str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        envs.append(kwargs["env"])
        return subprocess.run(argv, **kwargs)  # noqa: PLW1510 - check is the caller's

    path = _mirrors(tmp_path, run=run).fresh(REPOSITORY)
    assert TOKEN not in (path / ".git/config").read_text()
    assert "extraheader" not in (path / ".git/config").read_text()
    environment = envs[0]
    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    pairs = {
        environment[f"GIT_CONFIG_KEY_{n}"]: environment[f"GIT_CONFIG_VALUE_{n}"]
        for n in range(int(environment["GIT_CONFIG_COUNT"]))
    }
    assert pairs["core.hooksPath"] == "/dev/null"


def test_the_git_environment_is_what_the_generator_inherits(tmp_path: Path) -> None:
    mirrors = _mirrors(tmp_path)
    assert mirrors.environment()["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert "GIT_CONFIG_COUNT" in mirrors.environment()


def test_a_failing_command_is_mirror_failed_naming_no_secret(tmp_path: Path) -> None:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 128, "", "fatal: unable to access")

    with pytest.raises(MirrorFailed, match="clone exited 128") as caught:
        _mirrors(tmp_path, run=run).fresh(REPOSITORY)
    assert TOKEN not in str(caught.value)


def test_a_command_that_hangs_is_mirror_failed(tmp_path: Path) -> None:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 1)

    with pytest.raises(MirrorFailed, match="timed out"):
        _mirrors(tmp_path, run=run).fresh(REPOSITORY)
