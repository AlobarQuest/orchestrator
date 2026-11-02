"""The verifier lane over a bump unit, through the real routes and the real evaluator.

Only GitHub is substituted: it answers per check name. Everything else is the orchestrator the
lane talks to in production, so what these prove is what the evaluator does with the rows the lane
files, not what a fake says it does.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.persistence.models import Evidence
from orchestrator.services.verifier.github_checks import (
    CheckObservation,
    CheckObservationError,
)
from tests.api.test_verifier_api import substitute_github
from tests.fixtures.named_check import (
    HEAD_SHA,
    PR_NUMBER,
    TARGET_REPOSITORY,
    bind_dispatched_pull_request,
    mapped_submitted_unit,
    observed_job,
    record_worker_evidence,
)
from unit_verifier.cli import run_pass
from unit_verifier.client import VerifierClient

BUMP_AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
    constraints={"target_repository": TARGET_REPOSITORY},
    change_class="dependency-update",
)


class GitHubByCheck:
    """A GitHub whose answer depends on the job name; an unlisted name has not concluded."""

    def __init__(self, conclusions: dict[str, str]) -> None:
        self.conclusions = conclusions
        self.asked: list[str] = []

    def observe(self, *, repository: str, head_sha: str, check_name: str) -> CheckObservation:
        self.asked.append(check_name)
        conclusion = self.conclusions.get(check_name)
        if conclusion is None:
            raise CheckObservationError("not_concluded", "running")
        return CheckObservation(
            check_name=check_name, conclusion=conclusion, jobs=(observed_job(conclusion),)
        )


class ThroughTestClient(httpx.BaseTransport):
    def __init__(self, client: TestClient) -> None:
        self._client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        answer = self._client.request(
            request.method,
            request.url.raw_path.decode(),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)


def _submitted_bump(engine: Engine, key: str) -> uuid.UUID:
    with Session(engine) as session:
        unit = mapped_submitted_unit(
            session,
            key=key,
            evidence_type="automated_check",
            ac_id="AC-001",
            authority=BUMP_AUTHORITY,
        )
        record_worker_evidence(
            session,
            unit,
            ac_id="AC-001",
            evidence_type="runner.pr.opened",
            payload={"pr_number": PR_NUMBER, "head_sha": HEAD_SHA},
            idempotency_key=f"{key}-worker-evidence",
        )
        bind_dispatched_pull_request(session, unit)
        return unit.id


def _lane(
    db_client: TestClient, conclusions: dict[str, str], *required: str, pass_id: str | None = None
):
    github = GitHubByCheck(conclusions)
    substitute_github(db_client, github)  # type: ignore[arg-type]
    client = VerifierClient(
        base_url="https://sds.example",
        credential_key_id="verifier-key",
        token="verifier-token",
        transport=ThroughTestClient(db_client),
    )
    outcomes = run_pass(client, read_required_checks=lambda repository: required, pass_id=pass_id)
    return github, outcomes


def _filed_check_names(engine: Engine, unit_id: uuid.UUID) -> list[str]:
    with Session(engine) as session:
        rows = session.scalars(
            select(Evidence)
            .where(
                Evidence.work_unit_id == unit_id,
                Evidence.evidence_type == "verifier.github.named_check",
            )
            .order_by(Evidence.recorded_at)
        ).all()
        return [(row.payload or {})["check_name"] for row in rows]


def test_every_required_check_green_completes_the_unit(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted_bump(migrated_engine, "bump-green")

    _, outcomes = _lane(db_client, {"Lint": "success", "test": "success"}, "test", "Lint")

    assert [o.result for o in outcomes] == ["completed"]
    assert _filed_check_names(migrated_engine, unit_id) == ["Lint", "test"]


def test_a_failed_required_check_stops_filing_and_fails_the_criterion(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted_bump(migrated_engine, "bump-first-fails")

    github, outcomes = _lane(db_client, {"Lint": "failure", "test": "success"}, "test", "Lint")

    assert github.asked == ["Lint"]  # the second check is never asked of GitHub, let alone filed
    assert _filed_check_names(migrated_engine, unit_id) == ["Lint"]
    assert [o.result for o in outcomes] == ["revision_required"]


def test_a_first_success_then_a_second_failure_fails_the_criterion(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted_bump(migrated_engine, "bump-second-fails")

    _, outcomes = _lane(db_client, {"Lint": "success", "test": "failure"}, "test", "Lint")

    assert _filed_check_names(migrated_engine, unit_id) == ["Lint", "test"]
    assert [o.result for o in outcomes] == ["revision_required"]


def test_a_pending_check_waits_then_a_later_pass_refiles_from_the_start_and_completes(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted_bump(migrated_engine, "bump-waits")

    _, first = _lane(db_client, {"Lint": "success"}, "test", "Lint")
    assert [o.result for o in first] == ["waiting"]

    _, second = _lane(db_client, {"Lint": "success", "test": "success"}, "test", "Lint")

    assert [o.result for o in second] == ["completed"]
    # Each pass observes afresh, so Lint is filed again; the newest row for the criterion is test's.
    assert _filed_check_names(migrated_engine, unit_id) == ["Lint", "Lint", "test"]


def test_a_check_rerun_red_between_passes_is_seen(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    unit_id = _submitted_bump(migrated_engine, "bump-rerun-red")

    _, first = _lane(db_client, {"Lint": "success"}, "test", "Lint", pass_id="one")
    assert [o.result for o in first] == ["waiting"]

    # Lint is re-run on the same head and fails; test would now succeed.
    github, second = _lane(
        db_client, {"Lint": "failure", "test": "success"}, "test", "Lint", pass_id="two"
    )

    assert github.asked == ["Lint"]
    assert _filed_check_names(migrated_engine, unit_id) == ["Lint", "Lint"]
    assert [o.result for o in second] == ["revision_required"]


@pytest.mark.parametrize("conclusion", ["skipped", "neutral", "cancelled"])
def test_a_check_that_did_not_succeed_does_not_meet_the_criterion(
    db_client: TestClient, migrated_engine: Engine, conclusion: str
) -> None:
    unit_id = _submitted_bump(migrated_engine, f"bump-{conclusion}")

    github, outcomes = _lane(db_client, {"Lint": conclusion, "test": "success"}, "test", "Lint")

    assert github.asked == ["Lint"]
    assert _filed_check_names(migrated_engine, unit_id) == ["Lint"]
    assert [o.result for o in outcomes] == ["revision_required"]
