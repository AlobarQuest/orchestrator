from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PreviousHashResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_hash: str
    until: datetime


class M2MIdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    token_hash: str
    previous: PreviousHashResponse | None = None
