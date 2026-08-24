"""What the sweep measures, against real git trees."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from activation_sweep.checkout import (
    BEHIND,
    CONDITIONS,
    DIRTY,
    READ_ONLY,
    ForbiddenCommandError,
    GitError,
    conditions_of,
    read_checkout,
    repository_of,
    run_git,
)
from tests.activation_sweep.conftest import Estate, git


def test_a_current_clean_checkout_reports_no_conditions(estate: Estate) -> None:
    state = read_checkout(estate.local)

    assert conditions_of(state) == ()
    assert state.repository == "AlobarQuest/example"
    assert state.branch == "main"
    assert state.upstream == "origin/main"
    assert state.behind_by == 0
    assert state.ahead_by == 0
    assert state.tracked_modifications == 0
    assert state.missing == ()
    assert state.head_committed_at.tzinfo is not None


def test_a_checkout_behind_its_upstream_names_the_commits_it_is_missing(estate: Estate) -> None:
    """The live shape this exists for: `intent-packages` sitting one bump behind."""
    landed = estate.land_upstream("bump ruff from 0.16.2 to 0.16.3 (#76)")

    state = read_checkout(estate.local)

    assert conditions_of(state) == (BEHIND,)
    assert state.behind_by == 1
    # FULL hashes: `%h` is sized against a repository's object count, so it can lengthen as the
    # repository grows and move the record's identity with nothing about reality having changed.
    assert [commit.commit for commit in state.missing] == [landed]
    assert len(landed) == 40
    assert state.missing[0].subject == "bump ruff from 0.16.2 to 0.16.3 (#76)"


def test_untracked_files_are_not_dirt_but_a_tracked_modification_is(estate: Estate) -> None:
    """SECTION 5.1, AND THE CONTROL IS THE PAIR RATHER THAN EITHER HALF.

    `FacelessTT`'s cron writes untracked artifacts as its normal output, so a rule counting
    porcelain lines makes that repository red on every sweep forever -- the permanently-red
    control this estate fixed one day before this was built. A rule that instead counted nothing
    would look identical on the first half of this test, which is why the second half is here.
    """
    estate.add_untracked(62)

    clean = read_checkout(estate.local)
    assert conditions_of(clean) == ()
    assert clean.tracked_modifications == 0
    # The measurement the excluded rule would have made, taken here so the exclusion is a
    # demonstrated difference rather than an assertion about one.
    assert len(git(estate.local, "status", "--porcelain").splitlines()) == 62

    estate.modify_tracked()

    dirty = read_checkout(estate.local)
    assert conditions_of(dirty) == (DIRTY,)
    assert dirty.tracked_modifications == 1
    assert len(git(estate.local, "status", "--porcelain").splitlines()) == 63


def test_a_staged_change_is_dirt_too(estate: Estate) -> None:
    """`--untracked-files=no` excludes untracked files, not the index."""
    estate.modify_tracked()
    git(estate.local, "add", "README.md")

    assert conditions_of(read_checkout(estate.local)) == (DIRTY,)


def test_behind_and_dirty_are_reported_together_rather_than_collapsed(estate: Estate) -> None:
    estate.land_upstream()
    estate.modify_tracked()

    state = read_checkout(estate.local)

    assert conditions_of(state) == (BEHIND, DIRTY)
    assert state.behind_by == 1
    assert state.tracked_modifications == 1


def test_the_condition_vocabulary_is_totally_covered_by_the_classifier(estate: Estate) -> None:
    """Every member of `CONDITIONS` is reachable, and the classifier returns nothing else.

    A member with no branch is a value nobody can produce; a branch with no member is a value
    nobody can interpret. Both are the same defect, and neither is visible from either side alone.
    """
    reachable = set()
    estate.land_upstream()
    reachable.update(conditions_of(read_checkout(estate.local)))
    estate.modify_tracked()
    reachable.update(conditions_of(read_checkout(estate.local)))

    assert reachable == set(CONDITIONS)


def test_an_unpushed_commit_is_measured_and_deliberately_not_a_condition(estate: Estate) -> None:
    """`ahead` is carried in every row and classified by nothing -- see the module docstring.

    A working copy with unpushed commits IS running code that was never merged, and whether that
    is a finding is a decision nobody has made. Recording the number means the decision can later
    be made against history rather than against an argument; inventing a third finding class here
    would be making it unilaterally.
    """
    estate.commit_locally()

    state = read_checkout(estate.local)

    assert state.ahead_by == 1
    assert conditions_of(state) == ()


def test_a_fetch_is_what_makes_behind_mean_anything(estate: Estate) -> None:
    """SECTION 5.4. Without it, `behind` is measured against stale refs and is always zero --
    the control reports current because it never looked. Measured as a differential on one tree
    rather than asserted, because a `--no-fetch` that happened to be right proves nothing."""
    estate.land_upstream()

    assert read_checkout(estate.local, fetch=False).behind_by == 0
    assert read_checkout(estate.local, fetch=True).behind_by == 1


def test_a_subdirectory_is_refused_rather_than_measured_as_its_parent(estate: Estate) -> None:
    """`git -C` on a subdirectory resolves to the enclosing repository, so a typo in an enrolled
    path would otherwise file some parent's answer under the parent's name, silently."""
    nested = estate.local / "nested"
    nested.mkdir()

    with pytest.raises(GitError):
        read_checkout(nested)


def test_a_path_that_is_not_a_repository_is_unmeasurable(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        read_checkout(tmp_path)


def test_a_checkout_with_no_upstream_is_unmeasurable(estate: Estate) -> None:
    """There is nothing to be behind. Saying `current` would be an answer nobody measured."""
    git(estate.local, "branch", "--unset-upstream")

    with pytest.raises(GitError):
        read_checkout(estate.local)


def test_a_remote_the_sweep_cannot_name_is_refused_rather_than_guessed(estate: Estate) -> None:
    git(estate.local, "config", "remote.origin.url", "https://gitlab.com/AlobarQuest/example.git")

    with pytest.raises(GitError):
        read_checkout(estate.local)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/AlobarQuest/orchestrator.git", "AlobarQuest/orchestrator"),
        ("https://github.com/AlobarQuest/orchestrator", "AlobarQuest/orchestrator"),
        ("https://github.com/AlobarQuest/orchestrator.git\n", "AlobarQuest/orchestrator"),
        ("https://github.com/AlobarQuest/orchestrator/", "AlobarQuest/orchestrator"),
        # `~/.claude`'s remote, and the reason the name is read from the remote rather than from
        # the path: no path convention turns `.claude` into `claude-control-plane`.
        (
            "https://github.com/AlobarQuest/claude-control-plane.git",
            "AlobarQuest/claude-control-plane",
        ),
        ("git@github.com:AlobarQuest/FacelessTT.git", "AlobarQuest/FacelessTT"),
    ],
)
def test_the_repository_name_comes_from_the_remote(url: str, expected: str) -> None:
    assert repository_of(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/AlobarQuest/example.git",
        "/Users/devon/Projects/example",
        "https://github.com/AlobarQuest",
        "",
        "https://github.com/AlobarQuest/a/b.git",
    ],
)
def test_a_remote_that_is_not_a_github_repository_fails_closed(url: str) -> None:
    with pytest.raises(GitError):
        repository_of(url)


def test_the_git_runner_never_prompts_and_takes_no_optional_lock(estate: Estate) -> None:
    """Both matter at 07:10: a scheduled job must not block on a credential prompt, and a sweep
    reading `git status` against a tree somebody is working in must not take an index lock."""
    import activation_sweep.checkout as module

    recorded: dict[str, str] = {}
    original = module.subprocess.run

    def capture(args, **kwargs):  # type: ignore[no-untyped-def]
        recorded.update(kwargs["env"])
        return original(args, **kwargs)

    module.subprocess.run = capture  # type: ignore[assignment]
    try:
        read_checkout(estate.local, fetch=False)
    finally:
        module.subprocess.run = original  # type: ignore[assignment]

    assert recorded["GIT_TERMINAL_PROMPT"] == "0"
    assert recorded["GIT_OPTIONAL_LOCKS"] == "0"


def test_a_git_failure_never_carries_its_output_into_the_message(estate: Estate) -> None:
    """A git failure's stderr can name the remote it was talking to. Only the status escapes."""
    with pytest.raises(GitError) as error:
        run_git(estate.local, "rev-parse", "--verify", "refs/heads/does-not-exist")

    assert "does-not-exist" not in str(error.value)


def test_the_read_only_surface_is_what_the_reader_actually_uses(estate: Estate) -> None:
    """A rot check on the allowlist: a member nothing runs is a permission nobody is watching."""
    estate.land_upstream()
    used: set[str] = set()
    import activation_sweep.checkout as module

    original = module.subprocess.run

    def capture(args, **kwargs):  # type: ignore[no-untyped-def]
        used.add(args[3])
        return original(args, **kwargs)

    # `subprocess` is one module object, so the fixture's own git calls would be captured too --
    # the tree is therefore moved into position BEFORE the patch goes on.
    module.subprocess.run = capture  # type: ignore[assignment]
    try:
        read_checkout(estate.local, fetch=True)
    finally:
        module.subprocess.run = original  # type: ignore[assignment]

    assert used == set(READ_ONLY)


def test_the_one_subcommand_that_could_write_may_only_read(estate: Estate) -> None:
    """`config` is on the allowlist so the ORIGIN URL can be read unrewritten, and `git config a b`
    SETS a value -- so the name alone is not enough of a permission."""
    before = git(estate.local, "config", "--get", "user.email").strip()

    with pytest.raises(ForbiddenCommandError):
        run_git(estate.local, "config", "user.email", "moved@example.invalid")
    with pytest.raises(ForbiddenCommandError):
        run_git(estate.local, "config", "--unset", "remote.origin.url")

    assert git(estate.local, "config", "--get", "user.email").strip() == before
    assert run_git(estate.local, "config", "--get", "user.email").strip() == before


def test_the_runner_refuses_a_subcommand_before_it_reaches_git(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    import activation_sweep.checkout as module

    original = module.subprocess.run

    def capture(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(args)
        return original(args, **kwargs)

    module.subprocess.run = capture  # type: ignore[assignment]
    try:
        with pytest.raises(ForbiddenCommandError):
            run_git(tmp_path, "pull", "--ff-only")
    finally:
        module.subprocess.run = original  # type: ignore[assignment]

    assert calls == []


def test_a_git_binary_that_cannot_run_is_an_unmeasurable_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It must not escape as an `OSError` and take the whole pass down."""

    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("no git here")

    monkeypatch.setattr(subprocess, "run", explode)

    with pytest.raises(GitError):
        run_git(tmp_path, "status")
