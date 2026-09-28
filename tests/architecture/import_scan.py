"""The one import scanner the architecture guards share.

Every out-of-process isolation test used to carry its own `_imports` helper -- twelve copies in
three variants, which disagreed about relative imports (one family dropped them, one kept the bare
module name, and none resolved them to the package that owns them). There are no relative imports
under `src/` today, so the variants agreed by accident; this resolves them properly so the day one
arrives it is charged to the package it is written in.

Comments are not in the AST and docstrings are not imports, so neither can satisfy or trip a scan.
An import inside a function body or behind `if TYPE_CHECKING:` counts: `ast.walk` visits it, and a
guard that skipped deferred imports would be a guard a deferred import walks past.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Import:
    """One imported module, where it was written, and the top-level package it names."""

    path: Path
    line: int
    module: str

    @property
    def top(self) -> str:
        return self.module.split(".")[0]

    def describe(self, root: Path) -> str:
        return f"{self.path.relative_to(root)}:{self.line}: imports {self.module}"


def _dotted_package(root: Path, path: Path) -> list[str]:
    """The package a module belongs to, as dotted parts, given `root` is the source root."""
    parts = list(path.relative_to(root).with_suffix("").parts)
    return parts[:-1]  # `pkg/__init__` and `pkg/mod` both belong to `pkg`


def file_imports(path: Path, root: Path | None = None) -> list[Import]:
    """Every module one file imports. Relative imports resolve against `root` when it is given.

    Without `root` a relative import cannot be resolved, so it is reported under the name it was
    written with -- never dropped, because a dropped import is one no allowlist can refuse.
    """
    found: list[Import] = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            found += [Import(path, node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.append(Import(path, node.lineno, node.module))
                continue
            base: list[str] = []
            if root is not None and node.level:
                package = _dotted_package(root, path)
                climb = node.level - 1
                if climb > len(package):
                    # Above the top package: Python raises at import time, but a deferred import
                    # never runs. Report it as written -- its top-level name is then empty, which
                    # no allowlist permits -- rather than let slicing wrap it into a real name.
                    written = node.module or ",".join(alias.name for alias in node.names)
                    found.append(Import(path, node.lineno, "." * node.level + written))
                    continue
                base = package[: len(package) - climb]
            if node.module:
                found.append(Import(path, node.lineno, ".".join([*base, node.module])))
            else:
                found += [
                    Import(path, node.lineno, ".".join([*base, alias.name])) for alias in node.names
                ]
    return found


def file_import_names(path: Path) -> set[str]:
    """The dotted names one file imports -- the shape a per-file membership test wants."""
    return {found.module for found in file_imports(path)}


def package_imports(root: Path, package: str) -> list[Import]:
    """Every import written anywhere under `root/package`, relative imports resolved."""
    found: list[Import] = []
    for path in sorted((root / package).rglob("*.py")):
        found += file_imports(path, root)
    return found


def out_of_process_packages(src: Path) -> list[str]:
    """Every top-level directory under `src/` holding Python, other than the orchestrator.

    The population both the ADR-0039 guard and the isolation table answer for, defined once so
    the two cannot come to disagree about which programs exist.
    """
    return sorted(
        child.name
        for child in src.iterdir()
        if child.is_dir()
        and child.name != "orchestrator"
        and not child.name.startswith((".", "__"))
        and any(child.rglob("*.py"))
    )
