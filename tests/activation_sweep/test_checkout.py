"""What the sweep measures, against real git trees."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from activation_sweep.binding import has_activated
from activation_sweep.checkout import (
    BEHIND,
    CONDITIONS,
    DIRTY,
    NO_SIGNATURE,
    PARKED,
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


def test_a_truncated_subject_never_ends_on_whitespace(estate: Estate) -> None:
    """The orchestrator strips every stored string, so a cut landing on a space would make the
    stored facts differ from the bytes the reference's digest was taken over -- and a later
    reader could no longer recompute one from the other."""
    estate.land_upstream("word " * 80)

    subject = read_checkout(estate.local).missing[0].subject

    assert len(subject) < 200
    assert subject == subject.strip()


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
    # Reached from a REAL read rather than a `replace()` synthetic. A parked checkout is one
    # whose comparison fields are all None, and a hand-built state carrying a feature branch
    # beside a measured `behind_by` is a shape no read produces -- so a synthetic would pass
    # this guard against a Checkout git could never hand back.
    estate.restore_tracked()
    estate.park()
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

    with pytest.raises(GitError) as error:
        read_checkout(estate.local)

    # And it SAYS what happened. `rev-parse @{u}` exits 128, so without this the operator gets
    # `git rev-parse exited 128` for a condition that has a name.
    assert "no upstream branch" in str(error.value)


def test_a_checkout_on_a_branch_with_no_upstream_is_PARKED_rather_than_unavailable(
    estate: Estate,
) -> None:
    """ACCEPTANCE 1, and the defect this increment exists for, reproduced exactly.

    `~/Projects/brain` sat on `chore/pin-code-standard-1.1` -- a feature branch never pushed --
    for six days, and the sweep reported `git rev-list exited 128`, `unavailable: true`. That was
    fail-closed and honest and the wrong category: every fact asserted below was knowable the
    whole time, and only *behind* was unanswerable.
    """
    branch = estate.park()

    state = read_checkout(estate.local)

    assert conditions_of(state) == (PARKED,)
    assert state.parked is True
    assert state.branch == branch
    assert state.default_branch == "main"
    # Measured, not merely not-crashed: the three facts the old row threw away.
    assert len(state.head) == 40
    assert state.head_committed_at.tzinfo is not None
    assert state.tracked_modifications == 0
    # And nothing was compared, so there is no comparison to report. None, never zero -- a zero
    # asserts the checkout has everything its upstream has, which nobody looked at.
    assert state.upstream is None
    assert state.behind_by is None
    assert state.ahead_by is None
    assert state.missing == ()


def test_returning_to_the_default_branch_clears_the_condition(estate: Estate) -> None:
    """ACCEPTANCE 2. The condition tracks the state rather than latching on the first sighting."""
    estate.park()
    assert conditions_of(read_checkout(estate.local)) == (PARKED,)

    estate.return_to_default()

    state = read_checkout(estate.local)
    assert conditions_of(state) == ()
    assert state.upstream == "origin/main"
    assert state.behind_by == 0


def test_a_parked_checkout_is_still_measured_for_dirt(estate: Estate) -> None:
    """Parked bounds only what was COMPARED. An uncommitted edit is still knowable, and a row
    that dropped it would hide an edit to code the machine runs behind a branch name."""
    estate.park()
    estate.modify_tracked()

    state = read_checkout(estate.local)

    assert conditions_of(state) == (PARKED, DIRTY)
    assert state.tracked_modifications == 1


def test_a_detached_head_is_parked(estate: Estate) -> None:
    """`rev-parse --abbrev-ref HEAD` answers the literal `HEAD` when detached, which is not the
    default branch and is parked for the same reason a feature branch is: what the working copy
    holds is not what landed."""
    git(estate.local, "checkout", "-q", "--detach", "HEAD")

    state = read_checkout(estate.local)

    assert state.branch == "HEAD"
    assert conditions_of(state) == (PARKED,)


def test_parked_and_unavailable_are_DIFFERENT_ANSWERS_rather_than_one_renamed(
    estate: Estate, tmp_path: Path
) -> None:
    """THE CONTROL THAT MAKES THE SPLIT REAL, and it has to be the pair.

    A change that merely renamed `unavailable` to `parked` would satisfy every parked assertion
    in this file. What distinguishes them is that a genuinely unreadable checkout still raises,
    on the same run, with `unavailable` keeping its meaning: the checkout could not be read at
    all. So both halves are measured here rather than in two files.
    """
    estate.park()
    parked = read_checkout(estate.local)
    assert conditions_of(parked) == (PARKED,)

    for unreadable in (tmp_path, estate.local / "does-not-exist"):
        with pytest.raises(GitError):
            read_checkout(unreadable)


def test_a_checkout_whose_default_branch_is_unknowable_is_unavailable(estate: Estate) -> None:
    """The one new unavailable trigger this increment introduces, named rather than discovered.

    There is no read-only way to ask the remote which branch is default -- `ls-remote` is network
    and off the allowlist, `symbolic-ref` is off it too -- so with `origin/HEAD` unset the sweep
    cannot tell parked from not-parked. Guessing `main` would report every checkout of a `master`
    repository as parked and a genuinely parked one as fine whenever it sat on `main`.

    The message carries the repair, because the operator reading it at 07:10 otherwise has to
    work out that a symbolic ref is missing from a row that says a checkout is unreadable.
    """
    estate.forget_the_default_branch()

    with pytest.raises(GitError) as error:
        read_checkout(estate.local)

    assert "origin/HEAD" in str(error.value)
    assert "git remote set-head origin -a" in str(error.value)


def test_the_absent_default_branch_read_is_refused_on_STATUS_not_on_stdout(estate: Estate) -> None:
    """`rev-parse --abbrev-ref origin/HEAD` prints the literal string `origin/HEAD` on STDOUT and
    exits 128 when the ref is absent -- measured 2026-08-24. A reader that trusted the output
    would take `origin/HEAD` for a branch name and classify EVERY checkout as parked, including
    the six that are fine. The behaviour is pinned here because it is git's, not this program's,
    and a git that stopped doing it would make the guard below untested rather than unnecessary.
    """
    estate.forget_the_default_branch()

    completed = subprocess.run(
        ["git", "-C", str(estate.local), "rev-parse", "--abbrev-ref", "origin/HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert completed.stdout.strip() == "origin/HEAD"


def test_a_default_branch_ref_that_names_nothing_on_origin_is_refused_rather_than_stripped(
    estate: Estate,
) -> None:
    """FOUND BY MUTATION, not by reading: deleting this refusal left the whole suite green.

    `--abbrev-ref` answers a BARE name when `origin/HEAD` is a symbolic ref to something outside
    `refs/remotes/origin/` -- `main` for a local branch, `v1` for a tag. Blind-stripping seven
    characters off `v1` yields nonsense and reports every checkout as parked; blind-RETURNING it
    reports the default branch as `v1` and does the same. Both are silent, both are wrong in the
    direction that makes the control fire on healthy repositories.

    The tag is what makes this discriminate. A symbolic ref to the LOCAL `main` answers `main`,
    which a reader that skipped the check would return unchanged and be accidentally right --
    so a control written with that shape passes either way. The sweep cannot tell a bare branch
    name from a bare tag name by looking, which is why it refuses both rather than accepting the
    one that would usually be harmless.
    """
    estate.point_the_default_branch_ref_outside_origin()

    with pytest.raises(GitError) as error:
        read_checkout(estate.local)

    assert "does not name a branch on origin" in str(error.value)


def test_a_checkout_ON_its_default_branch_with_no_upstream_stays_unavailable(
    estate: Estate,
) -> None:
    """The boundary, from the other side. Parked means "not on the default branch"; a checkout
    that IS on it with no upstream has a comparison to make and no way to make it, which is what
    unavailable means. Widening parked to cover it would be inventing a third finding class
    nobody decided -- the `ahead` precedent, one condition over.
    """
    git(estate.local, "branch", "--unset-upstream")

    with pytest.raises(GitError) as error:
        read_checkout(estate.local)

    assert "no upstream branch" in str(error.value)


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
    """A git failure's stderr can name the REMOTE it was talking to. Only the status escapes.

    The control has to be a FETCH. An earlier version failed a `rev-parse` on a missing ref and
    asserted the ref name was absent -- but git's message there is `fatal: Needed a single
    revision`, which never contains it, so the assertion held whether or not stderr was being
    interpolated. A failing fetch is the case the docstring names and the only one that
    discriminates: it writes the remote URL into stderr.
    """
    git(estate.local, "config", "remote.origin.url", "https://github.invalid/AlobarQuest/nope.git")
    git(estate.local, "config", "--unset-all", f"url.{estate.origin}.insteadOf")

    with pytest.raises(GitError) as error:
        run_git(estate.local, "fetch", "--quiet", timeout=60)

    message = str(error.value)
    assert "github.invalid" not in message
    assert "AlobarQuest/nope" not in message
    assert "git fetch exited" in message


def test_every_git_log_read_refuses_the_operators_signature_setting(estate: Estate) -> None:
    """`log.showSignature = true` in the operator's GLOBAL config makes `git log` print three
    `gpg:` lines PER COMMIT onto stdout ahead of the format string. Measured on this machine
    against a signed squash merge, the two-line HEAD read becomes five lines and the
    missing-commits read becomes forty entries whose commit is the literal `gpg:`.

    No hermetic control can reproduce it -- the fixtures scrub the global config, correctly, and
    these commits are unsigned -- so what is pinned is the SHAPE: every `git log` this program
    issues carries the flag, and a new call site that forgets it reds here.
    """
    estate.land_upstream()
    reads: list[tuple[str, ...]] = []
    import activation_sweep.checkout as module

    original = module.subprocess.run

    def capture(args, **kwargs):  # type: ignore[no-untyped-def]
        reads.append(tuple(args))
        return original(args, **kwargs)

    module.subprocess.run = capture  # type: ignore[assignment]
    try:
        # Fetched, so the checkout is behind and BOTH `log` reads fire -- the HEAD read and the
        # missing-commits read. The second is the one that would fill `missing` with `gpg:`.
        read_checkout(estate.local, fetch=True)
    finally:
        module.subprocess.run = original  # type: ignore[assignment]

    logs = [args for args in reads if args[3] == "log"]
    assert len(logs) == 2
    assert all(NO_SIGNATURE in args for args in logs)


def test_the_read_only_surface_is_what_the_readers_actually_use(estate: Estate) -> None:
    """A rot check on the allowlist: a member nothing runs is a permission nobody is watching.

    BOTH readers, because the allowlist serves both lanes (ADR-0030). `read_checkout` uses every
    member but one; `merge-base` belongs to the unit-caused lane, which asks whether a landing
    commit is in the history a working copy holds. Checking only the first reader would have
    turned this rot check into a test that fails whenever the OTHER lane grows a subcommand --
    which is the guard reporting on the wrong reader rather than a permission going unwatched.
    """
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
        has_activated(estate.local, git(estate.local, "rev-parse", "HEAD").strip())
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
