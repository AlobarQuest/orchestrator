from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path("src/orchestrator")
WS42_DISPATCH_PATHS = {
    Path("src/orchestrator/api/routes.py"),
    # WS-P2.1 AC-005: the dead-letter view READS DispatchRecord rows and re-applies the shared
    # failure-signature predicate to derive open circuit breakers. It reads that state; it never
    # dispatches, deploys, or merges.
    Path("src/orchestrator/services/dead_letter.py"),
    Path("src/orchestrator/api/schemas.py"),
    Path("src/orchestrator/config.py"),
    Path("src/orchestrator/persistence/models.py"),
    Path("src/orchestrator/services/dispatch.py"),
    Path("src/orchestrator/services/github_app.py"),
}
WS53_POST_DEPLOY_PATHS = {
    Path("src/orchestrator/services/deployment_observations.py"),
    # WS-P2.1 AC-003: deploy_split_brain is DEFINED over post-deploy verification units -- it
    # reads post_deploy_work_unit_id and the elapsed time since that unit went SUBMITTED. It
    # reads that state; it never dispatches, deploys, or merges.
    Path("src/orchestrator/services/reconciliation_detection.py"),
    # WS-P2.1 AC-009: the runner's read surface reports whether a release binding has a
    # post-deploy verification unit -- has_post_deploy_unit=False IS the deploy-nobody-reported
    # signal. It reads that state; it never dispatches, deploys, or merges.
    Path("src/orchestrator/services/in_flight.py"),
    Path("src/orchestrator/services/event_publications.py"),
    Path("src/orchestrator/services/evidence.py"),
    Path("src/orchestrator/services/lifecycle.py"),
    Path("src/orchestrator/services/verifier_criteria.py"),
    Path("src/orchestrator/services/verifier_evaluators.py"),
}
# These are fixed identifiers in the production-drill and runtime-observation contracts. They name
# the recovery conditions being observed; they do not add a deployment, dispatch, or Coolify
# control path. Keep this allowlist term-specific so the WS-3.2 boundary still catches any other
# use of these capability words in the same modules.
FIXED_DRILL_CONTRACT_TERMS: frozenset[tuple[Path, str, str]] = frozenset(
    {
        (
            Path("src/orchestrator/services/packages.py"),
            "deploy",
            "deploy_split_brain",
        ),
        (
            Path("src/orchestrator/services/production_drills.py"),
            "dispatch",
            "dispatch_failure_signature_threshold",
        ),
        (
            Path("src/orchestrator/services/production_drills.py"),
            "dispatch",
            "dispatch_enabled",
        ),
        (
            Path("src/orchestrator/services/production_drills.py"),
            "deploy",
            "/deploy-split-brain",
        ),
        (
            Path("src/orchestrator/services/production_drills.py"),
            "deploy",
            "deploy-split-brain",
        ),
        (
            Path("src/orchestrator/services/production_drills.py"),
            "deploy",
            "_execute_fixed_deploy_split_brain",
        ),
        (
            Path("src/orchestrator/services/production_drills.py"),
            "deploy",
            "deploy_split_brain",
        ),
        (
            Path("src/orchestrator/services/production_drills.py"),
            "deploy",
            "deploy_split_brain_failed",
        ),
        (
            Path("src/orchestrator/services/production_drills.py"),
            "deploy",
            "fixed deploy split-brain condition was not persisted",
        ),
        (
            Path("src/orchestrator/services/production_drills.py"),
            "deploy",
            "fixed deploy split-brain scenario did not produce its required condition",
        ),
        (
            Path("src/orchestrator/services/production_drills.py"),
            "deploy",
            "post_deploy_work_unit_id",
        ),
        (
            Path("src/orchestrator/services/production_drills.py"),
            "deploy",
            "production_drill.deploy_split_brain",
        ),
        (
            Path("src/orchestrator/services/runtime_observations.py"),
            "coolify",
            "coolify_application_id",
        ),
    }
)
CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")
TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

FORBIDDEN_SEQUENCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("factory-event/v1", ("factory", "event", "v1")),
    ("merge_pull_request", ("merge", "pull", "request")),
    ("workflow_dispatch", ("workflow", "dispatch")),
    ("factory-runner", ("factory", "runner")),
    ("production mutation", ("production", "mutation")),
    ("auto_merge", ("auto", "merge")),
    ("productionmutation", ("productionmutation",)),
    ("coolify", ("coolify",)),
    ("dispatch", ("dispatch",)),
    ("deploy", ("deploy",)),
)


@dataclass(frozen=True)
class RuntimeTerm:
    path: Path
    kind: str
    value: str
    tokens: tuple[str, ...]


def _tokenize(value: str) -> tuple[str, ...]:
    normalized = CAMEL_BOUNDARY.sub("_", value).lower()
    return tuple(token for token in TOKEN_SPLIT.split(normalized) if token)


def _contains_sequence(tokens: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    size = len(sequence)
    return any(tokens[index : index + size] == sequence for index in range(len(tokens) - size + 1))


def _match_forbidden_sequence(tokens: tuple[str, ...]) -> str | None:
    for label, sequence in FORBIDDEN_SEQUENCES:
        if _contains_sequence(tokens, sequence):
            return label
    return None


def _parse_source(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _is_fixed_drill_contract_term(term: RuntimeTerm, label: str) -> bool:
    return (term.path, label, term.value) in FIXED_DRILL_CONTRACT_TERMS


def _iter_identifier_terms(path: Path, tree: ast.AST) -> list[RuntimeTerm]:
    terms: list[RuntimeTerm] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                terms.append(
                    RuntimeTerm(
                        path=path, kind="import", value=alias.name, tokens=_tokenize(alias.name)
                    )
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            terms.append(
                RuntimeTerm(
                    path=path,
                    kind="import-from",
                    value=node.module,
                    tokens=_tokenize(node.module),
                )
            )
        elif isinstance(node, ast.Name):
            terms.append(
                RuntimeTerm(path=path, kind="name", value=node.id, tokens=_tokenize(node.id))
            )
        elif isinstance(node, ast.Attribute):
            terms.append(
                RuntimeTerm(
                    path=path, kind="attribute", value=node.attr, tokens=_tokenize(node.attr)
                )
            )
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            terms.append(
                RuntimeTerm(
                    path=path, kind="definition", value=node.name, tokens=_tokenize(node.name)
                )
            )
    return terms


def _iter_string_terms(path: Path, tree: ast.AST) -> list[RuntimeTerm]:
    terms: list[RuntimeTerm] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value.strip()
        if not value:
            continue
        terms.append(RuntimeTerm(path=path, kind="string", value=value, tokens=_tokenize(value)))
    return terms


def _find_matches(term_kind: str) -> list[str]:
    matches: set[str] = set()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path in WS42_DISPATCH_PATHS or path in WS53_POST_DEPLOY_PATHS:
            continue
        tree = _parse_source(path)
        terms = (
            _iter_identifier_terms(path, tree)
            if term_kind == "identifier"
            else _iter_string_terms(path, tree)
        )
        for term in terms:
            label = _match_forbidden_sequence(term.tokens)
            if label is None or _is_fixed_drill_contract_term(term, label):
                continue
            matches.add(f"{term.path} [{term.kind}] {term.value!r} matched {label!r}")
    return sorted(matches)


def test_ws32_runtime_identifiers_add_no_forbidden_merge_dispatch_or_mutation_paths() -> None:
    matches = _find_matches("identifier")
    assert not matches, "Forbidden runtime identifiers found:\n" + "\n".join(
        f"- {match}" for match in matches
    )


def test_ws32_runtime_string_literals_add_no_forbidden_merge_dispatch_or_mutation_paths() -> None:
    matches = _find_matches("string")
    assert not matches, "Forbidden runtime string literals found:\n" + "\n".join(
        f"- {match}" for match in matches
    )


def test_ws32_string_scanner_covers_spaced_forbidden_phrases() -> None:
    tree = ast.parse("value = 'production mutation'")
    terms = _iter_string_terms(Path("sample.py"), tree)

    assert [_match_forbidden_sequence(term.tokens) for term in terms] == ["production mutation"]
