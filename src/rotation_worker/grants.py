"""Whether the SDS member record grants `credential_rotation` on a repository.

A grant counts only in state `granted` on a member that isn't retired, and repositories compare
case-folded, as `orchestrator.services.execution.target_grant` does. This reads a body the caller
already fetched, so any program holding the record can ask, and it only ever narrows: it answers
for a repository the registry names and can't add one.

**UNREADABLE IS NOT "NOT GRANTED".** Every row is read before any answer, and a body or a row of
any other shape raises `GrantUnread`, so a partial record is never taken for a shorter one. A
record with no rows is unread too: it can't be an explicit withdrawal. A repository absent from a
record that reads is `False`.

This imports no client, so a program with its own client can use it. Every caller must handle
`GrantUnread`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

SERVICE: Final = "credential_rotation"


class GrantUnread(RuntimeError):
    """The body isn't a member record with rows in it, so it says nothing about any grant."""


def rotation_granted(body: Mapping[str, Any], repository: str) -> bool:
    try:
        rows = body["members"]
        if not isinstance(rows, list):
            raise TypeError("members")
        if not rows:
            raise GrantUnread("the member record has no rows")
        wanted = repository.lower()
        return any([_live_grant_on(row) == wanted for row in rows])
    except (KeyError, TypeError) as error:
        raise GrantUnread(f"the answer wasn't the member record: {error!r}") from None


def _live_grant_on(row: Any) -> str | None:
    """The folded repository a row grants the service on, `None` if it grants nothing."""
    repository, state, grant = row["repository"], row["state"], row["grants"][SERVICE]["state"]
    if not all(isinstance(text, str) for text in (repository, state, grant)):
        raise TypeError("member")
    return repository.lower() if state != "retired" and grant == "granted" else None
