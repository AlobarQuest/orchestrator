"""Turn a credential that is due for rotation into proposed work (ADR-0054 amendment 1).

**WHAT THIS IS THE HEAD OF.** infraops' 03:00 scan knows when a registry credential is due -- by
age, by a recorded exposure, or by a `rotate_requested` date -- and until now it filed that to
change-manager as a `security` record outside the SDS. Amendment 1 has the scan only DETECT, and
this program carry the detection into the SDS the way `bump_proposer` carries a refused bump:

    infraops reports a credential due (`security-drift-cli cred-findings`)
      -> HERE, PASS 1: FILE THE OBSERVATION, revise the credential's standing package
         `rotation-<credential_id>` so its new revision carries this occurrence and the
         registry's destinations (or generate the package, if it has none), take it to
         `ready_for_review`, publish the commit -- and STOP
      -> Devon approves that revision by name (no approval policy grants this profile)
      -> HERE, A LATER PASS: file the observation (it replays), propose a `work` record naming
         the approved revision and that observation
      -> Devon approves the record in change-manager
      -> the carry registers an intake -> decomposition -> units, every gate clicked by Devon

**WHY IT STOPS BETWEEN THE TWO.** A change record names a package REVISION, and the carry refuses a
record whose revision is not approved. `bump_proposer` approves its own revision by policy and so
proposes in the same pass; this program may not approve, so a pass that revises proposes nothing,
and the pass after Devon's approval proposes. The order of acts is the same as `bump_proposer`'s
-- observe, revise, publish, propose -- with the approval removed from the middle and handed to a
person.

**AN UNAPPROVED REVISION FOR A DIFFERENT ROTATION IS NOT REVISED OVER.** When the tip is in front
of Devon for one occurrence and infraops now reports another (an exposure arriving while an age
rotation waits), revising would replace what he is reviewing with something he has not seen. That
is reported as `stacked`, a finding, and left for a person.

**IT CANNOT APPROVE ANYTHING.** Not the revision -- no lifecycle call here names `approve` -- and
not the record: its change-manager bearer is propose-scoped and its client asserts a two-path
surface. Its orchestrator clients name one write, the observation ingest, and two reads, under the
OBSERVER bearer, which can neither transition a unit nor create work.

**IT WALKS ROTATION RECORDS, DUE OR NOT.** After the due walk, a writing pass reads the records
themselves, because a credential stops being due the moment the state says so, whether or not that
is true. An approved record of the tip with a `failed` or `cancelled` unit reports `stalled`.

**IT RECORDS A FINISHED ROTATION IN INFRAOPS.** Once the work watcher retires a record (`resolved`,
decided by `work-watcher`, which it does only when every unit completed), the pass runs
`security-drift-cli record-rotation` when that rotation is newer than infraops' `last_rotated`, so
infraops stops reporting the credential due. The date is the UTC day the record's last unit
completed: change-manager serves no date on a record, and the units' history can only run earlier
than the retirement, never later. The comparison is the whole of the idempotence, since infraops
accepts an earlier date. A record anyone else resolved records nothing, and is a finding while the
credential is due. An exposure records nothing either and is a finding: record-rotation doesn't
close it, and closing it is a person's statement that the old value is dead.

**A REPEAT PASS IS A REPLAY.** A package whose tip already carries the occurrence is not revised
again; the observation is content-addressed; change-manager answers 200 for an identical proposal.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, NamedTuple

from bump_proposer.change_manager import (
    DEFAULT_BASE_URL,
    ChangeManagerClient,
    ChangeManagerError,
    ProposalRefused,
)
from bump_proposer.orchestrator_client import (
    ObservationCredentialError,
    ObservationWriteError,
    OrchestratorClient,
    UnusableEndpointError,
    open_client,
)
from bump_proposer.standing import (
    PACKAGES_REPOSITORY,
    StandingError,
    checkout_root,
    require_clean,
    require_publishable,
    snapshot_hash,
)
from rotation_proposer.alerts import STATE_DIR_ENV, TOKEN_ENV, sync_alerts
from rotation_proposer.client import CallError, Orchestrator
from rotation_proposer.findings import (
    Due,
    FindingsError,
    Unrecognised,
    read_due,
    record_rotation,
)
from rotation_proposer.generate import GenerateError, render
from rotation_proposer.observation import ObservationUncomposable, rotation_observation
from rotation_proposer.outcome import FINDING_STATUSES, Outcome
from rotation_proposer.standing import (
    APPROVED,
    DRAFT,
    IN_REVIEW,
    REJECTED,
    REVISABLE,
    RotationPackage,
    commit,
    create,
    discover,
    to_review,
)
from rotation_worker.contract import ContractError
from rotation_worker.destinations import DestinationError
from rotation_worker.git_mirror import MirrorError, RepoMirror, default_mirror_root
from rotation_worker.registry import RegistryEntry, RegistryError, standing_credential
from rotation_worker.registry import credentials as registry_entries
from sds_members import populations
from sds_members.client import MembersUnreadable, read_members

EXIT_OK: Final = 0
EXIT_UNUSABLE: Final = 2
EXIT_FINDINGS: Final = 3

ACTOR: Final = "rotation-proposer"
RISK: Final = "caution"
# The class change-manager's work policy approves a rotation record on, and the carrier holds
# the package's profile to (`work_carrier.prepare.CREDENTIAL_ROTATION`).
CHANGE_CLASS: Final = "credential-rotation"
USER_AGENT: Final = "rotation-proposer/1 (+AlobarQuest/orchestrator)"
OBSERVER_KEY_ID: Final = "orchestrator-observer"
# The actor the work watcher retires a record as (`work_watcher.change_manager`), duplicated
# because this program imports only its own row; a test holds the two equal.
RETIREMENT_ACTOR: Final = "work-watcher"

# A unit state a rotation cannot finish from without a person (`WorkUnitState`, which this program
# may not import).
STALLED_STATES: Final = frozenset({"failed", "cancelled"})
COMPLETED_STATE: Final = "completed"
EXPOSURE_SUFFIX: Final = "-exposure"
# What the record walk turns into one credential's `error`.
_UNREAD: Final = (
    CallError,
    FindingsError,
    RegistryError,
    MirrorError,
    DestinationError,
    ContractError,
)

# What a pass does with one credential, decided from the package alone before anything is written.
_PROPOSE: Final = "propose"
_ADVANCE: Final = "advance"


def _decide(package: RotationPackage, due: Due, destinations: list[str]) -> tuple[str, str]:
    """`(action, detail)`: `propose`, `advance`, or a status to report with nothing written."""
    label = f"{package.package_id} rev {package.revision} ({package.state})"
    if package.carries(due.occurrence):
        if package.state == APPROVED and not package.lists(destinations):
            # Proposed as it stands, the executor would refuse its first unit: drift reaches Devon
            # as a revision to approve instead.
            return _ADVANCE, f"{label}: its destinations differ from the registry's"
        if package.state == APPROVED:
            return _PROPOSE, label
        if package.state == DRAFT:
            return _ADVANCE, label
        if package.state in IN_REVIEW:
            return (
                "awaiting-approval",
                f"{label} carries {due.occurrence}; a named human approves it before "
                "anything is proposed",
            )
        if package.state == REJECTED:
            return "declined", f"{label}: the revision carrying {due.occurrence} was rejected"
        return (
            "unexpected-state",
            f"{label} carries {due.occurrence} in a state this lane never writes",
        )
    if package.state in REVISABLE:
        return _ADVANCE, label
    if package.state in IN_REVIEW:
        return (
            "stacked",
            f"{label} carries {package.occurrence or 'nothing'} and awaits approval; "
            f"{due.occurrence} is now due -- a person decides which rotation the package carries",
        )
    return "unexpected-state", f"{label} is in a state this lane does not revise from"


def _reasoning(package: RotationPackage) -> str:
    """Why this work exists -- a FROZEN string, like every asserted field on a record.

    change-manager refuses a different assertion about the same revision with a terminal 409, so
    this names only what the REVISION fixes: the credential and the occurrence. Not the class, not
    the age, nothing dated by the pass.
    """
    return (
        f"infraops' credential registry reports {package.credential_id} due for rotation "
        f"(occurrence {package.occurrence}). Revision {package.revision} of the standing package "
        f"{package.package_id} carries this rotation, approved by a named human because no "
        f"approval policy grants rotation revisions (ADR-0054 amendment 1). That approval is the "
        f"decision to rotate the credential: change-manager's work policy approves this record "
        f"and the orchestrator approves the derived decomposition (ADR-0055 amendment 16)."
    )


def _proposal(package: RotationPackage, observation_id: str) -> dict[str, Any]:
    """The record's facts: `bump_proposer.cli.WORK_RECORD_FIELDS` and the change class, which a
    test holds it to. A rotation record names no target repository."""
    return {
        "package_id": package.package_id,
        "package_revision": package.revision,
        "package_source_repository": PACKAGES_REPOSITORY,
        "risk": RISK,
        "reasoning": _reasoning(package),
        "actor": ACTOR,
        "originating_observation_id": observation_id,
        "change_class": CHANGE_CLASS,
    }


def _superseded(records: list[dict[str, Any]], package: RotationPackage) -> list[Outcome]:
    """Open records for an EARLIER revision of this package, which nothing can carry any more."""
    stranded = []
    for row in records:
        revision = row.get("package_revision")
        if row.get("package_id") != package.package_id or not isinstance(revision, int):
            continue
        if revision >= package.revision or row.get("status") in {"resolved", "wontfix"}:
            continue
        stranded.append(
            Outcome(
                package.credential_id,
                "superseded",
                f"record {row.get('id')} names {package.package_id} revision {revision}, which is "
                "no longer the revision on disk",
            )
        )
    return stranded


def _recorded(records: list[dict[str, Any]], package: RotationPackage) -> bool:
    """Whether any record, in any status, names this revision."""
    return any(
        row.get("package_id") == package.package_id
        and row.get("package_revision") == package.revision
        for row in records
    )


def _resolved(records: list[dict[str, Any]], package: RotationPackage) -> dict[str, Any] | None:
    """The record for this revision, if it has been resolved."""
    for row in records:
        if (
            row.get("package_id") == package.package_id
            and row.get("package_revision") == package.revision
            and row.get("status") == "resolved"
        ):
            return row
    return None


def _finished(due: Due, row: dict[str, Any]) -> Outcome:
    """What the due walk says of a due credential whose carrying revision has been resolved. The
    record walk does any recording, so nothing here writes."""
    subject = due.credential_id
    record = f"record {row.get('id')}"
    if row.get("decided_by") != RETIREMENT_ACTOR:
        # The work watcher retires a record only once its work completed; anyone else's
        # resolution says nothing about that, and the credential would stay due unreported.
        return Outcome(
            subject,
            "resolved-by-person",
            f"{record} was resolved by {row.get('decided_by')!r}, not {RETIREMENT_ACTOR}; "
            "nothing is recorded, and the credential stays due until a person decides",
        )
    if due.trigger == "exposure":
        # record-rotation doesn't close an exposure, so the credential would stay due and a
        # daily record would move lastRotated every day. Both acts are a person's, once.
        return Outcome(
            subject,
            "exposure-open",
            f"{record} was retired by {RETIREMENT_ACTOR}: the rotation completed. Exposure "
            f"{due.basis} stays open: once the old value is confirmed dead, run "
            f"security-drift-cli resolve-exposure and record-rotation for {subject}",
        )
    return Outcome(subject, "retired", f"{record} was retired by {RETIREMENT_ACTOR}")


def _act(
    package: RotationPackage,
    due: Due,
    entry: RegistryEntry,
    client: ChangeManagerClient | None,
    observer: OrchestratorClient | None,
    records: list[dict[str, Any]],
    root: Path,
    granted: Callable[[], set[str]] | None,
) -> list[Outcome]:
    destinations = entry.destinations
    action, detail = _decide(package, due, destinations)
    subject = due.credential_id
    drifted = action == _ADVANCE and package.carries(due.occurrence) and package.state == APPROVED
    if drifted and _recorded(records, package):
        # A record already names this revision: its rotation is under way or done, so it is never
        # revised from under it. The registry's change reaches the next rotation's revision.
        action, detail = _PROPOSE, f"{detail}; a record already names this revision"
    if action not in {_PROPOSE, _ADVANCE}:
        # Reported on every path, not only when proposing: while a newer revision waits for
        # approval, an open record for an older one is still stranded, and still approvable.
        return [Outcome(subject, action, detail), *_superseded(records, package)]
    resolved = _resolved(records, package) if action == _PROPOSE else None
    if resolved is not None:
        # Reached only by a writing pass: a dry run reads no records.
        return [_finished(due, resolved), *_superseded(records, package)]
    if client is None or observer is None:
        return [Outcome(subject, f"would-{action}", f"{detail} -> {due.occurrence}")]
    refusal = _ungranted(due, entry.repository, granted)
    if refusal is not None:
        return [refusal, *_superseded(records, package)]

    # OBSERVE FIRST, before the revision as well as before the proposal: a revision cannot be
    # unminted and a published commit cannot be unpublished, so a pass that cannot state the fact
    # spends neither.
    observation_id = observer.record_observation(rotation_observation(due))

    if action == _ADVANCE:
        require_publishable(root)
        package = to_review(package, due.occurrence, destinations, root)
        snapshot_hash(package, root)
        published = commit(package, root)
        return [
            Outcome(
                subject,
                "revised",
                f"{package.package_id} rev {package.revision} carries {due.occurrence}, "
                f"published {published[:12]}; awaiting a named human's approval",
            ),
            *_superseded(records, package),
        ]

    record, created = client.propose(_proposal(package, observation_id))
    outcomes = [
        Outcome(
            subject,
            "proposed" if created else "replayed",
            f"item {record.get('id')} {package.package_id} rev {package.revision} "
            f"status={record.get('status')}",
        )
    ]
    return outcomes + _superseded(records, package)


def _generate(
    entry: RegistryEntry,
    due: Due,
    observer: OrchestratorClient | None,
    root: Path,
) -> Outcome:
    """Revision 1 of a missing package, generated from the registry row, to review."""
    try:
        rendered = render(entry, due.occurrence, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    except GenerateError as error:
        return Outcome(
            due.credential_id,
            "unlaned",
            f"{due.occurrence} is due, the registry hands it to the SDS, and no standing package "
            f"rotation-{due.credential_id} exists or can be generated: {error}",
        )
    if observer is None:
        return Outcome(due.credential_id, "would-generate", f"rotation-{due.credential_id} rev 1")
    observer.record_observation(rotation_observation(due))
    require_publishable(root)
    package = create(rendered, root)
    package = to_review(package, due.occurrence, entry.destinations, root)
    snapshot_hash(package, root)
    published = commit(package, root, generated=True)
    return Outcome(
        due.credential_id,
        "generated",
        f"{package.package_id} rev 1, generated from the registry, carries {due.occurrence}, "
        f"published {published[:12]}; awaiting a named human's approval",
    )


def _not_handed_over(item: Due, package: RotationPackage | None) -> Outcome:
    """infraops still rotates it. A package for it is a misconfiguration: acting would put two
    executors on one credential (ADR-0054), so it is reported and never acted on."""
    if package is None:
        return Outcome(item.credential_id, "legacy", f"{item.occurrence}: infraops rotates it")
    return Outcome(
        item.credential_id,
        "not-handed-over",
        f"{package.package_id} exists but the registry does not mark "
        f"{item.credential_id} rotated_by_sds; infraops' window still owns it",
    )


def _entry(registry: Callable[[], dict[str, RegistryEntry]], credential_id: str) -> RegistryEntry:
    entry = registry().get(credential_id)
    if entry is None:
        raise RegistryError(f"{credential_id}: no registry entry")
    return entry


def _ungranted(
    item: Due, repository: str, granted: Callable[[], set[str]] | None
) -> Outcome | None:
    """A finding when the member record does not grant rotation on the credential's repository.

    Asked only where the next act is an observation, a revision, a generation or a proposal, so an
    ungranted credential starts nothing, while recording a finished rotation and reporting never
    ask. `granted` is `None` on a dry run, which reads no record.
    """
    if granted is None or repository.lower() in granted():
        return None
    return Outcome(
        item.credential_id,
        "ungranted",
        f"the member record does not grant credential_rotation on {repository}; nothing is written",
    )


def _stalled(credential_id: str, row: dict[str, Any], reader: Orchestrator) -> Outcome | None:
    """A finding when an approved record's rotation can't finish without a person."""
    units = reader.work(int(row["id"])).get("units") or []
    stalled = sorted(
        str(unit.get("unit_key")) for unit in units if unit.get("state") in STALLED_STATES
    )
    if not stalled:
        return None
    return Outcome(
        credential_id,
        "stalled",
        f"record {row['id']} ({row['package_id']} rev {row['package_revision']}): "
        f"{', '.join(stalled)} failed or was cancelled; it stays stalled until a person acts",
    )


def _completed_date(row: dict[str, Any], reader: Orchestrator) -> str:
    """The UTC day the record's last unit completed, as `record-rotation` takes it."""
    completions: list[datetime] = []
    for unit in reader.work(int(row["id"])).get("units") or []:
        for event in reader.history(str(unit.get("unit_id"))):
            if event.get("to_state") == COMPLETED_STATE:
                try:
                    occurred = datetime.fromisoformat(str(event.get("occurred_at")))
                except ValueError as error:
                    raise CallError(f"record {row['id']}: an unreadable completion") from error
                completions.append(occurred.astimezone(UTC))
    if not completions:
        raise CallError(f"record {row['id']}: no unit's history shows a completion")
    return max(completions).strftime("%Y-%m-%d")


def _closes_an_exposure(
    credential_id: str, row: dict[str, Any], due: list[Due], packages: dict[str, RotationPackage]
) -> bool:
    """An exposure open now, or the trigger the record's own revision carries."""
    if any(i.credential_id == credential_id and i.trigger == "exposure" for i in due):
        return True
    package = packages.get(credential_id)
    return (
        package is not None
        and package.revision == row.get("package_revision")
        and package.occurrence.endswith(EXPOSURE_SUFFIX)
    )


def _record_retired(
    credential_id: str, row: dict[str, Any], last_rotated: dict[str, str], reader: Orchestrator
) -> Outcome | None:
    """Move `lastRotated` to a retired rotation newer than it, or leave it alone."""
    date = _completed_date(row, reader)
    known = last_rotated.get(credential_id)
    if known is not None and date <= known[:10]:
        return None
    record_rotation(credential_id, date)
    return Outcome(
        credential_id,
        "recorded",
        f"record {row['id']} was retired by {RETIREMENT_ACTOR}: infraops records it rotated {date}",
    )


def _newest_retired(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each credential's retired record of its highest revision."""
    newest: dict[str, dict[str, Any]] = {}
    for row in records:
        credential_id = standing_credential(str(row.get("package_id")))
        if (
            credential_id is None
            or row.get("status") != "resolved"
            or row.get("decided_by") != RETIREMENT_ACTOR
        ):
            continue
        held = newest.get(credential_id)
        if held is None or int(row["package_revision"]) > int(held["package_revision"]):
            newest[credential_id] = row
    return newest


def _walk_records(
    records: list[dict[str, Any]],
    due: list[Due],
    packages: dict[str, RotationPackage],
    registry: Callable[[], dict[str, RegistryEntry]],
    last_rotated: dict[str, str] | None,
    reader: Orchestrator,
) -> list[Outcome]:
    """Every credential's rotation records, whether or not it is due. A record this can't read is
    that credential's `error`, which holds its alerts."""
    outcomes: list[Outcome] = []
    for row in records:
        credential_id = standing_credential(str(row.get("package_id")))
        if credential_id is None or row.get("status") != "approved":
            continue
        package = packages.get(credential_id)
        if package is not None and int(row["package_revision"]) < package.revision:
            continue  # Only the tip's record can stall; an earlier one is `_superseded`'s.
        try:
            stalled = _stalled(credential_id, row, reader)
        except _UNREAD as error:
            stalled = Outcome(credential_id, "error", f"the record walk: {error}")
        outcomes.extend([stalled] if stalled else [])

    if last_rotated is None:
        return outcomes  # An infraops predating the field: nothing to compare, so nothing moves.
    for credential_id, row in _newest_retired(records).items():
        if _closes_an_exposure(credential_id, row, due, packages):
            continue
        try:
            entry = registry().get(credential_id)
            if entry is None or not entry.rotated_by_sds:
                continue
            recorded = _record_retired(credential_id, row, last_rotated, reader)
        except _UNREAD as error:
            recorded = Outcome(credential_id, "error", f"the record walk: {error}")
        outcomes.extend([recorded] if recorded else [])
    return outcomes


def _pass(
    due: list[Due],
    packages: dict[str, RotationPackage],
    records: list[dict[str, Any]],
    client: ChangeManagerClient | None,
    observer: OrchestratorClient | None,
    root: Path,
    registry: Callable[[], dict[str, RegistryEntry]],
    *,
    granted: Callable[[], set[str]] | None,
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for item in due:
        package = packages.get(item.credential_id)
        if not item.handed_over:
            outcomes.append(_not_handed_over(item, package))
            continue
        try:
            entry = _entry(registry, item.credential_id)
            if package is None:
                refusal = _ungranted(item, entry.repository, granted)
                outcomes.append(refusal or _generate(entry, item, observer, root))
                continue
            outcomes.extend(_act(package, item, entry, client, observer, records, root, granted))
        except MembersUnreadable as error:
            # Only a credential whose next act needs the grant gets here, and each one reports
            # it. `error` holds that credential's other alerts (`alerts.py`).
            outcomes.append(Outcome(item.credential_id, "error", f"the grant: {error}"))
        except (RegistryError, MirrorError, DestinationError, ContractError) as error:
            # One malformed row is that credential's finding; the pass goes on to the rest.
            outcomes.append(Outcome(item.credential_id, "error", f"the registry: {error}"))
        except (ObservationUncomposable, ObservationWriteError) as error:
            outcomes.append(Outcome(item.credential_id, "unobserved", str(error)))
        except StandingError as error:
            outcomes.append(Outcome(item.credential_id, "error", str(error)))
        except ProposalRefused as error:
            outcomes.append(Outcome(item.credential_id, "refused", str(error)))
        except ChangeManagerError as error:
            outcomes.append(Outcome(item.credential_id, "error", str(error)))
    return outcomes


def _once[T](read: Callable[[], T]) -> Callable[[], T]:
    """`read`, asked at most once per pass, and only when a handed-over credential is due. A
    failure is asked again by the next credential, so each reports it."""
    found: list[T] = []

    def cached() -> T:
        if not found:
            found.append(read())
        return found[0]

    return cached


def _granted_repositories(credentials: _Credentials) -> set[str]:
    """Lower-cased repositories the member record grants `credential_rotation` on.

    An empty record is unread, not a withdrawal of every grant.
    """
    members = read_members(
        token=credentials.orchestrator_token,
        credential_key_id=OBSERVER_KEY_ID,
        base_url=credentials.orchestrator_url,
    )
    if not members:
        raise MembersUnreadable("the member record has no rows")
    return {name.lower() for name in populations.granted(members, "credential_rotation")}


def _report(outcomes: list[Outcome]) -> int:
    for outcome in outcomes:
        print(f"{outcome.credential_id}  {outcome.status:<17} {outcome.detail}")
    findings = [o for o in outcomes if o.status in FINDING_STATUSES]
    print(f"\n{len(outcomes)} considered, {len(findings)} findings")
    return EXIT_FINDINGS if findings else EXIT_OK


class _Credentials(NamedTuple):
    change_manager: str
    change_manager_url: str
    orchestrator_url: str
    orchestrator_token: str
    alert_token: str


def _credentials(*, submit: bool) -> _Credentials | None:
    """Every credential a WRITING pass needs, or None -- which stops the pass.

    A dry run needs none: it reads infraops and the checkout, and writes nothing anywhere. The
    observer bearer is read from the estate-wide variables every observe-and-report lane uses, and
    its key id is PINNED: the route admits SYSTEM too, and `recorded_by` is permanent.
    """
    change_manager = os.environ.get("ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN", "")
    url = os.environ.get("ROTATION_PROPOSER_CHANGE_MANAGER_URL", "")
    observer_url = os.environ.get("ORCHESTRATOR_API_URL", "")
    observer_key_id = os.environ.get("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "")
    observer_token = os.environ.get("ORCHESTRATOR_API_TOKEN", "")
    alert_token = os.environ.get(TOKEN_ENV, "")
    missing = ""
    if submit and not change_manager:
        missing = "ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN is unset; --submit needs it"
    elif submit and not (observer_url and observer_key_id and observer_token):
        missing = (
            "the orchestrator observer credential is unset; --submit needs it "
            "(ORCHESTRATOR_API_URL, ORCHESTRATOR_API_CREDENTIAL_KEY_ID, ORCHESTRATOR_API_TOKEN)"
        )
    elif submit and not alert_token:
        missing = f"{TOKEN_ENV} is unset; --submit needs it"
    elif submit and observer_key_id != OBSERVER_KEY_ID:
        missing = (
            f"ORCHESTRATOR_API_CREDENTIAL_KEY_ID is {observer_key_id!r}, not "
            f"{OBSERVER_KEY_ID!r}; this producer files observations as the observer and "
            "attribution cannot be corrected once written"
        )
    if missing:
        print(missing, file=sys.stderr)
        return None
    return _Credentials(change_manager, url, observer_url, observer_token, alert_token)


class _Inputs(NamedTuple):
    root: Path
    due: list[Due]
    unrecognised: list[Unrecognised]
    packages: dict[str, RotationPackage]
    last_rotated: dict[str, str] | None


def _inputs(*, submit: bool) -> _Inputs | None:
    """What is due and the packages that stand for it, or None -- which stops the pass."""
    root = checkout_root()
    try:
        due, unrecognised, last_rotated = read_due()
        packages = discover(root)
        if submit:
            # Both refusals answer "may this program write to this checkout?" -- an uncommitted
            # change is somebody else's work, and an unpublished commit is unfinished work that a
            # further revision must not be built on (ADR-0033).
            require_clean(root)
            require_publishable(root)
    except (FindingsError, StandingError) as error:
        print(str(error), file=sys.stderr)
        return None
    return _Inputs(root, due, unrecognised, packages, last_rotated)


def _walks(
    inputs: _Inputs,
    credentials: _Credentials,
    client: ChangeManagerClient | None,
    observer: OrchestratorClient | None,
    reader: Orchestrator | None,
) -> list[Outcome]:
    """The due walk, then a writing pass's record walk. `reader` is None on a dry run."""
    mirror = RepoMirror(default_mirror_root("rotation-proposer"))
    granted = _once(lambda: _granted_repositories(credentials)) if reader is not None else None
    if granted is None:
        print("a dry run does not read the member record: grants were not checked")
    registry = _once(lambda: registry_entries(mirror))
    records = client.work_records() if client is not None else []
    outcomes = _pass(
        inputs.due,
        inputs.packages,
        records,
        client,
        observer,
        inputs.root,
        registry,
        granted=granted,
    )
    if reader is not None:
        outcomes.extend(
            _walk_records(
                records, inputs.due, inputs.packages, registry, inputs.last_rotated, reader
            )
        )
    return outcomes


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--submit",
        action="store_true",
        help=(
            "actually observe, revise, publish and propose. Without it the pass is a dry run: "
            "it writes nothing, commits nothing and touches no credential."
        ),
    )
    args = parser.parse_args(argv)

    credentials = _credentials(submit=args.submit)
    if credentials is None:
        return EXIT_UNUSABLE
    inputs = _inputs(submit=args.submit)
    if inputs is None:
        return EXIT_UNUSABLE
    outcomes = [
        Outcome(item.credential_id, "unrecognised", f"infraops reported {item.check}")
        for item in inputs.unrecognised
    ]
    client = None
    observer = None
    reader = None
    try:
        if args.submit:
            client = ChangeManagerClient(
                credentials.change_manager,
                base_url=credentials.change_manager_url or DEFAULT_BASE_URL,
                user_agent=USER_AGENT,
            )
            observer = open_client(
                base_url=credentials.orchestrator_url,
                credential_key_id=OBSERVER_KEY_ID,
                token=credentials.orchestrator_token,
                user_agent=USER_AGENT,
            )
            reader = Orchestrator(
                base_url=credentials.orchestrator_url,
                credential_key_id=OBSERVER_KEY_ID,
                token=credentials.orchestrator_token,
            )
        outcomes.extend(_walks(inputs, credentials, client, observer, reader))
    except (UnusableEndpointError, ObservationCredentialError, ChangeManagerError) as error:
        # "This pass could not use its inputs", not "this pass found something": a refused
        # identity or an unreachable service is every credential at once.
        print(str(error), file=sys.stderr)
        return EXIT_UNUSABLE
    finally:
        if client is not None:
            client.close()
        if observer is not None:
            observer.close()
        if reader is not None:
            reader.close()
    code = _report(outcomes)
    if args.submit:
        _tell_a_person(outcomes, credentials.alert_token)
    return code


def _state_dir() -> Path:
    configured = os.environ.get(STATE_DIR_ENV)
    if configured:
        return Path(configured)
    return Path.home() / "Library" / "Application Support" / "sds" / "rotation-proposer"


def _tell_a_person(outcomes: list[Outcome], token: str) -> None:
    """Sync the pass's alerts. A failure is reported and never changes the exit code, which
    describes the rotations and not alert-hub."""
    reason = sync_alerts(outcomes, token=token, state_dir=_state_dir(), now=datetime.now(UTC))
    if reason is not None:
        print(f"alert-hub was not brought up to date: {reason}", file=sys.stderr)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
