"""WS-P3.7 Increment 4b: the factory lands its own pull request.

The act, as opposed to the answer. Everything here runs with **no network** — the gateway is
injected, and the fake records what it was asked so the tests can assert on the *absence* of a
call, which is the assertion that matters most in a file about not doing something twice.

The idempotency section is the centre of it. A landing is not idempotent and its failure is
asymmetric: a lost response and a refusal are the same 405 at the remote, so the tests that matter
are the ones where the second call must NOT happen, and the one where a refusal must be
reconciled against reality before it is written down as a refusal.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, UnitPrMerge, WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_merge import (
    GitHubGatewayError,
    MergeCommand,
    MergeOutcome,
    PullRequestState,
    land_unit_pull_request,
)
from tests.services.change_record_doubles import no_record_source
from tests.services.estate_doubles import EstateAnswer, FakeEstateLandingSource, inert_source
from tests.services.test_pr_merge_admission import (
    HEAD,
    TARGET,
    _ready_unit,
    _unit,
)

SYSTEM = ActorContext("orchestrator-system", ActorRole.SYSTEM)
WORKER = ActorContext("claude-code-runner", ActorRole.WORKER)
HUMAN = ActorContext("devon", ActorRole.HUMAN)

LANDED_COMMIT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class FakeGateway:
    """Records every question asked of it, so a test can assert what was NOT asked."""

    def __init__(
        self,
        *,
        state: PullRequestState | None = None,
        outcome: MergeOutcome | None = None,
        merge_error: GitHubGatewayError | None = None,
        read_error: GitHubGatewayError | None = None,
        state_after_refusal: PullRequestState | None = None,
    ) -> None:
        self._state = state or PullRequestState(
            base_ref="main", default_branch="main", open=True, landed=False
        )
        self._outcome = outcome or MergeOutcome(
            landed=True, commit_sha=LANDED_COMMIT, status_code=200
        )
        self._merge_error = merge_error
        self._read_error = read_error
        self._state_after_refusal = state_after_refusal
        self.reads: list[tuple[str, int]] = []
        self.merges: list[tuple[str, int, str]] = []

    def read_pull_request(self, *, repository: str, number: int) -> PullRequestState:
        self.reads.append((repository, number))
        if self._read_error is not None:
            raise self._read_error
        if self.merges and self._state_after_refusal is not None:
            return self._state_after_refusal
        return self._state

    def submit_merge(self, *, repository: str, number: int, head_sha: str) -> MergeOutcome:
        self.merges.append((repository, number, head_sha))
        if self._merge_error is not None:
            raise self._merge_error
        return self._outcome


def _land(
    session: Session, unit: WorkUnit, gateway: FakeGateway, *, key: str = "merge-1", source=None
):
    return land_unit_pull_request(
        session,
        MergeCommand(
            unit_id=unit.id,
            actor=SYSTEM,
            idempotency_key=key,
            expected_version=unit.version,
        ),
        gateway,
        source or inert_source(),
        no_record_source(),
    )


# ---------------------------------------------------------------------------------------------
# The act.
# ---------------------------------------------------------------------------------------------


def test_an_admissible_unit_is_landed_and_the_act_is_recorded(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "landable")
    gateway = FakeGateway()

    record = _land(migrated_session, unit, gateway)

    assert record.status == "merged"
    assert record.merge_commit_sha == LANDED_COMMIT
    assert record.repository == TARGET
    assert record.pr_number == 7
    assert gateway.merges == [(TARGET, 7, HEAD)]


def test_the_call_NAMES_the_adjudicated_head(migrated_session: Session) -> None:
    """The load-bearing parameter. Every head this side holds is one somebody REPORTED, so an
    unreported push is invisible to any comparison of our own rows; naming the armed head makes
    the REMOTE refuse a tree the criteria were not decided about."""
    unit = _ready_unit(migrated_session, "names-the-head")
    gateway = FakeGateway()

    _land(migrated_session, unit, gateway)

    assert [head for _, _, head in gateway.merges] == [HEAD]


def test_the_record_is_persisted_and_readable_from_another_session(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Re-read through a DIFFERENT session, the only reader that cannot see an uncommitted write.
    A flush alone would leave the record of an act that really happened absent -- the one state
    the whole idempotency story depends on not reaching."""
    unit = _ready_unit(migrated_session, "record-persisted")
    record_id = _land(migrated_session, unit, FakeGateway()).id

    with Session(migrated_engine) as reader:
        stored = reader.get(UnitPrMerge, record_id)
        assert stored is not None
        assert stored.status == "merged"


def test_the_act_writes_an_event_naming_the_fingerprint_it_acted_under(
    migrated_session: Session,
) -> None:
    unit = _ready_unit(migrated_session, "act-event")

    _land(migrated_session, unit, FakeGateway())

    event = migrated_session.scalar(
        select(Event).where(Event.subject_id == unit.id, Event.action == "pr_merge.merged")
    )
    assert event is not None
    assert event.payload["head_sha"] == HEAD
    assert event.payload["authority_fingerprint"] == unit.authority_fingerprint


# ---------------------------------------------------------------------------------------------
# Idempotency: the second call must not happen.
# ---------------------------------------------------------------------------------------------


def test_a_repeat_replays_the_record_and_never_calls_the_remote_again(
    migrated_session: Session,
) -> None:
    """The scar this copies: a reused ordinal makes the workflow trigger a success-shaped no-op.
    Here the danger is the opposite -- a second call REACHES the remote and comes back with a
    plausible refusal -- so the record is read before the call, under the unit's row lock."""
    unit = _ready_unit(migrated_session, "repeat")
    gateway = FakeGateway()

    first = _land(migrated_session, unit, gateway)
    second = _land(migrated_session, unit, gateway, key="merge-2")

    assert second.id == first.id
    assert gateway.merges == [(TARGET, 7, HEAD)]
    assert len(gateway.merges) == 1


def test_a_refusal_we_could_not_confirm_is_terminal_and_does_not_call_again(
    migrated_session: Session,
) -> None:
    """The ONE ambiguous outcome: the remote refused and the confirming read also failed, so a
    landing cannot be ruled out. Recorded, and terminal — a retry would meet the same refusal and
    be no better informed, and the ledger observes the landing independently."""
    unit = _ready_unit(migrated_session, "repeat-after-refusal")

    class Blind(FakeGateway):
        def read_pull_request(self, *, repository: str, number: int) -> PullRequestState:
            if self.merges:
                raise GitHubGatewayError("request_error:ConnectError")
            return super().read_pull_request(repository=repository, number=number)

    gateway = Blind(merge_error=GitHubGatewayError("merge_status", 405))

    first = _land(migrated_session, unit, gateway)
    assert first.status == "refused"

    second = _land(migrated_session, unit, gateway, key="merge-2")

    assert second.id == first.id
    assert len(gateway.merges) == 1


def test_only_one_record_can_ever_exist_for_a_unit(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    unit = _ready_unit(migrated_session, "one-record")

    _land(migrated_session, unit, FakeGateway())
    _land(migrated_session, unit, FakeGateway(), key="merge-2")

    with Session(migrated_engine) as reader:
        rows = list(reader.scalars(select(UnitPrMerge).where(UnitPrMerge.work_unit_id == unit.id)))
    assert len(rows) == 1


def test_a_refusal_is_reconciled_against_reality_before_it_is_recorded(
    migrated_session: Session,
) -> None:
    """THE case act-then-record exists for. The remote refuses -- indistinguishable from a lost
    response to a call that succeeded -- so the pull request is re-read. It landed, so the record
    says `already_merged` rather than writing `refused` over something that happened."""
    unit = _ready_unit(migrated_session, "lost-response")
    gateway = FakeGateway(
        merge_error=GitHubGatewayError("merge_status", 405),
        state_after_refusal=PullRequestState(
            base_ref="main", default_branch="main", open=False, landed=True
        ),
    )

    record = _land(migrated_session, unit, gateway)

    assert record.status == "already_merged"
    assert record.reason_code is None
    assert len(gateway.reads) == 2


def test_a_refusal_CONFIRMED_not_to_have_landed_leaves_no_record_and_is_retryable(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """Branch protection refusing a red required check is a real refusal — and nothing happened,
    so it must not consume the unit's one row. A check that is red today can be green tomorrow;
    recording this would bar the unit from ever being landed by the factory."""
    unit = _ready_unit(migrated_session, "genuine-refusal")
    gateway = FakeGateway(merge_error=GitHubGatewayError("merge_status", 405))

    with pytest.raises(DomainError) as error:
        _land(migrated_session, unit, gateway)

    assert error.value.code == "pr_merge_refused_by_remote"
    with Session(migrated_engine) as reader:
        assert reader.scalar(select(UnitPrMerge).where(UnitPrMerge.work_unit_id == unit.id)) is None

    # ... and the retry reaches the remote again, which is the whole point.
    healthy = FakeGateway()
    assert _land(migrated_session, unit, healthy, key="merge-2").status == "merged"


def test_the_ambiguous_refusal_records_the_gateway_code_it_could_not_resolve(
    migrated_session: Session,
) -> None:
    """An operator reading a `refused` record must be able to tell a red required check from a
    connection that died — the gateway's own code is the only value that distinguishes them."""
    unit = _ready_unit(migrated_session, "read-fails-too")

    class Flaky(FakeGateway):
        def read_pull_request(self, *, repository: str, number: int) -> PullRequestState:
            if self.merges:
                raise GitHubGatewayError("request_error:ConnectError")
            return super().read_pull_request(repository=repository, number=number)

    record = _land(
        migrated_session, unit, Flaky(merge_error=GitHubGatewayError("merge_status", 405))
    )

    assert record.status == "refused"
    assert record.reason_code == "merge_refused_by_remote:merge_status"
    assert record.github_status == 405


def _no_record(session: Session, unit: WorkUnit) -> bool:
    session.expire_all()
    return session.scalar(select(UnitPrMerge).where(UnitPrMerge.work_unit_id == unit.id)) is None


# ---------------------------------------------------------------------------------------------
# The terms only the remote knows. NONE of them writes a record: nothing happened, and each is
# transient or fixable, so consuming the unit's one row would bar it permanently.
# ---------------------------------------------------------------------------------------------


def test_a_pull_request_against_a_non_default_base_is_refused_without_landing(
    migrated_session: Session,
) -> None:
    """The estate's answer is about the DEFAULT branch. A different base is a different question,
    and nothing on this side records what a pull request targets."""
    unit = _ready_unit(migrated_session, "wrong-base")
    gateway = FakeGateway(
        state=PullRequestState(
            base_ref="release/2026-08", default_branch="main", open=True, landed=False
        )
    )

    with pytest.raises(DomainError) as error:
        _land(migrated_session, unit, gateway)

    assert error.value.code == "pr_merge_base_not_default_branch"
    assert gateway.merges == []
    assert _no_record(migrated_session, unit)


def test_a_closed_pull_request_is_refused_without_landing(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "closed-pr")
    gateway = FakeGateway(
        state=PullRequestState(base_ref="main", default_branch="main", open=False, landed=False)
    )

    with pytest.raises(DomainError) as error:
        _land(migrated_session, unit, gateway)

    assert error.value.code == "pr_merge_not_open"
    assert gateway.merges == []
    assert _no_record(migrated_session, unit)


def test_a_pull_request_somebody_else_landed_is_recorded_as_such_not_claimed(
    migrated_session: Session,
) -> None:
    unit = _ready_unit(migrated_session, "landed-elsewhere")
    gateway = FakeGateway(
        state=PullRequestState(base_ref="main", default_branch="main", open=False, landed=True)
    )

    record = _land(migrated_session, unit, gateway)

    assert record.status == "already_merged"
    assert gateway.merges == []


def test_an_unreadable_remote_is_refused_without_landing(migrated_session: Session) -> None:
    unit = _ready_unit(migrated_session, "unreadable-remote")
    gateway = FakeGateway(read_error=GitHubGatewayError("read_status", 502))

    with pytest.raises(DomainError) as error:
        _land(migrated_session, unit, gateway)

    assert error.value.code == "pr_merge_remote_unreadable"
    assert gateway.merges == []
    # THE FINDING both reviews made severe: a transient read failure must not consume the unit's
    # one and only row. Recording it barred the unit from ever being landed by the factory, for
    # the duration of one bad response, with no repair path.
    assert _no_record(migrated_session, unit)
    assert _land(migrated_session, unit, FakeGateway(), key="merge-2").status == "merged"


# ---------------------------------------------------------------------------------------------
# Admission is re-evaluated at the moment of the act, and refuses without recording.
# ---------------------------------------------------------------------------------------------


def test_an_inadmissible_unit_is_refused_and_NOTHING_is_called_or_recorded(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """No record, deliberately: the unit was never acted on, and consuming its one row here would
    refuse every later legitimate attempt."""
    unit = _unit(migrated_session, "never-completed")
    gateway = FakeGateway()

    with pytest.raises(DomainError) as error:
        _land(migrated_session, unit, gateway)

    assert error.value.code == "pr_merge_not_admissible"
    assert gateway.merges == []
    assert gateway.reads == []
    with Session(migrated_engine) as reader:
        assert reader.scalar(select(UnitPrMerge).where(UnitPrMerge.work_unit_id == unit.id)) is None


def test_a_term_that_changed_since_the_answer_was_read_refuses_at_the_act(
    migrated_session: Session,
) -> None:
    """The window the act exists to close. The reported answer said yes; by the time the act runs
    the estate says the repository redeploys, and the act asks again rather than trusting it."""
    unit = _ready_unit(migrated_session, "estate-changed")
    gateway = FakeGateway()

    with pytest.raises(DomainError) as error:
        _land(
            migrated_session,
            unit,
            gateway,
            source=FakeEstateLandingSource(default=EstateAnswer("redeploys")),
        )

    assert error.value.code == "pr_merge_not_admissible"
    assert gateway.merges == []


# ---------------------------------------------------------------------------------------------
# Who may ask.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("actor", [WORKER, HUMAN], ids=["worker", "human"])
def test_only_the_system_actor_may_ask_for_a_landing(
    migrated_session: Session, actor: ActorContext
) -> None:
    """Not the worker, above all: a runner asking for its own pull request to be landed is the
    runner attesting to its own compliance. A human does not need this route -- a person can land
    a pull request themselves, and this exists for the case where nobody had to."""
    unit = _ready_unit(migrated_session, f"role-{actor.role.value}")
    gateway = FakeGateway()

    with pytest.raises(DomainError) as error:
        land_unit_pull_request(
            migrated_session,
            MergeCommand(
                unit_id=unit.id,
                actor=actor,
                idempotency_key="role-check",
                expected_version=unit.version,
            ),
            gateway,
            inert_source(),
            no_record_source(),
        )

    assert error.value.code == "role_forbidden"
    assert gateway.merges == []


def test_a_stale_expected_version_refuses_before_anything_is_called(
    migrated_session: Session,
) -> None:
    unit = _ready_unit(migrated_session, "stale-version")
    gateway = FakeGateway()

    with pytest.raises(DomainError) as error:
        land_unit_pull_request(
            migrated_session,
            MergeCommand(
                unit_id=unit.id,
                actor=SYSTEM,
                idempotency_key="stale",
                expected_version=unit.version + 5,
            ),
            gateway,
            inert_source(),
            no_record_source(),
        )

    assert error.value.code == "version_conflict"
    assert gateway.merges == []


def test_a_unit_that_does_not_exist_is_a_named_domain_error(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        land_unit_pull_request(
            migrated_session,
            MergeCommand(
                unit_id=uuid.uuid4(),
                actor=SYSTEM,
                idempotency_key="missing",
                expected_version=0,
            ),
            FakeGateway(),
            inert_source(),
            no_record_source(),
        )

    assert error.value.code == "work_unit_not_found"


# ---------------------------------------------------------------------------------------------
# Two more the reviews found, both of which end in a bare 500 without their guard.
# ---------------------------------------------------------------------------------------------


def test_unconfigured_app_credentials_refuse_before_the_remote_is_touched(
    migrated_session: Session,
) -> None:
    """The workflow trigger asks this at its gate and says why: the gate and the minter must read
    the same answer. Without the term, a release that shipped without the App variables would meet
    the remote on every unit, fail to mint, and record a permanent refusal each time."""
    unit = _ready_unit(migrated_session, "no-credentials")
    gateway = FakeGateway()

    with pytest.raises(DomainError) as error:
        land_unit_pull_request(
            migrated_session,
            MergeCommand(
                unit_id=unit.id,
                actor=SYSTEM,
                idempotency_key="no-creds",
                expected_version=unit.version,
            ),
            gateway,
            inert_source(),
            no_record_source(),
            False,
        )

    assert error.value.code == "pr_merge_app_credentials_missing"
    assert gateway.reads == []
    assert gateway.merges == []
    assert _no_record(migrated_session, unit)


def test_an_idempotency_key_spent_on_another_unit_is_refused_before_the_call(
    migrated_session: Session,
) -> None:
    """An operator copies one request and changes only the unit id. Without this check the second
    unit's pull request IS LANDED and the flush then violates the global unique key, rolling the
    whole transaction back and losing the record of a merge that happened — as a bare 500, since
    `IntegrityError` has no registered handler. Reproduced by review before it was closed."""
    first = _ready_unit(migrated_session, "key-owner")
    second = _ready_unit(migrated_session, "key-borrower")
    _land(migrated_session, first, FakeGateway(), key="shared-key")
    gateway = FakeGateway()

    with pytest.raises(DomainError) as error:
        _land(migrated_session, second, gateway, key="shared-key")

    assert error.value.code == "idempotency_conflict"
    assert gateway.merges == []
    assert _no_record(migrated_session, second)
