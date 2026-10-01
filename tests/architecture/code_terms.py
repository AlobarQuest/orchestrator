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
    """A non-empty string with no whitespace in it. Anything else is prose."""
    return bool(value) and not any(character.isspace() for character in value)


def code_strings(tree: ast.AST) -> list[str]:
    """Every string constant in the tree that is code rather than prose."""
    docstrings = docstring_nodes(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and is_code_string(node.value)
    ]


def code_text(path: Path) -> str:
    """A file's code terms, lowercased and one per line, for the guards that match substrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    terms: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            terms.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            terms.append(node.module)
        elif isinstance(node, ast.Name):
            terms.append(node.id)
        elif isinstance(node, ast.Attribute):
            terms.append(node.attr)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            terms.append(node.name)
    terms.extend(code_strings(tree))
    return "\n".join(terms).lower()
