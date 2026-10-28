"""Should the SDS work on this repository at all? Asked at the carry, and it DECIDES.

Devon's rule, 2026-09-11: a repository is workable by the SDS when all three of
these hold -- it is GRANTED (the SDS member record grants the service that
covers the record's change class, in state `granted`, on a member that is not
retired), the conformance kit says it is CAPABLE, and the permissions needed to
do the work are SUFFICIENT. The grant replaced the repository's own
`factory-target.toml` as the scope authority (bridge layer Part 2); the file is
no longer read here. "The intent was always to have SDS decide when starting
a work package to check and make sure it should."

**IT USED TO ONLY REPORT, AND THAT INCREMENT'S SAFETY ARGUMENT IS NOW SPENT.** It
landed answering and not acting -- the carried set and the exit code identical
with it and without it -- so the estate could see what a refusing version would
refuse across the live population before anything refused on it. It refused
nothing: the approved work queue was empty (7 `work` records, 6 resolved, 1
wontfix), and a hand-run assessment of all eight factory repositories gave five
workable, three not, none undecided. So a record is now carried only when all
three constraints answer YES, and anything else is NOT carried with the reason
printed.

**A REASON TO HOLD IS NOT A REASON TO FAIL, AND THE TWO EXIT DIFFERENTLY.** A
definite NO is a FINDING -- an approved record that can never be carried while
the repository says what it says, which waits on a person. An UNKNOWN is an input
this pass could not use, which is a different code in this lane and, per
`run-work-carrier.sh`'s ranking, the worse of the two. Nothing here knows those
codes; it answers, and `work_carrier.cli` maps the answers onto the lane's own
vocabulary.

**WHY THE CARRY.** Everything expensive happens after it -- the package
revision, the write-once authority envelope, both human approvals, the coding
attempt. A refusal here costs nothing; the same refusal at dispatch wastes the
design, and at the final push wastes the unit.

**THE REPOSITORY IS THE PACKAGE'S, NOT THE RECORD'S, and getting that wrong makes
the whole check decorative.** A `work` change record's
`package_source_repository` is `AlobarQuest/intent-packages` for every one --
hard-coded by the producer, because that is where the packages live -- and its
`target_repository` is set only by a producer that sends one (a hand-proposed
record carries none). The repository the factory would WORK IN is the approved
package's own `profile_fields.target_repo`, carried into the intake payload's
enforcement snapshot, and that is what is judged here. Keying on
`package_source_repository` would judge every carry against `intent-packages`,
which is granted and capable, so the check would pass everything forever while
looking like a gate. Where the record does name a `target_repository` or a
`dependency-update` change class -- the fields change-manager's work policy
approves on -- `prepare` refuses the record unless the package agrees.

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
diff touches a workflow file, and the line that authorises a carry says so in
words. That exception is not hypothetical; it killed two work units on
2026-08-03, at the final push, after coding and verification had both succeeded.

**A STANDING OPERATIONAL PACKAGE HAS NO REPOSITORY, AND IS ASKED THREE OTHER
QUESTIONS (ADR-0054 increment 2).** A `non-software-operational` package -- a
credential rotation, one standing package per credential -- names no repository,
because there is none: an HQ session works its units, and no runner ever does.
The three repository questions have nothing to be asked of, so for this profile
they are replaced, and each answers YES or NO, never UNKNOWN: the package
declares itself STANDING (`profile_fields.standing is True`), its id is on
`STANDING_OPERATIONAL_PACKAGES` (the opt-in, held here because there is no
repository to hold a member-record grant), and its declared `reach` lies
wholly within `OPERATIONAL_REACH`. All three YES is `OPERATIONAL`, which is
carried; any NO is `NOT WORKABLE`, a finding. The branch keys on the PROFILE,
never on the missing repository: a `dependency-update` package that names no
`target_repo` is still held, because for that profile the absence is a defect
rather than the shape of the work. Nothing here makes such a unit runnable --
dispatch refuses `operational_action`, which is outside the runner vocabulary
and the admission capabilities.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sds_members.client import SERVICES, Member
from sds_members.populations import granted as granted_repositories
from sds_members.populations import service_for_change_class

DEFAULT_PORTFOLIO = "~/.portfolio/portfolio.json"
PORTFOLIO_ENV = "WORK_CARRIER_PORTFOLIO"
# The nightly sweep writes the file at 03:00 and the carrier runs at 07:05, so 48 hours tolerates
# one missed scan and refuses after two (ruling 5). A plain int with a real value and no off switch.
PORTFOLIO_MAX_AGE_HOURS: Final = 48

# The check ids the conformance kit publishes, mirrored from project-standards
# `src/portfolio/factory_checks.py::FACTORY_CHECKS` and `onboard_checks`'
# `runner.caller`. They are literals here because this is a separate program that
# imports no conformance kit; a name that stopped agreeing shows up as a check
# the scan recorded nothing for, which this module reports as UNKNOWN rather than
# passing over.
CAPABILITY_CHECKS = ("runner.caller", "factory.secrets", "factory.landing_known")
PERMISSION_CHECKS = ("factory.pat_access",)

OPERATIONAL_PROFILE = "non-software-operational"

# THE OPT-IN FOR WORK THAT HAS NO REPOSITORY. A repository is granted in the SDS
# member record; a standing operational package has no repository
# to hold a grant, so its opt-in is declared HERE, beside the other literals this
# program holds, and widening it is a reviewed diff to the lane that acts on it --
# which is what a standing-authority change should cost. Not in intent-packages,
# because a package that could opt itself in would be no opt-in at all.
STANDING_OPERATIONAL_PACKAGES = frozenset(
    {
        "rotation-openrouter-generic",
        "rotation-orchestrator-operator-bearer",
        "rotation-orchestrator-observer-bearer",
        "rotation-orchestrator-drift-reporter-bearer",
        "rotation-change-manager-observe-bearer",
        "rotation-change-manager-read-bearer",
        "rotation-change-manager-propose-bearer",
        "rotation-factory-pr-token",
        "rotation-github-finegrained-mirror",
        "rotation-bitbucket-mirror-token",
        "rotation-bws-tok-brain-ops",
        "rotation-change-manager-full-bearer",
        "rotation-bws-tok-content-mini",
    }
)

# Mirrored from `orchestrator.reach_vocabulary` (this program imports nothing from the
# orchestrator); `tests/work_carrier` pins it as a subset of that vocabulary. Every
# member but `source_repository`: work that reaches a repository is a runner's.
OPERATIONAL_REACH = frozenset({"external_system", "operator_machine", "live_estate"})

YES = "yes"
NO = "no"
UNKNOWN = "unknown"

WORKABLE = "WORKABLE"
# Carried like WORKABLE, but no runner will ever take it: an HQ session works it.
OPERATIONAL = "OPERATIONAL"
NOT_WORKABLE = "NOT WORKABLE"
UNDECIDED = "CANNOT DECIDE"
# A FOURTH ANSWER THAT NO CONSTRAINT PRODUCES: the judgment itself failed. It
# exists because this module now decides, so "the report broke" can no longer
# mean "nothing changed" -- a record nobody judged must be held, not carried.
UNJUDGED = "NOT JUDGED"

# The decisions are different lengths, so a fixed indent leaves the longest one's continuation
# lines out of line with the rest. Padding to the widest keeps one column for a reader scanning
# a queue, which is the whole point of a report nobody can act on otherwise.
_HEADER = "WORKABILITY"
_WIDTH = (
    max(len(word) for word in (WORKABLE, OPERATIONAL, NOT_WORKABLE, UNDECIDED, UNJUDGED, _HEADER))
    + 2
)
_INDENT = " " * (_WIDTH + 1)


def _tag(word: str) -> str:
    return f"[{word}]".ljust(_WIDTH)


PAT_SCOPE_RESIDUAL = (
    "factory.pat_scope is unknown by construction, so this says the token may push to the "
    "repository and NOT that it may push a commit touching .github/workflows/**; that is the "
    "case that killed two work units on 2026-08-03, at the final push"
)

OPERATIONAL_RESIDUAL = "no runner; worked by HQ"

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
    operational: bool = False

    @property
    def decision(self) -> str:
        """Three-valued AND: a definite NO decides, and an UNKNOWN never can.

        A false term makes a conjunction false whatever else is unknown, so a
        repository the member record does not grant would be refused
        even if nothing could be learned about its capability -- it answered.
        What cannot happen is the reverse: no combination of UNKNOWNs produces a
        refusal, which is the property that keeps a mapping miss from becoming
        one.
        """
        verdicts = {constraint.verdict for constraint in self.constraints}
        if NO in verdicts:
            return NOT_WORKABLE
        if UNKNOWN in verdicts:
            return UNDECIDED
        return OPERATIONAL if self.operational else WORKABLE


@dataclass(frozen=True)
class Portfolio:
    """The estate's capability answers as this machine last recorded them."""

    projects: tuple[dict[str, Any], ...]
    age_seconds: float | None
    detail: str
    # Past PORTFOLIO_MAX_AGE_HOURS: the file is not read, so every capability and permission term
    # is UNKNOWN for a reason that names the age, never a `pass` from a sweep that stopped running.
    stale: bool = False


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

    THE AGE IS BOUNDED. Past `PORTFOLIO_MAX_AGE_HOURS` the file is not read as an answer
    at all: the result is `stale`, carries no projects, and every capability and permission
    term is UNKNOWN, so a stale `pass` can no longer authorise a carry. The grant constraint
    reads the live SDS member record and is unaffected. The age is still printed on the
    line that authorises the carry.
    """
    resolved = path or Path(os.environ.get(PORTFOLIO_ENV) or DEFAULT_PORTFOLIO).expanduser()
    try:
        raw = resolved.read_text(encoding="utf-8")
        age = (now if now is not None else time.time()) - resolved.stat().st_mtime
    except OSError as error:
        return Portfolio((), None, f"{resolved} could not be read: {error}")
    if age > PORTFOLIO_MAX_AGE_HOURS * 3600:
        detail = (
            f"{resolved.name} is {age / 3600:.0f}h old, past the {PORTFOLIO_MAX_AGE_HOURS}h "
            f"limit, so it is not read ({_STALE_SOURCE})"
        )
        return Portfolio((), age, detail, stale=True)
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
    if portfolio.stale:
        return None, portfolio.detail
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

    **`not-applicable` FALLS IN WITH UNKNOWN, and that is a fold rather than a
    decision.** The estate has now met "not applicable is a distinct answer from
    not met" in three subsystems, and this is a fourth place it could bite. It
    does not bite TODAY, and the reason is worth writing down rather than
    rediscovering: the kit marks `runner.caller` not-applicable exactly when a
    repository declares `factory_target = false`, so the two co-occur and the
    definite NO on the grant decides first. A check that became not-applicable for
    some OTHER reason would hold a record under a code meaning "a later pass may
    carry it", which would be the same category error again.

    An EMPTY factory block means the sweep did not measure this repository at all
    -- the kit measures Q2 only for members granted or remediating app changes -- and a
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


def _granted(repository: str, granted: bool, service: str) -> Constraint:
    if granted:
        return Constraint("granted", YES, f"the member record grants {service} on {repository}")
    return Constraint(
        "granted",
        NO,
        f"the member record does not grant {service} on {repository} (absent, not `granted`, "
        "or the member is retired)",
    )


def grants_by_service(members: list[Member]) -> dict[str, frozenset[str]]:
    """Per service, the case-folded repositories whose grant is `granted` on a live member."""
    return {
        service: frozenset(name.lower() for name in granted_repositories(members, service))
        for service in SERVICES
    }


def assess(
    repository: str | None,
    granted: bool,
    portfolio: Portfolio,
    *,
    service: str = "app_changes",
) -> Workability:
    """The three constraints for one repository. Total, and it decides nothing."""
    if repository is None:
        # NOT A PROPERTY OF THIS RECORD, AND NOT ONE A LATER PASS MENDS. The field
        # is read off the approved package's own profile fields, and only the
        # `dependency-update` profile declares `target_repo` -- the others name
        # their repository differently or not at all, and a package's schema is
        # closed, so it cannot simply be added. So this line says what is true and
        # stops: nobody can answer the three questions about a repository nothing
        # named. The one profile whose work has no repository by design is asked
        # its own questions in `assess_operational` before this is reached; for
        # every other profile, holding is the fail-closed answer.
        unknown = (
            "the approved package's profile names no target repository under `target_repo`, so "
            "there is nothing to ask the three questions about; this is a fact about the "
            "profile rather than about this record, and no later pass changes it"
        )
        return Workability(
            None,
            tuple(
                Constraint(name, UNKNOWN, unknown) for name in ("granted", "capable", "permissions")
            ),
        )
    project, why = project_for(portfolio, repository)
    if project is None:
        return Workability(
            repository,
            (
                _granted(repository, granted, service),
                Constraint("capable", UNKNOWN, why),
                Constraint("permissions", UNKNOWN, why),
            ),
        )
    return Workability(
        repository,
        (
            _granted(repository, granted, service),
            _from_checks("capable", project, CAPABILITY_CHECKS),
            _from_checks("permissions", project, PERMISSION_CHECKS),
        ),
    )


def is_operational(payload: dict[str, Any]) -> bool:
    """Whether the approved package declares the non-software-operational profile.

    `profile` is a top-level field of the intake payload, not of its enforcement
    snapshot -- `package_sources.py` emits it beside the snapshot -- so it is read there.
    """
    return payload.get("profile") == OPERATIONAL_PROFILE


def _standing(fields: object) -> Constraint:
    standing = fields.get("standing") if isinstance(fields, dict) else None
    if standing is True:
        return Constraint("standing", YES, "profile_fields.standing is true")
    return Constraint(
        "standing",
        NO,
        f"profile_fields.standing is {standing!r}, not true; only a standing package is "
        "carried without a repository",
    )


def _allowlisted(package_id: object) -> Constraint:
    if isinstance(package_id, str) and package_id in STANDING_OPERATIONAL_PACKAGES:
        return Constraint("opts in", YES, f"{package_id} is a declared standing package")
    return Constraint(
        "opts in",
        NO,
        f"{package_id!r} is not in work_carrier.workability.STANDING_OPERATIONAL_PACKAGES, "
        "which is where a package with no repository opts in",
    )


def _reach(snapshot: object) -> Constraint:
    """All or nothing, like `orchestrator.reach_vocabulary.reach_from_snapshot`.

    Absent, empty, or carrying any member outside the set is a NO: a reach this
    program cannot place is not one it may carry work into.
    """
    reach = snapshot.get("reach") if isinstance(snapshot, dict) else None
    if not isinstance(reach, list) or not reach:
        return Constraint("reach", NO, "the package declares no reach")
    # `isinstance` first: an unhashable member would make the membership test raise.
    outside = [m for m in reach if not isinstance(m, str) or m not in OPERATIONAL_REACH]
    if outside:
        allowed = ", ".join(sorted(OPERATIONAL_REACH))
        return Constraint("reach", NO, f"{outside!r} lies outside {allowed}")
    return Constraint("reach", YES, ", ".join(sorted(set(reach))))


def assess_operational(payload: dict[str, Any]) -> Workability:
    """The three operational checks for a standing package. Total, and asks no network."""
    snapshot = payload.get("enforcement_snapshot")
    fields = snapshot.get("profile_fields") if isinstance(snapshot, dict) else None
    return Workability(
        None,
        (_standing(fields), _allowlisted(payload.get("package_id")), _reach(snapshot)),
        operational=True,
    )


def target_repository(payload: dict[str, Any]) -> str | None:
    """The repository the factory would work in, from the approved package.

    Read off the intake payload's enforcement snapshot rather than by parsing
    `package.yaml` a second time: that snapshot is what the emitter verified and
    what the orchestrator will store, so there is no second reader to diverge.
    Every hop is defensive -- a profile that declares no `target_repo` is an
    honest `None`, held as "could not be answered" and never refused as "not a target".
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


_CONSEQUENCE = {
    NOT_WORKABLE: (
        "NOT CARRIED — a constraint answered no, so this record can never be carried while "
        "that stays true; a person decides what to do about it"
    ),
    UNDECIDED: (
        "NOT CARRIED — a constraint could not be answered, so this pass does not know whether "
        "the work belongs in that repository; nothing is wrong with the record"
    ),
}


def _print_one(label: str, verdict: Workability, out) -> None:
    if verdict.operational:
        subject = "operational package, no repository"
    else:
        subject = verdict.repository or "no target repository"
    print(f"{_tag(verdict.decision)} {label} -> {subject}", file=out)
    for constraint in verdict.constraints:
        print(
            f"{_INDENT}{constraint.name:<11} {constraint.verdict:<7} {constraint.detail}",
            file=out,
        )
    if verdict.decision == WORKABLE:
        # ON THE LINE THAT AUTHORISES WORK, which is the whole point of printing
        # it: `factory.pat_scope` is unknown by construction, so the permission
        # constraint is answered EXCEPT for a change whose diff touches a
        # workflow file, and a reader must see that on the line that says yes.
        print(f"{_INDENT}residual    {PAT_SCOPE_RESIDUAL}", file=out)
        return
    if verdict.decision == OPERATIONAL:
        print(f"{_INDENT}residual    {OPERATIONAL_RESIDUAL}", file=out)
        return
    print(f"{_INDENT}{_CONSEQUENCE[verdict.decision]}", file=out)


def judge(
    subjects: list[tuple[str, dict[str, Any]]],
    out,
    *,
    grants: dict[str, frozenset[str]],
    portfolio: Portfolio | None = None,
) -> tuple[str, ...]:
    """Decide, per subject, whether the carry may proceed -- and say why either way.

    Returns one decision per subject, in the order they were given and of the
    same length, so a caller can pair them off positionally. The caller maps
    those onto its own exit vocabulary; this module does not know one.

    **STILL TOTAL, AND THE TOTALITY NOW MEANS SOMETHING ELSE.** It used to keep a
    report from moving an exit code the carry owned. It now keeps a DEFECT IN THE
    JUDGE from carrying a record nobody judged: every failure yields `UNJUDGED`,
    which is not `WORKABLE`, so the record is held. Fail closed -- the opposite
    of the old guard's "failed and changed nothing", which would now be a
    sentence that admits work by being wrong.

    The decisions list is built as the loop runs and padded on the way out, so a
    failure BETWEEN subjects keeps the answers already reached rather than
    discarding them; only the subjects never reached are padded. That is a
    different question from a failure WITHIN one subject, which holds it -- see
    the ordering note in `_judge`.

    `grants` is `grants_by_service` over a member record the CALLER read: an unreadable
    record is the caller's to stop on, because there is no honest per-subject answer to give
    from a record nobody has.
    """
    decisions: list[str] = []
    try:
        _judge(subjects, out, grants, portfolio, decisions)
    except Exception as error:  # noqa: BLE001 - see the docstring: it may not raise
        # THE TYPE NAME ONLY: an exception's repr can carry a credential, and this
        # stream is a log file nobody is watching when it is written.
        print(
            f"{_tag(_HEADER)} the workability judgment failed, so nothing it had not already "
            f"decided is carried: {type(error).__name__}",
            file=out,
        )
    return tuple(decisions) + (UNJUDGED,) * (len(subjects) - len(decisions))


def _judge(
    subjects: list[tuple[str, dict[str, Any]]],
    out,
    grants: dict[str, frozenset[str]],
    portfolio: Portfolio | None,
    decisions: list[str],
) -> None:
    loaded = portfolio if portfolio is not None else load_portfolio()
    print(
        f"\n{_tag(_HEADER)} a record is carried only when all three answer yes "
        "(for a standing operational package, its own three).",
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
            if is_operational(payload):
                verdict = assess_operational(payload)
            else:
                repository = target_repository(payload)
                profile = payload.get("profile")
                service = service_for_change_class(profile if isinstance(profile, str) else "")
                held = repository is not None and repository.lower() in grants.get(
                    service, frozenset()
                )
                verdict = assess(repository, held, loaded, service=service)
            # PRINTED BEFORE IT IS RECORDED, and the order is fail-closed rather
            # than tidy. Writing the line is part of answering: a record carried
            # under a reason that never finished printing is a carry nobody can
            # read. So a failure anywhere in here -- including in the print --
            # reaches the guard below and the subject is HELD. The other order
            # was proposed in review and is the fail-open: it records WORKABLE,
            # the print raises, the guard prints "NOT carried", and the record is
            # carried anyway -- the stream saying the opposite of what happened.
            _print_one(label, verdict, out)
            decisions.append(verdict.decision)
        except Exception as error:  # noqa: BLE001 - the type name only, as above
            print(
                f"{_tag(UNJUDGED)} {label} could not be judged, so it is NOT carried: "
                f"{type(error).__name__}",
                file=out,
            )
            decisions.append(UNJUDGED)
