"""A standing bump lands on its recognition and its grant (ADR-0025 amendment 4).

The approval term reads "a bound approval, or the policy path". Every unit below starts with every
other term met and no human approval, so each test measures one condition of that path. A
recognised bump in a granted repository is admitted; remove one condition and the answer is
`authority_approval_not_bound`, unchanged. Everything the switch governs is the policy path: a bound
approval never asks it.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.factory_policy import FactoryPolicy, load_factory_policy
from orchestrator.kernel.authority import normalize_authority
from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import Event, UnitPrMerge, WorkUnit
from orchestrator.services.intake import authority_gate
from orchestrator.services.intake.authority_gate import AuthorityGate
from orchestrator.services.intake.bump_policy import recognised_only_by_bump_patterns
from orchestrator.services.intake.packages import register_approved_unit, register_revision
from orchestrator.services.landing import pr_merge_admission as admission_module
from orchestrator.services.landing.pr_merge import (
    GitHubGatewayError,
    MergeCommand,
    MergeOutcome,
    PullRequestState,
    land_unit_pull_request,
)
from orchestrator.services.landing.pr_merge_admission import (
    AUTHORITY_APPROVAL_NOT_BOUND,
    MERGE_POLICY_PATH_OFF,
    admission_for,
    pr_merge_admission,
)
from tests._support.seeding import NOW
from tests.services.change_record_doubles import approved_record_source, no_record_source
from tests.services.estate_doubles import (
    LANDING_UNKNOWN,
    SOURCE_UNREADABLE,
    EstateAnswer,
    FakeEstateLandingSource,
    inert_source,
    redeploying_source,
)
from tests.services.test_adjudications import FROM_EVALUATION, add_criterion
from tests.services.test_bump_decomposition_policy import _criterion, _propose
from tests.services.test_bump_decomposition_policy import _envelope as _bump_envelope
from tests.services.test_bump_decomposition_policy import _revision as _bump_revision
from tests.services.test_member_proposals import grant_services
from tests.services.test_pr_merge import FakeGateway
from tests.services.test_pr_merge_admission import (
    IN_WINDOW,
    SYSTEM,
    FixedClock,
    _approve_authority,
    _bind_and_arm,
    _complete,
    _open_condition,
    _revision,
    _verifier_decided,
)
from tests.services.test_verifier_decided_completion import VERIFIER, _decide_observed

REPOSITORY = "AlobarQuest/orchestrator"
POLICY_APPROVER = "policy:dependency-update@v2"
GATE_ACTION = "authority.human_gate_not_required"
STANDING_FIELDS = {
    "target_repo": REPOSITORY,
    "package": "httpx2",
    "from_version": "2.13.1",
    "to_version": "2.16.0",
    "standing": True,
}


def _bump(
    session: Session,
    key: str,
    *,
    approved_by: str = POLICY_APPROVER,
    profile: str | None = "dependency-update",
    fields: object = None,
    reach: tuple[str, ...] | None = ("source_repository",),
    change_class: str | None = "dependency-update",
    grants: tuple[str, ...] = ("dependency_updates",),
) -> WorkUnit:
    """A completed unit with every term met but the authority term; each test changes one thing."""
    unit_id = uuid.uuid4()
    payload = _bump_envelope()
    payload["constraints"]["work_unit_id"] = str(unit_id)
    if change_class is None:
        del payload["change_class"]
    else:
        payload["change_class"] = change_class
    authority = normalize_authority(payload)
    grant_services(session, REPOSITORY, *grants)
    declared = {} if reach is None else {"reach": list(reach)}
    revision = register_revision(
        session,
        package_id=f"orchestrator-uv-httpx2-{key}",
        source_repository="AlobarQuest/intent-packages",
        revision=1,
        content_hash=f"sha256:{key}",
        source_path="intent.md",
        source_commit="abc123",
        approved_by=approved_by,
        approved_at=NOW,
        approval_event_id=f"approval-{key}",
        enforcement_snapshot={
            "acceptance_criteria": ["ac-1"],
            "profile_fields": STANDING_FIELDS if fields is None else fields,
            **declared,
        },
        authority=authority,
        registry_version=1,
        profile=profile,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    unit = register_approved_unit(
        session,
        revision_id=revision.id,
        unit_id=unit_id,
        unit_key=key,
        title=key,
        outcome=f"{key} complete",
        required_capability="repo.edit",
        authority=authority,
        max_attempts=3,
        approved_by="human-1",
        approved_at=NOW,
        actor_id="human-1",
        actor_role=ActorRole.HUMAN,
    )
    add_criterion(session, unit, "ac-1", "automated_check")
    session.commit()
    _verifier_decided(session, unit)
    _bind_and_arm(session, unit)
    _complete(session, unit)
    return unit


def _answer(session: Session, unit: WorkUnit, *, enabled: bool = True, source: Any = None):
    return admission_for(
        session,
        unit,
        _revision(session, unit),
        source or inert_source(),
        no_record_source(),
        FixedClock(IN_WINDOW),
        bump_merge_enabled=enabled,
    )


def _land(
    session: Session,
    unit: WorkUnit,
    gateway: FakeGateway,
    *,
    enabled: bool = True,
    key: str = "bump-merge",
) -> UnitPrMerge:
    return land_unit_pull_request(
        session,
        MergeCommand(
            unit_id=unit.id, actor=SYSTEM, idempotency_key=key, expected_version=unit.version
        ),
        gateway,
        inert_source(),
        no_record_source(),
        clock=FixedClock(IN_WINDOW),
        bump_merge_enabled=enabled,
    )


class AmbiguousGateway(FakeGateway):
    """The remote refused and the confirming read failed too."""

    def read_pull_request(self, *, repository: str, number: int) -> PullRequestState:
        if self.merges:
            raise GitHubGatewayError("read_status", 502)
        return super().read_pull_request(repository=repository, number=number)


def _gate_events(session: Session, unit: WorkUnit) -> list[Event]:
    return list(
        session.scalars(
            select(Event).where(Event.action == GATE_ACTION, Event.subject_id == unit.id)
        )
    )


def _narrowed() -> FactoryPolicy:
    """The policy with every known-good pattern withdrawn, as a release after dispatch might."""
    policy = load_factory_policy()
    return replace(
        policy,
        rows={key: replace(row, known_good=()) for key, row in policy.rows.items()},
    )


def _for_another_class() -> FactoryPolicy:
    """Recognition stands, but no pattern is a dependency-update pattern."""
    policy = load_factory_policy()
    return replace(
        policy,
        rows={
            key: replace(
                row,
                known_good=tuple(replace(p, change_class="other") for p in row.known_good),
            )
            for key, row in policy.rows.items()
        },
    )


def _unreadable() -> FactoryPolicy:
    raise DomainError("factory_policy_invalid", "the artifact is broken", None)


# ---------------------------------------------------------------------------------------------
# The policy path.
# ---------------------------------------------------------------------------------------------


def test_a_recognised_bump_in_a_granted_repository_merges_without_an_approval(
    migrated_session: Session,
) -> None:
    unit = _bump(migrated_session, "admitted")
    assert unit.authority_approval_id is None

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is True
    assert answer.refusals == ()
    assert answer.authority_recognised_by
    assert answer.authority_policy_version == load_factory_policy().version
    assert answer.authority_policy_source == load_factory_policy().source


def test_a_unit_policy_approved_through_the_real_path_merges(migrated_session: Session) -> None:
    """End to end from Task 2's proposal: no fixture here says the unit is recognised."""
    grant_services(migrated_session, REPOSITORY, "dependency_updates")
    revision = _bump_revision(migrated_session, criteria=(_criterion("AC-001"),))
    _propose(migrated_session, revision)
    unit = migrated_session.scalars(
        select(WorkUnit).where(WorkUnit.work_package_revision_id == revision.id)
    ).one()
    _decide_observed(migrated_session, unit, "AC-001", actor=VERIFIER, **FROM_EVALUATION)
    _bind_and_arm(migrated_session, unit)
    _complete(migrated_session, unit)

    answer = _answer(migrated_session, unit)

    assert answer.refusals == ()
    assert answer.satisfied is True


def test_the_act_lands_and_records_that_the_gate_did_not_apply(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "acted")
    gateway = FakeGateway()

    record = _land(migrated_session, unit, gateway)

    assert record.status == "merged"
    assert len(gateway.merges) == 1
    policy = load_factory_policy()
    [event] = _gate_events(migrated_session, unit)
    assert event.actor_id == SYSTEM.actor_id
    assert event.payload == {
        "at": "merge",
        "recognised_by": list(_answer(migrated_session, unit).authority_recognised_by),
        "policy_version": policy.version,
        "policy_source": policy.source,
        "authority_fingerprint": unit.authority_fingerprint,
    }
    assert event.payload["recognised_by"]
    assert event.idempotency_key == "bump-merge:authority-gate"
    assert unit.authority_approval_id is None


def test_a_replayed_act_records_nothing_further(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "replayed")
    gateway = FakeGateway()
    first = _land(migrated_session, unit, gateway)

    second = _land(migrated_session, unit, gateway, key="bump-merge-again")

    assert second.id == first.id
    assert len(gateway.merges) == 1
    assert len(_gate_events(migrated_session, unit)) == 1


def test_a_confirmed_remote_refusal_leaves_no_record_of_the_gate(
    migrated_session: Session,
) -> None:
    """Nothing happened, so nothing is claimed about why it might have."""
    unit = _bump(migrated_session, "refused-remote")
    gateway = FakeGateway(
        merge_error=GitHubGatewayError("merge_status", 405),
        state_after_refusal=PullRequestState(
            base_ref="main", default_branch="main", open=True, landed=False
        ),
    )

    with pytest.raises(DomainError):
        _land(migrated_session, unit, gateway)

    assert _gate_events(migrated_session, unit) == []


def test_landing_after_a_lost_response_is_not_recorded_as_policy_lifted(
    migrated_session: Session,
) -> None:
    unit = _bump(migrated_session, "reconciled")
    gateway = FakeGateway(
        merge_error=GitHubGatewayError("merge_status", 405),
        state_after_refusal=PullRequestState(
            base_ref="main", default_branch="main", open=False, landed=True
        ),
    )

    record = _land(migrated_session, unit, gateway)

    assert record.status == "already_merged"
    assert _gate_events(migrated_session, unit) == []


def test_a_pull_request_somebody_else_landed_records_no_lifted_gate(
    migrated_session: Session,
) -> None:
    unit = _bump(migrated_session, "landed-elsewhere")
    gateway = FakeGateway(
        state=PullRequestState(base_ref="main", default_branch="main", open=False, landed=True)
    )

    record = _land(migrated_session, unit, gateway)

    assert record.status == "already_merged"
    assert gateway.merges == []
    assert _gate_events(migrated_session, unit) == []


def test_a_remote_answer_that_did_not_land_records_no_lifted_gate(
    migrated_session: Session,
) -> None:
    unit = _bump(migrated_session, "not-landed")
    gateway = FakeGateway(outcome=MergeOutcome(landed=False, commit_sha=None, status_code=200))

    record = _land(migrated_session, unit, gateway)

    assert record.status == "refused"
    assert _gate_events(migrated_session, unit) == []


def test_an_ambiguous_refusal_records_no_lifted_gate(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "ambiguous")
    gateway = AmbiguousGateway(merge_error=GitHubGatewayError("merge_status", 405))

    record = _land(migrated_session, unit, gateway)

    assert record.status == "refused"
    assert _gate_events(migrated_session, unit) == []


def test_reading_the_answer_records_nothing(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "read-only")

    answer = pr_merge_admission(
        migrated_session, unit.id, inert_source(), no_record_source(), bump_merge_enabled=True
    )

    assert answer.satisfied is True
    assert _gate_events(migrated_session, unit) == []


# ---------------------------------------------------------------------------------------------
# Remove one condition: the approval term refuses, and only that term.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"change_class": "software-delivery"}, id="another-change-class"),
        pytest.param({"change_class": None}, id="no-change-class"),
        pytest.param({"profile": "software-delivery"}, id="another-profile"),
        pytest.param({"profile": None}, id="no-profile"),
        pytest.param({"approved_by": "human-1"}, id="a-person-approved-the-revision"),
        pytest.param({"approved_by": "policy:rotation@v1"}, id="another-approval-policy"),
        pytest.param({"fields": {**STANDING_FIELDS, "standing": False}}, id="not-standing"),
        pytest.param(
            {"fields": {k: v for k, v in STANDING_FIELDS.items() if k != "standing"}},
            id="standing-absent",
        ),
        pytest.param({"fields": {**STANDING_FIELDS, "standing": "true"}}, id="standing-a-string"),
        pytest.param({"reach": ("live_estate",)}, id="live-estate-reach"),
        pytest.param({"reach": ("source_repository", "live_estate")}, id="wider-reach"),
        pytest.param({"reach": None}, id="undeclared-reach"),
    ],
)
def test_a_unit_outside_the_policy_path_needs_its_approval(
    migrated_session: Session, changed: dict[str, Any]
) -> None:
    grants = ("dependency_updates", "app_changes")
    unit = _bump(migrated_session, "outside", grants=grants, **changed)

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert AUTHORITY_APPROVAL_NOT_BOUND in answer.refusals
    assert MERGE_POLICY_PATH_OFF not in answer.refusals
    assert answer.authority_recognised_by == ()


def test_the_change_class_is_asked_for_itself(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a dependency-update envelope matches these patterns today; don't rely on that."""
    unit = _bump(
        migrated_session,
        "class-alone",
        change_class="software-delivery",
        grants=("dependency_updates", "app_changes"),
    )
    policy = load_factory_policy()
    names = tuple(p.name for row in policy.rows.values() for p in row.known_good)[:1]
    monkeypatch.setattr(
        admission_module,
        "human_authority_gate",
        lambda *_: AuthorityGate((), names, policy.version, policy.source),
    )

    assert _answer(migrated_session, unit).refusals == (AUTHORITY_APPROVAL_NOT_BOUND,)


def test_an_ungranted_repository_is_refused_on_both_terms(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "ungranted", grants=("app_changes",))

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == (AUTHORITY_APPROVAL_NOT_BOUND, "merge_repository_not_granted")
    assert answer.authority_recognised_by == ()


@pytest.mark.parametrize(
    "policy", [_narrowed, _unreadable], ids=["narrowed-since-dispatch", "unreadable"]
)
def test_recognition_is_asked_again_at_merge(
    migrated_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    policy: Callable[[], FactoryPolicy],
) -> None:
    """The unit ran on a recognition; only the policy as loaded now may land it."""
    unit = _bump(migrated_session, "narrowed")
    assert _answer(migrated_session, unit).satisfied is True
    monkeypatch.setattr(authority_gate, "load_factory_policy", policy)

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == (AUTHORITY_APPROVAL_NOT_BOUND,)
    gateway = FakeGateway()
    with pytest.raises(DomainError) as refused:
        _land(migrated_session, unit, gateway)
    assert refused.value.code == "pr_merge_not_admissible"
    assert gateway.merges == []
    assert gateway.reads == []


def test_recognition_by_anything_but_a_bump_pattern_is_refused(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = _bump(migrated_session, "other-pattern")
    monkeypatch.setattr(admission_module, "load_factory_policy", _for_another_class)

    assert _answer(migrated_session, unit).refusals == (AUTHORITY_APPROVAL_NOT_BOUND,)


def test_an_unreadable_policy_at_the_pattern_read_is_refused(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = _bump(migrated_session, "pattern-read")
    monkeypatch.setattr(admission_module, "load_factory_policy", _unreadable)

    assert _answer(migrated_session, unit).refusals == (AUTHORITY_APPROVAL_NOT_BOUND,)


def test_no_refusal_and_no_pattern_is_not_recognition() -> None:
    """Every subset of nothing holds, so the affirmative case has to say a pattern recognised it."""
    gate = AuthorityGate((), (), 1, "source")

    assert recognised_only_by_bump_patterns(gate, load_factory_policy()) is False


def test_a_refusal_beside_a_bump_pattern_is_not_recognition() -> None:
    """One reach member recognised and another refused: every member has to be recognised."""
    policy = load_factory_policy()
    name = next(
        pattern.name
        for row in policy.rows.values()
        for pattern in row.known_good
        if pattern.change_class == "dependency-update"
    )
    gate = AuthorityGate(("authority_envelope_novel",), (name,), policy.version, policy.source)

    assert recognised_only_by_bump_patterns(gate, policy) is False


def test_an_open_reconciliation_condition_still_refuses(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "open")
    _open_condition(migrated_session, unit)

    answer = _answer(migrated_session, unit)

    assert answer.satisfied is False
    assert answer.refusals == ("open_reconciliation_condition",)


def test_a_redeploying_repository_stays_with_a_person_even_with_its_record(
    migrated_session: Session,
) -> None:
    """A `source_repository` declaration, an approved record and an open window: every landing
    term is met, and the estate's answer alone keeps this unit off the policy path."""
    unit = _bump(migrated_session, "redeploying")

    answer = admission_for(
        migrated_session,
        unit,
        _revision(migrated_session, unit),
        redeploying_source(),
        approved_record_source(REPOSITORY, 7),
        FixedClock(IN_WINDOW),
        bump_merge_enabled=True,
    )

    assert answer.satisfied is False
    assert answer.refusals == (AUTHORITY_APPROVAL_NOT_BOUND,)
    assert answer.authority_recognised_by == ()


def test_a_bound_approval_still_lands_in_a_redeploying_repository(
    migrated_session: Session,
) -> None:
    unit = _bump(migrated_session, "redeploying-human")
    _approve_authority(migrated_session, unit)

    answer = admission_for(
        migrated_session,
        unit,
        _revision(migrated_session, unit),
        redeploying_source(),
        approved_record_source(REPOSITORY, 7),
        FixedClock(IN_WINDOW),
    )

    assert answer.satisfied is True


@pytest.mark.parametrize(
    "estate",
    [
        pytest.param(EstateAnswer(landing=LANDING_UNKNOWN, reason=None), id="not-assessed"),
        pytest.param(EstateAnswer(landing=None, reason=SOURCE_UNREADABLE), id="unreadable"),
    ],
)
def test_an_estate_with_no_inert_answer_is_outside_the_path(
    migrated_session: Session, estate: EstateAnswer
) -> None:
    unit = _bump(migrated_session, "no-answer")

    answer = _answer(migrated_session, unit, source=FakeEstateLandingSource(default=estate))

    assert answer.satisfied is False
    assert answer.refusals[0] == AUTHORITY_APPROVAL_NOT_BOUND


# ---------------------------------------------------------------------------------------------
# The switch governs the policy path and nothing else.
# ---------------------------------------------------------------------------------------------


def test_the_refusal_code_is_pinned() -> None:
    """The bump driver reads this literal as `waiting`, so a rename turns a stop into a finding."""
    assert MERGE_POLICY_PATH_OFF == "merge_policy_path_off"


def test_the_refusal_is_no_substring_of_another_and_contains_none() -> None:
    others = {
        value
        for name, value in vars(admission_module).items()
        if name.isupper() and isinstance(value, str) and value != MERGE_POLICY_PATH_OFF
    }
    assert AUTHORITY_APPROVAL_NOT_BOUND in others
    assert [o for o in others if o in MERGE_POLICY_PATH_OFF or MERGE_POLICY_PATH_OFF in o] == []


def test_the_switch_off_refuses_a_unit_the_policy_path_would_admit(
    migrated_session: Session,
) -> None:
    """Named in place of the not-bound refusal, never beside it."""
    unit = _bump(migrated_session, "off")

    answer = _answer(migrated_session, unit, enabled=False)

    assert answer.satisfied is False
    assert answer.refusals == (MERGE_POLICY_PATH_OFF,)
    assert answer.authority_recognised_by == ()


def test_the_switch_off_reports_beside_another_terms_refusal(migrated_session: Session) -> None:
    """Why a lane may read it as `waiting` only when it is the sole refusal."""
    unit = _bump(migrated_session, "off-open")
    _open_condition(migrated_session, unit)

    answer = _answer(migrated_session, unit, enabled=False)

    assert answer.refusals == ("open_reconciliation_condition", MERGE_POLICY_PATH_OFF)


def test_the_switch_off_refuses_the_act(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "off-act")
    gateway = FakeGateway()

    with pytest.raises(DomainError) as refused:
        _land(migrated_session, unit, gateway, enabled=False)

    assert MERGE_POLICY_PATH_OFF in refused.value.message
    assert gateway.reads == []
    assert gateway.merges == []
    assert _gate_events(migrated_session, unit) == []


def test_a_caller_that_says_nothing_has_the_switch_off(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "silent")

    answer = pr_merge_admission(migrated_session, unit.id, inert_source(), no_record_source())

    assert answer.refusals == (MERGE_POLICY_PATH_OFF,)


def test_the_switch_off_says_nothing_about_a_unit_outside_the_path(
    migrated_session: Session,
) -> None:
    """Turning the switch back on wouldn't help this unit, so it isn't waiting on the switch."""
    unit = _bump(
        migrated_session,
        "off-outside",
        change_class="software-delivery",
        grants=("dependency_updates", "app_changes"),
    )

    answer = _answer(migrated_session, unit, enabled=False)

    assert answer.refusals == (AUTHORITY_APPROVAL_NOT_BOUND,)


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("change_class", ["dependency-update", "software-delivery"])
def test_a_human_approved_merge_ignores_the_switch(
    migrated_session: Session, enabled: bool, change_class: str
) -> None:
    unit = _bump(
        migrated_session,
        "human",
        change_class=change_class,
        grants=("dependency_updates", "app_changes"),
    )
    _approve_authority(migrated_session, unit)

    answer = _answer(migrated_session, unit, enabled=enabled)

    assert answer.satisfied is True
    assert answer.refusals == ()
    assert answer.authority_recognised_by == ()


def test_a_human_approved_act_records_no_lifted_gate(migrated_session: Session) -> None:
    unit = _bump(migrated_session, "human-act")
    _approve_authority(migrated_session, unit)

    record = _land(migrated_session, unit, FakeGateway(), enabled=False)

    assert record.status == "merged"
    assert _gate_events(migrated_session, unit) == []


def test_a_bound_approval_never_consults_the_policy(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = _bump(migrated_session, "human-unread")
    _approve_authority(migrated_session, unit)
    monkeypatch.setattr(authority_gate, "load_factory_policy", _unreadable)
    monkeypatch.setattr(admission_module, "load_factory_policy", _unreadable)

    assert _answer(migrated_session, unit).satisfied is True


def test_the_switch_defaults_off_on_every_entry_point() -> None:
    for function in (admission_for, pr_merge_admission, land_unit_pull_request):
        parameter = inspect.signature(function).parameters["bump_merge_enabled"]
        assert parameter.default is False
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_an_approved_record_does_not_widen_a_redeploying_unit_past_the_approval_term(
    migrated_session: Session,
) -> None:
    """A `live_estate` package in a deploying repository: the record is met, the approval is not."""
    unit = _bump(migrated_session, "routed", reach=("live_estate",))

    answer = admission_for(
        migrated_session,
        unit,
        _revision(migrated_session, unit),
        redeploying_source(),
        approved_record_source(REPOSITORY, 7),
        FixedClock(IN_WINDOW),
        bump_merge_enabled=True,
    )

    assert answer.refusals == (AUTHORITY_APPROVAL_NOT_BOUND,)
