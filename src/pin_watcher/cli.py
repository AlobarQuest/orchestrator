"""The watcher's entry point. Operator- or launcher-invoked; there is no scheduler and no loop.

FAILING OPEN, PER CALLER. This is a recorder, not a gate: nothing downstream waits on it, so a
repository that cannot be read must cost that repository and nothing else. A pass that died on the
third of six would discard the two it had already filed.

FAILING OPEN IS NOT EXITING ZERO. The four codes below separate the four answers a caller actually
needs, and a broken tool sharing a code with an honest finding is a collision this estate has
already paid for.

  0  every caller was measured and every one is at the recommendation.
  1  the tool itself failed -- a missing credential, an unusable URL, an unhandled error.
  2  a caller is not at the recommendation. The pass worked; the estate did not.
  3  some caller could not be read, or its row could not be filed, so the answer is missing
     rather than clean.

3 OUTRANKS 2, and the reason is the same one every sibling lane states: an incomplete pass cannot
claim it found everything there was to find.

WHAT AN EXIT CODE DOES AND DOES NOT DO. `sds-deadman.sh` pings its check SUCCESS for a declared
finding code -- the check answers "is this lane alive", never "did it find something". So exit 2
does not page anybody, and the finding's durable home is the observation this pass files, which is
why the pass writes even when nothing has changed.
"""

from __future__ import annotations

import os
from typing import Annotated

import typer

from pin_watcher.github import GitHubReader, PinWatcherError
from pin_watcher.measure import Pass, as_lines, sweep
from pin_watcher.orchestrator_client import (
    OrchestratorClient,
    PinWriteError,
    UnusableEndpointError,
    open_client,
)
from pin_watcher.record import pin_observation

EXIT_CLEAN = 0
EXIT_TOOL_FAILED = 1
EXIT_FOUND = 2
EXIT_INCOMPLETE = 3

app = typer.Typer(add_completion=False, help=__doc__)


def _file_rows(result: Pass, client: OrchestratorClient) -> list[str]:
    """File one row per caller. Returns the repositories whose row could not be filed.

    EVERY caller is filed, not only the findings. A row saying a caller is current is what makes
    "this lane is measuring that repository" a fact rather than an inference from silence -- and
    silence is exactly what let five callers drift twenty-three commits behind.
    """
    unfiled: list[str] = []
    for caller in result.callers:
        try:
            client.record_observation(
                pin_observation(caller, result.recommended, result.recommended_at)
            )
        except (PinWriteError, ValueError):
            unfiled.append(caller.repository)
    return unfiled


@app.command()
def main(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Measure and report; file nothing.")
    ] = False,
) -> None:
    token = os.environ.get("PIN_WATCHER_GITHUB_TOKEN", "").strip()
    if not token:
        typer.echo("FATAL: set PIN_WATCHER_GITHUB_TOKEN, or authenticate gh.", err=True)
        raise typer.Exit(EXIT_TOOL_FAILED)

    try:
        with GitHubReader(token=token) as reader:
            result = sweep(reader)
    except PinWatcherError as error:
        # The recommendation itself is unreadable, or GitHub is. Either way nothing was measured,
        # and an unmeasured pass is incomplete rather than clean.
        typer.echo(f"INCOMPLETE: {error}", err=True)
        raise typer.Exit(EXIT_INCOMPLETE) from error

    for line in as_lines(result):
        typer.echo(line)

    unfiled: list[str] = []
    if dry_run:
        typer.echo(f"dry run: {len(result.callers)} rows not filed")
    else:
        base_url = os.environ.get("ORCHESTRATOR_API_URL", "").strip()
        key_id = os.environ.get("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "").strip()
        bearer = os.environ.get("ORCHESTRATOR_API_TOKEN", "").strip()
        if not (base_url and key_id and bearer):
            typer.echo("FATAL: the orchestrator credential is not configured.", err=True)
            raise typer.Exit(EXIT_TOOL_FAILED)
        try:
            with open_client(base_url=base_url, credential_key_id=key_id, token=bearer) as client:
                unfiled = _file_rows(result, client)
        except UnusableEndpointError as error:
            # The URL is a typo, which is the tool being unusable for every caller at once --
            # deliberately not the per-caller failure that would report as an incomplete pass.
            typer.echo(f"FATAL: {error}", err=True)
            raise typer.Exit(EXIT_TOOL_FAILED) from error

    for repository in sorted(unfiled):
        typer.echo(f"!! {repository} measured, row not filed", err=True)

    findings = result.findings
    typer.echo(
        f"{len(result.callers)} callers, {len(findings)} findings, "
        f"{len(result.unreadable)} unreadable, {len(unfiled)} unfiled"
    )
    if result.unreadable or unfiled:
        raise typer.Exit(EXIT_INCOMPLETE)
    if findings:
        raise typer.Exit(EXIT_FOUND)
    raise typer.Exit(EXIT_CLEAN)
