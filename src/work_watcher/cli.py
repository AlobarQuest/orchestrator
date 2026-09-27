"""The work lane's watcher pass, in the carry's invocation and before it (ADR-0029).

Reads the work proposals a human approved in change-manager, asks the orchestrator which of them
caused work that is finished, and retires those records -- or reports what it would retire,
depending on whether it was asked to act.

**A BARE INVOCATION WRITES NOTHING.** `--retire` is what makes this pass act; without it the
answers go to stdout and both systems are left exactly as they were. That is the mode in which the
lane is inspected, and it is the carry's own property, kept deliberately identical.

**IT DECIDES NOTHING.** Whether a record's work is complete is derived inside the orchestrator, in
the transaction that reads the units; whether a record may be retired on that fact is decided
inside change-manager, in the transaction that records it. This program relays a verdict it did
not compute about a record it did not approve, which is the whole reason it may run unattended.

**IT RUNS BEFORE THE CARRY, and that ordering is the point rather than a preference.** The carry
selects on `status=approved`. A record whose work is done is still in that queue until this pass
retires it, so a carry that read the queue first would re-register a finished revision, draw the
409 this program exists to prevent, and only then watch the record be retired -- reporting a
finding on the morning the defect was fixed.

EXIT CODES: 0 clean, 1 tool failure, 2 unusable input, 3 findings.

**WHAT IS NOT A FINDING**, because this estate has now left a control permanently red four times
by getting this wrong. A retirement performed is not a finding -- it is the job. A replay of one
already made is not a finding; change-manager answers 200 unchanged by design, because a sweeping
producer must not turn its own earlier work into an alarm. A record whose work is merely
incomplete is not a finding: that is what an approved queue IS, and reporting it would make this
control red for every record waiting its turn. A record with no work at all is not a finding
either -- it has not been carried yet, which is the carry's business and not this pass's.

A finding is a record this pass could not get an answer about, a retirement change-manager
refused, and -- since ruling B1 (Devon, 2026-09-27) -- a STALE record: one pending or approved while
a newer revision's record has superseded it, or while the Dependabot pull request it names was
closed or merged by hand. All of them need a person to look at why, and a stale record needs a
person to RETIRE it: this pass reports it and holds no write that could (see
`work_watcher/staleness.py` for why the report belongs here and in this order). A record whose
staleness cannot be assessed because its reasoning names no pull request is printed and is NOT a
finding. A pass that needed GitHub and had no credential for it exits 2, which outranks 3 exactly
as the launcher ranks them: a lane that cannot tell whether a record is stale has stopped doing its
job.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Protocol

from work_carrier.change_manager import (
    DEFAULT_BASE_URL as CHANGE_MANAGER_DEFAULT_BASE_URL,
)
from work_carrier.change_manager import (
    ChangeManagerError as ListingError,
)
from work_carrier.change_manager import (
    WorkRecord,
    WorkRecordSource,
)
from work_watcher.change_manager import (
    ChangeManagerError,
    RetirementClient,
    RetirementRefused,
)
from work_watcher.github import (
    CLOSED,
    GITHUB_TOKEN_ENV,
    MERGED,
    GitHubError,
    PullRequestReader,
)
from work_watcher.orchestrator_client import (
    DEFAULT_BASE_URL as ORCHESTRATOR_DEFAULT_BASE_URL,
)
from work_watcher.orchestrator_client import (
    OrchestratorClient,
    OrchestratorError,
)
from work_watcher.pipeline import PipelineListing, PipelineRecord
from work_watcher.staleness import LIVE, pull_request_of, superseded_by

EXIT_OK = 0
EXIT_TOOL_FAILURE = 1
EXIT_UNUSABLE = 2
EXIT_FINDINGS = 3

# The orchestrator credential this pass reads with. SYSTEM, the same actor the carry registers
# with: the read is authentication-only and needs no role, and a second identity would attribute
# nothing the change record does not already carry.
SYSTEM_KEY_ID = "orchestrator-system"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="work-watcher",
        description=(
            "Retire every approved change-manager work proposal whose work the software "
            "delivery system has finished building. Without --retire the pass reports and "
            "writes nothing."
        ),
    )
    parser.add_argument(
        "--retire",
        action="store_true",
        help=(
            "actually retire the records whose work is complete. Without it the pass reports "
            "and writes nothing, to either system."
        ),
    )
    parser.add_argument(
        "--change-manager-url",
        default=os.environ.get("CHANGE_MANAGER_URL", CHANGE_MANAGER_DEFAULT_BASE_URL),
    )
    parser.add_argument(
        "--orchestrator-url",
        default=os.environ.get("WORK_WATCHER_ORCHESTRATOR_URL", ORCHESTRATOR_DEFAULT_BASE_URL),
    )
    return parser.parse_args(argv)


def _source(args: argparse.Namespace) -> PipelineListing | None:
    """The listing, read with the RETIREMENT bearer rather than the carry's read-only one.

    The `propose` scope includes every read route, so one credential serves both halves of this
    pass and there is no second secret to keep in step. The carry keeps its own narrower bearer.
    """
    token = os.environ.get("WORK_WATCHER_CHANGE_MANAGER_TOKEN", "")
    if not token:
        return None
    return PipelineListing(base_url=args.change_manager_url, token=token)


def _retirer(args: argparse.Namespace) -> RetirementClient | None:
    token = os.environ.get("WORK_WATCHER_CHANGE_MANAGER_TOKEN", "")
    if not token:
        return None
    return RetirementClient(base_url=args.change_manager_url, token=token)


class PipelineSource(WorkRecordSource, Protocol):
    """The carry's listing plus the whole pipeline, which the staleness report reads."""

    def work_pipeline(self) -> tuple[PipelineRecord, ...]: ...


def _github() -> PullRequestReader | None:
    token = os.environ.get(GITHUB_TOKEN_ENV, "")
    if not token:
        return None
    return PullRequestReader(token)


def _reader(args: argparse.Namespace) -> OrchestratorClient | None:
    token = os.environ.get("WORK_WATCHER_ORCHESTRATOR_TOKEN", "")
    if not token:
        return None
    return OrchestratorClient(token, SYSTEM_KEY_ID, base_url=args.orchestrator_url)


class _Stale:
    """What the staleness question answered for one live record.

    `finding` is set for a stale record AND for one whose pull request could not be read -- both
    need a person. `note` is the suffix a not-stale line carries, so an unassessed record says why.
    `unusable` marks a record that needed GitHub on a pass with no credential for it.
    """

    __slots__ = ("finding", "note", "unusable")

    def __init__(
        self, *, finding: str | None = None, note: str = "", unusable: bool = False
    ) -> None:
        self.finding = finding
        self.note = note
        self.unusable = unusable


def _staleness(
    change_record_id: int,
    package_id: str,
    package_revision: int,
    reasoning: str,
    pipeline: tuple[PipelineRecord, ...],
    github: PullRequestReader | None,
) -> _Stale:
    """Ruling B1: is this live record superseded, or has its pull request gone? REPORTED ONLY."""
    newer = superseded_by(package_id, package_revision, pipeline)
    if newer is not None:
        return _Stale(
            finding=(
                f"STALE: superseded by change record {newer.change_record_id} "
                f"(revision {newer.package_revision}, {newer.status}); the carry can never "
                "register this revision -- a person retires it"
            )
        )
    named = pull_request_of(reasoning)
    if named is None:
        return _Stale(note="; staleness not assessed: the reasoning names no pull request")
    repository, number = named
    if github is None:
        return _Stale(
            note=f"; staleness not assessed: no GitHub credential ({GITHUB_TOKEN_ENV})",
            unusable=True,
        )
    try:
        state = github.state(repository, number)
    except GitHubError as error:
        return _Stale(finding=f"could not read {repository}#{number}: {error}")
    if state in (CLOSED, MERGED):
        verb = "merged" if state == MERGED else "closed without merging"
        return _Stale(
            finding=(
                f"STALE: {repository}#{number} was {verb} and the work is not built; "
                "a person retires this record"
            )
        )
    return _Stale(note=f"; {repository}#{number} is open")


def _consider(
    record: WorkRecord,
    reader: OrchestratorClient,
    retirer: RetirementClient | None,
    out,
    pipeline: tuple[PipelineRecord, ...] = (),
    github: PullRequestReader | None = None,
) -> tuple[bool, str | None, bool]:
    """Report one record, and retire it when its work is done and this pass was asked to.

    Returns `(retired, finding, unusable)`. Per-record isolation, deliberately: one record this
    pass cannot answer about must not stop the rest being retired, and each retirement is its own
    transaction in change-manager, so there is nothing partial to unwind.
    """
    label = (
        f"change record {record.change_record_id}: "
        f"{record.package_id} revision {record.package_revision}"
    )
    try:
        answer = reader.work_for(record.change_record_id)
    except OrchestratorError as error:
        print(f"[FINDING]  {label}: {error}", file=out)
        return False, str(error), False

    if not answer.all_units_completed:
        # COMPLETION FIRST, STALENESS SECOND, and the order is the whole rule: a record whose
        # work the factory built is retired above even though its pull request is now closed.
        states = ", ".join(answer.unit_states) or "no units yet"
        stale = _staleness(
            record.change_record_id,
            record.package_id,
            record.package_revision,
            record.reasoning,
            pipeline,
            github,
        )
        if stale.finding is not None:
            print(f"[FINDING]  {label}: {stale.finding} ({states})", file=out)
            return False, stale.finding, False
        print(f"[WAITING]  {label}: {states}{stale.note}", file=out)
        return False, None, stale.unusable

    if retirer is None:
        print(f"[COMPLETE] {label}: would retire — this pass was not asked to (--retire)", file=out)
        return False, None, False

    try:
        retirer.retire(
            record.change_record_id,
            package_id=record.package_id,
            package_revision=record.package_revision,
        )
    except (RetirementRefused, ChangeManagerError) as error:
        print(f"[FINDING]  {label}: NOT RETIRED: {error}", file=out)
        return False, str(error), False
    print(f"[RETIRED]  {label}", file=out)
    return True, None, False


def _consider_pending(
    row: PipelineRecord,
    pipeline: tuple[PipelineRecord, ...],
    github: PullRequestReader | None,
    out,
) -> tuple[str | None, bool]:
    """A PENDING record has caused no work -- the carry reads only approved ones -- so the only
    question about it is staleness. Returns `(finding, unusable)`."""
    label = (
        f"change record {row.change_record_id}: {row.package_id} revision {row.package_revision}"
    )
    stale = _staleness(
        row.change_record_id,
        row.package_id,
        row.package_revision,
        row.reasoning,
        pipeline,
        github,
    )
    if stale.finding is not None:
        print(f"[FINDING]  {label}: {stale.finding} (pending)", file=out)
        return stale.finding, False
    print(f"[PENDING]  {label}: awaiting a person's decision{stale.note}", file=out)
    return None, stale.unusable


def run(
    argv: list[str],
    *,
    source: PipelineSource | None = None,
    reader: OrchestratorClient | None = None,
    retirer: RetirementClient | None = None,
    github: PullRequestReader | None = None,
    out=sys.stdout,
) -> int:
    args = _parse_args(argv)

    records_source = source if source is not None else _source(args)
    if records_source is None:
        print("[UNUSABLE] WORK_WATCHER_CHANGE_MANAGER_TOKEN is not set", file=out)
        return EXIT_UNUSABLE

    work_reader = reader if reader is not None else _reader(args)
    if work_reader is None:
        print("[UNUSABLE] WORK_WATCHER_ORCHESTRATOR_TOKEN is not set", file=out)
        return EXIT_UNUSABLE

    # THE FLAG DECIDES, NOT THE PRESENCE OF A CLIENT. A pass that was not asked to retire does not
    # write even when a credential and an injected client are both to hand, which is what makes
    # "a bare invocation writes nothing" a property of this branch rather than of how the caller
    # happened to configure the environment.
    writer = (retirer if retirer is not None else _retirer(args)) if args.retire else None
    if args.retire and writer is None:
        print("[UNUSABLE] WORK_WATCHER_CHANGE_MANAGER_TOKEN is not set", file=out)
        return EXIT_UNUSABLE

    try:
        records = records_source.approved_work()
        pipeline = records_source.work_pipeline()
    except ListingError as error:
        print(f"[TOOL FAILURE] {error}", file=out)
        return EXIT_TOOL_FAILURE

    pull_requests = github if github is not None else _github()

    retired = 0
    unusable = False
    findings: list[str] = []
    for record in records:
        moved, finding, blind = _consider(record, work_reader, writer, out, pipeline, pull_requests)
        retired += 1 if moved else 0
        unusable = unusable or blind
        if finding is not None:
            findings.append(finding)

    pending = [row for row in pipeline if row.status in LIVE and row.status != "approved"]
    for row in pending:
        finding, blind = _consider_pending(row, pipeline, pull_requests, out)
        unusable = unusable or blind
        if finding is not None:
            findings.append(finding)

    stale = sum(1 for finding in findings if finding.startswith("STALE:"))
    print(
        f"\n{len(records)} approved, {retired} retired, {len(findings)} findings "
        f"({stale} stale, {len(pending)} pending).",
        file=out,
    )
    if unusable:
        print(f"[UNUSABLE] {GITHUB_TOKEN_ENV} is not set; staleness was not assessed", file=out)
        return EXIT_UNUSABLE
    return EXIT_FINDINGS if findings else EXIT_OK


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
