"""The second half of ADR-0026's join: a revision that names the FACT behind the decision.

The change record says which decision caused this work. The observation says what the decision
was about -- and it is the half the orchestrator can answer for itself, because every branch of
`resolve_anchors` is a local query and this service has no egress. Without it, "what work did
this signal cause?" needs a hop into change-manager that this process cannot make.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, Observation, WorkPackageRevision
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.observations import ObservationCommand, record_observation
from orchestrator.services.package_intake import _command_identity, register_package_intake
from orchestrator.services.packages import register_approved_unit, register_revision
from orchestrator.services.traceability import (
    TraceabilityAnchor,
    resolve_anchors,
    traceability_response,
)
from tests.services.test_package_intake import AUTHORITY, human_actor, intake_command

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
OBSERVED_AT = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _observation(session: Session, *, reference: str = "bump:one") -> Observation:
    """A repository-scoped fact, which is the shape every signal producer in this estate emits.

    `subject_type="repo"` is exactly why this cannot reach a chain through the observation hop,
    which filters on `subject_type="work_unit"` -- the reason the id is carried on the revision.
    """
    result = record_observation(
        session,
        ObservationCommand(
            actor=SYSTEM,
            source_system="github",
            source_reference=reference,
            source_url=None,
            trust_classification="delivery_system",
            subject_type="repo",
            subject_reference="AlobarQuest/intent-packages",
            environment=None,
            observation_type="github_check",
            status="observed",
            severity="info",
            observed_at=OBSERVED_AT,
            summary="A dependency update is available",
            facts={"package": "ruff"},
            payload_digest=None,
            idempotency_key=f"obs-{reference}",
            expected_version=0,
        ),
    )
    assert not isinstance(result, DomainError), result
    return result


def test_the_revision_carries_the_originating_observation(migrated_session: Session) -> None:
    observation = _observation(migrated_session)
    revision = register_package_intake(
        migrated_session,
        intake_command(originating_observation_id=observation.id),
        human_actor(),
    )
    migrated_session.commit()
    assert revision.originating_observation_id == observation.id


def test_a_revision_with_no_originating_observation_carries_none(
    migrated_session: Session,
) -> None:
    """NULL means nobody recorded a cause. Every intake before this column, and every change
    record proposed before the contract, which are never back-filled (ADR-0014)."""
    revision = register_package_intake(migrated_session, intake_command(), human_actor())
    migrated_session.commit()
    assert revision.originating_observation_id is None


def test_it_survives_a_reread_through_a_different_session(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    """A flushed-but-uncommitted row is visible to the session that wrote it, so only a DIFFERENT
    session can tell persistence from a call that returned an object."""
    observation = _observation(migrated_session)
    revision = register_package_intake(
        migrated_session,
        intake_command(originating_observation_id=observation.id),
        human_actor(),
    )
    migrated_session.commit()
    revision_id = revision.id
    with Session(migrated_engine) as reader:
        stored = reader.get(WorkPackageRevision, revision_id)
        assert stored is not None
        assert stored.originating_observation_id == observation.id


def test_an_intake_naming_an_unknown_observation_is_refused(migrated_session: Session) -> None:
    """change-manager cannot verify the observation exists; this database owns the table and can.

    Refused rather than stored, because the contract's order is observe-then-propose: an id that
    resolves to nothing is a dangling cause with no repair, since observations are append-only
    with no supersession model and no delete route.
    """
    with pytest.raises(DomainError) as raised:
        register_package_intake(
            migrated_session,
            intake_command(originating_observation_id=uuid.uuid4()),
            human_actor(),
        )
    assert raised.value.code == "intake_originating_observation_unknown"


def test_the_refusal_is_not_shaped_like_a_missing_route(migrated_session: Session) -> None:
    """`main.py` maps a code ending in `_not_found` to HTTP 404, and a 404 from a POST is
    indistinguishable to a client from a route the deployed image does not serve -- a confusion
    this estate has already paid for. The refusal must reach the wire as a 4xx that says the
    REQUEST named something absent.
    """
    with pytest.raises(DomainError) as raised:
        register_package_intake(
            migrated_session,
            intake_command(originating_observation_id=uuid.uuid4()),
            human_actor(),
        )
    assert not raised.value.code.endswith("_not_found")


def _insert_revision_round_the_service(
    migrated_session: Session,
    package_id: uuid.UUID,
    originating_observation_id: uuid.UUID,
    *,
    revision: int,
    revision_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """A row written by INSERT, which is the only write this table accepts.

    `work_package_revisions` carries the same append-only trigger `evidence` and `events` do, so
    an UPDATE raises `IntegrityConstraintViolation: work_package_revisions is append-only` -- an
    `IntegrityError`, which is what a foreign-key violation raises too. A bypass test written as
    an UPDATE therefore PASSES WITHOUT THE FOREIGN KEY EXISTING, on the trigger's refusal. Its
    control is what found that here.
    """
    inserted = revision_id if revision_id is not None else uuid.uuid4()
    migrated_session.execute(
        insert(WorkPackageRevision).values(
            id=inserted,
            work_package_id=package_id,
            revision=revision,
            content_hash=f"sha256:round-the-service-{revision}",
            source_path="intent.md",
            source_commit="d" * 40,
            approved_by="human-1",
            approved_at=OBSERVED_AT,
            approval_event_id=str(uuid.uuid4()),
            enforcement_snapshot={},
            authority_fingerprint="fp",
            registry_version=1,
            registered_by="human-1",
            intake_source="manual_ws31",
            originating_observation_id=originating_observation_id,
        )
    )
    migrated_session.flush()
    return inserted


def test_a_write_that_bypasses_the_service_is_refused_by_the_database(
    migrated_session: Session,
) -> None:
    """The foreign key, which is the second line under the service refusal rather than a
    substitute for it: an `IntegrityError` has no registered handler and would reach the wire as
    a bare HTTP 500, so a caller must never meet this -- only a path that skipped the service.

    Written round the service deliberately. A service-level test cannot see the constraint at
    all, because the service refuses first.
    """
    seed = register_package_intake(migrated_session, intake_command(), human_actor())
    migrated_session.commit()
    with pytest.raises(IntegrityError):
        _insert_revision_round_the_service(
            migrated_session, seed.work_package_id, uuid.uuid4(), revision=2
        )
    migrated_session.rollback()


def test_a_known_observation_written_round_the_service_is_accepted(
    migrated_session: Session,
) -> None:
    """The control, and it is what makes the test above mean anything.

    Without it that test passes against a column refusing every value -- which is exactly what
    happened when it was written as an UPDATE and the append-only trigger answered instead of
    the constraint under test.
    """
    observation = _observation(migrated_session)
    seed = register_package_intake(migrated_session, intake_command(), human_actor())
    migrated_session.commit()
    _insert_revision_round_the_service(
        migrated_session, seed.work_package_id, observation.id, revision=2
    )
    migrated_session.commit()
    stored = migrated_session.scalar(
        select(WorkPackageRevision).where(WorkPackageRevision.revision == 2)
    )
    assert stored is not None
    assert stored.originating_observation_id == observation.id


def test_two_intakes_differing_only_by_observation_are_not_replays(
    migrated_session: Session,
) -> None:
    """The field is IN the command identity, so a second registration naming a different cause is
    a conflict rather than a silent replay that drops the second cause with nothing said."""
    first = _observation(migrated_session, reference="bump:one")
    second = _observation(migrated_session, reference="bump:two")
    register_package_intake(
        migrated_session,
        intake_command(originating_observation_id=first.id),
        human_actor(),
    )
    migrated_session.commit()
    with pytest.raises(DomainError) as raised:
        register_package_intake(
            migrated_session,
            intake_command(originating_observation_id=second.id),
            human_actor(),
        )
    assert raised.value.code == "idempotency_conflict"


def test_an_identical_intake_still_replays(migrated_session: Session) -> None:
    """The inverse. Without it the test above passes on an identity comparison that has simply
    stopped matching anything."""
    observation = _observation(migrated_session)
    first = register_package_intake(
        migrated_session,
        intake_command(originating_observation_id=observation.id),
        human_actor(),
    )
    migrated_session.commit()
    second = register_package_intake(
        migrated_session,
        intake_command(originating_observation_id=observation.id),
        human_actor(),
    )
    assert first.id == second.id


def _revision_registered_without_the_key(migrated_session: Session) -> WorkPackageRevision:
    """A revision whose stored intake event predates the key, which is EVERY event in production.

    The event is INSERTED rather than edited: `events` is append-only at the database, so an
    UPDATE fails on the trigger rather than on the assertion. The identity is built by the
    production function and then has exactly the one key removed, so the fixture cannot drift
    from what the comparison expects.
    """
    command = intake_command()
    actor = human_actor()
    revision = register_revision(
        migrated_session,
        package_id=command.package_id,
        source_repository=command.source_repository,
        revision=command.revision,
        content_hash=command.content_hash,
        source_path=command.source_path,
        source_commit=command.source_commit,
        approved_by=command.approved_by,
        approved_at=command.approved_at,
        approval_event_id=command.approval_event_id,
        enforcement_snapshot=command.enforcement_snapshot,
        authority=command.authority,
        registry_version=command.registry_version,
        profile=command.profile,
        status_at_intake=command.status_at_intake,
        intake_source="package_cli",
        approval_ledger_commit=command.approval_ledger_commit,
        verification_mode=command.verification_mode,
        verification_limitations=command.verification_limitations,
        actor_id=actor.actor_id,
        actor_role=actor.role,
    )
    migrated_session.flush()
    legacy = dict(_command_identity(command, actor))
    assert "originating_observation_id" in legacy, (
        "the key is not in the identity; the exemption is untested"
    )
    del legacy["originating_observation_id"]
    migrated_session.add(
        Event(
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            actor_id=actor.actor_id,
            action="package_revision.intake_registered",
            subject_type="work_package_revision",
            subject_id=revision.id,
            from_state=None,
            to_state=None,
            payload={"command": legacy},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    migrated_session.commit()
    return revision


def test_an_event_written_before_the_key_existed_still_replays(
    migrated_session: Session,
) -> None:
    """The legacy exemption, driven the way it will actually be met: every stored intake event
    predates this key, so without it the first replay against an existing intake would conflict
    on a field it never had."""
    revision = _revision_registered_without_the_key(migrated_session)
    replayed = register_package_intake(migrated_session, intake_command(), human_actor())
    assert replayed.id == revision.id


def test_a_command_naming_a_cause_does_not_replay_against_a_legacy_event(
    migrated_session: Session,
) -> None:
    """The exemption's FAIL-OPEN direction, which its condition on the COMMAND exists to close.

    Applied unconditionally, the expected identity always lacks the key -- so a stored event that
    predates it compares EQUAL to a command that names an observation, and the registration
    replays: the caller is handed the existing revision, told it succeeded, and the cause it
    supplied is silently dropped.
    """
    _revision_registered_without_the_key(migrated_session)
    observation = _observation(migrated_session)
    with pytest.raises(DomainError) as raised:
        register_package_intake(
            migrated_session,
            intake_command(originating_observation_id=observation.id),
            human_actor(),
        )
    assert raised.value.code == "idempotency_conflict"


def test_a_cause_free_command_does_not_replay_against_an_intake_that_named_one(
    migrated_session: Session,
) -> None:
    """The VALUE dimension: two intakes of one revision naming different causes are two
    registrations, so the one naming none must not be handed the one that named an observation.

    It does NOT exercise the exemption's gate on the observed event, though an earlier version of
    this docstring claimed it did. Here the two identities differ in the key's VALUE, so they are
    unequal whether or not the key is popped -- both forms refuse, and a mutant that drops the
    gate survives this test. The control for the gate itself is the one below.
    """
    observation = _observation(migrated_session)
    register_package_intake(
        migrated_session,
        intake_command(originating_observation_id=observation.id),
        human_actor(),
    )
    migrated_session.commit()
    with pytest.raises(DomainError) as raised:
        register_package_intake(migrated_session, intake_command(), human_actor())
    assert raised.value.code == "idempotency_conflict"


def _revision_carrying_this_key_but_not_an_older_one(
    migrated_session: Session,
) -> WorkPackageRevision:
    """A stored event carrying THIS key while lacking an OLDER one.

    Every event a writer has produced carries a temporal PREFIX of the identity's optional keys --
    `intake_purpose`, `follow_up`, `change_record_id` and this one were added in that order and
    ADR-0014 forbids back-filling -- and for a prefix-shaped event the gate on the observed event
    is satisfied anyway, so it never changes an answer there. The shape built here is the one it
    is load-bearing for, and it is what a back-fill or a partial-identity writer would produce.

    The identity is built by the production function and has exactly the OLDER key removed, so
    the fixture cannot drift from what the comparison expects. The event is INSERTED rather than
    edited, because `events` is append-only at the database.
    """
    command = intake_command()
    actor = human_actor()
    revision = register_revision(
        migrated_session,
        package_id=command.package_id,
        source_repository=command.source_repository,
        revision=command.revision,
        content_hash=command.content_hash,
        source_path=command.source_path,
        source_commit=command.source_commit,
        approved_by=command.approved_by,
        approved_at=command.approved_at,
        approval_event_id=command.approval_event_id,
        enforcement_snapshot=command.enforcement_snapshot,
        authority=command.authority,
        registry_version=command.registry_version,
        profile=command.profile,
        status_at_intake=command.status_at_intake,
        intake_source="package_cli",
        approval_ledger_commit=command.approval_ledger_commit,
        verification_mode=command.verification_mode,
        verification_limitations=command.verification_limitations,
        actor_id=actor.actor_id,
        actor_role=actor.role,
    )
    migrated_session.flush()
    legacy = dict(_command_identity(command, actor))
    assert "originating_observation_id" in legacy, (
        "the key is not in the identity; the gate under test is unreachable"
    )
    assert "follow_up" in legacy, "the older key is not in the identity; the shape is not built"
    del legacy["follow_up"]
    migrated_session.add(
        Event(
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            actor_id=actor.actor_id,
            action="package_revision.intake_registered",
            subject_type="work_package_revision",
            subject_id=revision.id,
            from_state=None,
            to_state=None,
            payload={"command": legacy},
            correlation_id=uuid.uuid4(),
            idempotency_key=command.idempotency_key,
        )
    )
    migrated_session.commit()
    return revision


def test_the_exemption_is_withheld_when_the_stored_event_carries_the_key(
    migrated_session: Session,
) -> None:
    """The gate on the OBSERVED event, driven by the one input that can tell it apart.

    Popped on the command alone, `legacy` loses a key `observed` still carries, the two differ by
    a key rather than by a value, and this ordinary cause-free replay is refused as a conflict.
    The gate withholds the pop precisely because the stored event has the key.

    The sibling clause for the OLDER key still pops, which is what makes `observed != expected`
    and so brings the comparison into play at all -- the caller consults it only then.
    """
    revision = _revision_carrying_this_key_but_not_an_older_one(migrated_session)
    replayed = register_package_intake(migrated_session, intake_command(), human_actor())
    assert replayed.id == revision.id


def _unit_on_a_revision_caused_by(
    migrated_session: Session,
    originating_observation_id: uuid.UUID | None,
    *,
    key: str = "a",
):
    """A revision carrying (or not carrying) an originating fact, and a unit on it.

    Registered through the WS-3.1 bootstrap lane, which is the fixture shape every traceability
    test in this repository uses: a `package_cli` revision requires an approved breakdown before
    it can carry a unit, and building one here would exercise a different service than the hop
    under test. The hop reads the column whatever lane wrote it, and the intake half of the join
    is asserted by the tests above.
    """
    revision = register_revision(
        migrated_session,
        package_id=f"pkg-obs-{key}",
        source_repository="AlobarQuest/orchestrator",
        revision=1,
        content_hash=f"sha256:obs-{key}",
        source_path="intent.md",
        source_commit="c" * 40,
        approved_by="human-1",
        approved_at=OBSERVED_AT,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"acceptance_criteria": ["AC-001"]},
        authority=AUTHORITY,
        registry_version=1,
        originating_observation_id=originating_observation_id,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key=f"unit-obs-{key}",
        title="Do the work",
        outcome="It is done",
        required_capability="repo.edit",
        authority=AUTHORITY,
        approved_by="human-1",
        approved_at=OBSERVED_AT,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    migrated_session.commit()
    return unit


def test_the_intent_hop_names_the_originating_observation(migrated_session: Session) -> None:
    """The chain must be able to USE the join, not merely store it. The observation hop could
    never carry this: it filters on `subject_type="work_unit"`, and a signal is about a repo."""
    observation = _observation(migrated_session)
    unit = _unit_on_a_revision_caused_by(migrated_session, observation.id)
    response = traceability_response(
        migrated_session, TraceabilityAnchor(kind="work_unit", work_unit_id=unit.id)
    )
    assert response.chains[0].intent.originating_observation_id == observation.id


def test_a_chain_for_a_revision_with_no_originating_observation_says_so(
    migrated_session: Session,
) -> None:
    """The control: the hop must report absence rather than always reporting the value."""
    unit = _unit_on_a_revision_caused_by(migrated_session, None)
    response = traceability_response(
        migrated_session, TraceabilityAnchor(kind="work_unit", work_unit_id=unit.id)
    )
    assert response.chains[0].intent.originating_observation_id is None


def test_the_observation_anchor_resolves_to_the_work_the_signal_caused(
    migrated_session: Session,
) -> None:
    observation = _observation(migrated_session)
    unit = _unit_on_a_revision_caused_by(migrated_session, observation.id)
    anchor = TraceabilityAnchor(kind="observation", observation_id=observation.id)
    assert resolve_anchors(migrated_session, anchor) == (unit.id,)


def test_the_observation_anchor_resolves_only_the_work_that_names_it(
    migrated_session: Session,
) -> None:
    """The discriminator. Without a second revision carrying nothing, a branch that ignored the
    filter entirely would answer identically."""
    observation = _observation(migrated_session)
    named = _unit_on_a_revision_caused_by(migrated_session, observation.id, key="named")
    _unit_on_a_revision_caused_by(migrated_session, None, key="unnamed")
    anchor = TraceabilityAnchor(kind="observation", observation_id=observation.id)
    assert resolve_anchors(migrated_session, anchor) == (named.id,)


def test_an_observation_that_caused_nothing_yet_resolves_empty(
    migrated_session: Session,
) -> None:
    """An ordinary answer, and the state of every observation nobody has acted on -- which is why
    the existence check below has to separate it from a caller error."""
    observation = _observation(migrated_session)
    anchor = TraceabilityAnchor(kind="observation", observation_id=observation.id)
    assert resolve_anchors(migrated_session, anchor) == ()


def test_an_unknown_observation_anchor_is_refused(migrated_session: Session) -> None:
    """`observation_not_found` the way the `work_unit` sibling answers, and unlike the intake
    refusal this one IS a lookup, so the 404 mapping is correct for it."""
    anchor = TraceabilityAnchor(kind="observation", observation_id=uuid.uuid4())
    with pytest.raises(DomainError) as raised:
        resolve_anchors(migrated_session, anchor)
    assert raised.value.code == "observation_not_found"


def test_the_anchor_orders_revisions_of_one_package_by_revision(
    migrated_session: Session,
) -> None:
    """Two revisions of ONE package under one observation, ordered by REVISION rather than by
    the primary key -- which the clause's own comment claimed while the code did the opposite.

    THE IDS ARE CONSTRUCTED, and that is what makes this a control rather than a coin toss.
    `UUIDPrimaryKey.id` defaults to `uuid4`, so an id-first order agrees with revision order
    about half the time: built on `register_revision` this test would pass under the defect on
    roughly every other run, and a flaky kill is worth less than no kill at all. Inverting the
    two orders deliberately means an id-first clause is wrong EVERY run.
    """
    observation = _observation(migrated_session)
    seed = register_package_intake(migrated_session, intake_command(), human_actor())
    migrated_session.commit()
    earlier = _insert_revision_round_the_service(
        migrated_session,
        seed.work_package_id,
        observation.id,
        revision=2,
        revision_id=uuid.UUID(int=(1 << 128) - 1),
    )
    later = _insert_revision_round_the_service(
        migrated_session,
        seed.work_package_id,
        observation.id,
        revision=3,
        revision_id=uuid.UUID(int=1),
    )
    migrated_session.commit()
    units = tuple(
        register_approved_unit(
            migrated_session,
            revision_id=revision_id,
            unit_key="unit-ordering",
            title="Do the work",
            outcome="It is done",
            required_capability="repo.edit",
            authority=AUTHORITY,
            approved_by="human-1",
            approved_at=OBSERVED_AT,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
        )
        for revision_id in (earlier, later)
    )
    migrated_session.commit()

    anchor = TraceabilityAnchor(kind="observation", observation_id=observation.id)
    assert resolve_anchors(migrated_session, anchor) == (units[0].id, units[1].id)


def test_the_anchor_reports_which_subject_it_matched(migrated_session: Session) -> None:
    """`display_value` is a dict keyed by anchor kind, so a member added to the dataclass and
    forgotten there raises `KeyError` rather than answering."""
    observation = _observation(migrated_session)
    _unit_on_a_revision_caused_by(migrated_session, observation.id)
    response = traceability_response(
        migrated_session, TraceabilityAnchor(kind="observation", observation_id=observation.id)
    )
    assert response.anchor.matched_on == "observation"
    assert response.anchor.value == str(observation.id)
