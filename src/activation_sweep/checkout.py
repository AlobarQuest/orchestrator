"""One enrolled working copy, and the read-only git surface that measures it.

WHAT A MEASUREMENT ASSERTS IS BOUNDED, and the bound is ADR-0030's weak form: it attests what the
next start, run, or invocation will execute. It never attests the currency of a running process.
That is why the enrolment rule admits only consumers that begin a fresh process in the ordinary
course, and why the KeepAlive daemons are excluded.

THE GIT SURFACE IS AN ALLOWLIST, ENFORCED HERE IN CODE. ADR-0030 stops at recording, so `pull`,
`merge`, `reset` and `checkout` are not merely unused -- they are unreachable. `run_git` refuses
any subcommand outside `READ_ONLY`, and `fetch` is the single member that writes anything. Be
precise about what that is, because the short version overstates it: `fetch` writes remote-tracking
refs, `FETCH_HEAD`, any tags reachable from what it fetched, and it can trigger an automatic `gc`.
The load-bearing half is what it does NOT touch -- never HEAD, never the index, never a tracked
file -- so no working copy's content changes and nothing becomes live that was not already.

FETCHING IS NOT OPTIONAL. Without it `behind` is computed against stale remote-tracking refs and
is always 0 -- the control reports clean because it never looked. `read_checkout` takes `fetch`
so a test can measure hermetically; the CLI refuses to RECORD an unfetched measurement.

DIRTY MEANS MODIFIED TRACKED FILES, NEVER UNTRACKED ONES, and the exclusion is the whole reason
this reads `--untracked-files=no`. `FacelessTT`'s cron writes untracked artifacts as its normal
output -- 62 of them on 2026-08-24 -- so counting porcelain lines would make that repository red
on every sweep forever, which is a control nobody reads. A tracked modification is a different
thing: somebody edited code the machine runs and never committed it, and `security-scan`'s
`controlplane.drift` already reports exactly that condition.

`ahead` IS MEASURED AND DELIBERATELY NOT CLASSIFIED. A working copy with unpushed commits is
running code that was never merged, which is arguably a condition worth reporting -- but the
condition vocabulary was decided at two members plus current, and inventing a third finding class
nobody decided is the wrong side of that line. So the number is carried in every row, where a
later decision can be made against recorded history rather than against an argument.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Every git subcommand this program may run. See the module docstring: `fetch` is the only member
# that writes anything at all, and remote-tracking refs are the whole of what it writes.
READ_ONLY = frozenset({"rev-parse", "rev-list", "config", "status", "log", "fetch"})

# `config` is the one member that can also WRITE -- `git config a b` sets a value -- so it carries
# an extra condition rather than being trusted by name. It is on the list at all because
# `git remote get-url` applies `url.<base>.insteadOf` and answers with the URL git will TALK to,
# while what identifies a repository is the URL it is CONFIGURED with; the rewrite is a transport
# detail of one machine. Measured, not assumed: with an `insteadOf` in place, `remote get-url`
# and `remote -v` both answer the rewritten path and `config --get` alone answers the configured
# value.
CONFIG_READ_FLAG = "--get"

# A scheduled job must never block on a credential prompt, and a sweep that reads `git status`
# against a tree somebody is working in at 07:10 should not take even an optional index lock.
GIT_ENVIRONMENT = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}

TIMEOUT_SECONDS = 120

# `git log` OBEYS the operator's global config, and `log.showSignature = true` makes it print three
# `gpg:` lines PER COMMIT onto STDOUT before the format string. Measured on this machine against a
# signed squash merge: the two-line HEAD read becomes five lines, so every checkout reads
# unavailable; and the missing-commits read becomes forty entries whose `commit` field is the
# literal `gpg:`, which the orchestrator refuses as an oversized list. Every commit GitHub squashes
# onto `main` here is signed, so the trigger is one `git config --global` away and the failure is
# total and silent. The suite cannot see this -- its fixtures scrub the global config, correctly --
# so the flag is the guard.
NO_SIGNATURE = "--no-show-signature"

# How many of the commits a checkout is missing to name. The true count travels beside the list as
# `behind_by`, the way the landing ledger keeps `files_changed` beside `files`, so a reader who
# sees fewer entries knows the list was trimmed rather than that fewer commits are missing.
MAX_MISSING = 10
MAX_SUBJECT = 200

# The two conditions a sweep can report. Empty means CURRENT, which is a third state and not the
# absence of the other two: `current` says the measurement happened and found nothing, where an
# unreadable checkout says nothing at all and is reported as unavailable instead.
BEHIND = "behind"
DIRTY = "dirty"
CONDITIONS = (BEHIND, DIRTY)


class GitError(RuntimeError):
    """This checkout could not be measured. The answer is missing, never clean."""


class ForbiddenCommandError(GitError):
    """The sweep attempted a git subcommand outside its read-only surface."""


def run_git(path: Path, *args: str, timeout: int = TIMEOUT_SECONDS) -> str:
    """One git invocation, refused unless its subcommand is on the read-only allowlist.

    Only the exit status reaches the error message. A git failure's stderr can carry the remote
    URL it was talking to, and a diagnostic that prints what it was given is how a value that
    should not be in a transcript gets into one.
    """
    subcommand = args[0] if args else ""
    if subcommand not in READ_ONLY:
        raise ForbiddenCommandError(f"the sweep may not run git {subcommand or '<nothing>'}")
    if subcommand == "config" and args[1:2] != (CONFIG_READ_FLAG,):
        raise ForbiddenCommandError("the sweep may only run git config --get")
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **GIT_ENVIRONMENT},
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(f"git {subcommand} could not run in {path}: {type(error).__name__}") from (
            error
        )
    if completed.returncode != 0:
        raise GitError(f"git {subcommand} exited {completed.returncode} in {path}")
    return completed.stdout


@dataclass(frozen=True)
class MissingCommit:
    """One commit the upstream branch has and the working copy does not."""

    commit: str
    subject: str


@dataclass(frozen=True)
class Checkout:
    """One working copy, as this sweep reads it from local git.

    `head_committed_at` is the committer date of HEAD, and it is the record's clock. See
    `record.activation_observation` for why that has to be a function of the facts.
    """

    path: str
    repository: str
    branch: str
    upstream: str
    head: str
    head_committed_at: datetime
    behind_by: int
    ahead_by: int
    tracked_modifications: int
    missing: tuple[MissingCommit, ...] = ()


def conditions_of(checkout: Checkout) -> tuple[str, ...]:
    """Which of `CONDITIONS` this checkout is in. Empty is `current`, and is an answer.

    ONE classifier, so the rule for what counts as dirty has one definition. The raw counts travel
    in the record beside the conditions they produced, which is what lets a reader check the
    classification rather than take it.
    """
    found = []
    if checkout.behind_by > 0:
        found.append(BEHIND)
    if checkout.tracked_modifications > 0:
        found.append(DIRTY)
    return tuple(found)


_HTTPS_ORIGIN = re.compile(r"^https://github\.com/(?P<owner>[^/\s]+)/(?P<name>[^/\s]+?)(?:\.git)?$")
_SSH_ORIGIN = re.compile(r"^git@github\.com:(?P<owner>[^/\s]+)/(?P<name>[^/\s]+?)(?:\.git)?$")


def repository_of(origin_url: str) -> str:
    """`owner/name`, read from the remote rather than transcribed beside the path.

    The observation's `subject_reference` is what a later reader joins on, and the landing ledger
    already keys `subject_type: repo` rows by `owner/name`. Deriving it from the remote means the
    two producers agree about a repository's name without either one holding a table -- and it is
    how `~/.claude` gets named `claude-control-plane`, which no path convention would produce.
    It is read from `config --get` rather than from `remote get-url` for the reason recorded
    beside `CONFIG_READ_FLAG`.

    Fail closed. A remote this cannot name is a checkout this sweep cannot honestly file a row
    about, so it becomes an unreadable checkout rather than a row under a guessed name.
    """
    candidate = origin_url.strip().rstrip("/")
    for pattern in (_HTTPS_ORIGIN, _SSH_ORIGIN):
        match = pattern.match(candidate)
        if match is not None:
            return f"{match['owner']}/{match['name']}"
    raise GitError("origin is not a GitHub remote this sweep can name")


def _ahead_behind(output: str) -> tuple[int, int]:
    parts = output.split()
    if len(parts) != 2:
        raise GitError("git rev-list did not answer with an ahead/behind pair")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as error:
        raise GitError("git rev-list answered with a non-numeric ahead/behind pair") from error


def _head(output: str) -> tuple[str, datetime]:
    lines = output.splitlines()
    if len(lines) != 2 or not lines[0].strip():
        raise GitError("git log did not answer with a commit and a committer date")
    try:
        committed_at = datetime.fromisoformat(lines[1].strip())
    except ValueError as error:
        raise GitError("HEAD's committer date is not an ISO 8601 instant") from error
    if committed_at.tzinfo is None:
        # The orchestrator refuses a naive `observed_at`, and this value becomes it.
        raise GitError("HEAD's committer date carries no timezone")
    return lines[0].strip(), committed_at


def _missing(output: str) -> tuple[MissingCommit, ...]:
    commits = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        commit, _, subject = stripped.partition(" ")
        # Truncate THEN strip. The orchestrator normalizes every stored string with
        # `.strip()`, so a cut landing on a space would make the stored facts differ from the
        # bytes the reference's digest was taken over, and a later reader could no longer
        # recompute one from the other.
        commits.append(MissingCommit(commit=commit, subject=subject.strip()[:MAX_SUBJECT].strip()))
    return tuple(commits)


def read_checkout(path: Path, *, fetch: bool = True) -> Checkout:
    """Measure one working copy. Raises `GitError` when it cannot be measured at all.

    The root check is not ceremony: `git -C` on a SUBDIRECTORY resolves to the enclosing
    repository, so a typo in an enrolled path would otherwise measure some parent silently and
    file the answer under the parent's name.
    """
    resolved = path.expanduser().resolve()
    toplevel = run_git(resolved, "rev-parse", "--show-toplevel").strip()
    if not toplevel or Path(toplevel).resolve() != resolved:
        raise GitError(f"{resolved} is not the root of a git working copy")
    repository = repository_of(run_git(resolved, "config", "--get", "remote.origin.url"))
    try:
        # `rev-parse @{u}` EXITS 128 when there is no upstream -- a detached HEAD, a deleted
        # remote branch, a missing remote-tracking ref -- so this is the reachable path and a
        # truthiness check below it would be dead code. It is named here because the condition is
        # a real one a reader will meet, and `git rev-parse exited 128` does not say what happened.
        upstream = run_git(
            resolved, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
        ).strip()
    except GitError as error:
        raise GitError(f"{resolved} has no upstream branch to be measured against") from error
    branch = run_git(resolved, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if fetch:
        run_git(resolved, "fetch", "--quiet")
    ahead_by, behind_by = _ahead_behind(
        run_git(resolved, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    )
    head, head_committed_at = _head(
        run_git(resolved, "log", NO_SIGNATURE, "-1", "--format=%H%n%cI", "HEAD")
    )
    tracked_modifications = len(
        [
            line
            for line in run_git(
                resolved, "status", "--porcelain", "--untracked-files=no"
            ).splitlines()
            if line.strip()
        ]
    )
    missing: tuple[MissingCommit, ...] = ()
    if behind_by:
        missing = _missing(
            run_git(
                resolved,
                "log",
                NO_SIGNATURE,
                f"--max-count={MAX_MISSING}",
                # Full hashes, never `%h`: git sizes an abbreviation against the repository's
                # object count, so the same missing commit can render one character longer as
                # the repository grows -- which moves the digest, and therefore the row's
                # identity, with nothing about reality having changed.
                "--format=%H %s",
                "HEAD..@{u}",
            )
        )
    return Checkout(
        path=str(resolved),
        repository=repository,
        branch=branch,
        upstream=upstream,
        head=head,
        head_committed_at=head_committed_at,
        behind_by=behind_by,
        ahead_by=ahead_by,
        tracked_modifications=tracked_modifications,
        missing=missing,
    )
