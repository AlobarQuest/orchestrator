"""`fork-sync ack`: accept a held sync after one approval.

Accepting a held sync used to take five hand steps: read the report, write `[[ack]]` entries, open
a pull request changing the acknowledgements on main, admin-merge it after reading its own report
(it changes the gate, so the old gate judges it, and its review always flags the file), and re-run
the sync so the held pull request is reviewed against the new list. `plan` reads what would be
acknowledged and changes nothing; `land` does the rest, and only for the fingerprints a person was
shown.

The review comment is read only from `github-actions[bot]`, which posts it with the workflow's
token: anyone can comment on a public fork, and a planted comment could pair a harmless-looking row
with the fingerprint of a real finding. Its rows are upstream's text, printed and recorded as data.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from fork_sync import onboard
from fork_sync.fork_sync_review import (
    ACKS_PATH,
    COMMENT_MARKER,
    MODEL_SEVERITIES,
    ConfigError,
    parse_acks,
)
from fork_sync.gh import Runner, gh_api
from fork_sync.onboard import REQUIRED_CHECKS, Refused

SYNC_BRANCH = "upstream-sync"
SYNC_WORKFLOW = "upstream-sync.yml"
REVIEW_AUTHOR = "github-actions[bot]"
DEFAULT_BY = "devon (approved in session; recorded by Claude)"
POLL_SECONDS = 30
DEADLINE_SECONDS = 45 * 60
"""How long the acknowledgement pull request's checks may take before `land` stops, unmerged."""
TEXT_LIMIT = 240

sleep = time.sleep
monotonic = time.monotonic
"""Looked up at call time, so tests replace them; nothing waits for real in a test."""

_HEADER = re.compile(r"^## Fork-sync security review \(([0-9a-f]{12})\)$", re.MULTILINE)
_SEPARATOR = re.compile(r"(?<!\\)\|")
_KIND = re.compile(r"(\S+) \((.*)\)")
_FINGERPRINT = re.compile(r"`([0-9a-f]{16})`")


@dataclass(frozen=True)
class Row:
    """One failing row of a review comment, as the report rendered it."""

    kind: str
    severity: str
    path: str
    text: str
    fingerprint: str


@dataclass(frozen=True)
class Review:
    head: str
    """The 12-character head the review ran on."""
    failing: tuple[Row, ...]
    unavailable: bool


def _unescape(cell: str) -> str:
    return cell.replace("\\|", "|")


def _row(line: str) -> Row:
    cells = [cell.strip() for cell in _SEPARATOR.split(line)]
    kind = _KIND.fullmatch(cells[2]) if len(cells) == 7 else None
    fingerprint = _FINGERPRINT.fullmatch(cells[5]) if len(cells) == 7 else None
    path = cells[3] if len(cells) == 7 else ""
    if kind is None or fingerprint is None or not (path.startswith("`") and path.endswith("`")):
        raise Refused(f"a review row is not one the report renders: {line[:200]!r}")
    return Row(
        kind.group(1),
        kind.group(2),
        _unescape(path[1:-1]),
        _unescape(cells[4]),
        fingerprint.group(1),
    )


def parse_review(body: str) -> Review:
    """The rows `Report.markdown` marks failing, read back from the comment it rendered."""
    header = _HEADER.search(body)
    if header is None:
        raise Refused("the review comment has no `## Fork-sync security review (<head>)` header")
    rows = tuple(_row(line) for line in body.splitlines() if line.startswith("| **fails**"))
    return Review(header.group(1), rows, "**The review could not run:**" in body)


def read_review(run: Runner, fork: str, number: int) -> Review:
    """The latest review comment the workflow posted on the pull request."""
    comments = gh_api(run, f"repos/{fork}/issues/{number}/comments?per_page=100") or []
    posted = [
        comment
        for comment in comments
        if (comment.get("user") or {}).get("login") == REVIEW_AUTHOR
        and (comment.get("user") or {}).get("type") == "Bot"
        and COMMENT_MARKER in (comment.get("body") or "")
    ]
    if not posted:
        raise Refused(f"pull request #{number} has no review comment from {REVIEW_AUTHOR}")
    return parse_review(posted[-1]["body"])


def _pull(run: Runner, fork: str, number: int) -> dict[str, Any]:
    pull = gh_api(run, f"repos/{fork}/pulls/{number}")
    if pull is None:
        raise Refused(f"{fork} has no pull request #{number}")
    return pull


@dataclass(frozen=True)
class Entry:
    fingerprint: str
    by: str
    date: date
    reason: str
    severity: str | None

    def toml(self) -> str:
        lines = [
            "[[ack]]",
            f"fingerprint = {_toml_string(self.fingerprint)}",
            f"by = {_toml_string(self.by)}",
            f"date = {self.date.isoformat()}",
        ]
        if self.severity is not None:
            lines.append(f"severity = {_toml_string(self.severity)}")
        lines.append(f"reason = {_toml_string(self.reason)}")
        return "\n".join(lines) + "\n"


def _toml_string(text: str) -> str:
    """A TOML basic string. JSON's escapes are TOML's, except that TOML also refuses a raw DEL and
    a `\\u` escape of a surrogate, so neither is let through."""
    text = text.encode("utf-8", "replace").decode("utf-8").replace("\x7f", "")
    return json.dumps(text, ensure_ascii=False)


@dataclass(frozen=True)
class Plan:
    fork: str
    number: int
    title: str
    head: str
    failing: tuple[Row, ...]
    entries: tuple[Entry, ...]

    @property
    def fingerprints(self) -> list[str]:
        return [entry.fingerprint for entry in self.entries]


def _trimmed(text: str) -> str:
    text = " ".join(text.split()).rstrip(".")
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1].rstrip() + "…"


def _entries(
    plan_rows: Sequence[Row], prefix: str, reason: str, by: str, today: date
) -> list[Entry]:
    """One entry per fingerprint. Two model concerns about one file share a fingerprint (it names
    the content, not the wording), and a fingerprint may be acknowledged only once, so they are
    one entry at the highest severity among them."""
    grouped: dict[str, list[Row]] = {}
    for row in plan_rows:
        grouped.setdefault(row.fingerprint, []).append(row)
    entries = []
    for fingerprint, rows in grouped.items():
        severity = None
        if rows[0].kind == "ai":
            ranked = [row.severity for row in rows]
            if any(rank not in MODEL_SEVERITIES for rank in ranked):
                raise Refused(f"an ai row of {fingerprint} has a severity the gate doesn't rank")
            severity = max(ranked, key=MODEL_SEVERITIES.index)
        texts = "; ".join(_trimmed(row.text) for row in rows)
        text = f"{prefix} {rows[0].kind} {rows[0].path}: {texts}. {reason.strip()}"
        entries.append(Entry(fingerprint, by, today, text, severity))
    return entries


def plan(run: Runner, fork: str, number: int, reason: str, by: str, today: date) -> Plan:
    """What `land` would acknowledge: every row the current review of the sync marks failing."""
    if not reason.strip():
        raise Refused("--reason must say why these findings are accepted")
    pull = _pull(run, fork, number)
    head = pull["head"]
    if pull.get("state") != "open":
        raise Refused(f"pull request #{number} is {pull.get('state')}, not open")
    if head.get("ref") != SYNC_BRANCH or (head.get("repo") or {}).get("full_name", "").lower() != (
        fork.lower()
    ):
        raise Refused(f"pull request #{number} is not {fork}'s {SYNC_BRANCH} pull request")
    review = read_review(run, fork, number)
    if review.head != head["sha"][:12]:
        raise Refused(
            f"the review comment is for {review.head}, but the head is {head['sha'][:12]}: the "
            "report is stale; wait for the review of the current head"
        )
    if review.unavailable:
        raise Refused("the review could not run; re-run the failed `security review` job")
    if not review.failing:
        raise Refused(f"no row of pull request #{number}'s review fails; nothing to acknowledge")
    prefix = f"{fork} {pull['title']}"
    entries = _entries(review.failing, prefix, reason, by, today)
    return Plan(fork, number, pull["title"], head["sha"], review.failing, tuple(entries))


def _acknowledged(current: str, entries: Sequence[Entry]) -> str:
    """The acknowledgements file with the entries appended, refused unless it parses to exactly
    the old entries plus the new ones, each as intended."""
    existing = parse_acks(current)
    already = sorted({entry.fingerprint for entry in entries} & set(existing))
    if already:
        raise Refused(f"already acknowledged: {', '.join(already)}; edit {ACKS_PATH} by hand")
    text = current if current.endswith("\n") or not current else current + "\n"
    text += "".join(f"\n{entry.toml()}" for entry in entries)
    try:
        parsed = parse_acks(text)
    except (ConfigError, ValueError) as error:
        raise Refused(f"the new {ACKS_PATH} does not parse: {error}") from None
    expected = {
        **{fp: (ack.by, ack.reason, ack.severity) for fp, ack in existing.items()},
        **{e.fingerprint: (e.by, e.reason, e.severity) for e in entries},
    }
    if {fp: (ack.by, ack.reason, ack.severity) for fp, ack in parsed.items()} != expected:
        raise Refused(f"the new {ACKS_PATH} does not read back as written")
    return text


def _open_pull(run: Runner, plan: Plan) -> tuple[int, str]:
    """Commit the entries to a new branch from main and open its pull request."""
    branch = f"fork-sync-ack-{plan.number}-{plan.head[:12]}"
    with tempfile.TemporaryDirectory() as scratch:
        clone = onboard.clone_fork(run, plan.fork, Path(scratch), "--depth", "1")
        acks = clone / ACKS_PATH
        if not acks.is_file():
            raise Refused(f"{plan.fork} main has no {ACKS_PATH}")
        text = _acknowledged(acks.read_text(encoding="utf-8"), plan.entries)
        run(["git", "checkout", "--quiet", "-b", branch], None, clone)
        acks.write_text(text, encoding="utf-8")
        run(["git", "add", "--", ACKS_PATH], None, clone)
        message = f"fork-sync: acknowledge {len(plan.entries)} finding(s) on #{plan.number}"
        run(["git", "commit", "--quiet", "-m", message], None, clone)
        run(["git", "push", "--quiet", "origin", branch], None, clone)
        sha = run(["git", "rev-parse", "HEAD"], None, clone).strip()
        url = run(
            [
                "gh", "pr", "create", "--repo", plan.fork, "--base", "main", "--head", branch,
                "--title", message,
                "--body", f"Acknowledges the failing findings of #{plan.number} at "
                f"`{plan.head[:12]}`, by `fork-sync ack`. Only `{ACKS_PATH}` changes.",
            ],
            None,
            clone,
        )  # fmt: skip
    match = re.search(r"/pull/(\d+)", url)
    if match is None:
        raise Refused(f"gh pr create printed no pull request URL: {url.strip()[:200]}")
    return int(match.group(1)), sha


def _jobs(run: Runner, fork: str, sha: str) -> dict[str, str] | None:
    """Each Actions job at `sha` and its conclusion, once every run there has completed and both
    required jobs have reported; `None` until then."""
    runs = (gh_api(run, f"repos/{fork}/actions/runs?head_sha={sha}&per_page=100") or {}).get(
        "workflow_runs", []
    )
    if any(workflow_run.get("status") != "completed" for workflow_run in runs):
        return None
    jobs: dict[str, str] = {}
    for workflow_run in runs:
        listed = gh_api(run, f"repos/{fork}/actions/runs/{workflow_run['id']}/jobs") or {}
        jobs |= {job["name"]: job.get("conclusion") or "" for job in listed.get("jobs", [])}
    return jobs if set(REQUIRED_CHECKS) <= set(jobs) else None


def wait_for_checks(run: Runner, fork: str, number: int, sha: str) -> dict[str, str]:
    deadline = monotonic() + DEADLINE_SECONDS
    while (jobs := _jobs(run, fork, sha)) is None:
        if monotonic() >= deadline:
            raise Refused(f"#{number}'s checks did not finish in time; it is open and unmerged")
        sleep(POLL_SECONDS)
    return jobs


def merge_refusal(run: Runner, fork: str, number: int, sha: str, jobs: dict[str, str]) -> str:
    """Why the acknowledgement pull request must not be admin-merged, or "" when it may: its diff
    is only the acknowledgements file at the commit pushed, `build and test` passed, and every
    failing row of its own review is the gate flagging that file."""
    files = gh_api(run, f"repos/{fork}/pulls/{number}/files?per_page=100") or []
    names = sorted(item["filename"] for item in files)
    if names != [ACKS_PATH]:
        return f"its diff is {names}, not only {ACKS_PATH}"
    if _pull(run, fork, number)["head"]["sha"] != sha:
        return "its head is no longer the commit fork-sync ack pushed"
    if jobs.get("build and test") != "success":
        return f"`build and test` concluded {jobs.get('build and test') or 'nothing'}"
    review = read_review(run, fork, number)
    if review.head != sha[:12] or review.unavailable:
        return "its review comment is not a completed review of its head"
    elsewhere = [row for row in review.failing if row.path != ACKS_PATH]
    if elsewhere:
        return (
            f"its review fails on {sorted({row.path for row in elsewhere})}, not only {ACKS_PATH}"
        )
    return ""


@dataclass(frozen=True)
class Landed:
    number: int
    merge_sha: str


def land(run: Runner, plan: Plan, expect: Sequence[str]) -> Landed:
    """Acknowledge exactly the fingerprints a person was shown, admin-merge the change when the
    gate flags only itself, and re-run the sync so the held pull request is reviewed against it."""
    if sorted(expect) != sorted(plan.fingerprints):
        raise Refused(
            f"the failing fingerprints are now {', '.join(plan.fingerprints)}, not the "
            f"{', '.join(expect) or 'none'} expected; run without --yes and read them again"
        )
    number, sha = _open_pull(run, plan)
    jobs = wait_for_checks(run, plan.fork, number, sha)
    refusal = merge_refusal(run, plan.fork, number, sha, jobs)
    if refusal:
        raise Refused(f"#{number} is open and unmerged: {refusal}")
    run(
        ["gh", "pr", "merge", str(number), "--repo", plan.fork, "--merge", "--admin",
         "--match-head-commit", sha],
        None,
        None,
    )  # fmt: skip
    merged = _pull(run, plan.fork, number)
    if not merged.get("merged") or not merged.get("merge_commit_sha"):
        raise Refused(f"#{number} did not merge")
    run(["gh", "workflow", "run", SYNC_WORKFLOW, "--repo", plan.fork], None, None)
    return Landed(number, merged["merge_commit_sha"])
