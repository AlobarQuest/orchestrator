"""The carry, on a schedule. ADR-0026.

Reads the work proposals a human approved in change-manager and prints, for each, the exact
intake payload to paste into the orchestrator's `/review/intakes/new` form -- or the reason it
cannot be prepared.

**THIS PROGRAM WRITES NOTHING, to either system.** It holds no write path to change-manager and
none to the orchestrator, so "a record the carry cannot prepare is left exactly as it was" is a
property of its shape rather than of a branch that has to be reached correctly. The last step of
the carry is a HUMAN PASTE, and that is a decision ADR-0026 declined to make rather than a gap:
intake requires an `ActorRole.HUMAN` actor and human gates are browser-only, permanently
(ADR-0006), so registering an intake unattended would be the first automated path into canonical
work. `prepare.py` carries the argument.

WHY IT ENUMERATES FROM CHANGE-MANAGER, naming the pipeline. `GET /api/items` withholds a proposed
source from any caller that does not name one, because the 04:00 change-window executor lists
approved items with no source filter and hands what comes back to an LLM agent holding production
Coolify tools. Naming the source is how this program sees what that one deliberately cannot.

EXIT CODES: 0 clean, 1 tool failure, 2 unusable input, 3 findings. A record that could not be
PREPARED is a finding -- somebody has to look at why. A record prepared successfully is NOT: it is
ordinary work waiting on the paste that is the design, and making it a finding would leave this
control permanently red for doing its job, which this estate has now recorded four times.
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
from work_carrier.prepare import Prepared, Refused, prepare

EXIT_OK = 0
EXIT_TOOL_FAILURE = 1
EXIT_UNUSABLE = 2
EXIT_FINDINGS = 3

DEFAULT_CHECKOUT_ROOT = "~/Projects"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="work-carrier",
        description=(
            "Prepare an orchestrator package intake for every approved change-manager work "
            "proposal. Reads only; the intake itself is a human act."
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
    return parser.parse_args(argv)


def _source(args: argparse.Namespace) -> WorkRecordSource | None:
    token = os.environ.get("CHANGE_MANAGER_TOKEN", "")
    if not token:
        return None
    return HttpWorkRecordSource(base_url=args.change_manager_url, token=token)


def run(
    argv: list[str],
    *,
    source: WorkRecordSource | None = None,
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

    for item in prepared:
        print(
            f"[PREPARED] change record {item.record.change_record_id}: "
            f"{item.record.package_id} revision {item.record.package_revision} "
            f"(approved by {item.record.decided_by or 'unrecorded'})",
            file=out,
        )
        print(f"           package: {item.package_path}", file=out)
        print(
            "           paste into https://sds.alobar.net/review/intakes/new "
            "(the form supplies its own idempotency key):",
            file=out,
        )
        print(json.dumps(item.payload, sort_keys=True, default=str), file=out)
    for item in refused:
        print(
            f"[REFUSED]  change record {item.record.change_record_id}: "
            f"{item.reason} — {item.detail}",
            file=out,
        )

    print(
        f"\n{len(records)} approved, {len(prepared)} prepared, {len(refused)} refused.",
        file=out,
    )
    return EXIT_FINDINGS if refused else EXIT_OK


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
