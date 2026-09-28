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
    Path("src/orchestrator/services/reporting/dead_letter.py"),
    Path("src/orchestrator/api/schemas.py"),
    Path("src/orchestrator/config.py"),
    Path("src/orchestrator/persistence/models.py"),
    Path("src/orchestrator/services/execution/dispatch.py"),
    Path("src/orchestrator/services/github_app.py"),
    # The verifier evidence command reads the immutable dispatch identity to bind an externally
    # observed named check to the exact unit attempt. It cannot initiate workflow execution.
    Path("src/orchestrator/services/verifier/verifier_evidence.py"),
    # Verification re-reads that dispatch identity before trusting stored named-check evidence.
    # The helper is read-only apart from locking the canonical PR binding through transition.
    Path("src/orchestrator/services/verifier/verifier_named_check.py"),
}
WS53_POST_DEPLOY_PATHS = {
    Path("src/orchestrator/services/release/deployment_observations.py"),
    # WS-P2.16: the capability vocabulary names `post_deploy_verification` -- the capability the
    # orchestrator mints for its own post-hoc verification units. That string literal (and the
    # module docstring describing it) is data, not a merge/dispatch/mutation path.
    Path("src/orchestrator/capability_vocabulary.py"),
    # WS-P2.1 AC-003: deploy_split_brain is DEFINED over post-deploy verification units -- it
    # reads post_deploy_work_unit_id and the elapsed time since that unit went SUBMITTED. It
    # reads that state; it never dispatches, deploys, or merges.
    Path("src/orchestrator/services/reconciliation/reconciliation_detection.py"),
    # WS-P2.1 AC-009: the runner's read surface reports whether a release binding has a
    # post-deploy verification unit -- has_post_deploy_unit=False IS the deploy-nobody-reported
    # signal. It reads that state; it never dispatches, deploys, or merges.
    Path("src/orchestrator/services/reconciliation/in_flight.py"),
    Path("src/orchestrator/services/release/event_publications.py"),
    Path("src/orchestrator/services/verifier/evidence.py"),
    Path("src/orchestrator/services/lifecycle.py"),
    Path("src/orchestrator/services/verifier/verifier_criteria.py"),
    Path("src/orchestrator/services/verifier/verifier_evaluators.py"),
}
# ADR-0020's named exception, in this guard. The two allowlists above are FILE-scoped: a path in
# them is excused from every forbidden sequence at once, including `deploy` and `coolify`. That is
# far wider than a merge exception needs to be, so this one is keyed by (path, label) -- a module
# admitted here may name the merge it performs and nothing else. It is EMPTY, and not because
# nothing lands a pull request: `services/landing/pr_merge.py` (ADR-0020) and
# `services/landing/estate_pr_merge.py` (ADR-0019) both do, and are exempted for it in
# `test_wsp21_invariant_scan.py::MERGE_EXEMPT_PATHS`. Neither spells either label below in a runtime
# string, so neither needs an entry here. The mechanism was built first, while nothing needed it, so
# that a first entry arrives into a door already shown to fire in both directions.
MERGE_LABELS = frozenset({"merge_pull_request", "auto_merge"})
MERGE_EXEMPT_PATHS: set[Path] = set()

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


def _forbidden_labels(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """EVERY forbidden sequence a term matches, not just the first.

    The merge exemption below is only as narrow as this is complete. `merge_pull_request` and
    `auto_merge` sit at indices 1 and 5 of `FORBIDDEN_SEQUENCES`, ahead of `coolify`, `dispatch`
    and `deploy` -- so a first-match-wins lookup reports a term like
    `"merge_pull_request then deploy"` as a merge and nothing else, and an exemption keyed on that
    answer excuses the deploy along with it. That is precisely the deploying merge ADR-0020 says
    the exception must be too narrow to cover.
    """
    return tuple(
        label for label, sequence in FORBIDDEN_SEQUENCES if _contains_sequence(tokens, sequence)
    )


def _match_forbidden_sequence(tokens: tuple[str, ...]) -> str | None:
    labels = _forbidden_labels(tokens)
    return labels[0] if labels else None


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
            labels = _forbidden_labels(term.tokens)
            if not labels:
                continue
            # EVERY label the term matched must be a merge term. A term carrying a merge phrase
            # AND a deploy is not excused by the merge exemption -- it is the thing the exemption
            # exists to keep refusing.
            if path in MERGE_EXEMPT_PATHS and set(labels) <= MERGE_LABELS:
                continue
            matches.add(f"{term.path} [{term.kind}] {term.value!r} matched {labels[0]!r}")
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


def test_ws32_merge_exemption_names_only_merge_labels() -> None:
    """The exemption may only ever excuse merge vocabulary. If a label were added here that is not
    a merge term, a module could name a deploy or a hosted platform under a merge exception --
    which is the "narrow enough that it cannot cover a deploying merge" clause of ADR-0020."""
    labels = {label for label, _ in FORBIDDEN_SEQUENCES}

    assert MERGE_LABELS <= labels
    assert all("merge" in label for label in MERGE_LABELS)


def test_ws32_merge_exemption_does_not_excuse_a_term_that_also_names_something_else() -> None:
    """The narrowness the exemption CLAIMS, asserted against the matcher rather than against the
    comment above it. `_match_forbidden_sequence` reports only the first label, so a check keyed
    on it would call every one of these a pure merge and wave it through."""
    for value in (
        "merge_pull_request then deploy to coolify",
        "auto_merge and then deploy",
        "mergePullRequest(); deploy()",
    ):
        labels = _forbidden_labels(_tokenize(value))

        assert set(labels) & MERGE_LABELS, value
        assert not set(labels) <= MERGE_LABELS, (
            f"{value!r} matched only merge labels {labels}, so the exemption would excuse it "
            "along with the deploy it also names"
        )

    assert set(_forbidden_labels(_tokenize("mergePullRequest(input: {})"))) <= MERGE_LABELS


def test_ws32_merge_exemption_names_only_files_that_exist_and_still_need_it() -> None:
    """The same rot check its wsp21 twin carries. Existence alone is not enough: an entry that
    outlives the merge code it was granted for goes on excusing merge vocabulary in a file that
    merges nothing -- an exemption nobody needs is an exemption nobody is watching."""
    missing = [str(path) for path in MERGE_EXEMPT_PATHS if not path.exists()]
    assert not missing, f"the merge exemption names files that no longer exist: {missing}"

    unused = [
        str(path)
        for path in MERGE_EXEMPT_PATHS
        if path.exists()
        and not any(
            set(_forbidden_labels(term.tokens)) & MERGE_LABELS
            for kind in (_iter_identifier_terms, _iter_string_terms)
            for term in kind(path, _parse_source(path))
        )
    ]
    assert not unused, (
        f"these files are exempt from the merge vocabulary but no longer name any of it: "
        f"{unused}. Remove them."
    )


def test_ws32_file_exemptions_name_only_files_that_exist_and_still_need_them() -> None:
    """The rot check for the two FILE-scoped allowlists, which had none. `_find_matches` skips a
    listed path with `in`, so an entry that no longer names a file -- a module moved or renamed --
    excuses nothing and reddens nothing, and the moved module's new path is scanned or not by
    accident. The second half is the merge exemption's own: an entry whose file no longer carries
    any forbidden term goes on excusing every term a later edit adds to it, unwatched."""
    for name, exempt in (
        ("WS42_DISPATCH_PATHS", WS42_DISPATCH_PATHS),
        ("WS53_POST_DEPLOY_PATHS", WS53_POST_DEPLOY_PATHS),
    ):
        missing = sorted(
            str(path)
            for path in exempt
            if not path.is_file() or not path.is_relative_to(SOURCE_ROOT)
        )
        assert not missing, f"{name} names no file inside the tree it scans: {missing}"

        unused = sorted(
            str(path)
            for path in exempt
            if not any(
                _forbidden_labels(term.tokens)
                for kind in (_iter_identifier_terms, _iter_string_terms)
                for term in kind(path, _parse_source(path))
            )
        )
        assert not unused, (
            f"{name} exempts files that no longer carry any forbidden term: {unused}. Remove them."
        )


def test_ws32_string_scanner_covers_spaced_forbidden_phrases() -> None:
    tree = ast.parse("value = 'production mutation'")
    terms = _iter_string_terms(Path("sample.py"), tree)

    assert [_match_forbidden_sequence(term.tokens) for term in terms] == ["production mutation"]


def test_verifier_named_check_dispatch_access_is_read_only() -> None:
    for path in (
        Path("src/orchestrator/services/verifier/verifier_evidence.py"),
        Path("src/orchestrator/services/verifier/verifier_named_check.py"),
    ):
        source = path.read_text(encoding="utf-8")
        for forbidden_reference in (
            "dispatch_workflow",
            "WorkflowDispatcher",
            "GitHubActionsDispatcher",
        ):
            assert forbidden_reference not in source
