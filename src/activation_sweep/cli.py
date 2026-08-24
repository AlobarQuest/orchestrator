"""The sweep's entry point. Operator- or launcher-invoked; there is no scheduler and no loop.

FAILING OPEN, PER CHECKOUT. This is a recorder, not a gate: nothing downstream waits on it, so a
working copy that cannot be measured must cost that working copy and nothing else. A pass that
died on the third of nine would discard the two it had already filed.

FAILING OPEN IS NOT EXITING ZERO. The three codes below separate the three answers a caller
actually needs, and a broken tool sharing a code with an honest finding is a collision this
estate has already paid for.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any, Protocol

import typer

from activation_sweep.bind import (
    NullBinder,
    bind_checkout,
    has_findings,
)
from activation_sweep.binding_client import (
    UnusableEndpointError as BindingUnusableEndpointError,
)
from activation_sweep.binding_client import (
    open_binding_client,
)
from activation_sweep.checkout import (
    ForbiddenCommandError,
    GitError,
    conditions_of,
    read_checkout,
)
from activation_sweep.orchestrator_client import (
    ForbiddenEndpointError,
    SweepWriteError,
    UnusableEndpointError,
    open_client,
)
from activation_sweep.record import activation_observation

app = typer.Typer(no_args_is_help=True)

RECOVERABLE = (GitError, SweepWriteError, KeyError, TypeError, ValueError)

# The two guard violations, which must NEVER be absorbed. Both are subclasses of a `RECOVERABLE`
# family, so without naming them here a `git pull` reaching the runner, or a write aimed outside
# the one permitted endpoint, would be reported as "this working copy could not be measured" --
# the guards firing and nobody hearing it. They are programming errors: let them crash.
UNRECOVERABLE = (ForbiddenCommandError, ForbiddenEndpointError)

TOKEN_VARIABLE = "ACTIVATION_SWEEP_TOKEN"
# A SECOND credential, for the SECOND lane. The sweep records observations as OBSERVER; binding a
# release artifact is admitted only for the SYSTEM actor, so the two cannot share a bearer and
# must not share a variable -- one ambient token serving both identities is the failure this
# estate has already paid for twice, in the launchers and in `factory decompose`.
BINDING_TOKEN_VARIABLE = "ACTIVATION_BIND_TOKEN"

# Nothing to report, and every enrolled checkout was measured and filed.
EXIT_OK = 0
# Something was found: a checkout is behind, or carries modified tracked files. The pass worked;
# the machine is not where it should be.
EXIT_FINDINGS = 2
# Some checkout could not be measured, or its row could not be filed, so the answer is missing
# rather than clean. This outranks findings: an incomplete pass cannot claim it found everything.
EXIT_INCOMPLETE = 3


def _exit_code(*, findings: bool, incomplete: bool) -> int:
    if incomplete:
        return EXIT_INCOMPLETE
    return EXIT_FINDINGS if findings else EXIT_OK


@app.callback()
def _cli() -> None:
    """Machine-activation sweep (ADR-0030).

    Defining a callback keeps the command named at the CLI -- Typer otherwise collapses a lone
    command into the top level, which would break the invocation the launcher script uses.
    """


class ObservationWriter(Protocol):
    """The write surface `sweep_checkout` needs, structural so a test can pass a hermetic fake."""

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class _NullWriter:
    """A dry-run writer: any use is a bug, so it fails loudly rather than looking successful."""

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("dry run must not record observations")


def sweep_checkout(
    path: str,
    writer: ObservationWriter,
    *,
    fetch: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """One pass over one working copy. Never raises; always returns what it managed to do.

    `unavailable` and `recorded` are separate answers on purpose. A checkout that could not be
    measured said nothing; a checkout that was measured and whose row was refused said something
    nobody can read. Both make the pass incomplete, and collapsing them would lose which happened.
    """
    summary: dict[str, Any] = {
        "checkout": path,
        "unavailable": False,
        "recorded": None if dry_run else False,
        # Why, not just that. Nine expired-bearer 401s and nine broken repositories are the same
        # exit code and the same log line without this, and `run_git` and `post` both compose a
        # careful, deliberately secret-free diagnostic that was being thrown away.
        "reason": None,
    }
    try:
        state = read_checkout(Path(path), fetch=fetch)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        summary["unavailable"] = True
        summary["reason"] = str(error)
        return summary
    conditions = conditions_of(state)
    summary.update(
        repository=state.repository,
        head=state.head,
        branch=state.branch,
        # Beside the branch, because "parked" in `conditions` without it makes the reader open a
        # terminal to find out what the checkout should have been on.
        default_branch=state.default_branch,
        upstream=state.upstream,
        conditions=list(conditions),
        behind_by=state.behind_by,
        ahead_by=state.ahead_by,
        tracked_modifications=state.tracked_modifications,
        missing=[commit.commit for commit in state.missing],
    )
    body = activation_observation(state)
    if dry_run:
        # A dry run's whole purpose is to show WHAT would be written before anything permanent
        # exists. Carried on the summary rather than printed inline so the output stays one
        # parseable JSON document.
        summary["record"] = body
        return summary
    try:
        writer.record_observation(body)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        summary["reason"] = str(error)
        return summary
    summary["recorded"] = True
    return summary


@app.command("sweep")
def sweep_command(
    checkout: Annotated[
        list[str], typer.Option(help="Path to an enrolled working copy; repeatable.")
    ],
    orchestrator_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    credential_key_id: Annotated[str, typer.Option()] = "orchestrator-observer",
    fetch: Annotated[
        bool, typer.Option(help="Update remote-tracking refs before measuring. Never pulls.")
    ] = True,
    dry_run: Annotated[bool, typer.Option(help="Print the records; write nothing.")] = False,
) -> None:
    """Report what each enrolled working copy will execute at its next start."""
    if not fetch and not dry_run:
        # Without a fetch, `behind` is measured against stale remote-tracking refs and is always
        # zero -- the sweep reports current because it never looked. A row asserting that is worse
        # than no row, so `--no-fetch` is a measurement aid and never a recording mode.
        typer.echo("--no-fetch may only be used with --dry-run", err=True)
        raise typer.Exit(code=1)
    token = "" if dry_run else os.environ.get(TOKEN_VARIABLE, "")
    if not dry_run and not token:
        typer.echo(f"{TOKEN_VARIABLE} is required", err=True)
        raise typer.Exit(code=1)
    if dry_run:
        summaries = [
            sweep_checkout(path, _NullWriter(), fetch=fetch, dry_run=True) for path in checkout
        ]
    else:
        try:
            client = open_client(
                base_url=orchestrator_url, credential_key_id=credential_key_id, token=token
            )
        except UnusableEndpointError as error:
            # The operator's typo, and it is the tool failing rather than a checkout being
            # unmeasurable -- so it exits 1 rather than joining the incomplete count. Both halves
            # of the malformed-URL guard live in the client module; see `open_client`.
            typer.echo(f"--orchestrator-url: {error}", err=True)
            raise typer.Exit(code=1) from error
        with client:
            summaries = [
                sweep_checkout(path, client, fetch=fetch, dry_run=False) for path in checkout
            ]
    typer.echo(json.dumps(summaries, indent=2, sort_keys=True))
    raise typer.Exit(
        code=_exit_code(
            findings=any(summary.get("conditions") for summary in summaries),
            incomplete=any(
                summary["unavailable"] or summary["recorded"] is False for summary in summaries
            ),
        )
    )


@app.command("bind")
def bind_command(
    checkout: Annotated[
        list[str],
        typer.Option(help="Path to a MACHINE-LOCAL working copy; repeatable."),
    ],
    orchestrator_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    credential_key_id: Annotated[str, typer.Option()] = "orchestrator-system",
    fetch: Annotated[
        bool, typer.Option(help="Update remote-tracking refs before measuring. Never pulls.")
    ] = True,
    dry_run: Annotated[
        bool, typer.Option(help="Read candidates and print what would be bound; write nothing.")
    ] = False,
) -> None:
    """Bind a release artifact for every unit whose landing this machine has activated.

    THE CHECKOUTS PASSED HERE ARE NOT THE SWEEP'S SIX. Two of the SDS targets -- `change-manager`
    and `brain` -- become live by a hosted application swapping a container image, which is the
    first model and already recorded. Binding a machine-local artifact for one of them would
    assert that a working copy on this machine is what serves them, which is false, and it is the
    collapse the kind discriminator exists to prevent. The wrapper passes the four that have no
    hosted application; nothing here infers the list.

    A DRY RUN READS AND WRITES NOTHING. It still needs the credential, because knowing what would
    be bound requires asking which units are candidates -- and that is the run to make first, so
    the units about to be bound are seen before anything permanent exists.
    """
    token = os.environ.get(BINDING_TOKEN_VARIABLE, "")
    if not token:
        typer.echo(f"{BINDING_TOKEN_VARIABLE} is required", err=True)
        raise typer.Exit(code=1)
    try:
        client = open_binding_client(
            base_url=orchestrator_url, credential_key_id=credential_key_id, token=token
        )
    except BindingUnusableEndpointError as error:
        typer.echo(f"--orchestrator-url: {error}", err=True)
        raise typer.Exit(code=1) from error
    with client:
        binder = NullBinder(client) if dry_run else client
        summaries = [bind_checkout(path, binder, fetch=fetch, dry_run=dry_run) for path in checkout]
    typer.echo(json.dumps(summaries, indent=2, sort_keys=True))
    # No `EXIT_FINDINGS` here, and the asymmetry with `sweep` is the point. The sweep's findings
    # are conditions of the MACHINE -- behind, dirty -- which a person should act on. This lane's
    # only non-clean answers are missing ones, so an incomplete pass is the only thing to report.
    raise typer.Exit(code=EXIT_INCOMPLETE if has_findings(summaries) else EXIT_OK)
