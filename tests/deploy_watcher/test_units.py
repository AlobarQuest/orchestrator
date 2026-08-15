"""ADR-0022's second half: attributing a rollout to the work unit whose landing caused it.

THE PROPERTY THESE PIN is that the commit's claim never decides anything. A `SDS-Unit:` trailer is
written by factory-runner, which is the party whose compliance the observation would be about, so
an observation recorded on the trailer alone would let a commit choose its own subject. Every
`binds` case below is a way for the orchestrator's durable record to disagree with the claim, and
each one must refuse.

The second property is the boring one that goes wrong quietly: the row an hourly pass writes must
be the SAME row every hour for unchanged reality, or the orchestrator's different-facts branch
turns a healthy schedule into a nightly conflict.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from deploy_watcher.model import Merge, Rollout, Run
from deploy_watcher.units import (
    LANDED_MERGE_STATUS,
    OBSERVATION_TYPE,
    SOURCE_SYSTEM,
    STATUS_INCONCLUSIVE,
    SUBJECT_TYPE,
    UnitLanding,
    binds,
    claimed_unit,
    status_for,
    unit_observation,
)

UNIT = "1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f"
MERGE_SHA = "2ba9f7f2ef4121aef69153fc2e6dd248cfdcf33b"
OTHER_SHA = "06f9268b5160d3d064f1f2e63d7f36faa2cb06df"
REPOSITORY = "AlobarQuest/change-manager"


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------


def test_the_trailer_is_read_from_the_commit_message() -> None:
    message = f"bump alembic (#50)\n\nSDS-Unit: {UNIT}\nSDS-Package-Rev: 4\n"
    assert claimed_unit(message) == UNIT


@pytest.mark.parametrize(
    "message",
    [
        None,
        "",
        "bump alembic (#50)\n\nno trailers here\n",
        f"we should look at unit {UNIT} some time\n",
        # A malformed id is not a claim either.
        "SDS-Unit: not-a-uuid\n",
        # THE ANCHORING CASE, and a mutation proved it necessary: without `^…$` the two lines
        # below are claims, and this one is reachable. A squash carries the branch's commit
        # messages verbatim, so a revert or a follow-up that QUOTES the trailer in a sentence
        # would attribute its own rollout to the unit it is talking about.
        f'Revert "bump alembic" — this undoes SDS-Unit: {UNIT} which landed yesterday\n',
        f"    SDS-Unit: {UNIT} (quoted from the reverted commit)\n",
    ],
)
def test_an_absent_or_malformed_claim_is_None(message: str | None) -> None:
    """No claim is the ORDINARY case: almost every landing the watcher sees is an update the bot
    opened, and whether a trailer survives a squash is a repository setting."""
    assert claimed_unit(message) is None


# ---------------------------------------------------------------------------
# The binding — every case here is the durable record refusing the claim
# ---------------------------------------------------------------------------


def _history(**overrides) -> list[dict]:
    payload = {
        "status": LANDED_MERGE_STATUS,
        "repository": REPOSITORY.lower(),
        "pr_number": 50,
        "merge_commit_sha": MERGE_SHA,
        "head_sha": "aaaaaaaa" + "0" * 32,
    }
    payload.update(overrides)
    return [
        {"action": "work_unit.claimed", "payload": {}},
        {"action": "pr_merge.recorded", "payload": payload},
    ]


def _binds(history: list[dict], **overrides) -> bool:
    kwargs = {
        "repository": REPOSITORY,
        "pull_request_number": 50,
        "merge_commit_sha": MERGE_SHA,
    }
    kwargs.update(overrides)
    return binds(history, **kwargs)


def test_a_matching_record_binds() -> None:
    assert _binds(_history()) is True


def test_the_repository_is_case_folded_on_both_sides() -> None:
    """The orchestrator lowercases what it records; change-manager stores whatever the proposer
    sent, and production holds BOTH spellings today. An exact comparison would refuse a real
    binding."""
    assert _binds(_history(repository="ALOBARQUEST/CHANGE-MANAGER")) is True
    assert _binds(_history(), repository=REPOSITORY.upper()) is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        # Neither of the other two statuses asserts the orchestrator MADE this landing:
        # `already_merged` also covers a pull request somebody else had landed before the call,
        # and `refused` also covers the genuinely ambiguous outcome.
        ("status", "already_merged"),
        ("status", "refused"),
        ("repository", "alobarquest/brain"),
        ("pr_number", 51),
        ("merge_commit_sha", OTHER_SHA),
    ],
)
def test_a_record_that_disagrees_on_any_coordinate_does_not_bind(field: str, value: object) -> None:
    assert _binds(_history(**{field: value})) is False


def test_a_unit_with_no_merge_record_does_not_bind() -> None:
    assert _binds([{"action": "work_unit.completed", "payload": {}}]) is False
    assert _binds([]) is False


def test_a_malformed_event_is_skipped_rather_than_raising() -> None:
    """A history is read from a service; a row that is not the shape expected must not take the
    whole pass down, and must not bind either."""
    assert _binds([{"action": "pr_merge.recorded", "payload": "not-an-object"}]) is False
    assert _binds([{"payload": _history()[1]["payload"]}]) is False


def test_a_malformed_row_alongside_a_matching_one_still_binds() -> None:
    assert _binds([{"action": "pr_merge.x", "payload": None}, *_history()]) is True


# ---------------------------------------------------------------------------
# The observation
# ---------------------------------------------------------------------------


def _rollout(*, conclusion: str = "success", run: bool = True) -> Rollout:
    merge = Merge(
        repository=REPOSITORY,
        number=50,
        merged=True,
        merge_commit_sha=MERGE_SHA,
        base_ref="main",
        merged_at=datetime(2026, 8, 13, 8, 55, tzinfo=UTC),
    )
    return Rollout(
        merge=merge,
        workflow_path=".github/workflows/deploy.yml",
        workflow_revision="a47d4b187c93971a5b5915ce87a963bd4ef35e30",
        attestation="revision_confirmed",
        run=Run(
            run_id=31685940716,
            run_attempt=1,
            run_url="https://github.com/AlobarQuest/change-manager/actions/runs/31685940716",
            status="completed",
            conclusion=conclusion,
            started_at=datetime(2026, 8, 13, 8, 56, tzinfo=UTC),
            concluded_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
        )
        if run
        else None,
        settled=True,
        rollout_job="build-and-deploy",
        rollout_job_conclusion=conclusion,
        trigger_step="Trigger Coolify redeploy",
        trigger_step_conclusion=conclusion,
    )


def _landing() -> UnitLanding:
    return UnitLanding(
        work_unit_id=UNIT,
        repository=REPOSITORY,
        pull_request_number=50,
        merge_commit_sha=MERGE_SHA,
    )


def test_the_observation_is_subject_to_the_work_unit() -> None:
    """The whole point: the traceability chain's observation hop filters on this pair."""
    body = unit_observation(_landing(), _rollout(), verdict="success", production_reached="yes")
    assert body["subject_type"] == SUBJECT_TYPE == "work_unit"
    assert body["subject_reference"] == UNIT
    assert body["source_system"] == SOURCE_SYSTEM
    assert body["observation_type"] == OBSERVATION_TYPE


def test_the_vocabularies_are_the_orchestrators_own() -> None:
    """`success` is NOT an orchestrator observation status — the two vocabularies read as one and
    are not, which is the cross-boundary mismatch this estate keeps rediscovering."""
    from orchestrator.persistence.models import (
        OBSERVATION_SEVERITIES,
        OBSERVATION_SOURCE_SYSTEMS,
        OBSERVATION_STATUSES,
        OBSERVATION_SUBJECT_TYPES,
        OBSERVATION_TRUST_CLASSIFICATIONS,
        OBSERVATION_TYPES,
    )

    body = unit_observation(_landing(), _rollout(), verdict="success", production_reached="yes")
    assert body["status"] == "passed" and "success" not in OBSERVATION_STATUSES
    assert body["source_system"] in OBSERVATION_SOURCE_SYSTEMS
    assert body["subject_type"] in OBSERVATION_SUBJECT_TYPES
    assert body["observation_type"] in OBSERVATION_TYPES
    assert body["trust_classification"] in OBSERVATION_TRUST_CLASSIFICATIONS
    assert body["severity"] in OBSERVATION_SEVERITIES


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [("success", "passed"), ("failed", "failed"), ("unknown", "unknown"), ("absent", "unknown")],
)
def test_an_unrecognised_verdict_degrades_rather_than_reading_green(
    verdict: str, expected: str
) -> None:
    assert status_for(verdict) == expected
    assert status_for("something_invented_later") == STATUS_INCONCLUSIVE


def test_a_failed_rollout_is_STILL_observed() -> None:
    """Facts and decisions are split (ADR-0021): the SETTLEMENT is conditioned on success, the
    observation is not. A rollout that failed is exactly what a unit's chain should carry."""
    body = unit_observation(
        _landing(), _rollout(conclusion="failure"), verdict="failed", production_reached="unknown"
    )
    assert body["status"] == "failed"
    assert body["facts"]["rollout_verdict"] == "failed"


def test_the_row_is_stable_across_passes() -> None:
    """UPSTREAM'S CLOCK, never the pass's. With a wall-clock timestamp every hourly pass would
    recompute a different fact hash for unchanged reality and conflict, forever."""
    first = unit_observation(_landing(), _rollout(), verdict="success", production_reached="yes")
    second = unit_observation(_landing(), _rollout(), verdict="success", production_reached="yes")
    assert first == second
    assert first["observed_at"] == "2026-08-13T09:00:00+00:00"


def test_a_RE_RUN_APPENDS_rather_than_colliding_with_the_first_attempt() -> None:
    """KILLED A DESIGN, and it was found by two reviewers independently.

    The first version copied the landing ledger's rule — a reference that is NOT content-addressed
    — which is right for a commit on a branch and wrong for a rollout. The orchestrator refuses a
    second observation at the same `source_reference` with different facts and has no supersession
    route, so the first re-run would have wedged the watcher at exit 3 forever while the successful
    attempt was never attributed to the unit. Rollout re-runs are the ordinary case in exactly the
    two repositories this can fire on.
    """
    first = unit_observation(
        _landing(), _rollout(conclusion="failure"), verdict="failed", production_reached="unknown"
    )
    green = _rollout()
    assert green.run is not None
    rerun = replace(green, run=replace(green.run, run_attempt=2))
    second = unit_observation(_landing(), rerun, verdict="success", production_reached="yes")
    assert first["source_reference"] != second["source_reference"]
    assert first["idempotency_key"] != second["idempotency_key"]


def test_a_TRANSCRIBED_workflow_revision_appends_rather_than_colliding() -> None:
    """The other route to the same wedge, and the one that does not need a re-run at all.

    `workflow_attests` is a property of the BYTES, and transcribing a revision nobody had
    classified moves it from `unknown` to a real level for an attempt that has not changed. That is
    ordinary maintenance, and under a reference that did not carry the digest it would have been
    permanent.
    """
    unknown = replace(_rollout(), attestation="unknown")
    before = unit_observation(_landing(), unknown, verdict="success", production_reached="yes")
    after = unit_observation(_landing(), _rollout(), verdict="success", production_reached="yes")
    assert before["source_reference"] != after["source_reference"]


def test_the_reference_folds_case_so_one_attempt_is_one_row() -> None:
    """change-manager holds both spellings of the repository; a reference that did not fold would
    open a second row for the same rollout the day a record was proposed differently."""
    lower = unit_observation(
        UnitLanding(UNIT, REPOSITORY.lower(), 50, MERGE_SHA),
        _rollout(),
        verdict="success",
        production_reached="yes",
    )
    upper = unit_observation(_landing(), _rollout(), verdict="success", production_reached="yes")
    assert lower["source_reference"] == upper["source_reference"]
    assert lower["idempotency_key"] == upper["idempotency_key"]


def test_the_reference_stays_inside_the_orchestrators_bound() -> None:
    """512 characters (`services/observations.py::MAX_REF`), and a reference that overran it would
    be a `observation_invalid` on every pass rather than on none."""
    from orchestrator.services.observations import MAX_REF

    body = unit_observation(_landing(), _rollout(), verdict="success", production_reached="yes")
    assert 0 < len(body["source_reference"]) <= MAX_REF
    assert 0 < len(body["idempotency_key"]) <= MAX_REF


def test_a_rollout_with_no_run_falls_back_to_the_merge_time() -> None:
    """`rollout_never_ran` is a real observed outcome and it has no run to date it by. The merge
    time is the next most stable fact about the same event, and is still not this pass's clock."""
    body = unit_observation(
        _landing(), _rollout(run=False), verdict="absent", production_reached="no"
    )
    assert body["observed_at"] == "2026-08-13T08:55:00+00:00"
    assert body["status"] == "unknown"
    assert "run_id" not in body["facts"]


def test_the_facts_carry_nothing_dated_or_counted() -> None:
    """Every string a row carries is frozen at the first row carrying it, so a value that could be
    corrected later is an `observation_conflict` on a landing where nothing changed."""
    facts = unit_observation(_landing(), _rollout(), verdict="success", production_reached="yes")[
        "facts"
    ]
    assert set(facts) == {
        "rollout_verdict",
        "production_reached",
        "workflow_attests",
        "workflow_path",
        "merge_commit",
        "pull_request",
        "repository",
        "run_id",
        "run_attempt",
        "run_conclusion",
    }
