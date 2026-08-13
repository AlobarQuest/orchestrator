"""One pass, then exit. No scheduler and no loop (ADR-0002/0003).

FAILING OPEN IS THE DESIGN. This program observes and reports; it cannot revert an image,
re-point a tag, redeploy anything or move a change's state, and change-manager will not let it.
A wrong verdict here costs a wrong record, never a wrong action.

FAILING OPEN IS NOT THE SAME AS EXITING ZERO. The exit code is the whole interface a scheduled
run has, and it distinguishes four things:

    0  everything was measured and nothing was found.
    1  the tool itself failed -- a missing credential, an unhandled error.
    2  something was found. The pass worked; reality did not.
    3  some part of reality could not be read, so the answer is missing rather than clean.

3 outranks 2: an incomplete pass cannot claim it found everything there was to find. A broken
tool and an honest finding sharing one code is a collision this estate has already paid for.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import typer

from deploy_watcher.change_manager import (
    ChangeManagerClient,
    ChangeManagerError,
    RefusedError,
    observation_body,
)
from deploy_watcher.github import GitHubReader, ReadError
from deploy_watcher.model import ChangeRecord, Finding, Rollout
from deploy_watcher.observe import (
    MERGE_DIVERGENCE,
    RECHECK_DIVERGENCE,
    SETTLE_SECONDS,
    Unmeasurable,
    observe,
)
from deploy_watcher.orchestrator import (
    DEFAULT_BASE_URL as ORCHESTRATOR_URL,
)
from deploy_watcher.orchestrator import (
    OrchestratorClient,
    OrchestratorError,
)
from deploy_watcher.units import (
    UNIT_CLAIM_UNBOUND,
    UNIT_CLAIM_UNKNOWN,
    UnitLanding,
    binds,
    claimed_unit,
    is_work_unit_id,
    unit_observation,
)
from deploy_watcher.workflows import level_of

EXIT_OK = 0
EXIT_BROKEN = 1
EXIT_FINDINGS = 2
EXIT_INCOMPLETE = 3

GITHUB_TOKEN_VAR = "DEPLOY_WATCHER_GITHUB_TOKEN"
CHANGE_MANAGER_TOKEN_VAR = "DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN"
# ADR-0022. REQUIRED, exactly like the other two, and that is the correction rather than an
# oversight: an optional credential whose absence silently skips the unit-scoped observation is a
# scope that exists with no value, which this estate shipped twice in one increment and which does
# not fail -- it falls back to doing nothing while the pass reports success.
ORCHESTRATOR_TOKEN_VAR = "DEPLOY_WATCHER_ORCHESTRATOR_TOKEN"

DEFAULT_ACTOR = "deploy-watcher"

# A change record that is closed while its latest observed rollout did not succeed. Nothing
# un-settles a record -- reopening is a decision and this program records outcomes -- so the
# contradiction is REPORTED, and this is the report. Reachable when a re-run fails after a
# settlement, and when a human closed a record whose rollout then went wrong.
SETTLED_ROLLOUT_NOT_SUCCESS = "a_closed_record_whose_latest_rollout_did_not_succeed"

# The statuses that mean the record is closed. Mirrored from `app/deploy_settlement._TERMINAL` in
# change-manager, which is the party that owns them.
TERMINAL_STATUSES = frozenset({"resolved", "wontfix"})

app = typer.Typer(
    add_completion=False, help="Observe the rollout a deploying merge caused. Reports; never acts."
)


@app.callback()
def _root() -> None:
    """Present only so Typer does not collapse a lone command to the top level.

    It would, and a launcher invoking `deploy-watcher watch` would then fail on an unexpected
    argument -- which is a thing this estate has written down and paid for elsewhere.
    """


def _exit_code(*, findings: bool, incomplete: bool) -> int:
    if incomplete:
        return EXIT_INCOMPLETE
    return EXIT_FINDINGS if findings else EXIT_OK


def _require(variable: str) -> str:
    value = os.environ.get(variable, "")
    if not value:
        typer.echo(f"[broken] {variable} is not set", err=True)
        raise typer.Exit(code=EXIT_BROKEN)
    return value


def _say(line: str) -> None:
    typer.echo(line)


def _report(finding: Finding) -> None:
    _say(f"  [found]  {finding.kind}: {finding.subject} — {finding.detail}")


def _body(record: ChangeRecord, rollout: Rollout, *, now: datetime, actor: str) -> dict[str, Any]:
    run = rollout.run
    return observation_body(
        repository=record.target_repository,
        pull_request_number=record.pull_request_number,
        merge_commit_sha=rollout.merge.merge_commit_sha or "",
        merged_at=rollout.merge.merged_at,
        workflow_path=rollout.workflow_path,
        workflow_revision=rollout.workflow_revision,
        workflow_attestation=rollout.attestation,
        rollout_job=rollout.rollout_job,
        rollout_job_conclusion=rollout.rollout_job_conclusion,
        trigger_step=rollout.trigger_step,
        trigger_step_conclusion=rollout.trigger_step_conclusion,
        concurrent_run_id=rollout.concurrent_run_id,
        run_id=run.run_id if run else None,
        run_attempt=run.run_attempt if run else None,
        run_url=run.run_url if run else None,
        run_conclusion=run.conclusion if run else None,
        run_concluded_at=run.concluded_at if run else None,
        observed_at=now,
        actor=actor,
    )


@app.command()
def watch(
    change_manager_url: str = typer.Option("https://change-mgr.alobar.net"),
    orchestrator_url: str = typer.Option(ORCHESTRATOR_URL),
    actor: str = typer.Option(DEFAULT_ACTOR),
    settle_seconds: int = typer.Option(SETTLE_SECONDS),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Observe every deploying-merge change change-manager holds, and record what settled."""
    github_token = _require(GITHUB_TOKEN_VAR)
    # Required even for a dry run: without the listing there is no subject, so a dry run that
    # skipped it would exercise nothing and report a confident "0 to watch". Reading is safe --
    # the client's path allowlist is what keeps a read from becoming a write.
    cm_token = _require(CHANGE_MANAGER_TOKEN_VAR)
    orchestrator_token = _require(ORCHESTRATOR_TOKEN_VAR)

    now = datetime.now(UTC)
    found = incomplete = False

    with (
        GitHubReader(github_token) as reader,
        ChangeManagerClient(cm_token, base_url=change_manager_url) as changes,
        OrchestratorClient(orchestrator_token, base_url=orchestrator_url) as units,
    ):
        try:
            # The source is NAMED. `GET /api/items` withholds proposed sources when it is not,
            # which is increment 1's guard keeping these records away from the 04:00 executor --
            # and a watcher that forgot would receive a clean empty list and report a quiet,
            # wrong "nothing to watch".
            records = changes.deploy_changes()
        except ChangeManagerError as error:
            _say(f"[incomplete] {error}")
            raise typer.Exit(code=EXIT_INCOMPLETE) from error

        _say(f"{len(records)} deploying-merge change(s) to watch")
        for record in records:
            item_found, item_incomplete = _watch_one(
                reader,
                changes,
                units,
                record,
                now=now,
                actor=actor,
                settle_seconds=settle_seconds,
                dry_run=dry_run,
            )
            found = found or item_found
            incomplete = incomplete or item_incomplete

    raise typer.Exit(code=_exit_code(findings=found, incomplete=incomplete))


def _watch_one(
    reader: GitHubReader,
    changes: ChangeManagerClient,
    units: OrchestratorClient,
    record: ChangeRecord,
    *,
    now: datetime,
    actor: str,
    settle_seconds: int,
    dry_run: bool,
) -> tuple[bool, bool]:
    """One change: observe, record, and check the divergence the server cannot refuse.

    Returns `(found, incomplete)`. Every failure is contained here so one unreachable change
    does not discard the answers already gathered for the others.
    """
    where = f"item {record.item_id}"
    try:
        outcome = observe(
            reader,
            record.target_repository,
            record.pull_request_number,
            now=now,
            settle_seconds=settle_seconds,
        )
    except (Unmeasurable, ReadError) as error:
        _say(f"[incomplete] {where}: {error}")
        return False, True

    found = bool(outcome.findings)
    for finding in outcome.findings:
        _report(finding)
    if outcome.pending is not None:
        _say(f"  [pending] {where}: {outcome.pending}")
        return found, False
    if outcome.rollout is None:
        return found, False
    if dry_run:
        _say(f"  [dry-run] {where}: would record {outcome.subject}")
        return found, False

    try:
        recorded = changes.observe(
            record.item_id, _body(record, outcome.rollout, now=now, actor=actor)
        )
    except RefusedError as error:
        # change-manager refusing is a fact about the estate, not a broken tool: the observation
        # named a change it does not belong to, or one that has no merge to observe.
        _say(f"  [found]  change_manager_refused_the_observation: {where} — {error}")
        return True, False
    except ChangeManagerError as error:
        _say(f"[incomplete] {where}: {error}")
        return found, True
    item_status = str(recorded.get("item_status") or "")
    _say(
        f"  [recorded] {where}: verdict={recorded.get('verdict')} "
        f"production_reached={recorded.get('production_reached')} "
        f"attests={recorded.get('workflow_attestation')} record={item_status or '?'}"
    )

    unit_found, unit_incomplete = _observe_unit(reader, units, record, outcome.rollout, recorded)
    found = found or unit_found
    incomplete = unit_incomplete

    try:
        page = changes.observations(record.item_id)
    except ChangeManagerError as error:
        _say(f"[incomplete] {where}: {error}")
        return found, True
    finding = _ledger_finding(where, item_status, page)
    if finding is not None:
        _report(finding)
        return True, incomplete
    return found, incomplete


def _ledger_finding(where: str, item_status: str, page: dict[str, Any]) -> Finding | None:
    """What the change's observation history says that only a reader can act on.

    Two of them, and the second is ADR-0022's. The FIRST: the server records a second merge commit
    rather than refusing it, deliberately -- refusing would freeze whichever arrived first and make
    the true verdict unrecordable forever -- so the divergence has to be reported by somebody, and
    this is that somebody.

    The SECOND: a CLOSED record whose latest rollout did not succeed. change-manager settles a
    record on a confirmed rollout and never un-settles it, because reopening is a DECISION and
    neither party makes those -- so a re-run that fails afterwards, or a person who closed a record
    whose rollout then went wrong, has to reach a person some other way. Keyed on the server's own
    reduction rather than on re-deriving what a settlement would have decided: a second copy of
    that rule is drift this estate has paid for.
    """
    commits = page.get("merge_commits_observed") or []
    if len(commits) > 1:
        return Finding(
            MERGE_DIVERGENCE,
            where,
            f"observations exist at {len(commits)} merge commits: {', '.join(commits)}",
        )
    current = page.get("current")
    if (
        item_status in TERMINAL_STATUSES
        and isinstance(current, dict)
        and current.get("verdict") != "success"
    ):
        return Finding(
            SETTLED_ROLLOUT_NOT_SUCCESS,
            where,
            f"the record is {item_status} and its latest observed rollout "
            f"(run {current.get('run_id')} attempt {current.get('run_attempt')}) concluded "
            f"{current.get('verdict')}",
        )
    return None


def _observe_unit(
    reader: GitHubReader,
    units: OrchestratorClient,
    record: ChangeRecord,
    rollout: Rollout,
    recorded: dict[str, Any],
) -> tuple[bool, bool]:
    """Record a UNIT-SCOPED observation of this rollout, when a work unit genuinely owns it.

    ADR-0022's second half. Returns `(found, incomplete)`; both False is the ordinary answer,
    because almost every landing this watcher sees is an update the bot opened and no unit exists.

    Every step can decline and only one of them is a finding. No claim in the commit means no unit
    and nothing to say. A claim the orchestrator cannot confirm -- a unit it does not hold, or one
    whose own record does not bind this pull request and this commit to it -- IS a finding: the
    trailer is written by the party whose compliance the observation would describe, so a claim the
    durable record disagrees with is a fact about the estate. A read that fails is incomplete
    rather than either.
    """
    where = f"item {record.item_id}"
    commit = rollout.merge.merge_commit_sha
    if commit is None:  # pragma: no cover - a rollout is only recorded for a merged pull request
        return False, False
    try:
        claim = claimed_unit(reader.commit_message(record.target_repository, commit))
    except ReadError as error:
        _say(f"[incomplete] {where}: {error}")
        return False, True
    if claim is None or not is_work_unit_id(claim):
        return False, False

    try:
        history = units.unit_history(claim)
    except OrchestratorError as error:
        _say(f"[incomplete] {where}: {error}")
        return False, True
    if history is None:
        _report(Finding(UNIT_CLAIM_UNKNOWN, where, f"{commit[:12]} names work unit {claim}"))
        return True, False
    if not binds(
        history,
        repository=record.target_repository,
        pull_request_number=record.pull_request_number,
        merge_commit_sha=commit,
    ):
        _report(
            Finding(
                UNIT_CLAIM_UNBOUND,
                where,
                f"{commit[:12]} names work unit {claim}, whose history holds no record of the "
                f"orchestrator landing this pull request as that commit",
            )
        )
        return True, False

    landing = UnitLanding(
        work_unit_id=claim,
        repository=record.target_repository,
        pull_request_number=record.pull_request_number,
        merge_commit_sha=commit,
    )
    body = unit_observation(
        landing,
        rollout,
        verdict=str(recorded.get("verdict")),
        production_reached=str(recorded.get("production_reached")),
    )
    try:
        units.record_observation(body)
    except OrchestratorError as error:
        _say(f"[incomplete] {where}: {error}")
        return False, True
    _say(f"  [unit]     {where}: observed the rollout against work unit {claim}")
    return False, False


@app.command()
def backfill(
    repository: str = typer.Argument(..., help="owner/repo"),
    pages: int = typer.Option(5),
    settle_seconds: int = typer.Option(SETTLE_SECONDS),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Report what every past merge's rollout did. WRITES NOTHING.

    There are no change records for historical merges and manufacturing sixty-seven of them
    would be fabricating history, so this command has no change-manager credential and no write
    path at all. Its product is the distribution -- which is the number nobody had.
    """
    github_token = _require(GITHUB_TOKEN_VAR)
    now = datetime.now(UTC)
    conclusions: dict[str, int] = {}
    attestations: dict[str, int] = {}
    reached: dict[str, int] = {}
    findings: list[Finding] = []
    unmeasured: list[str] = []

    with GitHubReader(github_token) as reader:
        try:
            numbers = reader.merged_pull_numbers(repository, pages=pages)
        except ReadError as error:
            _say(f"[incomplete] {error}")
            raise typer.Exit(code=EXIT_INCOMPLETE) from error

        for number in sorted(numbers):
            try:
                outcome = observe(
                    reader, repository, number, now=now, settle_seconds=settle_seconds
                )
            except (Unmeasurable, ReadError) as error:
                unmeasured.append(f"#{number}: {error}")
                continue
            findings.extend(outcome.findings)
            if outcome.rollout is None:
                conclusions["<not merged>"] = conclusions.get("<not merged>", 0) + 1
                continue
            run = outcome.rollout.run
            key = run.conclusion if run else "<no run>"
            conclusions[str(key)] = conclusions.get(str(key), 0) + 1
            attestations[outcome.rollout.attestation] = (
                attestations.get(outcome.rollout.attestation, 0) + 1
            )
            if run and run.run_attempt > 1:
                conclusions["<re-run>"] = conclusions.get("<re-run>", 0) + 1
            step = outcome.rollout.trigger_step_conclusion or "<unread>"
            reached[step] = reached.get(step, 0) + 1

    summary = {
        "repository": repository,
        "merges": sum(v for k, v in conclusions.items() if k != "<re-run>"),
        "conclusions": conclusions,
        "attestations": attestations,
        "trigger_step_conclusions": reached,
        "findings": [f.kind for f in findings],
        "unmeasured": unmeasured,
    }
    if as_json:
        _say(json.dumps(summary, indent=2, sort_keys=True))
    else:
        _say(f"=== {repository}")
        for label, table in (
            ("run conclusions", conclusions),
            ("what a green run attested", attestations),
            ("trigger step conclusions", reached),
        ):
            _say(f"  {label}: {dict(sorted(table.items()))}")
        for finding in findings:
            _report(finding)
        for line in unmeasured:
            _say(f"  [incomplete] {line}")

    raise typer.Exit(code=_exit_code(findings=bool(findings), incomplete=bool(unmeasured)))


@app.command()
def recheck(
    change_manager_url: str = typer.Option("https://change-mgr.alobar.net"),
) -> None:
    """Re-derive every recorded observation from GitHub and report where they disagree.

    This is the increment's answer to a real limitation rather than a nicety. change-manager has
    no GitHub egress, so an observation is ASSERTED by the watcher and the server cannot check
    it; every `/api/*` route shares one static bearer and `actor` is caller-declared free text,
    so the server cannot tell a watcher from anything else holding that secret either. Per-caller
    identity would fix that and is not built here. What is available is making the assertion
    re-derivable and then re-deriving it, which is what this does.

    Only STABLE facts are compared. GitHub's `updated_at` keeps moving after a run finishes, and
    a divergence detector that fires on legitimate change trains its reader to ignore it.
    """
    github_token = _require(GITHUB_TOKEN_VAR)
    cm_token = _require(CHANGE_MANAGER_TOKEN_VAR)
    found = incomplete = False

    with (
        GitHubReader(github_token) as reader,
        ChangeManagerClient(cm_token, base_url=change_manager_url) as changes,
    ):
        try:
            records = changes.deploy_changes()
        except ChangeManagerError as error:
            _say(f"[incomplete] {error}")
            raise typer.Exit(code=EXIT_INCOMPLETE) from error

        for record in records:
            try:
                page = changes.observations(record.item_id)
            except ChangeManagerError as error:
                incomplete = True
                _say(f"[incomplete] item {record.item_id}: {error}")
                continue
            for stored in page.get("observations", []):
                item_found, item_incomplete = _recheck_one(
                    reader, record.target_repository, record.item_id, stored
                )
                found = found or item_found
                incomplete = incomplete or item_incomplete
        if not found and not incomplete:
            _say("every recorded observation still matches GitHub")

    raise typer.Exit(code=_exit_code(findings=found, incomplete=incomplete))


def _recheck_one(
    reader: GitHubReader, repository: str, item_id: int, stored: dict[str, Any]
) -> tuple[bool, bool]:
    """Re-derive one stored observation from GitHub. Returns `(found, incomplete)`."""
    run_id = stored.get("run_id")
    if run_id is None:
        # "No run existed when I looked" is a statement about a moment, and a later run
        # appearing is the ordinary case rather than a contradiction -- the reduction rule
        # already prefers the row that saw one.
        return False, False
    where = f"item {item_id} run {run_id}"
    workflow_path = str(stored.get("workflow_path"))
    merge_commit = str(stored.get("merge_commit_sha"))
    try:
        seen = reader.rollout_step(
            repository,
            int(run_id),
            int(stored.get("run_attempt") or 1),
            str(stored.get("rollout_job") or ""),
            str(stored.get("trigger_step") or ""),
        )
        revision = reader.blob_revision(repository, workflow_path, merge_commit)
        # THE FIELD THE VERDICT IS DERIVED FROM. An earlier version compared the job and step
        # conclusions and the workflow revision and stopped -- so a row whose `run_conclusion`
        # was a lie re-checked clean, and the command's own docstring claimed it re-derived the
        # assertion that matters. It is the assertion that matters.
        runs = reader.runs_at_head(repository, workflow_path, merge_commit)
    except ReadError as error:
        _say(f"[incomplete] {where}: {error}")
        return False, True
    if revision is None:
        # The file is gone, or the token can no longer read it. That is a failure to measure,
        # not a divergence -- and fabricating "recorded facts no longer match GitHub" out of a
        # rename is the one thing a divergence detector must not do.
        _say(f"[incomplete] {where}: {workflow_path} is unreadable at {merge_commit[:8]}")
        return False, True
    job_conclusion, step_conclusion = seen if seen is not None else (None, None)
    live_run = next(
        (
            r
            for r in runs
            if r.run_id == int(run_id) and r.run_attempt == int(stored.get("run_attempt") or 1)
        ),
        None,
    )

    differences = [
        f"{label} {was!r} -> {now!r}"
        for label, was, now in (
            (
                "run conclusion",
                stored.get("run_conclusion"),
                live_run.conclusion if live_run else None,
            ),
            ("rollout job", stored.get("rollout_job_conclusion"), job_conclusion),
            ("trigger step", stored.get("trigger_step_conclusion"), step_conclusion),
            ("workflow revision", stored.get("workflow_revision"), revision),
            # ADR-0022. THE FIELD A SETTLEMENT RESTS ON, and it was absent from this list while
            # `deploy_settlement.py` and `app/scopes.py` both justified an `observe` credential
            # moving a status on the grounds that this command re-derives it. It is a pure function
            # of the revision, which is already re-derived one line up, so the claim was one line
            # from being true and was not. `workflow_attestation` is caller-supplied and unlocks
            # two of the settlement's three clauses -- it is also what `production_reached_for`
            # reads as `classified`.
            ("workflow attestation", stored.get("workflow_attestation"), level_of(revision)),
        )
        if was != now
    ]
    if differences:
        _report(Finding(RECHECK_DIVERGENCE, where, "; ".join(differences)))
        return True, False
    return False, False
