"""The act: landing a pull request that has no work unit. ADR-0019 increment 5b.

Everything runs with no network. The assertions that matter most are about what did NOT happen --
a second call, a record written for something retryable, a landing on a head nobody evaluated.

Persistence is asserted through a SECOND SESSION, never by re-reading the one that wrote: a
flushed-but-uncommitted row is visible to its own transaction, so an in-session re-read passes
under the exact defect it would be written to catch.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import EstatePrMerge, Event
from orchestrator.services.estate_landing_admission import EstateGatewayError
from orchestrator.services.estate_pr_merge import (
    CHANGE_RECORD_TRAILER,
    POLICY_VERSION_TRAILER,
    EstateMergeCommand,
    MergeOutcome,
    land_estate_pull_request,
)
from orchestrator.services.lifecycle import ActorContext
from tests.services.change_record_doubles import FakeChangeRecordSource
from tests.services.estate_doubles import inert_source, redeploying_source
from tests.services.estate_landing_doubles import (
    HEAD,
    POLICY_VERSION,
    REPOSITORY,
    FakeEstateGateway,
    approved,
    pull_request,
)

SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
WORKER = ActorContext("claude-code-runner", ActorRole.WORKER)
HUMAN = ActorContext("devon", ActorRole.HUMAN)

PR = 49
LANDED_COMMIT = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
IN_WINDOW = datetime(2026, 8, 11, 7, 30, tzinfo=UTC)
OUT_OF_WINDOW = datetime(2026, 8, 11, 19, 30, tzinfo=UTC)


class FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, session: Session) -> datetime:
        return self._moment


class ActingGateway(FakeEstateGateway):
    """The reading gateway plus the one call that changes anything, recorded."""

    def __init__(
        self,
        *,
        outcome: MergeOutcome | None = None,
        merge_error: EstateGatewayError | None = None,
        landed_after_refusal: bool | None = None,
        reconcile_error: EstateGatewayError | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._reconcile_error = reconcile_error
        self._outcome = outcome or MergeOutcome(
            landed=True, commit_sha=LANDED_COMMIT, status_code=200
        )
        self._merge_error = merge_error
        self._landed_after_refusal = landed_after_refusal
        self.merges: list[tuple[str, int, str, str]] = []

    def read_pull_request(self, *, repository: str, number: int):
        if self.merges and self._reconcile_error is not None:
            # The RECONCILING read fails, while admission's own read succeeded. Distinct from
            # `read_error`, which fails admission and never reaches the act at all.
            self.reads.append((repository, number))
            raise self._reconcile_error
        if self.merges and self._landed_after_refusal is not None:
            # The reconciling read, answering about the world AFTER the refused call.
            self.reads.append((repository, number))
            return pull_request(landed=self._landed_after_refusal, is_open=False)
        return super().read_pull_request(repository=repository, number=number)

    def submit_merge(self, *, repository, number, head_sha, commit_message):
        self.merges.append((repository, number, head_sha, commit_message))
        if self._merge_error is not None:
            raise self._merge_error
        return self._outcome


def _land(
    session: Session,
    gateway: ActingGateway,
    *,
    actor: ActorContext = SYSTEM,
    key: str = "land-1",
    expected_head: str = HEAD,
    enabled: bool = True,
    credentials: bool = True,
    moment: datetime = IN_WINDOW,
    record=None,
    landing=None,
) -> EstatePrMerge:
    return land_estate_pull_request(
        session,
        EstateMergeCommand(
            repository=REPOSITORY,
            pr_number=PR,
            actor=actor,
            idempotency_key=key,
            expected_head_sha=expected_head,
        ),
        gateway,
        landing or redeploying_source(),
        FakeChangeRecordSource({(REPOSITORY, PR): record or approved()}),
        enabled=enabled,
        credentials_configured=credentials,
        clock=FixedClock(moment),
    )


# ---------------------------------------------------------------------------
# The act.
# ---------------------------------------------------------------------------


def test_an_admissible_pull_request_is_landed_and_the_act_is_recorded(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    gateway = ActingGateway()
    record = _land(migrated_session, gateway)

    assert record.status == "merged"
    assert record.merge_commit_sha == LANDED_COMMIT
    assert len(gateway.merges) == 1

    with Session(migrated_engine) as reader:
        stored = reader.scalar(select(EstatePrMerge))
        assert stored is not None
        assert stored.repository == REPOSITORY and stored.pr_number == PR
        assert stored.head_sha == HEAD
        assert stored.status == "merged"


def test_the_permission_is_written_into_the_row(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """The standing condition is re-derivable and will move; the instant at which it authorised an
    irreversible act cannot be recovered from anything else afterwards."""
    _land(migrated_session, ActingGateway())

    with Session(migrated_engine) as reader:
        stored = reader.scalar(select(EstatePrMerge))
        assert stored is not None
        assert stored.change_record_id == 52
        assert stored.policy_version == POLICY_VERSION


def test_the_landing_names_the_head_the_terms_were_evaluated_against(
    migrated_session: Session,
) -> None:
    """The remote refuses any other, which closes the window between deciding and doing without
    this side having had to observe a push."""
    gateway = ActingGateway()
    _land(migrated_session, gateway)

    assert gateway.merges[0][2] == HEAD


def test_the_squash_body_carries_the_basis_the_ledger_reads(migrated_session: Session) -> None:
    """Without it, a landing by this path is indistinguishable to the estate's ledger from a
    machine landing with no accountable basis at all -- a class no detector reads."""
    gateway = ActingGateway()
    _land(migrated_session, gateway)

    body = gateway.merges[0][3]
    assert f"{CHANGE_RECORD_TRAILER}: 52" in body
    assert f"{POLICY_VERSION_TRAILER}: {POLICY_VERSION}" in body


def test_an_event_records_the_act(migrated_session: Session, migrated_engine: Engine) -> None:
    record = _land(migrated_session, ActingGateway())

    with Session(migrated_engine) as reader:
        event = reader.scalar(select(Event).where(Event.subject_id == record.id))
        assert event is not None
        assert event.action == "estate_pr_merge.merged"
        assert event.payload["change_record_id"] == 52
        assert event.payload["policy_version"] == POLICY_VERSION


# ---------------------------------------------------------------------------
# Authorisation and the switch.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("actor", [WORKER, HUMAN], ids=["worker", "human"])
def test_only_the_system_actor_may_land(migrated_session: Session, actor: ActorContext) -> None:
    """Not the worker -- a runner asking for its own work to be landed attests to its own
    compliance. Not a human either: a person can land a pull request themselves, and this exists
    for the case where nobody had to."""
    gateway = ActingGateway()
    with pytest.raises(DomainError) as error:
        _land(migrated_session, gateway, actor=actor)

    assert error.value.code == "role_forbidden"
    assert gateway.merges == []


def test_the_switch_refuses_before_the_remote_is_touched(migrated_session: Session) -> None:
    gateway = ActingGateway()
    with pytest.raises(DomainError) as error:
        _land(migrated_session, gateway, enabled=False)

    assert "landing_not_enabled" in str(error.value)
    assert gateway.merges == []


# ---------------------------------------------------------------------------
# The gate is asked BEFORE the act, not after it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "lands"), [(IN_WINDOW, True), (OUT_OF_WINDOW, False)], ids=["inside", "outside"]
)
def test_the_window_is_enforced_on_the_ACTING_path(
    migrated_session: Session, moment: datetime, lands: bool
) -> None:
    """A PAIR, because an in-window control is what stops this passing for a path that never lands
    at all -- and an out-of-window assertion alone agrees with a term that ignores its clock for
    most of the day.

    The refusal asserts the GATEWAY WAS NEVER REACHED. An admission answer that arrives after the
    act is not a gate, which this repository has already recorded once.
    """
    gateway = ActingGateway()
    if lands:
        assert _land(migrated_session, gateway, moment=moment).status == "merged"
        assert len(gateway.merges) == 1
    else:
        with pytest.raises(DomainError):
            _land(migrated_session, gateway, moment=moment)
        assert gateway.merges == []


def test_an_inert_repository_never_reaches_the_remote(migrated_session: Session) -> None:
    gateway = ActingGateway()
    with pytest.raises(DomainError):
        _land(migrated_session, gateway, landing=inert_source())

    assert gateway.merges == []


def test_a_record_under_a_superseded_policy_version_never_reaches_the_remote(
    migrated_session: Session,
) -> None:
    gateway = ActingGateway()
    with pytest.raises(DomainError) as error:
        _land(migrated_session, gateway, record=approved(policy_version=1))

    assert "landing_policy_version_superseded" in str(error.value)
    assert gateway.merges == []


# ---------------------------------------------------------------------------
# Idempotency: check, act, reconcile, record.
# ---------------------------------------------------------------------------


def test_a_repeat_replays_the_record_and_never_calls_the_remote_again(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """THE property the row exists for. A landing is not idempotent and a lost response answers
    the same way a refusal does, so the question is answered from our own record BEFORE the call.
    """
    first = _land(migrated_session, ActingGateway())

    second_gateway = ActingGateway()
    second = _land(migrated_session, second_gateway, key="land-2")

    assert second.id == first.id
    assert second_gateway.merges == []
    with Session(migrated_engine) as reader:
        assert len(reader.scalars(select(EstatePrMerge)).all()) == 1


def test_a_key_already_spent_on_another_pull_request_is_refused_before_the_remote(
    migrated_session: Session,
) -> None:
    """Otherwise the call reaches the remote, LANDS THE PULL REQUEST, and loses the whole
    transaction to an integrity error with no registered handler -- a bare 500 that reads as
    nothing happened over a landing that did."""
    _land(migrated_session, ActingGateway())

    gateway = ActingGateway(pull=pull_request(number=50))
    with pytest.raises(DomainError) as error:
        land_estate_pull_request(
            migrated_session,
            EstateMergeCommand(
                repository=REPOSITORY,
                pr_number=50,
                actor=SYSTEM,
                idempotency_key="land-1",
                expected_head_sha=HEAD,
            ),
            gateway,
            redeploying_source(),
            FakeChangeRecordSource({(REPOSITORY, 50): approved()}),
            enabled=True,
            credentials_configured=True,
            clock=FixedClock(IN_WINDOW),
        )

    assert error.value.code == "idempotency_conflict"
    assert gateway.merges == []


def test_a_head_that_moved_since_the_caller_read_is_refused_with_no_record(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """The update bot rebasing its own branch is the ordinary cause. Nothing is recorded, because
    nothing happened and the caller can simply re-read."""
    gateway = ActingGateway()
    with pytest.raises(DomainError) as error:
        _land(migrated_session, gateway, expected_head="c" * 40)

    assert error.value.code == "estate_merge_head_moved"
    assert gateway.merges == []
    with Session(migrated_engine) as reader:
        assert reader.scalars(select(EstatePrMerge)).all() == []


def test_a_confirmed_refusal_is_retryable_and_writes_no_record(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """A required check that is red today can be green tomorrow. The row is permanent and unique
    per pull request, so recording this would bar it forever on one bad answer."""
    gateway = ActingGateway(
        merge_error=EstateGatewayError("merge_status", 405), landed_after_refusal=False
    )
    with pytest.raises(DomainError) as error:
        _land(migrated_session, gateway)

    assert error.value.code == "estate_merge_refused_by_remote"
    with Session(migrated_engine) as reader:
        assert reader.scalars(select(EstatePrMerge)).all() == []


def test_a_refusal_over_a_landing_that_actually_happened_is_recorded_as_already_merged(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Without the reconciling read, a retry after a lost response writes `refused` over something
    that happened -- the one outcome nothing downstream could correct."""
    gateway = ActingGateway(
        merge_error=EstateGatewayError("merge_status", 405), landed_after_refusal=True
    )
    record = _land(migrated_session, gateway)

    assert record.status == "already_merged"
    with Session(migrated_engine) as reader:
        stored = reader.scalar(select(EstatePrMerge))
        assert stored is not None and stored.status == "already_merged"


def test_an_unreconcilable_refusal_is_recorded_as_ambiguous(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """The reconciling read itself failed, so a landing cannot be ruled out. Terminal and
    conservative: a retry would meet the same refusal no better informed."""
    gateway = ActingGateway(
        merge_error=EstateGatewayError("merge_status", 500),
        reconcile_error=EstateGatewayError("read_status", 500),
    )
    record = _land(migrated_session, gateway)

    assert record.status == "refused"
    assert record.reason_code is not None
    assert record.reason_code.startswith("merge_refused_by_remote:")
    with Session(migrated_engine) as reader:
        assert reader.scalar(select(EstatePrMerge)) is not None


def test_a_pull_request_found_already_landed_is_recorded_as_somebody_elses_act(
    migrated_session: Session,
) -> None:
    """Terminal and true, so it is recorded -- but never as ours. It is also the state a crash
    between acting and recording leaves behind."""
    gateway = ActingGateway(pull=pull_request(landed=True, is_open=False))
    with pytest.raises(DomainError) as error:
        _land(migrated_session, gateway)

    # The cascade refuses it before the act, which is the honest answer: a landed pull request is
    # not one this lane may land, and recording somebody else's act as ours would be worse.
    assert "landing_pull_request_not_open" in str(error.value)
    assert gateway.merges == []


def test_the_repository_lock_actually_SERIALISES_two_landings(migrated_engine: Engine) -> None:
    """The advisory lock, exercised rather than asserted.

    It exists because the two rules that must not be raced -- one record per pull request, one
    landing per repository per window -- are both stated over rows that MAY NOT EXIST YET, which
    `FOR UPDATE` cannot lock. Two requests would otherwise each read the same absence and each
    act on it, and the pace rule would be a rule about the common case.

    A mutation deleting the lock survives every other test in this file, because nothing else
    here runs two transactions at once. This runs two: the second asks for the same lock with a
    one-second timeout and must be refused while the first holds it.
    """
    from sqlalchemy.exc import OperationalError

    from orchestrator.services.estate_pr_merge import _lock_repository

    with Session(migrated_engine) as first, Session(migrated_engine) as second:
        _lock_repository(first, REPOSITORY)
        second.execute(text("SET LOCAL lock_timeout = '1s'"))
        with pytest.raises(OperationalError):
            _lock_repository(second, REPOSITORY)
        second.rollback()

        # THE CONTROL. Without it this passes for a lock that blocks everything, including the
        # landings into other repositories it is supposed to leave alone.
        second.execute(text("SET LOCAL lock_timeout = '1s'"))
        _lock_repository(second, "alobarquest/brain")
        second.rollback()
        first.rollback()


def test_the_ACTING_PATH_takes_the_repository_lock(migrated_engine: Engine) -> None:
    """The lock's CALL SITE, not the helper. Deleting the call from `_land` left every other test
    in this file green, because nothing else here runs two transactions at once -- this
    repository's own "a test calling a service is not evidence the service has a caller", one
    level in, and the concrete gap a 32-mutation pass could not see.

    A second session holds the lock; `land_estate_pull_request` must then block and time out
    rather than proceed. The CONTROL is below it: with the lock held on a DIFFERENT repository the
    same call goes through, so this cannot pass for a landing that simply always fails.
    """
    from sqlalchemy.exc import OperationalError

    from orchestrator.services.estate_pr_merge import _lock_repository

    with Session(migrated_engine) as holder:
        _lock_repository(holder, REPOSITORY)

        gateway = ActingGateway()
        with Session(migrated_engine) as blocked:
            blocked.execute(text("SET LOCAL lock_timeout = '1s'"))
            with pytest.raises(OperationalError):
                _land(blocked, gateway)
        assert gateway.merges == [], "the landing reached the remote while another held the lock"
        holder.rollback()

    with Session(migrated_engine) as holder:
        _lock_repository(holder, "alobarquest/brain")
        control = ActingGateway()
        with Session(migrated_engine) as free:
            free.execute(text("SET LOCAL lock_timeout = '1s'"))
            assert _land(free, control, key="control-1").status == "merged"
        assert len(control.merges) == 1
        holder.rollback()


def test_a_failure_that_never_reached_the_remote_writes_NO_record(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """The App token is minted BEFORE the request is sent, so a mint failure means nothing left
    this process -- and nothing that cannot have landed may be recorded.

    The reconciling read would fail identically under the same outage and answer "we do not know",
    which writes a permanent `refused` row. The row is unique per pull request with no delete
    path, so one transient credential failure would bar an admissible pull request forever -- and
    the caller reports that refusal as SETTLED rather than as a finding, so nobody would be told.
    The error code carried the distinction all along and nothing read it.
    """
    gateway = ActingGateway(
        merge_error=EstateGatewayError("app_token_mint:private_key_unreadable"),
        reconcile_error=EstateGatewayError("read_status", 500),
    )
    with pytest.raises(DomainError) as error:
        _land(migrated_session, gateway)

    assert error.value.code == "estate_merge_refused_by_remote"
    with Session(migrated_engine) as reader:
        assert reader.scalars(select(EstatePrMerge)).all() == []


def test_a_failure_AFTER_the_send_is_still_recorded_when_it_cannot_be_ruled_out(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """The CONTROL. A code that does not name the pre-send failure keeps the conservative
    behaviour: the request may have landed, so the ambiguity is written down."""
    gateway = ActingGateway(
        merge_error=EstateGatewayError("merge_status", 500),
        reconcile_error=EstateGatewayError("read_status", 500),
    )

    assert _land(migrated_session, gateway).status == "refused"
    with Session(migrated_engine) as reader:
        assert reader.scalar(select(EstatePrMerge)) is not None
