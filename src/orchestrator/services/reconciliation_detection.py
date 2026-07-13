"""Conflict detection on pushed observations (AC-001..003).

Detection runs on the ingest path and therefore **never raises**. A malformed or unknown
correlation is skipped and COUNTED. If detection could raise, a forged correlation field would
turn a valid observation into a rejected ingest -- a denial of service on the observation path.
Fail-open, but never silently: the counters make a miss observable.

It also never writes `work_units` and never transitions. Detection surfaces a divergence for an
operator decision; it never auto-un-completes a completed unit and never auto-resolves.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import (
    DeploymentObservation,
    Observation,
    ProductionDrillResource,
    ReleaseArtifactBinding,
    WorkUnit,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_bindings import get_pr_binding
from orchestrator.services.production_drills import production_drill_deadlines
from orchestrator.services.reconciliation import (
    ConditionCommand,
    ConditionOutcome,
    record_reconciliation_condition,
)

EXTERNAL_MERGE_ALARM = "external_merge_alarm"
PR_STATE_DIVERGENCE = "pr_state_divergence"
CHECK_RESULT_FLIP = "check_result_flip"
DEPLOY_SPLIT_BRAIN = "deploy_split_brain"
DIGEST_DIVERGENCE = "digest_divergence"

SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class DetectionCounters:
    """Fail-open is COUNTED, not silent: a skipped correlation or a suppressed duplicate is a
    miss, and a miss nobody can see is indistinguishable from a system with nothing to report."""

    conditions_recorded: int = 0
    skipped_correlations: int = 0
    suppressed_duplicates: int = 0

    def __add__(self, other: "DetectionCounters") -> "DetectionCounters":
        return DetectionCounters(
            self.conditions_recorded + other.conditions_recorded,
            self.skipped_correlations + other.skipped_correlations,
            self.suppressed_duplicates + other.suppressed_duplicates,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "conditions_recorded": self.conditions_recorded,
            "skipped_correlations": self.skipped_correlations,
            "suppressed_duplicates": self.suppressed_duplicates,
        }


SKIPPED = DetectionCounters(skipped_correlations=1)


def detect_observation_conditions(
    session: Session,
    observation: Observation,
    actor: ActorContext,
) -> DetectionCounters:
    """The post-commit ingest hook. `record_observation` has already committed, so this runs in
    the next transaction -- a rejected ingest never reaches it, and a detection failure cannot
    roll the observation back."""
    try:
        if observation.subject_type == "work_unit":
            if observation.observation_type == "github_pr":
                return _detect_pull_request(session, observation, actor)
            if observation.observation_type == "github_check":
                return _detect_check(session, observation, actor)
        return DetectionCounters()
    except Exception:
        session.rollback()
        return SKIPPED


def _detect_pull_request(
    session: Session,
    observation: Observation,
    actor: ActorContext,
) -> DetectionCounters:
    unit = _correlated_unit(session, observation)
    if unit is None:
        return SKIPPED
    facts = _pull_request_facts(observation.facts)
    if facts is None:
        return SKIPPED
    binding = get_pr_binding(session, unit.id)
    # Cross-check the PR IDENTITY -- and identity is the pr_number, never the head. Gating on
    # `head_sha == binding.head_sha` would make the head-change rule below unfireable by
    # construction: the only observations that reached it would be the ones whose head already
    # matched. The head is a rule INPUT, not a correlation gate.
    if binding is None or binding.pr_number != facts["pr_number"]:
        return SKIPPED
    if _current_observation(session, observation.subject_reference, "github_pr") != observation:
        # A late-arriving OLDER fact. The current one has already been (or will be) evaluated.
        return DetectionCounters()

    stored = {
        "state": unit.state,
        "pr_number": binding.pr_number,
        "head_sha": binding.head_sha,
        "verification_read_head_sha": binding.verification_read_head_sha,
    }
    completed = WorkUnitState(unit.state) is WorkUnitState.COMPLETED
    counters = DetectionCounters()

    if facts["merged"] and not completed:
        counters += _record(
            session,
            actor,
            unit,
            observation,
            condition_type=EXTERNAL_MERGE_ALARM,
            key_facts={"pr_number": facts["pr_number"], "head_sha": facts["head_sha"]},
            stored_state=stored,
            observed_state=facts,
            detail="pull request was merged outside the session before the unit completed",
        )
    if completed:
        # Never un-complete, and do not re-alarm on a unit that legitimately finished.
        return counters

    read_head = binding.verification_read_head_sha
    if facts["state"] == "closed" and not facts["merged"]:
        counters += _record(
            session,
            actor,
            unit,
            observation,
            condition_type=PR_STATE_DIVERGENCE,
            key_facts={"pr_number": facts["pr_number"], "head_sha": facts["head_sha"]},
            stored_state=stored,
            observed_state=facts,
            detail="pull request was closed outside the session",
        )
    elif read_head is not None and facts["head_sha"] != read_head:
        # The alarm arms only once verification has READ a head. Before that, a rebase or
        # force-push is normal iteration and must not fire.
        counters += _record(
            session,
            actor,
            unit,
            observation,
            condition_type=PR_STATE_DIVERGENCE,
            key_facts={"pr_number": facts["pr_number"], "head_sha": facts["head_sha"]},
            stored_state=stored,
            observed_state=facts,
            detail="pull request head changed after verification read it",
        )
    return counters


def _record(
    session: Session,
    actor: ActorContext,
    unit: WorkUnit,
    observation: Observation,
    *,
    condition_type: str,
    key_facts: dict[str, Any],
    stored_state: dict[str, Any],
    observed_state: dict[str, Any],
    detail: str,
) -> DetectionCounters:
    outcome = record_reconciliation_condition(
        session,
        ConditionCommand(
            actor=actor,
            work_unit_id=unit.id,
            observation_kind=observation.observation_type,
            condition_type=condition_type,
            key_facts=key_facts,
            stored_state=stored_state,
            observed_state=observed_state,
            detail=detail,
            observation_id=observation.id,
        ),
    )
    if isinstance(outcome, DomainError):
        return SKIPPED
    if not isinstance(outcome, ConditionOutcome):  # pragma: no cover - defensive
        return SKIPPED
    if outcome.suppressed:
        return DetectionCounters(suppressed_duplicates=1)
    return DetectionCounters(conditions_recorded=1)


def _correlated_unit(session: Session, observation: Observation) -> WorkUnit | None:
    try:
        unit_id = uuid.UUID(observation.subject_reference)
    except ValueError:
        return None
    return session.get(WorkUnit, unit_id)


def _current_observation(
    session: Session,
    subject_reference: str,
    observation_type: str,
    partition: tuple[str, object] | None = None,
) -> Observation | None:
    """The current observation is the newest by `(observed_at, received_at, id)` -- never
    `observed_at` alone. Two upstream facts can carry the same upstream timestamp, and upstream
    clocks skew."""
    stmt = select(Observation).where(
        Observation.subject_type == "work_unit",
        Observation.subject_reference == subject_reference,
        Observation.observation_type == observation_type,
    )
    if partition is not None:
        name, value = partition
        stmt = stmt.where(Observation.facts[name].astext == str(value))
    stmt = stmt.order_by(
        Observation.observed_at.desc(),
        Observation.received_at.desc(),
        Observation.id.desc(),
    ).limit(1)
    return session.scalar(stmt)


def _pull_request_facts(facts: dict[str, Any]) -> dict[str, Any] | None:
    number = facts.get("pr_number")
    head_sha = facts.get("head_sha")
    state = facts.get("state")
    merged = facts.get("merged")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or not isinstance(head_sha, str)
        or SHA.fullmatch(head_sha) is None
        or not isinstance(state, str)
        or not isinstance(merged, bool)
    ):
        return None
    return {"pr_number": number, "head_sha": head_sha, "state": state, "merged": merged}


def _detect_check(
    session: Session,
    observation: Observation,
    actor: ActorContext,
) -> DetectionCounters:
    """AC-002. A check that was GREEN at the head verification actually read, and is now RED,
    contradicts what verification concluded. A check that simply fails was never green -- that is
    an ordinary red build, not reality disagreeing with stored state."""
    unit = _correlated_unit(session, observation)
    if unit is None:
        return SKIPPED
    facts = _check_facts(observation.facts)
    if facts is None:
        return SKIPPED
    binding = get_pr_binding(session, unit.id)
    if binding is None or binding.pr_number != facts["pr_number"]:
        return SKIPPED
    # Newest wins PER CHECK NAME: Security going red says nothing about Quality.
    current = _current_observation(
        session,
        observation.subject_reference,
        "github_check",
        ("check_name", facts["check_name"]),
    )
    if current != observation:
        return DetectionCounters()

    read_head = binding.verification_read_head_sha
    if read_head is None or facts["conclusion"] != "failure":
        return DetectionCounters()
    if not _was_green_at(session, observation, facts["check_name"], read_head):
        return DetectionCounters()

    return _record(
        session,
        actor,
        unit,
        observation,
        condition_type=CHECK_RESULT_FLIP,
        key_facts={"check_name": facts["check_name"]},
        stored_state={
            "check_name": facts["check_name"],
            "conclusion": "success",
            "head_sha": read_head,
        },
        observed_state=facts,
        detail="check succeeded when verification read it and has since failed",
    )


def _was_green_at(
    session: Session,
    observation: Observation,
    check_name: str,
    read_head: str,
) -> bool:
    """AC-002's supersession is COMPUTED over the append-only rows, not stored: an earlier
    observation for this (unit, check) that was `success` on the head verification read."""
    earlier = session.scalar(
        select(Observation.id)
        .where(
            Observation.id != observation.id,
            Observation.subject_type == "work_unit",
            Observation.subject_reference == observation.subject_reference,
            Observation.observation_type == "github_check",
            Observation.facts["check_name"].astext == check_name,
            Observation.facts["conclusion"].astext == "success",
            Observation.facts["head_sha"].astext == read_head,
        )
        .limit(1)
    )
    return earlier is not None


def _check_facts(facts: dict[str, Any]) -> dict[str, Any] | None:
    number = facts.get("pr_number")
    head_sha = facts.get("head_sha")
    check_name = facts.get("check_name")
    conclusion = facts.get("conclusion")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or not isinstance(head_sha, str)
        or SHA.fullmatch(head_sha) is None
        or not isinstance(check_name, str)
        or not check_name.strip()
        or not isinstance(conclusion, str)
        or not conclusion.strip()
    ):
        return None
    return {
        "pr_number": number,
        "head_sha": head_sha,
        "check_name": check_name,
        "conclusion": conclusion,
    }


def record_digest_divergence(
    session: Session,
    *,
    actor: ActorContext,
    release_artifact_binding_id: uuid.UUID,
    observed_artifact_digest: str,
    environment: str,
) -> DetectionCounters:
    """AC-003's digest half, and it CANNOT live inside the ingest service.

    The digest guard raises `deployment_observation_digest_mismatch` and the deployment-ingest
    service rolls the transaction back, so a condition written in there would be erased along
    with the rejected observation. This is therefore called from the ROUTE, in its own
    transaction, after the DomainError has been caught -- and the ingest stays rejected.

    Never raises: the caller still has a rejection to return.
    """
    try:
        binding = session.get(ReleaseArtifactBinding, release_artifact_binding_id)
        if binding is None:
            return SKIPPED
        outcome = record_reconciliation_condition(
            session,
            ConditionCommand(
                actor=actor,
                work_unit_id=binding.work_unit_id,
                observation_kind="deployment",
                condition_type=DIGEST_DIVERGENCE,
                key_facts={"release_artifact_binding_id": str(binding.id)},
                stored_state={
                    "artifact_digest": binding.artifact_digest,
                    "release_artifact_binding_id": str(binding.id),
                },
                observed_state={
                    "observed_artifact_digest": observed_artifact_digest,
                    "environment": environment,
                },
                detail="observed artifact digest does not match the immutable release binding",
            ),
        )
        if not isinstance(outcome, ConditionOutcome):
            return SKIPPED
        if outcome.suppressed:
            return DetectionCounters(suppressed_duplicates=1)
        return DetectionCounters(conditions_recorded=1)
    except Exception:
        session.rollback()
        return SKIPPED


def detect_reconciliation_conditions(
    session: Session,
    actor: ActorContext,
    *,
    stall_seconds: int,
    production_drill_run_id: uuid.UUID | None = None,
) -> DetectionCounters:
    """AC-003. Operator/runner-invoked: it creates no unit and sets no lifecycle state.

    This is the one approved deviation from on-ingest detection, and it is structural rather than
    a shortcut. The post-deploy verification unit is minted SUBMITTED *inside* the
    deployment-ingest transaction -- zero seconds old -- so "verification stalled" is a statement
    about elapsed time that cannot be true at the instant of ingest under any design.

    Never raises. Each rule is independently fail-open and counted.
    """
    counters = DetectionCounters()
    for rule in (_detect_stalled_verifications, _detect_unreported_deploys):
        try:
            counters += rule(session, actor, stall_seconds, production_drill_run_id)
        except Exception:
            session.rollback()
            counters += SKIPPED
    return counters


def _detect_stalled_verifications(
    session: Session,
    actor: ActorContext,
    stall_seconds: int,
    production_drill_run_id: uuid.UUID | None = None,
) -> DetectionCounters:
    """A deploy that succeeded while its verification never finished.

    The DeploymentObservation row's existence already PROVES the deploy succeeded -- it only
    exists because an accepted ingest created it -- so no runner-pushed deploy observation is
    needed as a precondition here.
    """
    if production_drill_run_id is not None:
        deadlines = production_drill_deadlines(session, production_drill_run_id)
        if isinstance(deadlines, DomainError):
            return SKIPPED
        stall_seconds = int(deadlines.reporting_deadline.total_seconds())
    deadline = TransactionClock().now(session) - timedelta(seconds=stall_seconds)
    stalled = tuple(
        session.execute(
            select(DeploymentObservation, WorkUnit)
            .join(WorkUnit, WorkUnit.id == DeploymentObservation.post_deploy_work_unit_id)
            .where(
                WorkUnit.state == WorkUnitState.SUBMITTED,
                WorkUnit.created_at <= deadline,
            )
            .order_by(DeploymentObservation.id)
        )
    )
    if production_drill_run_id is not None:
        stalled = tuple(
            row
            for row in stalled
            if _run_owns_resource(session, production_drill_run_id, "work_unit", row[1].id)
            and _run_owns_resource(
                session, production_drill_run_id, "deployment_observation", row[0].id
            )
        )
    counters = DetectionCounters()
    for observation, unit in stalled:
        counters += _record_split_brain(
            session,
            actor,
            work_unit_id=unit.id,
            key_facts={"release_artifact_binding_id": str(observation.release_artifact_binding_id)},
            stored_state={"state": unit.state, "created_at": unit.created_at.isoformat()},
            observed_state={
                "environment": observation.environment,
                "observed_artifact_digest": observation.observed_artifact_digest,
                "deployment_ref": observation.deployment_ref,
                "stall_seconds": stall_seconds,
            },
            detail=(
                "deployment succeeded but its post-deploy verification has not completed "
                f"within {stall_seconds}s"
            ),
            deployment_observation_id=observation.id,
        )
    return counters


def _detect_unreported_deploys(
    session: Session,
    actor: ActorContext,
    stall_seconds: int,
    production_drill_run_id: uuid.UUID | None = None,
) -> DetectionCounters:
    """The deploy NOBODY reported -- the case ADR-0002 rejects Alternative A over.

    With no ingested deployment observation there is no post-deploy unit at all, so
    elapsed-since-SUBMITTED can never fire and the stalled-verification rule above is blind to it.
    A runner-reported deploy for a binding that has no verification unit IS the signal. It needs
    no threshold: nothing was ever ingested, so there is nothing to time out.
    """
    del stall_seconds
    reported = tuple(
        session.scalars(
            select(Observation)
            .where(
                Observation.observation_type == "deployment",
                Observation.subject_type == "release_binding",
            )
            .order_by(Observation.id)
        )
    )
    counters = DetectionCounters()
    for observation in reported:
        if production_drill_run_id is not None and not _run_owns_resource(
            session, production_drill_run_id, "observation", observation.id
        ):
            continue
        binding = _correlated_binding(session, observation)
        if binding is None:
            counters += SKIPPED
            continue
        if production_drill_run_id is not None and not _run_owns_resource(
            session, production_drill_run_id, "release_artifact", binding.id
        ):
            continue
        already_verified = session.scalar(
            select(DeploymentObservation.id)
            .where(DeploymentObservation.release_artifact_binding_id == binding.id)
            .limit(1)
        )
        if already_verified is not None:
            continue
        counters += _record_split_brain(
            session,
            actor,
            work_unit_id=binding.work_unit_id,
            key_facts={"release_artifact_binding_id": str(binding.id)},
            stored_state={
                "release_artifact_binding_id": str(binding.id),
                "post_deploy_work_unit": None,
            },
            observed_state={
                "deploy_status": observation.facts.get("deploy_status"),
                "artifact_digest": observation.facts.get("artifact_digest"),
                "environment": observation.environment,
            },
            detail="a deploy was reported for a release binding with no post-deploy verification",
            observation_id=observation.id,
        )
    return counters


def _record_split_brain(
    session: Session,
    actor: ActorContext,
    *,
    work_unit_id: uuid.UUID,
    key_facts: dict[str, Any],
    stored_state: dict[str, Any],
    observed_state: dict[str, Any],
    detail: str,
    observation_id: uuid.UUID | None = None,
    deployment_observation_id: uuid.UUID | None = None,
) -> DetectionCounters:
    outcome = record_reconciliation_condition(
        session,
        ConditionCommand(
            actor=actor,
            work_unit_id=work_unit_id,
            observation_kind="deployment",
            condition_type=DEPLOY_SPLIT_BRAIN,
            key_facts=key_facts,
            stored_state=stored_state,
            observed_state=observed_state,
            detail=detail,
            observation_id=observation_id,
            deployment_observation_id=deployment_observation_id,
        ),
    )
    if not isinstance(outcome, ConditionOutcome):
        return SKIPPED
    if outcome.suppressed:
        return DetectionCounters(suppressed_duplicates=1)
    return DetectionCounters(conditions_recorded=1)


def _correlated_binding(
    session: Session, observation: Observation
) -> ReleaseArtifactBinding | None:
    try:
        binding_id = uuid.UUID(observation.subject_reference)
    except ValueError:
        return None
    return session.get(ReleaseArtifactBinding, binding_id)


def _run_owns_resource(
    session: Session,
    run_id: uuid.UUID,
    resource_type: str,
    resource_id: uuid.UUID,
) -> bool:
    return (
        session.scalar(
            select(ProductionDrillResource.id).where(
                ProductionDrillResource.run_id == run_id,
                ProductionDrillResource.resource_type == resource_type,
                ProductionDrillResource.resource_id == resource_id,
            )
        )
        is not None
    )
