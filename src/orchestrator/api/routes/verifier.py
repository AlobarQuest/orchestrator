from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from orchestrator.api.dependencies import ActorDep, SessionDep, SettingsDep
from orchestrator.api.routes.common import ERROR_RESPONSES, github_app_credentials_for, raise_error
from orchestrator.api.schemas.verifier import (
    AdjudicationCommand,
    AdjudicationResponse,
    EvidenceCommand,
    EvidenceResponse,
    RecoverEvidenceCommand,
    VerifierNamedCheckEvidenceCommandModel,
    VerifyCommandModel,
    VerifyResponse,
)
from orchestrator.services.github_app import token_provider_for
from orchestrator.services.verifier.evidence import (
    append_evidence,
    list_evidence,
    record_adjudication,
    recover_evidence,
    supersede_evidence,
)
from orchestrator.services.verifier.github_checks import CheckObserver, GitHubActionsCheckObserver
from orchestrator.services.verifier.verifier import VerifyCommand, verify_work_unit
from orchestrator.services.verifier.verifier_evidence import (
    NamedCheckEvidenceCommand,
    record_named_check_evidence,
)

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


def get_check_observer(settings: SettingsDep) -> CheckObserver:
    """Build the thing that asks GitHub how a named check concluded.

    A dependency rather than a call inside the route body so a test can substitute GitHub — the
    same reason the workflow trigger is passed in rather than constructed where it is used. The
    same App installation serves both, resolved through the one definition of "App credentials
    are configured".
    """
    return GitHubActionsCheckObserver(token_provider_for(github_app_credentials_for(settings)))


CheckObserverDep = Annotated[CheckObserver, Depends(get_check_observer)]


@router.post(
    "/work-units/{unit_id}/attempts/{attempt}/recover-evidence",
    response_model=EvidenceResponse,
    status_code=201,
)
def recover_evidence_route(
    unit_id: UUID,
    attempt: int,
    body: RecoverEvidenceCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """AC-004. SYSTEM/operator only -- never the expired worker."""
    return raise_error(
        recover_evidence(
            session,
            work_unit_id=unit_id,
            attempt=attempt,
            actor=actor,
            **body.model_dump(),
        )
    )


@router.post("/work-units/{unit_id}/verify", response_model=VerifyResponse)
def verify(
    unit_id: UUID,
    body: VerifyCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return verify_work_unit(
        session,
        VerifyCommand(
            unit_id=unit_id,
            actor=actor,
            expected_version=body.expected_version,
            idempotency_key=body.idempotency_key,
        ),
    )


@router.post(
    "/work-units/{unit_id}/verifier-evidence/named-check",
    response_model=EvidenceResponse,
)
def record_verifier_named_check_evidence(
    unit_id: UUID,
    body: VerifierNamedCheckEvidenceCommandModel,
    actor: ActorDep,
    session: SessionDep,
    observer: CheckObserverDep,
) -> object:
    return raise_error(
        record_named_check_evidence(
            session,
            NamedCheckEvidenceCommand(
                unit_id=unit_id,
                work_package_revision_id=body.work_package_revision_id,
                ac_id=body.ac_id,
                dispatch_id=body.dispatch_id,
                repository=body.repository,
                pr_number=body.pr_number,
                pr_url=body.pr_url,
                head_sha=body.head_sha,
                check_name=body.check_name,
                expected_conclusion=body.expected_conclusion,
                actor=actor,
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key,
            ),
            observer,
        )
    )


@router.post("/work-units/{unit_id}/adjudications", response_model=AdjudicationResponse)
def adjudication(
    unit_id: UUID,
    body: AdjudicationCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return raise_error(
        record_adjudication(
            session,
            work_unit_id=unit_id,
            actor=actor,
            **body.model_dump(),
        )
    )


@router.post("/work-units/{unit_id}/evidence", response_model=EvidenceResponse)
def evidence(
    unit_id: UUID,
    body: EvidenceCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    fields = body.model_dump()
    store = supersede_evidence if fields.pop("supersede") else append_evidence
    return raise_error(
        store(
            session,
            work_unit_id=unit_id,
            actor=actor,
            **fields,
        )
    )


@router.get("/work-units/{unit_id}/evidence", response_model=list[EvidenceResponse])
def evidence_list(
    unit_id: UUID,
    _actor: ActorDep,
    session: SessionDep,
) -> object:
    return list_evidence(session, unit_id)
