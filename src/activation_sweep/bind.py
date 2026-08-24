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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from activation_sweep.binding import BindingError, content_digest, has_activated
from activation_sweep.binding_client import BindingCallError, ForbiddenEndpointError
from activation_sweep.checkout import ForbiddenCommandError, GitError, read_checkout

# What a machine-local binding IS, in the orchestrator's own vocabulary. A literal rather than an
# import: `src/activation_sweep` is a separate program and imports nothing from `orchestrator.*`,
# which `tests/architecture` enforces. The pin is a contract test, not an import.
MACHINE_LOCAL_KIND = "machine_local"

# The recorded fact travels with the row, so a reader can tell WHICH working copy was measured and
# at what HEAD without asking this machine. `summary` is the binding's existing free-form column;
# no new column was needed for a fact this specific to one kind.
DIGEST_METHOD = "git archive HEAD, sha256"

RECOVERABLE = (GitError, BindingError, BindingCallError, KeyError, TypeError, ValueError)

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

# The outcomes that make a pass incomplete: the answer is missing rather than clean.
FINDING_OUTCOMES = frozenset({UNAVAILABLE, REFUSED})


class Binder(Protocol):
    """The surface `bind_checkout` needs, structural so a test can pass a hermetic fake."""

    def candidates(self, repository: str) -> list[dict[str, Any]]: ...

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class NullBinder:
    """A dry-run binder. Reading is real; any WRITE is a bug, so it fails loudly."""

    def __init__(self, source: Binder) -> None:
        self._source = source

    def candidates(self, repository: str) -> list[dict[str, Any]]:
        return self._source.candidates(repository)

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("dry run must not bind release artifacts")


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
    source_repository: str
    pr_number: int
    source_commit: str
    merge_commit: str
    bound: bool

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
        missing = [name for name in required if not row.get(name)]
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
            source_commit=str(row["source_commit"]),
            merge_commit=str(row["merge_commit"]),
            bound=row.get("binding_id") is not None,
        )


def binding_payload(candidate: Candidate, *, path: str, head: str, digest: str) -> dict[str, Any]:
    """The command, with the registry three deliberately absent rather than blanked.

    The orchestrator REFUSES them for this kind: a working copy has no registry, and a placeholder
    would make the two activation models look identical in exactly the columns a reader would use
    to tell them apart. Omitting the keys is what says nobody wrote one.
    """
    return {
        "idempotency_key": f"machine-activation:{candidate.work_unit_id}",
        "expected_version": None,
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

    for row in rows:
        summary["units"].append(
            _consider(row, state.path, state.head, digest, binder, dry_run=dry_run)
        )
    return summary


def _consider(
    row: dict[str, Any],
    path: str,
    head: str,
    digest: str,
    binder: Binder,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    try:
        candidate = Candidate.of(row)
    except RECOVERABLE as error:
        return {"outcome": UNAVAILABLE, "reason": str(error)}
    answer: dict[str, Any] = {
        "work_unit_id": candidate.work_unit_id,
        "unit_key": candidate.unit_key,
        "merge_commit": candidate.merge_commit,
    }
    if candidate.bound:
        answer["outcome"] = BOUND
        return answer
    try:
        activated = has_activated(Path(path), candidate.merge_commit)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        answer["outcome"] = UNAVAILABLE
        answer["reason"] = str(error)
        return answer
    if not activated:
        answer["outcome"] = WAITING
        return answer
    payload = binding_payload(candidate, path=path, head=head, digest=digest)
    if dry_run:
        answer["outcome"] = RECORDED
        answer["dry_run"] = True
        answer["record"] = payload
        return answer
    try:
        binder.bind(candidate.work_unit_id, payload)
    except UNRECOVERABLE:
        raise
    except RECOVERABLE as error:
        answer["outcome"] = REFUSED
        answer["reason"] = str(error)
        return answer
    answer["outcome"] = RECORDED
    return answer


def has_findings(summaries: list[dict[str, Any]]) -> bool:
    """A pass is incomplete when any answer is missing. WAITING and BOUND are answers."""
    for summary in summaries:
        if summary["unavailable"]:
            return True
        if any(unit.get("outcome") in FINDING_OUTCOMES for unit in summary["units"]):
            return True
    return False
