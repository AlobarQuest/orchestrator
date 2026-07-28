import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from orchestrator.capability_vocabulary import RUNNER_CAPABILITIES
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import authority_fingerprint, normalize_authority
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkPackageRevision, WorkUnit
from orchestrator.services.follow_ups import (
    FOLLOW_UP_CAPABILITY,
    SKIP_ALREADY_MINTED,
    SKIP_NO_COMPLETED_UNIT,
    SKIP_NOT_REQUIRED,
    SKIP_NOT_YET_DUE,
    SKIP_UNITS_IN_FLIGHT,
    SKIP_UNSETTLED_FAILED_UNIT,
    RevisionFacts,
    UnitFacts,
    evaluate_due,
    mint_due_follow_ups,
    validate_follow_up,
)
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.packages import register_approved_unit, register_revision
from tests.services.test_package_registration import AUTHORITY
from tests.services.test_package_registration import NOW as REVISION_NOW

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
WORKER = ActorContext("worker-1", ActorRole.WORKER)
HUMAN = ActorContext("human-1", ActorRole.HUMAN)

VALID = {
    "required": True,
    "revisit_when": "After the next quarterly review.",
    "signals": ["A guard nobody triaged."],
    "owner": "devon",
}


def test_a_valid_declaration_round_trips() -> None:
    assert validate_follow_up(VALID) == VALID


def test_absent_declaration_is_none_not_an_error() -> None:
    assert validate_follow_up(None) is None


def test_the_fully_degenerate_declaration_is_valid() -> None:
    degenerate = {"required": False, "revisit_when": None, "signals": [], "owner": None}

    assert validate_follow_up(degenerate) == degenerate


@pytest.mark.parametrize(
    "value",
    [
        {"required": True, "revisit_when": None, "signals": []},
        {"required": True, "revisit_when": None, "signals": [], "owner": None, "extra": 1},
        {"required": "yes", "revisit_when": None, "signals": [], "owner": None},
        {"required": True, "revisit_when": 7, "signals": [], "owner": None},
        {"required": True, "revisit_when": None, "signals": "not-a-list", "owner": None},
        {"required": True, "revisit_when": None, "signals": [None], "owner": None},
        "not-a-mapping",
    ],
    ids=[
        "missing-key",
        "unknown-key",
        "required-not-bool",
        "revisit-when-not-str",
        "signals-not-list",
        "signal-item-not-str",
        "not-a-mapping",
    ],
)
def test_a_malformed_declaration_is_a_named_domain_error(value: object) -> None:
    with pytest.raises(DomainError) as caught:
        validate_follow_up(value)

    assert caught.value.code == "follow_up_invalid"


NOW = datetime(2026, 9, 1, tzinfo=UTC)
SETTLED = NOW - timedelta(days=40)
REQUIRED = {"required": True, "revisit_when": "Later.", "signals": [], "owner": None}


def facts(*units: UnitFacts, follow_up=REQUIRED) -> RevisionFacts:
    return RevisionFacts(
        revision_id=uuid.uuid4(),
        follow_up=follow_up,
        units=units,
    )


def completed(settled_at=SETTLED) -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="completed", settled_at=settled_at)


def cancelled(settled_at=SETTLED) -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="cancelled", settled_at=settled_at)


def in_flight() -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="executing", settled_at=None)


def failed() -> UnitFacts:
    return UnitFacts(required_capability="repo.edit", state="failed", settled_at=None)


def test_a_settled_revision_past_the_window_is_due() -> None:
    decision = evaluate_due(facts(completed()), now=NOW, due_after_days=30)

    assert decision.skip_reason is None
    assert decision.due_at == SETTLED + timedelta(days=30)


def test_the_anchor_is_the_latest_settling_not_the_earliest() -> None:
    late = NOW - timedelta(days=31)
    decision = evaluate_due(facts(completed(), completed(late)), now=NOW, due_after_days=30)

    assert decision.due_at == late + timedelta(days=30)


def test_a_declaration_that_does_not_require_follow_up_is_skipped() -> None:
    declaration = {"required": False, "revisit_when": None, "signals": [], "owner": None}

    decision = evaluate_due(facts(completed(), follow_up=declaration), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NOT_REQUIRED


def test_a_revision_with_no_declaration_is_skipped() -> None:
    decision = evaluate_due(facts(completed(), follow_up=None), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NOT_REQUIRED


def test_a_revision_with_work_still_moving_is_skipped() -> None:
    decision = evaluate_due(facts(completed(), in_flight()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_UNITS_IN_FLIGHT


def test_a_lingering_failed_unit_blocks_with_its_own_reason() -> None:
    """FAILED is not terminal -- it can go back to READY or on to CANCELLED -- so a revision
    behind one has an undecided outcome. It must NOT read as units_in_flight: 'still working'
    and 'stopped, and nobody decided' are different operator actions."""
    decision = evaluate_due(facts(completed(), failed()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_UNSETTLED_FAILED_UNIT


def test_a_wholly_cancelled_revision_never_mints() -> None:
    """Nothing shipped, so there is no outcome to revisit."""
    decision = evaluate_due(facts(cancelled(), cancelled()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NO_COMPLETED_UNIT


def test_a_revision_with_no_units_at_all_never_mints() -> None:
    decision = evaluate_due(facts(), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NO_COMPLETED_UNIT


def test_a_lone_failed_unit_reports_unsettled_failed_not_no_completed_unit() -> None:
    """FAILED must win over no_completed_unit's own absence-of-completion check -- the clause
    order matters, and this pins it against the more generic reason swallowing the specific
    one."""
    decision = evaluate_due(facts(failed()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_UNSETTLED_FAILED_UNIT


def test_a_lone_in_flight_unit_reports_units_in_flight_not_no_completed_unit() -> None:
    decision = evaluate_due(facts(in_flight()), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_UNITS_IN_FLIGHT


def test_a_revision_inside_the_window_is_not_yet_due() -> None:
    recent = NOW - timedelta(days=5)
    decision = evaluate_due(facts(completed(recent)), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_NOT_YET_DUE
    assert decision.due_at == recent + timedelta(days=30)


def test_zero_days_makes_a_settled_revision_immediately_due() -> None:
    decision = evaluate_due(facts(completed(NOW)), now=NOW, due_after_days=0)

    assert decision.skip_reason is None


def test_an_existing_review_unit_stops_the_revision_from_minting_again() -> None:
    own = UnitFacts(
        required_capability=FOLLOW_UP_CAPABILITY, state="awaiting_review", settled_at=None
    )

    decision = evaluate_due(facts(completed(), own), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_ALREADY_MINTED


def test_a_completed_review_unit_still_stops_a_second_mint() -> None:
    """One review per revision, forever. Completing the review must not make the revision
    eligible again -- which is exactly what a predicate that merely filtered the unit out of
    the settled-set would do."""
    own = UnitFacts(required_capability=FOLLOW_UP_CAPABILITY, state="completed", settled_at=SETTLED)

    decision = evaluate_due(facts(completed(), own), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_ALREADY_MINTED


# ------------------------------------------------------------------------------------------------
# mint_due_follow_ups -- needs the database, so these use `migrated_session`.
# ------------------------------------------------------------------------------------------------

DECLARATION = {
    "required": True,
    "revisit_when": "After the next quarterly review.",
    "signals": ["A guard nobody triaged."],
    "owner": "devon",
}


def _settled_revision(session, key: str, declaration) -> WorkPackageRevision:
    # `work_package_revisions` is append-only (a trigger rejects UPDATE), so the declaration must
    # be supplied at construction -- via `register_revision`'s `follow_up` parameter -- rather than
    # assigned onto an already-registered revision. Each fixture also needs its OWN revision: the
    # shared `tests.services.test_dependencies.register_unit` helper pins package_id="pkg-1" /
    # revision=1 / content_hash="sha256:one" for every caller, so two calls in the same test would
    # resolve to the identical revision row and conflict the moment their `follow_up` values
    # differ. Registering directly, keyed on `key`, keeps the three fixtures independent.
    revision = register_revision(
        session,
        package_id=f"pkg-{key}",
        source_repository="owner/repo",
        revision=1,
        content_hash=f"sha256:{key}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=REVISION_NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["ac-1"]},
        authority=AUTHORITY,
        registry_version=1,
        follow_up=declaration,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=REVISION_NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    for target in (
        WorkUnitState.READY,
        WorkUnitState.CLAIMED,
        WorkUnitState.EXECUTING,
    ):
        unit.state = target
    session.flush()
    # Drive the final hop through the ledger so `to_state="completed"` really exists: the
    # predicate reads settling time from Event.occurred_at, never from updated_at.
    session.add(
        Event(
            subject_type="work_unit",
            subject_id=unit.id,
            action="work_unit.transitioned",
            to_state=WorkUnitState.COMPLETED,
            actor_id="human-1",
            correlation_id=uuid.uuid4(),
            idempotency_key=f"settle:{unit.id}",
            payload={},
        )
    )
    unit.state = WorkUnitState.COMPLETED
    session.flush()
    return revision


@pytest.fixture
def due_revision(migrated_session):
    return _settled_revision(migrated_session, "wsp28-due", DECLARATION)


@pytest.fixture
def degenerate_due_revision(migrated_session):
    return _settled_revision(
        migrated_session,
        "wsp28-degenerate",
        {"required": True, "revisit_when": None, "signals": [], "owner": None},
    )


@pytest.fixture
def malformed_revision(migrated_session):
    return _settled_revision(migrated_session, "wsp28-malformed", {"required": "yes"})


def test_a_due_revision_mints_exactly_one_unit(migrated_session, due_revision) -> None:
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)

    assert len(result.minted) == 1
    migrated_session.expire_all()
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)
    assert unit is not None
    assert unit.state == WorkUnitState.AWAITING_REVIEW
    assert unit.required_capability == "follow_up_review"
    assert unit.authority_approval_id is None
    assert unit.decomposition_approved_by == "system"
    assert unit.max_attempts == 1


def test_the_minted_envelope_carries_no_runner_capability(migrated_session, due_revision) -> None:
    """A minted unit must be structurally unable to reach a runner: no runner capability, no
    target repository, no command list."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    envelope = normalize_authority(unit.authority)
    assert set(envelope.capabilities) & RUNNER_CAPABILITIES == set()
    assert envelope.constraints == {}
    assert envelope.change_class is None


def test_the_stored_envelope_is_a_fixed_point_of_normalisation(
    migrated_session, due_revision
) -> None:
    """Storing a raw dict makes normalized() a non-fixed-point on re-read, and the re-derived
    fingerprint then disagrees with the one that was minted.

    The fingerprint check alone does NOT pin `_mint` calling `.normalized()`: for THIS particular
    envelope (no constraints, no change_class, no conformance, no unknown fields) the raw authored
    dict and its normalized form happen to fingerprint identically, so swapping
    `authority=authority.normalized()` for the raw dict at the call site leaves this assertion
    green. Pinned by verifying it the same way a regression would be caught: made that exact
    substitution in `_mint`, watched `test_the_stored_envelope_matches_the_normalized_shape` (only)
    go red, reverted. The shape assertion below is what actually protects the `.normalized()`
    call; this one documents the fixed-point property, which remains true and worth keeping."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    assert authority_fingerprint(normalize_authority(unit.authority)) == unit.authority_fingerprint


def test_the_stored_envelope_matches_the_normalized_shape(migrated_session, due_revision) -> None:
    """Asserts the stored dict directly, not just its fingerprint. The normalized form always
    carries every `KNOWN_FIELDS` key (`constraints`, `change_class`, `conformance`,
    `unknown_fields`, plus `capabilities`/`budgets`); the raw authored dict `_mint` builds before
    calling `.normalized()` carries only the two it wrote. Storing the raw dict would fail this on
    the missing keys even though (per the test above) it would NOT fail the fingerprint check."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    expected = normalize_authority(
        {
            "capabilities": {FOLLOW_UP_CAPABILITY: "allowed"},
            "budgets": {"max_attempts": 1},
        }
    ).normalized()
    assert unit.authority == expected


def test_running_the_pass_twice_mints_nothing_new(migrated_session, due_revision) -> None:
    first = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    second = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)

    assert len(first.minted) == 1
    assert second.minted == ()
    assert [row.reason for row in second.skipped] == ["already_minted"]
    assert (
        migrated_session.scalar(
            select(func.count())
            .select_from(WorkUnit)
            .where(WorkUnit.required_capability == "follow_up_review")
        )
        == 1
    )


def test_the_declaration_prose_reaches_the_unit(migrated_session, due_revision) -> None:
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    assert "Revisit:" in unit.outcome
    assert "After the next quarterly review." in unit.outcome


def test_a_degenerate_declaration_still_produces_a_legible_unit(
    migrated_session, degenerate_due_revision
) -> None:
    """required:true with everything else null is a VALID declaration. It must not yield an
    empty outcome the reviewer cannot act on."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    assert unit.outcome.strip() != ""
    assert "Signals" not in unit.outcome


def test_one_malformed_declaration_does_not_abort_the_pass(
    migrated_session, due_revision, malformed_revision
) -> None:
    """Per-item fail-open with a counted skip -- the ADR-0002 discipline. A pass that dies on
    item three and discards items one and two reports nothing about either."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)

    assert len(result.minted) == 1
    assert "declaration_malformed" in {row.reason for row in result.skipped}


def _poison_unit_key(session, revision: WorkPackageRevision) -> WorkUnit:
    """Occupies the exact `unit_key` `_mint` would use for this revision (`follow_up_unit_id`'s
    docstring names the `(work_package_revision_id, unit_key)` unique constraint as the backstop
    for this), under a DIFFERENT capability so `evaluate_due`'s already-minted check -- which only
    looks for `required_capability == FOLLOW_UP_CAPABILITY` -- does not short-circuit before
    `_mint` is ever attempted. Settled (CANCELLED) so it does not itself block due-ness, and with
    no settling event of its own, so it contributes nothing to the due-at computation."""
    unit = WorkUnit(
        work_package_revision_id=revision.id,
        unit_key=f"follow-up:{revision.id}",
        title="pre-existing collision",
        outcome="planted to occupy the unit_key _mint would use",
        state=WorkUnitState.CANCELLED,
        decomposition_approved_by="human-1",
        decomposition_approved_at=REVISION_NOW,
        required_capability="repo.edit",
        authority=AUTHORITY.normalized(),
        authority_fingerprint=authority_fingerprint(AUTHORITY),
        max_attempts=1,
    )
    session.add(unit)
    session.flush()
    return unit


def test_a_failure_inside_mint_does_not_abort_units_already_due(
    migrated_session, due_revision, degenerate_due_revision
) -> None:
    """Per-item fail-open must cover more than declaration validation: a revision whose OWN
    `_mint` call raises must not roll back units due on OTHER revisions in the same pass, and
    those units must actually be PERSISTED (not merely reported), not just held in an uncommitted,
    about-to-be-discarded transaction.

    The planted revision is genuinely due -- same declaration and same settled-unit shape as
    `due_revision` -- so it reaches `_mint`, and `_mint`'s own INSERT collides with the
    pre-occupied `unit_key`, raising `IntegrityError` from inside the SAVEPOINT rather than from a
    malformed declaration."""
    poisoned = _settled_revision(migrated_session, "wsp28-poisoned", DECLARATION)
    _poison_unit_key(migrated_session, poisoned)

    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)

    minted_revision_ids = {row.work_package_revision_id for row in result.minted}
    assert minted_revision_ids == {due_revision.id, degenerate_due_revision.id}
    assert SKIP_ALREADY_MINTED in {row.reason for row in result.skipped}

    migrated_session.expire_all()
    for revision_id in (due_revision.id, degenerate_due_revision.id):
        unit = migrated_session.scalar(
            select(WorkUnit).where(
                WorkUnit.work_package_revision_id == revision_id,
                WorkUnit.required_capability == FOLLOW_UP_CAPABILITY,
            )
        )
        assert unit is not None, f"revision {revision_id} was not persisted"


@pytest.mark.parametrize("actor", [WORKER, HUMAN], ids=["worker", "human"])
def test_only_the_system_actor_may_mint(migrated_session, actor: ActorContext) -> None:
    """`_authorize_actor` is the only authorization on a service that mints canonical lifecycle
    state, and every other test in this module drives it exclusively through the module-level
    `SYSTEM` context -- deleting `_authorize_actor` entirely leaves every one of them green."""
    with pytest.raises(DomainError) as caught:
        mint_due_follow_ups(migrated_session, actor=actor, due_after_days=0)

    assert caught.value.code == "role_forbidden"


def test_minting_writes_one_event_per_unit(migrated_session, due_revision) -> None:
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)

    events = migrated_session.scalars(
        select(Event).where(Event.action == "follow_up_unit.created")
    ).all()
    assert len(events) == 1
    assert events[0].subject_id == result.minted[0].work_unit_id
