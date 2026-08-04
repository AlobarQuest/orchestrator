import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.authority import (
    AuthorityBudgets,
    AuthorityEnvelope,
    authority_fingerprint,
    normalize_authority,
)
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import (
    Approval,
    Event,
    PackageAcceptanceCriterion,
    WorkPackageRevision,
)
from orchestrator.services.packages import (
    record_approval,
    register_approved_unit,
    register_revision,
)

AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
)
NOW = datetime(2026, 7, 5, tzinfo=UTC)
APPROVAL_EVENT_ID = str(uuid.UUID(int=1))


def register_test_revision(
    session: Session, *, acceptance_criteria: tuple[str, ...] = ("ac-1",)
) -> WorkPackageRevision:
    """The canonical single-criterion revision, or a revision declaring several criteria.

    `work_package_revisions` is append-only at the database (`reject_append_only_mutation`), so a
    test that needs more than one declared criterion must say so at registration -- the list cannot
    be widened afterwards. A non-default list gets its own package id and content hash so it is a
    genuinely different revision rather than a conflicting registration of the canonical one.
    """
    suffix = "" if acceptance_criteria == ("ac-1",) else "-" + "-".join(acceptance_criteria)
    return register_revision(
        session,
        package_id=f"pkg-1{suffix}",
        source_repository="owner/repo",
        revision=1,
        content_hash=f"sha256:one{suffix}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=NOW,
        approval_event_id=APPROVAL_EVENT_ID if not suffix else f"{APPROVAL_EVENT_ID}{suffix}",
        enforcement_snapshot={"acceptance_criteria": list(acceptance_criteria)},
        authority=AUTHORITY,
        registry_version=1,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )


def test_revision_registration_is_idempotent_and_normalized(
    migrated_session: Session,
) -> None:
    approval_event_id = str(uuid.uuid4())
    values = {
        "package_id": "pkg-1",
        "source_repository": "owner/repo",
        "revision": 1,
        "content_hash": "sha256:one",
        "source_path": "intent.md",
        "source_commit": "abc123",
        "approved_by": "human-1",
        "approved_at": NOW,
        "approval_event_id": approval_event_id,
        "enforcement_snapshot": {"z": 1, "a": {"later": True}},
        "authority": AUTHORITY,
        "registry_version": 1,
        "actor_id": "human-1",
        "actor_role": ActorRole.HUMAN,
    }

    first = register_revision(migrated_session, **values)
    second = register_revision(migrated_session, **values)

    assert second.id == first.id
    assert list(first.enforcement_snapshot) == ["a", "authority", "z"]
    assert first.authority_fingerprint


def test_conflicting_revision_registration_has_stable_error(
    migrated_session: Session,
) -> None:
    register_test_revision(migrated_session)

    with pytest.raises(DomainError) as error:
        register_revision(
            migrated_session,
            package_id="pkg-1",
            source_repository="owner/repo",
            revision=1,
            content_hash="sha256:different",
            source_path="intent.md",
            source_commit="def456",
            approved_by="human-1",
            approved_at=NOW,
            approval_event_id=str(uuid.uuid4()),
            enforcement_snapshot={},
            authority=AUTHORITY,
            registry_version=1,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
        )

    assert error.value.code == "revision_conflict"


def test_registration_requires_registered_human_actor(migrated_session: Session) -> None:
    with pytest.raises(DomainError) as error:
        register_revision(
            migrated_session,
            package_id="pkg-1",
            source_repository="owner/repo",
            revision=1,
            content_hash="sha256:one",
            source_path="intent.md",
            source_commit="abc123",
            approved_by="human-1",
            approved_at=NOW,
            approval_event_id=str(uuid.uuid4()),
            enforcement_snapshot={},
            authority=AUTHORITY,
            registry_version=1,
            actor_id="worker-1",
            actor_role=ActorRole.WORKER,
        )

    assert error.value.code == "human_actor_required"


def test_approved_unit_registration_only_creates_draft(migrated_session: Session) -> None:
    revision = register_test_revision(migrated_session)

    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-1",
        title="Implement one",
        outcome="One works",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=3,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )

    assert unit.state == "draft"
    assert unit.decomposition_approved_by == "human-1"


def test_approved_unit_registration_defaults_to_three_attempts(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)

    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-default-attempts",
        title="Implement defaults",
        outcome="Defaults work",
        required_capability="repo.edit",
        authority=AUTHORITY,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )

    assert unit.max_attempts == 3


def test_approved_unit_registration_preserves_explicit_attempt_budget(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)

    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-explicit-attempts",
        title="Implement explicit budget",
        outcome="Explicit budget works",
        required_capability="repo.edit",
        authority=AUTHORITY,
        max_attempts=2,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )

    assert unit.max_attempts == 2


def test_approved_unit_registration_idempotency_conflicts_when_raw_authority_differs(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)
    raw_authority = {
        "capabilities": {"repo.edit": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {
            "target_repository": "owner/repo-a",
            "allowed_commands": ["make check"],
        },
    }
    conflicting_raw_authority = {
        "capabilities": {"repo.edit": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "constraints": {
            "target_repository": "owner/repo-b",
            "allowed_commands": ["make check"],
        },
    }

    # Constraint values are covered by the authority fingerprint, so these two envelopes
    # are no longer normalization-identical. Raw-payload replay identity is still what
    # catches differences the fingerprint cannot see — see the explicitly-null test below.
    assert normalize_authority(raw_authority) != normalize_authority(conflicting_raw_authority)

    first = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-raw-authority",
        title="Respect raw authority",
        outcome="Replay identity includes raw authority.",
        required_capability="repo.edit",
        authority=normalize_authority(raw_authority),
        authority_payload=raw_authority,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        idempotency_key="unit-raw-authority",
    )
    replay = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-raw-authority",
        title="Respect raw authority",
        outcome="Replay identity includes raw authority.",
        required_capability="repo.edit",
        authority=normalize_authority(raw_authority),
        authority_payload=raw_authority,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        idempotency_key="unit-raw-authority",
    )

    assert replay.id == first.id

    with pytest.raises(DomainError) as error:
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="unit-raw-authority",
            title="Respect raw authority",
            outcome="Replay identity includes raw authority.",
            required_capability="repo.edit",
            authority=normalize_authority(conflicting_raw_authority),
            authority_payload=conflicting_raw_authority,
            approved_by="human-1",
            approved_at=NOW,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            idempotency_key="unit-raw-authority",
        )

    assert error.value.code == "idempotency_conflict"


def test_approved_unit_registration_conflicts_when_unknown_field_values_differ(
    migrated_session: Session,
) -> None:
    """An envelope the fingerprint cannot honestly cover is refused, not merely distinguished.

    Normalization records unknown fields by *name* only, so two envelopes whose unknown field
    values differ share a fingerprint -- an approved fingerprint would cover an envelope nobody
    approved. Until WS-P2.34 registration ACCEPTED both and relied on comparing the stored raw
    payload to make the second one conflict, which mitigates the hazard one step downstream of
    where it is created. It is now refused at the gate, so the pair below can never both exist.

    That raw-payload comparison remains in `register_approved_unit` and is still LIVE — see
    `test_approved_unit_registration_conflicts_when_a_known_field_is_explicitly_null`, which
    keeps it pinned. An earlier draft of this docstring claimed it "no longer has a reachable
    case of its own"; that was false, and a false unreachability note is exactly the licence a
    later session needs to delete working behaviour.

    The refusal is checked BEFORE idempotent replay, matching `record_approval`'s authority
    check. There is no accepted-then-refused asymmetry to protect: the first registration is
    refused too, so only a unit predating the gate could be replayed, and such a replay is a
    no-op nobody needs.
    """
    revision = register_test_revision(migrated_session)
    raw_authority = {
        "capabilities": {"repo.edit": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "future_field": "original",
    }
    conflicting_raw_authority = {**raw_authority, "future_field": "tampered"}

    assert authority_fingerprint(normalize_authority(raw_authority)) == authority_fingerprint(
        normalize_authority(conflicting_raw_authority)
    )

    for payload in (raw_authority, conflicting_raw_authority):
        with pytest.raises(DomainError) as error:
            register_approved_unit(
                migrated_session,
                revision_id=revision.id,
                unit_key="unit-unknown-field",
                title="Respect raw authority",
                outcome="An unknown field never reaches an approval.",
                required_capability="repo.edit",
                authority=normalize_authority(payload),
                authority_payload=payload,
                approved_by="human-1",
                approved_at=NOW,
                actor_id="human-1",
                actor_role=ActorRole.HUMAN,
                idempotency_key="unit-unknown-field",
            )

        assert error.value.code == "authority_unknown_fields"
        assert "future_field" in error.value.message


def test_authority_approval_idempotency_binds_expected_version(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="approval-version",
        title="Approval version",
        outcome="Approval is exact",
        required_capability="repo.edit",
        authority=AUTHORITY,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    first = record_approval(
        migrated_session,
        unit_id=unit.id,
        subject_type="authority",
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        reason="approved",
        idempotency_key="authority-version",
        expected_version=1,
    )
    migrated_session.commit()

    with pytest.raises(DomainError) as error:
        record_approval(
            migrated_session,
            unit_id=unit.id,
            subject_type="authority",
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            reason="approved",
            idempotency_key="authority-version",
            expected_version=2,
        )

    assert first.subject_revision_or_fingerprint == unit.authority_fingerprint
    assert error.value.code == "idempotency_conflict"


def test_authority_approval_rejects_legacy_invalid_dependency_update_envelope(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="invalid-authority",
        title="Invalid authority",
        outcome="Authority remains unapproved.",
        required_capability="repo.edit",
        authority=AUTHORITY,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit.authority = {
        "capabilities": {"repo.edit": "allowed", "command.run": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
        "change_class": "dependency-update",
        "constraints": {"allowed_commands": ["uv sync --locked"]},
    }
    migrated_session.flush()

    with pytest.raises(DomainError) as error:
        record_approval(
            migrated_session,
            unit_id=unit.id,
            subject_type="authority",
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            reason="approve legacy envelope",
            idempotency_key="invalid-authority-approval",
            expected_version=unit.version,
        )

    assert error.value.code == "authority_mutation_commands_invalid"
    assert migrated_session.scalar(select(func.count()).select_from(Approval)) == 0
    assert migrated_session.scalar(select(func.count()).select_from(Event)) == 0


def test_action_approval_remains_version_bound_without_authority_effect(
    migrated_session: Session,
) -> None:
    revision = register_test_revision(migrated_session)
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="action-approval",
        title="Action approval",
        outcome="Action approval remains unchanged.",
        required_capability="repo.edit",
        authority=AUTHORITY,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )

    approval = record_approval(
        migrated_session,
        unit_id=unit.id,
        subject_type="action",
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        reason="approve action",
        idempotency_key="action-approval",
        expected_version=unit.version,
    )

    assert approval.subject_revision_or_fingerprint == str(unit.version)
    assert unit.authority_approval_id is None


def test_concurrent_identical_first_registration_converges(
    migrated_engine: Engine,
) -> None:
    start = Barrier(2)
    before_registration_lock = Barrier(2)

    def synchronize_registration_lock(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if "pg_advisory_xact_lock" in statement:
            before_registration_lock.wait(timeout=5)

    event.listen(migrated_engine, "before_cursor_execute", synchronize_registration_lock)

    def register() -> uuid.UUID:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            start.wait(timeout=5)
            revision = register_test_revision(session)
            session.commit()
            return revision.id

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(register) for _ in range(2)]
            revision_ids = tuple(future.result(timeout=10) for future in futures)
    finally:
        event.remove(migrated_engine, "before_cursor_execute", synchronize_registration_lock)

    assert revision_ids[0] == revision_ids[1]


def test_concurrent_conflicting_first_registration_returns_stable_error(
    migrated_engine: Engine,
) -> None:
    start = Barrier(2)
    before_registration_lock = Barrier(2)

    def synchronize_registration_lock(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if "pg_advisory_xact_lock" in statement:
            before_registration_lock.wait(timeout=5)

    event.listen(migrated_engine, "before_cursor_execute", synchronize_registration_lock)

    def register(registration: tuple[int, str]) -> str:
        with Session(migrated_engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            start.wait(timeout=5)
            try:
                register_revision(
                    session,
                    package_id="pkg-1",
                    source_repository="owner/repo",
                    revision=registration[0],
                    content_hash=registration[1],
                    source_path="intent.md",
                    source_commit="abc123",
                    approved_by="human-1",
                    approved_at=NOW,
                    approval_event_id=APPROVAL_EVENT_ID,
                    enforcement_snapshot={"acceptance_criteria": ["ac-1"]},
                    authority=AUTHORITY,
                    registry_version=1,
                    actor_id="human-1",
                    actor_role=ActorRole.HUMAN,
                )
                session.commit()
                return "registered"
            except DomainError as error:
                session.rollback()
                return error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(register, registration)
                for registration in ((1, "sha256:one"), (2, "sha256:other"))
            ]
            results = tuple(future.result(timeout=10) for future in futures)
    finally:
        event.remove(migrated_engine, "before_cursor_execute", synchronize_registration_lock)

    assert sorted(results) == ["registered", "revision_conflict"]


CRITERION = {
    "ac_id": "ac-1",
    "condition": "A human confirms the outcome.",
    "evidence_type": "human_review",
    "evidence": "the reviewer's note",
    "approver": "human-1",
}


def register_with_criteria(
    session: Session, criteria: list[dict[str, Any]], *, suffix: str
) -> WorkPackageRevision:
    return register_revision(
        session,
        package_id=f"pkg-declared{suffix}",
        source_repository="owner/repo",
        revision=1,
        content_hash=f"sha256:declared{suffix}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by="human-1",
        approved_at=NOW,
        approval_event_id=f"{APPROVAL_EVENT_ID}-declared{suffix}",
        enforcement_snapshot={"acceptance_criteria": ["ac-1"]},
        authority=AUTHORITY,
        registry_version=1,
        acceptance_criteria=criteria,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )


def test_a_registration_may_declare_what_its_required_criteria_are(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    # The bootstrap lane could always say WHICH ac_ids it requires and never what any of them was.
    # A required ac_id with no criterion behind it is decidable by no actor -- which is why such a
    # unit used to be completable only through the verifier bypass WS-P2.32 shuts.
    revision = register_with_criteria(migrated_session, [dict(CRITERION)], suffix="")
    migrated_session.commit()

    with Session(migrated_engine) as reader:
        rows = tuple(
            reader.scalars(
                select(PackageAcceptanceCriterion).where(
                    PackageAcceptanceCriterion.work_package_revision_id == revision.id
                )
            )
        )
    assert len(rows) == 1
    assert rows[0].ac_id == "ac-1"
    assert rows[0].evidence_type == "human_review"


@pytest.mark.parametrize("field", ["ac_id", "condition", "evidence_type", "evidence", "approver"])
def test_an_empty_criterion_field_is_a_clean_error_not_a_500(
    migrated_session: Session, field: str
) -> None:
    # The table's CHECK would reject each of these. Letting it fire is an unhandled IntegrityError,
    # which this application serves as a bare HTTP 500 -- only DomainError has a handler.
    with pytest.raises(DomainError) as error:
        register_with_criteria(
            migrated_session, [dict(CRITERION) | {field: "   "}], suffix=f"-{field}"
        )

    assert error.value.code == "acceptance_criterion_invalid"


def test_a_criterion_declared_twice_is_a_clean_error_not_a_500(migrated_session: Session) -> None:
    # The UNIQUE on (revision, ac_id) would reject this.
    with pytest.raises(DomainError) as error:
        register_with_criteria(
            migrated_session, [dict(CRITERION), dict(CRITERION)], suffix="-twice"
        )

    assert error.value.code == "acceptance_criterion_invalid"


def test_a_criterion_for_an_undeclared_ac_id_is_refused(migrated_session: Session) -> None:
    # A criterion the completion guard never reads looks like scrutiny and is not: `required_ac_ids`
    # reads the enforcement snapshot, so a criterion outside it is decoration.
    with pytest.raises(DomainError) as error:
        register_with_criteria(
            migrated_session, [dict(CRITERION) | {"ac_id": "ac-9"}], suffix="-undeclared"
        )

    assert error.value.code == "acceptance_criterion_invalid"


def test_declaring_nothing_leaves_the_lane_exactly_as_it_was(
    migrated_session: Session, migrated_engine: Engine
) -> None:
    # The default is None, and None must mean "write no rows" rather than "write empty" -- every
    # existing caller, including the whole package-intake path (which owns these rows itself),
    # relies on that.
    revision = register_test_revision(migrated_session)
    migrated_session.commit()

    with Session(migrated_engine) as reader:
        assert (
            reader.scalar(
                select(func.count())
                .select_from(PackageAcceptanceCriterion)
                .where(PackageAcceptanceCriterion.work_package_revision_id == revision.id)
            )
            == 0
        )


def test_an_unhashable_enforcement_snapshot_is_a_clean_error_not_a_500(
    migrated_session: Session,
) -> None:
    # `enforcement_snapshot` is `dict[str, Any]` straight off the wire. `set(declared)` on a list
    # of dicts raises TypeError, which has no handler -- a bare HTTP 500 from a validator whose
    # whole job is to prevent one. The repo's own named invariant: an unhashable value in a
    # membership test is a 500, not a validation error.
    with pytest.raises(DomainError) as error:
        register_revision(
            migrated_session,
            package_id="pkg-unhashable",
            source_repository="owner/repo",
            revision=1,
            content_hash="sha256:unhashable",
            source_path="intent.md",
            source_commit="abc123",
            approved_by="human-1",
            approved_at=NOW,
            approval_event_id=f"{APPROVAL_EVENT_ID}-unhashable",
            enforcement_snapshot={"acceptance_criteria": [{"ac_id": "ac-1"}, ["ac-2"]]},
            authority=AUTHORITY,
            registry_version=1,
            acceptance_criteria=[dict(CRITERION)],
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
        )

    assert error.value.code == "acceptance_criterion_invalid"


def test_an_unknown_evidence_type_is_refused_by_name(migrated_session: Session) -> None:
    # The OTHER writer of this table (`package_intake._validate_acceptance_criteria`) rejects an
    # unsupported type with this exact code, for the reason its comment gives: an unknown type
    # floors to `human` and is indistinguishable from a typo, so the criterion becomes one the
    # verifier can never resolve -- silently. Two writers of one table must agree on the
    # vocabulary or it drifts.
    with pytest.raises(DomainError) as error:
        register_with_criteria(
            migrated_session,
            [dict(CRITERION) | {"evidence_type": "automated_tests"}],
            suffix="-typo",
        )

    assert error.value.code == "unknown_evidence_type"


def test_declaring_some_required_criteria_but_not_all_is_refused(migrated_session: Session) -> None:
    # A SUBSET is worse than declaring nothing: it recreates the shape this feature exists to
    # eliminate while looking equipped. `load_required_criteria` then refuses the whole revision as
    # incomplete, at verify time, naming neither the missing id nor this registration.
    with pytest.raises(DomainError) as error:
        register_revision(
            migrated_session,
            package_id="pkg-partial",
            source_repository="owner/repo",
            revision=1,
            content_hash="sha256:partial",
            source_path="intent.md",
            source_commit="abc123",
            approved_by="human-1",
            approved_at=NOW,
            approval_event_id=f"{APPROVAL_EVENT_ID}-partial",
            enforcement_snapshot={"acceptance_criteria": ["ac-1", "ac-2"]},
            authority=AUTHORITY,
            registry_version=1,
            acceptance_criteria=[dict(CRITERION)],
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
        )

    assert error.value.code == "acceptance_criterion_invalid"


def test_declaring_an_empty_list_is_refused(migrated_session: Session) -> None:
    # `[]` is a declaration that covers none of the required ids -- the subset rule's floor. Only
    # `None` means "this lane declares nothing", which is what every pre-existing caller passes.
    with pytest.raises(DomainError) as error:
        register_with_criteria(migrated_session, [], suffix="-empty")

    assert error.value.code == "acceptance_criterion_invalid"


def test_a_re_registration_may_restate_its_criteria_but_may_not_change_them(
    migrated_session: Session,
) -> None:
    # The rows a revision is born with are the rows it keeps -- but saying so by returning success
    # and discarding what the caller declared is the wrong way to say it. An idempotent retry
    # still succeeds; a divergent restatement is refused, here rather than at verify time.
    register_with_criteria(migrated_session, [dict(CRITERION)], suffix="-restate")
    migrated_session.commit()

    replay = register_with_criteria(migrated_session, [dict(CRITERION)], suffix="-restate")
    assert isinstance(replay, WorkPackageRevision)

    with pytest.raises(DomainError) as error:
        register_with_criteria(
            migrated_session,
            [dict(CRITERION) | {"condition": "Something else entirely."}],
            suffix="-restate",
        )

    assert error.value.code == "acceptance_criterion_invalid"
    # Both validators use that code, so pin the hint -- only the already-recorded check sets one.
    assert error.value.recovery == "register a new revision"


def test_approved_unit_registration_conflicts_when_a_known_field_is_explicitly_null(
    migrated_session: Session,
) -> None:
    """Raw-payload replay identity, pinned on a shape the field gate admits.

    `normalized()` emits `change_class` and `conformance` unconditionally, so a payload that
    states one of them as null and one that omits it produce the SAME envelope and therefore
    the same fingerprint — while differing as stored bytes. Both carry an empty unknown-field
    set and only declared top-level keys, so both pass `runner_envelope_field_violation`.

    This is what makes the raw-payload comparison in `register_approved_unit` reachable. It
    used to be pinned by the unknown-field test above, which now asserts a refusal instead;
    without this, deleting the comparison outright leaves the suite green.
    """
    revision = register_test_revision(migrated_session)
    omitted = {
        "capabilities": {"repo.edit": "allowed"},
        "budgets": {"max_attempts": 3, "max_llm_calls": 4},
    }
    explicit_null = {**omitted, "change_class": None}

    assert authority_fingerprint(normalize_authority(omitted)) == authority_fingerprint(
        normalize_authority(explicit_null)
    )

    register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-null-known-field",
        title="Respect raw authority",
        outcome="Replay identity sees what the fingerprint cannot.",
        required_capability="repo.edit",
        authority=normalize_authority(omitted),
        authority_payload=omitted,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        idempotency_key="unit-null-known-field",
    )

    with pytest.raises(DomainError) as error:
        register_approved_unit(
            migrated_session,
            revision_id=revision.id,
            unit_key="unit-null-known-field",
            title="Respect raw authority",
            outcome="Replay identity sees what the fingerprint cannot.",
            required_capability="repo.edit",
            authority=normalize_authority(explicit_null),
            authority_payload=explicit_null,
            approved_by="human-1",
            approved_at=NOW,
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            idempotency_key="unit-null-known-field",
        )

    assert error.value.code == "idempotency_conflict"


def test_authority_approval_is_refused_for_an_envelope_the_runner_cannot_parse(
    migrated_session: Session,
) -> None:
    """A human must not bind an approval to an envelope no runner can read.

    An approval is what a person attests, so the envelope rules apply here too and not only at
    authoring time — the units this reaches are the ones authored before the ingress rules
    existed, whose envelopes are write-once. The command rule was already checked here; the
    level and field rules were not, which left the two shapes WS-P2.34 closes approvable.
    """
    revision = register_test_revision(migrated_session)
    unit = register_approved_unit(
        migrated_session,
        revision_id=revision.id,
        unit_key="unit-approval-bad-level",
        title="Approve nothing readable",
        outcome="The envelope names a level the runner refuses.",
        required_capability="repo.edit",
        authority=normalize_authority({"capabilities": {"repo.edit": "allowed"}, "budgets": {}}),
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
        idempotency_key="unit-approval-bad-level",
    )
    # Written directly: ingress refuses this shape, which is the point — the reachable
    # population is envelopes that predate the rule, and there is no way to author one now.
    unit.authority = {**unit.authority, "capabilities": {"repo.edit": "requires_approval"}}
    migrated_session.flush()

    with pytest.raises(DomainError) as error:
        record_approval(
            migrated_session,
            unit_id=unit.id,
            subject_type="authority",
            actor_id="human-1",
            actor_role=ActorRole.HUMAN,
            reason="Approve the envelope.",
            idempotency_key="approval-bad-level",
            expected_version=unit.version,
        )

    assert error.value.code == "unknown_capability_level"
