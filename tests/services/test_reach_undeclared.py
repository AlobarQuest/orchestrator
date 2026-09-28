"""An undeclared reach is refused, and no revision is exempt from that.

WS-P2.18 Increment 4 made reach required for admission and grandfathered a named list of revisions
that predated the key. Schema 6 of the policy artifact removed that list (ADR-0047): its last
subject was never broken down, so it was settled and removed in one change. What remains is the
rule itself, held for every revision whatever its id or age, and a loader that refuses a document
still carrying the exemption so it cannot return by accident.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.factory_policy import PACKAGED_ARTIFACT, REACH_UNDECLARED, load_factory_policy
from orchestrator.persistence.models import WorkPackageRevision
from orchestrator.services.execution.dispatch import dispatch_work_unit
from orchestrator.services.execution.reach_admission import (
    REACH_POLICY_UNREADABLE,
    reach_admission_refusal,
)
from tests.services.estate_doubles import inert_source
from tests.services.target_doubles import declared_source
from tests.services.test_dispatch import (
    FakeGitHubDispatcher,
    dispatch_command,
    ready_unit,
    settings,
)
from tests.services.test_factory_policy import VALID

# The last revision the removed `[grandfathered]` table named. Kept only as the id a test proves is
# no longer exempt; nothing in the source tree names it.
FORMERLY_GRANDFATHERED = "f921c842-52b0-46f1-8568-caf5429d2d6b"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "factory-policy.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_undeclared_objection_is_the_whole_answer_for_no_reach() -> None:
    policy = load_factory_policy()

    assert policy.refusals_for(None) == (REACH_UNDECLARED,)
    assert policy.refusals_for(()) == (REACH_UNDECLARED,)
    assert policy.refusals_for(("source_repository",)) == ()  # control


def test_a_document_still_carrying_the_exemption_does_not_load(tmp_path: Path) -> None:
    """The exemption cannot come back by accident: its table is an unknown key now."""
    assert load_factory_policy(write(tmp_path, VALID)).version == 6  # control

    with_table = (
        VALID
        + f"""
[grandfathered]
rationale = "the one revision that predates the key"
decided = "2026-08-01"
revisions = ["{FORMERLY_GRANDFATHERED}"]
"""
    )
    with pytest.raises(DomainError) as raised:
        load_factory_policy(write(tmp_path, with_table))

    assert raised.value.code == "factory_policy_invalid"
    assert "grandfathered" in str(raised.value)


def test_the_schema_that_carried_the_exemption_is_no_longer_read(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as raised:
        load_factory_policy(write(tmp_path, VALID.replace("version = 6", "version = 5")))

    assert raised.value.code == "factory_policy_version_unsupported"


def test_the_shipped_artifact_exempts_nobody() -> None:
    policy = load_factory_policy()

    assert policy.version == 6
    assert "grandfathered" not in policy.report()
    assert FORMERLY_GRANDFATHERED not in PACKAGED_ARTIFACT.read_text(encoding="utf-8")


def test_an_unreadable_artifact_refuses_rather_than_falling_silent(
    migrated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = ready_unit(migrated_session, key="unreadable-policy")
    revision = migrated_session.get(WorkPackageRevision, unit.work_package_revision_id)
    assert revision is not None
    assert reach_admission_refusal(revision) is None  # control

    def unreadable(*_args: object, **_kwargs: object) -> None:
        raise DomainError("factory_policy_invalid", "the policy artifact is invalid", "correct it")

    monkeypatch.setattr(
        "orchestrator.services.execution.reach_admission.load_factory_policy", unreadable
    )

    assert reach_admission_refusal(revision) == REACH_POLICY_UNREADABLE


def test_an_undeclared_reach_is_refused_and_a_declared_one_is_not(
    migrated_session: Session,
) -> None:
    undeclared = ready_unit(migrated_session, key="admission-undeclared", reach=[])
    declared = ready_unit(migrated_session, key="admission-declared")
    github = FakeGitHubDispatcher([])

    refused = dispatch_work_unit(
        migrated_session,
        dispatch_command(undeclared.id),
        settings(),
        github,
        inert_source(),
        target_source=declared_source(),
    )
    admitted = dispatch_work_unit(
        migrated_session,
        dispatch_command(declared.id),
        settings(),
        github,
        inert_source(),
        target_source=declared_source(),
    )

    assert (refused.status, refused.reason_code) == ("blocked", REACH_UNDECLARED)
    assert (admitted.status, admitted.reason_code) == ("dispatched", None)
    assert len(github.calls) == 1
