import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.kernel.authority import normalize_authority
from orchestrator.persistence.models import (
    DispatchRecord,
    Evidence,
    PackageAcceptanceCriterion,
    UnitPrBinding,
    WorkUnit,
)
from orchestrator.services.verifier_evaluators import EvaluationStatus

BindingFailure = tuple[EvaluationStatus, str, str]


def validate_named_check_bindings(
    session: Session,
    unit: WorkUnit,
    criterion: PackageAcceptanceCriterion,
    evidence: Evidence,
) -> BindingFailure | None:
    payload = evidence.payload
    locked_unit, binding = lock_named_check_subject(session, unit.id)
    if locked_unit is not None:
        unit = locked_unit
    repository = normalize_authority(unit.authority).constraints.get("target_repository")
    dispatch_id = _uuid(payload.get("dispatch_id")) if isinstance(payload, dict) else None
    dispatch = (
        session.get(DispatchRecord, dispatch_id, populate_existing=True)
        if dispatch_id is not None
        else None
    )
    if (
        not isinstance(payload, dict)
        or locked_unit is None
        or not isinstance(repository, str)
        or not repository.strip()
        or dispatch is None
        or binding is None
        or evidence.work_package_revision_id != unit.work_package_revision_id
        or evidence.work_unit_id != unit.id
        or evidence.ac_id != criterion.ac_id
        or evidence.attempt != unit.attempt_count
        or dispatch.work_unit_id != unit.id
        or dispatch.work_package_revision_id != unit.work_package_revision_id
        or dispatch.runner_attempt != unit.attempt_count
        or dispatch.status != "dispatched"
        or dispatch.target_repository != repository
        or payload.get("repository") != repository
        or binding.pr_number != payload.get("pr_number")
        or payload.get("pr_url") != f"https://github.com/{repository}/pull/{binding.pr_number}"
        or binding.head_sha != payload.get("head_sha")
        or binding.verification_read_head_sha != payload.get("head_sha")
        or binding.verification_read_attempt != unit.attempt_count
    ):
        return (
            "failed_closed",
            "failed",
            "named-check evidence no longer matches canonical bindings",
        )
    return None


def lock_named_check_subject(
    session: Session,
    unit_id: uuid.UUID,
) -> tuple[WorkUnit | None, UnitPrBinding | None]:
    unit = session.scalar(
        select(WorkUnit)
        .where(WorkUnit.id == unit_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    binding = session.scalar(
        select(UnitPrBinding)
        .where(UnitPrBinding.work_unit_id == unit_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return unit, binding


def _uuid(value: object) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
