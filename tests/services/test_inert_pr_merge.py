"""The act: landing a pull request that has no work unit and no change record. ADR-0038 part 2.

Everything runs with no network. The assertions that matter most are about what did NOT happen --
a second call, a record written for something retryable, a landing on a head nobody evaluated.

Persistence is asserted through a SECOND SESSION, never by re-reading the one that wrote: a
flushed-but-uncommitted row is visible to its own transaction, so an in-session re-read passes
under the exact defect it would be written to catch.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import EstatePrMerge, Event
from orchestrator.services.estate_landing import EstateAnswer
from orchestrator.services.estate_landing_admission import EstateGatewayError
from orchestrator.services.estate_pr_merge import MergeOutcome
from orchestrator.services.inert_landing_policy import InertLandingAnswer
from orchestrator.services.inert_pr_merge import (
    INERT_LANDING_POLICY_TRAILER,
    INERT_MERGE_HEAD_MOVED,
    INERT_MERGE_NOT_ADMISSIBLE,
    INERT_MERGE_REFUSED_BY_REMOTE,
    InertMergeCommand,
    land_inert_pull_request,
)
from orchestrator.services.lifecycle import ActorContext
from tests.services.estate_doubles import LANDING_REDEPLOYS, FakeEstateLandingSource
from tests.services.estate_landing_doubles import HEAD, pull_request
from tests.services.inert_landing_doubles import (
    INERT_POLICY_VERSION,
    INERT_REPOSITORY,
    LANDED_COMMIT,
    ActingInertGateway,
    FakeInertPolicySource,
    rules,
)

SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
WORKER = ActorContext("claude-code-runner", ActorRole.WORKER)
HUMAN = ActorContext("devon", ActorRole.HUMAN)
OBSERVER = ActorContext("orchestrator-observer", ActorRole.OBSERVER)

PR = 3
UV_BRANCH = "dependabot/uv/typer-0.21.0"


def _command(*, key: str = "inert-1", head: str = HEAD, actor: ActorContext = SYSTEM):
    return InertMergeCommand(
        repository=INERT_REPOSITORY,
        pr_number=PR,
        actor=actor,
        idempotency_key=key,
        expected_head_sha=head,
    )


def _land(
    session: Session,
    *,
    gateway: ActingInertGateway | None = None,
    landing_source: FakeEstateLandingSource | None = None,
    policy_source: FakeInertPolicySource | None = None,
    command: InertMergeCommand | None = None,
    enabled: bool = True,
    credentials_configured: bool = True,
):
    return land_inert_pull_request(
        session,
        command or _command(),
        gateway or ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH)),
        landing_source or FakeEstateLandingSource(),
        policy_source or FakeInertPolicySource(),
        enabled=enabled,
        credentials_configured=credentials_configured,
    )


def _rows(engine: Engine) -> list[EstatePrMerge]:
    with Session(engine) as reader:
        return list(reader.scalars(select(EstatePrMerge)))


def test_an_admissible_pull_request_is_landed_and_the_row_is_committed(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    gateway = ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    record = _land(migrated_session, gateway=gateway)

    assert record.status == "merged"
    assert record.merge_commit_sha == LANDED_COMMIT
    assert len(gateway.merges) == 1

    # THROUGH A DIFFERENT SESSION, which is the only reader that cannot see an uncommitted write.
    rows = _rows(migrated_engine)
    assert len(rows) == 1
    assert rows[0].repository == INERT_REPOSITORY
    assert rows[0].pr_number == PR
    assert rows[0].head_sha == HEAD
    assert rows[0].policy_version == INERT_POLICY_VERSION
    # NO CHANGE RECORD, and there cannot be one: that is what a row from this lane looks like.
    assert rows[0].change_record_id is None


def test_the_squash_body_carries_the_policy_version_under_this_lanes_own_trailer(
    migrated_session: Session,
) -> None:
    """**The trailer is the contract the estate's ledger reads back**, and its NAME is what
    identifies the lane -- there is no change record here, so a bare version number would be
    indistinguishable from the deploying lane's second trailer."""
    gateway = ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    _land(migrated_session, gateway=gateway)

    body = gateway.merges[0][3]
    assert body == f"{INERT_LANDING_POLICY_TRAILER}: {INERT_POLICY_VERSION}"


def test_the_trailer_carries_nothing_dated_and_nothing_that_can_move() -> None:
    """The ledger freezes every string a landing carries at the first observation of it, so a body
    naming a count or a moment would make a later pass over an unchanged landing conflict with
    itself. The literal is asserted here rather than derived, because the reader on the other side
    pins the same spelling and a disagreement is a landing recorded with no basis."""
    assert INERT_LANDING_POLICY_TRAILER == "SDS-Inert-Landing-Policy"


def test_the_event_names_this_lane_rather_than_the_one_it_shares_a_table_with(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """One table, two lanes, and the ACTION is the honest discriminator in the event stream --
    `change_record_id IS NULL` is a derived one that nothing reads."""
    record = _land(migrated_session)

    with Session(migrated_engine) as reader:
        event = reader.scalar(select(Event).where(Event.id == record.event_id))
    assert event is not None
    assert event.action == "inert_pr_merge.merged"
    assert event.payload["policy_version"] == INERT_POLICY_VERSION
    assert "change_record_id" not in event.payload


@pytest.mark.parametrize("actor", [WORKER, HUMAN, OBSERVER])
def test_only_the_system_actor_may_land(migrated_session: Session, actor: ActorContext) -> None:
    """Not the worker, because a runner asking for its own work to be landed is the runner
    attesting to its own compliance; and not a human, because a person can land it themselves."""
    gateway = ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    with pytest.raises(DomainError) as caught:
        _land(migrated_session, gateway=gateway, command=_command(actor=actor))

    assert caught.value.code == "role_forbidden"
    assert gateway.merges == []


def test_an_inadmissible_pull_request_is_refused_before_the_remote_is_touched(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """An admission answer that arrives after the act is not a gate."""
    gateway = ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    with pytest.raises(DomainError) as caught:
        _land(
            migrated_session,
            gateway=gateway,
            landing_source=FakeEstateLandingSource(default=EstateAnswer(LANDING_REDEPLOYS)),
        )

    assert caught.value.code == INERT_MERGE_NOT_ADMISSIBLE
    assert "inert_landing_target_not_inert" in caught.value.message
    assert gateway.merges == []
    # NO ROW: consuming this pull request's one row would refuse every later legitimate attempt.
    assert _rows(migrated_engine) == []


def test_a_deployment_that_is_not_enabled_lands_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    gateway = ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    with pytest.raises(DomainError) as caught:
        _land(migrated_session, gateway=gateway, enabled=False)

    assert caught.value.code == INERT_MERGE_NOT_ADMISSIBLE
    assert gateway.merges == []
    assert _rows(migrated_engine) == []


def test_a_head_that_moved_since_the_caller_read_is_refused_with_no_record(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    gateway = ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    with pytest.raises(DomainError) as caught:
        _land(migrated_session, gateway=gateway, command=_command(head="e" * 40))

    assert caught.value.code == INERT_MERGE_HEAD_MOVED
    assert gateway.merges == []
    assert _rows(migrated_engine) == []


def test_a_repeat_replays_the_record_and_never_calls_the_remote_again(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """The idempotency claim in the coverage matrix. A landing is not idempotent and its failure is
    asymmetric, so the question "did we already do this?" is answered from our own record BEFORE
    the call -- and a SECOND KEY replays the same record rather than acting again, which is why
    the key is not what makes this safe."""
    first_gateway = ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))
    first = _land(migrated_session, gateway=first_gateway)

    second_gateway = ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))
    second = _land(migrated_session, gateway=second_gateway, command=_command(key="inert-2"))

    assert second.id == first.id
    assert second_gateway.merges == []
    assert len(_rows(migrated_engine)) == 1


def test_a_key_already_spent_on_a_different_pull_request_is_refused_before_the_remote(
    migrated_session: Session,
) -> None:
    """An operator who copies one request and changes only the number would otherwise reach the
    remote, LAND THE PULL REQUEST, and lose the whole transaction to an integrity error with no
    registered handler -- a bare 500 that reads as "nothing happened" over a landing that did."""
    _land(migrated_session)

    other = InertMergeCommand(
        repository=INERT_REPOSITORY,
        pr_number=PR + 1,
        actor=SYSTEM,
        idempotency_key="inert-1",
        expected_head_sha=HEAD,
    )
    gateway = ActingInertGateway(pull=pull_request(number=PR + 1, head_ref=UV_BRANCH))

    with pytest.raises(DomainError) as caught:
        _land(migrated_session, gateway=gateway, command=other)

    assert caught.value.code == "idempotency_conflict"
    assert gateway.merges == []


def test_a_credential_that_could_not_be_minted_records_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """NOTHING WAS SENT, so nothing can have landed. The reconciling read would fail the same way
    under the same outage and answer "we do not know", which would write a permanent `refused` row
    -- silently barring an admissible pull request forever on a transient credential failure."""
    gateway = ActingInertGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH),
        merge_error=EstateGatewayError("app_token_mint:private_key_invalid"),
    )

    with pytest.raises(DomainError) as caught:
        _land(migrated_session, gateway=gateway)

    assert caught.value.code == INERT_MERGE_REFUSED_BY_REMOTE
    assert _rows(migrated_engine) == []


def test_a_confirmed_refusal_is_retryable_and_records_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """A required check that is red today can be green tomorrow."""
    gateway = ActingInertGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH),
        merge_error=EstateGatewayError("merge_status", 405),
        landed_after_refusal=False,
    )

    with pytest.raises(DomainError) as caught:
        _land(migrated_session, gateway=gateway)

    assert caught.value.code == INERT_MERGE_REFUSED_BY_REMOTE
    assert _rows(migrated_engine) == []


def test_a_refusal_the_reconciling_read_reveals_as_a_landing_is_recorded(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """A lost success answers the same way a refusal does, so the pull request is re-read."""
    gateway = ActingInertGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH),
        merge_error=EstateGatewayError("merge_status", 502),
        landed_after_refusal=True,
    )

    record = _land(migrated_session, gateway=gateway)

    assert record.status == "already_merged"
    assert record.github_status == 502
    assert len(_rows(migrated_engine)) == 1


def test_a_refusal_whose_reconciling_read_also_fails_is_recorded_conservatively(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """A landing cannot be ruled out. Terminal and conservative: a retry would meet the same
    refusal no better informed, and the ledger observes the landing independently."""
    gateway = ActingInertGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH),
        merge_error=EstateGatewayError("merge_status", 502),
        reconcile_error=EstateGatewayError("read_status", 502),
    )

    record = _land(migrated_session, gateway=gateway)

    assert record.status == "refused"
    assert record.reason_code == "merge_refused_by_remote:merge_status"
    assert len(_rows(migrated_engine)) == 1


def test_the_remote_is_asked_to_land_the_head_the_terms_were_evaluated_against(
    migrated_session: Session,
) -> None:
    """`sha` is the load-bearing parameter: the remote refuses when the pull request has moved,
    which closes the window between deciding and doing without this side observing the move."""
    gateway = ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    _land(migrated_session, gateway=gateway)

    repository, number, head_sha, _ = gateway.merges[0]
    assert (repository, number, head_sha) == (INERT_REPOSITORY, PR, HEAD)


def test_a_landing_that_the_remote_declines_outright_is_recorded_as_refused(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    gateway = ActingInertGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH),
        outcome=MergeOutcome(landed=False, commit_sha=None, status_code=200),
    )

    record = _land(migrated_session, gateway=gateway)

    assert record.status == "refused"
    assert record.reason_code == "merge_refused_by_remote"
    assert len(_rows(migrated_engine)) == 1


def test_a_policy_that_could_not_be_read_lands_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """The permission is the policy version, so a version that could not be read is a landing with
    no basis to write into the artifact -- refused here rather than discovered by the ledger."""
    gateway = ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    with pytest.raises(DomainError) as caught:
        _land(
            migrated_session,
            gateway=gateway,
            policy_source=FakeInertPolicySource(InertLandingAnswer(None, "source_unreadable")),
        )

    assert caught.value.code == INERT_MERGE_NOT_ADMISSIBLE
    assert gateway.merges == []
    assert _rows(migrated_engine) == []


def test_a_repository_the_policy_does_not_declare_lands_nothing(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    gateway = ActingInertGateway(pull=pull_request(number=PR, head_ref=UV_BRANCH))

    with pytest.raises(DomainError) as caught:
        _land(
            migrated_session,
            gateway=gateway,
            policy_source=FakeInertPolicySource(
                InertLandingAnswer(rules(repositories=frozenset({"alobarquest/elsewhere"})))
            ),
        )

    assert caught.value.code == INERT_MERGE_NOT_ADMISSIBLE
    assert "inert_landing_repository_not_declared" in caught.value.message
    assert gateway.merges == []
    assert _rows(migrated_engine) == []
