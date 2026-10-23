"""What this lane may install, as a table rather than as code.

FOUR ROWS NOW. The fourth, `n8nac`, is a second npm row, and it is the first to disagree with
`rea`: its CLI is one workspace of a monorepo whose runtime dependencies are its siblings, it needs
data files its safe build cannot produce, and the place it must be reachable from is Homebrew's npm
prefix rather than this lane's own. Each disagreement arrived as an optional field on `NpmInstall`
whose default is exactly what `rea` already did -- the rule below, applied: a field added when a
row needed it, with that row to fit it to.

THREE ROWS NOW. The paragraphs below were written at two, and they held: the third, `rea`, is a
Node CLI and MCP server, and it arrived as a row plus a third strategy type (`NpmInstall`) with no
change to the other two. See that type's docstring for what it does that neither of the others
does.

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
class PublishedAssets:
    """DATA FILES, NEVER CODE, taken from upstream's published package at the fork's own version.

    The files are JSON the package's code reads with `readFileSync` and `JSON.parse`, and nothing
    else of that tarball is used: its `dist/` code is upstream's, and the fork's own build supplies
    the code. They exist because the build that GENERATES them builds n8n itself, which would run
    n8n's code on this machine, so Devon chose upstream's published data instead on 2026-10-23.
    The registry verifies the tarball's integrity on download and the installed manifest records
    what landed.
    """

    package: str
    """The published package the data comes from, fetched at the version `workspace` names."""

    workspace: str
    """The fork's workspace whose `package.json` names the version to fetch, and whose tree
    receives the files."""

    directory: str
    """Where the files sit, both inside the published package and inside `workspace`."""


@dataclass(frozen=True)
class NpmInstall:
    """A Node package built from the fork's source and installed into a dedicated prefix.

    Built from source rather than fetched from the npm registry, because the fork is the reviewed
    artifact and the registry's copy is upstream's. See `npm.py` for the act and its reasons.
    """

    package: str
    """The npm package name, which need not equal the binary's name (`rea-agents` ships `rea`)."""

    build_commands: tuple[tuple[str, ...], ...]
    """`npm` argument vectors run after `npm ci --ignore-scripts`, in order, to produce what the
    package's `files` list ships. Named here rather than delegated to a lifecycle script, because
    the lane runs no lifecycle script at all."""

    probe_commands: tuple[tuple[str, ...], ...]
    """As `CargoInstall.probe_commands`: suffixes run against the installed binary's path."""

    mcp_arguments: tuple[str, ...] | None = None
    """The suffix that starts the tool's stdio MCP server. The probe sends one `initialize` and
    checks the server's reported version by value -- a stdin exchange, which no argv suffix in
    `probe_commands` can express. `None` for a package that serves no MCP, which is then not
    asked to."""

    workspace: str | None = None
    """The package's directory inside a monorepo, when it is one workspace of several.

    Then the WHOLE built checkout is installed, not a packed copy of this directory, because the
    package's runtime dependencies are its sibling workspaces and a packed copy would fetch them
    from the registry -- upstream's code, not the fork's. The version, `gitHead` and `bin` map
    are read from `<checkout>/<workspace>/package.json`. See `npm.py`."""

    published_assets: PublishedAssets | None = None
    """Data files taken from upstream's published package, when the safe build cannot make them."""

    link_into_npm_prefix: bool = False
    """Also own `<npm prefix -g>/bin/<name>`, pointing at this lane's own `bin/<name>`.

    For a tool something else runs through `npx`, which resolves a command from the global
    prefix's `bin/` and downloads upstream's package from the registry when it finds none there.
    """


@dataclass(frozen=True)
class Tool:
    """One installable tool: where it comes from, how it is installed, how it is proven."""

    name: str
    """The tool's name on this machine -- a binary under the install root's `bin/` for a crate or
    an npm package, and the plugin's own name for a plugin."""

    repository: str
    """`owner/name` on GitHub. The lane reads its head; it never clones it."""

    branch: str
    """The branch the install tracks, and the one whose head decides whether work is pending."""

    install: CargoInstall | PluginInstall | NpmInstall
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

# The third row. `rea` reaches Claude Code as a stdio MCP server (`rea mcp`); registering it there
# is a one-time manual step outside this lane, which keeps the binary current and nothing else.
REA = Tool(
    name="rea",
    repository="AlobarQuest/rea",
    branch="main",
    install=NpmInstall(
        package="rea-agents",
        # Exactly what `prepack` would produce, without `prepack`: it re-runs this build through
        # turbo, which brings its own cache and telemetry. `compile` writes `dist/` and
        # `artifacts:skills` writes `skills/`; both are in the package's `files`. Measured at
        # rea-agents 6.7.0 with network denied: 5.8s and 0.5s.
        build_commands=(("run", "compile"), ("run", "artifacts:skills")),
        probe_commands=(("--help",),),
        mcp_arguments=("mcp",),
    ),
)

# The fourth row. The n8n-as-code plugin runs `npx --yes n8nac ...`, and npx resolves that from
# Homebrew's npm prefix -- where `bin/n8nac` was a hand-made `npm link` into an old checkout nobody
# had reviewed. So this row builds the fork, and also owns that prefix link, pointing it at this
# lane's own `bin/n8nac`; with no link there, npx would fetch upstream from the registry instead.
# Devon's decisions of 2026-10-23: install from the fork, take the knowledge data from upstream's
# published `@n8n-as-code/skills` at the fork's version, and own the prefix link. The hardening
# session (docs/superpowers/plans/2026-10-21-handoff-fork-hardening.md) should remove the need for
# that link by having the plugin call this lane's binary rather than `npx`.
N8NAC = Tool(
    name="n8nac",
    repository="AlobarQuest/n8n-as-code",
    branch="main",
    install=NpmInstall(
        package="n8nac",
        # Builds `packages/cli/dist` and the `dist/` of every workspace it references. Measured at
        # n8nac 2.7.0 with lifecycle scripts refused: the production install that follows leaves
        # 0 invalid and 0 missing packages.
        build_commands=(("run", "build:cli"),),
        # Both exit 1 ("Knowledge index not found") without the published data, so their exit
        # status alone proves the data landed where the code reads it.
        probe_commands=(
            ("skills", "search", "http request"),
            ("skills", "node-info", "httpRequest", "--compact"),
        ),
        workspace="packages/cli",
        published_assets=PublishedAssets(
            package="@n8n-as-code/skills", workspace="packages/skills", directory="dist/assets"
        ),
        link_into_npm_prefix=True,
    ),
)

TOOLS: tuple[Tool, ...] = (RTK, OCTO, REA, N8NAC)
