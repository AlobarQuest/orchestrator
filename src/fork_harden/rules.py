"""What is hardened, as a table: one rule per finding, and the forks that carry rules.

A RULE IS A CHECK, A FIX AND THE PATHS IT MAY EDIT. The check (`detect`) answers whether the
finding is present in one file; the fix rewrites that file. Both are pure functions of a path and
its text, so the program reads the fork only as git objects and never checks upstream's tree out.
The paths bound the fix: the guard in `harden.py` refuses a hardened tree that differs from `main`
anywhere a rule does not cover.

THE CHECK OVER-COUNTS, AND A FALSE HOLD IS THE CHEAP DIRECTION. A finding the check misses ships
to the operator machine unhardened, silently; a site it flags that is not really the finding holds
the branch until a person reads it (Code Brain lesson 2: narrow by structure, never by what a
command does). So a check is written to flag more than its fix can repair, and what it flags after
the fix is a hold.

THE THREE OUTCOMES COME FROM RUNNING THE CHECK ON BOTH TREES. On `main`, nothing flagged means
upstream fixed the finding and the rule can retire. On the hardened tree, anything flagged means
the fix no longer covers what upstream wrote, and the branch holds.

AN EMPTY TABLE COSTS NOTHING. A pass with no fork listed makes no read at all (ADR-0057: dormant
when idle).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase

from fork_harden import n8n, octo


@dataclass(frozen=True)
class Rule:
    """One finding, its check and its fix."""

    name: str
    """Short, stable, and written into every commit the rule contributes to."""

    finding: str
    """What the rule fixes and where the finding was recorded, for the report and the commit."""

    paths: tuple[str, ...]
    """`fnmatch` patterns over repository paths, matched case-sensitively. `*` also matches `/`,
    so `hooks/*` is everything under `hooks/`."""

    detect: Callable[[str, str], tuple[int, ...]]
    """`(path, text) -> line numbers` at which the finding is present; empty when it isn't."""

    fix: Callable[[str, str], str]
    """`(path, text) -> text` with the finding fixed, or unchanged when there is nothing it can
    fix. Raises `ValueError` when the file is not in a shape the fix knows, which holds."""

    def covers(self, path: str) -> bool:
        return any(fnmatchcase(path, pattern) for pattern in self.paths)


@dataclass(frozen=True)
class Fork:
    repository: str
    """`owner/name` on GitHub: the fork, never upstream."""

    rules: tuple[Rule, ...]
    """Applied in this order to each file they cover."""

    def covers(self, path: str) -> bool:
        return any(rule.covers(path) for rule in self.rules)


BRANCH = "hardened"
"""The branch every fork's hardened version is written to, and the one a tool row tracks."""

SOURCE_BRANCH = "main"
"""fork-sync's landed branch: the only input."""

INLINE_PYTHON = Rule(
    name="octo-inline-python-isolated",
    finding=(
        "inline Python (`python3 -c`, `-m`, `-`) puts the session's project directory first on "
        "the import path, so a repository's own `json.py` runs as Devon from octo's hooks "
        "(one-time review of v11.14.1, HIGH, 2026-10-20)"
    ),
    paths=("hooks/*", "scripts/*"),
    detect=octo.detect,
    fix=octo.fix,
)

MCP_SERVER = Rule(
    name="n8n-mcp-server-from-the-fork",
    finding=(
        "the plugin's MCP server runs `npx -y @n8n-as-code/mcp`, upstream's newest npm release, "
        "on every session (AlobarQuest/n8n-as-code #15, v2.6.0 review, medium, 2026-10-22)"
    ),
    paths=(n8n.PLUGIN_MANIFEST,),
    detect=n8n.detect_mcp,
    fix=n8n.fix_mcp,
)

REGISTRY_FETCH = Rule(
    name="n8n-skill-runs-no-registry-package",
    finding=(
        "the skill tells an agent to run `npx --yes n8nac` and `npx --yes "
        "@n8n-as-code/n8n-manager`, which resolve from npm rather than the fork (same review)"
    ),
    paths=(f"{n8n.PLUGIN_ROOT}/*",),
    detect=n8n.detect_registry,
    fix=n8n.fix_registry,
)

FORKS: tuple[Fork, ...] = (
    Fork("AlobarQuest/claude-octopus", (INLINE_PYTHON,)),
    Fork("AlobarQuest/n8n-as-code", (MCP_SERVER, REGISTRY_FETCH)),
)
