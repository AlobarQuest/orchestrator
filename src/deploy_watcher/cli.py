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

EXIT_OK = 0
EXIT_BROKEN = 1
EXIT_FINDINGS = 2
EXIT_INCOMPLETE = 3

GITHUB_TOKEN_VAR = "DEPLOY_WATCHER_GITHUB_TOKEN"
CHANGE_MANAGER_TOKEN_VAR = "DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN"

DEFAULT_ACTOR = "deploy-watcher"

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

    now = datetime.now(UTC)
    found = incomplete = False

    with (
        GitHubReader(github_token) as reader,
        ChangeManagerClient(cm_token, base_url=change_manager_url) as changes,
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
    _say(
        f"  [recorded] {where}: verdict={recorded.get('verdict')} "
        f"production_reached={recorded.get('production_reached')} "
        f"attests={recorded.get('workflow_attestation')}"
    )

    try:
        page = changes.observations(record.item_id)
    except ChangeManagerError as error:
        _say(f"[incomplete] {where}: {error}")
        return found, True
    # The server records a second merge commit rather than refusing it, deliberately: refusing
    # would freeze whichever arrived first and make the true verdict unrecordable forever. So
    # the divergence has to be REPORTED by somebody, and this is that somebody.
    commits = page.get("merge_commits_observed") or []
    if len(commits) > 1:
        _report(
            Finding(
                MERGE_DIVERGENCE,
                where,
                f"observations exist at {len(commits)} merge commits: {', '.join(commits)}",
            )
        )
        return True, False
    return found, False


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
    try:
        job_conclusion, step_conclusion = reader.rollout_step(
            repository,
            int(run_id),
            int(stored.get("run_attempt") or 1),
            str(stored.get("rollout_job") or ""),
            str(stored.get("trigger_step") or ""),
        )
        revision = reader.blob_revision(
            repository,
            str(stored.get("workflow_path")),
            str(stored.get("merge_commit_sha")),
        )
    except ReadError as error:
        _say(f"[incomplete] {where}: {error}")
        return False, True

    differences = [
        f"{label} {was!r} -> {now!r}"
        for label, was, now in (
            ("rollout job", stored.get("rollout_job_conclusion"), job_conclusion),
            ("trigger step", stored.get("trigger_step_conclusion"), step_conclusion),
            ("workflow revision", stored.get("workflow_revision"), revision),
        )
        if was != now
    ]
    if differences:
        _report(Finding(RECHECK_DIVERGENCE, where, "; ".join(differences)))
        return True, False
    return False, False
