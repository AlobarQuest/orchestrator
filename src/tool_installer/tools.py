"""What this lane may install, as a table rather than as code.

TWO ROWS, AND THE SHAPE IS THE POINT: a second tool was a row plus a strategy, not a rewrite. The
table stayed; what varies per row is HOW the artifact is fetched, replaced and proven, because
that is the only thing the two rows disagree about. `rtk` is a compiled binary `cargo install`
replaces on this machine; `octo` is a Claude Code plugin that reaches the machine through a
serving clone, a marketplace pin and a versioned install cache. Nothing about the first predicted
the second, which is why the first shipped with a docstring saying an abstraction fitted to a
single case is a guess wearing the shape of a design.

NOTHING HERE IS GENERALISED PAST WHAT THESE TWO ROWS PROVE. `CargoInstall` and `PluginInstall`
are separate types rather than one with optional fields, so a row cannot be half of each; and
`PluginInstall` carries only the marketplace name, because the serving clone's directory and the
plugin's manifest location are derivable from the tool's name for both plugins this machine
serves. A third row that disagrees adds the field then, with a case to fit it to.

`probe_commands` is the bar Devon named, and it is deliberately per-tool rather than a constant.
What proves a binary works is a fact about that binary: for `rtk` it is that the tool still filters
a command, which `proxy` exercises and `--version` does not. A plugin is not executable and has no
counterpart -- see `plugin.py` for what stands in its place, and for the honest statement of how
much weaker that is.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CargoInstall:
    """A crate `cargo install --git` builds and writes into an install root."""

    crate: str
    """The crate name `cargo install` is given, which need not equal the binary's name."""

    probe_commands: tuple[tuple[str, ...], ...]
    """Argument vectors run against the NEWLY INSTALLED binary, all of which must exit zero.

    Each is a suffix appended to the installed binary's own path, so a probe can never
    accidentally exercise some other copy found on `PATH`.
    """


@dataclass(frozen=True)
class PluginInstall:
    """A Claude Code plugin served from a local directory-marketplace.

    The serving clone is `<hub>/<tool name>` and its manifest is `.claude-plugin/plugin.json` at
    that clone's root. Both are derived from the tool's name rather than declared.

    THAT IS TRUE OF `octo` AND FALSE OF THE HUB'S OTHER PLUGIN, which this docstring claimed
    until 2026-09-07. `n8n-as-code/.claude-plugin/` exists and holds a `marketplace.json`, not a
    `plugin.json`; its real manifest is at `n8n-as-code/plugins/claude/n8n-as-code/.claude-plugin/`,
    exactly as the hub's own `source` field says. So the derivation would read the wrong document
    for that row rather than fail loudly, and the hub already DECLARES the answer the old
    reasoning dismissed as "a second copy of a fact".

    Left derived because `octo` is the only plugin row, and a field fitted to a second row before
    that row exists is a guess. A THIRD ROW MUST ADD A DECLARED MANIFEST PATH -- read it from the
    hub's `source`, do not extend the derivation.
    """

    marketplace: str
    """The marketplace's name, which is both the hub's `name` and the suffix of the installed
    plugin's key in Claude Code's own record (`<tool name>@<marketplace>`)."""


@dataclass(frozen=True)
class Tool:
    """One installable tool: where it comes from, how it is installed, how it is proven."""

    name: str
    """The tool's name on this machine -- a binary under the install root's `bin/` for a crate,
    and the plugin's own name for a plugin."""

    repository: str
    """`owner/name` on GitHub. The lane reads its head; it never clones it."""

    branch: str
    """The branch the install tracks, and the one whose head decides whether work is pending."""

    install: CargoInstall | PluginInstall
    """How this row is fetched, replaced and proven. The one thing that varies per row."""


RTK = Tool(
    name="rtk",
    repository="AlobarQuest/rtk",
    branch="main",
    install=CargoInstall(
        crate="rtk",
        # `--version` is checked separately and by VALUE, not merely for a zero exit -- see
        # `install.probe`. These three are the ones whose exit status is the whole assertion.
        # `gain` reads the tool's own analytics store, and `proxy` runs a command THROUGH the
        # filter, which is the one path that exercises what rtk exists to do.
        probe_commands=(
            ("--version",),
            ("gain",),
            ("proxy", "echo", "sds-tool-installer-probe"),
        ),
    ),
)

# ADR-0042's second row. `octo` sat at 9.45.0 for 72 days -- 323 commits and two major versions
# behind the fork -- because the routine that updates it fires only after a sync pull request
# merges, and a person has to remember. The subject is `AlobarQuest/claude-octopus`, the FORK,
# never upstream: what reaches this machine is the hardened clone, and its head is what "behind"
# is measured against.
OCTO = Tool(
    name="octo",
    repository="AlobarQuest/claude-octopus",
    branch="main",
    install=PluginInstall(marketplace="devon-plugins"),
)

TOOLS: tuple[Tool, ...] = (RTK, OCTO)
