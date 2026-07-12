"""AC-009: the observation contract, and the two halves that must ship together."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from reconciliation_runner.facts import (
    NormalizedFacts,
    check_observation,
    deploy_observation,
    fact_digest,
    pr_observation,
)

UPDATED_AT = datetime(2026, 7, 10, 9, 15, tzinfo=UTC)
LATER = datetime(2026, 7, 10, 11, 0, tzinfo=UTC)
HEAD = "a" * 40
UNIT = "3f6b6c68-0000-4000-8000-000000000001"


def _pr(**overrides):
    kwargs = {
        "work_unit_id": UNIT,
        "pr_number": 41,
        "head_sha": HEAD,
        "state": "open",
        "merged": False,
        "observed_at": UPDATED_AT,
    }
    kwargs.update(overrides)
    return pr_observation(**kwargs)


def test_the_source_reference_is_content_addressed_and_the_timestamp_is_upstream() -> None:
    body = _pr()

    assert body["source_reference"] == f"pr:41@{HEAD}:{fact_digest(body['facts'])}"
    # UPSTREAM, not the runner's clock -- and it is inside the fact hash, which is why the two
    # halves are inseparable.
    assert body["observed_at"] == UPDATED_AT.isoformat()
    assert body["facts"]["observed_at"] == UPDATED_AT.isoformat()


def test_unchanged_reality_repulled_is_byte_identical() -> None:
    """So a second pass DEDUPS instead of conflicting, and the table does not grow unboundedly."""
    assert _pr() == _pr()


def test_changed_reality_mints_a_new_reference() -> None:
    merged = _pr(state="closed", merged=True, observed_at=LATER)

    assert merged["source_reference"] != _pr()["source_reference"]


def test_a_wall_clock_timestamp_would_change_the_reference() -> None:
    """The failure this contract exists to prevent.

    If observed_at were the PULL time, an unchanged PR would produce a DIFFERENT fact hash every
    pass -- same source, different facts -- which is precisely the observation_conflict branch.
    Every pass. Forever. This test makes that dependency explicit rather than tacit.
    """
    same_reality_pulled_later = _pr(observed_at=LATER)

    assert same_reality_pulled_later["source_reference"] != _pr()["source_reference"]


def test_normalized_facts_reject_a_raw_provider_payload() -> None:
    """GitHub's check payload carries `logs_url`. The orchestrator's secret scanner rejects any
    fact key containing "log", so forwarding a raw payload would fail ingest every single time."""
    with pytest.raises(ValidationError):
        NormalizedFacts(
            observed_at=UPDATED_AT,
            logs_url="https://api.github.com/repos/x/y/check-runs/1/logs",  # type: ignore[call-arg]
        )


def test_each_kind_uses_its_own_namespace_and_subject() -> None:
    check = check_observation(
        work_unit_id=UNIT,
        pr_number=41,
        check_name="Quality",
        head_sha=HEAD,
        conclusion="success",
        observed_at=UPDATED_AT,
    )
    deploy = deploy_observation(
        binding_id="7c1c0f4a-0000-4000-8000-000000000002",
        artifact_digest="sha256:" + "b" * 64,
        deploy_status="succeeded",
        environment="production",
        observed_at=UPDATED_AT,
    )

    assert check["source_reference"].startswith(f"check:Quality@{HEAD}:")
    assert check["subject_type"] == "work_unit"
    assert deploy["source_reference"].startswith("deploy:7c1c0f4a-0000-4000-8000-000000000002@")
    assert deploy["subject_type"] == "release_binding"
