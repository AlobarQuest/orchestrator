"""The carry, on a schedule. ADR-0026, completed by ADR-0027.

Reads the work proposals a human approved in change-manager and, for each, builds the exact
intake payload the orchestrator wants -- then either prints it or registers it, depending on
whether it was asked to.

**A BARE INVOCATION WRITES NOTHING.** `--register` is what makes this pass act; without it the
payloads go to stdout and both systems are left exactly as they were. That is not a leftover of
the old design, it is the mode in which the lane is inspected: a person can read what would be
registered without anything being.

**WITH `--register`, THE LAST STEP IS NO LONGER A HUMAN PASTE.** ADR-0027 removed the
`ActorRole.HUMAN` requirement from intake registration, having found that the gate was
protecting a transcription: every intake in production was authored by an AI and typed into a
form by a person. What replaced it is attribution -- a machine-registered intake must name the
approved change record that caused it, which is exactly what this program has and a person
pasting JSON did not. ADR-0006 is narrowed, not overturned: the breakdown approval and the
authority approval are decisions and are still a human in a browser, so this pass ends at a
queue for a person rather than at a running change.

**IT STILL CANNOT DECIDE ANYTHING.** Every rule about what may be registered is evaluated inside
the orchestrator, in the transaction that records it. This program relays a payload it did not
compose, for a record it did not approve.

WHY IT ENUMERATES FROM CHANGE-MANAGER, naming the pipeline. `GET /api/items` withholds a proposed
source from any caller that does not name one, because the 04:00 change-window executor lists
approved items with no source filter and hands what comes back to an LLM agent holding production
Coolify tools. Naming the source is how this program sees what that one deliberately cannot.

EXIT CODES: 0 clean, 1 tool failure, 2 unusable input, 3 findings. A record that could not be
PREPARED is a finding, and so is one that could not be REGISTERED -- somebody has to look at why
in both cases. A record carried successfully is NOT, nor is one merely prepared on a pass that
was not asked to register: making either a finding would leave this control permanently red for
doing its job, which this estate has now recorded four times.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from work_carrier.change_manager import (
    DEFAULT_BASE_URL,
    ChangeManagerError,
    HttpWorkRecordSource,
    WorkRecordSource,
)
from work_carrier.orchestrator_client import (
    DEFAULT_BASE_URL as ORCHESTRATOR_DEFAULT_BASE_URL,
)
from work_carrier.orchestrator_client import (
    IntakeRefused,
    OrchestratorClient,
    OrchestratorError,
)
from work_carrier.prepare import Prepared, Refused, prepare

EXIT_OK = 0
EXIT_TOOL_FAILURE = 1
EXIT_UNUSABLE = 2
EXIT_FINDINGS = 3

DEFAULT_CHECKOUT_ROOT = "~/Projects"

# ADR-0027 recommends the SYSTEM actor: it already performs canonical mutation, so it needs no
# registry entry and no image rebuild. A carrier-specific actor would attribute more precisely
# and costs a merged security-standards commit plus a rebuild, because `agent_id` resolves
# against a bundle baked into the image. `source_system` on the record and the change record id
# on the revision already carry the provenance a distinct identity would add.
SYSTEM_KEY_ID = "orchestrator-system"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="work-carrier",
        description=(
            "Carry every approved change-manager work proposal into an orchestrator package "
            "intake. Without --register the pass prints the payloads and writes nothing."
        ),
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help=(
            "actually register the prepared intakes (ADR-0027). Without it the pass reports "
            "and writes nothing, to either system."
        ),
    )
    parser.add_argument(
        "--checkout-root",
        default=os.environ.get("WORK_CARRIER_CHECKOUT_ROOT", DEFAULT_CHECKOUT_ROOT),
        help="Directory holding the repository checkouts the records name.",
    )
    parser.add_argument(
        "--change-manager-url",
        default=os.environ.get("CHANGE_MANAGER_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument(
        "--orchestrator-url",
        default=os.environ.get("WORK_CARRIER_ORCHESTRATOR_URL", ORCHESTRATOR_DEFAULT_BASE_URL),
    )
    return parser.parse_args(argv)


def _source(args: argparse.Namespace) -> WorkRecordSource | None:
    token = os.environ.get("CHANGE_MANAGER_TOKEN", "")
    if not token:
        return None
    return HttpWorkRecordSource(base_url=args.change_manager_url, token=token)


def _registrar(args: argparse.Namespace) -> OrchestratorClient | None:
    token = os.environ.get("WORK_CARRIER_ORCHESTRATOR_TOKEN", "")
    if not token:
        return None
    return OrchestratorClient(token, SYSTEM_KEY_ID, base_url=args.orchestrator_url)


def _writer_for(
    args: argparse.Namespace, registrar: OrchestratorClient | None, out
) -> tuple[OrchestratorClient | None, int | None]:
    """The client this pass will write with, or the exit code that says why there is none.

    THE FLAG DECIDES, NOT THE PRESENCE OF A CLIENT. A pass that was not asked to register does
    not touch the orchestrator even when a credential and an injected client are both to hand,
    which is what makes "a bare invocation writes nothing" a property of this branch rather than
    of how the caller happened to configure the environment.
    """
    if not args.register:
        return None, None
    if registrar is not None:
        return registrar, None
    try:
        writer = _registrar(args)
    except OrchestratorError as error:
        # The CONSTRUCTOR raises for some malformed URLs and request time for others, so catching
        # only the latter leaves an environment-variable typo crashing the pass with a traceback
        # -- exactly what the constructor's own guard exists to prevent. Its sibling
        # `HttpWorkRecordSource` has always reported this class as a tool failure; this agrees.
        print(f"[TOOL FAILURE] {error}", file=out)
        return None, EXIT_TOOL_FAILURE
    if writer is None:
        print("[UNUSABLE] WORK_CARRIER_ORCHESTRATOR_TOKEN is not set", file=out)
        return None, EXIT_UNUSABLE
    return writer, None


def _carry(item: Prepared, writer: OrchestratorClient | None, out) -> str | None:
    """Report one prepared record, and register it when this pass was asked to.

    Returns the refusal message when a registration failed and `None` otherwise -- including on
    a pass that was not asked to register, which is not a finding.
    """
    print(
        f"[PREPARED] change record {item.record.change_record_id}: "
        f"{item.record.package_id} revision {item.record.package_revision} "
        f"(approved by {item.record.decided_by or 'unrecorded'})",
        file=out,
    )
    print(f"           package: {item.package_path}", file=out)
    if writer is None:
        print("           not registered — this pass was not asked to (--register):", file=out)
        print(json.dumps(item.payload, sort_keys=True, default=str), file=out)
        return None
    try:
        revision = writer.register_intake(item.payload)
    except OrchestratorError as error:
        # Per-record isolation, and it matters more here than at prepare time: one refusal must
        # not strand the rest of an approved queue behind it, and a registration is its own
        # transaction in the orchestrator, so there is nothing partial to unwind.
        print(f"           NOT CARRIED: {error}", file=out)
        guidance = _guidance(error)
        if guidance:
            print(f"           {guidance}", file=out)
        return str(error)
    print(f"           carried: revision {revision.get('id')}", file=out)
    return None


def _guidance(error: OrchestratorError) -> str:
    """What a person should do about a refusal whose own message would misdirect them.

    `package_intake_conflict` reads "already registered with different content", and for the
    case this lane actually produces that is FALSE: a revision somebody registered by hand
    through the form carries no change record and a different registrar, so the carry's payload
    differs from the stored row in exactly those two fields and in no content at all. Whoever
    reads the morning log would go looking for a divergence that is not there.

    It is also the one refusal here that repeats. Nothing marks a change record carried -- the
    carry holds no write to change-manager, deliberately -- so an approved record stays in the
    queue and is re-attempted every pass. That is harmless while it replays and reports a
    finding every morning once it conflicts, so the report has to name the act that ends it:
    a person resolves the record in change-manager, and it leaves the approved queue.
    """
    if not isinstance(error, IntakeRefused) or error.code != "package_intake_conflict":
        return ""
    return (
        "this package revision is already registered under a different cause or registrar; "
        "a person decides whether the existing revision or this change record is right, and "
        "resolving the record in change-manager is what takes it out of this queue"
    )


def _carry_all(
    prepared: list[Prepared], writer: OrchestratorClient | None, out
) -> tuple[int, list[str]]:
    carried = 0
    unregistered: list[str] = []
    for item in prepared:
        failure = _carry(item, writer, out)
        if failure is not None:
            unregistered.append(failure)
        elif writer is not None:
            carried += 1
    return carried, unregistered


def run(
    argv: list[str],
    *,
    source: WorkRecordSource | None = None,
    registrar: OrchestratorClient | None = None,
    out=sys.stdout,
) -> int:
    args = _parse_args(argv)
    root = Path(args.checkout_root).expanduser()
    if not root.is_dir():
        print(f"[UNUSABLE] no checkout root at {root}", file=out)
        return EXIT_UNUSABLE
    records_source = source if source is not None else _source(args)
    if records_source is None:
        print("[UNUSABLE] CHANGE_MANAGER_TOKEN is not set", file=out)
        return EXIT_UNUSABLE

    writer, refusal = _writer_for(args, registrar, out)
    if refusal is not None:
        return refusal

    try:
        records = records_source.approved_work()
    except ChangeManagerError as error:
        print(f"[TOOL FAILURE] {error}", file=out)
        return EXIT_TOOL_FAILURE

    prepared: list[Prepared] = []
    refused: list[Refused] = []
    for record in records:
        # Per-record isolation, deliberately: one record that cannot be prepared must not stop
        # the others being carried, and there is nothing to roll back because nothing was
        # written. A pass is a report over the whole approved queue or it is not a report.
        outcome = prepare(record, checkout_root=root)
        if isinstance(outcome, Prepared):
            prepared.append(outcome)
        else:
            refused.append(outcome)

    carried, unregistered = _carry_all(prepared, writer, out)
    for item in refused:
        print(
            f"[REFUSED]  change record {item.record.change_record_id}: "
            f"{item.reason} — {item.detail}",
            file=out,
        )

    print(
        f"\n{len(records)} approved, {len(prepared)} prepared, {carried} carried, "
        f"{len(refused)} refused, {len(unregistered)} not carried.",
        file=out,
    )
    return EXIT_FINDINGS if (refused or unregistered) else EXIT_OK


def main() -> int:
    argv = sys.argv[1:]
    args = _parse_args(argv)
    if not args.register:
        return run(argv)
    try:
        writer = _registrar(args)
    except OrchestratorError as error:
        print(f"[TOOL FAILURE] {error}")
        return EXIT_TOOL_FAILURE
    if writer is None:
        print("[UNUSABLE] WORK_CARRIER_ORCHESTRATOR_TOKEN is not set")
        return EXIT_UNUSABLE
    with writer:
        return run(argv, registrar=writer)


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
