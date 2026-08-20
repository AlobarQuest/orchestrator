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

A finding is a record this pass could not get an answer about, and a retirement change-manager
refused. Both need a person to look at why.
"""

from __future__ import annotations

import argparse
import os
import sys

from work_carrier.change_manager import (
    DEFAULT_BASE_URL as CHANGE_MANAGER_DEFAULT_BASE_URL,
)
from work_carrier.change_manager import (
    ChangeManagerError as ListingError,
)
from work_carrier.change_manager import (
    HttpWorkRecordSource,
    WorkRecord,
    WorkRecordSource,
)
from work_watcher.change_manager import (
    ChangeManagerError,
    RetirementClient,
    RetirementRefused,
)
from work_watcher.orchestrator_client import (
    DEFAULT_BASE_URL as ORCHESTRATOR_DEFAULT_BASE_URL,
)
from work_watcher.orchestrator_client import (
    OrchestratorClient,
    OrchestratorError,
)

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


def _source(args: argparse.Namespace) -> WorkRecordSource | None:
    """The listing, read with the RETIREMENT bearer rather than the carry's read-only one.

    The `propose` scope includes every read route, so one credential serves both halves of this
    pass and there is no second secret to keep in step. The carry keeps its own narrower bearer.
    """
    token = os.environ.get("WORK_WATCHER_CHANGE_MANAGER_TOKEN", "")
    if not token:
        return None
    return HttpWorkRecordSource(base_url=args.change_manager_url, token=token)


def _retirer(args: argparse.Namespace) -> RetirementClient | None:
    token = os.environ.get("WORK_WATCHER_CHANGE_MANAGER_TOKEN", "")
    if not token:
        return None
    return RetirementClient(base_url=args.change_manager_url, token=token)


def _reader(args: argparse.Namespace) -> OrchestratorClient | None:
    token = os.environ.get("WORK_WATCHER_ORCHESTRATOR_TOKEN", "")
    if not token:
        return None
    return OrchestratorClient(token, SYSTEM_KEY_ID, base_url=args.orchestrator_url)


def _consider(
    record: WorkRecord,
    reader: OrchestratorClient,
    retirer: RetirementClient | None,
    out,
) -> tuple[bool, str | None]:
    """Report one record, and retire it when its work is done and this pass was asked to.

    Returns `(retired, finding)`. Per-record isolation, deliberately: one record this pass cannot
    answer about must not stop the rest being retired, and each retirement is its own transaction
    in change-manager, so there is nothing partial to unwind.
    """
    label = (
        f"change record {record.change_record_id}: "
        f"{record.package_id} revision {record.package_revision}"
    )
    try:
        answer = reader.work_for(record.change_record_id)
    except OrchestratorError as error:
        print(f"[FINDING]  {label}: {error}", file=out)
        return False, str(error)

    if not answer.all_units_completed:
        states = ", ".join(answer.unit_states) or "no units yet"
        print(f"[WAITING]  {label}: {states}", file=out)
        return False, None

    if retirer is None:
        print(f"[COMPLETE] {label}: would retire — this pass was not asked to (--retire)", file=out)
        return False, None

    try:
        retirer.retire(
            record.change_record_id,
            package_id=record.package_id,
            package_revision=record.package_revision,
        )
    except (RetirementRefused, ChangeManagerError) as error:
        print(f"[FINDING]  {label}: NOT RETIRED: {error}", file=out)
        return False, str(error)
    print(f"[RETIRED]  {label}", file=out)
    return True, None


def run(
    argv: list[str],
    *,
    source: WorkRecordSource | None = None,
    reader: OrchestratorClient | None = None,
    retirer: RetirementClient | None = None,
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
    except ListingError as error:
        print(f"[TOOL FAILURE] {error}", file=out)
        return EXIT_TOOL_FAILURE

    retired = 0
    findings: list[str] = []
    for record in records:
        moved, finding = _consider(record, work_reader, writer, out)
        retired += 1 if moved else 0
        if finding is not None:
            findings.append(finding)

    print(
        f"\n{len(records)} approved, {retired} retired, {len(findings)} findings.",
        file=out,
    )
    return EXIT_FINDINGS if findings else EXIT_OK


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
