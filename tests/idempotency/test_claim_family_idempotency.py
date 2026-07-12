"""AC-007: the claim family, and the one assertion you must NOT make.

A claim/renew replay deliberately returns an EMPTY lease token: it does not re-issue the
credential. Asserting byte-identical responses here would be asserting a credential-reissue bug.
"Identical response" for this family means identical claim_id / attempt / expiry, with the token
WITHHELD on replay.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Claim, Event
from orchestrator.services.claims import LeaseGrant, claim_unit, renew_claim
from tests.services.test_claims import worker


def test_a_duplicate_claim_replays_and_withholds_the_lease_token(
    migrated_session: Session, ready_unit
) -> None:
    first = claim_unit(migrated_session, ready_unit.id, worker(), "idem-claim")
    replay = claim_unit(migrated_session, ready_unit.id, worker(), "idem-claim")

    assert isinstance(first, LeaseGrant)
    assert isinstance(replay, LeaseGrant)
    assert (first.claim_id, first.attempt, first.expires_at) == (
        replay.claim_id,
        replay.attempt,
        replay.expires_at,
    )
    assert first.lease_token != ""
    assert replay.lease_token == ""  # the credential is NOT re-issued

    claims = migrated_session.scalar(
        select(func.count()).select_from(Claim).where(Claim.work_unit_id == ready_unit.id)
    )
    assert claims == 1


def test_a_duplicate_renew_replays_and_withholds_the_lease_token(
    migrated_session: Session, ready_unit
) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "idem-renew-claim")
    assert isinstance(grant, LeaseGrant)

    first = renew_claim(
        migrated_session,
        ready_unit.id,
        worker(),
        grant.attempt,
        grant.lease_token,
        idempotency_key="idem-renew",
    )
    replay = renew_claim(
        migrated_session,
        ready_unit.id,
        worker(),
        grant.attempt,
        grant.lease_token,
        idempotency_key="idem-renew",
    )

    assert isinstance(first, LeaseGrant)
    assert isinstance(replay, LeaseGrant)
    assert first.expires_at == replay.expires_at  # the lease was NOT extended twice
    assert replay.lease_token == ""

    events = migrated_session.scalar(
        select(func.count()).select_from(Event).where(Event.idempotency_key == "idem-renew")
    )
    assert events == 1
