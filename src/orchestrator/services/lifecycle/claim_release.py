from datetime import datetime

from orchestrator.errors import DomainError
from orchestrator.persistence.models import Claim


def release_claim(claim: Claim, *, terminal_reason: str, released_at: datetime) -> None:
    """Write a claim's terminal disposition using the caller's transaction timestamp."""
    if not terminal_reason:
        raise DomainError(
            "terminal_reason_required",
            "releasing a claim requires an attributable terminal reason",
            None,
        )
    if claim.released_at is not None:
        raise DomainError("claim_already_released", "claim has already been released", None)
    claim.released_at = released_at
    claim.terminal_reason = terminal_reason
