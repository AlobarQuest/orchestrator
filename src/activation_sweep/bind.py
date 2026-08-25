"""One pass of the unit-caused lane: bind a release artifact for every unit this machine runs.

ADR-0030 §4 names two lanes. The staleness sweep files an observation about a working copy on a
clock; THIS lane files a `ReleaseArtifactBinding` for a completed work unit whose landing the
machine has actually pulled. They share a program because they share the only expensive thing --
knowing which working copy is which repository -- and nothing else: separate credentials,
separate confined surfaces, separate exit codes.

WHAT IS NOT A FINDING, because this estate has left a control permanently red four times by
getting this wrong. A unit whose landing commit the machine has not pulled yet is **WAITING**: it
is the ordinary state between a unit completing and the next `git pull`, and reporting it would
make this control red for every unit in the gap. A unit already bound is **BOUND**: the lane wrote
it on an earlier pass, and a producer must not turn its own earlier work into an alarm. A
repository with no candidates at all is quiet.

A FINDING is a working copy this pass could not measure, a candidates read that failed, and a
binding the orchestrator refused. All three mean the answer is missing, and a person has to look.

BINDING IS WRITE-ONCE PER UNIT, and the skip is deliberate rather than incidental. The digest is
taken over `HEAD`, which moves; a second pass over an already-bound unit at a moved HEAD would
present the same source tuple with a different digest and be refused as
`release_artifact_conflict` -- a finding on a morning when nothing was wrong. So the orchestrator
reports the existing machine-local binding and this lane skips. The idempotency key is a function
of the unit alone, which leaves it correct for the case the skip cannot cover: a response lost
inside a single pass, where HEAD was read once and the retry presents the same bytes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from activation_sweep.activation import (
    NO,
    OPERATOR_MACHINE_ENVIRONMENT,
    YES,
    ActivationError,
    ActivationFacts,
    RepositoryFacts,
    repository_facts,
)
from activation_sweep.binding import BindingError, content_digest, has_activated
from activation_sweep.binding_client import BindingCallError, ForbiddenEndpointError
from activation_sweep.checkout import Checkout, ForbiddenCommandError, GitError, read_checkout

# What a machine-local binding IS, in the orchestrator's own vocabulary. A literal rather than an
# import: `src/activation_sweep` is a separate program and imports nothing from `orchestrator.*`,
# which `tests/architecture` enforces. The pin is a contract test, not an import.
MACHINE_LOCAL_KIND = "machine_local"

# The recorded fact travels with the row, so a reader can tell WHICH working copy was measured and
# at what HEAD without asking this machine. `summary` is the binding's existing free-form column;
# no new column was needed for a fact this specific to one kind.
DIGEST_METHOD = "git archive HEAD, sha256"

RECOVERABLE = (
    GitError,
    BindingError,
    BindingCallError,
    ActivationError,
    KeyError,
    TypeError,
    ValueError,
)

# The two guard violations, which must NEVER be absorbed. Both subclass a `RECOVERABLE` family, so
# without naming them here a `git pull` reaching the runner, or a write aimed outside the two
# permitted paths, would be reported as "this working copy could not be measured" -- the guards
# firing and nobody hearing it.
UNRECOVERABLE = (ForbiddenCommandError, ForbiddenEndpointError)

WAITING = "waiting"
BOUND = "bound"
RECORDED = "recorded"
UNAVAILABLE = "unavailable"
REFUSED = "refused"

# The activation check's own outcomes, beside the binding's rather than folded into them. A unit
# can be bound and unchecked, or bound and checked, and one field saying "done" would lose which.
#
# SUPERSEDED is the one that reads like a fault and is not. The artifact a binding names is the
# tree its digest was taken over, so once HEAD moves past it that tree is no longer what the next
# start executes and there is nothing left to observe -- ever. It is a fact about time, not about
# the machine, and every future unit avoids it by construction because this lane binds and checks
# in the same pass.
OBSERVED = "observed"
CHECKED = "checked"
SUPERSEDED = "superseded"
UNSATISFIED = "unsatisfied"

# The outcomes that make a pass incomplete: the answer is missing rather than clean.
FINDING_OUTCOMES = frozenset({UNAVAILABLE, REFUSED})

# A fact answered `no`. The pass worked and the machine is not carrying this artifact in full --
# a person has to sync, and the check files nothing until they do, because the ingest refuses a
# second observation carrying different facts and a row written wrong is written wrong forever.
CONDITION_OUTCOMES = frozenset({UNSATISFIED})


class Binder(Protocol):
    """The surface `bind_checkout` needs, structural so a test can pass a hermetic fake."""

    def candidates(self, repository: str) -> list[dict[str, Any]]: ...

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def observe(self, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class NullBinder:
    """A dry-run binder. Reading is real; any WRITE is a bug, so it fails loudly."""

    def __init__(self, source: Binder) -> None:
        self._source = source

    def candidates(self, repository: str) -> list[dict[str, Any]]:
        return self._source.candidates(repository)

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("dry run must not bind release artifacts")

    def observe(self, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("dry run must not record deployment observations")


@dataclass(frozen=True)
class Candidate:
    """One row of the orchestrator's answer, read defensively.

    Every field is required. A response model DROPS every key it does not declare, so a field that
    stopped being served would arrive as absence rather than as an error -- and a binding assembled
    around a missing merge commit would be refused by the orchestrator's own validator anyway, but
    reported here as a rejection rather than as the narrowed contract it is.
    """

    work_unit_id: str
    package_revision_id: str
    package_revision_hash: str
    unit_key: str
    work_unit_version: int
    source_repository: str
    pr_number: int
    source_commit: str
    merge_commit: str
    bound: bool
    binding_id: str | None
    binding_artifact_digest: str | None
    observed: bool

    @classmethod
    def of(cls, row: dict[str, Any]) -> Candidate:
        required = (
            "work_unit_id",
            "work_package_revision_id",
            "package_revision_hash",
            "unit_key",
            "source_repository",
            "pr_number",
            "source_commit",
            "merge_commit",
        )
        # `work_unit_version` is checked for PRESENCE rather than truthiness: 0 is a real version
        # and the only falsy one, so a `not row.get(...)` test would report a brand-new unit's
        # candidate as a narrowed contract.
        missing = [name for name in required if not row.get(name)]
        if row.get("work_unit_version") is None:
            missing.append("work_unit_version")
        if missing:
            raise BindingCallError(
                f"the orchestrator's candidate is missing {', '.join(sorted(missing))}"
            )
        return cls(
            work_unit_id=str(row["work_unit_id"]),
            package_revision_id=str(row["work_package_revision_id"]),
            package_revision_hash=str(row["package_revision_hash"]),
            unit_key=str(row["unit_key"]),
            source_repository=str(row["source_repository"]),
            pr_number=int(row["pr_number"]),
            work_unit_version=int(row["work_unit_version"]),
            source_commit=str(row["source_commit"]),
            merge_commit=str(row["merge_commit"]),
            bound=row.get("binding_id") is not None,
            binding_id=None if row.get("binding_id") is None else str(row["binding_id"]),
            binding_artifact_digest=(
                None
                if row.get("binding_artifact_digest") is None
                else str(row["binding_artifact_digest"])
            ),
            observed=row.get("observation_id") is not None,
        )


def binding_payload(candidate: Candidate, *, path: str, head: str, digest: str) -> dict[str, Any]:
    """The command, with the registry three deliberately absent rather than blanked.

    The orchestrator REFUSES them for this kind: a working copy has no registry, and a placeholder
    would make the two activation models look identical in exactly the columns a reader would use
    to tell them apart. Omitting the keys is what says nobody wrote one.
    """
    return {
        "idempotency_key": f"machine-activation:{candidate.work_unit_id}",
        # A REAL version, read from the candidate. `CommandBase.expected_version` is a required,
        # non-nullable `int`, so `None` here is a 422 from request validation -- before the
        # service, before any of its named errors, and invisible to every test that calls the
        # service directly. Measured in production on this lane's first live pass: twelve
        # refusals, all 422, nothing written.
        "expected_version": candidate.work_unit_version,
        "kind": MACHINE_LOCAL_KIND,
        "package_revision_id": candidate.package_revision_id,
        "package_revision_hash": candidate.package_revision_hash,
        "source_repository": candidate.source_repository,
        "implementation_pr_number": candidate.pr_number,
        "source_commit": candidate.source_commit,
        "merge_commit": candidate.merge_commit,
        "artifact_digest": digest,
        "summary": {
            "activation": {
                "path": path,
                "head": head,
                "digest_method": DIGEST_METHOD,
            }
        },
    }


def activation_payload(
    *,
    work_unit_id: str,
    digest: str,
    head_committed_at: str,
    head: str,
    facts: ActivationFacts,
) -> dict[str, Any]:
    """The activation check, in the orchestrator's own command shape.

    `observed_at` is HEAD'S COMMIT TIME, not a wall clock, for the reason the staleness sweep
    beside this one already records: the ingest refuses a repeat carrying different facts, so a
    clock in the payload would turn a re-run over unchanged reality into a conflict. HEAD's
    commit time moves exactly when the artifact does.

    `expected_version` is 0 because the ingest requires it -- the subject is the binding, which is
    immutable, so there is no unit version to guard here. It is sent explicitly rather than
    omitted: the route's own model requires the field, which is a second rule set the service's
    tests never traverse, and omitting it 422s before any named error can be raised.

    THE KEY IS A FUNCTION OF THE UNIT, exactly as the binding's is, and keying it on the DIGEST
    instead is a defect that reads as correct. Every unit of one repository shares a digest -- six
    of them in `intent-packages` today -- so a digest-keyed key would have the first unit's
    observation written and every sibling refused as `idempotency_conflict`, because the stored
    command names a different binding. One unit has at most one machine-local binding, and a
    moved HEAD is superseded rather than re-observed, so the unit alone identifies this check for
    as long as it can be filed.
    """
    return {
        "idempotency_key": f"machine-activation-check:{work_unit_id}",
        "expected_version": 0,
        "kind": MACHINE_LOCAL_KIND,
        "environment": OPERATOR_MACHINE_ENVIRONMENT,
        "observed_artifact_digest": digest,
        # The ref of what is activated. A working copy has no image tag and no run URL, so the
        # commit is the only honest thing this field can name.
        "deployment_ref": head,
        "observed_at": head_committed_at,
        "activation_summary": facts.summary,
    }


def bind_checkout(
    path: str,
    binder: Binder,
    *,
    fetch: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """One pass over one working copy. Never raises; always returns what it managed to do.

    Per-checkout isolation, the same property the staleness sweep keeps: a working copy that
    cannot be measured must cost that working copy and nothing else. A pass that died on the third
    of four would discard the two it had already filed.
    """
    summary: dict[str, Any] = {
        "checkout": path,
        "unavailable": False,
        # Why, not just that. Four expired-bearer 401s and four broken repositories are the same
        # exit code and the same log line without this.
        "reason": None,
        "units": [],
    }
    try:
        state = read_checkout(Path(path), fetch=fetch)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        summary["unavailable"] = True
        summary["reason"] = str(error)
        return summary
    summary["repository"] = state.repository
    summary["head"] = state.head
    try:
        rows = binder.candidates(state.repository)
        # The digest is computed ONCE per pass, after the candidates read succeeds. Every unit
        # bound in this pass therefore names the same HEAD and the same content, which is what
        # makes the lost-response retry present identical bytes.
        digest = content_digest(Path(state.path)) if rows else ""
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        summary["unavailable"] = True
        summary["reason"] = str(error)
        return summary

    # The two repository-wide facts, measured ONCE and only when there is something to check. A
    # failure here costs the ACTIVATION half alone: binding an artifact does not depend on
    # knowing whether the environment matches its lockfile, and a pass that lost both because
    # `uv` was missing would report the machine as unmeasurable when the harder half worked.
    facts: RepositoryFacts | None = None
    facts_reason: str | None = None
    if rows:
        try:
            facts = repository_facts(Path(state.path))
        except UNRECOVERABLE:
            raise
        except RECOVERABLE as error:
            facts_reason = str(error)
    summary["repository_facts"] = None if facts is None else asdict(facts)

    pass_state = _Pass(
        state=state,
        digest=digest,
        facts=facts,
        facts_reason=facts_reason,
        binder=binder,
        dry_run=dry_run,
    )
    for row in rows:
        summary["units"].append(_consider(row, pass_state))
    return summary


@dataclass(frozen=True)
class _Pass:
    """Everything one pass over one working copy measured once and shares across its units."""

    state: Checkout
    digest: str
    facts: RepositoryFacts | None
    facts_reason: str | None
    binder: Binder
    dry_run: bool


def _consider(row: dict[str, Any], pass_state: _Pass) -> dict[str, Any]:
    try:
        candidate = Candidate.of(row)
    except RECOVERABLE as error:
        return {"outcome": UNAVAILABLE, "reason": str(error)}
    answer: dict[str, Any] = {
        "work_unit_id": candidate.work_unit_id,
        "unit_key": candidate.unit_key,
        "merge_commit": candidate.merge_commit,
    }
    binding_id = _bind_phase(candidate, answer, pass_state)
    if answer["outcome"] in {WAITING, UNAVAILABLE, REFUSED}:
        return answer
    answer["activation"] = _activation_phase(candidate, binding_id, pass_state)
    return answer


def _bind_phase(
    candidate: Candidate,
    answer: dict[str, Any],
    pass_state: _Pass,
) -> str | None:
    """Bind the artifact if it is not bound, and report which. Returns the binding's id.

    A dry run has no id to return, because the binding it would create does not exist -- so the
    activation phase reports what it would file against a binding named `null`, which is the
    honest shape of "both halves would happen in this pass".
    """
    if candidate.bound:
        answer["outcome"] = BOUND
        return candidate.binding_id
    try:
        activated = has_activated(Path(pass_state.state.path), candidate.merge_commit)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        answer["outcome"] = UNAVAILABLE
        answer["reason"] = str(error)
        return None
    if not activated:
        answer["outcome"] = WAITING
        return None
    payload = binding_payload(
        candidate,
        path=pass_state.state.path,
        head=pass_state.state.head,
        digest=pass_state.digest,
    )
    if pass_state.dry_run:
        answer["outcome"] = RECORDED
        answer["dry_run"] = True
        answer["record"] = payload
        return None
    try:
        recorded = pass_state.binder.bind(candidate.work_unit_id, payload)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        answer["outcome"] = REFUSED
        answer["reason"] = str(error)
        return None
    answer["outcome"] = RECORDED
    return None if recorded.get("id") is None else str(recorded["id"])


def _activation_phase(
    candidate: Candidate,
    binding_id: str | None,
    pass_state: _Pass,
) -> dict[str, Any]:
    """Whether the bound artifact is what the next start will execute, and file it if so.

    THE DIGEST IS THE WINDOW. An observation asserts that THIS artifact is live, and the artifact
    is the tree its digest was taken over -- so once HEAD moves past a binding written on an
    earlier pass, that tree is superseded and there is nothing left to observe. Recomputing the
    binding's digest at its recorded head would prove the tree is REACHABLE, which is a different
    and weaker claim than the one a `deployment` hop makes.
    """
    if candidate.observed:
        return {"outcome": CHECKED, "observed_before": True}
    if candidate.bound and candidate.binding_artifact_digest != pass_state.digest:
        return {"outcome": SUPERSEDED, "binding_artifact_digest": candidate.binding_artifact_digest}
    if pass_state.facts is None:
        return {"outcome": UNAVAILABLE, "reason": pass_state.facts_reason}
    try:
        present = has_activated(Path(pass_state.state.path), candidate.merge_commit)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        return {"outcome": UNAVAILABLE, "reason": str(error)}
    facts = ActivationFacts.of(pass_state.facts, merge_commit_present=YES if present else NO)
    if not facts.recordable:
        return {
            "outcome": UNSATISFIED,
            "unsatisfied": list(facts.unsatisfied),
            "activation": facts.summary,
        }
    return _file_activation(
        binding_id,
        activation_payload(
            work_unit_id=candidate.work_unit_id,
            digest=pass_state.digest,
            head_committed_at=pass_state.state.head_committed_at.isoformat(),
            head=pass_state.state.head,
            facts=facts,
        ),
        pass_state,
    )


def _file_activation(
    binding_id: str | None,
    payload: dict[str, Any],
    pass_state: _Pass,
) -> dict[str, Any]:
    """Send the check, or say what would have been sent."""
    if pass_state.dry_run:
        return {"outcome": OBSERVED, "dry_run": True, "binding_id": binding_id, "record": payload}
    if binding_id is None:
        # A binding whose id the orchestrator did not return. The row exists and the next pass
        # will find it as a candidate's `binding_id`, so this is a deferral rather than a fault.
        return {"outcome": UNAVAILABLE, "reason": "the binding carried no id to observe against"}
    try:
        pass_state.binder.observe(binding_id, payload)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        return {"outcome": REFUSED, "reason": str(error)}
    return {"outcome": OBSERVED, "binding_id": binding_id}


def has_findings(summaries: list[dict[str, Any]]) -> bool:
    """A pass is incomplete when any answer is missing. WAITING, BOUND and CHECKED are answers."""
    for summary in summaries:
        if summary["unavailable"]:
            return True
        for unit in summary["units"]:
            if unit.get("outcome") in FINDING_OUTCOMES:
                return True
            if _activation_outcome(unit) in FINDING_OUTCOMES:
                return True
    return False


def has_conditions(summaries: list[dict[str, Any]]) -> bool:
    """A pass found a condition of the MACHINE: an artifact is bound and not fully activated.

    Separate from `has_findings` because the two want different actions. A finding means nobody
    knows; a condition means somebody has to run `uv sync`. SUPERSEDED is neither -- the window
    for observing that artifact has closed and no action reopens it.
    """
    return any(
        _activation_outcome(unit) in CONDITION_OUTCOMES
        for summary in summaries
        for unit in summary["units"]
    )


def _activation_outcome(unit: dict[str, Any]) -> str | None:
    activation = unit.get("activation")
    return activation.get("outcome") if isinstance(activation, dict) else None
