"""The ledger's entry point. Operator-invoked; there is no scheduler and no loop.

FAILING OPEN IS THE DESIGN. This is a recorder, not a gate: nothing downstream waits on it, so
GitHub being unreachable, or one landing being unreadable, must cost that landing and nothing
else. Every failure is caught per landing and COUNTED -- a pass that dies on the third landing
would discard the two it already read, and a pass that swallows failures silently is a reporting
obligation that has been switched off.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Protocol

import typer

from landing_ledger.github import (
    GitHubReader,
    LedgerError,
    default_branch,
    landing_shas,
    read_landing,
)
from landing_ledger.orchestrator_client import LedgerWriteError, OrchestratorClient
from landing_ledger.record import landing_observation

app = typer.Typer(no_args_is_help=True)

RECOVERABLE = (LedgerError, LedgerWriteError, KeyError, TypeError, ValueError)


@app.callback()
def _cli() -> None:
    """Landing ledger (WS-P3.6 Increment 2).

    Defining a callback keeps the command named at the CLI -- Typer otherwise collapses a lone
    command into the top level, which would break the invocation a launcher script uses. It also
    mirrors the sibling adapters and leaves room for a second command.
    """


class ObservationWriter(Protocol):
    """The write surface `record_landings` needs, structural so a test can pass a hermetic fake."""

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def record_landings(
    reader: GitHubReader,
    writer: ObservationWriter,
    *,
    repository: str,
    since: str,
    pages: int,
    dry_run: bool,
) -> dict[str, Any]:
    """One pass over one repository. Never raises; always returns what it managed to do."""
    summary: dict[str, Any] = {
        "repository": repository,
        "landings": 0,
        "recorded": 0,
        "skipped": 0,
        "unavailable": False,
    }
    # A dry run's whole purpose is to show WHAT would be written -- the permission basis in
    # particular -- before anything permanent exists. Counts alone cannot serve that: they say
    # a record was computed, not what it says. Carried on the summary rather than printed
    # inline so the output stays one parseable JSON document.
    if dry_run:
        summary["records"] = []
    try:
        base_ref = default_branch(reader, repository)
        shas = landing_shas(reader, repository, base_ref, since, pages)
    except RECOVERABLE:
        summary["unavailable"] = True
        return summary
    summary["landings"] = len(shas)
    for sha in shas:
        try:
            body = landing_observation(read_landing(reader, repository, base_ref, sha))
            if dry_run:
                summary["records"].append(body)
            else:
                writer.record_observation(body)
        except RECOVERABLE:
            summary["skipped"] += 1
            continue
        summary["recorded"] += 1
    return summary


class _NullWriter:
    """A dry-run writer: any use is a bug, so it fails loudly rather than looking successful."""

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("dry run must not record observations")


@app.command("record")
def record_command(
    repository: Annotated[list[str], typer.Option(help="Repository as owner/name; repeatable.")],
    days: Annotated[int, typer.Option(help="How far back to read landings.")] = 30,
    pages: Annotated[int, typer.Option(help="Commit-listing pages to read per repository.")] = 5,
    orchestrator_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    credential_key_id: Annotated[str, typer.Option()] = "orchestrator-observer",
    dry_run: Annotated[bool, typer.Option(help="Print the records; write nothing.")] = False,
) -> None:
    github_token = os.environ.get("LANDING_LEDGER_GITHUB_TOKEN")
    if not github_token:
        typer.echo("LANDING_LEDGER_GITHUB_TOKEN is required", err=True)
        raise typer.Exit(code=1)
    token = "" if dry_run else os.environ.get("LANDING_LEDGER_TOKEN", "")
    if not dry_run and not token:
        typer.echo("LANDING_LEDGER_TOKEN is required", err=True)
        raise typer.Exit(code=1)
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    writer: ObservationWriter = _NullWriter()
    with GitHubReader(token=github_token) as reader:
        if dry_run:
            summaries = [
                record_landings(
                    reader, writer, repository=name, since=since, pages=pages, dry_run=True
                )
                for name in repository
            ]
        else:
            with OrchestratorClient(
                base_url=orchestrator_url, credential_key_id=credential_key_id, token=token
            ) as client:
                summaries = [
                    record_landings(
                        reader, client, repository=name, since=since, pages=pages, dry_run=False
                    )
                    for name in repository
                ]
    typer.echo(json.dumps(summaries, indent=2, sort_keys=True))
