"""The pass over one fork, against real git: a local bare repository stands in for the fork."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fork_harden import git
from fork_harden.harden import Held, guard, harden
from fork_harden.rules import BRANCH, Fork, Rule

AUTHOR = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def _detect(path: str, text: str) -> tuple[int, ...]:
    del path
    lines = text.splitlines()
    return tuple(n for n, line in enumerate(lines, 1) if "BAD" in line or "WORSE" in line)


def _fix(path: str, text: str) -> str:
    del path
    return text.replace("BAD", "GOOD")


RULE = Rule("toy", "BAD is bad", ("src/*",), _detect, _fix)
FORK = Fork("AlobarQuest/toy", (RULE,))


class Upstream:
    """The fork: a bare repository, written through a scratch clone as fork-sync would land."""

    def __init__(self, root: Path) -> None:
        self.bare = root / "fork.git"
        self.work = root / "work"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.work)], check=True)
        self._git("remote", "add", "origin", str(self.bare))

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.work), *AUTHOR, *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def land(self, files: dict[str, str | bytes | None]) -> str:
        """A release landing on `main`: write, delete (None) and push."""
        for name, content in files.items():
            path = self.work / name
            if content is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "release")
        self._git("push", "-q", "origin", "main")
        return self._git("rev-parse", "HEAD")

    def head(self, branch: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(self.bare), "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or None

    def show(self, ref: str, path: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.bare), "show", f"{ref}:{path}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def parents(self, commit: str) -> list[str]:
        out = subprocess.run(
            ["git", "-C", str(self.bare), "rev-list", "--parents", "-n1", commit],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        return out[1:]


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    return Upstream(tmp_path)


@pytest.fixture
def repo(tmp_path: Path, upstream: Upstream) -> git.Repo:
    return git.open_cache(tmp_path / "cache.git", str(upstream.bare))


RELEASE_1 = {
    "src/a.sh": "echo BAD\n",
    "src/clean.sh": "echo fine\n",
    "docs/BAD.md": "BAD outside the rule's paths\n",
    ".github/ci.yml": "on: push\n",
}


def test_the_first_pass_creates_hardened_on_top_of_main(upstream: Upstream, repo: git.Repo) -> None:
    main = upstream.land(dict(RELEASE_1))
    outcome = harden(repo, FORK, push=True)
    assert outcome.status == "advanced"
    hardened = upstream.head(BRANCH)
    assert hardened is not None
    assert hardened == outcome.hardened_after
    assert upstream.parents(hardened) == [main]
    assert upstream.show(BRANCH, "src/a.sh") == "echo GOOD\n"
    assert upstream.show(BRANCH, "docs/BAD.md") == "BAD outside the rule's paths\n"
    assert upstream.show(BRANCH, ".github/ci.yml") == "on: push\n"
    assert outcome.changed_files == 1


def test_a_second_pass_with_nothing_new_is_current_and_pushes_nothing(
    upstream: Upstream, repo: git.Repo
) -> None:
    upstream.land(dict(RELEASE_1))
    harden(repo, FORK, push=True)
    before = upstream.head(BRANCH)
    outcome = harden(repo, FORK, push=True)
    assert outcome.status == "current"
    assert upstream.head(BRANCH) == before


def test_a_new_release_moves_hardened_forward_with_both_parents(
    upstream: Upstream, repo: git.Repo
) -> None:
    upstream.land(dict(RELEASE_1))
    harden(repo, FORK, push=True)
    previous = upstream.head(BRANCH)
    assert previous is not None
    # Upstream rewrote the file the fix touched and added a new site elsewhere: no conflict, and
    # the new site is fixed like the old one.
    main = upstream.land({"src/a.sh": "echo BAD again\n", "src/new.sh": "BAD\n"})
    outcome = harden(repo, FORK, push=True)
    assert outcome.status == "advanced"
    assert upstream.parents(upstream.head(BRANCH) or "") == [previous, main]
    assert upstream.show(BRANCH, "src/a.sh") == "echo GOOD again\n"
    assert upstream.show(BRANCH, "src/new.sh") == "GOOD\n"


def test_without_push_nothing_reaches_the_fork(upstream: Upstream, repo: git.Repo) -> None:
    upstream.land(dict(RELEASE_1))
    outcome = harden(repo, FORK, push=False)
    assert outcome.status == "would_advance"
    assert outcome.hardened_after
    assert upstream.head(BRANCH) is None


def test_a_site_the_fix_cannot_repair_holds_and_hardened_stays(
    upstream: Upstream, repo: git.Repo
) -> None:
    upstream.land(dict(RELEASE_1))
    harden(repo, FORK, push=True)
    before = upstream.head(BRANCH)
    upstream.land({"src/b.sh": "WORSE\n"})
    outcome = harden(repo, FORK, push=True)
    assert outcome.status == "held"
    assert "still flags src/b.sh at line(s) 1" in outcome.reasons[0]
    assert upstream.head(BRANCH) == before


def test_a_fix_that_refuses_holds(upstream: Upstream, repo: git.Repo) -> None:
    def refusing(path: str, text: str) -> str:
        raise ValueError("not a shape this fix knows")

    fork = Fork("AlobarQuest/toy", (Rule("toy", "x", ("src/*",), _detect, refusing),))
    upstream.land(dict(RELEASE_1))
    outcome = harden(repo, fork, push=True)
    assert outcome.status == "held"
    assert "could not fix" in outcome.reasons[0]
    assert upstream.head(BRANCH) is None


def test_a_finding_upstream_fixed_is_reported_and_hardened_tracks_main(
    upstream: Upstream, repo: git.Repo
) -> None:
    upstream.land(dict(RELEASE_1))
    harden(repo, FORK, push=True)
    main = upstream.land({"src/a.sh": "echo GOOD upstream\n"})
    outcome = harden(repo, FORK, push=True)
    assert outcome.status == "advanced"
    assert outcome.results[0].fixed_upstream
    assert "fixed upstream" in "\n".join(outcome.lines())
    assert outcome.changed_files == 0
    hardened = upstream.head(BRANCH) or ""
    assert repo.text("rev-parse", f"{hardened}^{{tree}}") == repo.text(
        "rev-parse", f"{main}^{{tree}}"
    )


def test_a_rule_that_covers_no_file_holds(upstream: Upstream, repo: git.Repo) -> None:
    upstream.land(dict(RELEASE_1))
    fork = Fork("AlobarQuest/toy", (RULE, Rule("gone", "x", ("missing/*",), _detect, _fix)))
    outcome = harden(repo, fork, push=True)
    assert outcome.status == "held"
    assert "gone covers no file" in outcome.reasons[0]


def test_covered_text_that_is_not_utf8_holds(upstream: Upstream, repo: git.Repo) -> None:
    upstream.land({**RELEASE_1, "src/latin1.sh": b"echo BAD \xe9\n"})
    outcome = harden(repo, FORK, push=True)
    assert outcome.status == "held"
    assert "src/latin1.sh is not UTF-8 text" in outcome.reasons[0]
    assert outcome.lines()[1:] == [f"  held: {outcome.reasons[0]}"]


def test_a_covered_binary_file_is_skipped_unchanged(upstream: Upstream, repo: git.Repo) -> None:
    upstream.land({**RELEASE_1, "src/icon.png": b"BAD\0binary"})
    outcome = harden(repo, FORK, push=True)
    assert outcome.status == "advanced"
    assert outcome.changed_files == 1
    show = subprocess.run(
        ["git", "-C", str(upstream.bare), "show", f"{BRANCH}:src/icon.png"],
        check=True,
        capture_output=True,
    )
    assert show.stdout == b"BAD\0binary"


def test_a_covered_symlink_holds(upstream: Upstream, repo: git.Repo) -> None:
    upstream.land(dict(RELEASE_1))
    (upstream.work / "src" / "link.sh").symlink_to("a.sh")
    upstream.land({})
    outcome = harden(repo, FORK, push=True)
    assert outcome.status == "held"
    assert "src/link.sh is a blob (mode 120000)" in outcome.reasons[0]


def test_a_rule_that_reaches_into_fork_syncs_files_is_refused_by_the_guard(
    upstream: Upstream, repo: git.Repo
) -> None:
    upstream.land({**RELEASE_1, ".github/BAD.yml": "BAD\n"})
    fork = Fork("AlobarQuest/toy", (Rule("greedy", "x", ("*",), _detect, _fix),))
    outcome = harden(repo, fork, push=True)
    assert outcome.status == "held"
    assert "guard: .github/BAD.yml is fork-sync's" in outcome.reasons[0]
    assert upstream.head(BRANCH) is None


def _main_tree(upstream: Upstream, repo: git.Repo) -> str:
    main = upstream.land(dict(RELEASE_1))
    git.fetch(repo, {"main": main})
    return git.tree_of(repo, main)


def test_the_guard_refuses_a_change_to_a_path_no_rule_covers(
    upstream: Upstream, repo: git.Repo
) -> None:
    main_tree = _main_tree(upstream, repo)
    sha = git.write_blob(repo, b"changed\n")
    tree = git.tree_with(repo, main_tree, {"docs/BAD.md": ("100644", sha)})
    with pytest.raises(Held, match="docs/BAD.md is covered by no rule"):
        guard(repo, FORK, main_tree, tree)


@pytest.mark.parametrize(
    ("path", "mode", "message"),
    [
        ("src/added.sh", "100644", "src/added.sh is A"),
        ("src/a.sh", "100755", "src/a.sh is M 100644->100755"),
    ],
)
def test_the_guard_refuses_anything_but_a_content_change(
    upstream: Upstream, repo: git.Repo, path: str, mode: str, message: str
) -> None:
    main_tree = _main_tree(upstream, repo)
    sha = git.write_blob(repo, b"echo GOOD\n")
    tree = git.tree_with(repo, main_tree, {path: (mode, sha)})
    with pytest.raises(Held, match=message):
        guard(repo, FORK, main_tree, tree)


def test_the_guard_refuses_a_deletion(upstream: Upstream, repo: git.Repo) -> None:
    main_tree = _main_tree(upstream, repo)
    main = upstream.land({"src/clean.sh": None})
    git.fetch(repo, {"main": main})
    with pytest.raises(Held, match="src/clean.sh is D"):
        guard(repo, FORK, main_tree, git.tree_of(repo, main))


def test_a_push_that_is_not_a_fast_forward_is_refused(upstream: Upstream, repo: git.Repo) -> None:
    upstream.land(dict(RELEASE_1))
    harden(repo, FORK, push=True)
    main = upstream.land({"src/c.sh": "BAD\n"})
    git.fetch(repo, {"main": main})
    # A commit that does not descend from the published `hardened`: the push must fail rather
    # than rewrite the branch the installer's serving clone pulls with --ff-only.
    stray = git.commit(
        repo,
        git.tree_of(repo, main),
        [main],
        "stray\n",
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x",
        },
    )
    before = upstream.head(BRANCH)
    with pytest.raises(git.GitError):
        git.push(repo, stray, BRANCH)
    assert upstream.head(BRANCH) == before


def test_a_fork_with_no_main_is_unreadable(tmp_path: Path) -> None:
    bare = tmp_path / "empty.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo = git.open_cache(tmp_path / "cache.git", str(bare))
    with pytest.raises(git.GitError, match="has no main"):
        harden(repo, FORK, push=True)


def test_main_moving_without_changing_the_tree_still_moves_hardened(
    upstream: Upstream, repo: git.Repo
) -> None:
    upstream.land(dict(RELEASE_1))
    harden(repo, FORK, push=True)
    previous = upstream.head(BRANCH)
    # A commit on `main` with an identical tree (fork-sync re-landing the same release): `main`
    # must stay an ancestor of `hardened`, so the branch moves even though its tree does not.
    upstream._git("commit", "-q", "--allow-empty", "-m", "same tree")
    upstream._git("push", "-q", "origin", "main")
    main = upstream.head("main")
    outcome = harden(repo, FORK, push=True)
    assert outcome.status == "advanced"
    assert upstream.parents(upstream.head(BRANCH) or "") == [previous, main]


def test_a_replace_ref_in_the_cache_cannot_substitute_a_tree(
    upstream: Upstream, repo: git.Repo
) -> None:
    main = upstream.land(dict(RELEASE_1))
    other = upstream.land({"src/a.sh": "echo other\n"})
    git.fetch(repo, {"main": main, "other": other})
    real = git.tree_of(repo, main)
    subprocess.run(["git", "-C", str(repo.root), "replace", main, other], check=True)
    assert git.tree_of(repo, main) == real


def test_a_path_that_is_not_utf8_survives_the_tree_build(
    upstream: Upstream, repo: git.Repo
) -> None:
    # macOS refuses such a name on disk, so the tree is written with git's plumbing alone.
    name = b"src/caf\xe9.sh".decode("utf-8", "surrogateescape")
    main_tree = _main_tree(upstream, repo)
    tree = git.tree_with(repo, main_tree, {name: ("100644", git.write_blob(repo, b"ok\n"))})
    assert name in [entry.path for entry in git.entries(repo, tree)]
    assert [change.path for change in git.changes(repo, main_tree, tree)] == [name]


def test_a_git_timeout_is_a_git_error(monkeypatch: pytest.MonkeyPatch, repo: git.Repo) -> None:
    def slow(*_: object, **__: object) -> None:
        raise subprocess.TimeoutExpired(["git"], 300)

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(git.GitError, match="timed out"):
        repo.git("status")


def test_the_cache_keeps_what_it_fetched_for_the_next_fetch(
    upstream: Upstream, repo: git.Repo
) -> None:
    main = upstream.land(dict(RELEASE_1))
    harden(repo, FORK, push=False)
    assert repo.text("rev-parse", "refs/cache/main") == main


def test_a_nul_after_the_first_line_holds_because_bash_still_runs_it(
    upstream: Upstream, repo: git.Repo
) -> None:
    upstream.land({**RELEASE_1, "src/payload.sh": b"echo BAD\nexit 0\n\0payload"})
    outcome = harden(repo, FORK, push=True)
    assert outcome.status == "held"
    assert "src/payload.sh holds a NUL byte after its first line" in outcome.reasons[0]


def test_a_nul_past_bashs_sample_holds(upstream: Upstream, repo: git.Repo) -> None:
    upstream.land({**RELEASE_1, "src/long.sh": b"echo BAD " + b"x" * 80 + b"\0"})
    outcome = harden(repo, FORK, push=True)
    assert outcome.status == "held"


def test_a_guard_hold_keeps_the_whole_counts(upstream: Upstream, repo: git.Repo) -> None:
    upstream.land({**RELEASE_1, ".github/BAD.yml": "BAD\n"})
    fork = Fork("AlobarQuest/toy", (Rule("greedy", "x", ("*",), _detect, _fix),))
    outcome = harden(repo, fork, push=True)
    assert outcome.status == "held"
    assert any("greedy: 3 line(s) on main" in line for line in outcome.lines())
