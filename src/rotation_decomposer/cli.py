"""One rotation-decomposer pass: propose the decomposition of each carried rotation that has none.

For each approved `work` record naming a standing rotation package, the orchestrator says what work
it caused. A registered revision with no units and no decomposition proposal gets one, generated
from its intake (`decompose.py`) and proposed as SYSTEM. A person approves it on `/review`; this
lane never decides one, and never proposes over a proposal a person rejected or sent back.

EXIT CODES:
  0  every carried rotation has a decomposition, proposed now or before, or none was waiting.
  1  the tool itself failed (a missing credential, an unusable URL).
  2  something was found that a person decides: a package that can't be decomposed by machine, a
     proposal the orchestrator refused, or one a person rejected, sent back or superseded.
  3  a record or the registry could not be read, so the answer is missing rather than clean.
3 outranks 2: an incomplete pass cannot claim it found everything there was to find.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from rotation_decomposer.alerts import STATE_DIR_ENV, TOKEN_ENV, sync_alerts
from rotation_decomposer.client import (
    CallError,
    ChangeManagerReader,
    Orchestrator,
    UnusableEndpointError,
)
from rotation_decomposer.decompose import DecomposeError, credential_of, decomposition
from rotation_decomposer.outcome import Outcome
from rotation_worker.contract import ContractError
from rotation_worker.destinations import DestinationError
from rotation_worker.git_mirror import MirrorError, RepoMirror, default_mirror_root
from rotation_worker.grants import GrantUnread, rotation_granted
from rotation_worker.registry import RegistryEntry, RegistryError, credentials

FOUND, INCOMPLETE = 2, 3
CHANGE_MANAGER_URL: Final = "https://change-mgr.alobar.net"
ORCHESTRATOR_URL: Final = "https://sds.alobar.net"
# A proposal in either state is a person's to decide or already decided: never proposed over.
DECIDING: Final = frozenset({"proposed", "approved"})


def run_pass(
    records: Sequence[dict[str, Any]],
    orchestrator: Orchestrator,
    registry: Callable[[], dict[str, RegistryEntry]],
    *,
    dry_run: bool = False,
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    members = _once(orchestrator.members)
    for record in records:
        package_id = str(record.get("package_id"))
        credential = credential_of(package_id)
        if credential is None or record.get("status") != "approved":
            continue
        subject = f"record {record.get('id')} ({package_id} rev {record.get('package_revision')})"
        try:
            work = orchestrator.work(int(record["id"]))
            started = {str(unit.get("revision_id")) for unit in work.get("units") or []}
            for revision_id in work.get("revision_ids") or []:
                if str(revision_id) in started:
                    continue
                outcome = _revision(
                    subject,
                    str(revision_id),
                    int(record["package_revision"]),
                    credential,
                    orchestrator,
                    registry,
                    members,
                    dry_run,
                )
                if outcome is not None:
                    outcomes.append(outcome)
        except (
            CallError,
            GrantUnread,
            RegistryError,
            MirrorError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            # One record's read failing never stops the pass.
            outcomes.append(Outcome(subject, "unread", str(error)))
    return outcomes


def _existing(subject: str, states: list[str]) -> Outcome | None:
    """What a revision's proposals already say, or ``None`` when it has none."""
    if "proposed" in states:
        return Outcome(subject, "awaiting-approval", "a proposal waits for a person on /review")
    if "approved" in states:
        # Approval creates the units in its own transaction, so an approved proposal with none is
        # one a person superseded (ADR-0052): theirs to replace, never this lane's.
        return Outcome(
            subject, "superseded", "its approved decomposition was superseded; a person replaces it"
        )
    if states:
        return Outcome(
            subject,
            "declined",
            f"a person {states[-1].replace('_', ' ')} its proposal; it is never proposed again "
            "by machine",
        )
    return None


def _revision(
    subject: str,
    revision_id: str,
    named: int,
    credential: str,
    orchestrator: Orchestrator,
    registry: Callable[[], dict[str, RegistryEntry]],
    members: Callable[[], Any],
    dry_run: bool,
) -> Outcome | None:
    """What becomes of one registered revision with no units; `None` for one the record doesn't
    name, which an approval would strand (the executor runs only the package's latest)."""
    existing = _existing(
        subject, [str(p.get("state")) for p in orchestrator.proposals(revision_id)]
    )
    if existing is not None:
        return existing
    intake = orchestrator.intake(revision_id)
    if intake.get("revision") != named:
        return None
    entry = registry().get(credential)
    if entry is None:
        return Outcome(subject, "refused", f"no registry row names {credential}")
    # Asked only here, once a decomposition would be proposed; `GrantUnread` and `CallError` are
    # the caller's `unread`.
    if not rotation_granted(members(), entry.repository):
        return Outcome(
            subject,
            "refused",
            f"the member record does not grant credential_rotation on {entry.repository}",
        )
    try:
        body = decomposition(intake, entry)
    except (DecomposeError, ContractError, DestinationError, RegistryError) as error:
        return Outcome(subject, "refused", str(error))
    if dry_run:
        return Outcome(subject, "would-propose", f"{len(body['proposed_units'])} units")
    try:
        answer = orchestrator.propose(
            revision_id,
            {
                **body,
                "idempotency_key": f"rotation-decomposer:{revision_id}",
                "expected_version": 0,
            },
        )
    except CallError as error:
        if error.status is None or error.status >= 500:
            raise
        # The orchestrator read the proposal and refused it, for a reason a person resolves.
        return Outcome(subject, "refused", str(error))
    if answer.get("state") == "approved":
        # A rotation's decomposition is approved by policy as it is proposed (ADR-0055
        # amendment 16): its units exist now, and the executor takes them.
        return Outcome(subject, "approved", f"proposal {answer.get('id')} approved by policy")
    return Outcome(
        subject, "proposed", f"proposal {answer.get('id')} waits for a person on /review"
    )


def exit_code(outcomes: Sequence[Outcome]) -> int:
    results = {outcome.result for outcome in outcomes}
    if "unread" in results:
        return INCOMPLETE
    if results & {"declined", "refused", "superseded"}:
        return FOUND
    return 0


def _once[T](read: Callable[[], T]) -> Callable[[], T]:
    """`read`, asked at most once per pass, and only when a revision needs a decomposition."""
    found: list[T] = []

    def cached() -> T:
        if not found:
            found.append(read())
        return found[0]

    return cached


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rotation-decomposer",
        description="Propose the decomposition of each carried rotation that has none.",
    )
    parser.add_argument("--dry-run", action="store_true", help="list what would be proposed")
    args = parser.parse_args(argv)
    token = os.environ.get("ORCHESTRATOR_API_TOKEN", "")
    key_id = os.environ.get("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "")
    change_manager_token = os.environ.get("CHANGE_MANAGER_TOKEN", "")
    alert_token = os.environ.get(TOKEN_ENV, "")
    if not token or not key_id or not change_manager_token:
        print(
            "FATAL: ORCHESTRATOR_API_TOKEN, its key id and CHANGE_MANAGER_TOKEN are required",
            file=sys.stderr,
        )
        return 1
    if not args.dry_run and not alert_token:
        print(f"FATAL: {TOKEN_ENV} is required unless --dry-run", file=sys.stderr)
        return 1
    try:
        reader = ChangeManagerReader(
            base_url=os.environ.get("CHANGE_MANAGER_URL", CHANGE_MANAGER_URL),
            token=change_manager_token,
        )
        orchestrator = Orchestrator(
            base_url=os.environ.get("ORCHESTRATOR_API_URL", ORCHESTRATOR_URL),
            credential_key_id=key_id,
            token=token,
        )
    except UnusableEndpointError as error:
        print(f"FATAL: {error}", file=sys.stderr)
        return 1
    mirror = RepoMirror(default_mirror_root("rotation-decomposer"))
    try:
        records = reader.work_records()
        outcomes = run_pass(
            records, orchestrator, _once(lambda: credentials(mirror)), dry_run=args.dry_run
        )
    except CallError as error:
        print(f"could not list the work records: {error}", file=sys.stderr)
        return INCOMPLETE
    finally:
        reader.close()
        orchestrator.close()
    for outcome in outcomes:
        print(f"{outcome.subject:<60}  {outcome.result:<17}  {outcome.detail}".rstrip())
    print(f"\n{len(outcomes)} carried rotation revision(s) without units")
    if not args.dry_run:
        _tell_a_person(outcomes, alert_token)
    return exit_code(outcomes)


def _state_dir() -> Path:
    configured = os.environ.get(STATE_DIR_ENV)
    if configured:
        return Path(configured)
    return Path.home() / "Library" / "Application Support" / "sds" / "rotation-decomposer"


def _tell_a_person(outcomes: Sequence[Outcome], token: str) -> None:
    """Sync the pass's alerts. A failure is reported and never changes the exit code, which
    describes the rotations and not alert-hub."""
    reason = sync_alerts(outcomes, token=token, state_dir=_state_dir(), now=datetime.now(UTC))
    if reason is not None:
        print(f"alert-hub was not brought up to date: {reason}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
