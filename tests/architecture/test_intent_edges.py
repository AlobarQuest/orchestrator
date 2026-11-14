"""Architecture guard: one test states what the Intent layer's package reader reaches and reads.

`orchestrator.package_sources` verifies an approved intent package before it composes an intake
payload. Its whole outbound surface is stated here, from its source:

* four subprocess commands: `git rev-parse HEAD` (the package's git head), the sibling
  `intent_packages verify-approval`, security-standards' `factory_events verify` (the file
  chain), and `orchestrator.factory_chain_read` (the database chain, under the read password);
* no HTTP client, socket or other network import;
* eight environment variables: two of them FE-1's chain passwords, which select the chain, and
  three its retired whole-DSN variables, which refuse by name (ADR-0055 amendment 26);
* files read: the package's `package.yaml` and `lineage.yaml`, the security-standards agent
  registry, and the factory events file.

`orchestrator.factory_chain_read`, the database read it runs, is stated beside it: no subprocess,
no environment variable of its own (FE-1's `chain` reads the password and settings), three lazy
imports (psycopg, FE-1's `store` and `chain`), a connection as the read role only, one `SELECT`,
and a read-only repeatable-read transaction.

Each row is held to the module's text, and each control shows the comparison reporting a
deliberate widening.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import orchestrator.factory_chain_read as factory_chain_read
import orchestrator.package_sources as package_sources

SOURCE = Path(package_sources.__file__).read_text(encoding="utf-8")
CHAIN_READ_SOURCE = Path(factory_chain_read.__file__).read_text(encoding="utf-8")

SUBPROCESS_COMMANDS = frozenset(
    {
        ("git", "-C", "<expr>", "rev-parse", "HEAD"),
        ("<expr>", "-m", "intent_packages", "verify-approval", "<expr>"),
        ("<expr>", "-m", "factory_events", "verify"),
        ("<expr>", "-m", "orchestrator.factory_chain_read"),
    }
)
NETWORK_ROOTS = frozenset(
    {"httpx", "requests", "urllib", "urllib3", "http", "socket", "aiohttp", "ftplib", "smtplib"}
)
ENVIRONMENT = frozenset(
    {
        "PYTHONPATH",
        "SECURITY_STANDARDS_DIR",
        "FACTORY_EVENTS_HOME",
        "FACTORY_EVENTS_READ_PASSWORD",
        "FACTORY_EVENTS_APPEND_PASSWORD",
        "FACTORY_EVENTS_APPEND_DSN",
        "FACTORY_EVENTS_READ_DSN",
        "FACTORY_EVENTS_IMPORT_DSN",
    }
)
YAML_READS = frozenset({"package.yaml", "lineage.yaml", "<registry agent>"})
NEVER_IMPORTED = frozenset({"pty", "multiprocessing"})
OS_SPAWNERS = frozenset({"os.system", "os.popen", "os.execv", "os.execvp", "os.spawnv"})
READERS = frozenset({"_read_yaml", "_iter_package_approval_events"})
EVENTS_FILE_PARTS = frozenset({".factory", "events.jsonl"})
CHAIN_READ_ENVIRONMENT: frozenset[str] = frozenset()
CHAIN_READ_LAZY_IMPORTS = frozenset({"psycopg", "factory_events.store", "factory_events.chain"})
CHAIN_READ_CONNECTIONS = ["chain.READ_ROLE"]
CHAIN_READ_WALK = "SELECT seq, prev_hash, hash, event_id, event FROM factory_events ORDER BY seq"


def _render(node: ast.expr) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return "<expr>"


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return "<expr>"


def subprocess_commands(source: str) -> list[tuple[str, ...]]:
    commands: list[tuple[str, ...]] = []
    for call in _calls(ast.parse(source)):
        name = _dotted(call.func)
        if name.startswith(("subprocess.", "asyncio.create_subprocess_")) or name in OS_SPAWNERS:
            first = call.args[0] if call.args else None
            if name == "subprocess.run" and isinstance(first, ast.List):
                commands.append(tuple(_render(item) for item in first.elts))
            else:
                commands.append((name,))
    return commands


def spawn_imports(source: str) -> list[str]:
    """Imports that dotted-name matching would miss or that reach a process another way.

    `subprocess` and `os` may be imported only as themselves (no from-import, no alias);
    `pty` and `multiprocessing` may not be imported at all.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in {"subprocess", "os", *NEVER_IMPORTED}:
                found.append(f"from {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in NEVER_IMPORTED:
                    found.append(alias.name)
                elif root in {"subprocess", "os"} and alias.asname:
                    found.append(f"{alias.name} as {alias.asname}")
    return found


def network_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in NETWORK_ROOTS]
        elif (
            isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in NETWORK_ROOTS
        ):
            found.append(node.module or "")
    return found


def environment_names(source: str) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _dotted(node.func) in {"os.environ.get", "os.getenv"}:
            names.add(_render(node.args[0]))
        elif isinstance(node, ast.Call) and _dotted(node.func) == "env.get":
            names.add(_render(node.args[0]))
        elif isinstance(node, ast.Subscript) and _dotted(node.value) in {"os.environ", "env"}:
            names.add(_render(node.slice))
    return frozenset(names)


def readers(source: str) -> frozenset[str]:
    """The functions that read file contents, by name; a read outside any function is `<module>`."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found: set[str] = set()
    for call in _calls(tree):
        name = _dotted(call.func)
        if not (name == "open" or name.endswith((".read_text", ".read_bytes", ".open"))):
            continue
        scope: ast.AST | None = call
        while scope is not None and not isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef):
            scope = parents.get(scope)
        found.add(scope.name if scope is not None else "<module>")
    return frozenset(found)


def yaml_reads(source: str) -> frozenset[str]:
    """What each `_read_yaml(...)` call is handed: the package's two files or a registry agent."""
    found: set[str] = set()
    for call in _calls(ast.parse(source)):
        if _dotted(call.func) != "_read_yaml":
            continue
        arg = call.args[0]
        if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Div):
            found.add(_render(arg.right))
        elif isinstance(arg, ast.Name) and arg.id == "agent_path":
            found.add("<registry agent>")
        else:
            found.add("<unrecognised>")
    return frozenset(found)


def path_parts(source: str, function: str) -> frozenset[str]:
    """The string constants a function builds its path from."""
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
    return frozenset(
        c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)
    )


# ---- The rows -------------------------------------------------------------------------------


def test_the_module_runs_exactly_four_subprocess_commands() -> None:
    commands = subprocess_commands(SOURCE)
    assert len(commands) == 4
    assert set(commands) == SUBPROCESS_COMMANDS


def test_processes_are_reached_only_through_plain_imports() -> None:
    assert spawn_imports(SOURCE) == []


def test_the_module_imports_no_network_client() -> None:
    assert network_imports(SOURCE) == []


def test_the_module_reads_eight_environment_variables() -> None:
    assert environment_names(SOURCE) == ENVIRONMENT


def test_the_module_reads_files_in_two_places_only() -> None:
    assert readers(SOURCE) == READERS


def test_the_yaml_it_reads_is_the_package_two_files_and_a_registry_agent() -> None:
    assert yaml_reads(SOURCE) == YAML_READS


def test_the_events_file_and_the_registry_sit_where_they_are_stated() -> None:
    assert path_parts(SOURCE, "_factory_events_file") >= EVENTS_FILE_PARTS
    assert "registry" in path_parts(SOURCE, "_security_registry_dir")
    assert path_parts(SOURCE, "_is_human_operator") >= {"agents"}


# ---- Controls: each comparison reports a deliberate widening -------------------------------


def _widen(old: str, new: str) -> str:
    assert old in SOURCE
    return SOURCE.replace(old, new, 1)


def test_control_a_fourth_subprocess_command_is_reported() -> None:
    widened = SOURCE + '\n\ndef _extra() -> None:\n    subprocess.run(["curl", "http://x"])\n'
    assert len(subprocess_commands(widened)) == 5


def test_control_a_changed_subcommand_is_reported() -> None:
    widened = _widen('"verify-approval"', '"approve"')
    assert set(subprocess_commands(widened)) != SUBPROCESS_COMMANDS


def test_control_a_shell_spawn_is_reported() -> None:
    widened = SOURCE + '\n\ndef _extra() -> None:\n    os.system("ls")\n'
    assert ("os.system",) in subprocess_commands(widened)


def test_control_a_network_import_is_reported() -> None:
    assert network_imports("import httpx\n" + SOURCE) == ["httpx"]
    assert network_imports("from urllib.request import urlopen\n" + SOURCE) == ["urllib.request"]


def test_control_a_new_environment_variable_is_reported() -> None:
    widened = SOURCE + '\n\ndef _extra() -> str | None:\n    return os.environ.get("HOME")\n'
    assert environment_names(widened) != ENVIRONMENT


def test_control_a_new_reader_is_reported() -> None:
    widened = SOURCE + "\n\ndef _extra(p: Path) -> str:\n    return p.read_text()\n"
    assert readers(widened) != READERS


def test_control_a_new_yaml_file_is_reported() -> None:
    widened = _widen('resolved_path / "lineage.yaml"', 'resolved_path / "secrets.yaml"')
    assert yaml_reads(widened) != YAML_READS


def test_control_a_moved_events_file_is_reported() -> None:
    widened = _widen('"events.jsonl"', '"elsewhere.jsonl"')
    assert not path_parts(widened, "_factory_events_file") >= EVENTS_FILE_PARTS


@pytest.mark.parametrize("function", ["_factory_events_file", "_security_registry_dir"])
def test_control_the_path_check_finds_its_function(function: str) -> None:
    assert path_parts(SOURCE, function)


def test_control_an_aliased_or_from_import_of_subprocess_or_os_is_reported() -> None:
    assert spawn_imports("import subprocess as sp\n" + SOURCE) == ["subprocess as sp"]
    assert spawn_imports("from subprocess import run\n" + SOURCE) == ["from subprocess"]
    assert spawn_imports("import os as o\n" + SOURCE) == ["os as o"]
    assert spawn_imports("from os import system\n" + SOURCE) == ["from os"]


def test_control_pty_and_multiprocessing_imports_are_reported() -> None:
    assert spawn_imports("import pty\n" + SOURCE) == ["pty"]
    assert spawn_imports("from multiprocessing import Process\n" + SOURCE) == [
        "from multiprocessing"
    ]


def test_control_an_asyncio_subprocess_is_reported() -> None:
    widened = (
        SOURCE + "\n\nasync def _extra() -> None:\n    await asyncio.create_subprocess_exec('id')\n"
    )
    assert ("asyncio.create_subprocess_exec",) in subprocess_commands(widened)


def test_control_a_read_outside_any_function_is_recorded_as_the_module() -> None:
    widened = SOURCE + '\n_TEXT = Path("x").read_text()\n'
    assert readers(widened) == READERS | {"<module>"}


def test_control_check_output_and_a_held_argv_are_reported() -> None:
    widened = (
        SOURCE
        + "\n\ndef _extra() -> None:\n"
        + '    subprocess.check_output(["id"])\n'
        + '    argv = ["id"]\n    subprocess.run(argv)\n'
    )
    commands = subprocess_commands(widened)
    assert len(commands) == 6
    assert set(commands) != SUBPROCESS_COMMANDS
    assert ("subprocess.check_output",) in commands
    assert ("subprocess.run",) in commands


# ---- The database read `package_sources` runs -----------------------------------------------


def lazy_imports(source: str) -> frozenset[str]:
    """What `import_module(...)` is handed, as written."""
    return frozenset(
        _render(call.args[0])
        for call in _calls(ast.parse(source))
        if _dotted(call.func) == "import_module"
    )


def module_imports(source: str) -> frozenset[str]:
    """The roots of every `import` and `from ... import` statement."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    return frozenset(roots)


def attribute_assignments(source: str) -> dict[str, str]:
    """`<name>.<attr> = <value>` assignments, rendered."""
    found: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Attribute):
            found[node.targets[0].attr] = (
                _dotted(node.value)
                if isinstance(node.value, ast.Attribute)
                else ast.unparse(node.value)
            )
    return found


def connections(source: str) -> list[str]:
    """The role each `chain.connection_params(...)` call is handed, as written."""
    return [
        ast.unparse(call.args[0]) if call.args else "<none>"
        for call in _calls(ast.parse(source))
        if _dotted(call.func) == "chain.connection_params"
    ]


def test_the_chain_read_runs_no_subprocess_and_reads_no_environment_variable() -> None:
    assert subprocess_commands(CHAIN_READ_SOURCE) == []
    assert spawn_imports(CHAIN_READ_SOURCE) == []
    assert environment_names(CHAIN_READ_SOURCE) == CHAIN_READ_ENVIRONMENT
    assert readers(CHAIN_READ_SOURCE) == frozenset()


def test_the_chain_read_reaches_the_database_only_through_its_three_lazy_imports() -> None:
    assert network_imports(CHAIN_READ_SOURCE) == []
    assert lazy_imports(CHAIN_READ_SOURCE) == CHAIN_READ_LAZY_IMPORTS
    assert module_imports(CHAIN_READ_SOURCE) == {
        "__future__",
        "json",
        "sys",
        "collections",
        "importlib",
        "typing",
    }


def test_the_chain_read_connects_once_and_only_as_the_read_role() -> None:
    assert connections(CHAIN_READ_SOURCE) == CHAIN_READ_CONNECTIONS


def test_the_chain_read_runs_one_select_in_a_read_only_repeatable_read_transaction() -> None:
    assert factory_chain_read.WALK == CHAIN_READ_WALK
    assert attribute_assignments(CHAIN_READ_SOURCE) == {
        "read_only": "True",
        "isolation_level": "psycopg.IsolationLevel.REPEATABLE_READ",
    }
    executes = [c for c in _calls(ast.parse(CHAIN_READ_SOURCE)) if _dotted(c.func) == "cur.execute"]
    assert [ast.unparse(c.args[0]) for c in executes] == ["WALK"]


def test_control_a_widened_chain_read_is_reported() -> None:
    def widen(old: str, new: str) -> str:
        assert old in CHAIN_READ_SOURCE
        return CHAIN_READ_SOURCE.replace(old, new, 1)

    assert lazy_imports(widen('"factory_events.store"', '"factory_events.db"')) != (
        CHAIN_READ_LAZY_IMPORTS
    )
    assert (
        environment_names(
            widen(
                "params = chain.connection_params",
                'os.environ.get("FACTORY_EVENTS_APPEND_PASSWORD")\n'
                "        params = chain.connection_params",
            )
        )
        != CHAIN_READ_ENVIRONMENT
    )
    assert (
        connections(
            widen("connection_params(chain.READ_ROLE)", "connection_params(chain.APPEND_ROLE)")
        )
        != CHAIN_READ_CONNECTIONS
    )
    assert "read_only" not in attribute_assignments(widen("conn.read_only = True", "pass"))
    assert module_imports("import socket\n" + CHAIN_READ_SOURCE) != module_imports(
        CHAIN_READ_SOURCE
    )
