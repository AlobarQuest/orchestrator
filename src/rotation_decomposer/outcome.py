"""What a pass reports about one carried rotation revision."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    subject: str
    # approved, proposed, would-propose, awaiting-approval, superseded, declined, refused or
    # unread
    result: str
    detail: str = ""
