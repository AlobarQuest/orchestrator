"""Which work unit, if any, a landing belongs to — and the observation that records its rollout.

ADR-0022, the second half. Phase-3's exit criterion asks that a real release's traceability chain
CARRY a real observation, and `services/traceability.py` filters that hop on
`subject_type="work_unit"`. Measured 2026-08-12: of 553 observations, 509 are repo-scoped, 39
service, 1 deployment and 4 work-unit — all four written by `orchestrator-system`, none by an
external producer. Every unconnected producer in the estate's census is estate-, service- or
repo-scoped, so connecting all of them adds nothing to that number. The watcher can add to it,
because a rollout it observes may be the rollout a work unit's landing caused.

## The claim is not the answer, and this is the whole design

`SDS-Unit:` is a trailer factory-runner writes into its own commit message. Recording an
observation against whatever unit a commit names would let the commit choose its own subject —
which is the runner attesting to its own compliance, one artifact over. So the trailer SELECTS
which unit to ask about, and the orchestrator's own `pr_merge` record of its own act is what
confirms it: same repository, same pull request, same merge commit, and a status that actually
asserts the orchestrator made this landing.

`already_merged` and `refused` are deliberately NOT accepted. Read the five writers in
`services/pr_merge.py` before widening: `already_merged` has two, one being a pull request somebody
else had landed BEFORE the merge call, and `refused` has two, one being the genuinely ambiguous
outcome. Neither status can assert authorship, and an observation attributing a rollout to a unit
that may not have caused it is exactly the fiction ADR-0022 refuses.

## An absent claim is the ordinary case, never a finding

Almost every landing this watcher sees is a Dependabot update with no unit at all, and whether a
trailer survives a squash is a repository SETTING. So no claim means no observation and nothing to
report. A claim the orchestrator does NOT confirm is different: something asserted a unit and the
durable record disagrees, which is a fact about the estate and is reported.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from deploy_watcher.model import Rollout
from deploy_watcher.orchestrator import WORK_UNIT_ID, is_work_unit_id

# The trailer factory-runner writes into its COMMIT MESSAGE (`factory_runner/cli.py`), matched the
# way `landing_ledger/github.py` matches it: anchored per line, so a mention of a unit id in prose
# elsewhere in the message is not a claim.
SDS_UNIT = re.compile(rf"^\s*SDS-Unit:\s*({WORK_UNIT_ID})\s*$", re.MULTILINE)

# The one `pr_merge` status that asserts the ORCHESTRATOR made this landing.
LANDED_MERGE_STATUS = "merged"

# What the observation says, in the orchestrator's own closed vocabularies
# (`persistence/models.py`). Nothing here is new: `deployment_observation` is literally what this
# program produces, and a rollout is a deployment. A new vocabulary member would be a migration and
# a `CheckConstraint` change in a repository this program is deliberately not part of.
SOURCE_SYSTEM = "deployment_observation"
SUBJECT_TYPE = "work_unit"
OBSERVATION_TYPE = "deployment"
# `delivery_system` rather than `orchestrator`: this is a separate program reporting on the
# estate's behalf, which is what the landing ledger records for the same reason.
TRUST_CLASSIFICATION = "delivery_system"
# A CONSTANT, and that is a decision rather than an omission. Severity would be a second judgment
# about a rollout that `status` and `facts` already state, consumed by nothing -- and the alarm for
# a failed rollout is this program's exit code and the finding it prints, which reach a person.
SEVERITY = "info"

# The rollout verdicts this program derives, mapped onto the orchestrator's OWN observation
# statuses (`persistence/models.py`), which are `passed | failed | degraded | healthy | unhealthy |
# unknown | observed`. Note `success` is NOT among them: the two vocabularies are close enough to
# read as one and are not, which is the cross-boundary mismatch this estate keeps rediscovering.
# An unrecognised verdict degrades to `unknown` rather than being upgraded -- the same direction
# change-manager's own `verdict_for` takes for a conclusion GitHub invents later.
_STATUS_FOR = {"success": "passed", "failed": "failed"}
STATUS_INCONCLUSIVE = "unknown"

# An observation is emitted for EVERY verdict, not only a success. A rollout that failed is exactly
# the fact a unit's traceability chain should carry, and it is the settlement -- a decision -- that
# is conditioned on success. Facts and decisions are split by ADR-0021, and this is that split.

# Findings this module can produce.
UNIT_CLAIM_UNBOUND = "a_landing_claims_a_work_unit_the_orchestrator_does_not_bind_to_it"
UNIT_CLAIM_UNKNOWN = "a_landing_claims_a_work_unit_the_orchestrator_does_not_hold"


@dataclass(frozen=True)
class UnitLanding:
    """A landing the orchestrator's own record binds to a work unit."""

    work_unit_id: str
    repository: str
    pull_request_number: int
    merge_commit_sha: str


def claimed_unit(commit_message: str | None) -> str | None:
    """The work unit a landing commit claims, or None. A CLAIM; never an answer."""
    if not commit_message:
        return None
    match = SDS_UNIT.search(commit_message)
    return match.group(1) if match else None


def binds(
    history: list[dict[str, Any]],
    *,
    repository: str,
    pull_request_number: int,
    merge_commit_sha: str,
) -> bool:
    """Does the orchestrator's own history record IT landing THIS pull request as THIS commit?

    Matched on every coordinate at once rather than on the action alone: a unit that landed some
    other pull request would satisfy an action-only match while proving nothing about this rollout.
    The repository is case-folded on both sides -- the orchestrator lowercases what it records and
    change-manager stores whatever the proposer sent, and production holds both spellings today, so
    an exact comparison would refuse a binding that is real.
    """
    wanted = repository.lower()
    for event in history:
        if not str(event.get("action", "")).startswith("pr_merge."):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("status") == LANDED_MERGE_STATUS
            and str(payload.get("repository", "")).lower() == wanted
            and payload.get("pr_number") == pull_request_number
            and str(payload.get("merge_commit_sha", "")).lower() == merge_commit_sha.lower()
        ):
            return True
    return False


def status_for(verdict: str) -> str:
    """The observation status for a rollout verdict. An unrecognised verdict is inconclusive."""
    return _STATUS_FOR.get(verdict, STATUS_INCONCLUSIVE)


def unit_observation(
    landing: UnitLanding, rollout: Rollout, *, verdict: str, production_reached: str
) -> dict[str, Any]:
    """The observation body. Facts about a rollout, attributed to the unit that caused it.

    ## `source_reference` IDENTIFIES THE ATTEMPT AND CARRIES THE FACT DIGEST, and copying the
    ## landing ledger's rule here instead was measured to wedge the whole pass permanently.

    The orchestrator refuses a second observation with the same `(source_system, source_reference)`
    and different facts — `observation_conflict`, no supersession, no delete
    (`services/observations.py`). The landing ledger's reference is deliberately NOT
    content-addressed and that is right THERE, because its subject is a commit on a branch, which
    is immutable: a changed fact means something is wrong, and raising is the point.

    **A ROLLOUT IS NOT IMMUTABLE.** It is re-run — six failing rollout attempts across three runs
    in these two repositories alone — and change-manager's own reduction rule exists precisely
    because the answer legitimately changes (`absent` at 09:00, `success` at 09:30; `unknown` for a
    cancelled first attempt, `success` for the re-run). What a green run ATTESTS can move too, when
    somebody transcribes a workflow revision nobody had classified. Under a landing-shaped
    reference the second pass 409s, the watcher reads that as `incomplete`, and **every hourly pass
    from then on exits 3 while the successful rollout is never attributed to the unit** — the
    permanently-red control ADR-0022 exists to remove, rebuilt inside its own second half. Measured
    against a migrated database, not reasoned about.

    So the reference mirrors change-manager's `observation_key` — the same identity, for the same
    rows, chosen for the same reason: `(merge commit, run id, attempt, fact digest)`, or `no-run`
    where no run existed when the pass looked. An unchanged re-read replays; a re-run appends a
    second row; the history keeps both, because both happened.

    `observed_at` is UPSTREAM'S clock — when the rollout run concluded — and never the pass's. With
    a wall-clock timestamp every hourly pass would append a row for unchanged reality, forever.

    NOTHING DATED, COUNTED, OR SOURCE-NAMING GOES IN `facts`. That rule still holds, and the
    digest does not excuse it: a fact that says where a value was read, or how many of something
    there were, opens a new row every time it moves and buries the ones that matter.
    """
    run = rollout.run
    facts: dict[str, Any] = {
        "rollout_verdict": verdict,
        "production_reached": production_reached,
        "workflow_attests": rollout.attestation,
        "workflow_path": rollout.workflow_path,
        "merge_commit": landing.merge_commit_sha,
        "pull_request": landing.pull_request_number,
        "repository": landing.repository.lower(),
    }
    if run is not None:
        facts["run_id"] = run.run_id
        facts["run_attempt"] = run.run_attempt
        facts["run_conclusion"] = run.conclusion
    reference = _reference(landing, rollout, _fact_digest(facts))
    return {
        "idempotency_key": reference,
        "expected_version": 0,
        "source_system": SOURCE_SYSTEM,
        "source_reference": reference,
        "source_url": run.run_url
        if run is not None
        else f"https://github.com/{landing.repository}/commit/{landing.merge_commit_sha}",
        "trust_classification": TRUST_CLASSIFICATION,
        "subject_type": SUBJECT_TYPE,
        "subject_reference": landing.work_unit_id,
        "environment": None,
        "observation_type": OBSERVATION_TYPE,
        "status": status_for(verdict),
        "severity": SEVERITY,
        "observed_at": _observed_at(rollout).isoformat(),
        "summary": (
            f"the rollout of {landing.merge_commit_sha[:12]} into "
            f"{landing.repository.lower()} concluded {verdict}, production_reached="
            f"{production_reached}"
        )[:512],
        "facts": facts,
        "payload_digest": None,
    }


def _reference(landing: UnitLanding, rollout: Rollout, digest: str) -> str:
    """The identity of one observation of one rollout attempt.

    Keyed on the ATTEMPT, not the run: a re-run supersedes its predecessor and concludes
    differently, and both are true things that happened. Content-addressed over the derived facts
    as well, so a changed fact appends rather than colliding — the reasoning is in
    `unit_observation`, and it is change-manager's `observation_key`, one repository over.

    Bounded well inside the orchestrator's 512-character reference limit: the longest form is
    about 110 characters.
    """
    run = rollout.run
    attempt = "no-run" if run is None else f"{run.run_id}:{run.run_attempt}"
    return f"rollout:{landing.repository.lower()}@{landing.merge_commit_sha}:{attempt}:{digest}"


def _observed_at(rollout: Rollout) -> datetime:
    """Upstream's clock: when the rollout concluded, or failing that when the merge landed.

    A run that never existed has no conclusion time, and the merge time is the next most stable
    fact about the same event. Neither is this pass's clock, which is the property that matters:
    a timestamp that moves makes every pass a different row.
    """
    run = rollout.run
    if run is not None and run.concluded_at is not None:
        return run.concluded_at
    assert rollout.merge.merged_at is not None
    return rollout.merge.merged_at


def _fact_digest(facts: dict[str, Any]) -> str:
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "LANDED_MERGE_STATUS",
    "OBSERVATION_TYPE",
    "SDS_UNIT",
    "SOURCE_SYSTEM",
    "STATUS_INCONCLUSIVE",
    "SUBJECT_TYPE",
    "TRUST_CLASSIFICATION",
    "UNIT_CLAIM_UNBOUND",
    "UNIT_CLAIM_UNKNOWN",
    "UnitLanding",
    "binds",
    "claimed_unit",
    "is_work_unit_id",
    "status_for",
    "unit_observation",
]
