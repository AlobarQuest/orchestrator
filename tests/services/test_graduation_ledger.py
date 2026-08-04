"""The graduation ledger reports, discounts the approver, and discriminates (WS-P2.18 Inc 8).

Four things are proven here, each in both directions, because each has a way of passing while
being wrong:

* **the approver is discounted (ADR-0014)** -- proven by REWRITING every approver identity and
  asserting the report is unchanged, not by grepping for a column name. A discount asserted in a
  docstring decays silently; one pinned by mutation cannot;
* **the outcome definition discriminates** -- a population that scores identically under it would
  mean it measures nothing, so the same fixture produces all four verdicts;
* **the ledger suppresses nothing and declares nothing** -- the gate's answer, the row census and
  the policy artifact's bytes are all unchanged across a call;
* **comparability groups what one pattern could cover** -- an envelope differing only in change
  class or capabilities is excluded, one differing only in budget or repository is included.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope, normalize_authority
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import (
    Adjudication,
    Approval,
    Event,
    Evidence,
    ReconciliationCondition,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.authority_gate import human_authority_gate
from orchestrator.services.graduation_ledger import (
    ABANDONED,
    CLEAN,
    LEDGER_WINDOW,
    RECOVERED,
    UNFINISHED,
    GraduationLedger,
    graduation_ledger,
)
from orchestrator.services.packages import (
    record_approval,
    register_approved_unit,
    register_revision,
)

NOW = datetime(2026, 8, 2, tzinfo=UTC)
HUMAN = "devon"

# The shape the estate's dependency-update work actually carries. Comparability is decided on the
# change class and the capability map, so these two fields are what the fixtures vary.
# `command.run` and the two command lists are not decoration: `record_approval` refuses a
# `dependency-update` envelope without them, so a fixture missing either cannot clear the gate.
CAPABILITIES: dict[str, str] = {
    "repo.edit": "allowed",
    "repo.read": "allowed",
    "command.run": "allowed",
}
CHANGE_CLASS = "dependency-update"
COMMANDS: dict[str, list[str]] = {
    "allowed_commands": ["uv lock --upgrade", "uv sync --locked"],
    "mutation_commands": ["uv lock --upgrade"],
}

POLICY_ARTIFACT = Path("src/orchestrator/factory-policy.toml")


def envelope(
    *,
    change_class: str | None = CHANGE_CLASS,
    capabilities: dict[str, str] | None = None,
    max_llm_calls: int = 4,
    target_repository: str = "AlobarQuest/orchestrator",
) -> AuthorityEnvelope:
    return AuthorityEnvelope(
        capabilities=dict(CAPABILITIES if capabilities is None else capabilities),
        budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=max_llm_calls),
        change_class=change_class,
        constraints={"target_repository": target_repository, **COMMANDS},
    )


def cleared_unit(
    session: Session,
    key: str,
    *,
    authority: AuthorityEnvelope | None = None,
    state: str = WorkUnitState.COMPLETED,
    approved: bool = True,
    cleared_at: datetime = NOW,
) -> WorkUnit:
    """A unit whose authority gate a human cleared, parked in a terminal state.

    `approved=False` is the discriminating control for "the ledger indexes on a cleared gate": a
    unit with no authority approval must not appear however comparable its envelope is.
    """
    revision = register_revision(
        session,
        package_id=f"pkg-{key}",
        source_repository="AlobarQuest/orchestrator",
        revision=1,
        content_hash=f"sha256:{key}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by=HUMAN,
        approved_at=NOW,
        approval_event_id=str(uuid.uuid4()),
        enforcement_snapshot={"reach": ["source_repository"]},
        authority=envelope(),
        registry_version=1,
        actor_id=HUMAN,
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=authority or envelope(),
        max_attempts=3,
        approved_by=HUMAN,
        approved_at=NOW,
        actor_id=HUMAN,
        actor_role=ActorRole.HUMAN,
    )
    if approved:
        approval = record_approval(
            session,
            unit_id=unit.id,
            subject_type="authority",
            actor_id=HUMAN,
            actor_role=ActorRole.HUMAN,
            reason="cleared",
            idempotency_key=f"authority-{key}",
            expected_version=unit.version,
        )
        approval.created_at = cleared_at
    unit.state = state
    session.flush()
    return unit


def failure(session: Session, unit: WorkUnit, *, reason: str | None) -> None:
    session.add(
        Event(
            actor_id="orchestrator-system",
            action="work_unit.transitioned",
            subject_type="work_unit",
            subject_id=unit.id,
            from_state=WorkUnitState.EXECUTING,
            to_state=WorkUnitState.FAILED,
            payload={"reason": reason},
            correlation_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
        )
    )


def divergence(session: Session, unit: WorkUnit) -> None:
    event = Event(
        actor_id="drift-reconciler",
        action="reconciliation.required",
        subject_type="work_unit",
        subject_id=unit.id,
        payload={},
        correlation_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
    )
    session.add(event)
    session.flush()
    session.add(
        ReconciliationCondition(
            work_unit_id=unit.id,
            condition_type="pr_state_divergence",
            observation_kind="github_pr",
            detected_at=NOW,
            detail="the pull request closed without the unit settling",
            stored_state="open",
            observed_state="closed",
            lineage_hash=f"sha256:{unit.unit_key}",
            normalized_divergence_hash=f"sha256:{unit.unit_key}-divergence",
            event_id=event.id,
            idempotency_key=str(uuid.uuid4()),
        )
    )


def waiver(session: Session, unit: WorkUnit) -> None:
    """A waived criterion, with the failed evidence the schema requires it to name.

    `ck_adjudications_waiver_fields` will not accept a waiver without failed evidence, a risk
    class, a follow-up and an expiry -- a waiver is not a general "accepted with reservations".
    """
    failed = Evidence(
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        attempt=1,
        evidence_type="pytest",
        stable_ref=f"run/{unit.unit_key}",
        source_revision="abc123",
        recorded_by="factory-runner",
        event_id=uuid.uuid4(),
        idempotency_key=f"evidence-{unit.unit_key}",
    )
    session.add(failed)
    session.flush()
    session.add(
        Adjudication(
            work_package_revision_id=unit.work_package_revision_id,
            work_unit_id=unit.id,
            ac_id="ac-1",
            outcome="waived",
            decided_by=HUMAN,
            decided_at=NOW,
            rationale="accepted",
            failed_evidence_id=failed.id,
            risk="low",
            follow_up="tracked",
            scope="unit",
            expires_at=NOW + timedelta(days=30),
            event_id=uuid.uuid4(),
        )
    )


def population(session: Session) -> WorkUnit:
    """One clearing of each verdict, plus three exclusions. Returns the unit being decided on.

    Every entry is comparable to the subject except where it is stated not to be, so an assertion
    about the counts is an assertion about the verdict function rather than about the filter.
    """
    cleared_unit(session, "clean-one")
    cleared_unit(session, "clean-two", authority=envelope(max_llm_calls=9))
    recovered = cleared_unit(session, "recovered")
    failure(session, recovered, reason="lease_expired")
    cleared_unit(session, "abandoned", state=WorkUnitState.CANCELLED)
    cleared_unit(session, "unfinished", state=WorkUnitState.EXECUTING)
    # Three exclusions: a comparable envelope nobody cleared, and two cleared envelopes one
    # pattern could not cover -- a different class and a different capability map.
    cleared_unit(session, "never-cleared", approved=False)
    cleared_unit(session, "other-class", authority=envelope(change_class="software-delivery"))
    cleared_unit(
        session,
        "other-capabilities",
        authority=envelope(
            capabilities={"repo.edit": "allowed", "command.run": "allowed"},
        ),
    )
    return cleared_unit(session, "subject", state=WorkUnitState.DRAFT, approved=False)


def test_the_definition_discriminates_over_a_real_shaped_population(
    migrated_session: Session,
) -> None:
    subject = population(migrated_session)
    migrated_session.commit()

    ledger = graduation_ledger(migrated_session, subject)

    # Not uniform: a measure that scored these five identically would be measuring nothing.
    assert (ledger.total, ledger.clean, ledger.recovered, ledger.abandoned, ledger.unfinished) == (
        5,
        2,
        1,
        1,
        1,
    )
    assert {entry.unit_key for entry in ledger.recent} == {
        "clean-one",
        "clean-two",
        "recovered",
        "abandoned",
        "unfinished",
    }


def test_completion_alone_would_not_have_discriminated(migrated_session: Session) -> None:
    """The rejected definition, run against the same rows, to show what it would have hidden.

    `recovered` and `clean-one` both reach `completed`; only the adverse-signal reading separates
    them. This is the control for the whole §2 decision -- without it, "we chose a better
    definition" is an assertion.
    """
    subject = population(migrated_session)
    migrated_session.commit()

    ledger = graduation_ledger(migrated_session, subject)
    by_key = {entry.unit_key: entry for entry in ledger.recent}

    assert by_key["clean-one"].verdict == CLEAN
    assert by_key["recovered"].verdict == RECOVERED
    assert by_key["clean-one"].failed_attempts == 0
    assert by_key["recovered"].failed_attempts == 1
    assert by_key["abandoned"].verdict == ABANDONED
    assert by_key["unfinished"].verdict == UNFINISHED


def test_every_adverse_signal_moves_a_clearing_off_clean(migrated_session: Session) -> None:
    """Three independent signals, each proven to matter on its own.

    A verdict function reading only one of them would still pass a test that set all three.
    """
    subject = cleared_unit(migrated_session, "subject", state=WorkUnitState.DRAFT, approved=False)
    by_failure = cleared_unit(migrated_session, "by-failure")
    failure(migrated_session, by_failure, reason="coding_action_failed")
    by_divergence = cleared_unit(migrated_session, "by-divergence")
    divergence(migrated_session, by_divergence)
    by_waiver = cleared_unit(migrated_session, "by-waiver")
    waiver(migrated_session, by_waiver)
    control = cleared_unit(migrated_session, "control")
    migrated_session.commit()

    verdicts = {
        entry.unit_key: entry.verdict
        for entry in graduation_ledger(migrated_session, subject).recent
    }

    assert verdicts == {
        "by-failure": RECOVERED,
        "by-divergence": RECOVERED,
        "by-waiver": RECOVERED,
        "control": CLEAN,
    }
    assert control.state == WorkUnitState.COMPLETED  # the control really is a completed unit


def test_recorded_reasons_are_reported_verbatim_and_unclassified(
    migrated_session: Session,
) -> None:
    subject = cleared_unit(migrated_session, "subject", state=WorkUnitState.DRAFT, approved=False)
    unit = cleared_unit(migrated_session, "with-reasons", state=WorkUnitState.CANCELLED)
    failure(migrated_session, unit, reason="obsolete allowed_commands")
    failure(migrated_session, unit, reason=None)
    failure(migrated_session, unit, reason="obsolete allowed_commands")
    migrated_session.commit()

    entry = graduation_ledger(migrated_session, subject).recent[0]

    # Verbatim, deduplicated, and a null reason contributes nothing rather than an empty string.
    assert entry.recorded_reasons == ("obsolete allowed_commands",)


def test_a_novel_shape_says_so_rather_than_reporting_nothing(migrated_session: Session) -> None:
    subject = cleared_unit(migrated_session, "subject", state=WorkUnitState.DRAFT, approved=False)
    cleared_unit(migrated_session, "other-class", authority=envelope(change_class="feature"))
    migrated_session.commit()

    ledger = graduation_ledger(migrated_session, subject)

    assert ledger.total == 0
    assert ledger.recent == ()
    assert ledger.change_class == CHANGE_CLASS  # it still says WHICH shape has no history


def test_the_window_shortens_the_reading_not_the_evidence(migrated_session: Session) -> None:
    subject = cleared_unit(migrated_session, "subject", state=WorkUnitState.DRAFT, approved=False)
    for index in range(LEDGER_WINDOW + 3):
        cleared_unit(
            migrated_session,
            f"cleared-{index:02d}",
            cleared_at=NOW + timedelta(minutes=index),
        )
    migrated_session.commit()

    ledger = graduation_ledger(migrated_session, subject)

    assert ledger.total == LEDGER_WINDOW + 3
    assert ledger.clean == LEDGER_WINDOW + 3  # counted over ALL of them, not over the window
    assert len(ledger.recent) == LEDGER_WINDOW
    assert [entry.unit_key for entry in ledger.recent][0] == f"cleared-{LEDGER_WINDOW + 2:02d}"


def test_budgets_and_repositories_widen_a_pattern_so_they_do_not_split_the_group(
    migrated_session: Session,
) -> None:
    """The two fields a known-good pattern bounds rather than matches, reported not filtered."""
    subject = cleared_unit(migrated_session, "subject", state=WorkUnitState.DRAFT, approved=False)
    cleared_unit(migrated_session, "other-budget", authority=envelope(max_llm_calls=40))
    cleared_unit(
        migrated_session,
        "other-repository",
        authority=envelope(target_repository="AlobarQuest/brain"),
    )
    migrated_session.commit()

    ledger = graduation_ledger(migrated_session, subject)

    assert ledger.total == 2
    assert ledger.repositories == ("AlobarQuest/brain", "AlobarQuest/orchestrator")


def test_the_report_is_unchanged_when_every_approver_identity_is_rewritten(
    migrated_engine: Engine,
) -> None:
    """ADR-0014's discount, pinned by mutation.

    `approved_by` is the only column contamination lives in. If any part of the report were keyed
    on it -- a filter, an ordering, a rendered name -- rewriting every row would move something.
    Read back through a DIFFERENT session, because the ledger's answer must be a fact about the
    database rather than about objects one session happens to hold.
    """
    with Session(migrated_engine) as session:
        subject = population(session)
        subject_id = subject.id
        session.commit()
        before = graduation_ledger(session, subject)

    with Session(migrated_engine) as session:
        for approval in session.scalars(select(Approval)):
            approval.approved_by = "some-other-actor"
        session.commit()

    with Session(migrated_engine) as session:
        rewritten = session.get(WorkUnit, subject_id)
        assert rewritten is not None
        after = graduation_ledger(session, rewritten)
        assert {approval.approved_by for approval in session.scalars(select(Approval))} == {
            "some-other-actor"
        }

    assert after == before


def test_no_approver_identity_appears_anywhere_in_the_report(migrated_session: Session) -> None:
    """The complement of the mutation test: absence, checked over the whole rendered surface."""
    subject = population(migrated_session)
    migrated_session.commit()

    ledger = graduation_ledger(migrated_session, subject)

    assert HUMAN not in repr(ledger)
    assert not hasattr(ledger, "approved_by")
    assert all(not hasattr(entry, "approved_by") for entry in ledger.recent)


def census(session: Session) -> dict[str, int]:
    return {
        table.__tablename__: session.scalar(select(func.count()).select_from(table)) or 0
        for table in (Approval, Event, WorkUnit, Adjudication, ReconciliationCondition)
    }


def unit_revision(session: Session, unit: WorkUnit) -> WorkPackageRevision:
    revision = session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None
    return revision


def test_the_ledger_suppresses_nothing_and_declares_nothing(migrated_engine: Engine) -> None:
    """§3.1, asserted three ways: the gate's answer, the row census, and the artifact's bytes."""
    artifact_before = hashlib.sha256(POLICY_ARTIFACT.read_bytes()).hexdigest()
    with Session(migrated_engine) as session:
        subject_id = population(session).id
        session.commit()

    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, subject_id)
        assert unit is not None
        gate_before = human_authority_gate(unit, unit_revision(session, unit))
        rows_before = census(session)

        graduation_ledger(session, unit)

        gate_after = human_authority_gate(unit, unit_revision(session, unit))

    with Session(migrated_engine) as session:
        rows_after = census(session)

    assert gate_after == gate_before
    assert gate_before.refusals  # the gate really was refusing -- an empty control proves nothing
    assert rows_after == rows_before
    assert hashlib.sha256(POLICY_ARTIFACT.read_bytes()).hexdigest() == artifact_before


def test_the_report_carries_only_reporting_fields() -> None:
    """A closed field set, so a future field that could suppress something fails here first."""
    assert set(GraduationLedger.__dataclass_fields__) == {
        "change_class",
        "capabilities",
        "total",
        "clean",
        "recovered",
        "abandoned",
        "unfinished",
        "repositories",
        "recent",
        "caveat",
    }


def test_the_caveat_states_what_the_population_cannot_support(migrated_session: Session) -> None:
    subject = population(migrated_session)
    migrated_session.commit()

    caveat = graduation_ledger(migrated_session, subject).caveat

    assert "not a rate" in caveat
    assert "ADR-0014" in caveat


def test_comparability_reads_the_normalized_envelope_not_the_stored_json(
    migrated_session: Session,
) -> None:
    """A stored envelope and an authored one that normalize alike must group together.

    `register_approved_unit` stores `normalized()`, which emits every known field explicitly --
    including keys an authored envelope omits. Comparing raw JSON would split the population on
    that difference alone, which is the WS-P2.12-shaped failure of comparing a shape instead of
    its meaning.
    """
    subject = cleared_unit(migrated_session, "subject", state=WorkUnitState.DRAFT, approved=False)
    peer = cleared_unit(migrated_session, "peer")
    migrated_session.commit()

    # The stored JSON carries keys the authored envelope never had — normalized() emits every
    # declared field explicitly, including the ones an author omits — and it is the normalized
    # reading, not those bytes, that the ledger groups on. (It no longer carries
    # `unknown_fields`: WS-P2.34 stores runner_payload(), since the runner's model forbids
    # that key. The property under test is unchanged.)
    assert "conformance" in peer.authority
    assert normalize_authority(peer.authority).capabilities == dict(CAPABILITIES)
    assert graduation_ledger(migrated_session, subject).total == 1
