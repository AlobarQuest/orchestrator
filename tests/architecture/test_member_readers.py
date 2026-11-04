"""Every program that reads the member record handles an unreadable one.

The unit is the package: a client module reads the record and the package's `cli.py` catches.
`scripts/` modules are not packages, so each one is held to the rule on its own.

An empty record is `MembersUnreadable` in the shared client, so a reader that does not catch it
crashes instead of stopping cleanly. A module that calls `read_members`,
`read_members_from_environment` or `parse_members`, or names the `/api/v1/members` path, must
reference `MembersUnreadable`. `rotation_worker` and `rotation_decomposer` read the route with
their own clients and refuse an empty record as `GrantUnread` (`rotation_worker/grants.py`), so
that name also satisfies the check. The check reads names, not behaviour.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
READERS = {"read_members", "read_members_from_environment", "parse_members"}
MEMBERS_PATH = "/api/v1/members"
HANDLERS = {"MembersUnreadable", "GrantUnread"}
# The route's own definitions: the client and the server.
EXEMPT = ("sds_members", "orchestrator")
# A script that names the path and handles nothing, with the reason it may.
EXEMPT_SCRIPTS = {
    # A one-off, read-only replay for a recorded analysis: it prints what it fetched and decides
    # nothing, so an empty record is just an empty input.
    "replay_deploy_signal.py": "read-only replay, acts on nothing",
}


def _names(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.split(".")[-1])
        elif isinstance(node, ast.Constant) and node.value == MEMBERS_PATH:
            found.add(MEMBERS_PATH)
    return found


def _reading(modules: dict[str, str]) -> set[str]:
    return {
        name.split("/")[0]
        for name, source in modules.items()
        if (names := _names(source)) & READERS or MEMBERS_PATH in names
    }


def readers_without_a_handler(modules: dict[str, str]) -> list[str]:
    """The packages that read the record and reference no unreadable handler anywhere."""
    handled = {name.split("/")[0] for name, src in modules.items() if _names(src) & HANDLERS}
    return sorted(_reading(modules) - handled)


def _source_modules() -> dict[str, str]:
    return {
        str(path.relative_to(SRC)): path.read_text()
        for path in SRC.rglob("*.py")
        if path.relative_to(SRC).parts[0] not in EXEMPT
    }


def _script_modules() -> dict[str, str]:
    return {
        path.name: path.read_text()
        for path in SCRIPTS.glob("*.py")
        if path.name not in EXEMPT_SCRIPTS
    }


def scripts_without_a_handler(scripts: dict[str, str]) -> list[str]:
    """The scripts that read the record and reference no unreadable handler themselves."""
    return sorted(
        name
        for name, source in scripts.items()
        if ((names := _names(source)) & READERS or MEMBERS_PATH in names) and not names & HANDLERS
    )


def test_every_script_that_reads_the_member_record_handles_unreadable():
    scripts = _script_modules()
    assert _names(scripts["sds_scope.py"]) & READERS
    assert scripts_without_a_handler(scripts) == []


def test_a_script_with_no_handler_is_reported():
    reading = "from sds_members.client import read_members\nread_members()\n"
    handled = reading + "from sds_members.client import MembersUnreadable\n"
    path_only = "URL = '/api/v1/members'\n"
    assert scripts_without_a_handler({"a.py": reading, "b.py": handled, "c.py": path_only}) == [
        "a.py",
        "c.py",
    ]


def test_every_reader_of_the_member_record_handles_unreadable():
    modules = _source_modules()
    population = _reading(modules)
    assert len(population) >= 10, population
    assert readers_without_a_handler(modules) == []


def test_a_reader_with_no_handler_is_reported():
    synthetic = {
        "lane/cli.py": "from sds_members.client import read_members\nread_members()\n",
        "lane/ok.py": "from sds_members.client import read_members, MembersUnreadable\n",
        "path/x.py": "URL = '/api/v1/members'\n",
        "parse/cli.py": "from sds_members.client import parse_members\nparse_members({})\n",
    }
    assert readers_without_a_handler(synthetic) == ["parse", "path"]
    assert readers_without_a_handler({"lane/cli.py": synthetic["lane/cli.py"]}) == ["lane"]
