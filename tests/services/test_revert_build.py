"""Building a revert as the exact inverse of a squash merge (Landing Part 3a).

Every case runs against a recording double behind `RevertBuildGateway`: trees are answered per
commit as `path -> TreeEntry` maps, and every write is recorded. The review-focus case this file
pins: a revert never removes more than the bump, so a path the bump touched that changed on the
default branch after it landed is an overlap and nothing is built.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest

from orchestrator.services.landing.interfaces import (
    BranchHead,
    EstateGatewayError,
    Tree,
    TreeChange,
    TreeEntry,
)

# Private until Task 5 gives them a production caller (test_unreachable_guards).
from orchestrator.services.landing.revert_build import (
    REVERT_BRANCH_PREFIX,
    Overlap,
    RevertOf,
    RevertPlan,
    is_exact_inverse,
    open_revert,
    plan_revert,
)

REPOSITORY = "alobarquest/veritok"
PARENT = "1111111111111111111111111111111111111111"
MERGE = "2222222222222222222222222222222222222222"
HEAD = "3333333333333333333333333333333333333333"
REVERT = "4444444444444444444444444444444444444444"

OLD_LOCK = TreeEntry(mode="100644", type="blob", sha="a" * 40)
NEW_LOCK = TreeEntry(mode="100644", type="blob", sha="b" * 40)
OLD_TOML = TreeEntry(mode="100644", type="blob", sha="c" * 40)
NEW_TOML = TreeEntry(mode="100644", type="blob", sha="d" * 40)
README = TreeEntry(mode="100644", type="blob", sha="e" * 40)
SCRIPT = TreeEntry(mode="100755", type="blob", sha="f" * 40)
ADDED = TreeEntry(mode="100644", type="blob", sha="9" * 40)

PARENT_TREE = {"uv.lock": OLD_LOCK, "pyproject.toml": OLD_TOML, "README.md": README}
MERGE_TREE = {"uv.lock": NEW_LOCK, "pyproject.toml": NEW_TOML, "README.md": README}


class _Gateway:
    """Answers trees per commit and the default branch's head; records every write."""

    def __init__(
        self, trees: dict[str, dict[str, TreeEntry]], *, head: str = HEAD, broken: str = ""
    ) -> None:
        self._trees = trees
        self._head = head
        self._broken = broken
        self.writes: list[tuple[str, object]] = []

    def default_branch_head(self, *, repository: str) -> BranchHead:
        assert repository == REPOSITORY
        return BranchHead(name="main", sha=self._head)

    def tree(self, *, repository: str, commit_sha: str) -> Tree:
        assert repository == REPOSITORY
        if commit_sha == self._broken:
            raise EstateGatewayError("read_status", 502)
        return Tree(sha=f"tree-of-{commit_sha}", entries=dict(self._trees[commit_sha]))

    def create_tree(self, *, repository: str, base_tree: str, changes: Sequence[TreeChange]) -> str:
        self.writes.append(("tree", (base_tree, tuple(changes))))
        return "new-tree"

    def create_commit(self, *, repository: str, message: str, tree: str, parent: str) -> str:
        self.writes.append(("commit", (message, tree, parent)))
        return REVERT

    def create_branch(self, *, repository: str, branch: str, sha: str) -> None:
        self.writes.append(("branch", (branch, sha)))

    def open_pull_request(
        self, *, repository: str, title: str, body: str, head: str, base: str
    ) -> int:
        self.writes.append(("pull", (title, body, head, base)))
        return 77


def _plan(gateway: _Gateway) -> RevertPlan:
    plan = plan_revert(gateway, REPOSITORY, MERGE, PARENT)
    assert isinstance(plan, RevertPlan)
    return plan


def test_the_inverse_restores_each_touched_path_from_the_parent() -> None:
    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: MERGE_TREE, HEAD: MERGE_TREE})

    plan = _plan(gateway)

    assert plan.base_sha == HEAD
    assert plan.base_ref == "main"
    assert plan.base_tree_sha == f"tree-of-{HEAD}"
    assert plan.merge_sha == MERGE
    assert plan.changes == (
        TreeChange(path="pyproject.toml", mode="100644", type="blob", sha=OLD_TOML.sha),
        TreeChange(path="uv.lock", mode="100644", type="blob", sha=OLD_LOCK.sha),
    )


def test_a_file_the_bump_added_is_deleted() -> None:
    merged = {**MERGE_TREE, "requirements/extra.txt": ADDED}
    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: merged, HEAD: merged})

    plan = _plan(gateway)

    assert TreeChange(path="requirements/extra.txt", mode="100644", type="blob", sha=None) in (
        plan.changes
    )


def test_a_rename_restores_the_old_path_and_deletes_the_new_one() -> None:
    parent = {**PARENT_TREE, "old.txt": README}
    merged = {**MERGE_TREE, "new.txt": README}
    gateway = _Gateway({PARENT: parent, MERGE: merged, HEAD: merged})

    plan = _plan(gateway)

    paths = {change.path: change.sha for change in plan.changes}
    assert paths["old.txt"] == README.sha
    assert paths["new.txt"] is None


def test_a_mode_change_alone_is_touched_and_its_mode_restored() -> None:
    """An executable bit is part of the entry: the inverse restores the parent's mode."""
    parent = {**PARENT_TREE, "run.sh": TreeEntry(mode="100644", type="blob", sha=SCRIPT.sha)}
    merged = {**MERGE_TREE, "run.sh": SCRIPT}
    gateway = _Gateway({PARENT: parent, MERGE: merged, HEAD: merged})

    plan = _plan(gateway)

    assert TreeChange(path="run.sh", mode="100644", type="blob", sha=SCRIPT.sha) in plan.changes


def test_a_submodule_keeps_its_type() -> None:
    old = TreeEntry(mode="160000", type="commit", sha="5" * 40)
    new = TreeEntry(mode="160000", type="commit", sha="6" * 40)
    gateway = _Gateway(
        {PARENT: {"vendor/lib": old}, MERGE: {"vendor/lib": new}, HEAD: {"vendor/lib": new}}
    )

    plan = _plan(gateway)

    assert plan.changes == (
        TreeChange(path="vendor/lib", mode="160000", type="commit", sha=old.sha),
    )


def test_a_path_changed_since_the_landing_is_an_overlap() -> None:
    moved = {**MERGE_TREE, "uv.lock": TreeEntry(mode="100644", type="blob", sha="7" * 40)}
    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: MERGE_TREE, HEAD: moved})

    assert plan_revert(gateway, REPOSITORY, MERGE, PARENT) == Overlap(paths=("uv.lock",))
    assert gateway.writes == []


def test_a_touched_path_deleted_since_the_landing_is_an_overlap() -> None:
    gone = {"pyproject.toml": NEW_TOML, "README.md": README}
    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: MERGE_TREE, HEAD: gone})

    assert plan_revert(gateway, REPOSITORY, MERGE, PARENT) == Overlap(paths=("uv.lock",))


def test_a_change_on_top_to_an_untouched_path_is_not_an_overlap() -> None:
    later = {**MERGE_TREE, "README.md": ADDED, "docs/new.md": ADDED}
    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: MERGE_TREE, HEAD: later})

    plan = _plan(gateway)

    assert {change.path for change in plan.changes} == {"uv.lock", "pyproject.toml"}


def test_a_merge_that_touched_nothing_is_refused() -> None:
    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: PARENT_TREE, HEAD: PARENT_TREE})

    with pytest.raises(EstateGatewayError) as raised:
        plan_revert(gateway, REPOSITORY, MERGE, PARENT)
    assert raised.value.code == "revert_touched_nothing"


def test_an_unreadable_tree_raises_rather_than_planning() -> None:
    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: MERGE_TREE, HEAD: MERGE_TREE}, broken=HEAD)

    with pytest.raises(EstateGatewayError):
        plan_revert(gateway, REPOSITORY, MERGE, PARENT)


def test_opening_the_revert_writes_tree_commit_branch_and_pull_request_in_order() -> None:
    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: MERGE_TREE, HEAD: MERGE_TREE})
    plan = _plan(gateway)
    original = RevertOf(
        number=12, title="Bump httpx from 0.27.0 to 0.28.0", failed_job_names=("lint", "test")
    )

    assert open_revert(gateway, REPOSITORY, plan, original) == (77, REVERT)

    title = 'Revert "Bump httpx from 0.27.0 to 0.28.0"'
    body = f"Reverts #12: lint, test failed on {MERGE}."
    assert gateway.writes == [
        ("tree", (f"tree-of-{HEAD}", plan.changes)),
        ("commit", (f"{title}\n\n{body}", "new-tree", HEAD)),
        ("branch", (f"{REVERT_BRANCH_PREFIX}pr-12", REVERT)),
        ("pull", (title, body, f"{REVERT_BRANCH_PREFIX}pr-12", "main")),
    ]
    assert REVERT_BRANCH_PREFIX == "sds-revert/"


# The revert's head tree as `open_revert` would have built it: the parent's entries on the touched
# paths, everything else as the base has it.
REVERTED_TREE = {"uv.lock": OLD_LOCK, "pyproject.toml": OLD_TOML, "README.md": README}


def _inverse(
    revert_tree: dict[str, TreeEntry], base_tree: dict[str, TreeEntry] = MERGE_TREE
) -> bool:
    gateway = _Gateway(
        {PARENT: PARENT_TREE, MERGE: MERGE_TREE, HEAD: base_tree, REVERT: revert_tree}
    )
    return is_exact_inverse(gateway, REPOSITORY, REVERT, HEAD, MERGE, PARENT)


def test_the_exact_inverse_check_accepts_the_built_revert() -> None:
    assert _inverse(REVERTED_TREE) is True


def test_the_exact_inverse_check_accepts_a_revert_brought_current_with_an_unrelated_change() -> (
    None
):
    """After a branch update the base has moved on paths the bump never touched."""
    later = {**MERGE_TREE, "docs/new.md": ADDED}
    assert _inverse({**REVERTED_TREE, "docs/new.md": ADDED}, later) is True


def test_the_exact_inverse_check_refuses_an_extra_path() -> None:
    assert _inverse({**REVERTED_TREE, "README.md": ADDED}) is False
    assert _inverse({**REVERTED_TREE, "extra.txt": ADDED}) is False


def test_the_exact_inverse_check_refuses_a_different_blob() -> None:
    assert _inverse({**REVERTED_TREE, "uv.lock": ADDED}) is False


def test_the_exact_inverse_check_refuses_a_different_mode() -> None:
    assert (
        _inverse({**REVERTED_TREE, "uv.lock": TreeEntry("100755", "blob", OLD_LOCK.sha)}) is False
    )


def test_the_exact_inverse_check_refuses_a_missing_path() -> None:
    """A revert that leaves one touched path as the bump made it is not the inverse."""
    assert _inverse({**REVERTED_TREE, "uv.lock": NEW_LOCK}) is False


def test_the_exact_inverse_check_requires_a_file_the_bump_added_to_be_gone() -> None:
    merged = {**MERGE_TREE, "extra.txt": ADDED}
    gateway = _Gateway(
        {
            PARENT: PARENT_TREE,
            MERGE: merged,
            HEAD: merged,
            REVERT: {**REVERTED_TREE, "extra.txt": ADDED},
        }
    )
    assert is_exact_inverse(gateway, REPOSITORY, REVERT, HEAD, MERGE, PARENT) is False

    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: merged, HEAD: merged, REVERT: REVERTED_TREE})
    assert is_exact_inverse(gateway, REPOSITORY, REVERT, HEAD, MERGE, PARENT) is True


def test_the_exact_inverse_check_raises_on_an_unreadable_tree() -> None:
    gateway = _Gateway(
        {PARENT: PARENT_TREE, MERGE: MERGE_TREE, HEAD: MERGE_TREE, REVERT: REVERTED_TREE},
        broken=REVERT,
    )
    with pytest.raises(EstateGatewayError):
        is_exact_inverse(gateway, REPOSITORY, REVERT, HEAD, MERGE, PARENT)


def test_the_exact_inverse_check_refuses_when_the_base_moved_a_touched_path() -> None:
    """main changed uv.lock after the landing, and the revert was hand-resolved back to the
    parent's entry: landing it would discard main's change, so it is the overlap, refused."""
    moved = {**MERGE_TREE, "uv.lock": ADDED}
    assert _inverse(REVERTED_TREE, moved) is False


def _written(gateway: _Gateway, kind: str) -> tuple[str, ...]:
    """The one write of this kind, as the strings it sent."""
    (sent,) = [cast(tuple[str, ...], args) for name, args in gateway.writes if name == kind]
    return sent


def _original(title: str = "Bump httpx", names: tuple[str, ...] = ("test",)) -> RevertOf:
    return RevertOf(number=12, title=title, failed_job_names=names)


def test_a_long_title_is_shortened_to_githubs_limit_before_anything_is_written() -> None:
    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: MERGE_TREE, HEAD: MERGE_TREE})
    original = _original(title="x" * 300)

    open_revert(gateway, REPOSITORY, _plan(gateway), original)

    message = _written(gateway, "commit")[0]
    title = _written(gateway, "pull")[0]
    assert len(title) == 256
    assert title.startswith('Revert "xxx') and title.endswith('\u2026"')
    assert message.split("\n")[0] == title


def test_a_title_at_the_limit_is_kept_whole() -> None:
    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: MERGE_TREE, HEAD: MERGE_TREE})

    open_revert(gateway, REPOSITORY, _plan(gateway), _original(title="y" * 247))

    title = _written(gateway, "pull")[0]
    assert title == 'Revert "' + "y" * 247 + '"'


def test_a_revert_naming_no_failed_job_is_refused_before_any_write() -> None:
    gateway = _Gateway({PARENT: PARENT_TREE, MERGE: MERGE_TREE, HEAD: MERGE_TREE})
    plan = _plan(gateway)

    with pytest.raises(EstateGatewayError) as raised:
        open_revert(gateway, REPOSITORY, plan, _original(names=()))
    assert raised.value.code == "revert_names_no_failed_job"
    assert gateway.writes == []
