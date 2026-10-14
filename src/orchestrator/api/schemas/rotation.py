from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PreviousHashResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    token_hash: str
    until: datetime


class M2MIdentityResponse(BaseModel):
    """One entry of the credentials setting, in the setting's own shape.

    The route omits `previous` when there is none: the setting's parser refuses a null
    `previous`, and the executor writes these entries back as the setting.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    agent_id: str
    token_hash: str
    previous: PreviousHashResponse | None = None
