"""`fork-sync`: onboard a fork onto the upstream sync lane. Operator-run; never scheduled.

`check` and `human-steps` only read, as does `ack` without `--yes`. `scaffold`, `baseline`,
`protect` and `ack --yes` write to the fork with the operator's own `gh` and git credentials, and
each refuses before writing when its precondition does not hold. Exit codes: 0 done (or every
step ok); 1 something missing or refused.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from fork_sync import acknowledge, checks, estate, onboard
from fork_sync.fork_sync_review import ConfigError
from fork_sync.gh import CommandFailed, subprocess_runner

app = typer.Typer(add_completion=False, no_args_is_help=True)
run_command = subprocess_runner
"""The runner every command uses. Tests replace it; nothing here reaches GitHub without it."""

Fork = Annotated[str, typer.Argument(help="The fork, as owner/name.")]


def _readers() -> tuple[checks.PolicyReader | None, checks.LandingReader | None]:
    token = os.environ.get("FORK_SYNC_CHANGE_MANAGER_TOKEN", "").strip()
    brain_url = os.environ.get("FORK_SYNC_APP_BRAIN_URL", "").strip()
    brain_key = os.environ.get("FORK_SYNC_APP_BRAIN_KEY", "").strip()
    policy = (lambda: estate.read_inert_landing(token)) if token else None
    landing = (
        (lambda repo: estate.app_brain_landing(repo, base_url=brain_url, key=brain_key))
        if brain_url and brain_key
        else None
    )
    return policy, landing


@app.command()
def check(fork: Fork) -> None:
    """Report every onboarding step as ok / missing / human / info. Read-only."""
    policy, landing = _readers()
    steps = checks.check(run_command, fork, policy, landing)
    for step in steps:
        typer.echo(f"{step.status:8} {step.name}" + (f" -- {step.detail}" if step.detail else ""))
    if any(step.status == checks.MISSING for step in steps):
        raise typer.Exit(1)


def _act[T](action: str, fn: Callable[[], T]) -> T:
    try:
        return fn()
    except (onboard.Refused, ConfigError, CommandFailed) as error:
        typer.echo(f"{action} refused: {error}", err=True)
        raise typer.Exit(1) from None


@app.command()
def scaffold(
    fork: Fork,
    config: Annotated[Path, typer.Option(help="The fork-sync.toml to commit.", exists=True)],
) -> None:
    """Open a pull request adding the declaration and the rendered workflows and script."""
    number = _act("scaffold", lambda: onboard.scaffold(run_command, fork, config))
    typer.echo(f"opened pull request #{number} on {fork}")


@app.command()
def baseline(
    fork: Fork,
    tag: Annotated[str, typer.Option(help="The upstream release tag to reset main to.")],
    confirm: Annotated[bool, typer.Option(help="Required: this force-pushes main.")] = False,
    discard_fork_commits: Annotated[
        bool, typer.Option(help="Drop commits on main that upstream doesn't have.")
    ] = False,
) -> None:
    """Reset the fork's main to an upstream tag with upstream's .github removed."""
    if not confirm:
        typer.echo("baseline refused: it force-pushes main; pass --confirm", err=True)
        raise typer.Exit(1)
    head = _act(
        "baseline",
        lambda: onboard.baseline(run_command, fork, tag, discard_fork_commits=discard_fork_commits),
    )
    typer.echo(f"{fork} main is now {head} ({tag}, upstream .github removed)")


@app.command()
def protect(fork: Fork) -> None:
    """Apply the landing settings and require both checks on main."""
    number = _act("protect", lambda: onboard.protect(run_command, fork))
    typer.echo(
        f"protected {fork} main with {', '.join(onboard.REQUIRED_CHECKS)} "
        f"(both reported on pull request #{number})"
    )


@app.command("human-steps")
def human_steps(fork: Fork) -> None:
    """Print the steps only a person can do, then the agent steps."""
    typer.echo(onboard.human_steps(fork))


@app.command()
def ack(
    fork: Fork,
    number: Annotated[int, typer.Argument(help="The held upstream-sync pull request.")],
    reason: Annotated[str, typer.Option(help="Why these findings are accepted.")],
    by: Annotated[str, typer.Option(help="Who accepts them, as the acknowledgements record it.")],
    yes: Annotated[bool, typer.Option(help="Record, merge and re-sync; without it, only show.")] = (
        False
    ),
    expect: Annotated[
        str, typer.Option(help="With --yes: the fingerprints the dry run printed, comma-separated.")
    ] = "",
) -> None:
    """Acknowledge every failing finding of a held sync, then land it and re-run the sync."""
    plan = _act(
        "ack", lambda: acknowledge.plan(run_command, fork, number, reason, by, date.today())
    )
    if not yes:
        typer.echo(
            f"#{number} {plan.title} at {plan.head[:12]}: {len(plan.failing)} failing row(s)"
        )
        for row in plan.failing:
            typer.echo(
                f"  {row.kind} ({row.severity}) | {row.path} | {row.text} | {row.fingerprint}"
            )
        if plan.entries:
            typer.echo(f"\nWould add to {acknowledge.ACKS_PATH} on main:\n")
            typer.echo("\n".join(entry.toml() for entry in plan.entries))
        if plan.raises:
            typer.echo(f"\nWould raise in {acknowledge.ACKS_PATH} on main:\n")
            for change in plan.raises:
                typer.echo(f"  {change.fingerprint}: severity -> {change.severity}{change.suffix}")
            typer.echo("")
        command = ["fork-sync", "ack", fork, str(number), "--reason", reason, "--by", by]
        command += ["--yes", "--expect", ",".join(plan.fingerprints)]
        typer.echo(f"To record them: {shlex.join(command)}")
        return
    expected = [fingerprint for fingerprint in expect.split(",") if fingerprint]
    landed = _act("ack", lambda: acknowledge.land(run_command, plan, expected))
    typer.echo(
        f"acknowledged {len(plan.entries)} and raised {len(plan.raises)} finding(s) of "
        f"#{number} in #{landed.number}, "
        f"admin-merged as {landed.merge_sha}; dispatched {acknowledge.SYNC_WORKFLOW} on {fork}"
    )
