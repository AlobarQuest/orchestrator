"""n8n-as-code: the Claude plugin runs the fork's build, never a package fetched from npm.

THE FINDING. The plugin registers an MCP server that runs `npx -y @n8n-as-code/mcp`, and its skill
tells an agent to run `npx --yes n8nac` and `npx --yes @n8n-as-code/n8n-manager`. Each fetches and
runs upstream's newest npm release outside the fork's review. A version pin would not help: the
pinned package is still upstream's code, not the fork's.

THE FIX. The MCP server becomes `n8nac mcp`, which starts the server the tool installer built from
the fork (`packages/mcp`, beside the CLI). The skill's `npx --yes n8nac` becomes `n8nac`. Both
resolve through `<npm prefix>/bin/n8nac`, the link the installer's `n8nac` row owns, so that link
stays. n8n-manager is not installed on this machine (Devon, 2026-10-25), so its commands are
rewritten to a name that resolves to nothing, and the skill says so.

THE CHECK OVER-COUNTS: any `npx`, `pnpx`, `bunx`, `npm exec`, `npm x`, `pnpm dlx` or `yarn dlx` in
any file of the plugin, any mention of the n8n-manager package, and any `n8nac@` (a registry tag)
is the finding. An MCP server other than exactly `n8nac mcp` is the finding too.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from fork_harden.read_lines import ReadLines, is_read

PLUGIN_ROOT: Final = "plugins/claude/n8n-as-code"
PLUGIN_MANIFEST: Final = f"{PLUGIN_ROOT}/.claude-plugin/plugin.json"
SKILL_SUFFIX: Final = "/SKILL.md"

N8NAC: Final = "n8nac"
MCP_COMMAND: Final = N8NAC
MCP_ARGUMENTS: Final = ["mcp"]
MANAGER_PLACEHOLDER: Final = "n8n-manager-not-installed"

_REGISTRY_RUN: Final = re.compile(
    r"\b(?:npx|pnpx|bunx)\b|\bnpm\s+(?:exec|x)\b|\b(?:pnpm|yarn)\s+dlx\b"
    r"|@n8n-as-code/n8n-manager|\bn8nac@"
)
_NPX_N8NAC: Final = re.compile(r"\bnpx\s+(?:--yes|-y)\s+n8nac(?:@[\w.-]+)?(?![\w@/-])")
_NPX_MANAGER: Final = re.compile(
    r"\bnpx\s+(?:--yes|-y)\s+@n8n-as-code/n8n-manager(?:@[\w.-]+)?(?![\w@/-])"
)
_UPSTREAM_MCP_PACKAGE: Final = re.compile(r"@n8n-as-code/mcp(?:@[\w.-]+)?")

READ_LINES: Final[ReadLines] = {
    (
        f"{PLUGIN_ROOT}/README.md",
        "9b56eb359aec3152c1c1d353880bb55274e72fc634ce78f18daad77ea04c2e6c",
    ): "an example configuration for Claude Desktop, in the README a person reads; Claude Code "
    "never loads it",
}
"""Lines a person read at plugin 2.4.1 (2026-10-25) and found are not a run."""

MANAGER_NOTE: Final = (
    "> **On this machine the n8n-manager package is not installed** (Devon's decision, "
    f"2026-10-25). Commands written `{MANAGER_PLACEHOLDER}` are unavailable: skip the step and "
    "tell the user that a managed local instance or tunnel must be set up by hand."
)


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("a JSON object has a duplicate key")
    return dict(pairs)


def _manifest(text: str) -> dict[str, Any]:
    document = json.loads(text, object_pairs_hook=_refuse_duplicates)
    if not isinstance(document, dict):
        raise ValueError("the plugin manifest is not a JSON object")
    return document


def _servers(document: dict[str, Any]) -> dict[str, Any]:
    servers = document.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("`mcpServers` is not an object")
    return servers


def detect_mcp(path: str, text: str) -> tuple[int, ...]:
    del path
    try:
        servers = _servers(_manifest(text))
    except ValueError:
        return (1,)
    pinned = {"command": MCP_COMMAND, "args": MCP_ARGUMENTS}
    return () if all(server == pinned for server in servers.values()) else (1,)


def _is_upstream_mcp(server: object) -> bool:
    if not isinstance(server, dict) or set(server) != {"command", "args"}:
        return False
    args = server["args"]
    return (
        server["command"] == "npx"
        and isinstance(args, list)
        and len(args) == 2
        and args[0] in ("-y", "--yes")
        and isinstance(args[1], str)
        and _UPSTREAM_MCP_PACKAGE.fullmatch(args[1]) is not None
    )


def fix_mcp(path: str, text: str) -> str:
    del path
    document = _manifest(text)
    servers = _servers(document)
    if not any(_is_upstream_mcp(server) for server in servers.values()):
        return text
    for name, server in servers.items():
        if _is_upstream_mcp(server):
            servers[name] = {"command": MCP_COMMAND, "args": list(MCP_ARGUMENTS)}
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def detect_registry(path: str, text: str) -> tuple[int, ...]:
    return tuple(
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if _REGISTRY_RUN.search(line) and not is_read(READ_LINES, path, line)
    )


def _with_manager_note(text: str) -> str:
    """The note, as its own paragraph after the skill's first top-level heading."""
    lines = text.splitlines(keepends=True)
    in_front_matter = bool(lines) and lines[0].rstrip("\r\n") == "---"
    for index, line in enumerate(lines):
        if index > 0 and in_front_matter and line.rstrip("\r\n") == "---":
            in_front_matter = False
            continue
        if not in_front_matter and line.startswith("# "):
            return "".join([*lines[: index + 1], "\n", MANAGER_NOTE, "\n", *lines[index + 1 :]])
    raise ValueError("the skill has no top-level heading to put the n8n-manager note under")


def fix_registry(path: str, text: str) -> str:
    rewritten = _NPX_MANAGER.sub(MANAGER_PLACEHOLDER, _NPX_N8NAC.sub(N8NAC, text))
    if path.endswith(SKILL_SUFFIX) and MANAGER_PLACEHOLDER in rewritten:
        rewritten = _with_manager_note(rewritten)
    return rewritten
