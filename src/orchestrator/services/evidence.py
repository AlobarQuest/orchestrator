import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.clock import TransactionClock
from orchestrator.errors import DomainError
from orchestrator.kernel.evidence_types import VERIFIER_EVIDENCE_PREFIX
from orchestrator.kernel.leases import hash_lease_token
from orchestrator.kernel.states import WAIVER_RISK_CLASSES, ActorRole, WorkUnitState
from orchestrator.kernel.transitions import TransitionGuards, authorize_transition
from orchestrator.persistence.models import (
    Adjudication,
    Claim,
    ContextSnapshot,
    DeploymentObservation,
    Event,
    Evidence,
    PackageAcceptanceCriterion,
    WorkPackageRevision,
    WorkUnit,
)
from orchestrator.services.claim_release import release_claim
from orchestrator.services.claims import validate_active_claim

# POST_DEPLOY_AC_IDS is the SINGLE source of truth in `lifecycle` (the producer that generates
# these ACs). This module is the consumer that gates public adjudication against them, so it
# imports rather than keeping a second copy -- a divergence between generator and gate would let a
# newly-generated post-deploy AC be publicly adjudicated (the invariant this guards).
# FOLLOW_UP_AC_ID is the same producer/consumer split, pointing the opposite way: it must be
# ACCEPTED, not refused.
from orchestrator.services.lifecycle import (
    FOLLOW_UP_AC_ID,
    FOLLOW_UP_EVIDENCE_TYPE,
    POST_DEPLOY_AC_IDS,
    ActorContext,
    is_generated_follow_up_unit,
)
from orchestrator.services.verifier_evaluators import human_may_adjudicate

# not-a-vocabulary: internal policy subset of adjudication outcomes (which outcomes are not
# waivers), not a value shared across a repo or subsystem boundary.
NON_WAIVER_OUTCOMES = frozenset({"passed", "failed", "not_applicable"})
# not-a-vocabulary: internal policy subset of adjudication outcomes a HUMAN may record on an
# intrinsically-judgment AC (see _authorize_outcome), not a value shared across a repo or
# subsystem boundary.
HUMAN_ADJUDICABLE_OUTCOMES = frozenset({"passed", "not_applicable"})
IDEMPOTENCY_LOCK_NAMESPACE = 0x57503338


def list_evidence(session: Session, work_unit_id: uuid.UUID) -> tuple[Evidence, ...]:
    if session.get(WorkUnit, work_unit_id) is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    return tuple(
        session.scalars(
            select(Evidence)
            .where(Evidence.work_unit_id == work_unit_id)
            .order_by(Evidence.recorded_at, Evidence.id)
        )
    )


def append_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    attempt: int,
    actor: ActorContext,
    lease_token: str,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None = None,
    context_snapshot_id: uuid.UUID | None = None,
) -> Evidence | DomainError:
    return _store_evidence(
        session,
        work_package_revision_id=work_package_revision_id,
        work_unit_id=work_unit_id,
        ac_id=ac_id,
        attempt=attempt,
        actor=actor,
        lease_token=lease_token,
        evidence_type=evidence_type,
        stable_ref=stable_ref,
        payload=payload,
        source_revision=source_revision,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        context_snapshot_id=context_snapshot_id,
        supersede=False,
    )


def supersede_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    attempt: int,
    actor: ActorContext,
    lease_token: str,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None = None,
    context_snapshot_id: uuid.UUID | None = None,
) -> Evidence | DomainError:
    return _store_evidence(
        session,
        work_package_revision_id=work_package_revision_id,
        work_unit_id=work_unit_id,
        ac_id=ac_id,
        attempt=attempt,
        actor=actor,
        lease_token=lease_token,
        evidence_type=evidence_type,
        stable_ref=stable_ref,
        payload=payload,
        source_revision=source_revision,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        context_snapshot_id=context_snapshot_id,
        supersede=True,
    )


def append_verifier_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    actor: ActorContext,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None = None,
    attempt: int | None = None,
) -> Evidence | DomainError:
    return _store_verifier_evidence(
        session,
        work_package_revision_id=work_package_revision_id,
        work_unit_id=work_unit_id,
        ac_id=ac_id,
        actor=actor,
        evidence_type=evidence_type,
        stable_ref=stable_ref,
        payload=payload,
        source_revision=source_revision,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        attempt=attempt,
    )


def current_evidence(
    session: Session,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
) -> Evidence | None:
    rows = tuple(
        session.scalars(
            select(Evidence).where(
                Evidence.work_package_revision_id == work_package_revision_id,
                Evidence.work_unit_id == work_unit_id,
                Evidence.ac_id == ac_id,
            )
        )
    )
    return _terminal(rows, "supersedes_evidence_id", "evidence_chain_invalid")


def record_adjudication(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    outcome: str,
    actor: ActorContext,
    rationale: str,
    idempotency_key: str,
    expected_version: int | None = None,
    evidence_id: uuid.UUID | None = None,
    failed_evidence_id: uuid.UUID | None = None,
    risk: str | None = None,
    follow_up: str | None = None,
    scope: str | None = None,
    expires_at: datetime | None = None,
    allow_generated_post_deploy: bool = False,
) -> Adjudication | DomainError:
    command = {
        "ac_id": ac_id,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "evidence_id": _uuid_text(evidence_id),
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
        "expected_version": expected_version,
        "failed_evidence_id": _uuid_text(failed_evidence_id),
        "follow_up": follow_up,
        "outcome": outcome,
        "rationale": rationale,
        "risk": risk,
        "scope": scope,
        "work_package_revision_id": str(work_package_revision_id),
        "work_unit_id": str(work_unit_id),
    }
    try:
        lock_evidence_idempotency_key(session, idempotency_key)
        unit, revision = _validated_subject(
            session,
            work_package_revision_id,
            work_unit_id,
            ac_id,
            allow_generated_post_deploy=allow_generated_post_deploy,
        )
        del revision
        replay = _adjudication_replay(session, idempotency_key, command)
        if replay is not None:
            session.commit()
            return replay
        if expected_version is not None and unit.version != expected_version:
            raise DomainError(
                "version_conflict",
                "work unit version has changed",
                "reload",
                current_state=unit.state,
                current_version=unit.version,
            )
        now = TransactionClock().now(session)
        evidence_type = _criterion_evidence_type(
            session, work_package_revision_id, work_unit_id, ac_id
        )
        _authorize_outcome(
            actor,
            outcome,
            evidence_type,
            # The verifier's own current-evidence lookup, reused rather than reimplemented: a
            # second, divergent notion of "the current evidence" is the defect class this
            # increment closes.
            current_evidence(session, work_package_revision_id, work_unit_id, ac_id),
            unit.state,
        )
        _validate_adjudication_fields(
            session,
            work_package_revision_id,
            work_unit_id,
            ac_id,
            outcome,
            rationale,
            evidence_id,
            failed_evidence_id,
            risk,
            follow_up,
            expires_at,
            now,
        )
        previous = current_adjudication(session, work_package_revision_id, work_unit_id, ac_id)
        event_id = uuid.uuid4()
        row = Adjudication(
            work_package_revision_id=work_package_revision_id,
            work_unit_id=work_unit_id,
            ac_id=ac_id,
            outcome=outcome,
            evidence_id=evidence_id,
            decided_by=actor.actor_id,
            decided_at=now,
            rationale=rationale,
            failed_evidence_id=failed_evidence_id,
            risk=risk,
            follow_up=follow_up,
            scope=scope,
            expires_at=expires_at,
            event_id=event_id,
            supersedes_adjudication_id=previous.id if previous is not None else None,
        )
        session.add(row)
        session.flush()
        session.add(
            _event(
                event_id,
                now,
                actor,
                "adjudication.recorded",
                "adjudication",
                row.id,
                command,
                idempotency_key,
            )
        )
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError as error:
        session.rollback()
        return _adjudication_race_result(session, idempotency_key, command, error)
    except Exception:
        session.rollback()
        raise


def current_adjudication(
    session: Session,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
) -> Adjudication | None:
    rows = tuple(
        session.scalars(
            select(Adjudication).where(
                Adjudication.work_package_revision_id == work_package_revision_id,
                Adjudication.work_unit_id == work_unit_id,
                Adjudication.ac_id == ac_id,
            )
        )
    )
    return _terminal(rows, "supersedes_adjudication_id", "adjudication_chain_invalid")


def _store_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    attempt: int,
    actor: ActorContext,
    lease_token: str,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None,
    context_snapshot_id: uuid.UUID | None,
    supersede: bool,
) -> Evidence | DomainError:
    command = {
        "ac_id": ac_id,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "attempt": attempt,
        "evidence_type": evidence_type,
        "expected_version": expected_version,
        "lease_token_hash": hash_lease_token(lease_token),
        "payload": payload,
        "source_revision": source_revision,
        "stable_ref": stable_ref,
        "supersede": supersede,
        "context_snapshot_id": _uuid_text(context_snapshot_id),
        "work_package_revision_id": str(work_package_revision_id),
        "work_unit_id": str(work_unit_id),
    }
    try:
        lock_evidence_idempotency_key(session, idempotency_key)
        unit, revision = _validated_subject(session, work_package_revision_id, work_unit_id, ac_id)
        replay = _evidence_replay(session, idempotency_key, command)
        if replay is not None:
            session.commit()
            return replay
        if expected_version is not None and unit.version != expected_version:
            raise DomainError(
                "version_conflict",
                "work unit version has changed",
                "reload",
                current_state=unit.state,
                current_version=unit.version,
            )
        if not isinstance(evidence_type, str):
            raise DomainError("evidence_invalid", "evidence type must be a string", None)
        if evidence_type.startswith(VERIFIER_EVIDENCE_PREFIX):
            raise DomainError(
                "evidence_type_reserved",
                "verifier evidence types cannot be submitted by workers",
                None,
            )
        _validate_evidence_fields(stable_ref, payload, evidence_type, source_revision)
        claim = validate_active_claim(session, unit, actor, attempt, lease_token)
        bound_context_snapshot_id = _resolve_context_snapshot_id(
            session,
            unit,
            revision,
            claim,
            actor,
            attempt,
            context_snapshot_id,
        )
        command["context_snapshot_id"] = _uuid_text(bound_context_snapshot_id)
        previous = current_evidence(session, work_package_revision_id, work_unit_id, ac_id)
        if supersede and previous is None:
            raise DomainError(
                "evidence_not_found", "there is no current evidence to supersede", None
            )
        if not supersede and previous is not None:
            raise DomainError(
                "evidence_already_exists",
                "current evidence must be superseded rather than replaced",
                "supersede evidence",
            )
        now = TransactionClock().now(session)
        event_id = uuid.uuid4()
        row = Evidence(
            work_package_revision_id=work_package_revision_id,
            work_unit_id=work_unit_id,
            ac_id=ac_id,
            attempt=attempt,
            evidence_type=evidence_type,
            stable_ref=stable_ref,
            payload=payload,
            source_revision=source_revision,
            recorded_by=actor.actor_id,
            recorded_at=now,
            event_id=event_id,
            idempotency_key=idempotency_key,
            supersedes_evidence_id=previous.id if supersede and previous is not None else None,
            context_snapshot_id=bound_context_snapshot_id,
        )
        session.add(row)
        session.flush()
        session.add(
            _event(
                event_id,
                now,
                actor,
                "evidence.recorded",
                "evidence",
                row.id,
                command,
                idempotency_key,
            )
        )
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError as error:
        session.rollback()
        return _evidence_race_result(session, idempotency_key, command, error)
    except Exception:
        session.rollback()
        raise


def _store_verifier_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    actor: ActorContext,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None,
    attempt: int | None,
) -> Evidence | DomainError:
    command = {
        "ac_id": ac_id,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "attempt": attempt if attempt is not None else 1,
        "evidence_type": evidence_type,
        "expected_version": expected_version,
        "payload": payload,
        "source_revision": source_revision,
        "stable_ref": stable_ref,
        "context_snapshot_id": None,
        "work_package_revision_id": str(work_package_revision_id),
        "work_unit_id": str(work_unit_id),
    }
    try:
        lock_evidence_idempotency_key(session, idempotency_key)
        unit, _revision = _validated_subject(
            session,
            work_package_revision_id,
            work_unit_id,
            ac_id,
            allow_generated_post_deploy=True,
        )
        if attempt is not None and (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt <= 0
            or attempt != unit.attempt_count
        ):
            raise DomainError(
                "evidence_invalid",
                "evidence attempt must match the current positive unit attempt",
                None,
            )
        # Existing verifier findings and post-deploy observations intentionally retain the
        # historical attempt-1 sentinel. Named-check evidence supplies its locked attempt.
        evidence_attempt = attempt if attempt is not None else 1
        replay = _evidence_replay(session, idempotency_key, command)
        if replay is not None:
            session.commit()
            return replay
        if expected_version is not None and unit.version != expected_version:
            raise DomainError(
                "version_conflict",
                "work unit version has changed",
                "reload",
                current_state=unit.state,
                current_version=unit.version,
            )
        _authorize_verifier_evidence(actor)
        _validate_evidence_fields(stable_ref, payload, evidence_type, source_revision)
        previous = current_evidence(session, work_package_revision_id, work_unit_id, ac_id)
        now = TransactionClock().now(session)
        event_id = uuid.uuid4()
        row = Evidence(
            work_package_revision_id=work_package_revision_id,
            work_unit_id=work_unit_id,
            ac_id=ac_id,
            attempt=evidence_attempt,
            evidence_type=evidence_type,
            stable_ref=stable_ref,
            payload=payload,
            source_revision=source_revision,
            recorded_by=actor.actor_id,
            recorded_at=now,
            event_id=event_id,
            idempotency_key=idempotency_key,
            supersedes_evidence_id=previous.id if previous is not None else None,
            context_snapshot_id=None,
        )
        session.add(row)
        session.flush()
        session.add(
            _event(
                event_id,
                now,
                actor,
                "evidence.recorded",
                "evidence",
                row.id,
                command,
                idempotency_key,
            )
        )
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError as error:
        session.rollback()
        return _evidence_race_result(session, idempotency_key, command, error)
    except Exception:
        session.rollback()
        raise


def _validated_subject(
    session: Session,
    revision_id: uuid.UUID,
    unit_id: uuid.UUID,
    ac_id: str,
    *,
    allow_generated_post_deploy: bool = False,
) -> tuple[WorkUnit, WorkPackageRevision]:
    unit = session.scalar(select(WorkUnit).where(WorkUnit.id == unit_id).with_for_update())
    revision = session.get(WorkPackageRevision, revision_id)
    acceptance_criteria = (
        revision.enforcement_snapshot.get("acceptance_criteria") if revision is not None else None
    )
    generated_post_deploy = _is_generated_post_deploy_subject(session, revision_id, unit_id, ac_id)
    generated_follow_up = _is_generated_follow_up_subject(session, unit_id, ac_id)
    if generated_post_deploy and not allow_generated_post_deploy:
        raise DomainError(
            "post_deploy_verifier_required",
            "post-deploy verification adjudications must be recorded by the verifier command",
            "verify",
        )
    if (
        unit is None
        or revision is None
        or unit.work_package_revision_id != revision_id
        or (
            not generated_post_deploy
            and not generated_follow_up
            and (not isinstance(acceptance_criteria, list) or ac_id not in acceptance_criteria)
        )
    ):
        raise DomainError(
            "evidence_subject_invalid",
            "package revision, work unit, and acceptance criterion do not match",
            None,
        )
    return unit, revision


def _is_generated_post_deploy_subject(
    session: Session,
    revision_id: uuid.UUID,
    unit_id: uuid.UUID,
    ac_id: str,
) -> bool:
    if ac_id not in POST_DEPLOY_AC_IDS:
        return False
    observation = session.scalar(
        select(DeploymentObservation).where(
            DeploymentObservation.work_package_revision_id == revision_id,
            DeploymentObservation.post_deploy_work_unit_id == unit_id,
        )
    )
    return observation is not None


def _is_generated_follow_up_subject(session: Session, unit_id: uuid.UUID, ac_id: str) -> bool:
    """The generated follow-up criterion, which a HUMAN owns.

    No `allow_*` parameter, deliberately: unlike the verifier-owned generated ids above, this one
    is meant to be adjudicated from the public `/review` form. Gating it would make the unit
    undischargeable by the only actor designed to discharge it.

    Precisely because it is the one generated AC a HUMAN may discharge, the unit test is
    `is_generated_follow_up_unit` -- the DERIVED-ID check, not the capability alone.
    `required_capability` is authorable at unit ingress, so a capability-only marker would let any
    author hand their own unit this human-adjudicable carve-out; the `uuid5` id can only come from
    the minting pass.
    """
    if ac_id != FOLLOW_UP_AC_ID:
        return False
    unit = session.get(WorkUnit, unit_id)
    return unit is not None and is_generated_follow_up_unit(unit)


def _validate_evidence_fields(
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    evidence_type: str,
    source_revision: str,
) -> None:
    if not _text(stable_ref) and payload is None:
        raise DomainError(
            "evidence_required", "stable reference or structured payload is required", None
        )
    if payload is not None and not isinstance(payload, dict):
        raise DomainError("evidence_invalid", "evidence payload must be structured", None)
    if not _text(evidence_type) or not _text(source_revision):
        raise DomainError(
            "evidence_invalid", "evidence type and source revision are required", None
        )


def _resolve_context_snapshot_id(
    session: Session,
    unit: WorkUnit,
    revision: WorkPackageRevision,
    claim: Claim,
    actor: ActorContext,
    attempt: int,
    context_snapshot_id: uuid.UUID | None,
) -> uuid.UUID | None:
    selected_id = context_snapshot_id or claim.execution_context_snapshot_id
    if selected_id is None:
        if _has_required_context(revision):
            raise DomainError("context_missing_required", "standing context is incomplete", None)
        return None
    snapshot = session.get(ContextSnapshot, selected_id)
    valid = (
        snapshot is not None
        and snapshot.work_package_revision_id == revision.id
        and snapshot.work_unit_id == unit.id
        and snapshot.claim_id == claim.id
        and snapshot.actor_id == actor.actor_id
        and snapshot.actor_role == actor.role
        and snapshot.attempt == attempt
        and snapshot.decision == "accepted"
    )
    if not valid:
        raise DomainError(
            "context_snapshot_invalid",
            "evidence context snapshot does not match the active attempt",
            None,
        )
    assert snapshot is not None
    return snapshot.id


def _has_required_context(revision: WorkPackageRevision) -> bool:
    required = revision.enforcement_snapshot.get("required_context")
    return isinstance(required, dict) and bool(required)


def _criterion_evidence_type(
    session: Session, revision_id: uuid.UUID, unit_id: uuid.UUID, ac_id: str
) -> str | None:
    """The generated follow-up criterion is never persisted as a `PackageAcceptanceCriterion` row
    (it is constructed transiently by `services.verifier_criteria`), so the DB lookup below always
    misses for it.

    The fallback below re-runs `_is_generated_follow_up_subject` rather than trusting `ac_id ==
    FOLLOW_UP_AC_ID` alone. `_validated_subject` admits a subject through TWO independent paths --
    the generated-follow-up check, or `ac_id` merely appearing in the revision's
    `enforcement_snapshot["acceptance_criteria"]` list. That second path is capability-blind: a
    revision whose package-declared AC list happens to contain the literal string
    `"follow-up-review"` would let ANY of its units past `_validated_subject`, for any capability.
    Keying this fallback on `ac_id` alone would then hand every one of those units the generated
    criterion's `observation` evidence type -- and with it, a HUMAN's authority to record `passed`
    where none was intended. Re-running the unit identity check here closes that: this function
    does not get to assume `_validated_subject`'s admission reason.
    """
    evidence_type = session.scalar(
        select(PackageAcceptanceCriterion.evidence_type).where(
            PackageAcceptanceCriterion.work_package_revision_id == revision_id,
            PackageAcceptanceCriterion.ac_id == ac_id,
        )
    )
    if evidence_type is not None:
        return evidence_type
    if _is_generated_follow_up_subject(session, unit_id, ac_id):
        return FOLLOW_UP_EVIDENCE_TYPE
    return None


def _authorize_outcome(
    actor: ActorContext,
    outcome: str,
    evidence_type: str | None,
    evidence: Evidence | None,
    unit_state: str,
) -> None:
    if outcome == "waived":
        allowed = actor.role is ActorRole.HUMAN
    elif actor.role is ActorRole.VERIFIER:
        allowed = outcome in NON_WAIVER_OUTCOMES
    elif actor.role is ActorRole.HUMAN and outcome in HUMAN_ADJUDICABLE_OUTCOMES:
        # A human resolves what the machine does not own. `human_may_adjudicate` is the single
        # answer, shared with the /review form so it cannot offer what this refuses.
        #
        # This replaces a JUDGMENT_TYPES membership test that keyed on the criterion's STATIC
        # declared type -- a proxy for the real concern, which is timing: an automated_check must
        # not be settled by a human while CI evidence could still arrive. The predicate's clause
        # (b) guards that timing directly, by requiring the unit to be in awaiting_review, where
        # the verifier has already run and explicitly handed off.
        allowed = human_may_adjudicate(evidence_type, evidence, unit_state)
    else:
        allowed = False
    if not allowed:
        raise DomainError("role_forbidden", "actor may not record this outcome", None)


def _authorize_verifier_evidence(actor: ActorContext) -> None:
    if actor.role is not ActorRole.VERIFIER:
        raise DomainError("role_forbidden", "only verifiers may record verifier evidence", None)


def _validate_adjudication_fields(
    session: Session,
    revision_id: uuid.UUID,
    unit_id: uuid.UUID,
    ac_id: str,
    outcome: str,
    rationale: str,
    evidence_id: uuid.UUID | None,
    failed_evidence_id: uuid.UUID | None,
    risk: str | None,
    follow_up: str | None,
    expires_at: datetime | None,
    now: datetime,
) -> None:
    if not _text(rationale):
        code = "waiver_invalid" if outcome == "waived" else "adjudication_invalid"
        raise DomainError(code, "adjudication rationale is required", None)
    if risk is not None and risk not in WAIVER_RISK_CLASSES:
        code = "waiver_invalid" if outcome == "waived" else "adjudication_invalid"
        raise DomainError(code, "risk must be one of the controlled risk classes", None)
    for reference in (evidence_id, failed_evidence_id):
        if reference is not None:
            _validate_evidence_reference(session, reference, revision_id, unit_id, ac_id)
    if outcome == "waived" and (
        failed_evidence_id is None
        or not _text(risk)
        or not _text(follow_up)
        or (expires_at is not None and expires_at <= now)
    ):
        raise DomainError(
            "waiver_invalid",
            "waiver requires failed evidence, a risk class, follow-up, and a future expiry "
            "when set",
            None,
        )


def _validate_evidence_reference(
    session: Session,
    evidence_id: uuid.UUID,
    revision_id: uuid.UUID,
    unit_id: uuid.UUID,
    ac_id: str,
) -> None:
    evidence = session.get(Evidence, evidence_id)
    if evidence is None or (
        evidence.work_package_revision_id,
        evidence.work_unit_id,
        evidence.ac_id,
    ) != (revision_id, unit_id, ac_id):
        raise DomainError(
            "evidence_subject_invalid", "referenced evidence belongs to another subject", None
        )


def _evidence_replay(
    session: Session,
    idempotency_key: str,
    command: dict[str, object],
    *,
    action: str = "evidence.recorded",
) -> Evidence | None:
    event = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    row = session.scalar(select(Evidence).where(Evidence.idempotency_key == idempotency_key))
    if event is None and row is None:
        return None
    if row is None:
        raise _idempotency_conflict()
    assert row is not None
    command_context_snapshot_id = command.get("context_snapshot_id")
    expected_command = command | {
        "context_snapshot_id": (
            command_context_snapshot_id
            if command_context_snapshot_id is not None
            else _uuid_text(row.context_snapshot_id)
        )
    }
    if (
        event is None
        or event.action != action
        or event.subject_id != row.id
        or event.payload.get("command") != expected_command
    ):
        raise _idempotency_conflict()
    return row


def _adjudication_replay(
    session: Session, idempotency_key: str, command: dict[str, object]
) -> Adjudication | None:
    event = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    if event is None:
        return None
    if event.action != "adjudication.recorded" or event.payload.get("command") != command:
        raise _idempotency_conflict()
    row = session.get(Adjudication, event.subject_id)
    if row is None:
        raise _idempotency_conflict()
    return row


def lock_evidence_idempotency_key(session: Session, idempotency_key: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:idempotency_key))"),
        {
            "namespace": IDEMPOTENCY_LOCK_NAMESPACE,
            "idempotency_key": idempotency_key,
        },
    )


def _evidence_race_result(
    session: Session,
    idempotency_key: str,
    command: dict[str, object],
    error: IntegrityError,
    *,
    action: str = "evidence.recorded",
) -> Evidence | DomainError:
    try:
        replay = _evidence_replay(session, idempotency_key, command, action=action)
    except DomainError as conflict:
        return conflict
    if replay is not None:
        return replay
    raise error


def _adjudication_race_result(
    session: Session,
    idempotency_key: str,
    command: dict[str, object],
    error: IntegrityError,
) -> Adjudication | DomainError:
    try:
        replay = _adjudication_replay(session, idempotency_key, command)
    except DomainError as conflict:
        return conflict
    if replay is not None:
        return replay
    raise error


def _event(
    event_id: uuid.UUID,
    occurred_at: datetime,
    actor: ActorContext,
    action: str,
    subject_type: str,
    subject_id: uuid.UUID,
    command: dict[str, object],
    idempotency_key: str,
) -> Event:
    return Event(
        id=event_id,
        occurred_at=occurred_at,
        actor_id=actor.actor_id,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        from_state=None,
        to_state=None,
        payload={"command": command},
        correlation_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
    )


def _terminal(rows: tuple[Any, ...], link_name: str, error_code: str) -> Any | None:
    if not rows:
        return None
    by_id = {row.id: row for row in rows}
    superseded_ids: set[uuid.UUID] = set()
    for row in rows:
        previous_id = getattr(row, link_name)
        if previous_id is None:
            continue
        if previous_id not in by_id or previous_id in superseded_ids:
            raise DomainError(error_code, "supersession chain is invalid", None)
        superseded_ids.add(previous_id)
    terminal = tuple(row for row in rows if row.id not in superseded_ids)
    if len(terminal) != 1:
        raise DomainError(error_code, "supersession chain has multiple terminals", None)
    return terminal[0]


def _idempotency_conflict() -> DomainError:
    return DomainError(
        "idempotency_conflict",
        "idempotency key belongs to a different operation",
        "use a new idempotency key",
    )


def _text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _uuid_text(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


# Distinct from the evidence idempotency lock (0x57503338) and the reconciliation lock
# (0x57503231): this one serializes two concurrent RECOVERIES on the same (unit, ac) head.
EVIDENCE_HEAD_LOCK_NAMESPACE = 0x57503232
RECOVERY_REASON = "recovered_from_expired_lease"
RECOVERY_SOURCE_STATES = {WorkUnitState.CLAIMED, WorkUnitState.EXECUTING}


def recover_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    attempt: int,
    actor: ActorContext,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None = None,
) -> Evidence | DomainError:
    """AC-004: attach evidence a worker produced before its lease expired, without redoing it.

    There is NO lease_token parameter -- the whole scenario is that the lease is gone.

    This bypasses `_store_evidence`, because `_validate_attempt` rejects a SYSTEM actor, a
    released claim, and an expired lease. But `_store_evidence`'s `evidence_already_exists` check
    is the ONLY code preventing a second supersession head, and two heads make `_terminal` raise:
    the AC could then never be adjudicated, no further evidence could be written, and `evidence`
    is append-only so the row could never be repaired. One naive call would wedge the unit so it
    could NEVER complete. So this resolves the current head UNDER THE LOCKS and supersedes it.

    What AC-004 promises is attachment WITHOUT REDOING THE WORK -- not completion without a new
    attempt. `FAILED` has no edge to `SUBMITTED`, and only WORKER edges reach `SUBMITTED`, so
    some attempt must still submit. It just does not have to run the job again.
    """
    command: dict[str, object] = {
        "ac_id": ac_id,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "attempt": attempt,
        "context_snapshot_id": None,
        "evidence_type": evidence_type,
        "expected_version": expected_version,
        "payload": payload,
        "recovery": RECOVERY_REASON,
        "source_revision": source_revision,
        "stable_ref": stable_ref,
        "work_package_revision_id": str(work_package_revision_id),
        "work_unit_id": str(work_unit_id),
    }
    try:
        _authorize_recovery(actor)
        # Lock order: the (unit, ac) HEAD lock serializes two concurrent recoveries; the
        # idempotency lock serializes a duplicate delivery; and `_validated_subject` takes the
        # WorkUnit row lock, which is what serializes this against a concurrent submit from a
        # later attempt (that path takes the same row lock). The head is then re-read UNDER
        # those locks -- a check-then-insert without them is TOCTOU-racy, and the losing racer
        # writes the second head.
        _lock_evidence_head(session, work_unit_id, ac_id)
        lock_evidence_idempotency_key(session, idempotency_key)
        unit, _revision = _validated_subject(session, work_package_revision_id, work_unit_id, ac_id)
        replay = _evidence_replay(session, idempotency_key, command, action="evidence.recovered")
        if replay is not None:
            session.commit()
            return replay
        if expected_version is not None and unit.version != expected_version:
            raise DomainError(
                "version_conflict",
                "work unit version has changed",
                "reload",
                current_state=unit.state,
                current_version=unit.version,
            )
        _validate_evidence_fields(stable_ref, payload, evidence_type, source_revision)
        if WorkUnitState(unit.state) in {WorkUnitState.COMPLETED, WorkUnitState.CANCELLED}:
            raise DomainError(
                "recovery_not_allowed",
                "completed and cancelled work units may not receive recovered evidence",
                None,
            )

        now = TransactionClock().now(session)
        claim = _recoverable_claim(session, unit, attempt, now)
        if claim.released_at is None:
            # The AC's actual scenario: the lease lapsed just before submit and nothing has
            # reclaimed it. Recovery is the releaser -- through the SOLE writer of those columns.
            release_claim(claim, terminal_reason="lease_expired", released_at=now)
            _system_fail_without_new_attempt(session, unit, actor, now, idempotency_key, claim)

        previous = current_evidence(session, work_package_revision_id, work_unit_id, ac_id)
        event_id = uuid.uuid4()
        row = Evidence(
            work_package_revision_id=work_package_revision_id,
            work_unit_id=work_unit_id,
            ac_id=ac_id,
            attempt=attempt,
            evidence_type=evidence_type,
            stable_ref=stable_ref,
            payload={
                **(payload or {}),
                "recovery": {
                    "reason": RECOVERY_REASON,
                    "claim_id": str(claim.id),
                    "attempt": attempt,
                    "recovered_by": actor.actor_id,
                },
            },
            source_revision=source_revision,
            recorded_by=actor.actor_id,
            recorded_at=now,
            event_id=event_id,
            idempotency_key=idempotency_key,
            # SUPERSEDE the head -- never fork it.
            supersedes_evidence_id=previous.id if previous is not None else None,
            context_snapshot_id=None,
        )
        session.add(row)
        session.flush()
        session.add(
            _event(
                event_id,
                now,
                actor,
                "evidence.recovered",
                "evidence",
                row.id,
                command,
                idempotency_key,
            )
        )
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError as error:
        session.rollback()
        return _evidence_race_result(
            session, idempotency_key, command, error, action="evidence.recovered"
        )
    except Exception:
        session.rollback()
        raise


def _authorize_recovery(actor: ActorContext) -> None:
    # NEVER the expired worker. Letting it self-serve past its lease would re-open exactly the
    # hole the lease exists to close.
    if actor.role not in {ActorRole.SYSTEM, ActorRole.HUMAN}:
        raise DomainError(
            "role_forbidden",
            "only the system actor or a human operator may recover evidence",
            None,
        )


def _recoverable_claim(session: Session, unit: WorkUnit, attempt: int, now: datetime) -> Claim:
    claim = session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit.id, Claim.attempt == attempt)
        .with_for_update()
    )
    if claim is None:
        raise DomainError("claim_not_found", "work unit has no claim for that attempt", None)
    if claim.lease_expires_at > now:
        raise DomainError("lease_not_expired", "claim lease has not expired", None)
    if claim.released_at is not None and claim.terminal_reason != "lease_expired":
        raise DomainError(
            "claim_not_recoverable",
            "claim was released for a reason other than lease expiry",
            None,
        )
    return claim


def _system_fail_without_new_attempt(
    session: Session,
    unit: WorkUnit,
    actor: ActorContext,
    now: datetime,
    idempotency_key: str,
    claim: Claim,
) -> None:
    """CLAIMED/EXECUTING -> FAILED on a SYSTEM edge, attributed to the recovering actor.

    It mints NO new attempt: `attempt_count` is untouched, so requeue/reclaim still has the
    budget it would otherwise have silently spent on a run that never happened.
    """
    source = WorkUnitState(unit.state)
    if source not in RECOVERY_SOURCE_STATES:
        return
    authorize_transition(source, WorkUnitState.FAILED, ActorRole.SYSTEM, TransitionGuards())
    unit.state = WorkUnitState.FAILED
    unit.version += 1
    session.add(
        Event(
            id=uuid.uuid4(),
            occurred_at=now,
            actor_id=actor.actor_id,
            action="work_unit.transitioned",
            subject_type="work_unit",
            subject_id=unit.id,
            from_state=source,
            to_state=WorkUnitState.FAILED,
            payload={
                "attempt": claim.attempt,
                "expired_claim_id": str(claim.id),
                "reason": RECOVERY_REASON,
                "version": unit.version,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=f"{idempotency_key}:failed",
        )
    )
    session.flush()


def _lock_evidence_head(session: Session, work_unit_id: uuid.UUID, ac_id: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:head_key))"),
        {
            "namespace": EVIDENCE_HEAD_LOCK_NAMESPACE,
            "head_key": f"{work_unit_id}:{ac_id}",
        },
    )
