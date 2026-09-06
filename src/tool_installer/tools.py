"""What this lane may install, as a table rather than as code.

ONE ROW TODAY, and the shape is the point: a second tool is a row, not a rewrite. Nothing here is
generalised beyond what one row proves, because a second row's real requirements are unknown until
there is one -- an abstraction fitted to a single case is a guess wearing the shape of a design.

`probe_commands` is the bar Devon named, and it is deliberately per-tool rather than a constant.
What proves a binary works is a fact about that binary: for `rtk` it is that the tool still filters
a command, which `proxy` exercises and `--version` does not.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    """One installable tool: where it comes from, how it is built, how it is proven."""

    name: str
    """The binary's name, which is also its name under the install root's `bin/`."""

    repository: str
    """`owner/name` on GitHub. The lane reads its head; it never clones it."""

    branch: str
    """The branch the install tracks, and the one whose head decides whether work is pending."""

    crate: str
    """The crate name `cargo install` is given, which need not equal the binary's name."""

    probe_commands: tuple[tuple[str, ...], ...]
    """Argument vectors run against the NEWLY INSTALLED binary, all of which must exit zero.

    Each is a suffix appended to the installed binary's own path, so a probe can never
    accidentally exercise some other copy found on `PATH`.
    """


RTK = Tool(
    name="rtk",
    repository="AlobarQuest/rtk",
    branch="main",
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
)

TOOLS: tuple[Tool, ...] = (RTK,)
