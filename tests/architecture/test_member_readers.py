"""Every program that reads the member record handles an unreadable one.

The unit is the package: a client module reads the record and the package's `cli.py` catches.

An empty record is `MembersUnreadable` in the shared client, so a reader that does not catch it
crashes instead of stopping cleanly. A module that calls `read_members` or
`read_members_from_environment`, or names the `/api/v1/members` path, must reference
`MembersUnreadable`. `rotation_worker` and `rotation_decomposer` read the route with their own
clients and refuse an empty record as `GrantUnread` (`rotation_worker/grants.py`), so that name
also satisfies the check. The check reads names, not behaviour.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
READERS = {"read_members", "read_members_from_environment"}
MEMBERS_PATH = "/api/v1/members"
HANDLERS = {"MembersUnreadable", "GrantUnread"}
# The route's own definitions: the client and the server.
EXEMPT = ("sds_members", "orchestrator")


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
    }
    assert readers_without_a_handler(synthetic) == ["path"]
    assert readers_without_a_handler({"lane/cli.py": synthetic["lane/cli.py"]}) == ["lane"]
