from typing import Annotated

from fastapi import APIRouter, Header

from orchestrator.api.dependencies import ActorDep, SessionDep
from orchestrator.api.routes.common import ERROR_RESPONSES
from orchestrator.api.schemas.membership import (
    MemberProposalBody,
    MemberProposalResponse,
    MembersResponse,
    MemberView,
)
from orchestrator.services.membership.members import list_members, member_record
from orchestrator.services.membership.proposals import stage_member_proposal

router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


@router.post("/member-proposals", response_model=MemberProposalResponse, status_code=201)
def create_member_proposal(
    body: MemberProposalBody,
    idempotency_key: Annotated[str, Header(min_length=1)],
    actor: ActorDep,
    session: SessionDep,
) -> dict[str, object]:
    """Stage a membership proposal for a person to confirm at `/review/member-proposals/{id}`.

    SYSTEM only (`stage_member_proposal` refuses every other role). Staging changes no member.
    """
    proposal = stage_member_proposal(
        session, actor=actor, body=body, idempotency_key=idempotency_key
    )
    session.commit()
    return {
        "id": proposal.id,
        "state": proposal.state,
        "review_path": f"/review/member-proposals/{proposal.id}",
        "staged_by": proposal.staged_by,
        "staged_at": proposal.staged_at,
    }


@router.get("/members", response_model=MembersResponse)
def read_members(actor: ActorDep, session: SessionDep) -> MembersResponse:
    """Every member, retired included, with a grant for each of the seven services.

    Any authenticated role may read, OBSERVER included; the write surface is the proposal routes.
    """
    del actor
    return MembersResponse(
        members=[MemberView.model_validate(member_record(m)) for m in list_members(session)]
    )
