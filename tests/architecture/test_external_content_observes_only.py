"""ADR-0039, enforced: an out-of-process producer may OBSERVE, and it may never PROPOSE.

ADR-0039 ends *"Nothing enforces this ADR."* This module is the enforcement, and Phase-3 exit
criterion 5 is ratified on it rather than on the ADR's one-time reading of eleven producers.

WHAT IS ASSERTED
================

1. **Every out-of-process program under `src/` is classified, and the population is read from
   the filesystem.** A new top-level directory holding Python that is not a row in
   `CLASSIFICATION` fails the build, so a producer cannot arrive unclassified -- and a row whose
   package has gone fails too, so the table cannot keep describing programs that no longer exist.

2. **What each program can WRITE to the orchestrator is detected from its source, and must EQUAL
   what its row permits.** Equality rather than subset: a permitted route the program never sends
   is an unused grant, the same defect as a dead config knob, and the day somebody adds the call
   it would pass unnoticed. An OBSERVE_ONLY row permits exactly `POST /api/v1/observations`, which
   is the OBSERVER role's entire write surface (`OBSERVER_WRITE_ROUTES`) and reaches no code that
   constructs a work unit.

3. **No program may send a `container_image` release binding or deployment observation**, the one
   observation shape ADR-0039 names as minting a work unit.

4. **The ADR's own measurement is pinned**: exactly three orchestrator modules construct a
   `WorkUnit`, and the observations service is not one of them and imports none of them.

HOW A WRITE IS DETECTED, AND WHY THAT PREDICATE
===============================================

The predicate is the whole design (the `test_unreachable_guards` lesson), so here is the one
chosen and the ones rejected.

*Rejected: reading each program's own `is_allowed_write`.* That is the program's statement about
itself. It is code rather than prose, but it is not what the program SENDS -- a second `httpx`
call beside the confined client would never consult it -- and five programs spell it five ways.

*Rejected: tracing `self._request("POST", path)` through each helper.* Fourteen programs, fourteen
helper shapes (`_request`, `_send`, `_post`, `_get`, `client.post`); a tracer good enough for all
of them is a type checker, and one it cannot follow fails OPEN.

*Chosen: every orchestrator path the program's code can name, resolved against the route table
the orchestrator actually serves.* Every string constant and f-string in the program -- NOT its
docstrings, which are prose about the code, and comments are not in the AST at all -- is searched
for `/api/v1`. Each hit is reduced to path segments (f-string placeholders and regex fragments
become wildcards, a regex alternation `(a|b)` is expanded) and matched against
`create_app().openapi()["paths"]` -- flattened and authoritative, where `app.routes` hides an
included router (CLAUDE.md). A wildcard may only stand where the template has a `{param}`; one
standing where the template has a word is DYNAMIC path construction and fails the test, because
the guard cannot say what it names.

**The method is over-approximated, deliberately.** A program naming a template is taken to be
able to send EVERY method the orchestrator serves on it; the method is the caller's choice and
the only thing holding a program to GET is its own code, which this predicate does not model. So
a write is "a named template on which the route table serves POST, PUT, PATCH or DELETE". That
fails closed, and it passes today with no exemption: the seven templates serving both GET and a
write are observations, the two release-artifact routes, and four that no program names. **If a
read-only reference to such a route ever appears, the fix is method-awareness in this predicate,
not an entry excusing it.**

WHAT ELSE COUNTS AS A CHANNEL TO THE ORCHESTRATOR
=================================================

* **Imports.** A program importing `orchestrator` could call a service directly and send nothing
  at all. Refused for every program, whatever its row.
* **Another program's modules.** `inert_lander` reuses `deploy_watcher.github`; `estate_lander`
  reuses `change_proposer`. A program's surface is computed over ALL of its own modules plus what
  it can execute of every other program it imports, transitively -- at SYMBOL granularity across
  that boundary. `from other.mod import NAME` charges the top-level statements NAME depends on
  through that module's own names; `import other.mod` charges the whole module; and the imported
  package's `__init__.py` is charged its import-time statements, because Python runs it. Import
  statements always run, so they are always kept, but what they import is charged only as far as
  the kept code USES the bound name -- a function that is imported and never called sends nothing.
  Every binding of a name is followed (a rebinding does not hide the first), and relative imports
  are resolved. The
  granularity is load-bearing, measured: at MODULE granularity `change_proposer` (which imports only
  the `WORK_UNIT_ID` regex from `deploy_watcher.orchestrator`) and `estate_lander` (through it)
  both read as writing `/api/v1/observations` -- a false finding whose only cure would have been an
  exemption reading "in fact it is fine", which is the predicate being wrong.
* **The orchestrator's own CLI.** `work_carrier` shells out to the `orchestrator` console script,
  and that CLI has commands that POST. Any list or tuple literal whose first element resolves to
  the orchestrator's `[project.scripts]` name is an invocation; its subcommand must resolve to a
  constant and be declared in the row's `cli`. A declared subcommand whose command function
  (with the module-level helpers it calls) reaches `request(` is a write and must also appear in
  `writes` as `cli:<name>`; one that cannot be found statically fails, because it cannot be
  verified.
* **Joins.** A served read route can be the base of a write: `f"/api/v1/things/{id}"` is a GET,
  and `…/{id}/approve` a POST. So any orchestrator path extended at run time -- `BASE + "/x"`,
  `BASE + suffix`, `f"{BASE}/x"`, `f"{BASE}{suffix}"` -- fails, whatever the base resolved to.
  An interpolation followed by prose (`f"rejected {PATH}: {status}"`) is not a join.
* **Paths that are not the orchestrator's.** Coolify also serves `/api/v1/…`, and the Todoist
  base URL ends in `/api/v1`. A fully literal hit matching no orchestrator template, and any bare
  `/api/v1` fragment or strict prefix of a template (the base-URL-join construct), must be declared
  in the row's `foreign` with the service named. Declarations are rot-checked both ways: each
  must still appear in the program, and a declared non-fragment must still match NO orchestrator
  template -- so if the orchestrator grows `/api/v1/applications`, the declaration reddens instead
  of hiding a write.

THE CONTAINER-IMAGE RULE IS ABOUT THE DEFAULT, NOT THE LITERAL
==============================================================

Both wire models (`ReleaseArtifactCommandModel`, `DeploymentObservationCommandModel`) default
`kind` to `"container_image"`, so a payload that OMITS the key mints a post-deploy unit. A guard
that searched for the string `"container_image"` would pass that payload -- guarded on field A
while the write keys on field B. So for any program that can write either release-artifact route:
the literal `"container_image"` may not appear in its code, and every dict literal carrying
`artifact_digest` or `observed_artifact_digest` (the fields that make it a binding or an
observation command) must carry a `"kind"` that resolves to `"machine_local"`. BOTH payloads,
because the service refuses an observation whose kind differs from its binding's: guarding only
the observation would leave the binding free to fix the kind for it. A program that can write the
route and has NO such dict fails as well -- a rule with nothing to inspect is not a pass.

KNOWN BLIND SPOTS
=================

Named so their absence is not mistaken for coverage:

* A path assembled from pieces that are not themselves `/api/v1`-bearing -- `"/api/" + "v1/x"`,
  `"/".join([...])`, a path read from the environment or a file -- and a join written with `%`,
  `.format`, `urljoin`, or on an attribute (`self.BASE + "/x"`) rather than a name.
* A name reached only through `getattr`, `importlib`, or a function passed in from elsewhere: the
  symbol closure follows names, not values.
* A payload built by `dict(...)`, by mutation (`payload["kind"] = ...`) or by `**` spreading, which
  the kind rule does not read; and a CLI invocation whose argv is computed rather than a literal.
* An orchestrator write whose path on another host is identical to an orchestrator template is
  charged as an orchestrator write -- that fails closed, and would need the predicate to learn
  hosts.
* `scripts/` launchers are not in the population. `run-follow-up-mint.sh`, one of the three
  internal lane actors Devon named on 2026-08-25, invokes the orchestrator CLI from a script, not
  a `src/` package, and is therefore outside this guard.
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from functools import cache
from pathlib import Path

import pytest
import typer

from orchestrator import cli as orchestrator_cli
from orchestrator.api.dependencies import OBSERVER_WRITE_ROUTES
from orchestrator.api.schemas import DeploymentObservationCommandModel, ReleaseArtifactCommandModel
from orchestrator.main import create_app
from orchestrator.persistence.models import (
    CONTAINER_IMAGE_KIND,
    MACHINE_LOCAL_KIND,
    MACHINE_LOCAL_OBSERVATION,
)

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
ORCHESTRATOR = SRC / "orchestrator"
CLI_MODULE = ORCHESTRATOR / "cli.py"

OBSERVATIONS = "/api/v1/observations"
RELEASE_BINDING = "/api/v1/work-units/{unit_id}/release-artifacts"
DEPLOYMENT_OBSERVATION = "/api/v1/release-artifacts/{binding_id}/deployment-observations"
WRITE_METHODS = frozenset({"post", "put", "patch", "delete"})
PAYLOAD_DIGEST_KEYS = frozenset({"artifact_digest", "observed_artifact_digest"})

WORK_UNIT_CONSTRUCTORS = frozenset(
    {
        "services/packages.py",
        "services/follow_ups.py",
        "services/deployment_observations.py",
    }
)


class Role(Enum):
    OBSERVE_ONLY = "observe-only producer"
    INTERNAL_LANE_ACTOR = "internal lane actor"
    REPORT_ONLY_WRITER = "report-only writer"
    NOT_AN_ORCHESTRATOR_WRITER = "not an orchestrator writer"


@dataclass(frozen=True)
class Row:
    role: Role
    reason: str
    writes: frozenset[str] = frozenset()
    cli: frozenset[str] = frozenset()
    foreign: Mapping[str, str] = field(default_factory=dict)


OBSERVE = frozenset({OBSERVATIONS})

CLASSIFICATION: dict[str, Row] = {
    # ---- OBSERVE-ONLY: the ADR-0039 shape. Writes exactly the OBSERVER surface. -------------
    "bump_proposer": Row(
        Role.OBSERVE_ONLY,
        "ADR-0039: reads a Dependabot title only to extract a semver delta; its work record goes "
        "to change-manager (ADR-0028), and to the orchestrator it only observes",
        OBSERVE,
    ),
    "deploy_watcher": Row(
        Role.OBSERVE_ONLY,
        "ADR-0022: the watcher owns outcomes by observing them; reads unit history",
        OBSERVE,
    ),
    "landing_ledger": Row(
        Role.OBSERVE_ONLY,
        "ADR-0039: carries facts.what_changed.title into an observation and nowhere else",
        OBSERVE,
    ),
    "pin_watcher": Row(
        Role.OBSERVE_ONLY,
        "ADR-0039 criterion-5 re-run 2026-09-10: observation-only",
        OBSERVE,
    ),
    "revision_watcher": Row(
        Role.OBSERVE_ONLY,
        "ADR-0039 criterion-5 re-run 2026-09-10: observation-only; also reads Coolify with a "
        "separate credential",
        OBSERVE,
        foreign={"/api/v1/applications": "Coolify's application listing (GET)"},
    ),
    "tool_installer": Row(
        Role.OBSERVE_ONLY,
        "ADR-0039 criterion-5 re-run 2026-09-10: observation-only; reads /api/v1/factory-policy",
        OBSERVE,
    ),
    # ---- INTERNAL LANE ACTORS: they act by design, under their own ADRs. --------------------
    "estate_lander": Row(
        Role.INTERNAL_LANE_ACTOR,
        "Devon 2026-08-25 ruling: estate-landing is an internal lane actor; ADR-0019 inc 5b/6 "
        "landing and branch update",
        frozenset({"/api/v1/estate-pr-merge", "/api/v1/estate-pr-branch-update"}),
    ),
    "inert_lander": Row(
        Role.INTERNAL_LANE_ACTOR,
        "ADR-0038: the orchestrator merges the cascade's subjects; landing and branch update "
        "(postdates the 2026-08-25 ruling, which does not name it)",
        frozenset({"/api/v1/inert-pr-merge", "/api/v1/inert-pr-branch-update"}),
    ),
    "work_carrier": Row(
        Role.INTERNAL_LANE_ACTOR,
        "Devon 2026-08-25 ruling: work-carrier is an internal lane actor; ADR-0027/ADR-0028 "
        "register an intake naming its approved change record, from a payload the offline "
        "emit-intake-payload command builds",
        frozenset({"/api/v1/package-intakes"}),
        cli=frozenset({"emit-intake-payload"}),
    ),
    "activation_sweep": Row(
        Role.INTERNAL_LANE_ACTOR,
        "ADR-0030: binds a machine_local release artifact and records its activation check under "
        "SYSTEM (neither mints a unit); its staleness sweep observes. Not named by the 2026-08-25 "
        "ruling",
        frozenset({OBSERVATIONS, RELEASE_BINDING, DEPLOYMENT_OBSERVATION}),
    ),
    # ---- REPORT-ONLY WRITERS: record divergence or a projection binding, never lifecycle. ----
    "reconciliation_runner": Row(
        Role.REPORT_ONLY_WRITER,
        "ADR-0002: reconciliation via a report-only runner -- observations and a detect report",
        frozenset({OBSERVATIONS, "/api/v1/reconciliation/detect"}),
    ),
    "tracker_projection_adapter": Row(
        Role.REPORT_ONLY_WRITER,
        "ADR-0003/ADR-0004: outbound projection binding and inbound divergence report; retired "
        "as an operating lane by ADR-0040",
        frozenset(
            {
                "/api/v1/work-units/{unit_id}/tracker-binding",
                "/api/v1/reconciliation/tracker-detect",
            }
        ),
        foreign={"https://api.todoist.com/api/v1": "Todoist's REST base URL"},
    ),
    # ---- NOT ORCHESTRATOR WRITERS: read the orchestrator at most. ---------------------------
    "change_proposer": Row(
        Role.NOT_AN_ORCHESTRATOR_WRITER,
        "ADR-0019 inc 4: proposes deploy change records to change-manager; the orchestrator is "
        "not its client",
    ),
    "work_watcher": Row(
        Role.NOT_AN_ORCHESTRATOR_WRITER,
        "ADR-0029: reads what a change record caused and retires the record in change-manager",
    ),
}


# ------------------------------------------------------------------------------------------------
# The route table
# ------------------------------------------------------------------------------------------------


@cache
def route_table() -> dict[str, frozenset[str]]:
    paths = create_app().openapi()["paths"]
    return {
        template: frozenset(method.lower() for method in operations)
        for template, operations in paths.items()
        if template.startswith("/api/v1")
    }


def write_templates(table: Mapping[str, frozenset[str]]) -> frozenset[str]:
    return frozenset(template for template, methods in table.items() if methods & WRITE_METHODS)


# ------------------------------------------------------------------------------------------------
# Population and closure
# ------------------------------------------------------------------------------------------------


def out_of_process_packages(src: Path) -> list[str]:
    """Every top-level directory under `src/` holding Python, other than the orchestrator."""
    return sorted(
        child.name
        for child in src.iterdir()
        if child.is_dir()
        and child.name != "orchestrator"
        and not child.name.startswith((".", "__"))
        and any(child.rglob("*.py"))
    )


@cache  # load-bearing: docstrings are excluded by id(), so every reader must share one tree
def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _module_file(src: Path, dotted: str) -> Path | None:
    parts = dotted.split(".")
    as_module = src.joinpath(*parts).with_suffix(".py")
    if as_module.is_file():
        return as_module
    as_package = src.joinpath(*parts, "__init__.py")
    return as_package if as_package.is_file() else None


def _dotted(src: Path, path: Path) -> list[str]:
    parts = list(path.relative_to(src).with_suffix("").parts)
    return parts[:-1] if parts[-1] == "__init__" else parts


def _absolute(src: Path, path: Path, node: ast.ImportFrom) -> str | None:
    """The absolute module an ImportFrom names, relative forms included."""
    if node.level == 0:
        return node.module
    package = _dotted(src, path) if path.name == "__init__.py" else _dotted(src, path)[:-1]
    base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
    if node.level - 1 > len(package):
        return None
    return ".".join([*base, *(node.module.split(".") if node.module else [])]) or None


@dataclass(frozen=True)
class Reached:
    """The part of one module a program can execute: all of it, or the statements behind names."""

    path: Path
    statements: tuple[ast.stmt, ...]

    def nodes(self) -> Iterator[ast.AST]:
        for statement in self.statements:
            yield from ast.walk(statement)


def _bound_names(statement: ast.stmt) -> set[str]:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {statement.name}
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return {(alias.asname or alias.name).split(".")[0] for alias in statement.names}
    targets: list[ast.expr] = []
    if isinstance(statement, ast.Assign):
        targets = list(statement.targets)
    elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
        targets = [statement.target]
    return {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}


def _is_main_guard(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.If)
        and isinstance(statement.test, ast.Compare)
        and isinstance(statement.test.left, ast.Name)
        and statement.test.left.id == "__name__"
    )


def _statements_behind(tree: ast.Module, names: frozenset[str]) -> tuple[ast.stmt, ...]:
    """The top-level statements a set of imported names depends on, through the module's own names.

    A statement binding nothing runs at import, so it is kept -- except the `__main__` guard, which
    does not run when the module is imported.
    """
    body = [s for s in tree.body if not _is_main_guard(s)]
    binders: dict[str, list[ast.stmt]] = {}
    for statement in body:
        for name in _bound_names(statement):
            binders.setdefault(name, []).append(statement)  # EVERY binding, not the last
    # An import runs whenever the module is imported, whatever it binds -- so it is kept; what it
    # imports is charged only as far as the kept code uses it (see `closure`).
    kept: dict[int, ast.stmt] = {
        id(s): s for s in body if not _bound_names(s) or isinstance(s, (ast.Import, ast.ImportFrom))
    }
    pending = [s for n in names for s in binders.get(n, [])]
    while pending:
        statement = pending.pop()
        if id(statement) in kept:
            continue
        kept[id(statement)] = statement
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and node.id in binders:
                pending.extend(binders[node.id])
    return tuple(s for s in body if id(s) in kept)


def closure(src: Path, package: str, packages: list[str]) -> list[Reached]:
    """The package's own modules, whole, plus what it can execute of every other program it imports.

    Symbol granularity across a package boundary: `change_proposer` importing the `WORK_UNIT_ID`
    regex from `deploy_watcher.orchestrator` is charged that regex's statement, not the watcher's
    orchestrator client defined beside it. `import other.module` (the whole module) charges it all.
    """
    wanted: dict[Path, frozenset[str] | None] = {
        path: None for path in sorted((src / package).rglob("*.py"))
    }
    queue = list(wanted)
    reached: dict[Path, Reached] = {}
    while queue:
        path = queue.pop()
        names = wanted[path]
        tree = _parse(path)
        statements = tuple(tree.body) if names is None else _statements_behind(tree, names)
        reached[path] = Reached(path, statements)
        used = {
            n.id
            for s in statements
            if not isinstance(s, (ast.Import, ast.ImportFrom))
            for n in ast.walk(s)
            if isinstance(n, ast.Name)
        } | {
            n.value.id
            for s in statements
            for n in ast.walk(s)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
        }
        for node in (n for s in statements for n in ast.walk(s)):
            requests: list[tuple[str, frozenset[str] | None]] = []
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = (alias.asname or alias.name).split(".")[0]
                    requests.append((alias.name, None if bound in used else frozenset()))
            elif isinstance(node, ast.ImportFrom):
                module = _absolute(src, path, node)
                if module is None:
                    continue
                for alias in node.names:
                    wanted_here = (alias.asname or alias.name) in used
                    submodule = f"{module}.{alias.name}"
                    if _module_file(src, submodule) is not None:
                        requests.append((submodule, None if wanted_here else frozenset()))
                    else:
                        symbols = frozenset({alias.name}) if wanted_here else frozenset()
                        requests.append((module, symbols))
            for dotted, symbols in requests:
                top = dotted.split(".")[0]
                if top == package or top not in packages:
                    continue
                for target, target_names in (
                    (_module_file(src, dotted), symbols),
                    (_module_file(src, top), frozenset[str]()),  # the package __init__ runs
                ):
                    if target is None:
                        continue
                    before = wanted.get(target, frozenset())
                    merged = (
                        None if before is None or target_names is None else before | target_names
                    )
                    if target not in wanted or merged != before:
                        wanted[target] = merged
                        queue.append(target)
    return [reached[path] for path in sorted(reached)]


def imports_orchestrator(src: Path, units: list[Reached]) -> list[str]:
    found: list[str] = []
    for unit in units:
        for node in unit.nodes():
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules = [node.module]
            found += [
                f"{unit.path.relative_to(src)}:{getattr(node, 'lineno', 0)}: imports {m}"
                for m in modules
                if m.split(".")[0] == "orchestrator"
            ]
    return sorted(found)


# ------------------------------------------------------------------------------------------------
# Path hits
# ------------------------------------------------------------------------------------------------

API = re.compile(r"/api/v1(?=/|$|\?|\$|\\Z)")
WORD = re.compile(r"^[a-z][a-z0-9-]*$")
ALTERNATION = re.compile(r"^\(([a-z0-9|-]+)\)$")


@dataclass(frozen=True)
class Hit:
    where: str
    text: str


def _docstring_nodes(tree: ast.Module) -> set[int]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, owners)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }


def _code_strings(unit: Reached) -> Iterator[tuple[int, str]]:
    """Every string constant and f-string in the reached code, docstrings excluded."""
    docstrings = _docstring_nodes(_parse(unit.path))
    inside_fstrings: set[int] = set()
    for node in unit.nodes():
        if isinstance(node, ast.JoinedStr):
            inside_fstrings.update(id(value) for value in node.values)
            yield (
                node.lineno,
                "".join(
                    str(value.value) if isinstance(value, ast.Constant) else "{}"
                    for value in node.values
                ),
            )
    for node in unit.nodes():
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and id(node) not in inside_fstrings
        ):
            yield node.lineno, node.value


def path_hits(src: Path, units: list[Reached]) -> list[Hit]:
    return [
        Hit(f"{unit.path.relative_to(src)}:{line}", text)
        for unit in units
        for line, text in _code_strings(unit)
        if API.search(text)
    ]


def run_time_joins(src: Path, units: list[Reached], foreign: Mapping[str, str]) -> list[str]:
    """Where an orchestrator path is extended at run time: `BASE + "/x"` or `f"{BASE}/x"`.

    A path that is itself a served route can still be a join base -- `f"/api/v1/things/{id}"` is
    served, and so is `…/{id}/approve` -- so resolving the literal alone would charge the first and
    never see the second. Declared foreign bases (a third-party API root) are not orchestrator
    paths.
    """
    found: list[str] = []
    for unit in units:
        constants = _resolved_constants(src, unit.path)
        path_names = {
            name for name, text in constants.items() if API.search(text) and text not in foreign
        }
        for node in unit.nodes():
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                text = (
                    value.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                    else _fstring_text(value)
                    if isinstance(value, ast.JoinedStr)
                    else None
                )
                if text is not None and API.search(text) and text not in foreign:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    path_names |= {t.id for t in targets if isinstance(t, ast.Name)}

        def is_base(expr: ast.expr) -> bool:
            if isinstance(expr, ast.Name):
                return expr.id in path_names
            text = (
                expr.value
                if isinstance(expr, ast.Constant) and isinstance(expr.value, str)
                else _fstring_text(expr)
                if isinstance(expr, ast.JoinedStr)
                else None
            )
            return text is not None and bool(API.search(text)) and text not in foreign

        def continues_path(expr: ast.expr) -> bool:
            """What follows the base extends it as a PATH: a literal starting "/", or anything
            computed. A message like f"rejected {PATH}: {status}" continues it with prose."""
            if isinstance(expr, ast.Constant):
                return isinstance(expr.value, str) and expr.value.startswith("/")
            if isinstance(expr, ast.JoinedStr) and expr.values:
                return continues_path(expr.values[0])
            return True

        for node in unit.nodes():
            where = f"{unit.path.relative_to(src)}:{getattr(node, 'lineno', 0)}"
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Add)
                and is_base(node.left)
                and continues_path(node.right)
            ):
                found.append(f"{where}: extends an orchestrator path with `+` at run time")
            elif isinstance(node, ast.JoinedStr):
                for piece, following in zip(node.values, node.values[1:], strict=False):
                    if (
                        isinstance(piece, ast.FormattedValue)
                        and is_base(piece.value)
                        and continues_path(
                            following.value
                            if isinstance(following, ast.FormattedValue)
                            else following
                        )
                    ):
                        found.append(f"{where}: extends an orchestrator path in an f-string")
                        break
    return found


def _fstring_text(node: ast.JoinedStr) -> str:
    return "".join(
        str(value.value) if isinstance(value, ast.Constant) else "{}" for value in node.values
    )


def _segment_options(segment: str) -> list[str | None]:
    """A literal word, the words of an alternation, or None for a wildcard."""
    if WORD.match(segment):
        return [segment]
    alternation = ALTERNATION.match(segment)
    if alternation:
        return list(alternation.group(1).split("|"))
    return [None]


def _hit_segments(text: str) -> list[list[str | None]]:
    match = API.search(text)
    assert match is not None, text  # only reached through `path_hits`, which filtered on it
    path = text[match.start() :]
    path = re.split(r"\?|\$|\\Z", path, maxsplit=1)[0].rstrip("/")
    return [_segment_options(segment) for segment in path.split("/")[1:]]


def _template_segments(template: str) -> list[str | None]:
    return [None if segment.startswith("{") else segment for segment in template.split("/")[1:]]


def _aligns(hit: list[list[str | None]], template: list[str | None]) -> bool:
    for options, wanted in zip(hit, template, strict=True):
        if wanted is None:
            continue  # a template parameter takes a word or a wildcard
        if wanted not in options:  # a wildcard (None) never equals a template word
            return False
    return True


@dataclass(frozen=True)
class Resolution:
    templates: frozenset[str] = frozenset()
    fragment: bool = False
    foreign: bool = False
    dynamic: bool = False


def resolve(text: str, templates: frozenset[str]) -> Resolution:
    hit = _hit_segments(text)
    matches = frozenset(
        template
        for template in templates
        if len(_template_segments(template)) == len(hit)
        and _aligns(hit, _template_segments(template))
    )
    if matches:
        return Resolution(templates=matches)
    # A strict prefix of a served template -- "/api/v1" itself among them -- is a base that
    # something joins a path onto at run time, which is exactly what this scan cannot follow.
    if any(
        len(_template_segments(template)) > len(hit)
        and _aligns(hit, _template_segments(template)[: len(hit)])
        for template in templates
    ):
        return Resolution(fragment=True)
    if any(None in options for options in hit):
        return Resolution(dynamic=True)
    return Resolution(foreign=True)


# ------------------------------------------------------------------------------------------------
# The orchestrator CLI channel
# ------------------------------------------------------------------------------------------------


@cache
def orchestrator_script_names() -> frozenset[str]:
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    return frozenset(name for name, target in scripts.items() if target.startswith("orchestrator."))


def _module_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            constants[target.id] = value.value
    return constants


def _resolved_constants(src: Path, path: Path) -> dict[str, str]:
    """Module-level string constants, plus those it imports by name from a module under `src`."""
    tree = _parse(path)
    constants = _module_constants(tree)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = _absolute(src, path, node)
            target = _module_file(src, module) if module else None
            if target is None:
                continue
            theirs = _module_constants(_parse(target))
            for alias in node.names:
                if alias.name in theirs:
                    constants[alias.asname or alias.name] = theirs[alias.name]
    return constants


def _value(node: ast.expr, constants: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


@cache
def cli_subcommand_names() -> frozenset[str]:
    """Every subcommand the orchestrator CLI registers, read from typer rather than from source,
    because some commands are registered by a helper and never appear as a decorator."""
    commands = getattr(typer.main.get_command(orchestrator_cli.app), "commands", None)
    assert isinstance(commands, dict) and commands, "the orchestrator CLI registers no commands"
    return frozenset(commands)


def cli_invocations(
    src: Path, units: list[Reached], scripts: frozenset[str], subcommands: frozenset[str]
) -> list[tuple[str, str]]:
    """(where, subcommand) for every orchestrator-CLI invocation; subcommand "" if unresolvable.

    An argv LIST whose first element is the console script counts whatever follows it. A shell
    STRING counts only when its second word is a real subcommand -- otherwise every error message
    that begins "orchestrator is unreachable" would read as an invocation of `is`.
    """
    found: list[tuple[str, str]] = []
    for unit in units:
        constants = _resolved_constants(src, unit.path)
        for node in unit.nodes():
            where = f"{unit.path.relative_to(src)}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                head = _value(node.elts[0], constants)
                sub = _value(node.elts[1], constants) if len(node.elts) > 1 else None
                if head in scripts:
                    found.append((where, sub or ""))
                elif head is None and isinstance(node.elts[0], ast.Name) and sub in subcommands:
                    # The program name is computed, but what follows it is a real subcommand:
                    # counted rather than silently read as "not an invocation".
                    found.append((where, sub or ""))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                words = node.value.split()
                if len(words) > 1 and words[0] in scripts and words[1] in subcommands:
                    found.append((where, words[1]))
    return found


def _cli_command_functions() -> dict[str, ast.FunctionDef]:
    commands: dict[str, ast.FunctionDef] = {}
    for node in _parse(CLI_MODULE).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "command"
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "app"
            ):
                named = [a.value for a in decorator.args if isinstance(a, ast.Constant)]
                named += [
                    k.value.value
                    for k in decorator.keywords
                    if k.arg == "name" and isinstance(k.value, ast.Constant)
                ]
                commands[str(named[0]) if named else node.name.replace("_", "-")] = node
    return commands


def cli_command_writes(name: str) -> bool | None:
    """Whether the command reaches `request(` through module-level helpers; None if not found."""
    command = _cli_command_functions().get(name)
    if command is None:
        return None
    functions = {n.name: n for n in _parse(CLI_MODULE).body if isinstance(n, ast.FunctionDef)}
    pending, visited = [command], set()
    while pending:
        function = pending.pop()
        if function.name in visited:
            continue
        visited.add(function.name)
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "request":
                    return True
                if node.func.id in functions:
                    pending.append(functions[node.func.id])
    return False


# ------------------------------------------------------------------------------------------------
# The container-image rule
# ------------------------------------------------------------------------------------------------


def container_image_violations(src: Path, units: list[Reached]) -> list[str]:
    problems: list[str] = []
    payloads = 0
    for unit in units:
        path = unit.path
        constants = _resolved_constants(src, path)
        for line, text in _code_strings(unit):
            if text == CONTAINER_IMAGE_KIND:
                problems.append(f"{path.relative_to(src)}:{line}: names {CONTAINER_IMAGE_KIND!r}")
        for node in unit.nodes():
            if not isinstance(node, ast.Dict):
                continue
            keys = {
                _value(key, constants): value
                for key, value in zip(node.keys, node.values, strict=True)
                if key is not None
            }
            if not PAYLOAD_DIGEST_KEYS & keys.keys():
                continue
            payloads += 1
            where = f"{path.relative_to(src)}:{node.lineno}"
            if "kind" not in keys:
                problems.append(
                    f"{where}: a release binding or deployment observation payload omits 'kind', "
                    f"which the wire model defaults to {CONTAINER_IMAGE_KIND!r} -- a unit is minted"
                )
            elif _value(keys["kind"], constants) != MACHINE_LOCAL_KIND:
                problems.append(
                    f"{where}: payload 'kind' does not resolve to {MACHINE_LOCAL_KIND!r}"
                )
    if payloads == 0:
        problems.append(
            "can write a release-artifact route but no binding or observation payload was found "
            "to inspect -- a rule with nothing to read is not a pass"
        )
    return problems


# ------------------------------------------------------------------------------------------------
# The guard
# ------------------------------------------------------------------------------------------------


@dataclass
class Surface:
    writes: set[str] = field(default_factory=set)
    cli: set[str] = field(default_factory=set)
    foreign_seen: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)


def detect(
    src: Path,
    package: str,
    packages: list[str],
    row: Row,
    table: Mapping[str, frozenset[str]],
    scripts: frozenset[str],
    subcommands: frozenset[str],
) -> Surface:
    units = closure(src, package, packages)
    surface = Surface()
    surface.problems += imports_orchestrator(src, units)
    surface.problems += run_time_joins(src, units, row.foreign)
    writable = write_templates(table)
    templates = frozenset(table)
    for hit in path_hits(src, units):
        resolution = resolve(hit.text, templates)
        if resolution.templates:
            surface.writes |= resolution.templates & writable
            continue
        if hit.text in row.foreign:
            surface.foreign_seen.add(hit.text)
            continue
        if resolution.dynamic:
            surface.problems.append(
                f"{hit.where}: {hit.text!r} builds an orchestrator path the guard cannot resolve"
            )
        elif resolution.fragment:
            surface.problems.append(
                f"{hit.where}: {hit.text!r} is an /api/v1 base or prefix joined onto at run time; "
                "name the full path, or declare it foreign with the service it belongs to"
            )
        else:
            surface.problems.append(
                f"{hit.where}: {hit.text!r} matches no orchestrator route; declare it foreign "
                "with the service it belongs to"
            )
    for where, subcommand in cli_invocations(src, units, scripts, subcommands):
        if not subcommand:
            surface.problems.append(
                f"{where}: orchestrator CLI invoked with an unresolvable subcommand"
            )
            continue
        surface.cli.add(subcommand)
        writes = cli_command_writes(subcommand)
        if writes is None:
            surface.problems.append(
                f"{where}: orchestrator CLI subcommand {subcommand!r} cannot be found statically, "
                "so what it sends cannot be verified"
            )
        elif writes:
            surface.writes.add(f"cli:{subcommand}")
    if surface.writes & {RELEASE_BINDING, DEPLOYMENT_OBSERVATION}:
        surface.problems += container_image_violations(src, units)
    return surface


def violations(
    src: Path,
    classification: Mapping[str, Row],
    table: Mapping[str, frozenset[str]] | None = None,
    scripts: frozenset[str] | None = None,
    subcommands: frozenset[str] | None = None,
) -> list[str]:
    table = route_table() if table is None else table
    scripts = orchestrator_script_names() if scripts is None else scripts
    subcommands = cli_subcommand_names() if subcommands is None else subcommands
    packages = out_of_process_packages(src)
    found: list[str] = []
    for package in sorted(set(packages) - set(classification)):
        found.append(f"{package}: an out-of-process package with no row in CLASSIFICATION")
    for package in sorted(set(classification) - set(packages)):
        found.append(f"{package}: a row in CLASSIFICATION for a package that no longer exists")
    templates = frozenset(table)
    for package in sorted(set(packages) & set(classification)):
        row = classification[package]
        surface = detect(src, package, packages, row, table, scripts, subcommands)
        found += [f"{package}: {problem}" for problem in surface.problems]
        for route in sorted(surface.writes - row.writes):
            found.append(
                f"{package}: can write {route}, which its row ({row.role.value}) does not permit"
            )
        for route in sorted(row.writes - surface.writes):
            found.append(f"{package}: its row permits {route}, which it never writes")
        for command in sorted(surface.cli ^ row.cli):
            side = "invokes" if command in surface.cli else "declares but never invokes"
            found.append(f"{package}: {side} orchestrator CLI subcommand {command!r}")
        for literal in sorted(set(row.foreign) - surface.foreign_seen):
            found.append(f"{package}: declares foreign {literal!r}, which its code never names")
        for literal in sorted(row.foreign):
            if resolve(literal, templates).templates:
                found.append(
                    f"{package}: declares {literal!r} foreign, but it is an orchestrator route"
                )
    return found


# ------------------------------------------------------------------------------------------------
# Tests over the real tree
# ------------------------------------------------------------------------------------------------


def test_every_out_of_process_producer_is_classified_and_writes_only_what_its_row_permits() -> None:
    assert violations(SRC, CLASSIFICATION) == []


def test_the_population_is_the_one_on_disk_and_is_not_trivially_small() -> None:
    packages = out_of_process_packages(SRC)
    assert "orchestrator" not in packages
    assert set(packages) == set(CLASSIFICATION)
    assert packages


def test_each_role_fixes_the_shape_of_its_write_surface() -> None:
    """The role is not a label: it bounds what a row may permit, so a row cannot be re-labelled
    OBSERVE_ONLY while carrying a lifecycle write, nor carry a write under a role that has none."""
    writable = write_templates(route_table())
    for package, row in CLASSIFICATION.items():
        assert re.search(r"ADR-\d{4}|2026-08-25|2026-09-10", row.reason), package
        for route in row.writes:
            assert route in writable or route.startswith("cli:"), (package, route)
        if row.role is Role.OBSERVE_ONLY:
            assert row.writes == OBSERVE, package
            assert row.cli == frozenset(), package
        elif row.role is Role.NOT_AN_ORCHESTRATOR_WRITER:
            assert row.writes == frozenset(), package
        else:
            assert row.writes, package


def test_the_observe_only_surface_is_the_observer_roles_whole_surface() -> None:
    assert OBSERVE == OBSERVER_WRITE_ROUTES


def test_the_release_artifact_routes_are_spelled_as_the_orchestrator_serves_them() -> None:
    table = route_table()
    assert "post" in table[RELEASE_BINDING]
    assert "post" in table[DEPLOYMENT_OBSERVATION]
    assert "post" in table[OBSERVATIONS]


def test_omitting_kind_means_container_image_on_both_wire_models() -> None:
    """The fact the kind rule rests on, pinned from the orchestrator side."""
    assert ReleaseArtifactCommandModel.model_fields["kind"].default == CONTAINER_IMAGE_KIND
    assert DeploymentObservationCommandModel.model_fields["kind"].default == CONTAINER_IMAGE_KIND
    assert MACHINE_LOCAL_OBSERVATION == MACHINE_LOCAL_KIND == "machine_local"
    assert CONTAINER_IMAGE_KIND == "container_image"


def test_only_a_machine_local_observation_skips_the_minted_unit() -> None:
    """`record_deployment_observation` mints the post-deploy unit on every kind but machine_local;
    if that branch is ever re-keyed, the kind rule above is guarding the wrong value."""
    source = (ORCHESTRATOR / "services" / "deployment_observations.py").read_text()
    assert re.search(
        r"if command\.kind == MACHINE_LOCAL_OBSERVATION\s+else _post_deploy_work_unit\(",
        source,
    )


def test_exactly_three_modules_construct_a_work_unit() -> None:
    """ADR-0039's measurement, made a property. A fourth constructor means the ruling needs
    re-checking against it, which is this failure's whole message."""
    constructors: set[str] = set()
    for path in ORCHESTRATOR.rglob("*.py"):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "WorkUnit")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "WorkUnit")
            ):
                constructors.add(str(path.relative_to(ORCHESTRATOR)))
    assert constructors == WORK_UNIT_CONSTRUCTORS


def test_the_observations_service_reaches_no_work_unit_constructor() -> None:
    tree = _parse(ORCHESTRATOR / "services" / "observations.py")
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {
        alias.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for alias in n.names
    }
    assert "WorkUnit" not in names
    modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    for constructor in WORK_UNIT_CONSTRUCTORS:
        stem = constructor.removesuffix(".py").replace("/", ".")
        assert not any(module.endswith(stem) for module in modules), stem
        assert Path(constructor).stem not in names, constructor


def test_the_one_declared_cli_subcommand_is_offline() -> None:
    assert cli_command_writes("emit-intake-payload") is False
    # And the resolver can see a command that does send, or "offline" would mean "unseen".
    assert cli_command_writes("intake-package") is True


# ------------------------------------------------------------------------------------------------
# Controls: each rule, proven to fire on a synthetic tree beside the real one that passes.
# ------------------------------------------------------------------------------------------------

FIXTURE_TABLE: dict[str, frozenset[str]] = {
    OBSERVATIONS: frozenset({"get", "post"}),
    "/api/v1/work-units/{unit_id}/dispatch": frozenset({"post"}),
    "/api/v1/work-units/{unit_id}/history": frozenset({"get"}),
    RELEASE_BINDING: frozenset({"get", "post"}),
    DEPLOYMENT_OBSERVATION: frozenset({"get", "post"}),
}
SCRIPTS = frozenset({"orchestrator"})
SUBCOMMANDS = frozenset({"intake-package", "emit-intake-payload"})
OBSERVER_ROW = Row(Role.OBSERVE_ONLY, "ADR-0039 fixture", OBSERVE)
BINDER_ROW = Row(
    Role.INTERNAL_LANE_ACTOR,
    "ADR-0030 fixture",
    frozenset({RELEASE_BINDING, DEPLOYMENT_OBSERVATION}),
)

CLEAN_OBSERVER = (
    'OBSERVATIONS = "/api/v1/observations"\nHISTORY = f"/api/v1/work-units/{x}/history"\n'
)


def _tree(tmp_path: Path, **modules: str) -> Path:
    src = tmp_path / "src"
    (src / "orchestrator").mkdir(parents=True)
    (src / "orchestrator" / "__init__.py").write_text("")
    for name, body in modules.items():
        package, _, module = name.partition("__")
        (src / package).mkdir(exist_ok=True)
        (src / package / "__init__.py").touch()
        (src / package / f"{module or 'client'}.py").write_text(body)
    return src


def _run(src: Path, classification: Mapping[str, Row]) -> list[str]:
    return violations(src, classification, FIXTURE_TABLE, SCRIPTS, SUBCOMMANDS)


def test_control_the_clean_observer_passes(tmp_path: Path) -> None:
    src = _tree(tmp_path, producer=CLEAN_OBSERVER)
    assert _run(src, {"producer": OBSERVER_ROW}) == []


def test_control_an_unclassified_package_fails(tmp_path: Path) -> None:
    src = _tree(tmp_path, producer=CLEAN_OBSERVER, newcomer=CLEAN_OBSERVER)
    assert _run(src, {"producer": OBSERVER_ROW}) == [
        "newcomer: an out-of-process package with no row in CLASSIFICATION"
    ]


def test_control_a_row_for_a_vanished_package_fails(tmp_path: Path) -> None:
    src = _tree(tmp_path, producer=CLEAN_OBSERVER)
    found = _run(src, {"producer": OBSERVER_ROW, "ghost": OBSERVER_ROW})
    assert found == ["ghost: a row in CLASSIFICATION for a package that no longer exists"]


@pytest.mark.parametrize(
    "construct",
    [
        'PATH = "/api/v1/work-units/0b0e/dispatch"',
        'def path(u): return f"/api/v1/work-units/{u}/dispatch"',
        r'PATTERN = r"^/api/v1/work-units/[0-9a-f-]{36}/dispatch$"',
        'URL = "https://sds.alobar.net/api/v1/work-units/x/dispatch"',
        'def go(c, u):\n    """Only reads history."""\n'
        '    c.post(f"/api/v1/work-units/{u}/dispatch")',
    ],
    ids=["constant", "f-string", "regex", "full-url", "call-under-a-docstring"],
)
def test_control_an_observer_naming_a_lifecycle_write_fails(tmp_path: Path, construct: str) -> None:
    src = _tree(tmp_path, producer=CLEAN_OBSERVER + construct + "\n")
    assert _run(src, {"producer": OBSERVER_ROW}) == [
        "producer: can write /api/v1/work-units/{unit_id}/dispatch, which its row "
        "(observe-only producer) does not permit"
    ]


def test_control_a_docstring_is_prose_and_does_not_count(tmp_path: Path) -> None:
    src = _tree(
        tmp_path, producer='"""Never POST /api/v1/work-units/{id}/dispatch."""\n' + CLEAN_OBSERVER
    )
    assert _run(src, {"producer": OBSERVER_ROW}) == []


def test_control_a_base_url_join_fails(tmp_path: Path) -> None:
    src = _tree(tmp_path, producer=CLEAN_OBSERVER + 'BASE = "/api/v1"\nPATH = BASE + "/x"\n')
    found = " ".join(_run(src, {"producer": OBSERVER_ROW}))
    assert "'/api/v1' is an /api/v1 base or prefix joined onto at run time" in found
    assert "extends an orchestrator path with `+` at run time" in found


@pytest.mark.parametrize(
    "join",
    [
        'def go(c, u):\n    base = f"/api/v1/work-units/{u}/history"\n'
        '    c.post(base + "/dispatch")',
        'def go(c, u):\n    base = f"/api/v1/work-units/{u}/history"\n'
        '    c.post(f"{base}/dispatch")',
        'BASE = "/api/v1/work-units"\ndef go(c, rest):\n    c.post(BASE + rest)',
    ],
    ids=["plus-on-a-served-route", "fstring-on-a-served-route", "plus-a-computed-suffix"],
)
def test_control_a_served_route_extended_at_run_time_fails(tmp_path: Path, join: str) -> None:
    """The base is itself a served (read) route, so resolving literals alone would charge the read
    and never see the write the join produces."""
    src = _tree(tmp_path, producer=CLEAN_OBSERVER + join + "\n")
    assert any("extends an orchestrator path" in m for m in _run(src, {"producer": OBSERVER_ROW}))


def test_control_a_path_quoted_in_an_error_message_is_not_a_join(tmp_path: Path) -> None:
    src = _tree(
        tmp_path,
        producer=CLEAN_OBSERVER
        + 'def err(code):\n    return f"rejected POST {OBSERVATIONS}: {code}"\n',
    )
    assert _run(src, {"producer": OBSERVER_ROW}) == []


def test_control_a_template_prefix_is_a_join_too(tmp_path: Path) -> None:
    src = _tree(tmp_path, producer=CLEAN_OBSERVER + 'PREFIX = "/api/v1/work-units/"\n')
    [message] = _run(src, {"producer": OBSERVER_ROW})
    assert "base or prefix joined onto at run time" in message


def test_control_a_dynamic_route_word_fails(tmp_path: Path) -> None:
    """The route WORD is dynamic, and the rest happens to fit a real template's shape: a wildcard
    standing where a template has a word must not be read as that word."""
    src = _tree(tmp_path, producer=CLEAN_OBSERVER + 'def p(r): return f"/api/v1/{r}/x/dispatch"\n')
    [message] = _run(src, {"producer": OBSERVER_ROW})
    assert "builds an orchestrator path the guard cannot resolve" in message


def test_control_an_undeclared_foreign_path_fails(tmp_path: Path) -> None:
    src = _tree(tmp_path, producer=CLEAN_OBSERVER + 'COOLIFY = "/api/v1/zzz"\n')
    assert _run(src, {"producer": OBSERVER_ROW}) == [
        "producer: producer/client.py:3: '/api/v1/zzz' matches no orchestrator route; "
        "declare it foreign with the service it belongs to"
    ]


def test_control_a_declared_foreign_path_passes_and_rots_both_ways(tmp_path: Path) -> None:
    src = _tree(tmp_path, producer=CLEAN_OBSERVER + 'COOLIFY = "/api/v1/zzz"\n')
    declared = Row(Role.OBSERVE_ONLY, "ADR-0039 fixture", OBSERVE, foreign={"/api/v1/zzz": "x"})
    assert _run(src, {"producer": declared}) == []
    stale = Row(Role.OBSERVE_ONLY, "ADR-0039", OBSERVE, foreign={"/api/v1/gone": "x"})
    assert "declares foreign '/api/v1/gone', which its code never names" in " ".join(
        _run(src, {"producer": stale})
    )
    masking = Row(Role.OBSERVE_ONLY, "ADR-0039", OBSERVE, foreign={OBSERVATIONS: "x"})
    assert (
        "producer: declares '/api/v1/observations' foreign, but it is an orchestrator route"
        in _run(src, {"producer": masking})
    )


def test_control_an_unused_grant_fails(tmp_path: Path) -> None:
    src = _tree(tmp_path, producer='HISTORY = f"/api/v1/work-units/{x}/history"\n')
    assert _run(src, {"producer": OBSERVER_ROW}) == [
        "producer: its row permits /api/v1/observations, which it never writes"
    ]


def test_control_importing_the_orchestrator_fails(tmp_path: Path) -> None:
    src = _tree(tmp_path, producer=CLEAN_OBSERVER + "from orchestrator.services import packages\n")
    [message] = _run(src, {"producer": OBSERVER_ROW})
    assert "imports orchestrator.services" in message


SIBLING = {
    "sibling__client": (
        'DISPATCH = "/api/v1/work-units/x/dispatch"\n'
        'HARMLESS = "a value with no path in it"\n'
        "def go(client):\n"
        "    return client.post(DISPATCH)\n"
    ),
}
SIBLING_ROW = Row(
    Role.INTERNAL_LANE_ACTOR,
    "ADR-0000 fixture",
    frozenset({"/api/v1/work-units/{unit_id}/dispatch"}),
)
INHERITED = (
    "producer: can write /api/v1/work-units/{unit_id}/dispatch, which its row "
    "(observe-only producer) does not permit"
)


@pytest.mark.parametrize(
    "import_and_use",
    [
        "from sibling.client import go\ngo(None)",
        "import sibling.client\nsibling.client.go(None)",
        "from sibling import client\nclient.go(None)",
    ],
)
def test_control_a_write_inherited_from_another_program_is_charged(
    tmp_path: Path, import_and_use: str
) -> None:
    """The function that sends is imported and used, however it is imported: the write comes with
    it."""
    src = _tree(tmp_path, producer=CLEAN_OBSERVER + import_and_use + "\n", **SIBLING)
    assert _run(src, {"producer": OBSERVER_ROW, "sibling": SIBLING_ROW}) == [INHERITED]


def test_control_an_imported_programs_init_runs_and_is_charged(tmp_path: Path) -> None:
    """Importing any module of a package executes its `__init__.py`, so a write made there at import
    time is the importer's write too -- even when the symbol imported is harmless."""
    src = _tree(
        tmp_path, producer=CLEAN_OBSERVER + "from sibling.client import HARMLESS\n", **SIBLING
    )
    (src / "sibling" / "__init__.py").write_text(
        'import httpx\nhttpx.post("https://sds.example/api/v1/work-units/x/dispatch")\n'
    )
    assert INHERITED in _run(src, {"producer": OBSERVER_ROW, "sibling": SIBLING_ROW})


def test_control_a_rebound_name_keeps_every_binding(tmp_path: Path) -> None:
    """`DISPATCH = DISPATCH.strip()` must not hide the first binding, which holds the path."""
    src = _tree(
        tmp_path,
        producer=CLEAN_OBSERVER + "from sibling.client import go\ngo(None)\n",
        sibling__client=(
            'DISPATCH = "/api/v1/work-units/x/dispatch"\nDISPATCH = DISPATCH.strip()\n'
            "def go(client):\n    return client.post(DISPATCH)\n"
        ),
    )
    assert _run(src, {"producer": OBSERVER_ROW, "sibling": SIBLING_ROW}) == [INHERITED]


def test_control_a_relative_import_inside_another_program_is_followed(tmp_path: Path) -> None:
    src = _tree(
        tmp_path,
        producer=CLEAN_OBSERVER + "from sibling.client import go\ngo(None)\n",
        sibling__client=(
            "from .paths import DISPATCH\ndef go(client):\n    return client.post(DISPATCH)\n"
        ),
        sibling__paths='DISPATCH = "/api/v1/work-units/x/dispatch"\n',
    )
    assert _run(src, {"producer": OBSERVER_ROW, "sibling": SIBLING_ROW}) == [INHERITED]


def test_control_an_import_in_an_imported_init_runs_and_is_charged(tmp_path: Path) -> None:
    """An import binds a name, and it still runs: `from sibling import writer` in the package
    `__init__` executes `writer`'s import-time write for anyone importing any `sibling` module."""
    src = _tree(
        tmp_path,
        producer=CLEAN_OBSERVER + "from sibling.client import HARMLESS\nprint(HARMLESS)\n",
        **SIBLING,
        sibling__writer=(
            'import httpx\nhttpx.post("https://sds.example/api/v1/work-units/x/dispatch")\n'
        ),
    )
    (src / "sibling" / "__init__.py").write_text("from sibling import writer\n")
    assert INHERITED in _run(src, {"producer": OBSERVER_ROW, "sibling": SIBLING_ROW})


def test_control_importing_a_harmless_symbol_beside_a_client_charges_nothing(
    tmp_path: Path,
) -> None:
    """Symbol granularity: the constant is imported, the client defined beside it is not."""
    src = _tree(
        tmp_path, producer=CLEAN_OBSERVER + "from sibling.client import HARMLESS\n", **SIBLING
    )
    assert _run(src, {"producer": OBSERVER_ROW, "sibling": SIBLING_ROW}) == []


def test_control_an_undeclared_cli_invocation_fails(tmp_path: Path) -> None:
    src = _tree(
        tmp_path,
        producer=CLEAN_OBSERVER
        + 'EMITTER = "orchestrator"\nCMD = [EMITTER, "intake-package", "p"]\n',
    )
    found = _run(src, {"producer": OBSERVER_ROW})
    assert "producer: invokes orchestrator CLI subcommand 'intake-package'" in found
    assert (
        "producer: can write cli:intake-package, which its row (observe-only producer) does "
        "not permit" in found
    )


@pytest.mark.parametrize(
    "invocation",
    [
        'from producer.names import EMITTER, SUBCOMMAND\nCMD = [EMITTER, SUBCOMMAND, "p"]',
        'import os\nemitter = os.environ["X"]\nCMD = [emitter, "intake-package", "p"]',
    ],
    ids=["script-name-imported", "script-name-computed"],
)
def test_control_a_cli_invocation_whose_program_is_not_a_local_literal_is_seen(
    tmp_path: Path, invocation: str
) -> None:
    src = _tree(
        tmp_path,
        producer=CLEAN_OBSERVER + invocation + "\n",
        producer__names='EMITTER = "orchestrator"\nSUBCOMMAND = "intake-package"\n',
    )
    assert "producer: invokes orchestrator CLI subcommand 'intake-package'" in _run(
        src, {"producer": OBSERVER_ROW}
    )


def test_control_a_shell_string_cli_invocation_is_seen(tmp_path: Path) -> None:
    src = _tree(tmp_path, producer=CLEAN_OBSERVER + 'CMD = "orchestrator intake-package p"\n')
    assert "producer: invokes orchestrator CLI subcommand 'intake-package'" in _run(
        src, {"producer": OBSERVER_ROW}
    )


BIND_PAYLOAD = (
    'BIND = f"/api/v1/work-units/{u}/release-artifacts"\n'
    'OBS = f"/api/v1/release-artifacts/{b}/deployment-observations"\n'
    'MACHINE = "machine_local"\n'
    'binding = {"kind": BIND_KIND, "artifact_digest": "d"}\n'
    'observation = {OBS_KIND"observed_artifact_digest": "d"}\n'
)


def _payload(bind_kind: str, obs_kind: str) -> str:
    return BIND_PAYLOAD.replace("BIND_KIND", bind_kind).replace("OBS_KIND", obs_kind)


def test_control_machine_local_payloads_pass(tmp_path: Path) -> None:
    body = _payload("MACHINE", '"kind": MACHINE, ')
    src = _tree(tmp_path, binder=body)
    assert _run(src, {"binder": BINDER_ROW}) == []


def test_control_an_omitted_kind_is_the_container_image_default_and_fails(tmp_path: Path) -> None:
    body = _payload("MACHINE", "")
    src = _tree(tmp_path, binder=body)
    [message] = _run(src, {"binder": BINDER_ROW})
    assert "omits 'kind', which the wire model defaults to 'container_image'" in message


def test_control_a_container_image_binding_fails_even_with_a_machine_local_observation(
    tmp_path: Path,
) -> None:
    body = _payload('"container_image"', '"kind": MACHINE, ')
    src = _tree(tmp_path, binder=body)
    found = _run(src, {"binder": BINDER_ROW})
    assert any("names 'container_image'" in m for m in found)
    assert any("payload 'kind' does not resolve to 'machine_local'" in m for m in found)


def test_control_a_release_writer_with_no_payload_to_inspect_fails(tmp_path: Path) -> None:
    src = _tree(tmp_path, binder=BIND_PAYLOAD.split("MACHINE =")[0])
    [message] = _run(src, {"binder": BINDER_ROW})
    assert "no binding or observation payload was found to inspect" in message


def test_the_real_tree_really_has_machine_local_payloads_to_inspect() -> None:
    """The kind rule is not vacuous on the program it exists for."""
    units = closure(SRC, "activation_sweep", out_of_process_packages(SRC))
    assert container_image_violations(SRC, units) == []
    inspected = [
        node
        for unit in units
        for node in unit.nodes()
        if isinstance(node, ast.Dict)
        and PAYLOAD_DIGEST_KEYS & {k.value for k in node.keys if isinstance(k, ast.Constant)}
    ]
    assert len(inspected) >= 2
