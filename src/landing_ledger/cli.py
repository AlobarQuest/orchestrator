"""The ledger's entry point. Operator- or launcher-invoked; there is no scheduler and no loop.

FAILING OPEN IS THE DESIGN. This is a recorder, not a gate: nothing downstream waits on it, so
GitHub being unreachable, or one landing being unreadable, must cost that landing and nothing
else. Every failure is caught per landing and COUNTED -- a pass that dies on the third landing
would discard the two it already read, and a pass that swallows failures silently is a reporting
obligation that has been switched off.

FAILING OPEN IS NOT THE SAME AS EXITING ZERO, and until WS-P3.6 Increment 3 this file conflated
them. A pass that read six of eight repositories printed its per-repository `unavailable: true`
into an aggregate nobody had to look at, and exited 0 -- success-shaped output over a measurement
that did not happen. The three exit codes below separate the three answers a caller actually
needs, because a broken tool and an honest finding sharing one code is a collision this estate has
already paid for once.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Protocol

import typer

from landing_ledger.audit import (
    SETTLE_SECONDS,
    RepoAudit,
    audit_observation,
    audit_repository,
    branch_status,
)
from landing_ledger.github import (
    GitHubReader,
    LedgerError,
    branch_tip,
    current_rule_revision,
    default_branch,
    landing_shas,
    read_landing,
    read_pending_updates,
    workflow_runs_at,
)
from landing_ledger.model import BranchStatus
from landing_ledger.orchestrator_client import LedgerWriteError, OrchestratorClient
from landing_ledger.record import is_known_defective_metadata_landing, landing_observation

app = typer.Typer(no_args_is_help=True)

RECOVERABLE = (LedgerError, LedgerWriteError, KeyError, TypeError, ValueError)

# Nothing to report, and everything was measured.
EXIT_OK = 0
# Something was found. The pass worked; reality did not.
#
# A FINDING ONLY -- never an exception. `audit.exceptions` holds subjects current policy can never
# decide, and they are printed in the report and recorded in the observation without touching this
# code. That is the point: a control that exits non-zero on a condition no pass will ever clear is
# a control nobody reads, and a real finding then arrives as one more line in a report already
# known to be noise.
EXIT_FINDINGS = 2
# Some part of reality could not be read, so the answer is missing rather than clean. This
# outranks findings: an incomplete pass cannot claim it found everything there was to find.
EXIT_INCOMPLETE = 3


def _exit_code(*, findings: bool, incomplete: bool) -> int:
    if incomplete:
        return EXIT_INCOMPLETE
    return EXIT_FINDINGS if findings else EXIT_OK


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
        # NOT a skip, and the difference is the exit code. A skip means this pass could not do
        # something it should have done, so it reaches `incomplete`. An exempt landing is one this
        # pass deliberately did not attempt, for a reason that is permanent -- so it must not.
        "exempt": 0,
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
        # THE SIX ROWS OF THE KNOWN-DEFECTIVE WINDOW ARE NOT RE-READ AND NOT RE-WRITTEN, and this
        # is the consumer of that list which is easy to miss. Their stored `permitted_by` lacks
        # the three update keys because the reader could not read a requirement range's trailer;
        # the fixed reader now derives them. That is a different fact digest at a
        # `source_reference` which is deliberately immutable, so the write is an
        # `observation_conflict` -- caught below as a skip, which makes the pass incomplete and
        # exits 3 every night for as long as they sit inside the window. There is no route to
        # correcting the rows and none is wanted, so the honest act is not to attempt the write.
        # Skipped under `--dry-run` too: a dry run must describe what a real pass would do.
        #
        # THE PREMISE IS "ALREADY STORED", AND IT WAS MEASURED RATHER THAN ASSUMED. Skipping a row
        # that is NOT stored would suppress a write that would have succeeded, so the premise is
        # load-bearing: all six were read back from production on 2026-08-29 with their stored
        # `permitted_by` contents, and an observation has no delete route, so nothing can unmake
        # them. What could falsify it is a database restored from before 2026-08-28 -- and only
        # while these commits remain inside the pass's `--days` lookback, which for landings of
        # 2026-08-28 ends around 2026-09-27, after which `landing_shas` never yields them and this
        # branch matches nothing ever again. In that world the ledger has lost its whole history
        # and six rows are the least of what is missing, so the check is deliberately not built:
        # it would make "audit this repository" assert a global property of the ledger.
        if is_known_defective_metadata_landing(repository, sha):
            summary["exempt"] += 1
            continue
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


# A pass IS a moment, so its identity spells one -- the shape the sibling adapters already use for
# `--pass-id`. Requiring it rather than accepting any string is what lets the record's `observed_at`
# be a function of the pass instead of of the clock, which is what makes a re-run replay.
PASS_ID_FORMAT = "%Y%m%dT%H%M%SZ"


def pass_moment(pass_id: str) -> datetime:
    try:
        return datetime.strptime(pass_id, PASS_ID_FORMAT).replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError(f"pass id must be {PASS_ID_FORMAT}, got {pass_id!r}") from error


class LedgerReader(Protocol):
    """The read surface the audit needs, structural so a test can pass a hermetic fake."""

    def read_landings(self, repository: str) -> list[dict[str, Any]]: ...

    def read_evidence_pack(self, work_unit_id: str) -> dict[str, Any] | None: ...

    def read_unit_history(self, work_unit_id: str) -> list[dict[str, Any]] | None: ...


class _NullWriter:
    """A dry-run writer: any use is a bug, so it fails loudly rather than looking successful."""

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("dry run must not record observations")


def audit_pass(
    reader: GitHubReader,
    ledger: LedgerReader,
    writer: ObservationWriter,
    *,
    repository: str,
    pass_id: str,
    now: datetime,
    settle_seconds: int,
    dry_run: bool,
) -> tuple[RepoAudit, dict[str, Any]]:
    """One audit pass over one repository. Never raises; an unreadable repository is UNAVAILABLE.

    Unavailable is deliberately not the same as clean. The observation is still written -- the
    heartbeat has to exist for the pass to be distinguishable from a pass that never ran -- but it
    carries no verdict, and the caller turns it into the incomplete exit code.

    TWO CLOCKS, deliberately. `now` is the wall, and it is what "has this been green long enough
    to be worth reporting" must be measured against, because the GitHub state was read just now.
    The RECORD's clock is the pass's own moment, so that re-running a pass by its id replays.
    """
    try:
        base_ref = default_branch(reader, repository)
        # DETECTOR C'S READ IS CAUGHT SEPARATELY, and the nesting is the point. A branch this pass
        # could not ask about must reach `unavailable` -- never a pass -- but it must not discard
        # the landings and open updates the same pass already measured. `branch=None` carries the
        # first without costing the second; `audit_repository` turns it into `unavailable`.
        try:
            tip = branch_tip(reader, repository, base_ref)
            branch: BranchStatus | None = branch_status(
                tip, workflow_runs_at(reader, repository, tip)
            )
        except RECOVERABLE:
            branch = None
        audit = audit_repository(
            repository=repository,
            landings=[row.get("facts") for row in ledger.read_landings(repository)],
            pending=read_pending_updates(reader, repository, base_ref),
            rule_revision=current_rule_revision(reader, repository, base_ref),
            units=ledger,
            now=now,
            branch=branch,
            settle_seconds=settle_seconds,
        )
    except RECOVERABLE:
        audit = RepoAudit(
            repository=repository,
            rule_revision=None,
            landings_audited=0,
            permitted_landings=0,
            factory_landings=0,
            pending_audited=0,
            unavailable=True,
        )
    # The record's own clock is the PASS's, never the wall's. The orchestrator's replay check
    # compares the whole stored command, `observed_at` included, so a wall-clock timestamp would
    # make re-running a pass by its own id an `idempotency_conflict` -- the same key, a different
    # payload -- rather than the replay it obviously is.
    body = audit_observation(audit, pass_id, pass_moment(pass_id))
    if not dry_run:
        try:
            writer.record_observation(body)
        except RECOVERABLE:
            # The heartbeat did not land, so this repository's answer is missing however good the
            # measurement was. Say so rather than reporting the verdict as filed.
            audit = replace(audit, unavailable=True)
    return audit, body


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
    # A pass that could not read a repository, or that dropped a landing it did read, has NOT
    # recorded the window it claims to cover. Exiting 0 there is the aggregate hiding the
    # per-repository flag, which is the whole defect. A skipped landing counts too: it is usually
    # an unreadable commit, but it is also how a rejected write -- a landing whose facts have
    # drifted -- arrives, and that is worth a person's attention either way.
    incomplete = any(summary["unavailable"] or summary["skipped"] for summary in summaries)
    raise typer.Exit(code=_exit_code(findings=False, incomplete=incomplete))


@app.command("audit")
def audit_command(
    repository: Annotated[list[str], typer.Option(help="Repository as owner/name; repeatable.")],
    pass_id: Annotated[
        str | None,
        typer.Option(help=f"Identity of this pass as {PASS_ID_FORMAT}; defaults to the UTC now."),
    ] = None,
    settle_seconds: Annotated[
        int, typer.Option(help="How long armed-and-green must persist before it is reported.")
    ] = SETTLE_SECONDS,
    orchestrator_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    credential_key_id: Annotated[str, typer.Option()] = "orchestrator-observer",
    dry_run: Annotated[bool, typer.Option(help="Print the audit; write nothing.")] = False,
) -> None:
    """Re-evaluate what the rule permitted, and look for what it silently stopped permitting."""
    github_token = os.environ.get("LANDING_LEDGER_GITHUB_TOKEN")
    if not github_token:
        typer.echo("LANDING_LEDGER_GITHUB_TOKEN is required", err=True)
        raise typer.Exit(code=1)
    token = os.environ.get("LANDING_LEDGER_TOKEN", "")
    if not token:
        # Required even for a dry run, unlike `record`: the audit READS the ledger through the
        # same credential, so without it there is nothing to audit.
        typer.echo("LANDING_LEDGER_TOKEN is required", err=True)
        raise typer.Exit(code=1)
    now = datetime.now(UTC)
    identity = pass_id or now.strftime(PASS_ID_FORMAT)
    try:
        # Validated here, once, before anything is read: `audit_pass` promises never to raise, and
        # a malformed identity is the operator's typo rather than a repository being unreadable.
        pass_moment(identity)
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    audits: list[RepoAudit] = []
    bodies: list[dict[str, Any]] = []
    with (
        GitHubReader(token=github_token) as reader,
        OrchestratorClient(
            base_url=orchestrator_url, credential_key_id=credential_key_id, token=token
        ) as client,
    ):
        for name in repository:
            audit, body = audit_pass(
                reader,
                client,
                _NullWriter() if dry_run else client,
                repository=name,
                pass_id=identity,
                now=now,
                settle_seconds=settle_seconds,
                dry_run=dry_run,
            )
            audits.append(audit)
            bodies.append(body)
    report: dict[str, Any] = {
        "pass_id": identity,
        "repositories": [asdict(audit) for audit in audits],
    }
    if dry_run:
        report["records"] = bodies
    typer.echo(json.dumps(report, indent=2, sort_keys=True))
    raise typer.Exit(
        code=_exit_code(
            findings=any(audit.findings for audit in audits),
            incomplete=any(audit.unavailable for audit in audits),
        )
    )
