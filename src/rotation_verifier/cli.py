"""One rotation-verifier pass: verify every submitted rotation unit.

A unit is a rotation unit when its envelope names a `rotation_unit`; any other submitted unit is
left alone, because its verification is driven by whoever runs its own lane. The verifier, not
this lane, decides each criterion: a clean unit completes, and anything it can't prove goes to
`awaiting_review` for a person.

EXIT CODES:
  0  every submitted rotation unit was verified and completed, or none was submitted.
  1  the tool itself failed (a missing credential, an unusable URL).
  2  something was found: a verified unit went to a person.
  3  a unit could not be read or verified, so the answer is missing rather than clean.
3 outranks 2: an incomplete pass cannot claim it found everything there was to find.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rotation_verifier.client import UnusableEndpointError, VerifierCallError, VerifierClient

ROTATION_UNIT = "rotation_unit"
FOUND, INCOMPLETE = 2, 3


@dataclass(frozen=True)
class Outcome:
    unit_key: str
    result: str  # completed, awaiting_review, would-verify or refused
    detail: str = ""


def _is_rotation(unit: dict[str, Any]) -> bool:
    authority = unit.get("authority")
    constraints = authority.get("constraints") if isinstance(authority, dict) else None
    return isinstance(constraints, dict) and isinstance(constraints.get(ROTATION_UNIT), str)


def run_pass(client: VerifierClient, *, dry_run: bool = False) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for row in client.pending():
        key = str(row.get("unit_key"))
        unit_id = str(row.get("unit_id"))
        try:
            unit = client.unit(unit_id)
            if not _is_rotation(unit):
                continue
            if dry_run:
                outcomes.append(Outcome(key, "would-verify"))
                continue
            version = int(unit["version"])
            answer = client.verify(unit_id, version, f"rotation-verifier-{unit_id}-v{version}")
        except (VerifierCallError, KeyError, TypeError, ValueError) as error:
            # One unit's refusal (its version moved, say) never stops the pass.
            outcomes.append(Outcome(key, "refused", str(error)))
            continue
        state = str(answer.get("state"))
        outcomes.append(Outcome(key, "completed" if state == "completed" else state))
    return outcomes


def exit_code(outcomes: Sequence[Outcome]) -> int:
    results = {outcome.result for outcome in outcomes}
    if "refused" in results:
        return INCOMPLETE
    if results - {"completed", "would-verify"}:
        return FOUND
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rotation-verifier", description="Verify every submitted rotation unit."
    )
    parser.add_argument("--dry-run", action="store_true", help="list what would be verified")
    args = parser.parse_args(argv)
    token = os.environ.get("ORCHESTRATOR_API_TOKEN", "")
    key_id = os.environ.get("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "")
    if not token or not key_id:
        print("FATAL: ORCHESTRATOR_API_TOKEN and its key id are required", file=sys.stderr)
        return 1
    try:
        client = VerifierClient(
            base_url=os.environ.get("ORCHESTRATOR_API_URL", "https://sds.alobar.net"),
            credential_key_id=key_id,
            token=token,
        )
    except UnusableEndpointError as error:
        print(f"FATAL: {error}", file=sys.stderr)
        return 1
    try:
        outcomes = run_pass(client, dry_run=args.dry_run)
    except VerifierCallError as error:
        print(f"could not list submitted units: {error}", file=sys.stderr)
        return INCOMPLETE
    finally:
        client.close()
    for outcome in outcomes:
        print(f"{outcome.unit_key:<40}  {outcome.result:<16}  {outcome.detail}".rstrip())
    print(f"\n{len(outcomes)} rotation unit(s) awaiting the verifier")
    return exit_code(outcomes)


if __name__ == "__main__":
    raise SystemExit(main())
