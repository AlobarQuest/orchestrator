"""A public function in kernel/ or services/ that no production entry point can reach.

WS-P2.1 shipped `upsert_pr_binding` with no production caller. Every call site in the approved
plan was a test fixture. The route that finally called it (`routes.py`, commit c4be95d) WAS the
fix -- before it, the function was unreachable from every entry point, and ten green unit tests
said nothing. An obligation nobody is forced to discharge is not a guarantee; a guard nobody
calls is not protection. Both are indistinguishable from a healthy system from the outside.

So this test asks the question no other test in the repo asks: *can production actually get
here?*

THE PREDICATE IS THE WHOLE DESIGN. Two wrong ones were prototyped first, and both looked fine:

1. REFERENCE-COUNTING ("referenced nowhere outside its defining module") flags 11 of 93 --
   eight of them public helpers whose callers live in the same module. Their allowlist entries
   would have read "in fact it is called", which is the predicate being WRONG masquerading as an
   exemption being justified. That is how an allowlist rots into a rule that guards nothing.

2. NAME-KEYED REACHABILITY flags 3 of 93 and looks excellent. It is blind to twelve of its own
   subjects. `cli.py` is a pure HTTP client -- it imports zero services and reaches them only
   over HTTP -- but its typer commands are named identically to the services they proxy
   (`cli.py::dead_letter` vs `services/dead_letter.py::dead_letter`). Seeding roots by NAME
   marks the SERVICE reachable by the mere existence of its CLI command. Delete the dead_letter
   route entirely -- the WS-P2.1 defect, reconstructed -- and a name-keyed graph does not
   notice. `test_the_guard_flags_a_service_whose_only_production_caller_was_removed` is that
   control, and it is the reason this file resolves imports.

So: nodes are import-resolved `(module, symbol)` pairs, roots are NODES rather than names, and
only reachable nodes propagate reachability -- which is what refuses to launder a dead callee
behind a dead caller.

WHAT THIS GUARD DOES NOT CATCH, stated plainly, because a guard with an unstated blind spot is
this file's own subject:

  * EXTERNAL -- a reachable endpoint that no *client* ever calls. The pr-binding route today is
    reachable from `routes.py` and still has no worker calling it. An intra-repo call graph
    cannot see that, and no tightening will make it. That is WS-P2.16's problem, and a drill's.
  * SEMANTIC -- reachable, called, and wrong. `upsert_pr_binding` also flushed without
    committing. That is the commit/re-read discipline's problem.

The WS-P2.1 defect produced two failures. This guard covers one of them.
"""

import ast
from pathlib import Path

import pytest

SRC = Path("src")

# The surfaces the outside world can actually enter through. Everything defined in them is a
# root. `web.py` is the human /review lane; `cli.py` an operator's client; `main.py` the app
# factory. `reconciliation_runner` is a separate program (ADR-0002) with its own CLI, so it is
# its own root set -- its code is not dead merely because the orchestrator does not call it.
ENTRY_MODULES = {
    "orchestrator/api/routes.py",
    "orchestrator/api/health.py",
    "orchestrator/web.py",
    "orchestrator/cli.py",
    "orchestrator/main.py",
}
ENTRY_PACKAGES = ("reconciliation_runner/",)

# The subject: the layers where a guard would plausibly be written and forgotten.
SCOPE = ("orchestrator/kernel/", "orchestrator/services/")

ALLOWLIST: dict[tuple[str, str], str] = {
    ("orchestrator.services.github_app", "reset_token_providers"): (
        "A deliberate test-isolation seam, not a forgotten guard. github_app.py holds a "
        "process-lifetime _PROVIDERS cache BY DESIGN (an installation token is expensive to "
        "mint), and this drops it. There is no production moment at which dropping that cache "
        "is correct -- so it is unreachable on purpose. Deleting it makes the suite "
        "order-dependent: tests/api/test_dispatch_api.py relies on it to isolate token state."
    ),
}


def _under(path: Path, root: Path, prefixes: tuple[str, ...]) -> bool:
    rel = str(path.relative_to(root))
    return any(rel.startswith(prefix) for prefix in prefixes)


class CallGraph:
    """An import-resolved call graph over `src/`.

    Nodes are `(module, symbol)`. A call is an edge only when the callee resolves through the
    calling module's own import map -- which is what stops a name collision from marking a dead
    function live.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files = sorted(root.rglob("*.py"))
        self.defs: dict[tuple[str, str], ast.AST] = {}
        self.imports: dict[str, dict[str, tuple[str, str]]] = {}
        self.module_aliases: dict[str, dict[str, str]] = {}
        for path in self.files:
            module = self._module(path)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            self.imports[module] = {}
            self.module_aliases[module] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    self.defs[(module, node.name)] = node
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    for alias in node.names:
                        self.imports[module][alias.asname or alias.name] = (
                            node.module,
                            alias.name,
                        )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.module_aliases[module][alias.asname or alias.name] = alias.name

    def _module(self, path: Path) -> str:
        return str(path.relative_to(self.root).with_suffix("")).replace("/", ".")

    def _resolve(self, module: str, name: str) -> tuple[str, str] | None:
        imported = self.imports[module].get(name)
        if imported is not None:
            return imported if imported in self.defs else None
        return (module, name) if (module, name) in self.defs else None

    def _edges(self, module: str, node: ast.AST) -> set[tuple[str, str]]:
        edges: set[tuple[str, str]] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                # `module.func()` -- an edge only via an imported MODULE alias. A bare attribute
                # (`obj.method()`) is NOT an edge: we cannot know the receiver's type, and
                # guessing by method name is exactly the name-collision bug this class avoids.
                value = child.func.value
                if isinstance(value, ast.Name):
                    target = self.module_aliases[module].get(value.id)
                    if target and (target, child.func.attr) in self.defs:
                        edges.add((target, child.func.attr))
            elif isinstance(child, ast.Name):
                # Covers both `f()` and a bare reference passing `f` as a callback -- the
                # `after=` lifecycle hook is a real edge and must not be missed.
                resolved = self._resolve(module, child.id)
                if resolved is not None:
                    edges.add(resolved)
        return edges

    def roots(self) -> set[tuple[str, str]]:
        return {
            (self._module(path), name)
            for path in self.files
            if str(path.relative_to(self.root)) in ENTRY_MODULES
            or _under(path, self.root, ENTRY_PACKAGES)
            for (module, name) in self.defs
            if module == self._module(path)
        }

    def reachable(self) -> set[tuple[str, str]]:
        seen: set[tuple[str, str]] = set()
        frontier = list(self.roots())
        while frontier:
            node_id = frontier.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            definition = self.defs.get(node_id)
            if definition is None:
                continue
            frontier.extend(self._edges(node_id[0], definition) - seen)
        return seen

    def public_targets(self) -> list[tuple[str, str, str]]:
        """Public top-level functions in the guarded layers, with their location."""
        targets: list[tuple[str, str, str]] = []
        for path in self.files:
            if not _under(path, self.root, SCOPE):
                continue
            module = self._module(path)
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(
                    node, ast.FunctionDef | ast.AsyncFunctionDef
                ) and not node.name.startswith("_"):
                    rel = str(path.relative_to(self.root))
                    targets.append((module, node.name, f"{rel}:{node.lineno}"))
        return targets

    def unreachable(self) -> list[tuple[str, str, str]]:
        reachable = self.reachable()
        return [
            (module, name, where)
            for module, name, where in self.public_targets()
            if (module, name) not in reachable
        ]


@pytest.fixture(scope="module")
def graph() -> CallGraph:
    return CallGraph(SRC)


def test_every_public_kernel_and_service_function_is_reachable(graph: CallGraph) -> None:
    offenders = [
        f"{module}.{name} ({where})"
        for module, name, where in graph.unreachable()
        if (module, name) not in ALLOWLIST
    ]
    assert not offenders, (
        "these public functions cannot be reached from any production entry point:\n  "
        + "\n  ".join(offenders)
        + "\n\nA guard nobody calls is not protection. Wire it to a caller, make it private, or "
        "delete it. Add it to ALLOWLIST only if it is unreachable ON PURPOSE -- and if the "
        "justification you are about to write is 'in fact it is called', the predicate is "
        "wrong and the bug is in this file, not in the code it flagged."
    )


def test_every_allowlist_entry_is_still_unreachable(graph: CallGraph) -> None:
    """An exemption nobody needs is an exemption nobody is watching."""
    unreachable = {(module, name) for module, name, _ in graph.unreachable()}
    stale = [
        f"{module}.{name}" for (module, name) in ALLOWLIST if (module, name) not in unreachable
    ]
    assert not stale, (
        f"these symbols are allowlisted as unreachable but now have a production caller: {stale}. "
        "Remove the exemption."
    )


def test_every_allowlist_entry_carries_a_justification() -> None:
    empty = [
        f"{module}.{name}" for (module, name), why in ALLOWLIST.items() if len(why.strip()) < 40
    ]
    assert not empty, f"these allowlist entries have no real justification: {empty}"


# -------------------------------------------------------------------------------------------
# The guard must be shown to fail. A guard that cannot fail is decoration.
#
# Three controls, in ascending order of what they actually prove. The first two are passed by
# every candidate predicate, including the two wrong ones. The THIRD is the one that matters --
# it is the WS-P2.1 defect reconstructed, and a name-keyed graph does not catch it.
# -------------------------------------------------------------------------------------------


def _write(tmp: Path, rel: str, body: str) -> None:
    path = tmp / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def fake_src(tmp_path: Path) -> Path:
    """A miniature src/ tree: one route module (a root) and one service module."""
    root = tmp_path / "src"
    _write(root, "orchestrator/api/health.py", "def live() -> str:\n    return 'ok'\n")
    _write(root, "orchestrator/cli.py", "def noop() -> None:\n    pass\n")
    _write(root, "orchestrator/web.py", "def page() -> None:\n    pass\n")
    _write(root, "orchestrator/main.py", "def app() -> None:\n    pass\n")
    return root


def test_the_guard_flags_an_isolated_unreachable_function(fake_src: Path) -> None:
    _write(fake_src, "orchestrator/services/svc.py", "def orphan() -> None:\n    pass\n")
    _write(
        fake_src,
        "orchestrator/api/routes.py",
        "def index() -> None:\n    pass\n",
    )
    flagged = {name for _module, name, _where in CallGraph(fake_src).unreachable()}
    assert "orphan" in flagged


def test_the_guard_does_not_flag_a_function_a_route_actually_calls(fake_src: Path) -> None:
    _write(fake_src, "orchestrator/services/svc.py", "def live_one() -> None:\n    pass\n")
    _write(
        fake_src,
        "orchestrator/api/routes.py",
        "from orchestrator.services.svc import live_one\n\n\n"
        "def index() -> None:\n    live_one()\n",
    )
    flagged = {name for _module, name, _where in CallGraph(fake_src).unreachable()}
    assert "live_one" not in flagged


def test_the_guard_flags_a_function_laundered_by_a_dead_cross_module_caller(
    fake_src: Path,
) -> None:
    """A reference-counting predicate passes this. Reachability must not.

    `laundered` IS referenced -- from another module -- but its only caller is itself dead.
    Counting references cannot tell that apart from a live call.
    """
    _write(fake_src, "orchestrator/services/svc.py", "def laundered() -> None:\n    pass\n")
    _write(
        fake_src,
        "orchestrator/services/other.py",
        "from orchestrator.services.svc import laundered\n\n\n"
        "def also_dead() -> None:\n    laundered()\n",
    )
    _write(fake_src, "orchestrator/api/routes.py", "def index() -> None:\n    pass\n")
    flagged = {name for _module, name, _where in CallGraph(fake_src).unreachable()}
    assert "laundered" in flagged
    assert "also_dead" in flagged


def test_the_guard_flags_a_dead_function_whose_name_collides_with_a_live_one(
    fake_src: Path,
) -> None:
    """The case the name-keyed prototype failed, and the reason this graph resolves imports.

    `cli.py` is a pure HTTP client whose commands share their names with the services they
    proxy. A name-keyed graph marks the SERVICE reachable because the CLI COMMAND is a root.
    Here: the CLI defines `dead_letter` and calls nothing; the service `dead_letter` is dead.
    """
    _write(
        fake_src,
        "orchestrator/services/dead_letter.py",
        "def dead_letter() -> None:\n    pass\n",
    )
    _write(
        fake_src,
        "orchestrator/cli.py",
        "def dead_letter() -> None:\n    _http_get('/api/v1/dead-letter')\n\n\n"
        "def _http_get(path: str) -> None:\n    pass\n",
    )
    _write(fake_src, "orchestrator/api/routes.py", "def index() -> None:\n    pass\n")
    flagged = {(module, name) for module, name, _where in CallGraph(fake_src).unreachable()}
    assert ("orchestrator.services.dead_letter", "dead_letter") in flagged, (
        "the service function is dead; only the identically-named CLI command is a root. "
        "A name-keyed graph misses this -- which is exactly how it stayed blind to 12 of 93 "
        "real symbols."
    )


def test_the_guard_flags_a_service_whose_only_production_caller_was_removed(
    fake_src: Path,
) -> None:
    """THE control. This is the WS-P2.1 defect, reconstructed.

    A service is reachable only because one route calls it. Delete that route -- exactly what
    had NOT yet happened to `upsert_pr_binding` before commit c4be95d -- and the service is
    dead. If this test can be made to pass with a name-keyed graph, the guard is decoration.
    """
    _write(
        fake_src,
        "orchestrator/services/dead_letter.py",
        "def dead_letter() -> None:\n    pass\n",
    )
    # WITH the route: reachable.
    _write(
        fake_src,
        "orchestrator/api/routes.py",
        "from orchestrator.services.dead_letter import dead_letter\n\n\n"
        "def dead_letter_route() -> None:\n    dead_letter()\n",
    )
    before = {name for _module, name, _where in CallGraph(fake_src).unreachable()}
    assert "dead_letter" not in before, "precondition: the route makes it reachable"

    # Remove the ONLY production caller. Nothing else changes.
    _write(fake_src, "orchestrator/api/routes.py", "def index() -> None:\n    pass\n")
    after = {name for _module, name, _where in CallGraph(fake_src).unreachable()}
    assert "dead_letter" in after, (
        "removing a service's only production caller must make the guard flag it. This is the "
        "WS-P2.1 shape: the route WAS the fix (c4be95d); before it, the service was dead while "
        "its unit tests were green."
    )
