"""Should the SDS work on this repository at all? Asked at the carry, REPORTED ONLY.

Devon's rule, 2026-09-11: a repository is workable by the SDS when all three of
these hold -- it OPTS IN (`factory-target.toml` declares `factory_target =
true`), the conformance kit says it is CAPABLE, and the permissions needed to do
the work are SUFFICIENT. "The intent was always to have SDS decide when starting
a work package to check and make sure it should."

**THIS MODULE ANSWERS AND DOES NOT ACT.** It prints, per record, what the three
constraints say and what a refusing version of it would have done. Nothing here
skips a carry, alters an exit code or stops an intake being registered, and the
property that makes it safe to land is that the carried set and the exit code are
identical with it and without it. Devon wants to see what it would refuse across
the live population before it refuses anything.

**WHY THE CARRY.** Everything expensive happens after it -- the package
revision, the write-once authority envelope, both human approvals, the coding
attempt. A refusal here costs nothing; the same refusal at dispatch wastes the
design, and at the final push wastes the unit.

**THE REPOSITORY IS THE PACKAGE'S, NOT THE RECORD'S, and getting that wrong makes
the whole check decorative.** A `work` change record's `target_repository` is
`None` on every row that exists (measured 2026-09-11, all seven), and its
`package_source_repository` is `AlobarQuest/intent-packages` for every one --
hard-coded by the producer, because that is where the packages live. The
repository the factory would WORK IN is the approved package's own
`profile_fields.target_repo`, carried into the intake payload's enforcement
snapshot. Keying on either field the record carries would judge every carry
against `intent-packages`, which opts in and is capable, so the check would pass
everything forever while looking like a gate.

**A LOOKUP THAT FAILS IS NOT A REFUSAL.** `portfolio.json` keys projects by
DIRECTORY NAME while a repository is named `owner/repo`, so the mapping is a
guess that can miss -- and a miss that read as "not capable" would be a refusal
manufactured from a bug. Every unresolvable input here answers UNKNOWN with a
named reason, and the composition below can never turn an UNKNOWN into a
refusal.

**`factory.pat_scope` IS NOT A CONSTRAINT TERM, DELIBERATELY.** Its own docstring
in project-standards says "this check is ALWAYS `unknown`, and saying so is the
point": Actions secrets are write-only, a fine-grained PAT reports no
`x-oauth-scopes` header, no API exposes its permission set, and the only
demonstration is a push touching `.github/workflows/**`, which mutates. A term
that requires it to pass passes nothing, ever. The conformance kit met this first
and routed around it -- its capability checks ride in the report and never touch
`admission_passed` -- and this follows that precedent. What it does NOT do is
swallow the residual: `factory.pat_access` proves the token can push to the
repository, so the permission constraint is answered EXCEPT for a change whose
diff touches a workflow file, and the report says so in words on every repository
it would carry. That exception is not hypothetical; it killed two work units on
2026-08-03, at the final push, after coding and verification had both succeeded.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from work_carrier.declaration import Declaration, DeclarationSource, from_environment

DEFAULT_PORTFOLIO = "~/.portfolio/portfolio.json"
PORTFOLIO_ENV = "WORK_CARRIER_PORTFOLIO"

# The check ids the conformance kit publishes, mirrored from project-standards
# `src/portfolio/factory_checks.py::FACTORY_CHECKS` and `onboard_checks`'
# `runner.caller`. They are literals here because this is a separate program that
# imports no conformance kit; a name that stopped agreeing shows up as a check
# the scan recorded nothing for, which this module reports as UNKNOWN rather than
# passing over.
CAPABILITY_CHECKS = ("runner.caller", "factory.secrets", "factory.landing_known")
PERMISSION_CHECKS = ("factory.pat_access",)
SCOPE_CHECK = "factory.pat_scope"

YES = "yes"
NO = "no"
UNKNOWN = "unknown"

WOULD_CARRY = "WOULD CARRY"
WOULD_REFUSE = "WOULD REFUSE"
CANNOT_DECIDE = "CANNOT DECIDE"

# The decisions are different lengths, so a fixed indent leaves the longest one's continuation
# lines out of line with the rest. Padding to the widest keeps one column for a reader scanning
# a queue, which is the whole point of a report nobody can act on otherwise.
_HEADER = "WORKABILITY"
_WIDTH = max(len(word) for word in (WOULD_CARRY, WOULD_REFUSE, CANNOT_DECIDE, _HEADER)) + 2
_INDENT = " " * (_WIDTH + 1)


def _tag(word: str) -> str:
    return f"[{word}]".ljust(_WIDTH)


PAT_SCOPE_RESIDUAL = (
    "factory.pat_scope is unknown by construction, so this says the token may push to the "
    "repository and NOT that it may push a commit touching .github/workflows/**; that is the "
    "case that killed two work units on 2026-08-03, at the final push"
)

_STALE_SOURCE = (
    "it is written by one nightly job that measures working trees it does not update, so a "
    "check in it can be older than the file"
)


@dataclass(frozen=True)
class Constraint:
    """One of Devon's three, answered."""

    name: str
    verdict: str
    detail: str


@dataclass(frozen=True)
class Workability:
    """What the three constraints said about one repository, and what follows."""

    repository: str | None
    constraints: tuple[Constraint, ...]

    @property
    def decision(self) -> str:
        """Three-valued AND: a definite NO decides, and an UNKNOWN never can.

        A false term makes a conjunction false whatever else is unknown, so a
        repository that has declared `factory_target = false` would be refused
        even if nothing could be learned about its capability -- it answered.
        What cannot happen is the reverse: no combination of UNKNOWNs produces a
        refusal, which is the property that keeps a mapping miss from becoming
        one.
        """
        verdicts = {constraint.verdict for constraint in self.constraints}
        if NO in verdicts:
            return WOULD_REFUSE
        if UNKNOWN in verdicts:
            return CANNOT_DECIDE
        return WOULD_CARRY


@dataclass(frozen=True)
class Portfolio:
    """The estate's capability answers as this machine last recorded them."""

    projects: tuple[dict[str, Any], ...]
    age_seconds: float | None
    detail: str


def _describe_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m old"
    if seconds < 86400:
        return f"{seconds / 3600:.0f}h old"
    return f"{seconds / 86400:.0f}d old"


def load_portfolio(path: Path | None = None, *, now: float | None = None) -> Portfolio:
    """Read the nightly capability rollup, or say why there is none. Total.

    THE AGE IS REPORTED AND IS AN UNDERSTATEMENT. `generated_at` in the document
    is `None`, so the file's own mtime is the only timestamp -- and it says when
    the scan RAN, not how current the checkouts it read were. `portfolio scan`
    updates its own repository and none of the ones it measures, so a `pass`
    recorded this morning can describe a working tree weeks behind its remote.
    Measured 2026-09-11: the scan ran at 03:00 and recorded `runner.caller: pass`
    for a repository whose caller workflow had been deleted on the remote.
    """
    resolved = path or Path(os.environ.get(PORTFOLIO_ENV) or DEFAULT_PORTFOLIO).expanduser()
    try:
        raw = resolved.read_text(encoding="utf-8")
        age = (now if now is not None else time.time()) - resolved.stat().st_mtime
    except OSError as error:
        return Portfolio((), None, f"{resolved} could not be read: {error}")
    try:
        document = json.loads(raw)
    except ValueError as error:
        return Portfolio((), age, f"{resolved} is not readable JSON: {error}")
    projects = document.get("projects") if isinstance(document, dict) else None
    if not isinstance(projects, list):
        return Portfolio((), age, f"{resolved} carries no projects list")
    rows = tuple(row for row in projects if isinstance(row, dict))
    return Portfolio(rows, age, f"{resolved.name} is {_describe_age(age)} ({_STALE_SOURCE})")


def _names(project: dict[str, Any]) -> set[str]:
    """Every name this project could be found under, lowercased.

    `portfolio.json` carries no repository slug at all, so the only join
    available is the directory the checkout sits in -- `name`, which is the
    directory name, and the last segment of `path`, which is the same thing
    measured a second way. Both are taken because neither is declared to be the
    other.
    """
    found: set[str] = set()
    name = project.get("name")
    if isinstance(name, str) and name.strip():
        found.add(name.strip().lower())
    path = project.get("path")
    if isinstance(path, str) and path.strip():
        found.add(Path(path).name.lower())
    return found


def project_for(portfolio: Portfolio, repository: str) -> tuple[dict[str, Any] | None, str]:
    """The project row for `owner/repo`, or why there is not exactly one.

    The owner is dropped, which is the same approximation `prepare.package_path`
    already documents for finding a checkout: a fork under another owner resolves
    to the same directory. Two rows answering to one name is refused rather than
    resolved arbitrarily, because whichever was picked would be a guess about
    which repository the estate measured.
    """
    wanted = repository.split("/")[-1].strip().lower()
    if not wanted:
        return None, f"{repository!r} names no repository"
    matches = [row for row in portfolio.projects if wanted in _names(row)]
    if not matches:
        return None, (
            f"no project in {Path(DEFAULT_PORTFOLIO).name} is checked out as {wanted!r}, so the "
            "estate has recorded nothing about this repository -- this is a lookup that found "
            "nothing, not a repository that failed a check"
        )
    if len(matches) > 1:
        found = ", ".join(sorted(str(row.get("path")) for row in matches))
        return None, f"{wanted!r} matches more than one project ({found})"
    return matches[0], ""


def _checks(project: dict[str, Any]) -> dict[str, str]:
    """The sweep's answers for this project, keyed by check id.

    A row whose id or status is not a string is DROPPED rather than carried as a
    value nothing can compare: it then reads as a check the sweep recorded
    nothing for, which is UNKNOWN one function down. Admitting it as a status
    would make an unreadable row indistinguishable from a measured one.
    """
    block = project.get("factory")
    if not isinstance(block, list):
        return {}
    return {
        row["id"]: row["status"]
        for row in block
        if isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and isinstance(row.get("status"), str)
    }


def _from_checks(name: str, project: dict[str, Any], wanted: tuple[str, ...]) -> Constraint:
    """One constraint over a named set of conformance checks.

    An EMPTY factory block means the sweep did not measure this repository at all
    -- the kit scopes Q2 on the repository declaring a delivery profile -- and a
    check the scan recorded nothing for is the same answer one check down. Both
    are UNKNOWN: an unmeasured capability is not a demonstrated one, which is the
    fail-open the kit's own module says it must never produce.
    """
    statuses = _checks(project)
    if not statuses:
        return Constraint(
            name, UNKNOWN, "the conformance sweep recorded no factory checks for this repository"
        )
    missing = [check for check in wanted if check not in statuses]
    if missing:
        return Constraint(
            name, UNKNOWN, f"the sweep recorded no {', '.join(missing)} for this repository"
        )
    failed = [check for check in wanted if statuses[check] == "violation"]
    if failed:
        return Constraint(name, NO, f"{', '.join(failed)} reports a violation")
    unmeasured = [check for check in wanted if statuses[check] != "pass"]
    if unmeasured:
        detail = ", ".join(f"{check} is {statuses[check]}" for check in unmeasured)
        return Constraint(name, UNKNOWN, detail)
    named = ", ".join(wanted)
    return Constraint(name, YES, f"{named} passes" if len(wanted) == 1 else f"{named} all pass")


def _opt_in(declaration: Declaration) -> Constraint:
    if declaration.target is None:
        return Constraint("opts in", UNKNOWN, declaration.detail)
    return Constraint("opts in", YES if declaration.target else NO, declaration.detail)


def assess(
    repository: str | None,
    declaration: Declaration,
    portfolio: Portfolio,
) -> Workability:
    """The three constraints for one repository. Total, and it decides nothing."""
    if repository is None:
        unknown = "the intake payload names no target repository, so there is nothing to judge"
        return Workability(
            None,
            tuple(
                Constraint(name, UNKNOWN, unknown) for name in ("opts in", "capable", "permissions")
            ),
        )
    project, why = project_for(portfolio, repository)
    if project is None:
        return Workability(
            repository,
            (
                _opt_in(declaration),
                Constraint("capable", UNKNOWN, why),
                Constraint("permissions", UNKNOWN, why),
            ),
        )
    return Workability(
        repository,
        (
            _opt_in(declaration),
            _from_checks("capable", project, CAPABILITY_CHECKS),
            _from_checks("permissions", project, PERMISSION_CHECKS),
        ),
    )


def target_repository(payload: dict[str, Any]) -> str | None:
    """The repository the factory would work in, from the approved package.

    Read off the intake payload's enforcement snapshot rather than by parsing
    `package.yaml` a second time: that snapshot is what the emitter verified and
    what the orchestrator will store, so there is no second reader to diverge.
    Every hop is defensive -- a profile that declares no `target_repo` is an
    honest `None`, reported as "nothing to judge" and never as "not a target".
    """
    snapshot = payload.get("enforcement_snapshot")
    if not isinstance(snapshot, dict):
        return None
    fields = snapshot.get("profile_fields")
    if not isinstance(fields, dict):
        return None
    target = fields.get("target_repo")
    if not isinstance(target, str) or not target.strip():
        return None
    return target.strip()


def _declaration_for(repository: str | None, source: DeclarationSource | None) -> Declaration:
    if repository is None:
        return Declaration(None, "no repository to ask about")
    if source is None:
        return Declaration(
            None,
            "no GitHub credential, so the declaration could not be read; set "
            "WORK_CARRIER_GITHUB_TOKEN (the checkout is NOT a substitute -- nothing pulls it, "
            "and a stale tree reports a repository that has opted in as one that has not)",
        )
    return source.declaration(repository)


def _print_one(label: str, verdict: Workability, out) -> None:
    subject = verdict.repository or "no target repository"
    print(f"{_tag(verdict.decision)} {label} -> {subject}", file=out)
    for constraint in verdict.constraints:
        print(
            f"{_INDENT}{constraint.name:<11} {constraint.verdict:<7} {constraint.detail}",
            file=out,
        )
    if verdict.decision == WOULD_CARRY:
        print(f"{_INDENT}residual    {PAT_SCOPE_RESIDUAL}", file=out)


def report(
    subjects: list[tuple[str, dict[str, Any]]],
    out,
    *,
    source: DeclarationSource | None = None,
    portfolio: Portfolio | None = None,
) -> None:
    """Print what the three constraints say about everything this pass would carry.

    **TOTAL, AND THAT IS THE INCREMENT'S WHOLE SAFETY ARGUMENT.** It returns
    nothing, and it cannot raise: a report that could raise could change the
    carry's exit code, and this is landing precisely because it changes neither
    the exit code nor the carried set. The broad guard is deliberate rather than
    lazy -- every reader beneath it is already total, so anything reaching it is
    a defect in this module, and a defect in a report must not stop a lane that
    worked before the report existed.

    `source` absent means OPEN ONE FROM THE ENVIRONMENT, which answers `None`
    when this machine holds no GitHub credential -- and every repository then
    reports "could not tell", which is honest and is not a refusal. A caller
    that injects a reader keeps it; one opened here is closed here.
    """
    opened = None
    try:
        if source is None:
            opened = source = from_environment()
        _report(subjects, out, source, portfolio)
    except Exception as error:  # noqa: BLE001 - see the docstring: it may not raise
        # THE TYPE NAME ONLY, for the reason `declaration.py` gives one field
        # over: the credential is read inside this guard and reaches an
        # exception's repr by way of the client's own headers, and this stream is
        # a log file nobody is watching when it is written. Measured: a
        # non-ASCII `WORK_CARRIER_GITHUB_TOKEN` makes client construction raise a
        # `UnicodeEncodeError` whose repr carries `Bearer <token>` verbatim.
        print(
            "[WORKABILITY] the workability report failed and changed nothing: "
            f"{type(error).__name__}",
            file=out,
        )
    finally:
        if opened is not None:
            opened.close()


def _report(
    subjects: list[tuple[str, dict[str, Any]]],
    out,
    source: DeclarationSource | None,
    portfolio: Portfolio | None,
) -> None:
    loaded = portfolio if portfolio is not None else load_portfolio()
    print(
        f"\n{_tag(_HEADER)} reporting only — nothing below skips a carry or changes the exit code.",
        file=out,
    )
    print(f"{_INDENT}capability source: {loaded.detail}", file=out)
    if not subjects:
        print(f"{_INDENT}nothing was prepared this pass, so no repository was judged.", file=out)
        return

    for label, payload in subjects:
        # PER SUBJECT, because the outer guard is one level too high on its own:
        # it makes the PASS total and leaves the REPORT partial, so a defect
        # reading subject three prints one line and silently drops the verdicts
        # for four through ten. A reader cannot tell those missing rows from a
        # queue that was shorter than it was. The outer guard still stands above
        # this one -- it is what covers the header and the capability source, and
        # it is what the exit-code argument rests on.
        try:
            repository = target_repository(payload)
            verdict = assess(repository, _declaration_for(repository, source), loaded)
            _print_one(label, verdict, out)
        except Exception as error:  # noqa: BLE001 - the type name only, as above
            print(
                f"{_tag(_HEADER)} judging {label} failed and changed nothing: "
                f"{type(error).__name__}",
                file=out,
            )
