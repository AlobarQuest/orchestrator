"""The advisory locks the Dependabot lanes' acts take, held from a test's own connection.

Spelled out as LITERAL keys rather than through `lane_act._lock_repository`, so a test that holds
one pins the key the act must take -- a helper shared with the code under test would move with it,
and a lane that quietly took a key of its own would then be invisible. Both lanes take the same key
for the same act: landing and bringing a branch up to date each serialise per repository whichever
lane is asking, because the two populations cannot overlap and one table sits behind each act.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def hold_landing_lock(session: Session, repository: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"estate_pr_merge:{repository}"},
    )


def hold_branch_update_lock(session: Session, repository: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"estate_pr_branch_update:{repository}"},
    )


def hold_held_close_lock(session: Session, repository: str) -> None:
    """Bridge layer Part 4: closing a held pull request, one per repository whichever lane asks."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"held_pr_close:{repository}"},
    )
