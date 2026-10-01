"""What the word guards read: the CODE in a source file, never its prose (ADR-0051).

A word guard asks whether runtime code names a forbidden thing. It used to read every string in a
module, docstrings included, and fourteen recorded incidents were a guard going red on a sentence
that described the code. So the guards read only:

* identifiers -- names, attributes, definitions;
* import paths;
* string constants with no whitespace that are not docstrings: route paths, header names, literal
  identifiers such as `"workflow_dispatch"`.

A string containing whitespace is prose: a message, a description, a sentence. A docstring is prose
whatever it contains. Command strings like `"gh pr merge 1"` contain whitespace too, and they are
not lost: `test_wsp21_invariant_scan.py` and `test_no_automatic_merge.py` read raw file text for
the merge commands, and this change does not touch them.
"""

from __future__ import annotations

import ast
from pathlib import Path

_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def docstring_nodes(tree: ast.AST) -> set[int]:
    """The ids of every docstring constant in the tree."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _DOCUMENTED) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def is_code_string(value: str) -> bool:
    """A non-empty string with no whitespace once its ends are stripped. Anything else is prose."""
    stripped = value.strip()
    return bool(stripped) and not any(character.isspace() for character in stripped)


def code_strings(tree: ast.AST) -> list[str]:
    """Every string constant in the tree that is code rather than prose, with its ends stripped."""
    docstrings = docstring_nodes(tree)
    return [
        node.value.strip()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and is_code_string(node.value)
    ]


def identifier_of(node: ast.AST) -> tuple[str, str] | None:
    """The `(kind, name)` a node introduces or uses, or None. Imports are `identifier_terms`'."""
    if isinstance(node, ast.Name):
        return "name", node.id
    if isinstance(node, ast.Attribute):
        return "attribute", node.attr
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return "definition", node.name
    if isinstance(node, ast.arg):
        return "parameter", node.arg
    if isinstance(node, ast.keyword) and node.arg is not None:
        return "keyword", node.arg
    return None


def identifier_terms(tree: ast.AST) -> list[tuple[str, str]]:
    """Every identifier and import path in the tree, as `(kind, value)` pairs."""
    terms: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            terms.extend(("import", alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                terms.append(("import-from", node.module))
            terms.extend(("import-name", alias.name) for alias in node.names)
        elif (identifier := identifier_of(node)) is not None:
            terms.append(identifier)
    return terms


def code_text(path: Path) -> str:
    """A file's code terms, lowercased and one per line, for the guards that match substrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    terms = [value for _, value in identifier_terms(tree)]
    terms.extend(code_strings(tree))
    return "\n".join(terms).lower()
