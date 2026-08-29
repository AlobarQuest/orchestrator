"""Reading GitHub. This half is READ-ONLY -- the client refuses any method but GET.

Two things here are less obvious than they look.

**A landing is a FIRST-PARENT commit.** `GET /commits?sha=main` returns everything reachable from
the branch, so a true merge commit drags its whole side branch into the listing -- intent-packages
carries 12 such commits in its last 100. Those side commits did not land on their own; the merge
did. So the chain is walked through `parents[0]`, from the branch tip, using the `parents` the
listing already carries.

**A landing's route is read from the commit, not guessed.** A commit is a pull-request landing only
if some pull request lists it as its own landing commit; anything else was pushed straight at the
branch. That distinction is the ledger's whole subject, so it is established from GitHub rather
than inferred from a message shape.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx

from landing_ledger.model import (
    WORK_UNIT_ID,
    Check,
    FactoryClaim,
    Landing,
    PendingUpdate,
    PolicyPermission,
    RuleApplication,
    UpdateMetadata,
    WorkflowRun,
)
from landing_ledger.rules import GATE_PATH

API = "https://api.github.com"

# The pull-request author the gate's own job-level condition names.
UPSTREAM_AUTHOR = "dependabot[bot]"

# Which workflow-run events this reader will read, and they are deliberately NOT the same set for
# the two things it collects. See `_checks_and_gate` for why: a check counted from a `push` run
# would be counted twice, while the gate is matched by path and recorded rather than counted, so
# admitting its second trigger cannot double anything. `pull_request_target` is the gate's trigger
# from ADR-0035 onward; `pull_request` is every revision before it, and both must keep working
# because the ledger re-reads landings from any point in the estate's history.
CHECK_EVENTS = frozenset({"pull_request"})
GATE_EVENTS = frozenset({"pull_request", "pull_request_target"})

# The `updated-dependencies` block Dependabot writes into its commit message. This is the same
# text `dependabot/fetch-metadata` parses, so reading it here reads what the gate read.
DEPENDENCY_NAME = re.compile(r"^\s*-?\s*dependency-name:\s*(\S+)\s*$", re.MULTILINE)
UPDATE_TYPE = re.compile(r"^\s*update-type:\s*(\S+)\s*$", re.MULTILINE)

# The trailers factory-runner writes into its COMMIT MESSAGE (`factory_runner/cli.py`). The pull
# request's BODY carries the same two values plus the authority fingerprint -- and a body is
# editable after the landing, so reading it would let an unchanged reality re-encode to different
# facts on a later pass and conflict. The commit message cannot change. Same source, and the same
# reasoning, as the Dependabot trailers above.
SDS_UNIT = re.compile(rf"^\s*SDS-Unit:\s*({WORK_UNIT_ID})\s*$", re.MULTILINE)
SDS_PACKAGE_REVISION = re.compile(r"^\s*SDS-Package-Rev:\s*(\d+)\s*$", re.MULTILINE)

# ADR-0019 increment 5b. The trailers the orchestrator writes when it lands a pull request that has
# no work unit -- a change the estate routed through its change record. Spelled here as literals
# rather than imported from the writer, deliberately: this program imports nothing from the
# orchestrator (its isolation test says so), and a shared constant would be an import that does
# not exist. Both sides carry a test naming the literal, so a rename on one side is a red test
# rather than a landing silently recorded with no basis.
SDS_CHANGE_RECORD = re.compile(r"^\s*SDS-Change-Record:\s*(\d+)\s*$", re.MULTILINE)
SDS_POLICY_VERSION = re.compile(r"^\s*SDS-Policy-Version:\s*(\d+)\s*$", re.MULTILINE)


class LedgerError(RuntimeError):
    pass


class ForbiddenMethodError(LedgerError):
    """The reader attempted something other than a read."""


class GitHubReader:
    def __init__(
        self,
        *,
        token: str,
        base_url: str = API,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "landing-ledger/1 (+AlobarQuest/orchestrator)",
            },
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(self, path: str, **params: Any) -> Any:
        return self._request(path, **params)

    def _request(self, path: str, **params: Any) -> Any:
        if not path.startswith("/"):
            raise ForbiddenMethodError(f"the reader may not fetch {path}")
        try:
            response = self._client.request("GET", path, params=params or None)
        except httpx.HTTPError as error:
            # UNREACHABLE is not the same as UNHEALTHY, and only one of them has a status code.
            # A refused connection, a DNS failure or a timeout raises here, before any response
            # exists, so it has to become the reader's own error or it escapes the pass entirely
            # -- which is the one thing a recorder must never do. Type name only: an exception
            # from a client carries the request, and a diagnostic that prints what it was given
            # is how a value that should not be in a transcript gets into one.
            raise LedgerError(
                f"github is unreachable for GET {path}: {type(error).__name__}"
            ) from (error)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise LedgerError(f"github rejected GET {path}: {response.status_code}")
        return response.json()


def first_parent_chain(commits: list[dict[str, Any]], tip: str) -> list[dict[str, Any]]:
    """The branch's own commits, in order, dropping everything a merge brought with it.

    Walks `parents[0]` from the tip through whatever the listing contains and stops when the next
    link is absent -- which is the page boundary, not the end of history.
    """
    by_sha = {commit["sha"]: commit for commit in commits}
    chain: list[dict[str, Any]] = []
    cursor: str | None = tip
    while cursor is not None and cursor in by_sha:
        commit = by_sha[cursor]
        chain.append(commit)
        parents = commit.get("parents") or []
        cursor = parents[0]["sha"] if parents else None
    return chain


def update_metadata(message: str, head_ref: str | None) -> UpdateMetadata | None:
    """Dependabot's own metadata, or nothing. Never derived from a title.

    The ecosystem is the second segment of the branch name because that is where fetch-metadata
    takes it from -- which is why it reads `github_actions` with an underscore while
    `dependabot.yml` spells it with a hyphen.

    AN ABSENT `update-type` NO LONGER DISCARDS THE REST, and that is the substantive half of this
    function. The gate reads three outputs independently; a requirement range omits one of them
    and carries the other two, so returning None for the whole structure lost an ecosystem the
    gate itself had. Present-with-`None` and absent are therefore two different answers now, and
    consumers must read them as two: `None` means the update bot declared no delta, while no
    metadata at all means nothing here could be read.

    `None` IS WHAT DEPENDABOT DECLARED, NOT NECESSARILY WHAT THE GATE SAW. From `fetch-metadata`
    v3.1.0 -- the revision blob 3457db3c pins -- the action DERIVES an update type when the
    trailer states none, by scraping a version pair out of `update <name> requirement from A to B`
    and passing it to `calculateUpdateType`. v2 had no such regex. So on a requirement range the
    gate may report `semver-patch` while this reader honestly reports nothing, because it reads
    the commit the bot wrote rather than a title. That divergence is INERT under 3457db3c, whose
    condition reads no update type at all -- and it is exactly what a future revision keyed on
    update types would be transcribed against wrongly. Read the pinned action before writing such
    a transcription.
    """
    name = DEPENDENCY_NAME.search(message)
    if name is None:
        return None
    kind = UPDATE_TYPE.search(message)
    segments = (head_ref or "").split("/")
    ecosystem = segments[1] if len(segments) > 2 and segments[0] == "dependabot" else None
    return UpdateMetadata(
        dependency=name.group(1),
        ecosystem=ecosystem,
        update_type=kind.group(1) if kind is not None else None,
    )


def factory_claim(message: str) -> FactoryClaim | None:
    """The work unit a landing commit says it implements, or nothing.

    Read from the landing commit, falling back to the pull request's own head -- the same
    arrangement, and the same reason, as `update_metadata` twelve lines up. A first draft had no
    fall-back, on the grounds that the orchestrator lands with `merge_method: "squash"` and a
    squash carries the branch's messages through. The squash BODY is not the orchestrator's to
    decide: it sends no `commit_message`, so what the landing commit contains is governed by the
    repository's own `squash_merge_commit_message` setting, which anyone can change in a web form.
    All eight repositories the ledger covers are `COMMIT_MESSAGES` today, measured -- but a setting
    is not a literal in a merge call, and the failure it would cause is silent: no trailer, no
    claim, no basis, and a factory landing recorded as `unattributed`, which no detector reads.

    The revision is optional and the unit id is not: the unit id is what the audit resolves, and
    a claim without one selects nothing to check.
    """
    unit = SDS_UNIT.search(message)
    if unit is None:
        return None
    revision = SDS_PACKAGE_REVISION.search(message)
    return FactoryClaim(
        work_unit=unit.group(1),
        package_revision=int(revision.group(1)) if revision else None,
    )


def policy_permission(message: str) -> PolicyPermission | None:
    """The change record a landing commit says permitted it, or nothing.

    Read from the landing commit, falling back to the pull request's head exactly as the factory
    claim is -- and here the fall-back is less likely to be needed, because the orchestrator sends
    an explicit body rather than letting the repository's own setting compose one. Less likely is
    not never: a landing performed by any other route would have whatever body that route wrote.

    BOTH trailers or nothing. A half-read claim would name a record with no version to re-evaluate
    it under, which is a basis that cannot be checked wearing the name of one that can.
    """
    record = SDS_CHANGE_RECORD.search(message)
    version = SDS_POLICY_VERSION.search(message)
    if record is None or version is None:
        return None
    return PolicyPermission(
        change_record=int(record.group(1)), policy_version=int(version.group(1))
    )


def _landed_pull(reader: GitHubReader, repository: str, sha: str) -> dict[str, Any] | None:
    associated = reader.get(f"/repos/{repository}/commits/{sha}/pulls") or []
    for pull in associated:
        if pull.get("merge_commit_sha") == sha and pull.get("merged_at"):
            return pull
    return None


def _checks_and_gate(
    reader: GitHubReader,
    repository: str,
    head_sha: str,
    landed_at: datetime,
) -> tuple[tuple[Check, ...], dict[str, Any] | None]:
    """Every job that had CONCLUDED at the head before the landing, plus the gate's own run.

    Two filters, each load-bearing. Only runs whose last update precedes the landing count: a run
    that concluded afterwards cannot have informed it, and saying so is the difference between
    "what had concluded" and a reconstruction. And the event must be one this reader expects --
    but WHICH events those are differs between the two things being collected, which is why
    `CHECK_EVENTS` and `GATE_EVENTS` are separate rather than one set.

    A CHECK may only come from a `pull_request` run: the same head also carries `push` runs of the
    same workflows, and counting both would double every name.

    THE GATE MAY ALSO COME FROM A `pull_request_target` RUN, and that is not a loosening of the
    rule above -- it is the same rule applied to a workflow that changed its trigger. The gate
    moved to `pull_request_target` under ADR-0035, so that it can reach the arming credential;
    keyed on `pull_request` alone this reader stops finding the gate's own run at all, `rule`
    becomes None for every landing, and `basis_of` can never return `auto_merge_rule` again --
    silently, since `audit_landing` returns empty rather than raising. The doubling hazard does
    not apply here because the gate is matched by PATH and recorded rather than counted.

    Measured 2026-08-29 on the ADR-0035 probe runs: a `pull_request_target` run is filed under the
    PULL REQUEST'S head sha, not the base branch's, so the `head_sha` query above still returns it.
    That was the open question about whether this filter was the only thing in the way.

    Job names, not workflow names -- branch protection matches jobs, and in intent-packages the
    workflow is `Quality` while the job is `Lint, type-check, and test`.
    """
    payload = reader.get(f"/repos/{repository}/actions/runs", head_sha=head_sha, per_page=100)
    runs = (payload or {}).get("workflow_runs", []) if payload else []
    checks: list[Check] = []
    gate: dict[str, Any] | None = None
    for run in runs:
        is_gate = run.get("path") == GATE_PATH
        if run.get("event") not in (GATE_EVENTS if is_gate else CHECK_EVENTS):
            continue
        updated = run.get("updated_at")
        if updated is None or datetime.fromisoformat(updated) > landed_at:
            continue
        if is_gate:
            gate = run
            continue
        jobs = reader.get(f"/repos/{repository}/actions/runs/{run['id']}/jobs", per_page=100)
        for job in (jobs or {}).get("jobs", []):
            if job.get("conclusion") is None:
                continue
            checks.append(Check(name=job["name"], conclusion=job["conclusion"], run=run["id"]))
    return tuple(sorted(checks, key=lambda c: (c.name, c.run))), gate


def _gate_revision(reader: GitHubReader, repository: str, sha: str) -> str | None:
    blob = reader.get(f"/repos/{repository}/contents/{GATE_PATH}", ref=sha)
    return blob.get("sha") if isinstance(blob, dict) else None


def read_landing(reader: GitHubReader, repository: str, base_ref: str, sha: str) -> Landing:
    """Assemble one landing. Every value comes from GitHub; nothing is reconstructed."""
    commit = reader.get(f"/repos/{repository}/commits/{sha}")
    if commit is None:
        raise LedgerError(f"github has no commit {repository}@{sha}")
    message = commit["commit"]["message"]
    # The landing's title is the landing's own subject line, fixed here before the dependency
    # metadata below may reach for a different commit's message. Read it from the API's `sha`
    # rather than the caller's argument: an abbreviated argument would otherwise become the
    # record's identity, and two spellings of one landing are two rows.
    landing_sha = str(commit["sha"])
    title = message.split("\n")[0]
    files = tuple(sorted(entry["filename"] for entry in commit.get("files") or ()))
    pull = _landed_pull(reader, repository, landing_sha)
    if pull is None:
        # A direct push. `committer` date, not `author` date: a rebase or squash leaves the
        # author date at whenever the work was written, which is not when it landed.
        return Landing(
            repository=repository,
            base_ref=base_ref,
            commit=landing_sha,
            landed_at=datetime.fromisoformat(commit["commit"]["committer"]["date"]),
            title=title,
            files=files,
            files_changed=len(files),
        )
    detail = reader.get(f"/repos/{repository}/pulls/{pull['number']}") or pull
    landed_at = datetime.fromisoformat(detail["merged_at"])
    head_sha = detail["head"]["sha"]
    checks, gate = _checks_and_gate(reader, repository, head_sha, landed_at)
    rule = None
    if gate is not None:
        revision = _gate_revision(reader, repository, landing_sha)
        if revision is not None:
            rule = RuleApplication(
                path=GATE_PATH,
                revision=revision,
                run=gate["id"],
                outcome=gate.get("conclusion") or "unknown",
            )
    # A squash carries both sets of trailers through verbatim, so the landing commit almost always
    # has them. A true merge commit does not, and neither does a squash in a repository configured
    # to write a different body -- and the authority in both cases is the pull request's own head,
    # which is what `fetch-metadata` read and where the runner wrote its own. Fetched ONCE for all
    # three, and only when something is actually missing.
    #
    # EACH ARM ASKS ITS OWN QUESTION, AND THE UPDATE ARM'S USED TO ASK A DIFFERENT ONE. It was
    # keyed on an absent `update-type`, which spells "no version delta stated" and was being read
    # as "the trailer block is not here". Those coincide for a `bump X from A to B` and diverge for
    # a requirement range, where Dependabot states a `dependency-name` and no `update-type` at all
    # -- so every range landing discarded a message that DID yield metadata in favour of a head
    # that yielded less. On orchestrator#174 that head is a 60-byte branch-update merge commit our
    # own freshness lane created, carrying no trailers, and all three update keys were dropped.
    # `update_metadata` returning None IS the question this arm means: it is None exactly when no
    # `dependency-name` could be read. A message that already answers is never replaced.
    #
    # THE INNER GUARD IS THE ONE THAT CARRIES THE CORRECTNESS; the outer condition only decides
    # whether the head is worth FETCHING. Measured by mutation: keying the outer disjunct on an
    # absent `update-type` again is behaviourally inert, because the inner guard then declines the
    # replacement anyway -- it costs one wasted request and nothing else. Deleting or widening the
    # inner guard reds a test. Both are kept: the outer one avoids a request nobody needs and
    # states the same question the other two arms state, and the inner one is the rule.
    head_ref = detail["head"].get("ref")
    claim = factory_claim(message)
    policy = policy_permission(message)
    update = update_metadata(message, head_ref)
    if claim is None or policy is None or update is None:
        head = reader.get(f"/repos/{repository}/commits/{head_sha}")
        head_message = head["commit"]["message"] if head else ""
        claim = claim or factory_claim(head_message)
        policy = policy or policy_permission(head_message)
        if update is None:
            update = update_metadata(head_message, head_ref)
    return Landing(
        repository=repository,
        base_ref=base_ref,
        commit=landing_sha,
        landed_at=landed_at,
        title=title,
        files=files,
        files_changed=len(files),
        pull_request=detail["number"],
        head_commit=head_sha,
        # `merged_by` is populated ONLY on the single-pull-request GET; the list endpoint returns
        # it as null for every row, which reads as "nobody landed this".
        landed_by=(detail.get("merged_by") or {}).get("login"),
        checks=checks,
        rule=rule,
        update=update,
        claim=claim,
        policy=policy,
    )


def branch_tip(reader: GitHubReader, repository: str, base_ref: str) -> str:
    """The branch's own head, asked for directly.

    Not `commits[0]`: that is the newest commit by committer date among everything reachable, and
    the walk needs the branch's actual tip to start from.
    """
    branch = reader.get(f"/repos/{repository}/branches/{base_ref}")
    if branch is None:
        raise LedgerError(f"github has no branch {repository}@{base_ref}")
    return str(branch["commit"]["sha"])


def landing_shas(
    reader: GitHubReader, repository: str, base_ref: str, since: str, pages: int
) -> list[str]:
    """The first-parent chain of the default branch, newest first, back to `since`."""
    collected: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        batch = reader.get(
            f"/repos/{repository}/commits", sha=base_ref, since=since, per_page=100, page=page
        )
        if not batch:
            break
        collected.extend(batch)
        if len(batch) < 100:
            break
    if not collected:
        return []
    return [
        commit["sha"]
        for commit in first_parent_chain(collected, branch_tip(reader, repository, base_ref))
    ]


def default_branch(reader: GitHubReader, repository: str) -> str:
    repo = reader.get(f"/repos/{repository}")
    if repo is None:
        raise LedgerError(f"github has no repository {repository}")
    return str(repo["default_branch"])


def current_rule_revision(reader: GitHubReader, repository: str, base_ref: str) -> str | None:
    """The blob sha of the gate at the branch tip, or None when the repository has no gate.

    None is a real answer, not an error: two repositories in this estate deliberately have no
    gate, both of them ones where landing redeploys something already serving, so they belong on
    the routed lane instead.

    Until 2026-08-15 this said THREE, and that the third could not have a gate at all -- its own
    architecture guards forbade the command the gate runs. That repository was this one, and the
    guards now carry a named exemption for the lane rather than a prohibition on it.
    """
    blob = reader.get(f"/repos/{repository}/contents/{GATE_PATH}", ref=base_ref)
    return blob.get("sha") if isinstance(blob, dict) else None


def _concluded_checks(
    reader: GitHubReader, repository: str, head_sha: str
) -> tuple[tuple[Check, ...], datetime | None]:
    """Every pull-request job that has concluded at this head, and when the last one did.

    Only `pull_request` runs, for the reason `_checks_and_gate` gives: the same head also carries
    `push` runs of the same workflows, and counting both doubles every name. The gate's own run is
    excluded too -- it reports whether the gate EXECUTED, never whether the change is sound, so
    counting it as evidence of health would let a repository look green on the strength of the
    very workflow under audit.
    """
    payload = reader.get(f"/repos/{repository}/actions/runs", head_sha=head_sha, per_page=100)
    runs = (payload or {}).get("workflow_runs", []) if payload else []
    checks: list[Check] = []
    latest: datetime | None = None
    for run in runs:
        if run.get("event") != "pull_request" or run.get("path") == GATE_PATH:
            continue
        jobs = reader.get(f"/repos/{repository}/actions/runs/{run['id']}/jobs", per_page=100)
        for job in (jobs or {}).get("jobs", []):
            if job.get("conclusion") is None:
                continue
            checks.append(Check(name=job["name"], conclusion=job["conclusion"], run=run["id"]))
            completed = job.get("completed_at")
            if completed is not None:
                finished = datetime.fromisoformat(completed)
                latest = finished if latest is None or finished > latest else latest
    return tuple(sorted(checks, key=lambda c: (c.name, c.run))), latest


def workflow_runs_at(reader: GitHubReader, repository: str, commit: str) -> tuple[WorkflowRun, ...]:
    """Every workflow run at one commit, verbatim. Judging them is `audit.branch_status`'s job.

    NOT THE CHECKS API. `GET /commits/{sha}/check-runs` answers 403 to this estate's GitHub App,
    which holds no `checks` permission -- `services/github_checks.py` reads Actions runs for
    exactly that reason and documents it. This reads the same surface.

    `pull_request` runs are excluded on the same reasoning `_concluded_checks` gives for excluding
    them: a pull-request run is a verdict about a PROPOSAL, not about the branch. In practice none
    appears here anyway, because a pull-request run's `head_sha` is the pull request's own head
    rather than the branch tip -- but relying on that staying true is a coincidence, not a design.

    The gate's own run is excluded for the reason it is excluded everywhere else in this program:
    it reports that the gate EXECUTED, never that the change is sound, so counting it as evidence
    of health would let a repository look green on the strength of the very workflow under audit.
    """
    payload = reader.get(f"/repos/{repository}/actions/runs", head_sha=commit, per_page=100)
    runs = (payload or {}).get("workflow_runs", []) if payload else []
    collected: list[WorkflowRun] = []
    for run in runs:
        path = run.get("path")
        if run.get("event") == "pull_request" or path == GATE_PATH or not path:
            continue
        updated = run.get("updated_at")
        if updated is None:
            continue
        collected.append(
            WorkflowRun(
                path=str(path),
                run=int(run["id"]),
                status=str(run.get("status") or ""),
                conclusion=run.get("conclusion"),
                updated_at=datetime.fromisoformat(updated),
            )
        )
    return tuple(sorted(collected, key=lambda entry: (entry.path, entry.run)))


def read_pending_updates(
    reader: GitHubReader, repository: str, base_ref: str
) -> tuple[PendingUpdate, ...]:
    """Every open pull request the update bot has raised against the default branch.

    `auto_merge` is populated ONLY on the single-pull-request GET, exactly as `merged_by` is on
    the landing path -- the list endpoint returns it as null for every row, which would read as
    "nothing is armed" and turn every repository into a finding.
    """
    listing = (
        reader.get(
            f"/repos/{repository}/pulls",
            state="open",
            base=base_ref,
            per_page=100,
        )
        or []
    )
    pending: list[PendingUpdate] = []
    for row in listing:
        if (row.get("user") or {}).get("login") != UPSTREAM_AUTHOR:
            continue
        detail = reader.get(f"/repos/{repository}/pulls/{row['number']}")
        if detail is None:
            raise LedgerError(f"github has no pull request {repository}#{row['number']}")
        head_sha = detail["head"]["sha"]
        head = reader.get(f"/repos/{repository}/commits/{head_sha}")
        message = head["commit"]["message"] if head else ""
        checks, last_concluded = _concluded_checks(reader, repository, head_sha)
        pending.append(
            PendingUpdate(
                repository=repository,
                number=detail["number"],
                head_commit=head_sha,
                opened_at=datetime.fromisoformat(detail["created_at"]),
                armed=detail.get("auto_merge") is not None,
                title=str(detail.get("title") or "").split("\n")[0],
                checks=checks,
                update=update_metadata(message, detail["head"].get("ref")),
                last_concluded_at=last_concluded,
            )
        )
    return tuple(pending)
