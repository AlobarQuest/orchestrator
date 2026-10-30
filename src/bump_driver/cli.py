"""One bump-driver pass: decompose bump intakes, dispatch ready units, land finished ones.

It acts only on a standing dependency-update package in a repository the member record grants
`dependency_updates`. A `work` record is read while `approved`, and while `resolved` too, because
`work_watcher` retires a record as soon as its units complete and landing the unit comes after that.
The acts, in order, each on a fresh read of the unit:

1. **Decompose.** An approved record's revision with no units and no proposal gets the generator's
   proposal against this lane's mirror, posted as SYSTEM. Policy approves it in the proposal's own
   transaction, or the proposal waits for a person and `bump-decompose-refused` fires. A proposal a
   person rejected, sent back or superseded is never proposed over.
2. **Dispatch** one ready bump unit per repository, and none where a run is live. A run the
   orchestrator started and the runner hasn't claimed is live too.
3. **Merge** a completed bump unit whose bound pull request is open. The admission answer is read
   first, so the off switch can be told apart: `merge_policy_path_off` is waiting only as the sole
   refusal.

A unit that ended `failed` or `revision_required` raises `bump-unit-failed` and is left there.
Nothing here retries a unit: requeueing is a SYSTEM act this lane doesn't hold, and a person
decides. This lane never touches a unit whose envelope isn't a dependency update, a rotation's
above all.

EXIT CODES:
  0  every bump moved, or waits on something that clears by itself.
  1  the tool itself failed (a missing credential, an unusable URL).
  2  something was found that a person decides: a refused decomposition, a declined or superseded
     proposal, a refused dispatch or merge, or a failed unit.
  3  a record, a unit or a pull request could not be read, or alert-hub could not be told, so the
     answer is missing rather than clean.
3 outranks 2: an incomplete pass cannot claim it found everything there was to find.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from bump_driver.alerts import (
    TOKEN_ENV,
    UNREAD,
    Outcome,
    bump_failed_key,
    decompose_refused_key,
    sync_alerts,
)
from bump_driver.client import (
    CallError,
    ChangeManagerReader,
    GitHub,
    Orchestrator,
    UnusableEndpointError,
)
from bump_driver.decompose import (
    PROFILE,
    DecomposeRefused,
    Generator,
    factory_binary,
    profile_fields,
    tooling_of,
)
from bump_driver.mirror import MirrorFailed, MirrorRefused, Mirrors, state_directory
from sds_members.client import MembersUnreadable, read_members
from sds_members.populations import granted as granted_repositories

__all__ = ["FOUND", "INCOMPLETE", "Outcome", "bump_failed_key", "decompose_refused_key"]

FOUND, INCOMPLETE = 2, 3
CHANGE_MANAGER_URL: Final = "https://change-mgr.alobar.net"
ORCHESTRATOR_URL: Final = "https://sds.alobar.net"
# The identity this lane proposes as: the only proposer whose refusal it alerts on.
SYSTEM_ACTOR: Final = "orchestrator-system"
BUMP_CHANGE_CLASS: Final = "dependency-update"
ROTATION_UNIT: Final = "rotation_unit"
# not-a-vocabulary: change-manager statuses this lane reads, and work-unit states it reads below;
# each is a copy of another service's word, used only to select.
LIVE_RECORD_STATUSES: Final = frozenset({"approved", "resolved"})
DECOMPOSED_STATUS: Final = "approved"
LIVE_RUN_STATES: Final = frozenset({"claimed", "executing"})  # not-a-vocabulary: as above
ENDED_STATES: Final = frozenset({"failed", "revision_required"})  # not-a-vocabulary: as above
READY: Final = "ready"
COMPLETED: Final = "completed"
# A dispatch the orchestrator refused to start for now; nothing is wrong with the unit.
WAITING_DISPATCH: Final = frozenset(  # not-a-vocabulary: dispatch refusal codes this lane reads
    {"dispatch_disabled", "outside_change_window"}
)
DISPATCHED: Final = "dispatched"
DISPATCHED_ACTION: Final = "dispatch.dispatched"
DISPATCH_ACTION_PREFIX: Final = "dispatch."
OFF_SWITCH: Final = "merge_policy_path_off"
LANDED: Final = frozenset({"merged", "already_merged"})  # not-a-vocabulary: as above
VERSION_CONFLICT: Final = "version_conflict"
FINDINGS: Final = frozenset(  # not-a-vocabulary: this lane's own report words
    {"declined", "refused", "superseded", "failed"}
)


@dataclass(frozen=True)
class _Bump:
    """One registered revision this lane may act on: its record and the member's spelling."""

    record_id: int
    record_status: str
    revision_id: str
    repository: str
    intake: dict[str, Any]

    @property
    def subject(self) -> str:
        return f"record {self.record_id} ({self.intake.get('package_id')})"


Mirror = Callable[[str], Path]
Generate = Callable[[dict[str, Any], str, Path], dict[str, Any]]


def run_pass(
    records: Sequence[dict[str, Any]],
    orchestrator: Orchestrator,
    github: GitHub,
    granted: Mapping[str, str],
    mirror: Mirror,
    generate: Generate,
    *,
    dry_run: bool = False,
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    bumps = _bumps(records, orchestrator, granted, outcomes)
    for bump in bumps:
        if bump.record_status == DECOMPOSED_STATUS:
            _isolated(
                bump,
                outcomes,
                lambda b: _decompose(b, orchestrator, mirror, generate, dry_run),
            )
    # Read after decomposing, so a unit policy just approved is dispatched in this pass.
    units = _units(bumps, orchestrator, outcomes)
    outcomes.extend(_dispatch_all(units, orchestrator, dry_run))
    for bump, row in units:
        if row.get("state") == COMPLETED:
            _isolated(bump, outcomes, lambda b, r=row: _merge(b, r, orchestrator, github, dry_run))
        elif row.get("state") in ENDED_STATES:
            _isolated(bump, outcomes, lambda b, r=row: _failed(b, r, orchestrator))
    return outcomes


def _isolated(
    bump: _Bump,
    outcomes: list[Outcome],
    act: Callable[[_Bump], Outcome | None],
) -> None:
    """One subject's failure to read never stops the pass."""
    try:
        outcome = act(bump)
    except (CallError, MirrorFailed, KeyError, TypeError, ValueError) as error:
        outcome = Outcome(bump.subject, UNREAD, str(error), bump.repository)
    if outcome is not None:
        outcomes.append(outcome)


def _selected(record: Mapping[str, Any], granted: Mapping[str, str]) -> bool:
    """A live bump record in a granted repository whose package id reads as a standing bump's."""
    repository = record.get("target_repository")
    return (
        record.get("status") in LIVE_RECORD_STATUSES
        and record.get("change_class") == BUMP_CHANGE_CLASS
        and isinstance(repository, str)
        and repository.casefold() in granted
        and tooling_of(str(record.get("package_id")), repository) is not None
    )


def _is_standing_bump(intake: Mapping[str, Any], named: Any, repository: str) -> bool:
    """The revision the record names, of a standing dependency-update package for its repository."""
    fields = profile_fields(intake)
    target = fields.get("target_repo")
    return (
        intake.get("revision") == named
        and intake.get("profile") == PROFILE
        and fields.get("standing") is True
        and isinstance(target, str)
        and target.casefold() == repository.casefold()
    )


def _bumps(
    records: Sequence[dict[str, Any]],
    orchestrator: Orchestrator,
    granted: Mapping[str, str],
    outcomes: list[Outcome],
) -> list[_Bump]:
    bumps: list[_Bump] = []
    for record in records:
        if not _selected(record, granted):
            continue
        repository = granted[str(record["target_repository"]).casefold()]
        try:
            for revision_id in orchestrator.work(int(record["id"])).get("revision_ids") or []:
                intake = orchestrator.intake(str(revision_id))
                if _is_standing_bump(intake, record.get("package_revision"), repository):
                    bumps.append(
                        _Bump(
                            int(record["id"]),
                            str(record["status"]),
                            str(revision_id),
                            repository,
                            intake,
                        )
                    )
        except (CallError, KeyError, TypeError, ValueError) as error:
            outcomes.append(Outcome(f"record {record.get('id')}", UNREAD, str(error), repository))
    return bumps


def _units(
    bumps: Sequence[_Bump], orchestrator: Orchestrator, outcomes: list[Outcome]
) -> list[tuple[_Bump, dict[str, Any]]]:
    """Each bump's units as the record's work lists them, one read per record."""
    work: dict[int, dict[str, Any] | None] = {}
    rows: list[tuple[_Bump, dict[str, Any]]] = []
    for bump in bumps:
        if bump.record_id not in work:
            try:
                work[bump.record_id] = orchestrator.work(bump.record_id)
            except CallError as error:
                work[bump.record_id] = None
                outcomes.append(Outcome(bump.subject, UNREAD, str(error), bump.repository))
        answer = work[bump.record_id] or {}
        rows.extend(
            (bump, row)
            for row in answer.get("units") or []
            if str(row.get("revision_id")) == bump.revision_id
        )
    return rows


def _decompose(
    bump: _Bump,
    orchestrator: Orchestrator,
    mirror: Mirror,
    generate: Generate,
    dry_run: bool,
) -> Outcome | None:
    work = orchestrator.work(bump.record_id)
    if any(str(u.get("revision_id")) == bump.revision_id for u in work.get("units") or []):
        return None
    existing = _existing(bump, orchestrator.proposals(bump.revision_id))
    if existing is not None:
        return existing
    if dry_run:
        return Outcome(bump.subject, "would-propose", repository=bump.repository)
    try:
        body = generate(bump.intake, bump.repository, mirror(bump.repository))
    except (DecomposeRefused, MirrorRefused) as error:
        return _refused(bump, str(error))
    try:
        answer = orchestrator.propose(
            bump.revision_id,
            {**body, "idempotency_key": f"bump-driver:{bump.revision_id}", "expected_version": 0},
        )
    except CallError as error:
        if error.status is None or error.status >= 500:
            raise
        # The orchestrator read the proposal and refused it, for a reason a person resolves.
        return _refused(bump, str(error))
    if answer.get("state") == "approved":
        return Outcome(
            bump.subject,
            "approved",
            f"proposal {answer.get('id')} approved by policy",
            bump.repository,
        )
    return _refused(bump, _not_applied(answer))


def _existing(bump: _Bump, proposals: Sequence[dict[str, Any]]) -> Outcome | None:
    """What a revision's proposals already say, or ``None`` when it has none."""
    waiting = [p for p in proposals if p.get("state") == "proposed"]
    if any(p.get("proposed_by") == SYSTEM_ACTOR for p in waiting):
        own = next(p for p in waiting if p.get("proposed_by") == SYSTEM_ACTOR)
        return _refused(bump, _not_applied(own))
    if waiting:
        return Outcome(
            bump.subject,
            "awaiting-approval",
            "a person's proposal waits on /review",
            bump.repository,
        )
    states = [str(p.get("state")) for p in proposals]
    if "approved" in states:
        # Approval creates the units in its own transaction, so an approved proposal with none is
        # one a person superseded (ADR-0052): theirs to replace, never this lane's.
        return Outcome(
            bump.subject,
            "superseded",
            "its approved decomposition was superseded; a person replaces it",
            bump.repository,
        )
    if states:
        return Outcome(
            bump.subject,
            "declined",
            f"a person {states[-1].replace('_', ' ')} its proposal; it is never proposed again "
            "by machine",
            bump.repository,
        )
    return None


def _not_applied(proposal: Mapping[str, Any]) -> str:
    refusal = proposal.get("policy_not_applied")
    if not isinstance(refusal, Mapping):
        return f"proposal {proposal.get('id')} waits for a person: policy didn't approve it"
    detail = ", ".join(str(item) for item in refusal.get("detail") or [])
    return f"policy didn't approve it: {refusal.get('reason')}" + (f" ({detail})" if detail else "")


def _refused(bump: _Bump, detail: str) -> Outcome:
    return Outcome(
        bump.subject,
        "refused",
        detail,
        bump.repository,
        decompose_refused_key(bump.repository, bump.revision_id),
    )


def _is_bump_unit(unit: Mapping[str, Any], repository: str) -> bool:
    """Read from the unit's own envelope: a rotation unit is never one, whatever else it says."""
    authority = unit.get("authority")
    if not isinstance(authority, Mapping) or authority.get("change_class") != BUMP_CHANGE_CLASS:
        return False
    constraints = authority.get("constraints")
    if not isinstance(constraints, Mapping) or ROTATION_UNIT in constraints:
        return False
    target = constraints.get("target_repository")
    return isinstance(target, str) and target.casefold() == repository.casefold()


def _pending_run(history: Sequence[Mapping[str, Any]]) -> bool:
    """A run the orchestrator started and the runner hasn't claimed: it is starting now."""
    pending = False
    for event in history:
        if event.get("action") == DISPATCHED_ACTION:
            pending = True
        elif event.get("to_state") == "claimed":
            pending = False
    return pending


def _dispatch_all(
    units: Sequence[tuple[_Bump, dict[str, Any]]], orchestrator: Orchestrator, dry_run: bool
) -> list[Outcome]:
    busy = {b.repository for b, row in units if row.get("state") in LIVE_RUN_STATES}
    outcomes: list[Outcome] = []
    ready = [(b, r) for b, r in units if r.get("state") == READY]
    for bump, row in sorted(ready, key=lambda pair: str(pair[1].get("unit_id"))):
        _isolated(bump, outcomes, lambda b, r=row: _dispatch(b, r, orchestrator, busy, dry_run))
    return outcomes


def _dispatch_key(unit_id: str, version: Any, history: Sequence[Mapping[str, Any]]) -> str:
    """One key per version, plus one per attempt the orchestrator didn't start: the record of a
    skip holds its key and moves no version, so reusing the key would replay that skip for ever."""
    unstarted = sum(
        1
        for event in history
        if str(event.get("action")).startswith(DISPATCH_ACTION_PREFIX)
        and event.get("action") != DISPATCHED_ACTION
    )
    key = f"bump-driver:dispatch:{unit_id}:{version}"
    return f"{key}:{unstarted}" if unstarted else key


def _dispatch(
    bump: _Bump,
    row: Mapping[str, Any],
    orchestrator: Orchestrator,
    busy: set[str],
    dry_run: bool,
) -> Outcome | None:
    unit_id = str(row["unit_id"])
    unit = orchestrator.unit(unit_id)
    if not _is_bump_unit(unit, bump.repository) or unit.get("state") != READY:
        return None
    subject = f"unit {unit.get('unit_key')} ({bump.repository})"
    if bump.repository in busy:
        return Outcome(subject, "busy", "one run per repository", bump.repository)
    history = orchestrator.history(unit_id)
    if _pending_run(history):
        busy.add(bump.repository)
        return Outcome(subject, "running", "its run hasn't claimed yet", bump.repository)
    if dry_run:
        busy.add(bump.repository)
        return Outcome(subject, "would-dispatch", repository=bump.repository)
    version = unit["version"]
    try:
        answer = orchestrator.dispatch(
            unit_id,
            {
                "idempotency_key": _dispatch_key(unit_id, version, history),
                "expected_version": version,
            },
        )
    except CallError as error:
        return _call_refused(subject, error, bump.repository)
    status, reason = answer.get("status"), answer.get("reason_code")
    if status == DISPATCHED:
        busy.add(bump.repository)
        return Outcome(subject, DISPATCHED, repository=bump.repository)
    if reason in WAITING_DISPATCH:
        return Outcome(subject, "waiting", str(reason), bump.repository)
    return Outcome(subject, "refused", f"dispatch {status}: {reason}", bump.repository)


def _call_refused(subject: str, error: CallError, repository: str) -> Outcome:
    """A version that moved waits for the next pass's read; a refusal is reported; a call nothing
    answered is unread."""
    if error.code == VERSION_CONFLICT:
        return Outcome(subject, "waiting", "the unit moved under this pass", repository)
    if error.status is None or error.status >= 500:
        return Outcome(subject, UNREAD, str(error), repository)
    return Outcome(subject, "refused", str(error), repository)


def _merge(
    bump: _Bump,
    row: Mapping[str, Any],
    orchestrator: Orchestrator,
    github: GitHub,
    dry_run: bool,
) -> Outcome | None:
    unit_id = str(row["unit_id"])
    unit = orchestrator.unit(unit_id)
    number = unit.get("pr_number")
    if not _is_bump_unit(unit, bump.repository) or not isinstance(number, int):
        return None
    if not github.pull_is_open(bump.repository, number):
        return None
    subject = f"{bump.repository}#{number}"
    admission = orchestrator.merge_admission(unit_id)
    if not admission.get("satisfied"):
        refusals = [str(code) for code in admission.get("refusals") or []]
        if refusals == [OFF_SWITCH]:
            return Outcome(subject, "waiting", OFF_SWITCH, bump.repository)
        reasons = ", ".join(refusals) or "no reason given"
        return Outcome(subject, "refused", reasons, bump.repository)
    if dry_run:
        return Outcome(subject, "would-merge", repository=bump.repository)
    # Evidence writes move the version, so it is read at the last moment.
    version = orchestrator.unit(unit_id)["version"]
    try:
        answer = orchestrator.land(
            unit_id,
            {"idempotency_key": f"bump-driver:merge:{unit_id}", "expected_version": version},
        )
    except CallError as error:
        return _call_refused(subject, error, bump.repository)
    if answer.get("status") in LANDED:
        return Outcome(subject, "merged", str(answer.get("status")), bump.repository)
    return Outcome(subject, "refused", f"merge: {answer.get('reason_code')}", bump.repository)


def _failed(bump: _Bump, row: Mapping[str, Any], orchestrator: Orchestrator) -> Outcome | None:
    unit_id = str(row["unit_id"])
    unit = orchestrator.unit(unit_id)
    if not _is_bump_unit(unit, bump.repository) or unit.get("state") not in ENDED_STATES:
        return None
    return Outcome(
        f"unit {unit.get('unit_key')} ({bump.repository})",
        "failed",
        f"it ended {unit.get('state')}; nothing retries it, a person decides",
        bump.repository,
        bump_failed_key(bump.repository, unit_id),
    )


def exit_code(outcomes: Sequence[Outcome]) -> int:
    results = {outcome.result for outcome in outcomes}
    if UNREAD in results:
        return INCOMPLETE
    return FOUND if results & FINDINGS else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bump-driver",
        description="Decompose, dispatch and land standing dependency-update work.",
    )
    parser.add_argument("--dry-run", action="store_true", help="list what would be done")
    args = parser.parse_args(argv)
    environ = os.environ
    token = environ.get("ORCHESTRATOR_API_TOKEN", "")
    key_id = environ.get("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "")
    change_manager_token = environ.get("CHANGE_MANAGER_TOKEN", "")
    github_token = environ.get("BUMP_DRIVER_GITHUB_TOKEN", "")
    if not all((token, key_id, change_manager_token, github_token)):
        print(
            "FATAL: ORCHESTRATOR_API_TOKEN, its key id, CHANGE_MANAGER_TOKEN and "
            "BUMP_DRIVER_GITHUB_TOKEN are required",
            file=sys.stderr,
        )
        return 1
    alert_token = environ.get(TOKEN_ENV, "")
    if not args.dry_run and not alert_token:
        print(f"FATAL: {TOKEN_ENV} is required", file=sys.stderr)
        return 1
    base_url = environ.get("ORCHESTRATOR_API_URL", ORCHESTRATOR_URL)
    try:
        reader = ChangeManagerReader(
            base_url=environ.get("CHANGE_MANAGER_URL", CHANGE_MANAGER_URL),
            token=change_manager_token,
        )
        orchestrator = Orchestrator(base_url=base_url, credential_key_id=key_id, token=token)
        github = GitHub(token=github_token)
    except UnusableEndpointError as error:
        print(f"FATAL: {error}", file=sys.stderr)
        return 1
    home = Path(environ.get("HOME", str(Path.home())))
    state = Path(environ.get("BUMP_DRIVER_STATE_DIR", str(state_directory(home))))
    try:
        members = read_members(token=token, credential_key_id=key_id, base_url=base_url)
        granted = {
            name.casefold(): name for name in granted_repositories(members, "dependency_updates")
        }
        records = reader.work_records()
        mirrors = Mirrors(state / "mirrors", granted, token=github_token)
        outcomes = run_pass(
            records,
            orchestrator,
            github,
            granted,
            mirrors.fresh,
            Generator(factory_binary(home), _generator_environment(environ, token, mirrors)),
            dry_run=args.dry_run,
        )
    except (CallError, MembersUnreadable) as error:
        # No alert is touched: a pass that read nothing says nothing about what it remembers.
        print(f"could not list the work: {error}", file=sys.stderr)
        return INCOMPLETE
    finally:
        reader.close()
        orchestrator.close()
        github.close()
    for outcome in outcomes:
        print(f"{outcome.subject:<60}  {outcome.result:<17}  {outcome.detail}".rstrip())
    print(f"\n{len(outcomes)} bump subject(s) reported")
    code = exit_code(outcomes)
    if args.dry_run:
        return code
    problem = sync_alerts(outcomes, token=alert_token, state_dir=state)
    if problem is not None:
        print(f"alert-hub could not be told: {problem}", file=sys.stderr)
        return INCOMPLETE
    return code


def _generator_environment(
    environ: Mapping[str, str], token: str, mirrors: Mirrors
) -> dict[str, str]:
    """All `factory decompose` is given. Its reads need the SYSTEM bearer under the name it reads,
    the conformance scanner on `PYTHONPATH`, and the Brain keys, so it never asks BWS itself; its
    own `git fetch` in the mirror needs the mirror's environment."""
    return {
        "PATH": environ.get("PATH", ""),
        "HOME": environ.get("HOME", ""),
        "PYTHONPATH": environ.get("FACTORY_PYTHONPATH", ""),
        "ORCHESTRATOR_API_URL": environ.get("ORCHESTRATOR_API_URL", ORCHESTRATOR_URL),
        "ORCHESTRATOR_SYSTEM_TOKEN": token,
        "CODE_BRAIN_KEY": environ.get("CODE_BRAIN_KEY", ""),
        "INFRA_BRAIN_KEY": environ.get("INFRA_BRAIN_KEY", ""),
        **mirrors.environment(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
