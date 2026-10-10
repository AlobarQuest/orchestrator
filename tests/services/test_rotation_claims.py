"""Claims on rotation units: confinement both ways, the claim-time window, and lapse recovery.

ADR-0055 decisions 1, 8 and 9, as amended by amendment 2. Each guard has a refusal and its
control: the same predicate admitting the input it is meant to admit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.factory_policy import OUTSIDE_CHANGE_WINDOW
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.kernel.states import ActorContext, ActorRole, WorkUnitState
from orchestrator.persistence.models import Claim, Event, Evidence, WorkPackageRevision, WorkUnit
from orchestrator.services.intake.packages import register_approved_unit, register_revision
from orchestrator.services.lifecycle.claim_release import release_claim
from orchestrator.services.lifecycle.claims import (
    LeaseGrant,
    claim_unit,
    reclaim_expired_claim,
    release_expired_claim,
    renew_claim,
)
from orchestrator.services.lifecycle.rotation_claims import (
    NON_HOSTED_CONSTRAINT,
    claim_window_refusal,
    is_rotation_unit,
)
from orchestrator.services.reporting.pending_decisions import pending_decisions
from tests.services.test_reclaim import authorize_readiness, expire

EXECUTOR = "rotation-executor"
EXECUTOR_ACTOR = ActorContext(EXECUTOR, ActorRole.WORKER)
OTHER = ActorContext("worker-1", ActorRole.WORKER)
SYSTEM = ActorContext("system", ActorRole.SYSTEM)
NOW = datetime(2026, 10, 9, tzinfo=UTC)
# The shipped `live_estate` window is 02:00-06:00 America/New_York.
OPEN = datetime(2026, 10, 10, 7, 30, tzinfo=UTC)  # 03:30 in New York
SHUT = datetime(2026, 10, 10, 16, 0, tzinfo=UTC)  # 12:00 in New York

ROTATION_FIELDS: dict[str, Any] = {
    "owner": "devon",
    "operating_procedure": "rotate",
    "standing": True,
    "credential_id": "openrouter-generic",
    "occurrence": "2026-10-09-requested",
}


def seed_unit(
    session: Session,
    key: str,
    *,
    profile: str | None = "non-software-operational",
    fields: dict[str, Any] | None = None,
    capability: str = "operational_action",
    constraints: dict[str, Any] | None = None,
    reach: list[str] | None = None,
    max_llm_calls: int | None = 10,
    max_attempts: int = 5,
) -> WorkUnit:
    """A READY unit with the revision and envelope the test names; a rotation unit by default."""
    snapshot: dict[str, Any] = {
        "acceptance_criteria": ["ac-1"],
        "profile_fields": ROTATION_FIELDS if fields is None else fields,
        "reach": ["external_system", "operator_machine"] if reach is None else reach,
    }
    authority = AuthorityEnvelope(
        capabilities={capability: "allowed"},
        budgets=AuthorityBudgets(max_attempts=max_attempts, max_llm_calls=max_llm_calls),
        constraints=constraints or {},
    )
    revision = register_revision(
        session,
        package_id=f"{key}-package",
        source_repository="AlobarQuest/intent-packages",
        revision=1,
        content_hash=f"sha256:{key}",
        source_path="packages/x/package.yaml",
        source_commit="abc123",
        approved_by="devon",
        approved_at=NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot=snapshot,
        authority=authority,
        registry_version=1,
        actor_id="devon",
        actor_role=ActorRole.HUMAN,
        profile=profile,
    )
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} done",
        required_capability=capability,
        authority=authority,
        max_attempts=max_attempts,
        approved_by="devon",
        approved_at=NOW,
        actor_id="devon",
        actor_role=ActorRole.HUMAN,
    )
    authorize_readiness(session, unit)
    unit.state = WorkUnitState.READY
    session.commit()
    return unit


class _Policy:
    """A stand-in policy whose window answer the test chooses; it records the reach it was asked."""

    def __init__(self, refusal: str | None) -> None:
        self.refusal = refusal
        self.asked: list[tuple[str, ...]] = []

    def window_refusal(self, reach: tuple[str, ...], now: datetime) -> str | None:
        self.asked.append(tuple(reach))
        return self.refusal


@pytest.fixture
def window_shut(monkeypatch: pytest.MonkeyPatch) -> _Policy:
    policy = _Policy(OUTSIDE_CHANGE_WINDOW)
    monkeypatch.setattr(
        "orchestrator.services.lifecycle.rotation_claims.load_factory_policy", lambda: policy
    )
    return policy


@pytest.fixture
def window_open(monkeypatch: pytest.MonkeyPatch) -> _Policy:
    policy = _Policy(None)
    monkeypatch.setattr(
        "orchestrator.services.lifecycle.rotation_claims.load_factory_policy", lambda: policy
    )
    return policy


def _claim(
    session: Session, unit: WorkUnit, actor: ActorContext, key: str, executor: str | None = EXECUTOR
) -> LeaseGrant | DomainError:
    return claim_unit(session, unit.id, actor, key, rotation_executor=executor)


def _untouched(session: Session, unit: WorkUnit) -> None:
    """A refused claim left no trace: still READY, no attempt spent, no claim row."""
    session.expire_all()
    assert unit.state == WorkUnitState.READY
    assert unit.attempt_count == 0
    claims = session.scalar(select(func.count()).where(Claim.work_unit_id == unit.id))
    assert claims == 0


# ---------------------------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param({}, True, id="rotation"),
        pytest.param({"capability": "repo.edit"}, False, id="not_operational"),
        pytest.param({"profile": "software-delivery"}, False, id="other_profile"),
        pytest.param({"profile": None}, False, id="no_profile"),
        pytest.param({"fields": {**ROTATION_FIELDS, "standing": False}}, False, id="not_standing"),
        pytest.param({"fields": {**ROTATION_FIELDS, "standing": "true"}}, False, id="standing_str"),
        pytest.param(
            {"fields": {k: v for k, v in ROTATION_FIELDS.items() if k != "standing"}},
            False,
            id="standing_absent",
        ),
        pytest.param(
            {"fields": {k: v for k, v in ROTATION_FIELDS.items() if k != "credential_id"}},
            False,
            id="credential_absent",
        ),
        pytest.param(
            {"fields": {**ROTATION_FIELDS, "credential_id": "  "}}, False, id="credential_blank"
        ),
        pytest.param(
            {"fields": {**ROTATION_FIELDS, "credential_id": 7}}, False, id="credential_int"
        ),
    ],
)
def test_the_rotation_unit_predicate(
    migrated_session: Session, overrides: dict[str, Any], expected: bool
) -> None:
    unit = seed_unit(migrated_session, f"predicate-{uuid.uuid4().hex[:8]}", **overrides)
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None
    assert is_rotation_unit(unit, revision) is expected


def test_a_snapshot_without_profile_fields_is_not_a_rotation_unit(
    migrated_session: Session,
) -> None:
    unit = seed_unit(migrated_session, "predicate-no-fields", fields={})
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None
    revision.enforcement_snapshot = {"acceptance_criteria": ["ac-1"]}
    assert is_rotation_unit(unit, revision) is False
    migrated_session.rollback()


# ---------------------------------------------------------------------------------------------
# Confinement both ways
# ---------------------------------------------------------------------------------------------


def test_with_no_executor_configured_every_rotation_claim_is_refused(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "confine-unset")

    for actor in (OTHER, EXECUTOR_ACTOR):
        refused = _claim(migrated_session, unit, actor, f"confine-unset-{actor.actor_id}", None)
        assert isinstance(refused, DomainError) and refused.code == "rotation_claim_confined"
    _untouched(migrated_session, unit)


def test_another_worker_may_not_claim_a_rotation_unit(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "confine-other")

    refused = _claim(migrated_session, unit, OTHER, "confine-other-1")

    assert isinstance(refused, DomainError) and refused.code == "rotation_claim_confined"
    _untouched(migrated_session, unit)


def test_the_executor_may_claim_a_rotation_unit(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "confine-executor")

    grant = _claim(migrated_session, unit, EXECUTOR_ACTOR, "confine-executor-1")

    assert isinstance(grant, LeaseGrant), grant
    migrated_session.expire_all()
    assert unit.state == WorkUnitState.CLAIMED


def test_the_executor_may_not_claim_other_work(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "confine-not-rotation", capability="repo.edit")

    refused = _claim(migrated_session, unit, EXECUTOR_ACTOR, "confine-not-rotation-1")

    assert isinstance(refused, DomainError) and refused.code == "rotation_executor_confined"
    _untouched(migrated_session, unit)


def test_other_workers_claim_other_work_as_before(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "confine-control", capability="repo.edit")

    grant = _claim(migrated_session, unit, OTHER, "confine-control-1")

    assert isinstance(grant, LeaseGrant), grant
    assert window_open.asked == []


def test_confinement_refuses_before_the_budget_halt_writes(
    migrated_session: Session, window_open: _Policy
) -> None:
    """The budget halt commits a FAILED transition; a claimant who may not touch the unit must not
    cause it. The control shows the same unit IS halted by its rightful claimant."""
    unit = seed_unit(migrated_session, "confine-budget", max_llm_calls=0)

    refused = _claim(migrated_session, unit, OTHER, "confine-budget-1")
    assert isinstance(refused, DomainError) and refused.code == "rotation_claim_confined"
    _untouched(migrated_session, unit)

    halted = _claim(migrated_session, unit, EXECUTOR_ACTOR, "confine-budget-2")
    assert isinstance(halted, DomainError) and halted.code == "budget_exceeded"
    migrated_session.expire_all()
    assert unit.state == WorkUnitState.FAILED


# ---------------------------------------------------------------------------------------------
# The claim-time window
# ---------------------------------------------------------------------------------------------


def test_a_hosted_rotation_unit_does_not_start_outside_the_window(
    migrated_session: Session, window_shut: _Policy
) -> None:
    unit = seed_unit(migrated_session, "window-shut")

    refused = _claim(migrated_session, unit, EXECUTOR_ACTOR, "window-shut-1")

    assert isinstance(refused, DomainError) and refused.code == "outside_change_window"
    _untouched(migrated_session, unit)


def test_a_hosted_rotation_unit_starts_inside_the_window(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "window-open")

    grant = _claim(migrated_session, unit, EXECUTOR_ACTOR, "window-open-1")

    assert isinstance(grant, LeaseGrant), grant
    assert window_open.asked == [("live_estate",)]


def test_the_live_estate_window_is_asked_whatever_reach_is_declared(
    migrated_session: Session, window_shut: _Policy
) -> None:
    unit = seed_unit(migrated_session, "window-reach", reach=["source_repository"])

    refused = _claim(migrated_session, unit, EXECUTOR_ACTOR, "window-reach-1")

    assert isinstance(refused, DomainError) and refused.code == "outside_change_window"
    assert window_shut.asked == [("live_estate",)]


def test_a_unit_marked_non_hosted_starts_at_any_hour(
    migrated_session: Session, window_shut: _Policy
) -> None:
    unit = seed_unit(
        migrated_session, "window-non-hosted", constraints={NON_HOSTED_CONSTRAINT: True}
    )

    grant = _claim(migrated_session, unit, EXECUTOR_ACTOR, "window-non-hosted-1")

    assert isinstance(grant, LeaseGrant), grant
    assert window_shut.asked == []


@pytest.mark.parametrize("marker", ["true", 1, False], ids=["string", "one", "false"])
def test_only_a_literal_true_marks_a_unit_non_hosted(
    migrated_session: Session, window_shut: _Policy, marker: object
) -> None:
    unit = seed_unit(
        migrated_session, f"window-marker-{marker!s}", constraints={NON_HOSTED_CONSTRAINT: marker}
    )

    refused = _claim(migrated_session, unit, EXECUTOR_ACTOR, f"window-marker-{marker!s}-1")

    assert isinstance(refused, DomainError) and refused.code == "outside_change_window"


def test_an_operational_unit_that_is_not_a_rotation_unit_is_not_windowed_at_claim(
    migrated_session: Session, window_shut: _Policy
) -> None:
    unit = seed_unit(
        migrated_session,
        "window-operational",
        fields={"owner": "devon", "operating_procedure": "launch"},
        reach=["live_estate"],
    )

    grant = _claim(migrated_session, unit, OTHER, "window-operational-1")

    assert isinstance(grant, LeaseGrant), grant
    assert window_shut.asked == []


def test_an_unreadable_policy_refuses_the_claim(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable() -> None:
        raise DomainError("factory_policy_invalid", "the policy artifact is invalid", None)

    monkeypatch.setattr(
        "orchestrator.services.lifecycle.rotation_claims.load_factory_policy", unreadable
    )
    unit = seed_unit(migrated_session, "window-unreadable")

    refused = _claim(migrated_session, unit, EXECUTOR_ACTOR, "window-unreadable-1")

    assert isinstance(refused, DomainError) and refused.code == "factory_policy_invalid"
    _untouched(migrated_session, unit)


@pytest.mark.parametrize(("now", "expected"), [(OPEN, None), (SHUT, "outside_change_window")])
def test_the_shipped_policy_answers_the_claim_window(
    migrated_session: Session, now: datetime, expected: str | None
) -> None:
    unit = seed_unit(migrated_session, f"window-shipped-{expected}")
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None

    refusal = claim_window_refusal(migrated_session, unit, revision, now)

    assert (refusal.code if refusal is not None else None) == expected


def _lapse(
    session: Session,
    unit: WorkUnit,
    key: str,
    *,
    evidence_by: str | None,
    evidence_attempt_offset: int = 0,
    reason: str = "lease_expired",
) -> None:
    """The executor claims, optionally files evidence, and its claim is then released."""
    grant = _claim(session, unit, EXECUTOR_ACTOR, f"{key}-claim")
    assert isinstance(grant, LeaseGrant), grant
    if evidence_by is not None:
        session.add(
            Evidence(
                work_package_revision_id=unit.work_package_revision_id,
                work_unit_id=unit.id,
                ac_id="ac-1",
                attempt=grant.attempt + evidence_attempt_offset,
                evidence_type="observation",
                payload={"step": "mint"},
                source_revision="r1",
                recorded_by=evidence_by,
                event_id=uuid.uuid4(),
                idempotency_key=f"{key}-evidence",
            )
        )
    claim = session.get(Claim, grant.claim_id)
    assert claim is not None
    release_claim(claim, terminal_reason=reason, released_at=NOW)
    unit.state = WorkUnitState.READY
    session.commit()


def test_a_claim_after_a_lapse_with_the_holders_evidence_continues_outside_the_window(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = seed_unit(migrated_session, "continue-yes")
    monkeypatch.setattr(
        "orchestrator.services.lifecycle.rotation_claims.load_factory_policy",
        lambda: _Policy(None),
    )
    _lapse(migrated_session, unit, "continue-yes", evidence_by=EXECUTOR)
    shut = _Policy(OUTSIDE_CHANGE_WINDOW)
    monkeypatch.setattr(
        "orchestrator.services.lifecycle.rotation_claims.load_factory_policy", lambda: shut
    )

    grant = _claim(migrated_session, unit, EXECUTOR_ACTOR, "continue-yes-2")

    assert isinstance(grant, LeaseGrant), grant
    assert shut.asked == []


@pytest.mark.parametrize(
    ("evidence_by", "offset", "reason"),
    [
        pytest.param(None, 0, "lease_expired", id="no_evidence"),
        pytest.param("system", 0, "lease_expired", id="evidence_by_another_actor"),
        pytest.param(EXECUTOR, -1, "lease_expired", id="evidence_on_another_attempt"),
        pytest.param(EXECUTOR, 0, "work_unit_failed", id="released_by_failure"),
    ],
)
def test_every_other_claim_after_a_release_is_a_start(
    migrated_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    evidence_by: str | None,
    offset: int,
    reason: str,
) -> None:
    key = f"continue-no-{evidence_by}-{offset}-{reason}"
    unit = seed_unit(migrated_session, key)
    if offset:
        # An attempt before the lapsed one, so evidence can name it.
        unit.attempt_count = 1
        migrated_session.commit()
    monkeypatch.setattr(
        "orchestrator.services.lifecycle.rotation_claims.load_factory_policy",
        lambda: _Policy(None),
    )
    _lapse(
        migrated_session,
        unit,
        key,
        evidence_by=evidence_by,
        evidence_attempt_offset=offset,
        reason=reason,
    )
    monkeypatch.setattr(
        "orchestrator.services.lifecycle.rotation_claims.load_factory_policy",
        lambda: _Policy(OUTSIDE_CHANGE_WINDOW),
    )

    refused = _claim(migrated_session, unit, EXECUTOR_ACTOR, f"{key}-2")

    assert isinstance(refused, DomainError) and refused.code == "outside_change_window"


def test_renewal_is_never_windowed(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = seed_unit(migrated_session, "renew-shut")
    monkeypatch.setattr(
        "orchestrator.services.lifecycle.rotation_claims.load_factory_policy",
        lambda: _Policy(None),
    )
    grant = _claim(migrated_session, unit, EXECUTOR_ACTOR, "renew-shut-1")
    assert isinstance(grant, LeaseGrant), grant
    shut = _Policy(OUTSIDE_CHANGE_WINDOW)
    monkeypatch.setattr(
        "orchestrator.services.lifecycle.rotation_claims.load_factory_policy", lambda: shut
    )

    renewed = renew_claim(
        migrated_session, unit.id, EXECUTOR_ACTOR, grant.attempt, grant.lease_token
    )

    assert isinstance(renewed, LeaseGrant), renewed
    assert shut.asked == []


# ---------------------------------------------------------------------------------------------
# Reclaim refuses; release recovers
# ---------------------------------------------------------------------------------------------


def _lapsed(session: Session, unit: WorkUnit, actor: ActorContext, key: str) -> LeaseGrant:
    grant = _claim(session, unit, actor, f"{key}-claim")
    assert isinstance(grant, LeaseGrant), grant
    expire(session, grant.claim_id)
    session.expire_all()
    return grant


def _claim_row(session: Session, claim_id: uuid.UUID) -> Claim:
    session.expire_all()
    claim = session.get(Claim, claim_id)
    assert claim is not None
    return claim


def test_reclaim_refuses_a_rotation_unit_and_releases_nothing(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "reclaim-rotation")
    grant = _lapsed(migrated_session, unit, EXECUTOR_ACTOR, "reclaim-rotation")

    refused = reclaim_expired_claim(
        migrated_session,
        unit.id,
        SYSTEM,
        EXECUTOR_ACTOR,
        "reclaim-rotation-1",
        rotation_executor=EXECUTOR,
    )

    assert isinstance(refused, DomainError) and refused.code == "rotation_reclaim_refused"
    assert _claim_row(migrated_session, grant.claim_id).released_at is None
    assert unit.state == WorkUnitState.CLAIMED


def test_reclaim_refuses_to_grant_other_work_to_the_executor(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "reclaim-to-executor", capability="repo.edit")
    grant = _lapsed(migrated_session, unit, OTHER, "reclaim-to-executor")

    refused = reclaim_expired_claim(
        migrated_session,
        unit.id,
        SYSTEM,
        EXECUTOR_ACTOR,
        "reclaim-to-executor-1",
        rotation_executor=EXECUTOR,
    )

    assert isinstance(refused, DomainError) and refused.code == "rotation_executor_confined"
    assert _claim_row(migrated_session, grant.claim_id).released_at is None
    assert unit.state == WorkUnitState.CLAIMED


def test_reclaim_of_other_work_to_another_worker_is_unchanged(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "reclaim-control", capability="repo.edit")
    _lapsed(migrated_session, unit, OTHER, "reclaim-control")

    grant = reclaim_expired_claim(
        migrated_session,
        unit.id,
        SYSTEM,
        ActorContext("worker-2", ActorRole.WORKER),
        "reclaim-control-1",
        rotation_executor=EXECUTOR,
    )

    assert isinstance(grant, LeaseGrant), grant


def test_release_returns_a_lapsed_rotation_unit_to_ready_without_a_grant(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "release-rotation")
    grant = _lapsed(migrated_session, unit, EXECUTOR_ACTOR, "release-rotation")

    released = release_expired_claim(migrated_session, unit.id, SYSTEM, "release-rotation-1")

    assert isinstance(released, WorkUnit), released
    claim = _claim_row(migrated_session, grant.claim_id)
    assert (claim.terminal_reason, claim.released_at is not None) == ("lease_expired", True)
    assert unit.state == WorkUnitState.READY
    assert unit.attempt_count == 1
    claims = migrated_session.scalar(select(func.count()).where(Claim.work_unit_id == unit.id))
    assert claims == 1

    again = _claim(migrated_session, unit, EXECUTOR_ACTOR, "release-rotation-2")
    assert isinstance(again, LeaseGrant) and again.attempt == 2


def test_release_refuses_an_unexpired_claim(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "release-live")
    grant = _claim(migrated_session, unit, EXECUTOR_ACTOR, "release-live-claim")
    assert isinstance(grant, LeaseGrant)

    refused = release_expired_claim(migrated_session, unit.id, SYSTEM, "release-live-1")

    assert isinstance(refused, DomainError) and refused.code == "lease_not_expired"
    assert _claim_row(migrated_session, grant.claim_id).released_at is None


@pytest.mark.parametrize("role", [ActorRole.WORKER, ActorRole.HUMAN])
@pytest.mark.parametrize("max_attempts", [5, 1], ids=["eligible", "ineligible"])
def test_only_the_system_releases(
    migrated_session: Session, window_open: _Policy, role: ActorRole, max_attempts: int
) -> None:
    """The ineligible case matters: there the release stops at FAILED, an edge a worker holds."""
    unit = seed_unit(
        migrated_session, f"release-role-{role}-{max_attempts}", max_attempts=max_attempts
    )
    grant = _lapsed(migrated_session, unit, EXECUTOR_ACTOR, f"release-role-{role}-{max_attempts}")

    refused = release_expired_claim(
        migrated_session, unit.id, ActorContext("someone", role), f"release-role-{role}-1"
    )

    assert isinstance(refused, DomainError) and refused.code == "role_forbidden"
    assert _claim_row(migrated_session, grant.claim_id).released_at is None


def _events(session: Session, key: str) -> list[Event]:
    return list(
        session.scalars(
            select(Event).where(Event.idempotency_key.in_([key, f"{key}:failed", f"{key}:ready"]))
        )
    )


def test_a_duplicate_release_writes_one_failed_and_one_ready_event(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "release-replay")
    _lapsed(migrated_session, unit, EXECUTOR_ACTOR, "release-replay")

    one = release_expired_claim(migrated_session, unit.id, SYSTEM, "release-replay-1")
    two = release_expired_claim(migrated_session, unit.id, SYSTEM, "release-replay-1")

    assert isinstance(one, WorkUnit) and isinstance(two, WorkUnit)
    keys = sorted(event.idempotency_key for event in _events(migrated_session, "release-replay-1"))
    assert keys == ["release-replay-1:failed", "release-replay-1:ready"]


def test_an_ineligible_unit_stays_failed_and_its_replay_returns_the_same_error(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "release-exhausted", max_attempts=1)
    _lapsed(migrated_session, unit, EXECUTOR_ACTOR, "release-exhausted")

    one = release_expired_claim(migrated_session, unit.id, SYSTEM, "release-exhausted-1")
    # Read through another session BEFORE the replay, which commits whatever it finds open.
    with Session(migrated_session.get_bind()) as reader:
        stored = reader.get(WorkUnit, unit.id)
        assert stored is not None and stored.state == WorkUnitState.FAILED
    two = release_expired_claim(migrated_session, unit.id, SYSTEM, "release-exhausted-1")

    assert isinstance(one, DomainError) and one.code == "attempts_exhausted"
    assert isinstance(two, DomainError) and two.code == "attempts_exhausted"
    keys = [event.idempotency_key for event in _events(migrated_session, "release-exhausted-1")]
    assert keys == ["release-exhausted-1:failed"]


def test_a_release_replay_by_another_actor_conflicts(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "release-other-actor")
    _lapsed(migrated_session, unit, EXECUTOR_ACTOR, "release-other-actor")
    assert isinstance(
        release_expired_claim(migrated_session, unit.id, SYSTEM, "release-other-actor-1"), WorkUnit
    )

    conflict = release_expired_claim(
        migrated_session,
        unit.id,
        ActorContext("system-2", ActorRole.SYSTEM),
        "release-other-actor-1",
    )

    assert isinstance(conflict, DomainError) and conflict.code == "idempotency_conflict"


def test_a_release_and_a_reclaim_never_replay_each_other(
    migrated_session: Session, window_open: _Policy
) -> None:
    """Both write `{key}:failed`; each replay predicate must refuse the other's event."""
    rotation = seed_unit(migrated_session, "cross-release")
    _lapsed(migrated_session, rotation, EXECUTOR_ACTOR, "cross-release")
    assert isinstance(
        release_expired_claim(migrated_session, rotation.id, SYSTEM, "cross-1"), WorkUnit
    )
    reclaimed = reclaim_expired_claim(migrated_session, rotation.id, SYSTEM, OTHER, "cross-1")
    assert isinstance(reclaimed, DomainError) and reclaimed.code == "idempotency_conflict"

    other = seed_unit(migrated_session, "cross-reclaim", capability="repo.edit")
    _lapsed(migrated_session, other, OTHER, "cross-reclaim")
    assert isinstance(
        reclaim_expired_claim(
            migrated_session,
            other.id,
            SYSTEM,
            ActorContext("worker-2", ActorRole.WORKER),
            "cross-2",
        ),
        LeaseGrant,
    )
    released = release_expired_claim(migrated_session, other.id, SYSTEM, "cross-2")
    assert isinstance(released, DomainError) and released.code == "idempotency_conflict"


def test_a_release_replay_with_another_expected_version_conflicts(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "release-version")
    _lapsed(migrated_session, unit, EXECUTOR_ACTOR, "release-version")
    version = unit.version
    assert isinstance(
        release_expired_claim(
            migrated_session, unit.id, SYSTEM, "release-version-1", expected_version=version
        ),
        WorkUnit,
    )

    conflict = release_expired_claim(
        migrated_session, unit.id, SYSTEM, "release-version-1", expected_version=version + 1
    )

    assert isinstance(conflict, DomainError) and conflict.code == "idempotency_conflict"


def test_a_release_key_already_used_by_another_operation_conflicts(
    migrated_session: Session, window_open: _Policy
) -> None:
    unit = seed_unit(migrated_session, "release-reused-key")
    grant = _lapsed(migrated_session, unit, EXECUTOR_ACTOR, "release-reused-key")

    # The key the lapsed claim was granted under is an event's bare key.
    conflict = release_expired_claim(migrated_session, unit.id, SYSTEM, "release-reused-key-claim")

    assert isinstance(conflict, DomainError) and conflict.code == "idempotency_conflict"
    assert _claim_row(migrated_session, grant.claim_id).released_at is None


def test_only_the_latest_claim_decides_continuation(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lapse with evidence, then a claim released by failure: the next claim is a start."""
    unit = seed_unit(migrated_session, "continue-latest")
    monkeypatch.setattr(
        "orchestrator.services.lifecycle.rotation_claims.load_factory_policy",
        lambda: _Policy(None),
    )
    _lapse(migrated_session, unit, "continue-latest-a", evidence_by=EXECUTOR)
    _lapse(migrated_session, unit, "continue-latest-b", evidence_by=None, reason="work_unit_failed")
    monkeypatch.setattr(
        "orchestrator.services.lifecycle.rotation_claims.load_factory_policy",
        lambda: _Policy(OUTSIDE_CHANGE_WINDOW),
    )

    refused = _claim(migrated_session, unit, EXECUTOR_ACTOR, "continue-latest-c")

    assert isinstance(refused, DomainError) and refused.code == "outside_change_window"


@pytest.mark.parametrize(
    ("capability", "remedy"),
    [("operational_action", "release its expired claim"), ("repo.edit", "reclaim its expired")],
    ids=["rotation", "other"],
)
def test_a_stalled_unit_names_the_recovery_that_will_accept_it(
    migrated_session: Session, window_open: _Policy, capability: str, remedy: str
) -> None:
    """Reclaim refuses a rotation unit, so its stalled entry must name release instead."""
    unit = seed_unit(migrated_session, f"stalled-{capability}", capability=capability)
    claimant = EXECUTOR_ACTOR if capability == "operational_action" else OTHER
    _lapsed(migrated_session, unit, claimant, f"stalled-{capability}")

    entries = [
        entry
        for entry in pending_decisions(migrated_session, execution_stall_grace_seconds=0)
        if entry["kind"] == "stalled_execution" and entry["href"].endswith(str(unit.id))
    ]

    assert len(entries) == 1 and remedy in entries[0]["decision"]


def test_a_reclaim_that_found_the_budget_spent_replays_its_error(
    migrated_session: Session, window_open: _Policy
) -> None:
    """Both recovery replays rebuild every eligibility code, `budget_exceeded` included."""
    unit = seed_unit(migrated_session, "reclaim-budget", capability="repo.edit", max_llm_calls=1)
    _lapsed(migrated_session, unit, OTHER, "reclaim-budget")
    migrated_session.add(
        Event(
            actor_id="worker-1",
            action="attempt.cost_recorded",
            subject_type="work_unit",
            subject_id=unit.id,
            payload={"cost_known": True, "llm_calls": 1},
            correlation_id=uuid.uuid4(),
            idempotency_key="reclaim-budget-cost",
        )
    )
    migrated_session.commit()
    next_owner = ActorContext("worker-2", ActorRole.WORKER)

    one = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, next_owner, "reclaim-budget-1")
    two = reclaim_expired_claim(migrated_session, unit.id, SYSTEM, next_owner, "reclaim-budget-1")

    assert isinstance(one, DomainError) and one.code == "budget_exceeded"
    assert isinstance(two, DomainError) and two.code == "budget_exceeded"


@pytest.mark.parametrize(
    ("action", "from_state"),
    [("work_unit.transitioned", WorkUnitState.READY), ("claim.renewed", WorkUnitState.CLAIMED)],
    ids=["not_from_a_held_claim", "not_a_transition"],
)
def test_a_release_replay_accepts_only_its_own_kind_of_event(
    migrated_session: Session, window_open: _Policy, action: str, from_state: WorkUnitState
) -> None:
    """A `{key}:failed` event that names this actor but isn't a release's own is a conflict."""
    unit = seed_unit(migrated_session, f"release-foreign-{action}")
    grant = _lapsed(migrated_session, unit, EXECUTOR_ACTOR, f"release-foreign-{action}")
    migrated_session.add(
        Event(
            actor_id="system",
            action=action,
            subject_type="work_unit",
            subject_id=unit.id,
            from_state=from_state,
            to_state=WorkUnitState.FAILED,
            payload={"release_actor_id": "system", "expected_version": None},
            correlation_id=uuid.uuid4(),
            idempotency_key=f"release-foreign-{action}-1:failed",
        )
    )
    migrated_session.commit()

    conflict = release_expired_claim(
        migrated_session, unit.id, SYSTEM, f"release-foreign-{action}-1"
    )

    assert isinstance(conflict, DomainError) and conflict.code == "idempotency_conflict"
    assert _claim_row(migrated_session, grant.claim_id).released_at is None
