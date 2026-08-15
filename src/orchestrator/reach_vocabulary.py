"""The reach vocabulary -- what a package's work touches when it runs (WS-P2.18, ADR-0009).

Reach is DECLARED by the package author and validated at intake; the orchestrator never infers
it. It is a SET, because real packages reach more than one place at once: a repository change
that also restarts a running hosted service, or a credential rotation that touches both an
external console and the operator's own machine. A single-valued field forces a false choice on
five of the twenty-four packages authored to date, and always the permissive one.

Reach is not the change class. Two packages of the same class can differ completely in what they
touch -- ``ws-2.4-brain-approver-gate`` and ``ws-3.1-orchestrator-core`` are both
``software-delivery``, and the first restarts four running services while the second changes only
a repository. Reach is the dimension that separates them, and it is the one the change window
will be keyed on.

An absent declaration is UNKNOWN, not empty. The fourteen packages authored before this field
existed cannot be edited -- their YAML is hashed into lineage approvals -- so absence is a
permanent state of the record, and every consumer must treat it as "nobody has said", never as
"nothing is touched".

Membership is owned HERE, in one place. ``intent-packages`` accepts the key and checks its shape
so an author gets early feedback, but deliberately does not enumerate the members: two copies of
a vocabulary is the drift this repo has already paid for three times. The two failure directions
are both loud -- a member this module does not know fails intake with a named error, and a key
``intent-packages`` does not accept fails ``factory validate`` -- so there is nothing silent
between the repositories to pin.
"""

from collections.abc import Sequence
from typing import Any, Final

from orchestrator.errors import DomainError

# Named because two consumers ask specifically about this member: the change window keys on it,
# and the WS-P2.28 estate check asks whether a declaration that OMITS it is contradicted by what
# App Brain records about the target repository.
#
# The string is repeated in the dict below rather than used as the key, and that is deliberate:
# `tests/architecture/test_cross_boundary_vocabulary.py` recognises a vocabulary by finding a
# collection of string LITERALS, and a dict keyed by names is invisible to it. Naming the member
# here must not cost the registry its view of the vocabulary the member belongs to, so the two are
# pinned to each other by test instead.
LIVE_ESTATE: Final = "live_estate"

# The four places work lands, each with the sentence a human reads at the gate. The description
# is part of the vocabulary, not decoration: this field exists to answer "what does this touch"
# for a person, and a bare token like ``live_estate`` answers it only for a scheduler.
REACH_VOCABULARY: Final[dict[str, str]] = {
    "source_repository": (
        "a source repository -- writes land in a git repository and nowhere else, so nothing "
        "outside it changes until something separately acts on the result"
    ),
    "live_estate": (
        "the live estate -- something already serving is changed: a hosted application or its "
        "database, the VPS, DNS, or the orchestrator itself"
    ),
    "external_system": (
        "an external system of record -- state changes in a service outside the estate, such as "
        "a secrets console, a registrar, a task tracker or a listing service, which this estate "
        "cannot put back on its own"
    ),
    "operator_machine": (
        "the operator's own machine -- the work runs on it or writes to it: its keychain, its "
        "scheduled jobs, its agent configuration, or its local checkouts"
    ),
}

_SNAPSHOT_KEY = "reach"


def _invalid(detail: str) -> DomainError:
    return DomainError(
        "reach_invalid",
        f"package reach declaration is invalid: {detail}",
        "correct the package `reach` list and re-emit the intake payload",
    )


def validate_reach(value: object) -> tuple[str, ...] | None:
    """Return the normalized declaration, or ``None`` when the package declared none.

    Sorted and deduplicated on the way in, because reach is a set and two packages that reach the
    same places should read identically at the gate whatever order their authors wrote.
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise _invalid("it must be a list of reach values")
    if not value:
        raise _invalid("it must name at least one reach value, or be omitted entirely")
    for member in value:
        if not isinstance(member, str):
            raise _invalid("every entry must be a string")
        if member not in REACH_VOCABULARY:
            raise _invalid(
                f"{member!r} is not a recognised reach value; declare one of: "
                + ", ".join(sorted(REACH_VOCABULARY))
            )
    if len(set(value)) != len(value):
        raise _invalid("it must not repeat a reach value")
    return tuple(sorted(value))


def reach_from_snapshot(snapshot: object) -> tuple[str, ...] | None:
    """The declared reach held in an enforcement snapshot, or ``None`` when none was declared.

    Reads defensively rather than trusting the stored shape. Intake validates every declaration
    it admits, but revisions registered before this field existed carry no key at all, and the
    snapshot is data from another repository whose shape nothing else in this repo enforces.

    ALL OR NOTHING. A declaration containing any member this build does not recognise reads as
    UNDECLARED, not as the recognisable part of itself. Filtering the unknown member out would be
    the permissive reading twice over: it silently narrows what the author said the work touches,
    and it hands every consumer a declaration that looks complete. The likeliest way to meet one
    is a newer authoring side naming a reach this build predates -- exactly the case where
    dropping it turns "touches somewhere I have never heard of" into "touches only a repository".
    """
    values = snapshot.get(_SNAPSHOT_KEY) if isinstance(snapshot, dict) else None
    if not isinstance(values, list) or not values:
        return None
    # `isinstance` first, and not merely for tidiness: an unhashable member (a nested list or
    # object) makes the membership test raise TypeError, and only DomainError has a handler -- so
    # the shape this function exists to absorb would have surfaced as an unhandled 500.
    if any(not isinstance(member, str) or member not in REACH_VOCABULARY for member in values):
        return None
    return tuple(sorted(set(values)))


def carry_reach(snapshot: dict[str, Any], value: tuple[str, ...] | None) -> dict[str, Any]:
    """The snapshot with a declared reach carried into it, unchanged when nothing was declared.

    A package that declares no reach must not acquire an empty list on the way through: an empty
    list would later read as "reaches nothing", which is a different and much more permissive
    claim than "nobody said".
    """
    return snapshot if value is None else {**snapshot, _SNAPSHOT_KEY: value}


def reach_statement(members: Sequence[str] | None) -> str | None:
    """The sentence a human reads at the gate, or ``None`` when nothing was declared."""
    if not members:
        return None
    return "This work reaches " + "; ".join(REACH_VOCABULARY[member] for member in members)
