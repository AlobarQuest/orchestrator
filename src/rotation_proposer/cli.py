"""Turn a credential that is due for rotation into proposed work (ADR-0054 amendment 1).

**WHAT THIS IS THE HEAD OF.** infraops' 03:00 scan knows when a registry credential is due -- by
age, by a recorded exposure, or by a `rotate_requested` date -- and until now it filed that to
change-manager as a `security` record outside the SDS. Amendment 1 has the scan only DETECT, and
this program carry the detection into the SDS the way `bump_proposer` carries a refused bump:

    infraops reports a credential due (`security-drift-cli cred-findings`)
      -> HERE, PASS 1: FILE THE OBSERVATION, revise the credential's standing package
         `rotation-<credential_id>` so its new revision carries this occurrence, take it to
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
surface. Its orchestrator client names one route, the observation ingest, under the OBSERVER
bearer, which can neither transition a unit nor create work.

**A REPEAT PASS IS A REPLAY.** A package whose tip already carries the occurrence is not revised
again; the observation is content-addressed; change-manager answers 200 for an identical proposal.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
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
from rotation_proposer.findings import Due, FindingsError, Unrecognised, read_due
from rotation_proposer.observation import ObservationUncomposable, rotation_observation
from rotation_proposer.standing import (
    APPROVED,
    DRAFT,
    IN_REVIEW,
    REJECTED,
    REVISABLE,
    RotationPackage,
    commit,
    discover,
    to_review,
)

EXIT_OK: Final = 0
EXIT_UNUSABLE: Final = 2
EXIT_FINDINGS: Final = 3

ACTOR: Final = "rotation-proposer"
RISK: Final = "caution"
USER_AGENT: Final = "rotation-proposer/1 (+AlobarQuest/orchestrator)"
OBSERVER_KEY_ID: Final = "orchestrator-observer"

# What makes a pass a FINDING rather than a clean run. `unlaned` is deliberately absent, as it is
# from `bump_proposer`'s: a credential nobody authored a package for is outside the lane by
# construction, and with 21 registry entries and a handful of packages it would otherwise be a
# finding on every pass that nothing but authoring can clear. `awaiting-approval` and `revised`
# are absent because waiting on Devon is the lane working, not failing.
FINDING_STATUSES: Final = frozenset(
    {
        "error",
        "refused",
        "unobserved",
        "superseded",
        "stacked",
        "unexpected-state",
        "unrecognised",
    }
)

# What a pass does with one credential, decided from the package alone before anything is written.
_PROPOSE: Final = "propose"
_ADVANCE: Final = "advance"


@dataclass(frozen=True)
class Outcome:
    credential_id: str
    status: str
    detail: str


def _decide(package: RotationPackage, due: Due) -> tuple[str, str]:
    """`(action, detail)`: `propose`, `advance`, or a status to report with nothing written."""
    label = f"{package.package_id} rev {package.revision} ({package.state})"
    if package.carries(due.occurrence):
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
        f"approval policy grants rotation revisions (ADR-0054 amendment 1). Approving this record "
        f"is the decision to rotate the credential; the decomposition, the authority envelope and "
        f"every adjudication are each approved separately after it."
    )


def _proposal(package: RotationPackage, observation_id: str) -> dict[str, Any]:
    """The record's facts. Exactly `bump_proposer.cli.PROPOSAL_FIELDS`, which a test holds it to."""
    return {
        "package_id": package.package_id,
        "package_revision": package.revision,
        "package_source_repository": PACKAGES_REPOSITORY,
        "risk": RISK,
        "reasoning": _reasoning(package),
        "actor": ACTOR,
        "originating_observation_id": observation_id,
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


def _act(
    package: RotationPackage,
    due: Due,
    client: ChangeManagerClient | None,
    observer: OrchestratorClient | None,
    records: list[dict[str, Any]],
    root: Path,
) -> list[Outcome]:
    action, detail = _decide(package, due)
    subject = due.credential_id
    if action not in {_PROPOSE, _ADVANCE}:
        return [Outcome(subject, action, detail)]
    if client is None or observer is None:
        return [Outcome(subject, f"would-{action}", f"{detail} -> {due.occurrence}")]

    # OBSERVE FIRST, before the revision as well as before the proposal: a revision cannot be
    # unminted and a published commit cannot be unpublished, so a pass that cannot state the fact
    # spends neither.
    observation_id = observer.record_observation(rotation_observation(due))

    if action == _ADVANCE:
        require_publishable(root)
        package = to_review(package, due.occurrence, root)
        snapshot_hash(package, root)
        published = commit(package, root)
        return [
            Outcome(
                subject,
                "revised",
                f"{package.package_id} rev {package.revision} carries {due.occurrence}, "
                f"published {published[:12]}; awaiting a named human's approval",
            )
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


def _pass(
    due: list[Due],
    packages: dict[str, RotationPackage],
    client: ChangeManagerClient | None,
    observer: OrchestratorClient | None,
    root: Path,
) -> list[Outcome]:
    records = client.work_records() if client is not None else []
    outcomes: list[Outcome] = []
    for item in due:
        package = packages.get(item.credential_id)
        if package is None:
            outcomes.append(
                Outcome(
                    item.credential_id,
                    "unlaned",
                    f"{item.occurrence} is due and no standing package rotation-"
                    f"{item.credential_id} exists; authoring one brings it into this lane",
                )
            )
            continue
        try:
            outcomes.extend(_act(package, item, client, observer, records, root))
        except (ObservationUncomposable, ObservationWriteError) as error:
            outcomes.append(Outcome(item.credential_id, "unobserved", str(error)))
        except StandingError as error:
            outcomes.append(Outcome(item.credential_id, "error", str(error)))
        except ProposalRefused as error:
            outcomes.append(Outcome(item.credential_id, "refused", str(error)))
        except ChangeManagerError as error:
            outcomes.append(Outcome(item.credential_id, "error", str(error)))
    return outcomes


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
    missing = ""
    if submit and not change_manager:
        missing = "ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN is unset; --submit needs it"
    elif submit and not (observer_url and observer_key_id and observer_token):
        missing = (
            "the orchestrator observer credential is unset; --submit needs it "
            "(ORCHESTRATOR_API_URL, ORCHESTRATOR_API_CREDENTIAL_KEY_ID, ORCHESTRATOR_API_TOKEN)"
        )
    elif submit and observer_key_id != OBSERVER_KEY_ID:
        missing = (
            f"ORCHESTRATOR_API_CREDENTIAL_KEY_ID is {observer_key_id!r}, not "
            f"{OBSERVER_KEY_ID!r}; this producer files observations as the observer and "
            "attribution cannot be corrected once written"
        )
    if missing:
        print(missing, file=sys.stderr)
        return None
    return _Credentials(change_manager, url, observer_url, observer_token)


class _Inputs(NamedTuple):
    root: Path
    due: list[Due]
    unrecognised: list[Unrecognised]
    packages: dict[str, RotationPackage]


def _inputs(*, submit: bool) -> _Inputs | None:
    """What is due and the packages that stand for it, or None -- which stops the pass."""
    root = checkout_root()
    try:
        due, unrecognised = read_due()
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
    return _Inputs(root, due, unrecognised, packages)


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
    root, due, unrecognised, packages = inputs

    outcomes = [
        Outcome(item.credential_id, "unrecognised", f"infraops reported {item.check}")
        for item in unrecognised
    ]
    client = None
    observer = None
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
        outcomes.extend(_pass(due, packages, client, observer, root))
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
    return _report(outcomes)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
