from collections.abc import Iterator
from typing import Annotated

from fastapi import Header
from sqlalchemy.orm import Session

from orchestrator.db import session_factory
from orchestrator.kernel.states import ActorRole
from orchestrator.services.lifecycle import ActorContext


def get_session() -> Iterator[Session]:
    with session_factory() as session:
        yield session


def get_actor(
    x_actor_id: Annotated[str, Header(min_length=1)],
    x_actor_role: Annotated[ActorRole, Header()],
) -> ActorContext:
    """Fixture/config authentication boundary; production auth is intentionally external."""
    return ActorContext(x_actor_id, x_actor_role)
