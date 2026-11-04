"""Architecture guard: one test states what the Intent layer's package reader reaches and reads.

`orchestrator.package_sources` verifies an approved intent package before it composes an intake
payload. Its whole outbound surface is stated here, from its source:

* three subprocess commands: `git rev-parse HEAD` (the package's git head), the sibling
  `intent_packages verify-approval`, and security-standards' `factory_events verify`;
* no HTTP client, socket or other network import;
* three environment variables;
* files read: the package's `package.yaml` and `lineage.yaml`, the security-standards agent
  registry, and the factory events file.

Each row is held to the module's text, and each control shows the comparison reporting a
deliberate widening.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import orchestrator.package_sources as package_sources

SOURCE = Path(package_sources.__file__).read_text(encoding="utf-8")

SUBPROCESS_COMMANDS = frozenset(
    {
        ("git", "-C", "<expr>", "rev-parse", "HEAD"),
        ("<expr>", "-m", "intent_packages", "verify-approval", "<expr>"),
        ("<expr>", "-m", "factory_events", "verify"),
    }
)
NETWORK_ROOTS = frozenset(
    {"httpx", "requests", "urllib", "urllib3", "http", "socket", "aiohttp", "ftplib", "smtplib"}
)
ENVIRONMENT = frozenset({"PYTHONPATH", "SECURITY_STANDARDS_DIR", "FACTORY_EVENTS_HOME"})
YAML_READS = frozenset({"package.yaml", "lineage.yaml", "<registry agent>"})
OS_SPAWNERS = frozenset({"os.system", "os.popen", "os.execv", "os.execvp", "os.spawnv"})
READERS = frozenset({"_read_yaml", "_iter_package_approval_events"})
EVENTS_FILE_PARTS = frozenset({".factory", "events.jsonl"})


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
        if name.startswith("subprocess.") or (name.startswith("os.") and name in OS_SPAWNERS):
            first = call.args[0] if call.args else None
            if name == "subprocess.run" and isinstance(first, ast.List):
                commands.append(tuple(_render(item) for item in first.elts))
            else:
                commands.append((name,))
    return commands


def subprocess_imports(source: str) -> list[str]:
    """Imports of subprocess that `subprocess.<name>` matching would miss: from-imports, aliases."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "subprocess":
            found.append(f"from {node.module}")
        elif isinstance(node, ast.Import):
            found += [
                f"{a.name} as {a.asname}"
                for a in node.names
                if a.name.split(".")[0] == "subprocess" and a.asname
            ]
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
    """The functions that read file contents, by name (`read_text`, `read_bytes`, `open`)."""
    found: set[str] = set()
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, ast.FunctionDef):
            continue
        for call in _calls(function):
            name = _dotted(call.func)
            if name == "open" or name.endswith((".read_text", ".read_bytes", ".open")):
                found.add(function.name)
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


def test_the_module_runs_exactly_three_subprocess_commands() -> None:
    commands = subprocess_commands(SOURCE)
    assert len(commands) == 3
    assert set(commands) == SUBPROCESS_COMMANDS


def test_subprocess_is_imported_only_as_itself() -> None:
    assert subprocess_imports(SOURCE) == []


def test_the_module_imports_no_network_client() -> None:
    assert network_imports(SOURCE) == []


def test_the_module_reads_three_environment_variables() -> None:
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
    assert len(subprocess_commands(widened)) == 4


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


def test_control_an_aliased_or_from_import_of_subprocess_is_reported() -> None:
    assert subprocess_imports("import subprocess as sp\n" + SOURCE) == ["subprocess as sp"]
    assert subprocess_imports("from subprocess import run\n" + SOURCE) == ["from subprocess"]


def test_control_check_output_and_a_held_argv_are_reported() -> None:
    widened = (
        SOURCE
        + "\n\ndef _extra() -> None:\n"
        + '    subprocess.check_output(["id"])\n'
        + '    argv = ["id"]\n    subprocess.run(argv)\n'
    )
    commands = subprocess_commands(widened)
    assert len(commands) == 5
    assert set(commands) != SUBPROCESS_COMMANDS
    assert ("subprocess.check_output",) in commands
    assert ("subprocess.run",) in commands
