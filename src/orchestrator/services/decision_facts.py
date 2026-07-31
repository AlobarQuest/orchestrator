"""The three facts a human needs before deciding anything (WS-P2.17, spec 5.6).

Devon adjudicated a criterion with the rationale "It had words that looked right" -- the page
told him what the criterion *said* and nothing about what agreeing would let happen. The five
human gates are the same shape, so the answer is one projection, rendered by one partial, used at
every gate: **what it does**, **what it affects**, **can we back out**.

Every fact carries an explicit `known` flag. An unknown is a fact, not an absence: a row that is
simply omitted reads as "nothing to worry about", where the truth is "nobody knows yet". The
partial therefore always renders three rows.

This module is a pure projection over rows already loaded by the caller -- no session, no query.
Both entry points take the package revision, because the enforcement snapshot is where the
package's author-written answers live.
"""

from __future__ import annotations

from typing import Any

from orchestrator.kernel.authority import AuthorityEnvelope, normalize_authority
from orchestrator.persistence.models import WorkPackageRevision, WorkUnit

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
    "The package has not been broken into work units, so no target repository and no mutating "
    "command have been chosen yet."
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
        "affects": _affects_from_envelope(envelope),
        "reversibility": _reversibility(envelope.change_class, _declared_rollback_plan(revision)),
    }


def decision_facts_for_revision(revision: WorkPackageRevision) -> dict[str, dict[str, Any]]:
    """The three facts for a package revision at intake.

    "What it affects" is genuinely unanswerable here and says so: the package-level authority
    block is a capability declaration (`allowed`/`requires_approval`/`prohibited`), and the
    target repository is chosen per unit when the package is broken up.
    """
    outcome = _package_outcome(revision)
    return {
        "does": _fact(_DOES_LABEL, outcome is not None, outcome or _UNKNOWN_OUTCOME),
        "affects": _fact(_AFFECTS_LABEL, False, _UNKNOWN_AFFECTS_AT_INTAKE),
        # No change class exists here -- the package-level authority block is a capability
        # declaration, not an envelope -- so intake is answerable only from the declared plan.
        "reversibility": _reversibility(None, _declared_rollback_plan(revision)),
    }


def _declared_rollback_plan(revision: WorkPackageRevision) -> str | None:
    """`profile_fields.rollback_plan` from the enforcement snapshot, or `None`.

    Three of the five intent-package profiles require this field as a non-empty string, and
    `package_sources.py` copies `profile_fields` into the snapshot verbatim -- so for those
    packages the orchestrator has held an author-written answer to "can we back out" since intake.
    The snapshot is data from another repository and nothing here validates its shape, so a
    missing, mistyped or blank plan falls back rather than rendering an empty commitment.
    """
    snapshot = revision.enforcement_snapshot
    fields = snapshot.get("profile_fields") if isinstance(snapshot, dict) else None
    plan = fields.get("rollback_plan") if isinstance(fields, dict) else None
    return plan.strip() if isinstance(plan, str) and plan.strip() else None


def _package_outcome(revision: WorkPackageRevision) -> str | None:
    """`outcome.what` from the enforcement snapshot, tolerating a bare string.

    `package_sources.py` copies the intent package's whole `outcome` block, so production
    snapshots carry the mapping. The orchestrator never validates the snapshot's shape, and
    revisions registered by other callers carry `outcome` as a plain string -- read both rather
    than reporting a recorded outcome as absent.
    """
    snapshot = revision.enforcement_snapshot
    outcome = snapshot.get("outcome") if isinstance(snapshot, dict) else None
    if isinstance(outcome, str):
        return outcome or None
    what = outcome.get("what") if isinstance(outcome, dict) else None
    return what if isinstance(what, str) and what else None


def _affects_from_envelope(envelope: AuthorityEnvelope) -> dict[str, Any]:
    """Everything the envelope declares about what this work touches, in its own terms.

    The two repository-shaped keys keep their prominence, and while the envelope is repository-
    shaped at all, the missing half of that pair is still stated as the negative it is: a unit
    that names a repository but authorizes no mutating command is repository work with nothing to
    change, which is worth saying.

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
        parts.append(
            "runs " + ", ".join(str(command) for command in commands)
            if isinstance(commands, list) and commands
            else "no mutating command is authorized"
        )
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


def _reversibility(change_class: str | None, declared_plan: str | None) -> dict[str, Any]:
    """Declared plan, then class statement, then the explicit unknown.

    The order is the point. A plan the package's author wrote is a per-package commitment; the
    class statement is prose about a category that happens to contain this package. Rendering the
    second while holding the first is strictly worse information.
    """
    if declared_plan is not None:
        return _fact(_REVERSIBILITY_LABEL, True, f"{_DECLARED_PLAN_PREFIX}{declared_plan}")
    statement = (
        REVERSIBILITY_BY_CHANGE_CLASS.get(change_class) if change_class is not None else None
    )
    if statement is None:
        return _fact(_REVERSIBILITY_LABEL, False, _UNKNOWN_REVERSIBILITY)
    return _fact(
        _REVERSIBILITY_LABEL,
        True,
        f"{_CLASS_STATEMENT_PREFIX}{change_class} change in general: {statement}",
    )
