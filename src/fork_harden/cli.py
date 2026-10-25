"""`fork-harden`: write each fork's `hardened` branch from its landed `main` (ADR-0057).

`run` reads every fork in `rules.FORKS` and reports what it would do; `run --push` also moves
`hardened`. The scheduled pass (`scripts/run-fork-harden.sh`) runs `run --push` daily at 03:00,
before the tool installer's 03:15. Exit codes, the whole interface a scheduled run has:

  0  no fork is held (each is advanced, current, or would advance).
  1  the tool itself failed.
  2  a fork could not be read or pushed (git or GitHub failed), and none is held.
  3  a fork is held: `hardened` did not move, so the installer stays on the last hardened head.

GitHub is reached with the operator's own git credentials (`gh auth git-credential`), the login
`fork-sync ack`, `scaffold` and `baseline` already push to forks with. No other credential.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

import typer

from fork_harden import git
from fork_harden.harden import harden
from fork_harden.rules import FORKS, Fork

EXIT_UNREADABLE: Final = 2
EXIT_HELD: Final = 3

DEFAULT_CACHE: Final = Path.home() / ".local" / "share" / "sds-fork-harden"

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Harden the version of a fork that the tool installer installs."""


def remote(fork: Fork) -> str:
    return f"https://github.com/{fork.repository}.git"


def cache_for(cache: Path, fork: Fork) -> Path:
    owner, name = fork.repository.split("/")
    return cache / owner / f"{name}.git"


def run_pass(forks: tuple[Fork, ...], cache: Path, *, push: bool) -> tuple[list[str], int]:
    """Every fork's report lines and the pass's exit code. Reads nothing when `forks` is empty."""
    if not forks:
        return (["no fork has a hardening rule; nothing read"], 0)
    lines: list[str] = []
    held = unreadable = False
    for fork in forks:
        try:
            repo = git.open_cache(cache_for(cache, fork), remote(fork))
            outcome = harden(repo, fork, push=push)
        except git.GitError as error:
            unreadable = True
            lines.append(f"{fork.repository}: unreadable -- {error}")
            continue
        held = held or outcome.status == "held"
        lines += outcome.lines()
    code = EXIT_HELD if held else EXIT_UNREADABLE if unreadable else 0
    return (lines, code)


@app.command("run")
def run_command(
    push: Annotated[bool, typer.Option("--push", help="Move each fork's hardened branch.")] = False,
    fork: Annotated[
        str | None, typer.Option("--fork", help="Only this fork, as owner/name.")
    ] = None,
    cache: Annotated[Path, typer.Option(help="Where the bare caches live.")] = DEFAULT_CACHE,
) -> None:
    """One pass over every fork that has a rule. Without --push, nothing is written to GitHub."""
    forks = FORKS if fork is None else tuple(f for f in FORKS if f.repository == fork)
    if fork is not None and not forks:
        typer.echo(f"{fork} has no hardening rule")
        raise typer.Exit(1)
    lines, code = run_pass(forks, cache, push=push)
    for line in lines:
        # A tree path that isn't UTF-8 is carried with surrogate escapes (`git.PATHS`); print it
        # escaped rather than let the encoder end the pass.
        typer.echo(line.encode("utf-8", "backslashreplace").decode("utf-8"))
    if code:
        raise typer.Exit(code)
