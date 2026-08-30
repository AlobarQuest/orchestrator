"""The ledger's records, driven through the route an OBSERVER credential actually posts to.

This is the acceptance test for "re-running over the same history changes nothing". It goes
through the HTTP surface and re-reads through a DIFFERENT session, because a service that
flushes without committing returns the right object to its caller and leaves no row behind.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from landing_ledger.model import Check, Landing, RuleApplication, UpdateMetadata
from landing_ledger.record import landing_observation
from orchestrator.persistence.models import Observation

OBSERVER = {"Authorization": "Bearer observer-token", "X-Credential-Key-Id": "observer-key"}
REPO = "AlobarQuest/intent-packages"
LANDED = datetime(2026, 8, 7, 12, 42, 4, tzinfo=UTC)


def gate_landing(**overrides: Any) -> Landing:
    base: dict[str, Any] = {
        "repository": REPO,
        "base_ref": "main",
        "commit": "e931db8d31debfb08fd8f8410a4778f33c437fc1",
        "landed_at": LANDED,
        "title": "chore(deps-dev): bump ruff from 0.15.22 to 0.16.1 (#50)",
        "files": ("pyproject.toml", "uv.lock"),
        "files_changed": 2,
        "pull_request": 50,
        "head_commit": "4437bc985a55c1aa5ad8488067df594c5c1c676c",
        "landed_by": "github-actions[bot]",
        "checks": (
            Check(name="Lint, type-check, and test", conclusion="success", run=31179223856),
        ),
        "rule": RuleApplication(
            path=".github/workflows/dependabot-auto-merge.yml",
            revision="77ab867d1080d18baea3a2b230655c2729716970",
            run=31179223805,
            outcome="success",
            # The gate armed this landing. Since ADR-0037 that, rather than the merging login,
            # is what gives it the rule basis -- so a fixture without it records `unattributed`
            # and this file's whole subject stops being an auto-merged landing.
            arm_outcome="success",
        ),
        "update": UpdateMetadata(
            dependency="ruff", ecosystem="uv", update_type="version-update:semver-minor"
        ),
    }
    return Landing(**{**base, **overrides})


def push_landing() -> Landing:
    return Landing(
        repository=REPO,
        base_ref="main",
        commit="a0563643d1f92d9c9ce5f5806aaa11c53dca1437",
        landed_at=datetime(2026, 8, 7, 16, 25, 36, tzinfo=UTC),
        title="ci: auto-merge GitHub Actions majors, not just patch and minor",
        files=(".github/workflows/dependabot-auto-merge.yml",),
        files_changed=1,
    )


def _stored(engine: Engine) -> list[Observation]:
    """Re-read through a session the write never touched -- the only reader that cannot see an
    uncommitted row."""
    with Session(engine) as session:
        return list(
            session.scalars(select(Observation).order_by(Observation.source_reference)).all()
        )


def test_every_landing_kind_is_accepted_by_the_real_ingestion_route(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The premise made executable: the vocabularies, the bounds and the secret detector all
    admit these records unchanged. Every value is already in `persistence/models.py`."""
    human = gate_landing(commit="358ef93fa0516328255e880a69be94c9f2d7c431", landed_by="AlobarQuest")
    for landing in (gate_landing(), Landing(**{**human.__dict__, "rule": None}), push_landing()):
        response = db_client.post(
            "/api/v1/observations", headers=OBSERVER, json=landing_observation(landing)
        )
        assert response.status_code == 201, response.text

    stored = _stored(migrated_engine)
    assert [row.subject_type for row in stored] == ["repo", "repo", "repo"]
    # One type for every route -- and asserted against the DB rows, so it also proves the
    # `ck_observations_type` CHECK accepts the member migration 0022 added.
    assert {row.observation_type for row in stored} == {"landing"}
    assert {row.facts["permitted_by"]["basis"] for row in stored} == {
        "auto_merge_rule",
        "human",
        "none",
    }


def test_re_running_the_backfill_over_the_same_history_changes_nothing(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    history = [gate_landing(), push_landing()]

    for _ in range(2):
        for landing in history:
            assert (
                db_client.post(
                    "/api/v1/observations", headers=OBSERVER, json=landing_observation(landing)
                ).status_code
                == 201
            )

    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == 2
    first, second = _stored(migrated_engine)
    assert first.normalized_fact_hash != second.normalized_fact_hash
    assert first.facts["permitted_by"]["basis"] == "none"
    assert second.facts["permitted_by"]["basis"] == "auto_merge_rule"
    assert second.facts["permitted_by"]["rule_revision"].startswith("77ab867d")


def test_a_landing_whose_facts_drifted_is_a_loud_conflict_not_a_quiet_second_row(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """The reference is the commit, so a changed fact cannot open a second row for one landing.

    It reaches the same-source/different-facts branch rather than the idempotency-replay branch
    because the key is content-addressed: a plain deterministic key would raise
    `idempotency_conflict` here instead, which is a different error about a different thing.
    """
    assert (
        db_client.post(
            "/api/v1/observations", headers=OBSERVER, json=landing_observation(gate_landing())
        ).status_code
        == 201
    )

    drifted = landing_observation(gate_landing(landed_by="AlobarQuest", rule=None))
    conflict = db_client.post("/api/v1/observations", headers=OBSERVER, json=drifted)

    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "observation_conflict"
    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == 1


def test_the_ledger_is_queryable_as_one_set_per_repository(db_client: TestClient) -> None:
    """`observation_type` splits across the landing kinds, so the ledger's key is the subject --
    which is what a consumer filters on."""
    for landing in (gate_landing(), push_landing()):
        db_client.post("/api/v1/observations", headers=OBSERVER, json=landing_observation(landing))

    listed = db_client.get(
        "/api/v1/observations",
        headers=OBSERVER,
        params={"subject_type": "repo", "subject_reference": REPO, "source_system": "github"},
    )

    assert listed.status_code == 200
    assert len(listed.json()) == 2
