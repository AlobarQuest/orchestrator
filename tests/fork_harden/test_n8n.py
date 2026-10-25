"""n8n-as-code's rules: the plugin runs the fork's build, and nothing from the npm registry."""

from __future__ import annotations

import json

import pytest

from fork_harden import n8n
from fork_harden.read_lines import digest

MANIFEST = n8n.PLUGIN_MANIFEST
SKILL = f"{n8n.PLUGIN_ROOT}/skills/n8n-architect/SKILL.md"
README = f"{n8n.PLUGIN_ROOT}/README.md"


def manifest(servers: object) -> str:
    return json.dumps({"name": "n8n-as-code", "mcpServers": servers}, indent=2) + "\n"


UPSTREAM = {"command": "npx", "args": ["-y", "@n8n-as-code/mcp"]}
PINNED = {"command": "n8nac", "args": ["mcp"]}


@pytest.mark.parametrize(
    "server",
    [
        UPSTREAM,
        {"command": "npx", "args": ["--yes", "@n8n-as-code/mcp"]},
        {"command": "npx", "args": ["-y", "@n8n-as-code/mcp@2.2.1"]},
    ],
)
def test_the_upstream_mcp_server_becomes_the_forks_build(server: dict[str, object]) -> None:
    text = manifest({"n8n-as-code": server})
    assert n8n.detect_mcp(MANIFEST, text) == (1,)
    fixed = n8n.fix_mcp(MANIFEST, text)
    assert json.loads(fixed)["mcpServers"] == {"n8n-as-code": PINNED}
    assert n8n.detect_mcp(MANIFEST, fixed) == ()


@pytest.mark.parametrize(
    "server",
    [
        {"command": "npx", "args": ["-y", "@other/mcp"]},
        {"command": "npx", "args": ["-y", "@n8n-as-code/mcp"], "env": {"A": "1"}},
        {"command": "node", "args": ["server.js"]},
        {"command": "n8nac", "args": ["mcp", "--cwd", "."]},
    ],
)
def test_any_other_server_is_left_as_written_and_holds(server: dict[str, object]) -> None:
    text = manifest({"n8n-as-code": PINNED, "extra": server})
    assert n8n.fix_mcp(MANIFEST, text) == text
    assert n8n.detect_mcp(MANIFEST, text) == (1,)


def test_a_manifest_with_no_server_has_nothing_to_fix() -> None:
    text = json.dumps({"name": "n8n-as-code"}) + "\n"
    assert n8n.detect_mcp(MANIFEST, text) == ()
    assert n8n.fix_mcp(MANIFEST, text) == text


@pytest.mark.parametrize(
    "text",
    [
        '{"mcpServers": {}, "mcpServers": {}}',
        '{"mcpServers": []}',
        "[]",
        "not json",
    ],
)
def test_a_manifest_the_fix_cannot_read_holds(text: str) -> None:
    assert n8n.detect_mcp(MANIFEST, text) == (1,)
    with pytest.raises(ValueError):
        n8n.fix_mcp(MANIFEST, text)


SKILL_TEXT = """---
name: n8n-architect
description: n8n # not a heading
---

# n8n Architect

Use `npx --yes n8nac` first. Use `npx --yes @n8n-as-code/n8n-manager` for tunnels.

```bash
npx --yes n8nac env status --json
npx -y n8nac@next env list
npx --yes @n8n-as-code/n8n-manager@next instance list
```
"""


def test_the_skill_runs_the_installed_n8nac_and_no_n8n_manager() -> None:
    assert len(n8n.detect_registry(SKILL, SKILL_TEXT)) == 4
    fixed = n8n.fix_registry(SKILL, SKILL_TEXT)
    assert n8n.detect_registry(SKILL, fixed) == ()
    assert "n8nac env status --json\nn8nac env list\n" in fixed
    assert "n8n-manager-not-installed instance list" in fixed
    assert fixed.index(n8n.MANAGER_NOTE) > fixed.index("# n8n Architect")
    assert fixed.count(n8n.MANAGER_NOTE) == 1


def test_the_note_goes_only_into_a_skill_that_names_the_manager() -> None:
    text = "# n8n Architect\n\nRun `npx --yes n8nac list`.\n"
    assert n8n.MANAGER_NOTE not in n8n.fix_registry(SKILL, text)
    readme = "Run `npx --yes @n8n-as-code/n8n-manager instance list`.\n"
    assert n8n.MANAGER_NOTE not in n8n.fix_registry(README, readme)


def test_a_skill_with_no_heading_for_the_note_holds() -> None:
    with pytest.raises(ValueError):
        n8n.fix_registry(SKILL, "Run `npx --yes @n8n-as-code/n8n-manager instance list`.\n")


def test_a_heading_inside_front_matter_does_not_take_the_note() -> None:
    text = "---\n# not this\n---\n# n8n Architect\nnpx --yes @n8n-as-code/n8n-manager x\n"
    fixed = n8n.fix_registry(SKILL, text)
    assert fixed.startswith("---\n# not this\n---\n# n8n Architect\n\n> **On this machine")


@pytest.mark.parametrize(
    "line",
    [
        "npx -y some-other-package",
        "pnpx n8nac list",
        "bunx n8nac list",
        "npm exec n8nac -- list",
        "npm x n8nac",
        "pnpm dlx n8nac",
        "yarn dlx n8nac",
        "see @n8n-as-code/n8n-manager docs",
        "n8nac@next is the prerelease",
    ],
)
def test_any_other_registry_run_is_left_as_written_and_holds(line: str) -> None:
    text = line + "\n"
    assert n8n.detect_registry(README, n8n.fix_registry(README, text)) == (1,)


def test_a_read_line_is_excused_only_while_it_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = '      "command": "npx",'
    monkeypatch.setattr(n8n, "READ_LINES", {(README, digest(line)): "a docs example"})
    assert n8n.detect_registry(README, line + "\n") == ()
    assert n8n.detect_registry(SKILL, line + "\n") == (1,)
    assert n8n.detect_registry(README, line + " \n") == (1,)
