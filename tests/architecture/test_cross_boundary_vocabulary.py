"""WS-P2.16 U5 -- the general cross-boundary-vocabulary guard.

The workstream found THREE vocabulary mismatches (evidence_type, ac_id, github.pr.create) and
nothing checked any of them. The instances are one-offs; THIS detector is the payoff. It
fails closed: every module-level collection of string constants that is used in a membership
test (`x in S`) or lookup (`S.get(x)`) -- anywhere in `src/orchestrator`, resolved across module
boundaries by import, not by bare name -- must be EITHER registered in `VOCABULARY_REGISTRY` (with
the source of truth it is validated against on both sides) OR carry an inline
``# not-a-vocabulary: <reason>`` marker on its definition. A new such set that is neither reds
this test. A vocabulary nobody registers is a guard nobody calls -- so discovery is mechanical and
only the *exemption* is hand-declared, the inverse of a hand-maintained allowlist.

Nodes are import-resolved `(module, symbol)` pairs, NOT bare names: `cli.py`-style repos taught
WS-P2.15 that seeding by name is blind to identically-named symbols in different modules. A use of
`SUPPORTED_CRITERION_EVIDENCE_TYPES` in `package_intake` resolves to its definition in
`verifier_evaluators`, so a cross-module vocabulary is covered.

DB-PINNED ENUMS ARE EXCLUDED, structurally. A string collection in `persistence/models.py`
referenced by a ``CheckConstraint`` is pinned by the schema: the DB is its single source of truth
AND its validator, so it is not an un-guarded cross-boundary vocabulary. Registering or marking
the ~20 such enums would each read "a legitimate second enumeration pinned by the DB" -- which is
the WS-P2.15 tell for a WRONG PREDICATE, not a justified exemption. So the predicate excludes them
by detecting the CheckConstraint reference, rather than demanding 20 hand-written markers.

DELIBERATE BLIND SPOTS (documented, like WS-P2.15's detector documented its own):
 * **Off-repo producers.** The real producers of `evidence_type`/capabilities are the
   intent-packages YAML and factory-runner. No static test in THIS repo can see them; the runtime
   ingress validators (U1 capability ingress, U4 evidence-type intake) are the actual guard, and
   this detector is a lint on top of them.
 * **Direction is one-way (`used` => must be triaged).** A consumer member that no producer emits
   is a dead branch this cannot see; that is a `judgment`/reverse-report concern.
 * **Case / whitespace, raw SQL, and the evidence-row `evidence_type` vs criterion `evidence_type`
   collision** (`services/evidence.py` takes a free writer type the verifier never correlates) are
   set-membership-invisible and remain backlogged.
 * **`ac_id` UUID-vs-human-string** is one name with two value domains and no enumerated set;
   membership cannot see meaning.
 * **Derived collections are invisible** -- only LITERAL collections are discovered. A vocabulary
   built as ``frozenset(X["k"])`` or a union ``A | B`` is not a literal, so the U1 capability
   vocabulary (``RUNNER_CAPABILITIES``/``ORCHESTRATOR_CAPABILITIES`` in `capability_vocabulary.py`)
   and U4's ``SUPPORTED_CRITERION_EVIDENCE_TYPES`` are NOT discovered here. Each is guarded by its
   OWN test instead -- the U1 derivation contract test and U4's Assertion D -- which is why this
   detector does not need to see them.
 * **Only `in` / `.get` membership is seen.** Validation expressed as SET ALGEBRA
   (``set(x) - KNOWN``, ``.difference``, ``.issubset``, ``<=``) or a SQLAlchemy column ``.in_()``
   is not an ``ast.Compare/In`` and is invisible. So ``kernel/authority.py``'s ``KNOWN_FIELDS`` /
   ``KNOWN_BUDGETS`` (the authority-envelope vocabulary, validated by set-difference) are not
   discovered -- they are guarded by the byte-identical envelope-contract test instead. A vocabulary
   whose ONLY consumer iterates or ``return``s it (never ``in``) is likewise unseen; the fix for
   the one such case found (``POST_DEPLOY_AC_IDS``, produced in `lifecycle` and consumed there by
   ``return``) was to make `lifecycle` its SINGLE source and have the membership-side consumer
   import it, so the detector resolves it across the import rather than missing a second copy.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass

SOURCE_ROOT = pathlib.Path("src/orchestrator")
REPO_ROOT = SOURCE_ROOT.parent.parent
EXEMPT_MARKER = "# not-a-vocabulary:"

# Genuine cross-boundary vocabularies: a value crossing a repo or subsystem boundary, whose
# members must agree on both sides. Keyed by "<module-relpath>:<symbol>", the value naming the
# single source of truth and where the OTHER side is validated. An entry that would read "a
# legitimate second copy pinned elsewhere" means the predicate is wrong -- fix the predicate, do
# not add the entry.
_VE = "services/verifier_evaluators.py"
VOCABULARY_REGISTRY: dict[str, str] = {
    # The intent-packages evidence vocabulary (producer: intent-packages YAML `evidence_type`;
    # consumer: the verifier). Validated on the orchestrator side at intake by
    # SUPPORTED_CRITERION_EVIDENCE_TYPES (WS-P2.16 U4). SUPPORTED_CRITERION... is itself a derived
    # union (a BinOp, not a literal), so it is not a discovered subject.
    f"{_VE}:DETERMINISTIC_TYPES": "intent-packages evidence tags; intake-validated (U4)",
    f"{_VE}:JUDGMENT_TYPES": "intent-packages evidence tags; intake-validated (U4)",
    # The GitHub Checks API conclusion vocabulary (producer: GitHub; consumer: the verifier).
    f"{_VE}:CHECK_PASS_CONCLUSIONS": "GitHub Checks API conclusion values",
    f"{_VE}:CHECK_FAIL_CONCLUSIONS": "GitHub Checks API conclusion values",
    "services/verifier_evidence.py:SUPPORTED_CONCLUSIONS": "GitHub Checks API conclusion values",
    # The status-string vocabulary a runner/gate may report a result as, in cross-repo payloads.
    f"{_VE}:PASS_VALUES": "runner/gate status-string vocabulary (evidence payloads)",
    f"{_VE}:FAIL_VALUES": "runner/gate status-string vocabulary (evidence payloads)",
    # The commands/{command} API vocabulary the worker and CLI send.
    "api/routes.py:COMMAND_TARGETS": "commands/{command} API vocabulary (worker/CLI)",
    # Authority profiles minted in the security-standards registry bundle.
    "kernel/context.py:AUTHORITY_PROFILE_RANK": "authority profiles (registry bundle)",
    # AC ids the verifier generates for post-deploy units. Single source in lifecycle (the
    # producer); evidence imports it to gate public adjudication (verifier <-> lifecycle). The
    # detector sees this by resolving evidence's membership use across the import.
    "services/lifecycle.py:POST_DEPLOY_AC_IDS": "verifier-generated post-deploy AC ids",
}


@dataclass(frozen=True)
class Definition:
    module: str  # module-relpath under src, e.g. "services/verifier_evaluators.py"
    name: str
    lineno: int
    end_lineno: int


def _is_string_collection(node: ast.AST) -> bool:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"frozenset", "set"}
        and node.args
    ):
        return _is_string_collection(node.args[0])
    elts: list[ast.expr] | None = None
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        elts = list(node.elts)
    elif isinstance(node, ast.Dict):
        elts = [k for k in node.keys if k is not None]
    if not elts or len(elts) < 2:
        return False
    return all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts)


def _module_key(path: pathlib.Path) -> str:
    return str(path.relative_to(SOURCE_ROOT).as_posix()).replace(".py", ".py")


def _collect_definitions(module: str, tree: ast.Module) -> dict[str, Definition]:
    defs: dict[str, Definition] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and value is not None and _is_string_collection(value):
            defs[target.id] = Definition(
                module=module,
                name=target.id,
                lineno=node.lineno,
                end_lineno=getattr(node, "end_lineno", node.lineno),
            )
    return defs


def _import_origins(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """local name -> (origin module-relpath, origin symbol) for `from orchestrator... import`.

    Resolves ABSOLUTE `orchestrator.` imports only; `src/orchestrator` uses absolute imports
    uniformly. A relative import (`from .x import Y`) would resolve to nothing here -- a
    false-negative surface if the repo ever adopts relative imports.
    """
    origins: dict[str, tuple[str, str]] = {}
    prefix = "orchestrator."
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(prefix):
            rel = node.module[len(prefix) :].replace(".", "/") + ".py"
            for alias in node.names:
                origins[alias.asname or alias.name] = (rel, alias.name)
    return origins


def _db_pinned_names(tree: ast.Module) -> set[str]:
    """Module-level names referenced inside a CheckConstraint(...) call -- the DB pins them, so the
    schema is their source of truth and validator, and they are not un-guarded vocabularies. Covers
    the common `CheckConstraint(f"col IN {NAME!r}")` shape (a Name inside an f-string) as well as a
    bare Name argument."""
    pinned: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CheckConstraint"
        ):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name):
                    pinned.add(inner.id)
    return pinned


def _membership_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(comp, ast.Name):
                    names.add(comp.id)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get"}
            and isinstance(node.func.value, ast.Name)
        ):
            names.add(node.func.value.id)
    return names


def _scan() -> tuple[dict[str, Definition], set[str], dict[str, str]]:
    """Returns (definitions by 'module:name', used def-keys, source text by module)."""
    trees: dict[str, ast.Module] = {}
    sources: dict[str, str] = {}
    definitions: dict[str, Definition] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        module = _module_key(path)
        text = path.read_text(encoding="utf-8")
        sources[module] = text
        trees[module] = ast.parse(text)
    for module, tree in trees.items():
        db_pinned = _db_pinned_names(tree)
        for name, definition in _collect_definitions(module, tree).items():
            if name in db_pinned:
                continue  # schema-pinned enum: the DB is its source of truth and validator
            definitions[f"{module}:{name}"] = definition

    used: set[str] = set()
    for module, tree in trees.items():
        local_defs = {d.name for d in definitions.values() if d.module == module}
        origins = _import_origins(tree)
        for name in _membership_names(tree):
            if name in local_defs:
                used.add(f"{module}:{name}")
            elif name in origins:
                origin_module, origin_symbol = origins[name]
                key = f"{origin_module}:{origin_symbol}"
                if key in definitions:
                    used.add(key)
    return definitions, used, sources


def _has_exempt_marker(definition: Definition, source: str) -> bool:
    # A marker sits in the comment block immediately preceding the definition (or inline). Scan
    # upward across contiguous comment/blank lines so a multi-line marker comment is found.
    lines = source.splitlines()
    if any(EXEMPT_MARKER in line for line in lines[definition.lineno - 1 : definition.end_lineno]):
        return True
    index = definition.lineno - 2  # 0-indexed line directly above the definition
    while index >= 0 and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
        if EXEMPT_MARKER in lines[index]:
            return True
        index -= 1
    return False


def test_every_membership_tested_string_vocabulary_is_registered_or_exempt() -> None:
    definitions, used, sources = _scan()

    unresolved: list[str] = []
    for key in sorted(used):
        if key in VOCABULARY_REGISTRY:
            continue
        definition = definitions[key]
        if _has_exempt_marker(definition, sources[definition.module]):
            continue
        unresolved.append(f"{key} (line {definition.lineno})")

    assert not unresolved, (
        "Membership-tested string vocabularies that are neither registered in "
        "VOCABULARY_REGISTRY nor marked '# not-a-vocabulary:':\n"
        + "\n".join(f"  - {item}" for item in unresolved)
        + "\nRegister it (name the cross-boundary source of truth) or mark it not-a-vocabulary."
    )


def test_registry_entries_all_correspond_to_a_real_definition() -> None:
    # A stale registry entry (the vocabulary was renamed or deleted) is a guard pointing at
    # nothing. Keep the registry honest.
    definitions, _, _ = _scan()
    stale = sorted(key for key in VOCABULARY_REGISTRY if key not in definitions)
    assert not stale, f"VOCABULARY_REGISTRY entries with no matching definition: {stale}"


def test_the_detector_run_from_repo_root() -> None:
    # The scan is cwd-anchored on SOURCE_ROOT; guard against being run from a subdirectory where it
    # would silently find nothing (the security-scanner cwd-allowlist trap, portfolio-wide).
    assert (REPO_ROOT / SOURCE_ROOT).is_dir(), "run the suite from the repo root"
    assert _scan()[0], "the vocabulary scan found no definitions -- wrong cwd?"
