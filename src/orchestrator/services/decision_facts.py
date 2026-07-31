"""The three facts a human needs before deciding anything (WS-P2.17, spec 5.6).

Devon adjudicated a criterion with the rationale "It had words that looked right" -- the page
told him what the criterion *said* and nothing about what agreeing would let happen. The five
human gates are the same shape, so the answer is one projection, rendered by one partial, used at
every gate: **what it does**, **what it affects**, **can we back out**.

Every fact carries an explicit `known` flag. An unknown is a fact, not an absence: a row that is
simply omitted reads as "nothing to worry about", where the truth is "nobody knows yet". The
partial therefore always renders three rows.

This module is a pure projection over rows already loaded by the caller -- no session, no query.
"""

from __future__ import annotations

from typing import Any

from orchestrator.kernel.authority import AuthorityEnvelope, normalize_authority
from orchestrator.persistence.models import WorkPackageRevision, WorkUnit

# What a change of each class costs to undo. Editorial prose, keyed by the `change_class` an
# authority envelope declares; a class with no entry resolves to the explicit unknown rather than
# to a guess. WS-P2.18's policy artifact is where an authoritative per-package override belongs --
# this is deliberately a statement about the class, not about the package.
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
    "This work declares no change class with a recorded way to back it out, so how reversible it "
    "is has not been established."
)
_UNKNOWN_AFFECTS_AT_INTAKE = (
    "The package has not been broken into work units, so no target repository and no mutating "
    "command have been chosen yet."
)
_UNKNOWN_OUTCOME = "This package revision was registered without a recorded outcome statement."

_DOES_LABEL = "What it does"
_AFFECTS_LABEL = "What it affects"
_REVERSIBILITY_LABEL = "Can we back out"


def _fact(label: str, known: bool, detail: str) -> dict[str, Any]:
    return {"label": label, "known": known, "detail": detail}


def decision_facts_for_unit(unit: WorkUnit) -> dict[str, dict[str, Any]]:
    """The three facts for a work unit, whose authority envelope names its blast radius."""
    envelope = normalize_authority(unit.authority)
    return {
        "does": _fact(_DOES_LABEL, True, unit.outcome),
        "affects": _affects_from_envelope(envelope),
        "reversibility": _reversibility(envelope.change_class),
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
        "reversibility": _reversibility(None),
    }


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
    """Target repository, mutating commands and granted capabilities, in that order.

    Known iff a target repository is named: without one, "what it affects" has no answer, however
    much else the envelope grants. The granted capabilities are still reported either way -- an
    unknown fact is not an empty one.
    """
    target = envelope.constraints.get("target_repository")
    parts: list[str] = []
    if isinstance(target, str) and target:
        parts.append(f"Repository {target}")
    else:
        parts.append("No target repository is named in the authority envelope")
    commands = envelope.constraints.get("mutation_commands")
    if isinstance(commands, list) and commands:
        parts.append("runs " + ", ".join(str(command) for command in commands))
    else:
        parts.append("no mutating command is authorized")
    granted = sorted(
        capability
        for capability, level in envelope.capabilities.items()
        if level not in {"prohibited", ""}
    )
    parts.append("grants " + (", ".join(granted) if granted else "no capability"))
    known = isinstance(target, str) and bool(target)
    return _fact(_AFFECTS_LABEL, known, "; ".join(parts) + ".")


def _reversibility(change_class: str | None) -> dict[str, Any]:
    statement = (
        REVERSIBILITY_BY_CHANGE_CLASS.get(change_class) if change_class is not None else None
    )
    if statement is None:
        return _fact(_REVERSIBILITY_LABEL, False, _UNKNOWN_REVERSIBILITY)
    return _fact(_REVERSIBILITY_LABEL, True, f"{change_class}: {statement}")
