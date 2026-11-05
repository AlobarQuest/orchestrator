"""A function that commits never calls, before its own last commit, a function that commits.

The repository rule (CLAUDE.md, "Request entry points own their transaction"): entry points own
their transaction and commit; a function called inside another transaction never commits. A
nested commit ends the caller's transaction early. The work the caller did before the call is
committed with no guarantee that the rest will follow, and a rollback after it restores nothing.

THE PREDICATE. Over the import-resolved call graph `test_unreachable_guards.CallGraph` builds:

  * a DIRECT committer is a function in `orchestrator.` whose body calls `session.commit()`;
  * an EFFECTIVE committer is a direct committer, or a function that references an effective
    committer (the transitive closure, so a non-committing helper that reaches a commit counts);
  * a direct committer must not reference an effective committer other than itself on a line
    before its own last `session.commit()`.

A reference after the caller's last commit is allowed. It is sequential, not nested: the caller's
transaction is already committed. That is the shape of `record_named_check_evidence`, which commits
only on its replay branch and otherwise ends with `return append_verifier_evidence(...)`, handing
its transaction to the callee. Functions that commit nothing themselves and call several committers
in sequence (`verify_work_unit`, the reconciliation detectors, some routes) are outside the
predicate on purpose: each step is its own transaction, and nothing here says they should be one.

Measured when this file was written: 51 direct committers, 110 effective, no flagged reference,
and no allowlist. The designed own-step commits (`pr_merge.land_unit_pull_request` and
`lane_act.land_pull_request` settle an unknown outcome and commit before the ordinary path) are two
commits in ONE function, so the predicate never sees them as nesting.

WHAT THIS GUARD DOES NOT CATCH:

  * ORDER IS BY LINE. A commit at the top of a loop body with the call at its bottom reads as
    "call after commit" although the call runs before the next iteration's commit. None today.
  * EDGES ARE REFERENCES, not calls, so a committer passed as a callback counts (deliberately);
    but a call through an object (`self.service.f()`) or through a module imported with
    `from package import module` is invisible. Inside `orchestrator.` the only such import is
    `main.py` importing the route modules, which commit nothing themselves.
  * Functions are keyed `(module, name)`, so two same-named functions in one module are one node.
  * The rule's first half, "entry points must commit", is not checked: about thirty routes
    delegate their commit to the service they call by design.
"""

import ast
from pathlib import Path

import pytest

from tests.architecture.test_unreachable_guards import CallGraph

SRC = Path("src")
SCOPE = "orchestrator."

Node = tuple[str, str]


def _commit_lines(function: ast.AST) -> list[int]:
    return [
        call.lineno
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "commit"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "session"
        and not call.args
    ]


def _bound_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names the function binds itself (arguments, assignments, loop and comprehension targets).

    A comprehension variable called `dependency` in `routes/intake.py` is not the route
    `dependency`; without this the graph reports a commit nesting that does not exist.
    """
    bound = {arg.arg for arg in ast.walk(function.args) if isinstance(arg, ast.arg)}
    bound |= {
        node.id
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    return bound


def _references(graph: CallGraph, node_id: Node) -> list[tuple[int, Node]]:
    module, _ = node_id
    function = graph.defs[node_id]
    assert isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
    bound = _bound_names(function)
    found: list[tuple[int, Node]] = []
    for child in ast.walk(function):
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            target = graph.module_aliases[module].get(child.value.id)
            if target and (target, child.attr) in graph.defs:
                found.append((child.lineno, (target, child.attr)))
        elif (
            isinstance(child, ast.Name)
            and isinstance(child.ctx, ast.Load)
            and child.id not in bound
        ):
            resolved = graph._resolve(module, child.id)
            if resolved is not None:
                found.append((child.lineno, resolved))
    return found


def nested_commits(graph: CallGraph) -> list[str]:
    nodes = [node_id for node_id in graph.defs if node_id[0].startswith(SCOPE)]
    references = {node_id: _references(graph, node_id) for node_id in nodes}
    direct = {node_id: lines for node_id in nodes if (lines := _commit_lines(graph.defs[node_id]))}
    effective = set(direct)
    grew = True
    while grew:
        grew = False
        for node_id in nodes:
            if node_id not in effective and any(t in effective for _, t in references[node_id]):
                effective.add(node_id)
                grew = True
    offenders: list[str] = []
    for caller, lines in sorted(direct.items()):
        last = max(lines)
        for line, callee in references[caller]:
            if callee != caller and callee in effective and line < last:
                offenders.append(
                    f"{caller[0]}.{caller[1]} line {line} reaches {callee[0]}.{callee[1]}, "
                    f"which commits, before its own commit at line {last}"
                )
    return offenders


def test_no_committing_function_reaches_a_commit_before_its_own() -> None:
    offenders = nested_commits(CallGraph(SRC))
    assert not offenders, (
        "a function that owns its transaction calls something that commits it early:\n  "
        + "\n  ".join(offenders)
        + "\n\nMake the callee transaction-neutral (flush, never commit) and let the caller "
        "commit, or call it after the caller's own commit. There is no allowlist: if the "
        "flag is wrong, the predicate is wrong."
    )


def test_the_live_tree_has_committers_to_check() -> None:
    """A guard with no subjects is green forever: a renamed `session` would empty it."""
    graph = CallGraph(SRC)
    committers = [n for n in graph.defs if n[0].startswith(SCOPE) and _commit_lines(graph.defs[n])]
    assert len(committers) >= 40


# -------------------------------------------------------------------------------------------
# Controls. The two FAIL controls prove the guard fires; the two PASS controls fix the edges of
# what it allows, so it cannot drift stricter without a test saying so.
# -------------------------------------------------------------------------------------------


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    package = tmp_path / "orchestrator"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "inner.py").write_text(
        "def store(session):\n    session.add(1)\n    session.commit()\n",
        encoding="utf-8",
    )
    return tmp_path


def _outer(tree: Path, body: str) -> list[str]:
    (tree / "orchestrator" / "outer.py").write_text(
        "from orchestrator.inner import store\n\n\n" + body, encoding="utf-8"
    )
    return nested_commits(CallGraph(tree))


def test_a_committing_call_before_the_callers_commit_is_flagged(tree: Path) -> None:
    offenders = _outer(
        tree,
        "def handle(session):\n    store(session)\n    session.add(2)\n    session.commit()\n",
    )
    assert offenders and "orchestrator.outer.handle" in offenders[0]


def test_a_helper_that_reaches_a_commit_before_the_callers_commit_is_flagged(tree: Path) -> None:
    offenders = _outer(
        tree,
        "def _helper(session):\n    store(session)\n\n\n"
        "def handle(session):\n    _helper(session)\n    session.commit()\n",
    )
    assert offenders and "reaches orchestrator.outer._helper" in offenders[0]


def test_handing_the_transaction_to_a_committer_after_an_early_commit_passes(tree: Path) -> None:
    """Today's `record_named_check_evidence` shape: commit on the replay branch, else delegate."""
    assert not _outer(
        tree,
        "def handle(session, replay):\n    if replay:\n        session.commit()\n"
        "        return replay\n    return store(session)\n",
    )


def test_a_committer_called_after_the_callers_commit_passes(tree: Path) -> None:
    assert not _outer(
        tree,
        "def handle(session):\n    session.add(2)\n    session.commit()\n    store(session)\n",
    )


def test_a_local_name_shadowing_a_committer_is_not_an_edge(tree: Path) -> None:
    assert not _outer(
        tree,
        "def handle(session, rows):\n    names = [store for store in rows]\n"
        "    session.add(names)\n    session.commit()\n",
    )
