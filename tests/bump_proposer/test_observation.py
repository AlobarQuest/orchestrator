"""The durable row, and the properties that decide whether this producer can run twice.

A producer whose row moves over unchanged reality wedges permanently: the orchestrator refuses a
second observation at the same reference with different facts, there is no supersession model and
no delete route, so every subsequent pass fails. This producer is scheduled daily and sees every
open update on every pass, so there is no lookback to shrink and no window in which such a state
would clear. These pin the answer.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bump_proposer.observation import (
    MAX_IDEMPOTENCY_KEY,
    ObservationUncomposable,
    bump_observation,
    reference_for,
    summary_of,
)
from landing_ledger.model import Check, PendingUpdate, UpdateMetadata
from landing_ledger.titles import bump_of

REPOSITORY = "AlobarQuest/infraops-mcp-server"
TITLE = "build(deps): bump zod from 3.25.76 to 4.4.3"
OPENED = datetime(2026, 8, 1, 9, 30, tzinfo=UTC)


def _bump(title: str = TITLE):
    parsed = bump_of(title)
    assert parsed is not None, title
    return parsed


def _pending(
    *,
    title: str = TITLE,
    repository: str = REPOSITORY,
    dependency: str = "zod",
    head_commit: str = "b" * 40,
    armed: bool = False,
    checks: tuple[Check, ...] = (),
    concluded_at: datetime | None = None,
) -> PendingUpdate:
    return PendingUpdate(
        repository=repository,
        number=71,
        head_commit=head_commit,
        opened_at=OPENED,
        armed=armed,
        title=title,
        checks=checks,
        update=UpdateMetadata(
            dependency=dependency,
            ecosystem="npm_and_yarn",
            update_type="version-update:semver-major",
        ),
        last_concluded_at=concluded_at,
    )


def test_two_passes_over_unchanged_reality_compose_an_IDENTICAL_row() -> None:
    """The replay property. Anything that moves here -- a wall clock most of all -- turns the
    second pass into a permanent `observation_conflict`."""
    assert bump_observation(_pending(), _bump()) == bump_observation(_pending(), _bump())


def test_nothing_that_MOVES_under_a_stable_bump_reaches_the_row() -> None:
    """The sharper half of the replay property, and the one a wall clock would not have caught.

    A rebase moves `head_commit`, arming moves `armed`, and re-running a job moves the check
    conclusions and `last_concluded_at`. Every one of them happens to a pull request that is
    still the same bump -- so a row carrying any of them would refuse on the next pass while the
    delta it describes had not changed at all.
    """
    quiet = _pending()
    busy = _pending(
        head_commit="f" * 40,
        armed=True,
        checks=(Check(name="Quality", conclusion="failure", run=9),),
        concluded_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    assert bump_observation(quiet, _bump()) == bump_observation(busy, _bump())


def test_observed_at_is_when_the_BUMP_APPEARED_rather_than_when_the_pass_ran() -> None:
    row = bump_observation(_pending(), _bump())

    assert row["observed_at"] == OPENED.isoformat()


@pytest.mark.parametrize(
    "moved",
    [
        "build(deps): bump zod from 3.25.76 to 4.5.4",
        "build(deps): bump zod from 3.26.0 to 4.4.3",
    ],
    ids=["target moved", "base moved"],
)
def test_a_moved_version_composes_a_DIFFERENT_reference_rather_than_conflicting(moved) -> None:
    """The update bot rewrites a pull request IN PLACE, so a stable number carries different
    bumps over time. BOTH versions are in the reference: the common case is a newer target, and
    the one that would otherwise refuse this pull request forever is a moved base.
    """
    assert reference_for(_pending(), _bump()) != reference_for(_pending(title=moved), _bump(moved))


def test_a_different_dependency_in_one_repository_is_a_different_reference() -> None:
    first = reference_for(_pending(dependency="zod"), _bump())
    second = reference_for(_pending(dependency="typescript"), _bump())

    assert first != second


def test_the_row_asserts_the_FACT_and_takes_no_position_on_what_should_happen_to_it() -> None:
    """The observation is the durable fact; the change record is a decision about it.

    Whether the estate lands this bump by itself is read from a policy version that MOVES, and a
    fact carrying that judgment would refuse the first time the declaration changed under a bump
    that had not. It is also the wrong claim: this row says an update exists, and `_reasoning` on
    the record -- which a person approves -- says what should be done about it.
    """
    row = bump_observation(_pending(), _bump())
    prose = (row["summary"] + " " + str(row["facts"])).lower()

    for judgment in ("cascade", "factory", "refus", "permit", "policy", "unattended", "work"):
        assert judgment not in prose, judgment


def test_the_facts_carry_the_delta_and_nothing_dated_or_counted() -> None:
    row = bump_observation(_pending(), _bump())

    assert row["facts"] == {
        "repository": REPOSITORY,
        "pull_request": 71,
        "dependency": "zod",
        "ecosystem": "npm_and_yarn",
        "from_version": "3.25.76",
        "to_version": "4.4.3",
        "kind": _bump().kind,
    }


def test_the_summary_names_both_versions_because_a_reader_needs_the_delta() -> None:
    line = summary_of(_pending(), _bump())

    assert "3.25.76" in line
    assert "4.4.3" in line
    assert REPOSITORY in line


def test_an_over_long_key_is_a_NAMED_refusal_rather_than_a_422_nobody_can_read() -> None:
    """`idempotency_key` is `max_length=200` on the route's own request model, so exceeding it is
    a FastAPI 422 raised BEFORE any service code runs -- no named error, and no service test can
    reach it. A refusal here costs one pull request its row and says which one.
    """
    long_repository = "AlobarQuest/" + "a" * 120
    with pytest.raises(ObservationUncomposable, match="over the 200"):
        bump_observation(_pending(repository=long_repository, dependency="b" * 60), _bump())


def test_a_realistic_subject_is_COMFORTABLY_inside_that_cap() -> None:
    """The control that keeps the guard above from being satisfied by refusing everything -- and
    the reason the cap is worth checking rather than assuming: the estate's longest real
    repository and dependency names together leave only tens of characters of headroom."""
    row = bump_observation(
        _pending(
            repository="AlobarQuest/security-standards", dependency="@modelcontextprotocol/sdk"
        ),
        _bump(),
    )

    assert len(row["idempotency_key"]) <= MAX_IDEMPOTENCY_KEY


# ---------------------------------------------------------------------------------------------
# THE VOCABULARY THIS PAYLOAD MUST SATISFY LIVES IN ANOTHER PROGRAM. The revision watcher shipped
# filing NOTHING for exactly this reason: every field below is a closed vocabulary backed by a
# CHECK constraint, two of them had no member for that producer, and it survived the whole gate
# because every test that composed the payload also mocked the client that would have refused it.
#
# The orchestrator's tuples are IMPORTABLE here, so this needs no HTTP client and cannot drift.
# ---------------------------------------------------------------------------------------------


def test_every_vocabulary_field_this_payload_sets_is_one_the_orchestrator_accepts() -> None:
    from orchestrator.persistence.models import (
        OBSERVATION_SEVERITIES,
        OBSERVATION_SOURCE_SYSTEMS,
        OBSERVATION_STATUSES,
        OBSERVATION_SUBJECT_TYPES,
        OBSERVATION_TRUST_CLASSIFICATIONS,
        OBSERVATION_TYPES,
    )

    row = bump_observation(_pending(), _bump())

    assert row["source_system"] in OBSERVATION_SOURCE_SYSTEMS
    assert row["observation_type"] in OBSERVATION_TYPES
    assert row["subject_type"] in OBSERVATION_SUBJECT_TYPES
    assert row["trust_classification"] in OBSERVATION_TRUST_CLASSIFICATIONS
    assert row["status"] in OBSERVATION_STATUSES
    assert row["severity"] in OBSERVATION_SEVERITIES


def test_the_payload_satisfies_the_ROUTES_OWN_REQUEST_MODEL() -> None:
    """The second rule set. FastAPI answers before any service code runs, so a field the request
    model rejects is an HTTP 422 that no named error and no service test can reach."""
    from orchestrator.api.schemas import ObservationCommandModel

    ObservationCommandModel.model_validate(bump_observation(_pending(), _bump()))


def test_the_key_cap_mirrored_here_is_the_one_the_request_model_actually_declares() -> None:
    """A mirrored constant nothing checks is how a vocabulary drifts, and this one decides whether
    a refusal is named or arrives as a 422 naming a field location."""
    from orchestrator.api.schemas import CommandBase

    declared = CommandBase.model_fields["idempotency_key"].metadata
    caps = [getattr(item, "max_length", None) for item in declared]

    assert MAX_IDEMPOTENCY_KEY in caps


def test_the_repository_is_spelled_the_way_EVERY_OTHER_repo_subject_spells_it() -> None:
    """One repository must be ONE subject across the four producers that key on it. A second
    spelling would make it two, and no query would join them."""
    from landing_ledger.record import SUBJECT_TYPE as LEDGER_SUBJECT_TYPE

    row = bump_observation(_pending(), _bump())

    assert row["subject_type"] == LEDGER_SUBJECT_TYPE
    assert row["subject_reference"] == REPOSITORY
