"""One verifier pass: verify every submitted factory unit, rotation and dependency-bump units too.

A unit is a rotation unit when its envelope names a `rotation_unit`, and a bump unit when its
`change_class` is `dependency-update`. Any other is an app-change unit, taken once it has a dispatch
and an armed head. A session unit has neither, and `post_deploy_verification` units are
`image_release`'s, so both are left alone. The verifier, not this lane, decides each criterion: a
clean unit completes, and anything it can't prove goes to `awaiting_review` for a person.

Only the newest named-check row for a criterion counts, so for each criterion the lane files the
repository's required checks in sorted order and stops at the first that did not succeed: the newest
row is then a failure exactly when one failed. A bump has one criterion. An app-change unit has its
`automated_check_ac_ids`, and each gets every required check, because nothing maps a criterion to a
check. A check still running is `waiting`; the next pass files from the start again, under keys of
its own, so it observes every check afresh.

An app-change unit is held while the member record doesn't grant its repository, which alerts, and
while the record can't be read, which is no withdrawal and alerts nothing. Nothing filed means a
person decides: a unit with no `automated_check` criterion goes straight to `/verify`, and so does
one whose repository requires no check, where the criterion falls to a person. Rotation units have
no repository, and landing refuses a bump with no grant, so neither reads the record.

A unit the lane could not take to an answer is a keyed alert (`alerts.py`), sent after every unit
has had its turn, so a refused unit or an alert-hub failure never stops a rotation.

EXIT CODES:
  0  every submitted unit was verified and completed or is waiting on a check, or none was
     submitted.
  1  the tool itself failed (a missing credential, an unusable URL).
  2  something was found: a verified unit went to a person, or a unit raised an alert (it could
     not be read or verified, a bump's repository requires no check, its required checks could not
     be read, or its grant is gone), or the member record could not be read. The launcher
     declares 2 as this lane's finding code, so one cause never raises both an alert and `/fail`.
  3  the queue could not be read and every key was held, or alert-hub could not be told, so a
     finding may have reached nobody.
3 outranks 2: an incomplete pass cannot claim it told a person everything it found.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

from rotation_verifier.alerts import (
    CHECKS_UNREADABLE,
    NO_REQUIRED_CHECK,
    REFUSED,
    TOKEN_ENV,
    UNREAD,
    Outcome,
    state_directory,
    sync_alerts,
)
from rotation_verifier.client import (
    CheckNotConcluded,
    UnusableEndpointError,
    VerifierCallError,
    VerifierClient,
)
from rotation_verifier.required_checks import (
    NoRequiredChecks,
    RequiredChecksUnreadable,
    required_checks,
)
from sds_members.client import MembersUnreadable
from sds_members.populations import granted, service_for_change_class

ORCHESTRATOR_URL = "https://sds.alobar.net"
ROTATION_UNIT = "rotation_unit"
BUMP_CHANGE_CLASS = "dependency-update"
# The criterion a bump package's generator writes: its one `automated_check`.
BUMP_AC_ID = "AC-001"
# `image_release` owns these units, and `/verify` would refuse them from this lane.
POST_DEPLOY_CAPABILITY = "post_deploy_verification"
# Served by `GET /work-units/{id}`; a server without it serves no criterion to file under.
AUTOMATED_CHECK_IDS = "automated_check_ac_ids"
WAITING = "waiting"
FOUND, INCOMPLETE = 2, 3


def _is_rotation(unit: dict[str, Any]) -> bool:
    authority = unit.get("authority")
    constraints = authority.get("constraints") if isinstance(authority, dict) else None
    return isinstance(constraints, dict) and isinstance(constraints.get(ROTATION_UNIT), str)


def _is_bump(unit: dict[str, Any]) -> bool:
    authority = unit.get("authority")
    return isinstance(authority, dict) and authority.get("change_class") == BUMP_CHANGE_CLASS


def _is_app_change(unit: dict[str, Any]) -> bool:
    """A factory unit that ran: a session unit has no dispatch, and may have no head."""
    return (
        unit.get("required_capability") != POST_DEPLOY_CAPABILITY
        and bool(unit.get("latest_dispatch_id"))
        and bool(unit.get("armed_head_sha"))
    )


def _check_key(pass_id: str, unit_id: str, ac_id: str, head_sha: str, check_name: str) -> str:
    """Distinct per pass, criterion, check, head and unit.

    The orchestrator replays a key BEFORE it observes GitHub, so a key shared across passes would
    serve a check's old success after the check was re-run red. A fresh `pass_id` makes every pass
    observe afresh; a retry inside one pass still replays. A key shared across criteria would be
    refused as another operation's.
    """
    claim = f"{pass_id}\0{ac_id}\0{head_sha}\0{check_name}"
    digest = hashlib.sha256(claim.encode()).hexdigest()[:32]
    return f"rotation-verifier-check-{unit_id}-{digest}"


def _file_checks(
    client: VerifierClient,
    pass_id: str,
    unit_id: str,
    unit: dict[str, Any],
    criteria: Sequence[str],
    names: Sequence[str],
) -> None:
    """File each required check under each criterion in sorted order; each criterion stops at the
    first that did not succeed, so every criterion's newest row states the unit's answer.

    Raises `CheckNotConcluded` for a check still running and `KeyError` for a unit that lacks what
    filing needs; both are the caller's to classify.
    """
    repository = str(unit["authority"]["constraints"]["target_repository"]).strip()
    pr_number, head_sha = unit["pr_number"], unit["armed_head_sha"]
    dispatch_id = unit["latest_dispatch_id"]
    if not (isinstance(pr_number, int) and head_sha and dispatch_id):
        raise KeyError("the unit has no dispatch, pull request or armed head yet")
    for ac_id in criteria:
        for name in sorted(names):
            filed = client.named_check(
                unit_id,
                {
                    "work_package_revision_id": unit["work_package_revision_id"],
                    "ac_id": ac_id,
                    "dispatch_id": dispatch_id,
                    "repository": repository,
                    "pr_number": pr_number,
                    "pr_url": f"https://github.com/{repository}/pull/{pr_number}",
                    "head_sha": head_sha,
                    "check_name": name,
                    "expected_conclusion": "success",
                    "expected_version": int(unit["version"]),
                    "idempotency_key": _check_key(pass_id, unit_id, ac_id, head_sha, name),
                },
            )
            payload = filed.get("payload")
            if not isinstance(payload, dict) or payload.get("conclusion") != "success":
                break


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
        except (VerifierCallError, KeyError, TypeError, ValueError) as error:
            # Unread, so not known to be ours: a failed read holds the key and fires nothing.
            outcomes.append(Outcome(key, UNREAD, str(error), unit_id))
            continue
        try:
            bump, rotation = _is_bump(unit), _is_rotation(unit)
            if not (bump or rotation or _is_app_change(unit)):
                continue
            if dry_run:
                outcomes.append(Outcome(key, "would-verify"))
                continue
            early = None
            if bump:
                early = _file_bump_checks(client, pass_id, unit_id, unit, read_required_checks)
            elif not rotation:
                early = _file_app_change_checks(
                    client, pass_id, unit_id, unit, read_required_checks
                )
            if early is not None:
                outcomes.append(Outcome(key, *early, unit_id=unit_id))
                continue
            version = int(unit["version"])
            answer = client.verify(unit_id, version, f"rotation-verifier-{unit_id}-v{version}")
        except (VerifierCallError, KeyError, TypeError, ValueError) as error:
            # One unit's refusal (its version moved, say) never stops the pass.
            outcomes.append(Outcome(key, REFUSED, str(error), unit_id))
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
    repository = str(unit["authority"]["constraints"]["target_repository"]).strip()
    try:
        names = _required_names(read_required_checks, repository)
    except RequiredChecksUnreadable as error:
        return (CHECKS_UNREADABLE, str(error))
    if not names:
        return (NO_REQUIRED_CHECK, repository)
    return _file_all(client, pass_id, unit_id, unit, (BUMP_AC_ID,), names)


def _file_app_change_checks(
    client: VerifierClient,
    pass_id: str,
    unit_id: str,
    unit: dict[str, Any],
    read_required_checks: Callable[[str], tuple[str, ...]] | None,
) -> tuple[str, str] | None:
    """As `_file_bump_checks`, past the grant. With no check to file, `/verify` sends the unit to a
    person: a repository requiring nothing never passes by default."""
    repository = str(unit["authority"]["constraints"]["target_repository"]).strip()
    held = _grant_hold(client, unit, repository)
    if held is not None:
        return held
    criteria = tuple(unit.get(AUTOMATED_CHECK_IDS) or ())
    if not criteria:
        return None
    try:
        names = _required_names(read_required_checks, repository)
    except NoRequiredChecks:
        return None
    except RequiredChecksUnreadable as error:
        return (CHECKS_UNREADABLE, str(error))
    return _file_all(client, pass_id, unit_id, unit, criteria, names) if names else None


def _required_names(
    read_required_checks: Callable[[str], tuple[str, ...]] | None, repository: str
) -> tuple[str, ...]:
    if read_required_checks is None:
        raise VerifierCallError("no GitHub token to read the required checks with")
    return read_required_checks(repository)


def _file_all(
    client: VerifierClient,
    pass_id: str,
    unit_id: str,
    unit: dict[str, Any],
    criteria: Sequence[str],
    names: Sequence[str],
) -> tuple[str, str] | None:
    try:
        _file_checks(client, pass_id, unit_id, unit, criteria, names)
    except CheckNotConcluded:
        return (WAITING, "a required check has not concluded")
    return None


def _grant_hold(
    client: VerifierClient, unit: dict[str, Any], repository: str
) -> tuple[str, str] | None:
    """Why the member record holds this unit, or None. An unread record says nothing of a grant, so
    it holds the unit without alerting, and an empty one is unread, never a withdrawal."""
    try:
        members = client.members()
    except (VerifierCallError, MembersUnreadable) as error:
        return (UNREAD, str(error))
    if not members:
        return (UNREAD, "the member record has no rows")
    service = service_for_change_class(str(unit["authority"].get("change_class")))
    if repository.lower() in {name.lower() for name in granted(members, service)}:
        return None
    return (REFUSED, f"the member record does not grant {service} on {repository}")


def exit_code(outcomes: Sequence[Outcome]) -> int:
    results = {outcome.result for outcome in outcomes}
    return FOUND if results - {"completed", "would-verify", WAITING} else 0


def run(
    client: VerifierClient,
    *,
    dry_run: bool,
    read_required_checks: Callable[[str], tuple[str, ...]] | None,
    alert_token: str,
    state_dir: Path,
    hub_transport: httpx.BaseTransport | None = None,
) -> int:
    """One pass, then what it found to alert-hub. A dry run tells nobody."""
    try:
        outcomes = run_pass(client, dry_run=dry_run, read_required_checks=read_required_checks)
    except VerifierCallError as error:
        print(f"could not list submitted units: {error}", file=sys.stderr)
        return INCOMPLETE
    for outcome in outcomes:
        print(f"{outcome.unit_key:<40}  {outcome.result:<16}  {outcome.detail}".rstrip())
    print(f"\n{len(outcomes)} unit(s) awaiting the verifier")
    if not dry_run:
        problem = sync_alerts(
            outcomes, token=alert_token, state_dir=state_dir, transport=hub_transport
        )
        if problem is not None:
            print(f"alert-hub could not be told: {problem}", file=sys.stderr)
            return INCOMPLETE
    return exit_code(outcomes)


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
        return run(
            client,
            dry_run=args.dry_run,
            read_required_checks=(
                (lambda repository: required_checks(repository, token=github_token))
                if github_token
                else None
            ),
            alert_token=os.environ.get(TOKEN_ENV, ""),
            state_dir=state_directory(os.environ, Path(os.environ.get("HOME", str(Path.home())))),
        )
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
