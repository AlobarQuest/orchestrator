"""The one decision surface, rendered at two of the five human gates (AC-020, AC-021, AC-023)."""

import re
import uuid
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orchestrator.api.routes.common import get_landing_source
from orchestrator.kernel.authority import AuthorityBudgets, AuthorityEnvelope
from orchestrator.persistence.models import WorkPackageRevision, WorkUnit
from orchestrator.services.intake.package_intake import register_package_intake
from orchestrator.services.landing.estate_landing import LANDING_REDEPLOYS, EstateAnswer
from tests.api.test_lifecycle_api import HUMAN
from tests.services.test_package_intake import acceptance_criterion, human_actor, intake_command

FACT_LABELS = ("What it does", "What it affects", "Can we back out")

TARGETED_AUTHORITY = AuthorityEnvelope(
    capabilities={"repo.edit": "allowed"},
    budgets=AuthorityBudgets(max_attempts=3, max_llm_calls=4),
    constraints={
        "target_repository": "AlobarQuest/change-manager",
        "mutation_commands": ["uv lock --upgrade"],
    },
    change_class="dependency-update",
)


def _decision_section(page: str) -> str:
    """Only the decision surface's own markup.

    Asserting against the WHOLE page does not discriminate: the unit page already prints the
    target repository, the mutating commands and the change class inside the authority-envelope
    section, so a page-wide assertion passes with no decision surface rendered at all.
    """
    match = re.search(r'<section class="decision"[\s\S]*?</section>', page)
    assert match is not None, "no decision surface was rendered"
    return match.group(0)


def _intake_revision(
    migrated_engine: Engine, *, reach: list[str] | None = None
) -> WorkPackageRevision:
    suffix = uuid.uuid4().hex
    with Session(migrated_engine) as session:
        revision = register_package_intake(
            session,
            intake_command(
                package_id=f"pkg-decision-{suffix}",
                content_hash=f"sha256:{suffix}",
                idempotency_key=f"package-intake-decision-{suffix}",
                acceptance_criteria=(acceptance_criterion("AC-001"),),
                enforcement_snapshot={
                    "title": "Stop the tracker projection drifting",
                    "outcome": {"what": "The tracker projection stops drifting."},
                    "scope": {"in": ["projection"]},
                    "dependencies": [],
                    "applicable_standards": ["STD-1"],
                    **({"reach": reach} if reach is not None else {}),
                },
            ),
            human_actor(),
        )
        session.commit()
        session.refresh(revision)
        session.expunge(revision)
        return revision


def test_the_unit_page_renders_all_three_decision_facts(
    db_client: TestClient, review_unit: WorkUnit
) -> None:
    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert page.status_code == 200
    surface = _decision_section(page.text)
    for label in FACT_LABELS:
        assert label in surface
    assert review_unit.outcome in surface


def test_the_intake_page_renders_all_three_decision_facts(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    # AC-020: ONE component, used at both gates -- not two pages that happen to say similar things.
    revision = _intake_revision(migrated_engine)

    page = db_client.get(f"/review/intakes/{revision.id}", headers=HUMAN)

    assert page.status_code == 200
    surface = _decision_section(page.text)
    for label in FACT_LABELS:
        assert label in surface
    assert "The tracker projection stops drifting." in surface


def test_an_unknown_fact_renders_as_an_explicit_unknown_rather_than_an_omitted_row(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    # AC-021. An omitted row reads as "nothing to worry about"; the truth is "nobody knows yet".
    revision = _intake_revision(migrated_engine)

    page = db_client.get(f"/review/intakes/{revision.id}", headers=HUMAN)

    assert page.status_code == 200
    surface = _decision_section(page.text)
    assert "What it affects" in surface
    assert "Not known" in surface
    assert "no target repository and no mutating command have been chosen yet" in surface


def test_a_unit_whose_envelope_names_a_target_renders_it_as_known(
    db_client: TestClient, migrated_engine: Engine, review_unit: WorkUnit
) -> None:
    with Session(migrated_engine) as session:
        unit = session.get(WorkUnit, review_unit.id)
        assert unit is not None
        unit.authority = TARGETED_AUTHORITY.normalized()
        session.commit()

    page = db_client.get(f"/review/units/{review_unit.id}", headers=HUMAN)

    assert page.status_code == 200
    surface = _decision_section(page.text)
    assert "AlobarQuest/change-manager" in surface
    assert "uv lock --upgrade" in surface
    assert "dependency-update" in surface
    assert "Not known" not in surface


def test_the_intake_surface_names_what_the_cli_verified_and_what_the_server_did_not(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """AC-023. `caller_attested_cli_verified` is a mode name, not an explanation -- the operator
    pressing the button must be able to see what they are attesting to."""
    revision = _intake_revision(migrated_engine)

    page = db_client.get(f"/review/intakes/{revision.id}", headers=HUMAN)

    assert page.status_code == 200
    assert "cli_verified_local_package_hash" in page.text
    assert "cli_verified_approval_lineage" in page.text
    assert "did not re-verify" in page.text


def test_a_declared_reach_reaches_the_gate_a_human_actually_reads(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    """WS-P2.18. The field is decided once because the scheduler and the human read the same one.

    Asserted inside the decision surface, not page-wide: the intake page already prints the whole
    enforcement snapshot, so a page-wide assertion passes with reach rendered nowhere a human
    looks.
    """
    revision = _intake_revision(migrated_engine, reach=["live_estate", "operator_machine"])

    page = db_client.get(f"/review/intakes/{revision.id}", headers=HUMAN)

    assert page.status_code == 200
    surface = _decision_section(page.text)
    assert "hosted application" in surface
    assert "keychain" in surface
    # And the affects row specifically stops being an unknown -- the reversibility row is
    # legitimately still one for this package, so a surface-wide "Not known" check would not
    # discriminate.
    assert "Not known.</strong> No repository is named in a profile field" not in surface


class _Landing:
    """A landing source that answers one way and records what it was asked."""

    def __init__(self, answer: EstateAnswer) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def landing_for(self, github_repo: str) -> EstateAnswer:
        self.asked.append(github_repo)
        return self.answer


def _dependency_update_revision(migrated_engine: Engine) -> WorkPackageRevision:
    suffix = uuid.uuid4().hex
    with Session(migrated_engine) as session:
        revision = register_package_intake(
            session,
            intake_command(
                package_id=f"pkg-bump-{suffix}",
                content_hash=f"sha256:{suffix}",
                idempotency_key=f"package-intake-bump-{suffix}",
                profile="dependency-update",
                acceptance_criteria=(acceptance_criterion("AC-001"),),
                enforcement_snapshot={
                    "title": "Bump httpx",
                    "outcome": {"what": "httpx is current."},
                    "scope": {"in": ["lockfile"]},
                    "dependencies": [],
                    "applicable_standards": ["STD-1"],
                    "profile_fields": {
                        "target_repo": "AlobarQuest/change-manager",
                        "package": "httpx",
                        "from_version": "0.27.0",
                        "to_version": "0.28.1",
                    },
                },
            ),
            human_actor(),
        )
        session.commit()
        session.refresh(revision)
        session.expunge(revision)
        return revision


def test_the_intake_page_asks_the_estate_about_the_profiles_repository_at_render_time(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision = _dependency_update_revision(migrated_engine)
    landing = _Landing(EstateAnswer(LANDING_REDEPLOYS))
    cast(FastAPI, db_client.app).dependency_overrides[get_landing_source] = lambda: landing

    page = db_client.get(f"/review/intakes/{revision.id}", headers=HUMAN)

    assert page.status_code == 200
    surface = _decision_section(page.text)
    assert landing.asked == ["AlobarQuest/change-manager"]
    assert "updates httpx from 0.27.0 to 0.28.1" in surface
    assert "redeploys a running service" in surface
    assert "for a dependency-update change in general" in surface


def test_an_unconfigured_estate_renders_the_landing_as_not_known(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    # The test settings carry no App Brain URL, so the real source answers `source_unconfigured`.
    revision = _dependency_update_revision(migrated_engine)

    page = db_client.get(f"/review/intakes/{revision.id}", headers=HUMAN)

    surface = _decision_section(page.text)
    assert "redeploys anything is not known" in surface
    assert "could not be read (source_unconfigured)" in surface


def test_a_package_naming_no_repository_does_not_ask_the_estate(
    db_client: TestClient, migrated_engine: Engine
) -> None:
    revision = _intake_revision(migrated_engine)
    landing = _Landing(EstateAnswer(LANDING_REDEPLOYS))
    cast(FastAPI, db_client.app).dependency_overrides[get_landing_source] = lambda: landing

    page = db_client.get(f"/review/intakes/{revision.id}", headers=HUMAN)

    assert landing.asked == []
    assert "No target repository is declared" in _decision_section(page.text)
