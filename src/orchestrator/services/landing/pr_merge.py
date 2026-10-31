"""The factory lands its own pull request. ADR-0020, Increment 4b.

**This is the one place in this repository that changes a repository nobody asked it to change at
the moment it acts**, and the shape is copied from the only other authorized outbound mutation
this estate has: an admission cascade of named refusals, a record row with a unique constraint so
a repeat is detectable, an injected client so the whole path can be exercised with no network, and
credentials resolved once at the route and handed to both the gate and the actor, so the gate can
never attest to credentials the actor does not use.

**Every term is re-evaluated HERE, over the row this transaction locked.** The reported answer
(`services/landing/pr_merge_admission.py`) is a report; between reading it and acting, a unit can be
transitioned, an approval can be recorded, a divergence can be detected. Re-asking is what closes
that window, and it is why the act is an explicit command rather than a side effect of completion:
a failed landing must never roll back a recorded completion.

## Three terms only this module can evaluate, because only GitHub knows them

The estate's answer is about a repository's DEFAULT branch. Nothing on this side records what a
pull request targets, so a pull request opened against some other base would land somewhere that
answer does not describe. Asked here, refused here.

Whether the pull request is still open, likewise. And the head: every head this side holds is one
somebody REPORTED, so an unreported push is invisible to a local comparison. The call therefore
NAMES the adjudicated head, and the remote refuses anything else — a guarantee no comparison of
our own rows can offer.

## What the policy path also asks the remote, because nobody looked at this pull request

A green head says the checks passed, and the agent that wrote the bump can reach the checks: the
workspace root is writable and its token may push workflow files, so one pull request can move the
pin and rewrite `Quality` to `exit 0`. The post-landing watch keys on failures on `main`, which a
neutered workflow never produces. A bound human approval has a person behind the diff; the policy
path has nobody, so it asks of the pull request itself, after it is known to be open and before
the landing:

1. It is the unit's own branch, in the repository itself: `sds/<unit8>-attempt-<attempt>`, where
   the attempt is the claim's, the one `_head_terms` already holds the armed head to.
2. Its head is the adjudicated head, so the file list below describes the tree being landed.
3. Its body carries the `SDS-Unit:` trailer whole, on its own line.
4. Its file list reads completely, and touches nothing the checks stand on: `.github/`, a
   `CODEOWNERS` anywhere, a root makefile. A rename names both ends, or moving a workflow away
   would hide it.
5. It changes the package's manifest (`MANIFESTS_BY_ECOSYSTEM`), the generator's pin sites, so the
   bump is observed and not only attested.

Each failure is its own refusal with no record, like every term above the landing call: a row
bars another call and only an unknown outcome is ever settled, so a stored refusal would bar the
person who reads the pull request and lands it. The merge is left undone, the pull request open,
and the bump driver alerts `bump-act-refused`. Tests can still be weakened: only workflow and gate
files are refused (ADR-0025 amendment 4).

## Idempotency, and why the order is settle, check, act, reconcile, record

A landing is not idempotent and its failure is asymmetric: if it succeeds and the response is
lost, asking again answers 405, which is shaped exactly like a refusal. So:

1. **Settle.** A row whose outcome is unknown is settled by a read, in a step committed of its own
   (`_resettle`). Nothing here depends on the terms, which ran before the call that row records.
2. **Check.** The unit's row is locked and its merge record read. A record means the call was
   already made; it is replayed, never repeated. This is the scar the workflow trigger left —
   there, a reused ordinal returns a success-shaped record having fired nothing, and a repeat here
   would be worse, because the second call reaches GitHub and comes back with a plausible refusal.
3. **Act.**
4. **Reconcile before recording a refusal.** A refusal from the remote is followed by re-reading
   the pull request: if it is in fact landed, the record says so. Without that, a retry after a
   lost response writes `refused` over something that happened, which is the one outcome nothing
   downstream could correct.
5. **Record.**

Act-then-record, deliberately, because the two failure modes are not equal. A record written
before a call that then fails is a lie in the ledger. A landing whose record is lost is
recoverable: the landing ledger observes GitHub independently and would report it as basis-less
rather than silently.

## One off-switch, and it governs the policy path alone

A scheduled lane lands standing dependency updates here on their recognition and grant, with no
person behind each act (ADR-0025 amendment 4), and a switch is a real control against a loop:
`ORCHESTRATOR_BUMP_MERGE_ENABLED=false` stops that path with the named refusal
`merge_policy_path_off`. A landing on a bound human approval doesn't ask it, because that has one
accountable person behind each act, where a process-wide switch stops nothing a per-unit stop
doesn't and toggling it costs a restart, the one thing that strands a live run. Its stops are to
withhold the authority approval, withhold the capability in the envelope, branch protection, and
revoking the App installation's write.

## The lifted requirement is recorded by the act

The policy path's trace is `authority.human_gate_not_required`, written beside the merge record in
one transaction, whenever this act's call landed the pull request: `merged`, or `already_merged`
reconciled after the call, by the re-read or by a later settling. It is written here rather than
by the admission answer, which is also served to readers and writes nothing.

## Which `already_merged` is ours

`already_merged` has two meanings the status can't tell apart. Found landed after our call at the
head we named, it is our act with a lost answer. Found landed before any call, it is somebody
else's, or a crash nobody can attribute. Found landed after our call at any other head, or with no
head to compare, it is somebody else's too: the remote refuses a call naming a moved head, so that
tree is one the terms never read, and reverting it would take a person's work out with the bump.
Only the first carries the event's `landing_inert` and `policy_path`, written when the call was
made and copied when the row settles. The post-landing watch selects on that stamp.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.change_window_override import ChangeWindowOverride, override_record
from orchestrator.clock import Clock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorContext, ActorRole
from orchestrator.persistence.models import (
    Event,
    UnitPrMerge,
    WorkPackage,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.github_app import GitHubAppTokenError
from orchestrator.services.landing.change_record import ChangeRecordSource
from orchestrator.services.landing.estate_landing import EstateLandingSource
from orchestrator.services.landing.interfaces import MERGE_REFUSED_BY_REMOTE, outcome_is_unknown
from orchestrator.services.landing.pr_merge_admission import MergeAdmission, admission_for
from orchestrator.services.membership.holds import (
    TOOLING_TO_DEPENDABOT,
    ecosystem_of_package_id,
)

GITHUB_API_URL: Final = "https://api.github.com"

# Refusals that leave NO RECORD, because nothing happened and each can be tried again. The pull
# request does not target the branch the estate's answer is about; the pull request is not open;
# the remote could not be read; the remote refused and a confirming read established that nothing
# landed. A record bars another call, so recording any of these would bar a unit for a condition
# that clears on its own or with one edit.
PR_MERGE_BASE_NOT_DEFAULT_BRANCH: Final = "pr_merge_base_not_default_branch"
PR_MERGE_NOT_OPEN: Final = "pr_merge_not_open"
PR_MERGE_REMOTE_UNREADABLE: Final = "pr_merge_remote_unreadable"
PR_MERGE_REFUSED_BY_REMOTE: Final = "pr_merge_refused_by_remote"

# The App credentials are not configured, so nothing can be minted and no call can be made. Asked
# BEFORE the remote is touched, the way the workflow trigger asks it -- its own definition of
# "configured" says why: the gate and the minter must read the same answer, or the gate attests to
# credentials the actor does not have. Without this term a deployment that shipped without the App
# variables would meet the remote-unreadable path on every unit.
PR_MERGE_APP_CREDENTIALS_MISSING: Final = "pr_merge_app_credentials_missing"

# The policy path's own terms, asked of the pull request (module docstring). Each leaves no record:
# the row is permanent, and a person may read the pull request and land it.
#
# Not the unit's own branch in the repository itself, so nothing ties it to the unit's run.
PR_MERGE_HEAD_NOT_RUNNER_BRANCH: Final = "pr_merge_head_not_runner_branch"
# The pull request's head is not the adjudicated head, so its file list would describe another tree.
PR_MERGE_HEAD_NOT_ADJUDICATED: Final = "pr_merge_head_not_adjudicated"
PR_MERGE_UNIT_TRAILER_MISSING: Final = "pr_merge_unit_trailer_missing"
# The file list could not be read, or read fewer files than the pull request says it changes.
PR_MERGE_FILE_LIST_UNREADABLE: Final = "pr_merge_file_list_unreadable"
PR_MERGE_FILE_LIST_TRUNCATED: Final = "pr_merge_file_list_truncated"
# A file the checks stand on changed, which a person has to read.
PR_MERGE_GATE_FILE_CHANGED: Final = "pr_merge_gate_file_changed"
# No changed file is the package's manifest, or the package names no tooling to find one by.
PR_MERGE_MANIFEST_UNCHANGED: Final = "pr_merge_manifest_unchanged"

# The generator's pin sites per tooling (intent-packages' `dependency_update.py`), by Dependabot's
# name for it, all at the repository root.
MANIFESTS_BY_ECOSYSTEM: Final = {
    TOOLING_TO_DEPENDABOT["npm"]: frozenset({"package.json"}),
    TOOLING_TO_DEPENDABOT["pip"]: frozenset({"requirements.txt", "requirements-dev.txt"}),
    TOOLING_TO_DEPENDABOT["uv"]: frozenset({"pyproject.toml"}),
}
# Compared case-folded. `make` reads GNUmakefile and makefile before Makefile, so adding either
# replaces the root gate.
# not-a-vocabulary: root file names make reads, compared with a repository path
ROOT_GATE_FILES: Final = frozenset({"makefile", "gnumakefile"})

FILES_PER_PAGE: Final = 100
# GitHub lists at most 3000 files, so a thirty-first page is empty; the count against
# `changed_files` is what refuses a longer pull request.
MAX_FILE_PAGES: Final = 31

# The recorded statuses that say the pull request landed. A confirmed non-landing is `refused`.
_LANDED: Final = frozenset({"merged", "already_merged"})  # not-a-vocabulary: `_record`'s statuses


@dataclass(frozen=True)
class PullRequestState:
    """What the remote says about the pull request, as this module needs it."""

    base_ref: str
    default_branch: str
    open: bool
    landed: bool
    # Read only on the policy path. An absent value fails that path's comparison, so a shape the
    # remote stopped serving refuses there and changes nothing for a bound approval.
    head_ref: str = ""
    head_repository: str = ""
    head_sha: str = ""
    body: str = ""
    changed_files: int = -1
    # Only for a landed pull request: the remote names a test commit on an open one.
    merge_commit_sha: str | None = None


@dataclass(frozen=True)
class ChangedFile:
    """One entry of a pull request's file list. A rename names both ends."""

    path: str
    previous_path: str | None = None
    removed: bool = False


@dataclass(frozen=True)
class MergeOutcome:
    landed: bool
    commit_sha: str | None
    status_code: int | None


class PullRequestGateway(Protocol):
    """Read the pull request, and ask for it to be landed. Injected, so the whole admission
    cascade is exercisable with no network."""

    def read_pull_request(self, *, repository: str, number: int) -> PullRequestState: ...

    def pull_request_files(self, *, repository: str, number: int) -> tuple[ChangedFile, ...]: ...

    def submit_merge(self, *, repository: str, number: int, head_sha: str) -> MergeOutcome: ...


class GitHubGatewayError(Exception):
    """A failure to reach or read the remote. Carries a code, never a token."""

    def __init__(self, code: str, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class MergeCommand:
    unit_id: uuid.UUID
    actor: ActorContext
    idempotency_key: str
    # REQUIRED, with no default. A default of `None` meaning "skip the check" is a precondition
    # that holds by the good behaviour of the one caller that exists — the same shape the revision
    # argument was corrected for one module over. The caller has just read an admission answer;
    # stating the version it read is what makes "nothing moved in between" its claim.
    expected_version: int
    # A supervised landing may happen outside the hours policy declares (ADR-0032). It suppresses
    # `merge_outside_change_window` and NOTHING else, and it is this act's alone: whatever was
    # said about starting the run that produced this pull request grants nothing here.
    change_window_override: ChangeWindowOverride | None = None


def land_unit_pull_request(
    session: Session,
    command: MergeCommand,
    gateway: PullRequestGateway,
    landing_source: EstateLandingSource,
    record_source: ChangeRecordSource,
    credentials_configured: bool = True,
    clock: Clock | None = None,
    *,
    bump_merge_enabled: bool = False,
) -> UnitPrMerge:
    """Own the transaction, the way every request entry point in this repository does.

    A flush alone would return a correct-looking response while the row is discarded when the
    session closes — leaving the record of an act that really happened absent, which is the one
    state the whole idempotency story depends on not reaching.

    **A row whose outcome is unknown is settled in a step of its own, committed before the ordinary
    path starts**, as `lane_act.land_pull_request` does and for its reason: what the read found
    stands whatever the terms say next, and a rollback would restore a removed row. The commit
    releases the unit's lock, so the ordinary path takes it again and reads the row again.
    """
    try:
        settled = _settle(session, command, gateway)
        session.commit()
        if settled is not None:
            return settled
        record = _land_unit_pull_request(
            session,
            command,
            gateway,
            landing_source,
            record_source,
            credentials_configured,
            clock,
            bump_merge_enabled,
        )
        session.commit()
        return record
    except Exception:
        session.rollback()
        raise


def _settle(
    session: Session, command: MergeCommand, gateway: PullRequestGateway
) -> UnitPrMerge | None:
    """The row this request is answered from, settling it first if its outcome is unknown; `None`
    when there is none, or it was just removed, and the ordinary path runs."""
    unit = _locked_unit(session, command)
    existing = _recorded(session, unit)
    if existing is None or not outcome_is_unknown(existing.status, existing.reason_code):
        return existing
    return _resettle(session, command, unit, gateway, existing)


def _locked_unit(session: Session, command: MergeCommand) -> WorkUnit:
    _authorize_actor(command.actor)
    unit = session.scalar(select(WorkUnit).where(WorkUnit.id == command.unit_id).with_for_update())
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    if unit.version != command.expected_version:
        raise DomainError(
            "version_conflict",
            "work unit version has changed",
            "reload",
            current_state=unit.state,
            current_version=unit.version,
        )
    return unit


def _recorded(session: Session, unit: WorkUnit) -> UnitPrMerge | None:
    """Read BEFORE the call, under the unit's row lock, so two requests cannot both find nothing."""
    return session.scalar(
        select(UnitPrMerge).where(UnitPrMerge.work_unit_id == unit.id).with_for_update()
    )


def _resettle(
    session: Session,
    command: MergeCommand,
    unit: WorkUnit,
    gateway: PullRequestGateway,
    record: UnitPrMerge,
) -> UnitPrMerge | None:
    """Settle a row whose outcome was unknown, if the remote can now say. `None` means the row was
    removed and the caller proceeds as though it had never been written.

    Still unknown: the row is returned as it stands. Landed: the row becomes the `already_merged`
    it should have been and its event moves to one naming that action, because the post-landing
    watch reads the row's event; landed at any head but the one the call named, the event carries
    neither stamp and no gate trace is written (the module docstring). Not landed: nothing
    happened, so the row is removed; its event stays, and the removal has its own.

    **Every new event is the row's stored event with its outcome changed.** That event was written
    when the call was made, so `landing_inert` and `policy_path` are the answers the call was made
    on. No term is asked again: policy may have narrowed since, and no new call is made. A unit's
    state and version don't move, since a landing is not a transition.
    """
    state = _landed_after_all(gateway, record.repository, record.pr_number)
    if state is None:
        return record
    stored = session.get(Event, record.event_id)
    payload: dict[str, Any] = dict(stored.payload) if stored is not None else {}
    if not state.landed:
        _write_event(
            session,
            command,
            unit,
            {**payload, "status": "unlanded"},
            key=f"{record.idempotency_key}:unlanded:{record.id}",
        )
        session.delete(record)
        session.flush()
        return None
    ours = state.head_sha == record.head_sha
    policy_path = payload.get("policy_path") if ours else None
    record.status = "already_merged"
    record.reason_code = None
    record.merge_commit_sha = state.merge_commit_sha
    record.updated_at = datetime.now(UTC)
    event = _write_event(
        session,
        command,
        unit,
        {
            **payload,
            "status": record.status,
            "reason_code": None,
            "merge_commit_sha": record.merge_commit_sha,
            "landing_inert": ours and payload.get("landing_inert") is True,
            "policy_path": policy_path,
        },
        key=f"{record.idempotency_key}:{record.status}:{record.id}",
    )
    record.event_id = event.id
    if isinstance(policy_path, Mapping):
        _record_gate_not_required(session, command, unit, policy_path)
    session.flush()
    return record


def _land_unit_pull_request(
    session: Session,
    command: MergeCommand,
    gateway: PullRequestGateway,
    landing_source: EstateLandingSource,
    record_source: ChangeRecordSource,
    credentials_configured: bool,
    clock: Clock | None,
    bump_merge_enabled: bool,
) -> UnitPrMerge:
    unit = _locked_unit(session, command)
    existing = _recorded(session, unit)
    if existing is not None:
        return existing

    # A key already spent on a DIFFERENT unit, refused here rather than at the flush. Both
    # `unit_pr_merge.idempotency_key` and `events.idempotency_key` are globally unique, so an
    # operator who copies one request and changes only the unit id would otherwise reach the
    # remote, LAND THE PULL REQUEST, and then lose the whole transaction to an `IntegrityError`
    # with no registered handler -- a bare 500 that reads as "nothing happened" over a merge that
    # did. Found by two independent reviews; the cost of the check is one indexed read.
    spent = session.scalar(
        select(UnitPrMerge).where(UnitPrMerge.idempotency_key == command.idempotency_key)
    )
    if spent is not None:
        raise DomainError(
            "idempotency_conflict",
            "this idempotency key belongs to a different work unit",
            "use a new idempotency key",
        )

    if not credentials_configured:
        raise DomainError(
            PR_MERGE_APP_CREDENTIALS_MISSING,
            "the GitHub App credentials are not configured",
            "configure the App credentials before asking the factory to land anything",
        )

    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    if revision is None:
        raise DomainError("revision_not_found", "package revision does not exist", None)

    admission = admission_for(
        session,
        unit,
        revision,
        landing_source,
        record_source,
        clock,
        command.change_window_override,
        bump_merge_enabled=bump_merge_enabled,
    )
    if not admission.satisfied:
        # No record: this unit was never acted on, and consuming its one row here would refuse
        # every later legitimate attempt. The reasons are already served by the read surface.
        # The refusals are named in the message rather than in a structured field: `DomainError`
        # carries a closed set of attributes, and widening it for one caller would put a shape in
        # the error contract that every handler would then have to know about.
        raise DomainError(
            "pr_merge_not_admissible",
            "this unit may not have its pull request landed: " + ", ".join(admission.refusals),
            "read the merge-admission answer for every term that is unmet",
        )

    return _act(session, command, unit, revision, gateway, admission)


def _act(
    session: Session,
    command: MergeCommand,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    gateway: PullRequestGateway,
    admission: MergeAdmission,
) -> UnitPrMerge:
    """The remote terms, the call, and the reconciling re-read.

    **A RECORD IS WRITTEN ONLY FOR AN OUTCOME THAT CANNOT BE RETRIED**, and getting that boundary
    wrong is how the row meant to protect the unit becomes the thing that bars it. The row is
    unique per unit, so a recorded outcome bars another call — which is correct for *it landed* and
    for *we cannot rule out that it landed*, which only a later read may settle (`_resettle`), and
    badly wrong for *GitHub answered 502 once*. Two independent reviews found the first draft
    recording a transient read failure here, which would have permanently barred a unit from being
    landed for the duration of one bad response.

    So the boundary is `submit_merge`, which is the only call that is not idempotent. Everything
    before it — a failed read, a base that is not the default branch, a closed pull request — is
    a REFUSAL WITHOUT A RECORD: each is either transient or fixable (a pull request can be
    retargeted or reopened), and none of them changed anything.
    """
    number = admission.pr_number
    head_sha = admission.verified_head_sha
    if number is None or not head_sha:
        # Unreachable through `admission_for`, which refuses both. Stated rather than assumed,
        # because this is the last point at which either could be absent and the call would then
        # name no head — which is the whole guarantee.
        raise DomainError("pr_merge_not_admissible", "the pull request is not identified", None)

    try:
        state = gateway.read_pull_request(repository=admission.target_repository, number=number)
    except GitHubGatewayError as error:
        raise DomainError(
            PR_MERGE_REMOTE_UNREADABLE,
            f"the pull request could not be read: {error.code}",
            "retry once the remote answers",
        ) from error

    if state.base_ref != state.default_branch:
        raise DomainError(
            PR_MERGE_BASE_NOT_DEFAULT_BRANCH,
            f"the pull request targets {state.base_ref}, not the default branch",
            "retarget the pull request, or land it by hand",
        )
    if state.landed:
        # Terminal and true, so it is recorded — but as somebody else's act, never as ours. This
        # is also the state a crash between acting and recording leaves behind, which is why the
        # branch has to exist rather than being folded into a refusal.
        return _record(
            session,
            command,
            unit,
            revision,
            admission,
            number,
            head_sha,
            status="already_merged",
            reason_code=None,
            ours=False,
        )
    if not state.open:
        raise DomainError(
            PR_MERGE_NOT_OPEN,
            "the pull request is not open",
            "reopen the pull request, or land it by hand",
        )
    if admission.authority_recognised_by:
        _require_only_the_bump(session, unit, revision, gateway, admission, state, number, head_sha)

    try:
        outcome = gateway.submit_merge(
            repository=admission.target_repository, number=number, head_sha=head_sha
        )
    except GitHubGatewayError as error:
        # THE RECONCILING READ, and the three answers it can give are three different outcomes.
        # A refusal is indistinguishable from a lost response to a call that succeeded, so ask
        # what is true before writing what happened. Asked ONCE and bound to a name: reading twice
        # would be two answers to one question.
        reread = _landed_after_all(gateway, admission.target_repository, number)
        landed = None if reread is None else reread.landed
        if landed is True:
            return _record(
                session,
                command,
                unit,
                revision,
                admission,
                number,
                head_sha,
                status="already_merged",
                reason_code=None,
                merge_commit_sha=reread.merge_commit_sha if reread is not None else None,
                github_status=error.status_code,
                ours=reread is not None and reread.head_sha == head_sha,
            )
        if landed is False:
            # CONFIRMED not landed, so nothing happened and this is retryable — a required check
            # that is red today can be green tomorrow. No record: the row is for outcomes that
            # cannot be retried.
            raise DomainError(
                PR_MERGE_REFUSED_BY_REMOTE,
                f"the remote refused to land the pull request: {error.code}",
                "resolve what the remote objected to, then ask again",
            ) from error
        # The reconciling read ITSELF failed, so we cannot rule out that it landed. This is the
        # one genuinely ambiguous outcome, and it is recorded, because a repeat of the call would
        # meet the same refusal no better informed. A later request settles it (`_resettle`).
        return _record(
            session,
            command,
            unit,
            revision,
            admission,
            number,
            head_sha,
            status="refused",
            reason_code=f"{MERGE_REFUSED_BY_REMOTE}:{error.code}",
            github_status=error.status_code,
        )

    return _record(
        session,
        command,
        unit,
        revision,
        admission,
        number,
        head_sha,
        status="merged" if outcome.landed else "refused",
        reason_code=None if outcome.landed else MERGE_REFUSED_BY_REMOTE,
        merge_commit_sha=outcome.commit_sha,
        github_status=outcome.status_code,
    )


def _require_only_the_bump(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    gateway: PullRequestGateway,
    admission: MergeAdmission,
    state: PullRequestState,
    number: int,
    head_sha: str,
) -> None:
    """The policy path's terms, in the module docstring's order. Each refuses with no record."""
    repository = admission.target_repository
    branch = f"sds/{str(unit.id)[:8]}-attempt-{unit.attempt_count}"
    if state.head_ref != branch or state.head_repository.casefold() != repository.casefold():
        raise _held(PR_MERGE_HEAD_NOT_RUNNER_BRANCH, f"the head is not {branch} in {repository}")
    if state.head_sha != head_sha:
        raise _held(PR_MERGE_HEAD_NOT_ADJUDICATED, "the head is not the adjudicated head")
    if f"SDS-Unit: {unit.id}" not in state.body.splitlines():
        raise _held(PR_MERGE_UNIT_TRAILER_MISSING, "the body names no SDS-Unit trailer for it")
    try:
        files = gateway.pull_request_files(repository=repository, number=number)
    except GitHubGatewayError as error:
        raise DomainError(
            PR_MERGE_FILE_LIST_UNREADABLE,
            f"the pull request's files could not be read: {error.code}",
            "retry once the remote answers",
        ) from error
    if len(files) != state.changed_files:
        raise _held(
            PR_MERGE_FILE_LIST_TRUNCATED,
            f"the list holds {len(files)} of {state.changed_files} changed files",
        )
    gates = sorted({path for file in files for path in _ends(file) if _is_gate_file(path)})
    if gates:
        raise _held(PR_MERGE_GATE_FILE_CHANGED, f"it changes {', '.join(gates[:5])}")
    manifests = _manifests(session, revision, repository)
    if not any(file.path in manifests and not file.removed for file in files):
        raise _held(PR_MERGE_MANIFEST_UNCHANGED, "it changes no manifest of the package's tooling")


def _held(code: str, detail: str) -> DomainError:
    return DomainError(
        code,
        f"the policy path will not land this pull request: {detail}",
        "a person reads the pull request and lands it, or closes it",
    )


def _ends(file: ChangedFile) -> tuple[str, ...]:
    return (file.path,) if file.previous_path is None else (file.path, file.previous_path)


def _is_gate_file(path: str) -> bool:
    folded = path.casefold()
    return (
        folded.startswith(".github/")
        or folded.rsplit("/", 1)[-1] == "codeowners"
        or folded in ROOT_GATE_FILES
    )


def _manifests(session: Session, revision: WorkPackageRevision, repository: str) -> frozenset[str]:
    """The revision's tooling's manifests, or none when its package names no tooling."""
    package = session.get(WorkPackage, revision.work_package_id)
    ecosystem = None if package is None else ecosystem_of_package_id(package.package_id, repository)
    return MANIFESTS_BY_ECOSYSTEM.get(ecosystem or "", frozenset())


def _landed_after_all(
    gateway: PullRequestGateway, repository: str, number: int
) -> PullRequestState | None:
    """Did the pull request land despite the refusal? `None` means WE DO NOT KNOW; otherwise the
    re-read pull request, whose `landed` answers and whose merge commit names the landing.

    Three answers, not two, and the third is the one the caller must treat differently: a second
    failure to read cannot be collapsed into "it did not land", because that reads a lost success
    as a clean refusal — which is exactly the confusion this whole path exists to resolve.
    """
    try:
        return gateway.read_pull_request(repository=repository, number=number)
    except GitHubGatewayError:
        return None


def _authorize_actor(actor: ActorContext) -> None:
    """SYSTEM only, and deliberately not the worker.

    A runner asking for its own pull request to be landed is the runner attesting to its own
    compliance, which is the shape WS-P2.32 spent a workstream closing. A human does not need this
    route: a person can land a pull request themselves, and this exists for the case where nobody
    had to.
    """
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden", "only the orchestrator system actor may land a pull request", None
        )


def _record(
    session: Session,
    command: MergeCommand,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    admission: MergeAdmission,
    number: int,
    head_sha: str,
    *,
    status: str,
    reason_code: str | None,
    merge_commit_sha: str | None = None,
    github_status: int | None = None,
    ours: bool = True,
) -> UnitPrMerge:
    """`ours` is false when the row records a landing this act can't claim (`Which already_merged
    is ours`, in the module docstring), so its event carries neither of the stamps that say so."""
    policy_path = _policy_path(admission) if ours else None
    record = UnitPrMerge(
        work_unit_id=unit.id,
        repository=admission.target_repository,
        pr_number=number,
        head_sha=head_sha,
        status=status,
        reason_code=reason_code,
        merge_commit_sha=merge_commit_sha,
        github_status=github_status,
        idempotency_key=command.idempotency_key,
    )
    session.add(record)
    session.flush()
    event = _write_event(
        session,
        command,
        unit,
        {
            "pr_merge_record_id": str(record.id),
            "work_package_revision_id": str(revision.id),
            "repository": admission.target_repository,
            "pr_number": number,
            "head_sha": head_sha,
            "status": status,
            "reason_code": reason_code,
            "merge_commit_sha": merge_commit_sha,
            "authority_fingerprint": unit.authority_fingerprint,
            # The estate's answer at this act: the post-landing watch reads it, and App Brain is
            # not asked again for what this landing was. Only an act of ours carries it.
            "landing_inert": ours and admission.landing_inert,
            # What lifted the human requirement, kept so a row settled later can say so.
            "policy_path": policy_path,
            # Beside the fingerprint on purpose: the override's attribution is inherited from the
            # human approval bound to it, so the reason and the approval it rests on belong in one
            # row. `unit_pr_merge` carries no free-form column and this act writes a row only when
            # it acted, so every recordable moment has this event.
            "change_window_override": override_record(
                command.change_window_override,
                applied=admission.change_window_override_applied,
                authority_approval_id=unit.authority_approval_id,
                authority_fingerprint=unit.authority_fingerprint,
            ),
        },
        key=f"{command.idempotency_key}:event",
    )
    record.event_id = event.id
    if policy_path is not None and status in _LANDED:
        _record_gate_not_required(session, command, unit, policy_path)
    session.flush()
    return record


def _policy_path(admission: MergeAdmission) -> dict[str, Any] | None:
    """What lifted the human requirement, or `None` when a bound approval met the term."""
    if not admission.authority_recognised_by:
        return None
    return {
        "recognised_by": list(admission.authority_recognised_by),
        "policy_version": admission.authority_policy_version,
        "policy_source": admission.authority_policy_source,
    }


def _write_event(
    session: Session,
    command: MergeCommand,
    unit: WorkUnit,
    payload: Mapping[str, Any],
    *,
    key: str,
) -> Event:
    """One event about a unit's row. Its action is the payload's status."""
    event = Event(
        actor_id=command.actor.actor_id,
        action=f"pr_merge.{payload['status']}",
        subject_type="work_unit",
        subject_id=unit.id,
        from_state=unit.state,
        to_state=unit.state,
        payload=dict(payload),
        correlation_id=uuid.uuid4(),
        idempotency_key=_unspent(session, key),
    )
    session.add(event)
    session.flush()
    return event


def _unspent(session: Session, key: str) -> str:
    """The key this event carries. A removed row's event keeps the row's key, in a table that is
    unique on it and append-only, and a caller's key names the unit, so its next request carries
    it. Without a key of its own that request's event would be an unhandled 500 after a landing."""
    spent = session.scalar(select(Event.id).where(Event.idempotency_key == key))
    return key if spent is None else f"{key}:{uuid.uuid4()}"


def _record_gate_not_required(
    session: Session, command: MergeCommand, unit: WorkUnit, policy_path: Mapping[str, Any]
) -> None:
    """The trace dispatch and the claim leave when policy lifted the requirement, at the merge.

    Not an approval: `unit.authority_approval_id` stays null, and the event names the patterns, the
    artifact version and the fingerprint they recognised, as they stood when the call was made.
    """
    session.add(
        Event(
            actor_id=command.actor.actor_id,
            action="authority.human_gate_not_required",
            subject_type="work_unit",
            subject_id=unit.id,
            from_state=unit.state,
            to_state=unit.state,
            payload={
                "at": "merge",
                **policy_path,
                "authority_fingerprint": unit.authority_fingerprint,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=_unspent(session, f"{command.idempotency_key}:authority-gate"),
        )
    )


class GitHubPullRequests:
    """The real gateway. Reads one pull request and asks for one landing; nothing else.

    Holds a token PROVIDER rather than a token, for the reason the workflow client does: an
    installation token expires within the hour and a long-lived process would otherwise carry a
    dead one. Nothing here logs, formats or re-raises key material.
    """

    def __init__(self, token_provider, *, timeout: float = 15.0) -> None:
        self._token_provider = token_provider
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        try:
            token = self._token_provider()
        except GitHubAppTokenError as error:
            raise GitHubGatewayError(f"app_token_mint:{error.code}") from error
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def read_pull_request(self, *, repository: str, number: int) -> PullRequestState:
        url = f"{GITHUB_API_URL}/repos/{repository}/pulls/{number}"
        try:
            response = httpx.get(url, headers=self._headers(), timeout=self._timeout)
        # `InvalidURL` is not an `httpx.RequestError` (nor an `HTTPError`) -- the same gap this
        # branch closed one module over. `repository` is an operator-authored string interpolated
        # into the URL path, so a stray character in it would otherwise escape as a bare 500.
        except (httpx.RequestError, httpx.InvalidURL) as error:
            raise GitHubGatewayError(f"request_error:{error.__class__.__name__}") from error
        if response.status_code != 200:
            raise GitHubGatewayError("read_status", response.status_code)
        try:
            body = response.json()
        except ValueError as error:
            raise GitHubGatewayError("read_response_invalid") from error
        return _state_from_body(body)

    def pull_request_files(self, *, repository: str, number: int) -> tuple[ChangedFile, ...]:
        """Every page of the file list; a failed or unrecognised page raises, never a short list."""
        url = f"{GITHUB_API_URL}/repos/{repository}/pulls/{number}/files"
        files: list[ChangedFile] = []
        for page in range(1, MAX_FILE_PAGES + 1):
            try:
                response = httpx.get(
                    url,
                    headers=self._headers(),
                    params={"per_page": FILES_PER_PAGE, "page": page},
                    timeout=self._timeout,
                )
            except (httpx.RequestError, httpx.InvalidURL) as error:
                raise GitHubGatewayError(f"request_error:{error.__class__.__name__}") from error
            if response.status_code != 200:
                raise GitHubGatewayError("files_status", response.status_code)
            try:
                body = response.json()
            except ValueError as error:
                raise GitHubGatewayError("files_response_invalid") from error
            if not isinstance(body, list):
                raise GitHubGatewayError("files_response_invalid")
            files.extend(_file_from_entry(entry) for entry in body)
            if len(body) < FILES_PER_PAGE:
                break
        return tuple(files)

    def submit_merge(self, *, repository: str, number: int, head_sha: str) -> MergeOutcome:
        """PUT the landing, NAMING the head the criteria were adjudicated at.

        `sha` is the load-bearing parameter: the remote refuses when the pull request has moved,
        which closes the window between deciding and doing atomically and without this side having
        to have observed the move.
        """
        url = f"{GITHUB_API_URL}/repos/{repository}/pulls/{number}/merge"
        try:
            response = httpx.put(
                url,
                headers=self._headers(),
                json={"sha": head_sha, "merge_method": "squash"},
                timeout=self._timeout,
            )
        # `InvalidURL` is not an `httpx.RequestError` (nor an `HTTPError`) -- the same gap this
        # branch closed one module over. `repository` is an operator-authored string interpolated
        # into the URL path, so a stray character in it would otherwise escape as a bare 500.
        except (httpx.RequestError, httpx.InvalidURL) as error:
            raise GitHubGatewayError(f"request_error:{error.__class__.__name__}") from error
        if response.status_code != 200:
            raise GitHubGatewayError("merge_status", response.status_code)
        try:
            body = response.json()
        except ValueError as error:
            raise GitHubGatewayError("merge_response_invalid") from error
        landed = bool(body.get("merged")) if isinstance(body, dict) else False
        commit = body.get("sha") if isinstance(body, dict) else None
        return MergeOutcome(
            landed=landed,
            commit_sha=commit if isinstance(commit, str) else None,
            status_code=response.status_code,
        )


def _state_from_body(body: Any) -> PullRequestState:
    """The remote's answer, read defensively about a shape this repository does not own.

    Anything missing or of the wrong type reads as NOT open and NOT landed against a base that is
    not the default branch — every unrecognised shape falls to a refusal rather than to the
    nearest recognisable thing.
    """
    if not isinstance(body, dict):
        raise GitHubGatewayError("read_response_invalid")
    base = body.get("base")
    base = base if isinstance(base, dict) else {}
    repo = base.get("repo")
    repo = repo if isinstance(repo, dict) else {}
    base_ref = base.get("ref")
    default_branch = repo.get("default_branch")
    if not isinstance(base_ref, str) or not isinstance(default_branch, str):
        raise GitHubGatewayError("read_response_invalid")
    head = body.get("head")
    head = head if isinstance(head, dict) else {}
    head_repo = head.get("repo")
    head_repo = head_repo if isinstance(head_repo, dict) else {}
    landed = bool(body.get("merged"))
    return PullRequestState(
        base_ref=base_ref,
        default_branch=default_branch,
        open=body.get("state") == "open",
        landed=landed,
        head_ref=_text(head.get("ref")),
        head_repository=_text(head_repo.get("full_name")),
        head_sha=_text(head.get("sha")),
        body=_text(body.get("body")),
        changed_files=_count(body.get("changed_files")),
        merge_commit_sha=(_text(body.get("merge_commit_sha")) or None) if landed else None,
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def _file_from_entry(entry: object) -> ChangedFile:
    path = entry.get("filename") if isinstance(entry, dict) else None
    if not isinstance(entry, dict) or not isinstance(path, str) or not path:
        raise GitHubGatewayError("files_response_invalid")
    previous = entry.get("previous_filename")
    if previous is not None and not isinstance(previous, str):
        raise GitHubGatewayError("files_response_invalid")
    return ChangedFile(path, previous, entry.get("status") == "removed")
