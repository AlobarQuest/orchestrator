"""The reconciliation runner's read surface (AC-009).

Read-only: no write, no transition, no commit. It is the runner's entire view of reality.

It exposes release bindings and recently-completed units carrying them, NOT just in-flight units,
and that is load-bearing rather than convenience. A unit that carries a `ReleaseArtifactBinding`
is `COMPLETED` -- the deployment-ingest path requires it -- and therefore not "in flight". Without
these, the runner would be structurally blind to the deploy-nobody-reported case: a binding with
no `DeploymentObservation` and thus no post-deploy verification unit at all. `has_post_deploy_unit
= False` in this payload IS that signal, and it is the case ADR-0002 rejects Alternative A over.

READ THAT FIELD LITERALLY, because since ADR-0030's activation check it no longer implies the
absence of an observation. A machine-local activation is observed and mints no verification unit,
so a binding can carry an observation and still report `has_post_deploy_unit = False`. The signal
the detector actually keys on is the ABSENCE OF AN OBSERVATION, which it reads for itself
(`reconciliation_detection._detect_unreported_deploys`); nothing consumes this field to act, and
`tests/services/test_in_flight.py` pins both halves so a future consumer cannot inherit the older
reading by accident.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import (
    DeploymentObservation,
    ReleaseArtifactBinding,
    UnitPrBinding,
    WorkUnit,
)

IN_FLIGHT_STATES = (
    WorkUnitState.READY,
    WorkUnitState.CLAIMED,
    WorkUnitState.EXECUTING,
    WorkUnitState.BLOCKED,
    WorkUnitState.AWAITING_APPROVAL,
    WorkUnitState.SUBMITTED,
    WorkUnitState.VERIFYING,
    WorkUnitState.AWAITING_REVIEW,
    WorkUnitState.REVISION_REQUIRED,
)
COMPLETED_BINDING_WINDOW_HOURS = 168


@dataclass(frozen=True)
class InFlightUnitView:
    work_unit_id: UUID
    unit_key: str
    state: str
    version: int
    attempt_count: int
    work_package_revision_id: UUID
    pr_number: int | None
    head_sha: str | None
    verification_read_head_sha: str | None


@dataclass(frozen=True)
class ReleaseBindingView:
    binding_id: UUID
    work_unit_id: UUID
    work_unit_state: str
    source_repository: str
    artifact_digest: str
    has_post_deploy_unit: bool
    post_deploy_unit_state: str | None
    post_deploy_unit_created_at: datetime | None


@dataclass(frozen=True)
class InFlightSnapshot:
    units: tuple[InFlightUnitView, ...]
    release_bindings: tuple[ReleaseBindingView, ...]


def in_flight_snapshot(
    session: Session,
    *,
    completed_binding_window_hours: int = COMPLETED_BINDING_WINDOW_HOURS,
) -> InFlightSnapshot:
    return InFlightSnapshot(
        units=_in_flight_units(session),
        release_bindings=_release_bindings(session, completed_binding_window_hours),
    )


def _in_flight_units(session: Session) -> tuple[InFlightUnitView, ...]:
    rows = session.execute(
        select(WorkUnit, UnitPrBinding)
        .outerjoin(UnitPrBinding, UnitPrBinding.work_unit_id == WorkUnit.id)
        .where(WorkUnit.state.in_([state.value for state in IN_FLIGHT_STATES]))
        .order_by(WorkUnit.created_at, WorkUnit.id)
    ).all()
    return tuple(
        InFlightUnitView(
            work_unit_id=unit.id,
            unit_key=unit.unit_key,
            state=unit.state,
            version=unit.version,
            attempt_count=unit.attempt_count,
            work_package_revision_id=unit.work_package_revision_id,
            pr_number=binding.pr_number if binding is not None else None,
            head_sha=binding.head_sha if binding is not None else None,
            verification_read_head_sha=(
                binding.verification_read_head_sha if binding is not None else None
            ),
        )
        for unit, binding in rows
    )


def _release_bindings(
    session: Session, completed_binding_window_hours: int
) -> tuple[ReleaseBindingView, ...]:
    cutoff = datetime.now(UTC) - timedelta(hours=completed_binding_window_hours)
    rows = session.execute(
        select(ReleaseArtifactBinding, WorkUnit)
        .join(WorkUnit, WorkUnit.id == ReleaseArtifactBinding.work_unit_id)
        .where(
            WorkUnit.state.in_([state.value for state in IN_FLIGHT_STATES])
            | (ReleaseArtifactBinding.recorded_at >= cutoff)
        )
        .order_by(ReleaseArtifactBinding.recorded_at, ReleaseArtifactBinding.id)
    ).all()

    views: list[ReleaseBindingView] = []
    for binding, unit in rows:
        observation = session.scalar(
            select(DeploymentObservation)
            .where(DeploymentObservation.release_artifact_binding_id == binding.id)
            .limit(1)
        )
        # BOTH conditions. A machine-local observation carries no post-deploy verification unit
        # at all -- there is nothing on a working copy for a verifier to probe -- so this id is
        # legitimately NULL. Measured rather than assumed (SQLAlchemy 2.0.52): `Session.get`
        # with a None primary key does NOT raise; it returns None after emitting
        # `SAWarning: fully NULL primary key identity cannot load any object. This condition may
        # raise an error in a future release.` So the second clause buys a clean read today and
        # a working one when that release arrives, and the warning is what a control can see.
        post_deploy = (
            session.get(WorkUnit, observation.post_deploy_work_unit_id)
            if observation is not None and observation.post_deploy_work_unit_id is not None
            else None
        )
        views.append(
            ReleaseBindingView(
                binding_id=binding.id,
                work_unit_id=unit.id,
                work_unit_state=unit.state,
                source_repository=binding.source_repository,
                artifact_digest=binding.artifact_digest,
                has_post_deploy_unit=post_deploy is not None,
                post_deploy_unit_state=post_deploy.state if post_deploy is not None else None,
                post_deploy_unit_created_at=(
                    post_deploy.created_at if post_deploy is not None else None
                ),
            )
        )
    return tuple(views)
