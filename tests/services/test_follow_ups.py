import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from orchestrator.capability_vocabulary import RUNNER_CAPABILITIES
from orchestrator.errors import DomainError
from orchestrator.kernel.authority import authority_fingerprint, normalize_authority
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Event, WorkPackageRevision, WorkUnit
from orchestrator.services.evidence import record_adjudication
from orchestrator.services.follow_ups import (
    _DEFAULT_REVISIT,
    FOLLOW_UP_CAPABILITY,
    SKIP_ALREADY_MINTED,
    SKIP_NO_COMPLETED_UNIT,
    SKIP_NOT_REQUIRED,
    SKIP_NOT_YET_DUE,
    SKIP_REACH_UNDECLARED,
    SKIP_UNITS_IN_FLIGHT,
    SKIP_UNSETTLED_FAILED_UNIT,
    RevisionFacts,
    UnitFacts,
    evaluate_due,
    mint_due_follow_ups,
    validate_follow_up,
)
from orchestrator.services.lifecycle import ActorContext, follow_up_unit_id, required_ac_ids
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.verifier_criteria import (
    _FOLLOW_UP_DEFAULT_REVISIT,
    load_required_criteria,
)
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


def facts(
    *units: UnitFacts, follow_up=REQUIRED, revision_id=None, reach=("source_repository",)
) -> RevisionFacts:
    # Reach is DECLARED by default because a package authored today declares it (WS-P2.18
    # Increment 4): minting refuses without one, so a helper that omitted it would turn every
    # due-ness test below into a test of that refusal. Pass `reach=None` for the undeclared case.
    return RevisionFacts(
        revision_id=revision_id or uuid.uuid4(),
        follow_up=follow_up,
        units=units,
        reach=reach,
    )


def _ordinary(state: str, settled_at) -> UnitFacts:
    return UnitFacts(
        unit_id=uuid.uuid4(),
        required_capability="repo.edit",
        state=state,
        settled_at=settled_at,
    )


def completed(settled_at=SETTLED) -> UnitFacts:
    return _ordinary("completed", settled_at)


def cancelled(settled_at=SETTLED) -> UnitFacts:
    return _ordinary("cancelled", settled_at)


def in_flight() -> UnitFacts:
    return _ordinary("executing", None)


def failed() -> UnitFacts:
    return _ordinary("failed", None)


def minted(revision_id: uuid.UUID, *, state: str, settled_at=None) -> UnitFacts:
    """The unit the pass itself would create for `revision_id` -- capability AND derived id."""
    return UnitFacts(
        unit_id=follow_up_unit_id(revision_id),
        required_capability=FOLLOW_UP_CAPABILITY,
        state=state,
        settled_at=settled_at,
    )


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


def test_a_revision_whose_package_declared_no_reach_refuses_to_mint() -> None:
    """WS-P2.18 Increment 4. Minting SUPPLIES reach; it never inherits an unknown one.

    A minted unit hangs off the same revision, so the revision's declaration IS the minted unit's
    reach -- there is no second place to put one. Admitting it on "nobody said what this touches"
    would reopen through the back door the gap the admission term just closed, and would reopen it
    for every revision that has ever settled.
    """
    decision = evaluate_due(facts(completed(), reach=None), now=NOW, due_after_days=30)

    assert decision.skip_reason == SKIP_REACH_UNDECLARED


def test_the_same_revision_with_a_declared_reach_is_due_the_control() -> None:
    # The control: everything else about these two revisions is identical, so the refusal above is
    # the missing declaration and nothing else.
    decision = evaluate_due(facts(completed()), now=NOW, due_after_days=30)

    assert decision.skip_reason is None


def test_an_undeclared_reach_refuses_before_the_window_is_even_considered() -> None:
    # Reported the moment it is asked rather than when it comes due: "this can never mint until
    # reach is supplied" is a different operator action from "come back in a fortnight", and the
    # first is the true one.
    inside_the_window = evaluate_due(facts(completed(NOW), reach=None), now=NOW, due_after_days=30)

    assert inside_the_window.skip_reason == SKIP_REACH_UNDECLARED


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
    revision_id = uuid.uuid4()
    own = minted(revision_id, state="awaiting_review")

    decision = evaluate_due(
        facts(completed(), own, revision_id=revision_id), now=NOW, due_after_days=30
    )

    assert decision.skip_reason == SKIP_ALREADY_MINTED


def test_a_completed_review_unit_still_stops_a_second_mint() -> None:
    """One review per revision, forever. Completing the review must not make the revision
    eligible again -- which is exactly what a predicate that merely filtered the unit out of
    the settled-set would do."""
    revision_id = uuid.uuid4()
    own = minted(revision_id, state="completed", settled_at=SETTLED)

    decision = evaluate_due(
        facts(completed(), own, revision_id=revision_id), now=NOW, due_after_days=30
    )

    assert decision.skip_reason == SKIP_ALREADY_MINTED


def test_a_unit_that_merely_claims_the_capability_does_not_block_a_genuine_mint() -> None:
    """`follow_up_review` was, briefly, a capability an author could put on any unit -- and the
    already-minted check matched on it alone. A revision carrying such a unit reported
    `already_minted` forever and its genuine declaration could never mint. The derived id is what
    identifies the row this pass would create; claiming the capability is not being that row."""
    imposter = UnitFacts(
        unit_id=uuid.uuid4(),
        required_capability=FOLLOW_UP_CAPABILITY,
        state="completed",
        settled_at=SETTLED,
    )

    decision = evaluate_due(facts(completed(), imposter), now=NOW, due_after_days=30)

    assert decision.skip_reason is None


# ------------------------------------------------------------------------------------------------
# mint_due_follow_ups -- needs the database, so these use `migrated_session`.
# ------------------------------------------------------------------------------------------------

DECLARATION = {
    "required": True,
    "revisit_when": "After the next quarterly review.",
    "signals": ["A guard nobody triaged."],
    "owner": "devon",
}


def _settled_revision(
    session,
    key: str,
    declaration,
    *,
    snapshot_title: str | None = None,
    reach: tuple[str, ...] | None = ("source_repository",),
) -> WorkPackageRevision:
    # `work_package_revisions` is append-only (a trigger rejects UPDATE), so the declaration must
    # be supplied at construction -- via `register_revision`'s `follow_up` parameter -- rather than
    # assigned onto an already-registered revision. Each fixture also needs its OWN revision: the
    # shared `tests.services.test_dependencies.register_unit` helper pins package_id="pkg-1" /
    # revision=1 / content_hash="sha256:one" for every caller, so two calls in the same test would
    # resolve to the identical revision row and conflict the moment their `follow_up` values
    # differ. Registering directly, keyed on `key`, keeps the three fixtures independent.
    snapshot: dict[str, object] = {"acceptance_criteria": ["ac-1"]}
    if reach is not None:
        snapshot["reach"] = list(reach)
    if snapshot_title is not None:
        snapshot["title"] = snapshot_title
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
        enforcement_snapshot=snapshot,
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


PACKAGE_TITLE = "Retire the legacy intake path"


@pytest.fixture
def due_revision(migrated_session):
    return _settled_revision(
        migrated_session, "wsp28-due", DECLARATION, snapshot_title=PACKAGE_TITLE
    )


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
    assert unit.id == follow_up_unit_id(due_revision.id)


def test_the_title_names_the_revision_it_revisits(migrated_session, due_revision) -> None:
    """Spec 6. Several review units can be outstanding at once and they share one review queue,
    so a constant title renders rows a reviewer cannot tell apart."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    assert unit.title == f"Follow-up review: {PACKAGE_TITLE}"


def test_a_snapshot_without_a_title_falls_back_to_the_bare_constant(
    migrated_session, degenerate_due_revision
) -> None:
    """`enforcement_snapshot` is caller-supplied, so `title` is not guaranteed. The fallback must
    be the bare constant, never a dangling separator."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    assert unit.title == "Follow-up review"


def test_a_blank_snapshot_title_falls_back_too(migrated_session) -> None:
    _settled_revision(migrated_session, "wsp28-blank-title", DECLARATION, snapshot_title="   ")

    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    assert unit.title == "Follow-up review"


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
    empty outcome the reviewer cannot act on.

    Asserted as EQUALITY against the exact default prose, not merely as non-empty: a non-empty
    check passes for the literal string "None", which is what `str(None)` produces and is exactly
    the value a reviewer cannot act on. The equality also pins the absent Signals/Owner sections
    without a separate substring check."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    assert unit.outcome == f"Revisit: {_DEFAULT_REVISIT}"


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
    for this), under a DIFFERENT capability so `evaluate_due`'s already-minted check -- which now
    requires BOTH `required_capability == FOLLOW_UP_CAPABILITY` and the derived `uuid5` id -- does
    not short-circuit before `_mint` is ever attempted. Settled (CANCELLED) so it does not itself
    block due-ness, and with no settling event of its own, so it contributes nothing to the
    due-at computation."""
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


# ------------------------------------------------------------------------------------------------
# The generated review criterion and its human-owned adjudication carve-out.
# ------------------------------------------------------------------------------------------------


def test_a_review_unit_requires_exactly_one_generated_criterion(
    migrated_session, due_revision
) -> None:
    """Without a generated branch this falls through to the revision's FULL package AC set and
    the human is asked to re-adjudicate the entire original package."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)

    criteria = load_required_criteria(migrated_session, unit, revision)

    assert [criterion.ac_id for criterion in criteria] == ["follow-up-review"]
    assert criteria[0].evidence_type == "observation"
    assert criteria[0].condition == (
        "The follow-up questions declared by the package were answered."
    )
    # EQUALITY, not merely non-empty: this is the only place the package author's declared prose
    # reaches the criterion a human reads. A non-empty check stays green if the generator ignored
    # `revisit_when` entirely and always emitted the default.
    assert criteria[0].evidence == DECLARATION["revisit_when"]
    assert criteria[0].approver == "devon"


def test_a_degenerate_declaration_still_yields_a_non_empty_criterion(
    migrated_session, degenerate_due_revision
) -> None:
    """approver falls back to revision.approved_by, which is NOT NULL and CHECK-non-empty.
    evidence falls back to the exact default-revisit prose, not merely to something non-empty --
    a bare non-empty check passes for the literal string "None", which is what `str(None)`
    produces and is exactly the kind of value a reviewer cannot act on."""
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)

    criteria = load_required_criteria(migrated_session, unit, revision)

    assert criteria[0].approver == revision.approved_by
    assert criteria[0].evidence == _FOLLOW_UP_DEFAULT_REVISIT


def test_a_human_may_adjudicate_the_generated_follow_up_criterion(
    migrated_session, due_revision
) -> None:
    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)

    adjudication = record_adjudication(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="follow-up-review",
        outcome="passed",
        actor=HUMAN,
        rationale="Reviewed; the outcome still holds.",
        idempotency_key="follow-up-adjudication-1",
    )

    assert not isinstance(adjudication, DomainError)
    assert adjudication.outcome == "passed"


# ------------------------------------------------------------------------------------------------
# The marker is the DERIVED UNIT ID, not the capability. `required_capability` is a field a unit
# author supplies; if claiming `follow_up_review` were enough, an ordinary unit would inherit the
# single generated criterion IN PLACE OF its package's real acceptance criteria, and one human
# "passed" against a criterion the package never wrote would complete it. `consistency` computes
# the required set with the same `required_ac_ids`, so it agrees with such a bypass by
# construction and reports nothing.
# ------------------------------------------------------------------------------------------------


def _imposter_unit(session, revision: WorkPackageRevision) -> WorkUnit:
    """An ordinary unit that CLAIMS the follow-up capability, planted directly through the ORM.

    Deliberately not through `register_approved_unit`: unit ingress no longer accepts the
    capability at all (pinned below), so the model layer is the only way to build the row this
    predicate must refuse. That is the honest test -- the identity check must hold even if a row
    with the capability exists by some route ingress does not control.
    """
    unit = WorkUnit(
        work_package_revision_id=revision.id,
        unit_key="wsp28-imposter",
        title="an ordinary unit wearing the follow-up marker",
        outcome="unrelated work",
        state=WorkUnitState.DRAFT,
        required_capability=FOLLOW_UP_CAPABILITY,
        authority=AUTHORITY.normalized(),
        authority_fingerprint=authority_fingerprint(AUTHORITY),
        max_attempts=1,
    )
    session.add(unit)
    session.flush()
    assert unit.id != follow_up_unit_id(revision.id)
    return unit


def _two_ac_revision(session, key: str) -> WorkPackageRevision:
    return register_revision(
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
        enforcement_snapshot={"acceptance_criteria": ["AC-001", "AC-002"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )


def test_a_capability_claiming_unit_still_owes_its_package_acceptance_criteria(
    migrated_session,
) -> None:
    revision = _two_ac_revision(migrated_session, "wsp28-imposter-acs")
    unit = _imposter_unit(migrated_session, revision)

    assert required_ac_ids(migrated_session, revision, unit) == ("AC-001", "AC-002")


def test_a_capability_claiming_unit_is_not_offered_the_generated_criterion(
    migrated_session,
) -> None:
    """`load_required_criteria` is the other half: substituting the one generated criterion for the
    package's set is what the human would then be shown in `/review`."""
    revision = _two_ac_revision(migrated_session, "wsp28-imposter-criteria")
    unit = _imposter_unit(migrated_session, revision)

    with pytest.raises(DomainError) as caught:
        load_required_criteria(migrated_session, unit, revision)

    # It falls through to the package's own AC set, which has no persisted criterion rows here.
    assert caught.value.code == "verification_subject_invalid"


def test_a_human_may_not_adjudicate_the_generated_ac_against_a_capability_claiming_unit(
    migrated_session,
) -> None:
    revision = _two_ac_revision(migrated_session, "wsp28-imposter-adjudication")
    unit = _imposter_unit(migrated_session, revision)

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=revision.id,
        work_unit_id=unit.id,
        ac_id="follow-up-review",
        outcome="passed",
        actor=HUMAN,
        rationale="claiming the marker is not being the unit",
        idempotency_key="wsp28-imposter-adjudication-1",
    )

    assert isinstance(result, DomainError)
    assert result.code == "evidence_subject_invalid"


def test_unit_ingress_no_longer_accepts_the_follow_up_capability(migrated_session) -> None:
    """The second lock. `_mint` constructs its unit directly and never calls
    `validate_unit_capabilities`, so the feature never needed the capability in the ingress
    vocabulary -- and listing it is what let an author put the marker on a unit at all."""
    revision = _two_ac_revision(migrated_session, "wsp28-imposter-ingress")

    with pytest.raises(DomainError) as caught:
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="wsp28-ingress-imposter",
            title="an ordinary unit wearing the follow-up marker",
            outcome="unrelated work",
            required_capability=FOLLOW_UP_CAPABILITY,
            authority=AUTHORITY,
            max_attempts=3,
            approved_by="human-1",
            approved_at=REVISION_NOW,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
        )

    assert caught.value.code == "unknown_capability"


def test_a_colliding_package_ac_id_does_not_inherit_the_follow_up_carve_out(
    migrated_session,
) -> None:
    """`_is_generated_follow_up_subject` -- both in `_validated_subject` and in
    `_criterion_evidence_type`'s fallback -- must key on the UNIT's capability, not the ac_id
    alone. `_validated_subject` has a second, capability-blind admission path: any unit on a
    revision whose PACKAGE-declared `enforcement_snapshot["acceptance_criteria"]` happens to list
    the literal string "follow-up-review" passes that check regardless of capability. If the
    evidence-type fallback trusted `ac_id == FOLLOW_UP_AC_ID` alone, that ordinary unit would
    silently inherit the generated criterion's `observation` evidence type and a HUMAN could
    record `passed` on it -- an authorization widening, not merely a wrong id. This is a
    package-declared AC with no persisted `PackageAcceptanceCriterion` row, so this is exactly
    the "role_forbidden" a human hitting an ordinary judgment-less AC would get."""
    revision = register_revision(
        migrated_session,
        package_id="pkg-wsp28-collision",
        source_repository="owner/repo",
        revision=1,
        content_hash="sha256:wsp28-collision",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=REVISION_NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["follow-up-review"]},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="wsp28-collision-unit",
        title="an ordinary unit, not a follow-up review",
        outcome="unrelated work",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=REVISION_NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=revision.id,
        work_unit_id=unit.id,
        ac_id="follow-up-review",
        outcome="passed",
        actor=HUMAN,
        rationale="wrong lane entirely",
        idempotency_key="wsp28-collision-adjudication",
    )

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_the_release_observation_criteria_still_refuse_public_adjudication(
    migrated_session,
) -> None:
    """The counterpart carve-out points the OTHER way and must stay that way. Without this,
    a future reader assumes the two generated-AC rules match and loosens the wrong one."""
    from orchestrator.persistence.models import DeploymentObservation
    from orchestrator.services.deployment_observations import record_deployment_observation
    from tests.services.test_deployment_observations import observation_command, release_binding

    _unit, binding = release_binding(migrated_session, key="wsp28-asymmetry")
    observation = record_deployment_observation(
        migrated_session, observation_command(binding, key="wsp28-asymmetry-observation")
    )
    assert isinstance(observation, DeploymentObservation)

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=observation.work_package_revision_id,
        work_unit_id=observation.post_deploy_work_unit_id,
        ac_id="post-deploy-artifact",
        outcome="passed",
        actor=HUMAN,
        rationale="attempting the wrong lane",
        idempotency_key="wsp28-asymmetry-adjudication",
    )

    assert isinstance(result, DomainError)
    assert result.code == "post_deploy_verifier_required"


def test_the_review_form_offers_a_human_outcome_for_the_review_unit(
    migrated_session, due_revision
) -> None:
    """web._adjudicatable_criteria filters POST_DEPLOY_AC_IDS. The follow-up id is not in that
    tuple, so it renders -- but that is a property worth pinning, not assuming."""
    from orchestrator.web import _adjudicatable_criteria

    result = mint_due_follow_ups(migrated_session, actor=SYSTEM, due_after_days=0)
    unit = migrated_session.get(WorkUnit, result.minted[0].work_unit_id)
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)

    rows = _adjudicatable_criteria(migrated_session, unit, revision)

    assert [row["ac_id"] for row in rows] == ["follow-up-review"]
    # `is_judgment` until WS-P2.17 Inc 2 renamed it: the flag no longer reports what KIND of
    # criterion this is, but whether a human may decide THIS one, now. The value is unchanged and
    # state-independent -- the follow-up's `observation` evidence type has a HUMAN floor, so it is
    # admitted by clause (a) and never depends on the unit reaching awaiting_review.
    assert rows[0]["human_may_decide"] is True
