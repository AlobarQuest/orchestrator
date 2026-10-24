"""`fork-sync ack`: accept a held sync after one approval.

Accepting a held sync used to take five hand steps: read the report, write `[[ack]]` entries, open
a pull request changing the acknowledgements on main, admin-merge it after reading its own report
(it changes the gate, so the old gate judges it, and its review always flags the file), and re-run
the sync so the held pull request is reviewed against the new list. `plan` reads what would be
acknowledged and changes nothing; `land` does the rest, and only for the fingerprints a person was
shown.

The review comment is read only from `github-actions[bot]`, which posts it with the workflow's
token: anyone can comment on a public fork, and a planted comment could pair a harmless-looking row
with the fingerprint of a real finding. Its rows are upstream's text, printed and recorded as data,
with every control character escaped, so none can move the cursor, erase a row or recolour one.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from fork_sync import onboard
from fork_sync.fork_sync_review import (
    ACKS_PATH,
    COMMENT_MARKER,
    MODEL_SEVERITIES,
    Ack,
    ConfigError,
    parse_acks,
)
from fork_sync.gh import Runner, gh_api
from fork_sync.onboard import CI_WORKFLOW, Refused

SYNC_BRANCH = "upstream-sync"
SYNC_WORKFLOW = "upstream-sync.yml"
REVIEW_AUTHOR = "github-actions[bot]"
POLL_SECONDS = 30
DEADLINE_SECONDS = 45 * 60
"""How long the acknowledgement pull request's checks may take before `land` stops, unmerged."""
TEXT_LIMIT = 240
# `security review` fails on the acknowledgements file by design, so either verdict is a review
# that ran; `cancelled`, `skipped`, `timed_out` or no job at all is not.
REVIEW_VERDICTS = frozenset({"success", "failure"})

sleep = time.sleep
monotonic = time.monotonic
"""Looked up at call time, so tests replace them; nothing waits for real in a test."""

_HEADER = re.compile(r"^## Fork-sync security review \(([0-9a-f]{12})\)$", re.MULTILINE)
_SEPARATOR = re.compile(r"(?<!\\)\|")
_KIND = re.compile(r"(\S+) \((.*)\)")
_FINGERPRINT = re.compile(r"`([0-9a-f]{16})`")
# C0, DEL and C1 controls (ESC among them), and the bidirectional overrides and isolates, which
# reorder what a terminal shows without being visible themselves.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


def printable(text: str) -> str:
    """Upstream's or the model's text with every control character shown as its escape."""
    return _CONTROL.sub(lambda match: match.group().encode("unicode_escape").decode(), text)


@dataclass(frozen=True)
class Row:
    """One failing row of a review comment, as the report rendered it, made printable."""

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
    return printable(cell.replace("\\|", "|"))


def _row(line: str) -> Row:
    cells = [cell.strip() for cell in _SEPARATOR.split(line)]
    kind = _KIND.fullmatch(cells[2]) if len(cells) == 7 else None
    fingerprint = _FINGERPRINT.fullmatch(cells[5]) if len(cells) == 7 else None
    path = cells[3] if len(cells) == 7 else ""
    if kind is None or fingerprint is None or not (path.startswith("`") and path.endswith("`")):
        raise Refused(f"a review row is not one the report renders: {printable(line[:200])!r}")
    return Row(
        _unescape(kind.group(1)),
        _unescape(kind.group(2)),
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


@dataclass(frozen=True)
class Raise:
    """A model acknowledgement whose ceiling a new review rates higher, raised in place."""

    fingerprint: str
    severity: str
    """The new ceiling, above the existing one."""
    suffix: str
    """Appended to the entry's reason."""


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
    raises: tuple[Raise, ...]
    acks: str
    """Main's acknowledgements file as the plan read it; `land` refuses if main's has moved."""

    @property
    def fingerprints(self) -> list[str]:
        return [entry.fingerprint for entry in self.entries] + [r.fingerprint for r in self.raises]


def _trimmed(text: str) -> str:
    text = " ".join(text.split()).rstrip(".")
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1].rstrip() + "…"


def _highest(fingerprint: str, rows: Sequence[Row]) -> str:
    ranked = [row.severity for row in rows]
    if any(rank not in MODEL_SEVERITIES for rank in ranked):
        raise Refused(f"an ai row of {fingerprint} has a severity the gate doesn't rank")
    return max(ranked, key=MODEL_SEVERITIES.index)


def _changes(
    failing: Sequence[Row],
    existing: Mapping[str, Ack],
    title: str,
    prefix: str,
    reason: str,
    by: str,
    today: date,
) -> tuple[list[Entry], list[Raise]]:
    """One change per fingerprint. Two model concerns about one file share a fingerprint (it names
    the content, not the wording), and a fingerprint may be acknowledged only once, so they are
    one entry at the highest severity among them. A fingerprint already acknowledged is raised in
    place, and only when it is a model acknowledgement whose ceiling every row's rating exceeds
    upward; anything else already in the file is a person's to edit."""
    grouped: dict[str, list[Row]] = {}
    for row in failing:
        grouped.setdefault(row.fingerprint, []).append(row)
    entries, raises = [], []
    for fingerprint, rows in grouped.items():
        ai = all(row.kind == "ai" for row in rows)
        severity = _highest(fingerprint, rows) if ai else None
        ack = existing.get(fingerprint)
        if ack is not None:
            ceiling = ack.severity
            if (
                severity is None
                or ceiling not in MODEL_SEVERITIES
                or MODEL_SEVERITIES.index(severity) <= MODEL_SEVERITIES.index(ceiling)
            ):
                raise Refused(
                    f"{fingerprint} is already acknowledged (severity {ceiling or 'none'}) and "
                    f"its rows are not a higher model rating; edit {ACKS_PATH} by hand"
                )
            raises.append(
                Raise(fingerprint, severity, f"; raised to {severity} on {today} ({title})")
            )
            continue
        texts = "; ".join(_trimmed(row.text) for row in rows)
        text = f"{prefix} {rows[0].kind} {rows[0].path}: {texts}. {reason.strip()}"
        entries.append(Entry(fingerprint, by, today, text, severity))
    return entries, raises


def _main_acks(run: Runner, fork: str) -> str:
    text = gh_api(run, f"repos/{fork}/contents/{ACKS_PATH}?ref=main", raw=True)
    if text is None:
        raise Refused(f"{fork} main has no {ACKS_PATH}")
    return text


def plan(run: Runner, fork: str, number: int, reason: str, by: str, today: date) -> Plan:
    """What `land` would change: every row the current review of the sync marks failing."""
    if not reason.strip() or not by.strip():
        raise Refused("--reason and --by must say why these findings are accepted, and by whom")
    pull = _pull(run, fork, number)
    head = pull["head"]
    if pull.get("state") != "open":
        raise Refused(f"pull request #{number} is {pull.get('state')}, not open")
    repository = (head.get("repo") or {}).get("full_name", "")
    if head.get("ref") != SYNC_BRANCH or repository.lower() != fork.lower():
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
    acks = _main_acks(run, fork)
    title = printable(pull["title"])
    entries, raises = _changes(
        review.failing, parse_acks(acks), title, f"{fork} {title}", reason, by, today
    )
    return Plan(
        fork, number, title, head["sha"], review.failing, tuple(entries), tuple(raises), acks
    )


def _raised(text: str, change: Raise, reason: str) -> str:
    """`text` with one entry's `severity` and `reason` lines rewritten in place. The entry is found
    by its `fingerprint` line; anything but one plain line each is refused, and the result is
    checked by parsing it."""
    lines = text.splitlines(keepends=True)
    at = [
        i
        for i, line in enumerate(lines)
        if re.fullmatch(rf'\s*fingerprint\s*=\s*"{change.fingerprint}"\s*', line)
    ]
    starts = [i for i, line in enumerate(lines) if line.lstrip().startswith("[")]
    if len(at) != 1:
        raise Refused(f"{change.fingerprint} is not one plain entry in {ACKS_PATH}; edit by hand")
    start = max(i for i in starts if i < at[0])
    end = min((i for i in starts if i > at[0]), default=len(lines))
    block = range(start, end)
    severity = [i for i in block if re.match(r"\s*severity\s*=", lines[i])]
    reasons = [i for i in block if re.match(r"\s*reason\s*=", lines[i])]
    if len(severity) != 1 or len(reasons) != 1:
        raise Refused(f"{change.fingerprint}'s entry is not one I can edit; edit it by hand")
    lines[severity[0]] = f"severity = {_toml_string(change.severity)}\n"
    lines[reasons[0]] = f"reason = {_toml_string(reason + change.suffix)}\n"
    return "".join(lines)


def acknowledged(current: str, entries: Sequence[Entry], raises: Sequence[Raise]) -> str:
    """The acknowledgements file with the raises made and the entries appended, refused unless it
    parses to exactly the old entries, changed and added as intended."""
    existing = parse_acks(current)
    already = sorted({entry.fingerprint for entry in entries} & set(existing))
    if already:
        raise Refused(f"already acknowledged: {', '.join(already)}; edit {ACKS_PATH} by hand")
    expected = {fp: (ack.by, ack.reason, ack.severity) for fp, ack in existing.items()}
    text = current
    for change in raises:
        ack = existing[change.fingerprint]
        text = _raised(text, change, ack.reason)
        expected[change.fingerprint] = (ack.by, ack.reason + change.suffix, change.severity)
    text = text if text.endswith("\n") or not text else text + "\n"
    text += "".join(f"\n{entry.toml()}" for entry in entries)
    expected |= {e.fingerprint: (e.by, e.reason, e.severity) for e in entries}
    try:
        parsed = parse_acks(text)
    except (ConfigError, ValueError) as error:
        raise Refused(f"the new {ACKS_PATH} does not parse: {error}") from None
    if {fp: (ack.by, ack.reason, ack.severity) for fp, ack in parsed.items()} != expected:
        raise Refused(f"the new {ACKS_PATH} does not read back as written")
    return text


def _branch(plan: Plan) -> str:
    return f"fork-sync-ack-{plan.number}-{plan.head[:12]}"


def _open_pull(run: Runner, plan: Plan) -> tuple[int, str]:
    """Commit the changes to a new branch from main and open its pull request."""
    branch = _branch(plan)
    owner = plan.fork.split("/")[0]
    pulls = gh_api(run, f"repos/{plan.fork}/pulls?state=open&head={owner}:{branch}") or []
    if pulls:
        raise Refused(
            f"#{pulls[0]['number']} from {branch} is still open, from an earlier run; merge or "
            "close it, then run again"
        )
    with tempfile.TemporaryDirectory() as scratch:
        clone = onboard.clone_fork(run, plan.fork, Path(scratch), "--depth", "1")
        acks = clone / ACKS_PATH
        if not acks.is_file() or acks.read_text(encoding="utf-8") != plan.acks:
            raise Refused(f"{plan.fork} main's {ACKS_PATH} changed since it was read; run again")
        text = acknowledged(plan.acks, plan.entries, plan.raises)
        run(["git", "checkout", "--quiet", "-b", branch], None, clone)
        acks.write_text(text, encoding="utf-8")
        run(["git", "add", "--", ACKS_PATH], None, clone)
        count = len(plan.entries) + len(plan.raises)
        message = f"fork-sync: acknowledge {count} finding(s) on #{plan.number}"
        run(["git", "commit", "--quiet", "-m", message], None, clone)
        run(["git", "push", "--quiet", "origin", branch], None, clone)
        sha = run(["git", "rev-parse", "HEAD"], None, clone).strip()
        body = (
            f"Acknowledges the failing findings of #{plan.number} at `{plan.head[:12]}`, by "
            f"`fork-sync ack`. Only `{ACKS_PATH}` changes."
        )
        argv = ["gh", "pr", "create", "--repo", plan.fork, "--base", "main", "--head", branch]
        url = run([*argv, "--title", message, "--body", body], None, clone)
    match = re.search(r"/pull/(\d+)", url)
    if match is None:
        raise Refused(f"gh pr create printed no pull request URL: {url.strip()[:200]}")
    return int(match.group(1)), sha


def _ci_jobs(run: Runner, fork: str, sha: str) -> dict[str, dict[str, Any]] | None:
    """The jobs of the most recent run of the fork's own CI workflow at `sha`, once that run has
    completed; `None` until then. Only that workflow's jobs count: another workflow's job of the
    same name, or an older run, must not decide the merge."""
    runs = (gh_api(run, f"repos/{fork}/actions/runs?head_sha={sha}&per_page=100") or {}).get(
        "workflow_runs", []
    )
    ci = [workflow_run for workflow_run in runs if workflow_run.get("path") == CI_WORKFLOW]
    if not ci:
        return None
    latest = max(
        ci, key=lambda workflow_run: (workflow_run.get("created_at") or "", workflow_run["id"])
    )
    if latest.get("status") != "completed":
        return None
    listed = gh_api(run, f"repos/{fork}/actions/runs/{latest['id']}/jobs") or {}
    return {job["name"]: job for job in listed.get("jobs", [])}


def wait_for_checks(run: Runner, fork: str, number: int, sha: str) -> dict[str, dict[str, Any]]:
    deadline = monotonic() + DEADLINE_SECONDS
    while (jobs := _ci_jobs(run, fork, sha)) is None:
        if monotonic() >= deadline:
            raise Refused(
                f"no {CI_WORKFLOW} run on #{number} completed in time; it is open and unmerged"
            )
        sleep(POLL_SECONDS)
    return jobs


def merge_refusal(
    run: Runner, fork: str, number: int, sha: str, jobs: Mapping[str, Mapping[str, Any]]
) -> str:
    """Why the acknowledgement pull request must not be admin-merged, or "" when it may: its diff
    is only the acknowledgements file at the commit pushed, its CI run's `build and test` passed
    and `security review` reached a verdict, and every failing row of its own review is the gate
    flagging that file."""
    files = gh_api(run, f"repos/{fork}/pulls/{number}/files?per_page=100") or []
    names = sorted(item["filename"] for item in files)
    if names != [ACKS_PATH]:
        return f"its diff is {names}, not only {ACKS_PATH}"
    if _pull(run, fork, number)["head"]["sha"] != sha:
        return "its head is no longer the commit fork-sync ack pushed"
    build = jobs.get("build and test") or {}
    if build.get("conclusion") != "success":
        return f"`build and test` concluded {build.get('conclusion') or 'nothing'}"
    review_job = jobs.get("security review") or {}
    if review_job.get("status") != "completed" or review_job.get("conclusion") not in (
        REVIEW_VERDICTS
    ):
        return f"`security review` concluded {review_job.get('conclusion') or 'nothing'}"
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
    merge = ["gh", "pr", "merge", str(number), "--repo", plan.fork, "--merge", "--admin"]
    run([*merge, "--match-head-commit", sha], None, None)
    merged = _pull(run, plan.fork, number)
    if not merged.get("merged") or not merged.get("merge_commit_sha"):
        raise Refused(f"#{number} did not merge")
    run(["gh", "workflow", "run", SYNC_WORKFLOW, "--repo", plan.fork], None, None)
    return Landed(number, merged["merge_commit_sha"])
