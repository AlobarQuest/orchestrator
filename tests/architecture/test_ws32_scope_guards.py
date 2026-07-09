from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path("src/orchestrator")
WS42_DISPATCH_PATHS = {
    Path("src/orchestrator/api/routes.py"),
    Path("src/orchestrator/api/schemas.py"),
    Path("src/orchestrator/config.py"),
    Path("src/orchestrator/persistence/models.py"),
    Path("src/orchestrator/services/dispatch.py"),
    Path("src/orchestrator/services/github_app.py"),
}
WS53_POST_DEPLOY_PATHS = {
    Path("src/orchestrator/services/deployment_observations.py"),
    Path("src/orchestrator/services/event_publications.py"),
    Path("src/orchestrator/services/evidence.py"),
    Path("src/orchestrator/services/lifecycle.py"),
    Path("src/orchestrator/services/verifier_criteria.py"),
    Path("src/orchestrator/services/verifier_evaluators.py"),
}
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
            if label is None:
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
