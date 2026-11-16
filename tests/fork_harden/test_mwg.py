"""modern-web-guidance's rule: the skill runs the CLI installed here, never one fetched from npm."""

from __future__ import annotations

import pytest

from fork_harden import mwg
from fork_harden.read_lines import digest
from fork_harden.rules import FORKS

SKILL = mwg.SKILL
GUIDE = "skills/modern-web-guidance/guides/dialog.md"
EXTENSIONS = "skills/chrome-extensions/SKILL.md"

# The v1.0.1 skill's shape, cut down: the sentence at line 17, the three commands, the
# "Using npx / pnpx" section and the heading after it.
SKILL_TEXT = """---
name: modern-web-guidance
---

# Modern Web Guidance

### Step 1. Search Use Cases

Run `modern-web-guidance` directly with `npx` using a **2-5 word action-oriented English query**:

```sh
npx -y modern-web-guidance@latest search "<query>" --skill-version 2026_11_10-47b4c80e
```

```sh
npx -y modern-web-guidance@latest retrieve "<id>"
npx -y modern-web-guidance@latest legacy-scan <path> --json
```

## Using npx / pnpx

- **Runner & Permissions**: Prefer `pnpx` over `npx`; on Windows, use `npx.cmd`. Allowlist \
`npx -y modern-web-guidance@latest *` (or `pnpx modern-web-guidance@latest *`).
- **Sandboxes & Network**: Fetching `modern-web-guidance` requires outbound network access.
- **Offline**: If the command hangs due to being offline, retry with `npx --offline ...`.

## Guidelines

- Always search **first**.
"""

GUIDE_TEXT = """# Dialog

See `npx -y modern-web-guidance@latest retrieve "light-dismiss-a-dialog"` and
`npx -y modern-web-guidance@latest retrieve "html"`.
"""


def test_the_skill_runs_the_installed_cli_and_says_so() -> None:
    assert len(mwg.detect(SKILL, SKILL_TEXT)) == 7
    fixed = mwg.fix(SKILL, SKILL_TEXT)
    assert mwg.detect(SKILL, fixed) == ()
    assert 'modern-web-guidance search "<query>" --skill-version 2026_11_10-47b4c80e\n' in fixed
    assert 'modern-web-guidance retrieve "<id>"\nmodern-web-guidance legacy-scan <path>' in fixed
    assert "Run the installed `modern-web-guidance` command with a **2-5 word" in fixed
    assert mwg.RUNNER_HEADING not in fixed
    assert fixed.count(mwg.RUNNER_SECTION) == 1
    # The section is replaced up to the next heading, which stays.
    assert fixed.index(mwg.RUNNER_SECTION) < fixed.index("## Guidelines\n")
    assert "Offline" not in fixed


def test_a_guides_cross_references_run_the_installed_cli() -> None:
    assert mwg.detect(GUIDE, GUIDE_TEXT) == (3, 4)
    fixed = mwg.fix(GUIDE, GUIDE_TEXT)
    assert mwg.detect(GUIDE, fixed) == ()
    assert fixed.count('`modern-web-guidance retrieve "') == 2


@pytest.mark.parametrize(
    "written", ["npx -y modern-web-guidance list", "npx --yes modern-web-guidance@1.0.1 list"]
)
def test_any_tag_and_either_yes_flag_is_rewritten(written: str) -> None:
    assert mwg.fix(GUIDE, written + "\n") == "modern-web-guidance list\n"


def test_a_section_last_in_the_skill_ends_at_the_end_of_the_file() -> None:
    fixed = mwg.fix(SKILL, "# Skill\n\n## Using npx / pnpx\n\nPrefer `pnpx`.\n")
    assert fixed == "# Skill\n\n" + mwg.RUNNER_SECTION
    assert mwg.detect(SKILL, fixed) == ()


def test_a_command_closed_by_a_backtick_is_rewritten() -> None:
    assert mwg.fix(GUIDE, "Run `npx -y modern-web-guidance@latest`.\n") == (
        "Run `modern-web-guidance`.\n"
    )


def test_the_section_the_fix_writes_is_not_itself_a_finding() -> None:
    assert mwg.detect(SKILL, mwg.RUNNER_SECTION) == ()


def test_a_skill_with_nothing_to_fix_is_left_as_it_is() -> None:
    text = "# Modern Web Guidance\n\nRun `modern-web-guidance search`.\n"
    assert mwg.detect(SKILL, text) == ()
    assert mwg.fix(SKILL, text) == text
    assert mwg.fix(EXTENSIONS, text) == text


def test_the_section_is_replaced_only_in_the_skill() -> None:
    text = "## Using npx / pnpx\n\nPrefer `pnpx`.\n\n## Next\n"
    assert mwg.fix(GUIDE, text) == text
    assert mwg.detect(GUIDE, text) == (1, 3)


@pytest.mark.parametrize(
    "text",
    [
        # The runner is still named and there is no section to replace.
        "# Modern Web Guidance\n\nPrefer `pnpx modern-web-guidance search`.\n",
        # Two sections: which one to replace is a guess.
        "## Using npx / pnpx\n\nx\n\n## Using npx / pnpx\n\ny\n\n## Next\n",
    ],
)
def test_a_skill_in_a_shape_the_fix_does_not_know_holds(text: str) -> None:
    with pytest.raises(ValueError):
        mwg.fix(SKILL, text)


@pytest.mark.parametrize(
    "line",
    [
        "npx modern-web-guidance search x",
        "npx.cmd -y modern-web-guidance@latest search x",
        "pnpx modern-web-guidance@latest search x",
        "bunx modern-web-guidance search x",
        "npm exec modern-web-guidance -- search x",
        "npm x modern-web-guidance",
        "pnpm dlx modern-web-guidance",
        "yarn dlx modern-web-guidance",
        "npx -y some-other-package",
        "see modern-web-guidance@next",
        "modern-web-guidance install --choose",
        "modern-web-guidance update",
        "modern-web-guidance uninstall",
        "node modern-web.mjs update",
    ],
)
def test_any_other_registry_run_or_self_update_is_left_as_written_and_holds(line: str) -> None:
    text = line + "\n"
    assert mwg.detect(GUIDE, mwg.fix(GUIDE, text)) == (1,)


def test_the_cli_named_with_any_other_command_is_not_a_finding() -> None:
    text = "Run `modern-web-guidance search` and `modern-web-guidance retrieve`.\n"
    assert mwg.detect(GUIDE, text) == ()


def test_a_read_line_is_excused_only_while_it_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = "the guide's own example names `npx`"
    monkeypatch.setattr(mwg, "READ_LINES", {(GUIDE, digest(line)): "an example"})
    assert mwg.detect(GUIDE, line + "\n") == ()
    assert mwg.detect(SKILL, line + "\n") == (1,)
    assert mwg.detect(GUIDE, line + " \n") == (1,)


def test_the_rule_covers_every_skill_file_and_nothing_else() -> None:
    (fork,) = [fork for fork in FORKS if fork.repository == "AlobarQuest/modern-web-guidance"]
    assert [rule.name for rule in fork.rules] == ["mwg-skill-runs-the-installed-cli"]
    assert fork.covers(SKILL)
    assert fork.covers(GUIDE)
    assert fork.covers(EXTENSIONS)
    assert not fork.covers("README.md")
    assert not fork.covers("package.json")
    assert not fork.covers(".claude-plugin/plugin.json")
