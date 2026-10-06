"""The three facts a human needs before deciding anything (WS-P2.17, spec 5.6).

Devon adjudicated a criterion with the rationale "It had words that looked right" -- the page
told him what the criterion *said* and nothing about what agreeing would let happen. The five
human gates are the same shape, so the answer is one projection, rendered by one partial, used at
every gate: **what it does**, **what it affects**, **can we back out**.

Every fact carries an explicit `known` flag. An unknown is a fact, not an absence: a row that is
simply omitted reads as "nothing to worry about", where the truth is "nobody knows yet". The
partial therefore always renders three rows.

This module is a pure projection over rows already loaded by the caller -- no session, no query,
no network. The intake facts read the package's profile and enforcement snapshot, because that is
where the author-written answers live, and they work the same on a staged payload as on a
registered revision. The one estate fact they use, what landing the repository's default branch
does, is read by the caller at render time and passed in.
"""

from __future__ import annotations

from typing import Any

from orchestrator.kernel.authority import AuthorityEnvelope, normalize_authority
from orchestrator.persistence.models import WorkPackageRevision, WorkUnit
from orchestrator.reach_vocabulary import reach_from_snapshot, reach_statement
from orchestrator.services.landing.estate_landing import (
    LANDING_INERT,
    LANDING_REDEPLOYS,
    EstateAnswer,
)

# What a change of each class costs to undo. Editorial prose, keyed by the `change_class` an
# authority envelope declares; a class with no entry resolves to the explicit unknown rather than
# to a guess. This is the FALLBACK: a package whose author declared a rollback plan is answered
# from that plan, and this is deliberately a statement about the class, not about the package.
#
# not-a-vocabulary: a lookup whose keys need not agree with any producer. An unlisted change class
# is a supported answer ("no reversibility is recorded for this class"), not a mismatch, so there
# is no other side for these members to agree with.
REVERSIBILITY_BY_CHANGE_CLASS: dict[str, str] = {
    "dependency-update": (
        "Backed out by reverting the pull request: the change is confined to the repository's "
        "manifest and lockfile, and nothing outside the repository is written."
    ),
    "maintenance-remediation": (
        "Backed out by reverting the pull request, but the remediation may have been prompted by "
        "a live problem that returns when it is reverted."
    ),
    "software-delivery": (
        "Backed out by reverting the pull request before release. Once a release artifact is "
        "bound, backing out means a new release, not a revert."
    ),
}

# An unknown's detail says WHY it is unknown and nothing more: the surface renders the "not known"
# marker itself, so a detail that opened with one would say it twice.
_UNKNOWN_REVERSIBILITY = (
    "No rollback plan is declared for this package, and it names no change class with a recorded "
    "way to back it out, so how reversible it is has not been established."
)
# The two answers a human can get are sourced differently, and only one of them is a commitment
# somebody made about THIS package. Saying which is which is the point: an author's plan can be
# held to; a sentence about a category cannot.
_DECLARED_PLAN_PREFIX = "The package's author declared this rollback plan: "
_CLASS_STATEMENT_PREFIX = "No rollback plan is declared for this package; for a "
_UNKNOWN_AFFECTS_AT_INTAKE = (
    "No repository is named in a profile field this surface reads, and the package has not been "
    "broken into work units, so no target repository and no mutating command have been chosen yet."
)

# Where each profile's author names the repository the work happens in. Keyed by profile name,
# which is also the change class that profile's envelopes declare. Only the machine-originated
# profiles are read so far; any other profile keeps the explicit unknown above.
#
# not-a-vocabulary: a lookup whose keys need not agree with any producer. An unlisted profile is a
# supported answer ("this surface reads no repository for it"), not a mismatch, so there is no
# other side for these members to agree with -- the same reasoning as REVERSIBILITY_BY_CHANGE_CLASS.
_REPOSITORY_FIELD_BY_PROFILE: dict[str, str] = {
    "dependency-update": "target_repo",
    "maintenance-remediation": "repo",
}
# The one reach member every class statement is compatible with: work confined to its repository.
_REPOSITORY_ONLY_REACH = "source_repository"
_NO_REPOSITORY_LANDING = (
    "No target repository is declared, so whether landing this work redeploys anything is not "
    "known."
)
_UNKNOWN_AFFECTS_FOR_UNIT = (
    "The authority envelope declares no constraint at all, so what this work touches has not been "
    "stated"
)

# The two keys this projection gives prominence to, because they are what repository work is
# about. Every OTHER constraint is reported by name whatever it is called, so an envelope
# declaring neither of these is still fully read back.
#
# not-a-vocabulary: not a set anything else must agree with. These two names are a rendering
# preference within this module -- the envelope's constraints are an open map, and a key missing
# from here is reported rather than dropped, which is the opposite of a vocabulary mismatch.
_REPOSITORY_SHAPED_CONSTRAINTS = ("target_repository", "mutation_commands")
_UNKNOWN_OUTCOME = "This package revision was registered without a recorded outcome statement."

_DOES_LABEL = "What it does"
_AFFECTS_LABEL = "What it affects"
_REVERSIBILITY_LABEL = "Can we back out"


def _fact(label: str, known: bool, detail: str) -> dict[str, Any]:
    return {"label": label, "known": known, "detail": detail}


def decision_facts_for_unit(
    unit: WorkUnit, revision: WorkPackageRevision
) -> dict[str, dict[str, Any]]:
    """The three facts for a work unit, whose authority envelope names its blast radius."""
    envelope = normalize_authority(unit.authority)
    return {
        "does": _fact(_DOES_LABEL, True, unit.outcome),
        "affects": _affects(
            _declared_reach(revision.enforcement_snapshot), _affects_from_envelope(envelope)
        ),
        "reversibility": _reversibility(
            envelope.change_class,
            _declared_rollback_plan(revision.enforcement_snapshot),
            revision.enforcement_snapshot,
        ),
    }


def decision_facts_for_revision(
    revision: WorkPackageRevision, landing: EstateAnswer | None
) -> dict[str, dict[str, Any]]:
    """The three facts for a registered package revision. See `decision_facts_for_intake`."""
    return decision_facts_for_intake(revision.profile, revision.enforcement_snapshot, landing)


def decision_facts_for_intake(
    profile: str | None, snapshot: object, landing: EstateAnswer | None
) -> dict[str, dict[str, Any]]:
    """The three facts for a package at intake, staged or registered.

    The envelope's half of "what it affects" is unanswerable here: the package-level authority
    block is a capability declaration, and the target repository is chosen per unit when the
    package is broken up. Two other answers are on file before a single unit exists. The reach
    the author declared, and, for a profile whose fields name the repository, that repository.

    The profile also names the change class, because a profile's units declare the profile's name
    as theirs. So a profile with a recorded way to back out answers reversibility here, below a
    declared rollback plan.

    `landing` is the estate's answer for `declared_repository(profile, snapshot)`, or `None` when
    there was no repository to ask about. It qualifies the back-out sentence and never changes
    whether that fact is known: the route back is stated by the plan or the class, and an
    unreadable estate is said in words rather than guessed at.
    """
    outcome = _package_outcome(snapshot)
    repository = declared_repository(profile, snapshot)
    reversibility = _reversibility(profile, _declared_rollback_plan(snapshot), snapshot)
    reversibility["detail"] += " " + _landing_statement(repository, landing)
    return {
        "does": _fact(_DOES_LABEL, outcome is not None, outcome or _UNKNOWN_OUTCOME),
        "affects": _affects(_declared_reach(snapshot), _affects_from_profile(snapshot, repository)),
        "reversibility": reversibility,
    }


def declared_repository(profile: str | None, snapshot: object) -> str | None:
    """The repository a package's profile fields name, or `None` when there is none to read.

    `None` covers a profile this surface reads no repository for, and a known profile whose field
    is missing, mistyped or blank: the snapshot is data from another repository and nothing here
    validates its shape.
    """
    field = _REPOSITORY_FIELD_BY_PROFILE.get(profile) if isinstance(profile, str) else None
    value = _profile_fields(snapshot).get(field) if field is not None else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _affects_from_profile(snapshot: object, repository: str | None) -> dict[str, Any]:
    if repository is None:
        return _fact(_AFFECTS_LABEL, False, _UNKNOWN_AFFECTS_AT_INTAKE)
    # Only the dependency-update profile declares these three fields.
    fields = _profile_fields(snapshot)
    package, old, new = (fields.get(name) for name in ("package", "from_version", "to_version"))
    detail = f"Repository {repository}"
    if all(isinstance(value, str) and value.strip() for value in (package, old, new)):
        detail += f"; updates {package} from {old} to {new}"
    return _fact(_AFFECTS_LABEL, True, detail + ".")


def _landing_statement(repository: str | None, landing: EstateAnswer | None) -> str:
    """What the estate records about landing the repository's default branch, in one sentence.

    Only App Brain's two definite answers are stated as facts. Its own `unknown`, an unconfigured
    source and an unreadable one are all said to be not known, each with its reason, because an
    absent answer that read as "inert" would tell the person a merge is safe when nobody knows.
    """
    if repository is None:
        return _NO_REPOSITORY_LANDING
    branch = f"landing on {repository}'s default branch"
    if landing is not None and landing.landing == LANDING_REDEPLOYS:
        return (
            f"The estate records that {branch} redeploys a running service, so the change is live "
            "once it lands, and a revert is a second redeploy."
        )
    if landing is not None and landing.landing == LANDING_INERT:
        return f"The estate records that {branch} changes nothing already running."
    reason = f" ({landing.reason})" if landing is not None and landing.reason else ""
    if landing is not None and landing.landing is not None:
        return (
            f"Whether {branch} redeploys anything is not known: App Brain has no determination "
            f"for it{reason}."
        )
    return (
        f"Whether {branch} redeploys anything is not known: the estate's record could not be "
        f"read{reason}."
    )


def _affects(reach: str | None, from_envelope: dict[str, Any]) -> dict[str, Any]:
    """The declared reach first, then whatever the envelope says, as one fact.

    The two are answers to the same question at different resolutions, and neither replaces the
    other. Reach is a closed classification the author committed to and policy can be keyed on;
    the envelope's half is the specifics, read back in the author's own words. Leading with the
    classification is deliberate: it is the sentence that survives being skimmed.

    Reach alone makes this fact KNOWN. At intake that is the whole change -- the envelope half is
    an explicit unknown there, and a package that declared its reach has answered the question
    even though no unit exists yet.
    """
    if reach is None:
        return from_envelope
    detail = f"{reach}. {from_envelope['detail']}" if from_envelope["known"] else f"{reach}."
    return _fact(_AFFECTS_LABEL, True, detail)


def _declared_reach(snapshot: object) -> str | None:
    return reach_statement(reach_from_snapshot(snapshot))


def _profile_fields(snapshot: object) -> dict[str, Any]:
    fields = snapshot.get("profile_fields") if isinstance(snapshot, dict) else None
    return fields if isinstance(fields, dict) else {}


def _declared_rollback_plan(snapshot: object) -> str | None:
    """`profile_fields.rollback_plan` from the enforcement snapshot, or `None`.

    Three of the five intent-package profiles require this field as a non-empty string, and
    `package_sources.py` copies `profile_fields` into the snapshot verbatim -- so for those
    packages the orchestrator has held an author-written answer to "can we back out" since intake.
    The snapshot is data from another repository and nothing here validates its shape, so a
    missing, mistyped or blank plan falls back rather than rendering an empty commitment.
    """
    plan = _profile_fields(snapshot).get("rollback_plan")
    return plan.strip() if isinstance(plan, str) and plan.strip() else None


def _package_outcome(snapshot: object) -> str | None:
    """`outcome.what` from the enforcement snapshot, tolerating a bare string.

    `package_sources.py` copies the intent package's whole `outcome` block, so production
    snapshots carry the mapping. The orchestrator never validates the snapshot's shape, and
    revisions registered by other callers carry `outcome` as a plain string -- read both rather
    than reporting a recorded outcome as absent.
    """
    outcome = snapshot.get("outcome") if isinstance(snapshot, dict) else None
    if isinstance(outcome, str):
        return outcome or None
    what = outcome.get("what") if isinstance(outcome, dict) else None
    return what if isinstance(what, str) and what else None


def _affects_from_envelope(envelope: AuthorityEnvelope) -> dict[str, Any]:
    """Everything the envelope declares about what this work touches, in its own terms.

    The two repository-shaped keys keep their prominence, and while the envelope is repository-
    shaped at all, the missing half of that pair is still stated. Which negative it is depends on
    repo.edit: with edit authority granted, an absent mutation_commands is the edit-shaped norm —
    the coding agent produces the diff, and saying "nothing to change" would tell the approving
    human the opposite of what the unit does (WS-P2.33). Without edit authority it really is
    repository work with nothing to change, which is worth saying.

    But an envelope is an open map. For `non-software-operational` work -- the class where blast
    radius matters most -- the answer arrives under other names entirely (`credential`,
    `irreversible_actions`, `secret_value_handling`), and reading only the two repository keys
    rendered an explicit unknown over an answer already on file. So every other constraint the
    author declared is reported too, whatever it is called. A second fixed key list, chosen to fit
    today's operational packages, would repeat this defect for the next profile.

    The explicit unknown is therefore reserved for an envelope that declares nothing about what it
    touches. The granted capabilities are reported either way -- an unknown fact is not an empty
    one, but neither is a capability grant an answer to this question.
    """
    constraints = envelope.constraints
    parts: list[str] = []
    target = constraints.get("target_repository")
    commands = constraints.get("mutation_commands")
    if any(name in constraints for name in _REPOSITORY_SHAPED_CONSTRAINTS):
        parts.append(
            f"Repository {target}"
            if isinstance(target, str) and target
            else "No target repository is named in the authority envelope"
        )
        if isinstance(commands, list) and commands:
            parts.append("runs " + ", ".join(str(command) for command in commands))
        elif envelope.level_for("repo.edit") == "allowed":
            parts.append("mutates by direct edits; no command mutates")
        else:
            parts.append("no mutating command is authorized")
    parts.extend(
        statement
        for name, value in constraints.items()
        if name not in _REPOSITORY_SHAPED_CONSTRAINTS
        and (statement := _constraint_statement(name, value)) is not None
    )
    known = bool(parts)
    if not known:
        parts.append(_UNKNOWN_AFFECTS_FOR_UNIT)
    granted = sorted(
        capability
        for capability, level in envelope.capabilities.items()
        if level not in {"prohibited", ""}
    )
    parts.append("grants " + (", ".join(granted) if granted else "no capability"))
    return _fact(_AFFECTS_LABEL, known, "; ".join(parts) + ".")


def _constraint_statement(name: str, value: Any) -> str | None:
    """One declared constraint, read back as the author wrote it, or `None` if it states nothing.

    An empty list is a statement (`irreversible_actions: []` means there are none); an absent or
    blank value is not, and rendering `credential ` followed by nothing would read as a fact.
    """
    if isinstance(value, str):
        return f"{name} {value}" if value.strip() else None
    if isinstance(value, list):
        return f"{name} " + (", ".join(str(item) for item in value) if value else "none")
    if value is None:
        return None
    return f"{name} {value}"


def _reversibility(
    change_class: str | None, declared_plan: str | None, snapshot: object
) -> dict[str, Any]:
    """Declared plan, then class statement, then the explicit unknown.

    The order is the point. A plan the package's author wrote is a per-package commitment; the
    class statement is prose about a category that happens to contain this package. Rendering the
    second while holding the first is strictly worse information.

    Every class statement assumes the work stays in its repository, so that reverting the pull
    request undoes it. A package whose declared reach goes beyond its source repository has said
    otherwise, and the class statement is then not known to hold for it: it is shown, as the
    class's claim, beside the explicit unknown rather than as an answer.
    """
    if declared_plan is not None:
        return _fact(_REVERSIBILITY_LABEL, True, f"{_DECLARED_PLAN_PREFIX}{declared_plan}")
    statement = (
        REVERSIBILITY_BY_CHANGE_CLASS.get(change_class) if change_class is not None else None
    )
    if statement is None:
        return _fact(_REVERSIBILITY_LABEL, False, _UNKNOWN_REVERSIBILITY)
    beyond = [
        member for member in reach_from_snapshot(snapshot) or () if member != _REPOSITORY_ONLY_REACH
    ]
    if beyond:
        return _fact(
            _REVERSIBILITY_LABEL,
            False,
            f"No rollback plan is declared, and the package declares that it reaches beyond its "
            f"repository ({', '.join(beyond)}), so what a {change_class} change usually allows "
            f"does not establish how to back this one out. The class says: {statement}",
        )
    return _fact(
        _REVERSIBILITY_LABEL,
        True,
        f"{_CLASS_STATEMENT_PREFIX}{change_class} change in general: {statement}",
    )
