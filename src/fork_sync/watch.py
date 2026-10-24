"""`fork-sync watch`: the stuck-lane alarm. Read-only; one bounded pass, never a loop.

The lanes' dead-man pings prove only that a pass ran, so a pass that runs and holds everything
looks healthy. Three silent holds of 2026-10-21/22 were each found only because a person looked:
a sync pull request with a merge conflict, on which GitHub ran no checks; a stale change-manager
bearer in production, which held every passing sync for a day; and an exhausted GitHub rate
limit, under which every lane read 403. This pass reads each fork's lane and says whether it is
moving (ADR-0056 amendment 6).

The forks are the repositories `fork-sync onboard` registers (step 5): the `gh-actions-secret`
consumers named `APP_PRIVATE_KEY` under `fork-sync-app-private-key` in factory-runner's
`.cred-consumers.toml` on `main`, each confirmed by `.github/fork-sync.toml` on its own `main`.
That source is GitHub alone, so the pass needs nothing but the operator's `gh` login.

Every read goes through `gh_api`. Each fork costs a fixed number of reads (at most
`READS_PER_FORK`) and nothing pages, so a pass makes at most `1 + READS_PER_FORK * forks`.
The first read that fails ends the pass as `unreadable`: a rate-limited API is not read again.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from fork_sync import acknowledge
from fork_sync.fork_sync_review import (
    CONFIG_PATH,
    Config,
    ConfigError,
    manifest_version,
    next_release,
    parse_config,
)
from fork_sync.gh import CommandFailed, Runner, gh_api
from fork_sync.onboard import CI_WORKFLOW, REQUIRED_CHECKS, Refused
from fork_sync.onboarding import REGISTRY_PATH, REGISTRY_REPO

# ---- thresholds: plain ints, no off value --------------------------------------------------

NO_CHECKS_MINUTES: Final = 60
"""How long an open sync pull request may go without a completed `ci.yml` run at its head. A
run starts within seconds of a push and builds in minutes, so an hour with none means GitHub ran
nothing: a conflicted pull request gets no `pull_request` run at all (incident 1)."""
NOT_LANDING_HOURS: Final = 6
"""How long a sync pull request may stay open after both required checks passed. The inert
landing lane runs hourly, so six passes have had it; one that hasn't landed by then is held by
something outside the fork (incident 2: a stale bearer held every landing for a day)."""
SYNC_SILENCE_HOURS: Final = 36
"""How long the daily `upstream-sync.yml` may go without starting a run: one missed day, and
GitHub's 60-day disabling of an idle repository's schedules, both pass it."""
RELEASE_UNSYNCED_HOURS: Final = 36
"""How long a newer upstream release may be published with no sync pull request open: the daily
sync has had at least one run at it."""

# ---- classes and reasons -------------------------------------------------------------------

OK: Final = "ok"
WAITING_ON_PERSON: Final = "waiting_on_person"
STUCK: Final = "stuck"
NOT_ON_LANE: Final = "not_on_lane"
UNREADABLE: Final = "unreadable"

NO_CHECKS: Final = "no_checks"
NOT_LANDING: Final = "not_landing"
REVIEW_UNAVAILABLE: Final = "review_unavailable"
HELD_WITHOUT_ALERT: Final = "held_without_alert"
BUILD_FAILING: Final = "build_failing"
SYNC_FAILING: Final = "sync_failing"
NOT_SYNCING: Final = "not_syncing"

EXIT_OK: Final = 0
EXIT_UNREADABLE: Final = 2
EXIT_STUCK: Final = 3

REGISTRY_CREDENTIAL: Final = "fork-sync-app-private-key"
REGISTRY_SECRET: Final = "APP_PRIVATE_KEY"
ALERT_MARKER: Final = "<!-- fork-sync-alert:review -->"
"""`fork_sync_review.alert_body("review", ...)`'s marker; that file is copied into every fork,
so it is restated here rather than edited there, and a test holds the two equal."""
SYNC_RUN_FAILURES: Final = ("failure", "timed_out", "startup_failure")
READS_PER_FORK: Final = 8
"""Declaration, sync runs, pull requests, then either (manifest, releases) or (head commit, CI
runs, CI jobs, issues, review comment)."""


def _now() -> datetime:
    return datetime.now(UTC)


clock: Callable[[], datetime] = _now
"""Looked up at call time, so tests replace it."""


class Unreadable(RuntimeError):
    """A read failed, so this pass can't say what it would have found."""


@dataclass(frozen=True)
class Verdict:
    fork: str
    status: str
    reasons: tuple[str, ...] = ()
    detail: str = ""

    def line(self) -> str:
        reasons = f" {','.join(self.reasons)}" if self.reasons else ""
        detail = f" -- {self.detail}" if self.detail else ""
        return f"{self.status:17} {self.fork}{reasons}{detail}"


@dataclass
class Pass:
    verdicts: list[Verdict] = field(default_factory=list)
    unreadable: str | None = None
    calls: int = 0

    def exit_code(self) -> int:
        if self.unreadable is not None:
            return EXIT_UNREADABLE
        return EXIT_STUCK if any(v.status == STUCK for v in self.verdicts) else EXIT_OK

    def lines(self) -> list[str]:
        lines = [verdict.line() for verdict in self.verdicts]
        if self.unreadable is not None:
            lines.append(f"{UNREADABLE:17} {self.unreadable}")
        stuck = sum(v.status == STUCK for v in self.verdicts)
        lines.append(
            f"fork-sync watch: {len(self.verdicts)} fork(s), {stuck} stuck, "
            f"{self.calls} GitHub API call(s)"
        )
        return lines


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _age(now: datetime, text: str) -> timedelta:
    return now - _instant(text)


def _hours(delta: timedelta) -> str:
    return f"{delta.total_seconds() / 3600:.1f}h"


def forks(run: Runner) -> list[str]:
    """Every fork `onboard` registered a copy of the sync App's key for."""
    text = gh_api(run, f"repos/{REGISTRY_REPO}/contents/{REGISTRY_PATH}?ref=main", raw=True)
    if text is None:
        raise Unreadable(f"{REGISTRY_REPO} has no {REGISTRY_PATH} on main")
    try:
        credentials = {c.get("id"): c for c in tomllib.loads(text).get("credential", [])}
    except tomllib.TOMLDecodeError as error:
        raise Unreadable(f"{REGISTRY_REPO}'s {REGISTRY_PATH} does not parse: {error}") from None
    if REGISTRY_CREDENTIAL not in credentials:
        raise Unreadable(f"{REGISTRY_PATH} has no {REGISTRY_CREDENTIAL} entry")
    found: dict[str, str] = {}
    for consumer in credentials[REGISTRY_CREDENTIAL].get("consumer", []):
        repo = str(consumer.get("repo", ""))
        if consumer.get("kind") == "gh-actions-secret" and consumer.get("name") == REGISTRY_SECRET:
            found.setdefault(repo.lower(), repo)
    return sorted(found.values(), key=str.lower)


def _sync_reasons(run: Runner, fork: str, now: datetime) -> tuple[list[str], str]:
    listed = gh_api(
        run, f"repos/{fork}/actions/workflows/{acknowledge.SYNC_WORKFLOW}/runs?per_page=1"
    )
    latest = ((listed or {}).get("workflow_runs") or [None])[0]
    if latest is None:
        return [SYNC_FAILING], f"{acknowledge.SYNC_WORKFLOW} has never run"
    age = _age(now, latest["created_at"])
    if latest.get("conclusion") in SYNC_RUN_FAILURES:
        return [SYNC_FAILING], f"the latest sync concluded {latest['conclusion']}"
    if age > timedelta(hours=SYNC_SILENCE_HOURS):
        return [SYNC_FAILING], f"no sync has started for {_hours(age)}"
    return [], ""


def _latest_ci_run(run: Runner, fork: str, sha: str) -> dict[str, Any] | None:
    """The newest run of the fork's own `ci.yml` at `sha`, in any status (as `ci_jobs` picks
    it, but a run still going is returned too)."""
    runs = (gh_api(run, f"repos/{fork}/actions/runs?head_sha={sha}&per_page=100") or {}).get(
        "workflow_runs", []
    )
    ci = [workflow_run for workflow_run in runs if workflow_run.get("path") == CI_WORKFLOW]
    return max(ci, key=lambda r: (r.get("created_at") or "", r["id"])) if ci else None


def _alert_issue(run: Runner, fork: str) -> int | None:
    issues = (
        gh_api(run, f"repos/{fork}/issues?state=open&creator=github-actions%5Bbot%5D&per_page=100")
        or []
    )
    return next(
        (
            issue["number"]
            for issue in issues
            if "pull_request" not in issue and ALERT_MARKER in (issue.get("body") or "")
        ),
        None,
    )


def _review_unavailable(run: Runner, fork: str, number: int, sha: str) -> bool:
    """Whether the review couldn't run: its comment says so, or there is no comment of this head
    (the job failed before posting one)."""
    try:
        review = acknowledge.read_review(run, fork, number)
    except Refused:
        return True
    return review.unavailable or review.head != sha[:12]


def _pull_reasons(
    run: Runner, fork: str, pull: dict[str, Any], now: datetime
) -> tuple[list[str], str, str]:
    """(stuck reasons, detail, `waiting_on_person` detail or "") for an open sync pull request."""
    number, sha = pull["number"], pull["head"]["sha"]
    commit = gh_api(run, f"repos/{fork}/commits/{sha}") or {}
    # The sync commit is built with `commit-tree` immediately before it is pushed, so its
    # committer date is when the head arrived.
    pushed = (commit.get("commit") or {}).get("committer", {}).get("date") or pull["created_at"]
    since = max(_instant(pushed), _instant(pull["created_at"]))
    ci = _latest_ci_run(run, fork, sha)
    if ci is None or ci.get("status") != "completed":
        waited = now - since
        what = "no ci.yml run" if ci is None else f"ci.yml {ci.get('status')}"
        if waited > timedelta(minutes=NO_CHECKS_MINUTES):
            return [NO_CHECKS], f"#{number}: {what} at its head after {_hours(waited)}", ""
        return [], f"#{number}: {what} at its head, {_hours(waited)} after the push", ""
    jobs = {
        job["name"]: job
        for job in (gh_api(run, f"repos/{fork}/actions/runs/{ci['id']}/jobs") or {}).get("jobs", [])
    }
    build, review = (jobs.get(name, {}) for name in REQUIRED_CHECKS)
    reasons: list[str] = []
    details: list[str] = []
    if build.get("conclusion") != "success":
        reasons.append(BUILD_FAILING)
        details.append(f"`build and test` {build.get('conclusion', 'missing')}")
    if review.get("conclusion") == "success":
        if not reasons:
            done = max(
                (_instant(j["completed_at"]) for j in (build, review) if j.get("completed_at")),
                default=_instant(ci.get("updated_at") or pull["created_at"]),
            )
            open_for = now - done
            if open_for > timedelta(hours=NOT_LANDING_HOURS):
                reasons.append(NOT_LANDING)
                details.append(f"both checks passed {_hours(open_for)} ago and it is still open")
            else:
                details.append(f"both checks passed {_hours(open_for)} ago; landing")
        return reasons, f"#{number}: " + "; ".join(details), ""
    issue = _alert_issue(run, fork)
    unavailable = _review_unavailable(run, fork, number, sha)
    held = "could not run" if unavailable else "holds findings"
    if issue is not None:
        waiting = f"#{number}: the review {held}; alert issue #{issue} is open"
        return reasons, f"#{number}: " + "; ".join(details), waiting
    reasons.append(REVIEW_UNAVAILABLE if unavailable else HELD_WITHOUT_ALERT)
    details.append(f"the review {held} and no alert issue is open")
    return reasons, f"#{number}: " + "; ".join(details), ""


def _release_reasons(
    run: Runner, fork: str, config: Config, now: datetime
) -> tuple[list[str], str]:
    manifest = gh_api(run, f"repos/{fork}/contents/{config.version_manifest}?ref=main", raw=True)
    if manifest is None:
        raise Unreadable(f"{fork} has no {config.version_manifest} on main")
    current = manifest_version(config.ecosystem, manifest)
    releases = gh_api(run, f"repos/{config.upstream}/releases?per_page=100") or []
    tag = next_release(config, releases, current)
    if tag is None:
        return [], f"current at {current}"
    published = next(r.get("published_at") for r in releases if r.get("tag_name") == tag)
    age = _age(now, published or "1970-01-01T00:00:00Z")
    if age > timedelta(hours=RELEASE_UNSYNCED_HOURS):
        return [NOT_SYNCING], f"{tag} published {_hours(age)} ago, main at {current}, no sync open"
    return [], f"{tag} published {_hours(age)} ago; the next sync takes it"


def classify(run: Runner, fork: str, now: datetime) -> Verdict:
    text = gh_api(run, f"repos/{fork}/contents/{CONFIG_PATH}?ref=main", raw=True)
    if text is None:
        return Verdict(fork, NOT_ON_LANE, detail=f"registered, but main has no {CONFIG_PATH}")
    config = parse_config(text)
    reasons, sync_detail = _sync_reasons(run, fork, now)
    pulls = gh_api(run, f"repos/{fork}/pulls?state=open&head={fork.split('/')[0]}:upstream-sync")
    waiting = ""
    if pulls:
        more, detail, waiting = _pull_reasons(run, fork, pulls[0], now)
    else:
        more, detail = _release_reasons(run, fork, config, now)
    reasons += more
    detail = "; ".join(part for part in (detail, sync_detail) if part)
    if reasons:
        return Verdict(fork, STUCK, tuple(reasons), detail)
    if waiting:
        return Verdict(fork, WAITING_ON_PERSON, detail=waiting)
    return Verdict(fork, OK, detail=detail)


class _Counting:
    def __init__(self, run: Runner) -> None:
        self.run = run
        self.calls = 0

    def __call__(self, argv: Sequence[str], stdin: str | None, cwd: Path | None) -> str:
        self.calls += 1
        return self.run(argv, stdin, cwd)


def watch(run: Runner, now: datetime | None = None) -> Pass:
    """One pass over every fork on the lane. The first failed read ends it."""
    counting = _Counting(run)
    result = Pass()
    now = now or clock()
    try:
        for fork in forks(counting):
            try:
                result.verdicts.append(classify(counting, fork, now))
            except (CommandFailed, ConfigError, Unreadable) as error:
                raise Unreadable(f"{fork}: {error}") from None
    except (CommandFailed, Unreadable) as error:
        result.unreadable = str(error)
    result.calls = counting.calls
    return result
