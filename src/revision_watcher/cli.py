"""The watcher's entry point. Operator- or launcher-invoked; there is no scheduler and no loop.

FAILING OPEN, PER SUBJECT. This is a recorder, not a gate: nothing downstream waits on it, so an
application that cannot be asked must cost that application and nothing else. A pass that died on
the third of six would discard the two it had already filed.

  0  every subject was measured and every one serves what its branch names.
  1  the tool itself failed -- a missing credential, an unusable URL, an unhandled error.
  2  an application is not serving what its branch names. The pass worked; the estate did not.
  3  some subject could not be read, its row could not be filed, or the declared table could not
     be policed -- so the answer is missing rather than clean.

3 OUTRANKS 2, the vocabulary its two sibling detectors use: an incomplete pass cannot claim it
found everything there was to find.

WHAT AN EXIT CODE DOES AND DOES NOT DO. `sds-deadman.sh` pings its check SUCCESS for a declared
finding code -- the check answers "is this lane alive", never "did it find something". So exit 2
does not page anybody, and the finding's durable home is the observation this pass files, which is
why the pass writes even when nothing has changed. Whether a standing finding here should reach a
person by some other route is an open decision, deliberately not taken inside this lane.
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from typing import Annotated

import typer

from revision_watcher.census import Pass, as_lines, sweep
from revision_watcher.estate import ApplicationReader, LandingReader, PlatformReader
from revision_watcher.github import GitHubReader
from revision_watcher.orchestrator_client import (
    OrchestratorClient,
    RevisionWriteError,
    UnusableEndpointError,
    open_client,
)
from revision_watcher.record import revision_observation

EXIT_CLEAN = 0
EXIT_TOOL_FAILED = 1
EXIT_FOUND = 2
EXIT_INCOMPLETE = 3

app = typer.Typer(add_completion=False)


def _file_rows(result: Pass, client: OrchestratorClient) -> list[str]:
    unfiled: list[str] = []
    for reading in result.readings:
        if reading.state == "unreadable":
            # Nothing measured, so there is nothing to assert. Its absence is already carried by
            # the incomplete exit; a row saying "this could not be read" would be this lane
            # asserting a condition about an application it never reached.
            continue
        try:
            client.record_observation(revision_observation(reading))
        except (RevisionWriteError, ValueError):
            unfiled.append(reading.subject.name)
    return unfiled


def _measure(
    *,
    token: str,
    platform_url: str,
    platform_token: str,
    estate_url: str,
    estate_key: str,
) -> Pass:
    """One pass, with whatever optional sources are configured.

    Both optional sources fail towards REPORTING rather than towards quiet. Without the platform
    the declared table goes unpoliced and the pass says so; without the estate source every
    subject is classified `unknown` and judged strictly, so a separate-track application's ordinary
    queue is reported as a finding -- loud and wrong rather than quiet and wrong.
    """
    with (
        GitHubReader(token=token) as reader,
        ApplicationReader() as applications,
        ExitStack() as stack,
    ):
        platform = None
        if platform_url and platform_token:
            platform = stack.enter_context(
                PlatformReader(base_url=platform_url, token=platform_token)
            )
        landings = None
        if estate_url and estate_key:
            landings = stack.enter_context(LandingReader(base_url=estate_url, key=estate_key))
        result = sweep(
            applications=applications, reader=reader, platform=platform, landings=landings
        )
    if platform is None:
        result.coverage_unmeasured = "no platform credential was configured"
    return result


@app.command()
def main(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Measure and report; file nothing.")
    ] = False,
) -> None:
    token = os.environ.get("REVISION_WATCHER_GITHUB_TOKEN", "").strip()
    if not token:
        typer.echo("FATAL: set REVISION_WATCHER_GITHUB_TOKEN, or authenticate gh.", err=True)
        raise typer.Exit(EXIT_TOOL_FAILED)

    platform_url = os.environ.get("REVISION_WATCHER_PLATFORM_URL", "").strip()
    platform_token = os.environ.get("REVISION_WATCHER_PLATFORM_TOKEN", "").strip()
    estate_url = os.environ.get("REVISION_WATCHER_ESTATE_URL", "").strip()
    estate_key = os.environ.get("REVISION_WATCHER_ESTATE_KEY", "").strip()

    result = _measure(
        token=token,
        platform_url=platform_url,
        platform_token=platform_token,
        estate_url=estate_url,
        estate_key=estate_key,
    )

    for line in as_lines(result):
        typer.echo(line)

    unfiled: list[str] = []
    if dry_run:
        typer.echo(f"dry run: {len(result.readings)} rows not filed")
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
            # A typo in the URL is the tool being unusable for every subject at once, deliberately
            # not the per-subject failure that would report as an incomplete pass.
            typer.echo(f"FATAL: {error}", err=True)
            raise typer.Exit(EXIT_TOOL_FAILED) from error

    for name in sorted(unfiled):
        typer.echo(f"!! {name} measured, row not filed", err=True)

    typer.echo(
        f"{len(result.readings)} applications, {len(result.findings)} behind or diverged, "
        f"{len(result.awaiting)} awaiting a deploy, {len(result.unstamped)} unstamped, "
        f"{len(result.unreadable)} unreadable, {len(result.undeclared)} undeclared, "
        f"{len(unfiled)} unfiled"
    )
    if result.unreadable or unfiled or result.coverage_unmeasured:
        raise typer.Exit(EXIT_INCOMPLETE)
    if result.findings or result.undeclared:
        raise typer.Exit(EXIT_FOUND)
    raise typer.Exit(EXIT_CLEAN)
