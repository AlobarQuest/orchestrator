"""One rotation-verifier pass: verify every submitted rotation unit and dependency-bump unit.

A unit is a rotation unit when its envelope names a `rotation_unit`, and a bump unit when its
`change_class` is `dependency-update`; any other submitted unit is left alone, because its
verification is driven by whoever runs its own lane. The verifier, not this lane, decides each
criterion: a clean unit completes, and anything it can't prove goes to `awaiting_review` for a
person.

A bump unit has one criterion, and only the newest named-check row for it counts, so the lane files
the repository's required checks in sorted order and stops at the first that did not succeed: the
newest row is then a failure exactly when one failed. A check still running is `waiting`; the next
pass files from the start again, under keys of its own, so it observes every check afresh.

EXIT CODES:
  0  every submitted unit was verified and completed or is waiting on a check, or none was
     submitted.
  1  the tool itself failed (a missing credential, an unusable URL).
  2  something was found: a verified unit went to a person, a bump unit's repository requires no
     check, or a repository's required checks could not be read.
  3  a unit could not be read or verified, so the answer is missing rather than clean.
3 outranks 2: an incomplete pass cannot claim it found everything there was to find.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from rotation_verifier.client import (
    CheckNotConcluded,
    UnusableEndpointError,
    VerifierCallError,
    VerifierClient,
)
from rotation_verifier.required_checks import RequiredChecksUnreadable, required_checks

ORCHESTRATOR_URL = "https://sds.alobar.net"
ROTATION_UNIT = "rotation_unit"
BUMP_CHANGE_CLASS = "dependency-update"
# The criterion a bump package's generator writes: its one `automated_check`.
BUMP_AC_ID = "AC-001"
WAITING = "waiting"
NO_REQUIRED_CHECK = "bump_unit_no_required_check"
CHECKS_UNREADABLE = "required_checks_unreadable"
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


def _is_bump(unit: dict[str, Any]) -> bool:
    authority = unit.get("authority")
    return isinstance(authority, dict) and authority.get("change_class") == BUMP_CHANGE_CLASS


def _check_key(pass_id: str, unit_id: str, head_sha: str, check_name: str) -> str:
    """Distinct per pass, check, head and unit.

    The orchestrator replays a key BEFORE it observes GitHub, so a key shared across passes would
    serve a check's old success after the check was re-run red. A fresh `pass_id` makes every pass
    observe afresh; a retry inside one pass still replays.
    """
    digest = hashlib.sha256(f"{pass_id}\0{head_sha}\0{check_name}".encode()).hexdigest()[:32]
    return f"rotation-verifier-check-{unit_id}-{digest}"


def _file_checks(
    client: VerifierClient,
    pass_id: str,
    unit_id: str,
    unit: dict[str, Any],
    names: Sequence[str],
) -> None:
    """File each required check in sorted order, stopping at the first that did not succeed.

    Raises `CheckNotConcluded` for a check still running and `KeyError` for a unit that lacks what
    filing needs; both are the caller's to classify.
    """
    repository = str(unit["authority"]["constraints"]["target_repository"]).strip()
    pr_number, head_sha = unit["pr_number"], unit["armed_head_sha"]
    dispatch_id = unit["latest_dispatch_id"]
    if not (isinstance(pr_number, int) and head_sha and dispatch_id):
        raise KeyError("the unit has no dispatch, pull request or armed head yet")
    for name in sorted(names):
        filed = client.named_check(
            unit_id,
            {
                "work_package_revision_id": unit["work_package_revision_id"],
                "ac_id": BUMP_AC_ID,
                "dispatch_id": dispatch_id,
                "repository": repository,
                "pr_number": pr_number,
                "pr_url": f"https://github.com/{repository}/pull/{pr_number}",
                "head_sha": head_sha,
                "check_name": name,
                "expected_conclusion": "success",
                "expected_version": int(unit["version"]),
                "idempotency_key": _check_key(pass_id, unit_id, head_sha, name),
            },
        )
        payload = filed.get("payload")
        if not isinstance(payload, dict) or payload.get("conclusion") != "success":
            return


def run_pass(
    client: VerifierClient,
    *,
    dry_run: bool = False,
    read_required_checks: Callable[[str], tuple[str, ...]] | None = None,
    pass_id: str | None = None,
) -> list[Outcome]:
    pass_id = pass_id or uuid.uuid4().hex
    outcomes: list[Outcome] = []
    for row in client.pending():
        key = str(row.get("unit_key"))
        unit_id = str(row.get("unit_id"))
        try:
            unit = client.unit(unit_id)
            bump = _is_bump(unit)
            if not (bump or _is_rotation(unit)):
                continue
            if dry_run:
                outcomes.append(Outcome(key, "would-verify"))
                continue
            if bump:
                early = _file_bump_checks(client, pass_id, unit_id, unit, read_required_checks)
                if early is not None:
                    outcomes.append(Outcome(key, *early))
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


def _file_bump_checks(
    client: VerifierClient,
    pass_id: str,
    unit_id: str,
    unit: dict[str, Any],
    read_required_checks: Callable[[str], tuple[str, ...]] | None,
) -> tuple[str, str] | None:
    """File a bump unit's checks. None means go on to `/verify`; otherwise the unit's outcome."""
    if read_required_checks is None:
        raise VerifierCallError("no GitHub token to read the required checks with")
    repository = str(unit["authority"]["constraints"]["target_repository"]).strip()
    try:
        names = read_required_checks(repository)
    except RequiredChecksUnreadable as error:
        return (CHECKS_UNREADABLE, str(error))
    if not names:
        return (NO_REQUIRED_CHECK, repository)
    try:
        _file_checks(client, pass_id, unit_id, unit, names)
    except CheckNotConcluded:
        return (WAITING, "a required check has not concluded")
    return None


def exit_code(outcomes: Sequence[Outcome]) -> int:
    results = {outcome.result for outcome in outcomes}
    if "refused" in results:
        return INCOMPLETE
    if results - {"completed", "would-verify", WAITING}:
        return FOUND
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rotation-verifier", description="Verify every submitted rotation unit."
    )
    parser.add_argument("--dry-run", action="store_true", help="list what would be verified")
    args = parser.parse_args(argv)
    github_token = os.environ.get("ROTATION_VERIFIER_GITHUB_TOKEN", "")
    token = os.environ.get("ORCHESTRATOR_API_TOKEN", "")
    key_id = os.environ.get("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "")
    if not token or not key_id:
        print("FATAL: ORCHESTRATOR_API_TOKEN and its key id are required", file=sys.stderr)
        return 1
    try:
        client = VerifierClient(
            base_url=os.environ.get("ORCHESTRATOR_API_URL", ORCHESTRATOR_URL),
            credential_key_id=key_id,
            token=token,
        )
    except UnusableEndpointError as error:
        print(f"FATAL: {error}", file=sys.stderr)
        return 1
    try:
        outcomes = run_pass(
            client,
            dry_run=args.dry_run,
            read_required_checks=(
                (lambda repository: required_checks(repository, token=github_token))
                if github_token
                else None
            ),
        )
    except VerifierCallError as error:
        print(f"could not list submitted units: {error}", file=sys.stderr)
        return INCOMPLETE
    finally:
        client.close()
    for outcome in outcomes:
        print(f"{outcome.unit_key:<40}  {outcome.result:<16}  {outcome.detail}".rstrip())
    print(f"\n{len(outcomes)} unit(s) awaiting the verifier")
    return exit_code(outcomes)


if __name__ == "__main__":
    raise SystemExit(main())
