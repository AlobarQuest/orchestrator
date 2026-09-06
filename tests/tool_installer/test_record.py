"""The observation: what it asserts, and that re-running over unchanged reality changes nothing."""

from __future__ import annotations

import dataclasses
import json

import pytest

from tool_installer.install import (
    ACTION_INSTALL_FAILED,
    ACTION_INSTALLED,
    ACTION_NONE,
    ACTION_NOT_PERMITTED,
    ACTION_ROLLED_BACK,
    ProbeResult,
)
from tool_installer.record import (
    MAX_FACT_BYTES,
    OBSERVATION_TYPE,
    SOURCE_SYSTEM,
    STATE_ABSENT,
    STATE_BEHIND,
    STATE_CURRENT,
    Installation,
    installation_observation,
    observed_at_for,
    reference_for,
    status_of,
    summary_of,
)
from tool_installer.tools import RTK

HEAD = "4be91ebe1fc4eaf73688e28cd62782eadb355379"
OLD = "1111111111111111111111111111111111111111"
HEAD_AT = "2026-09-05T18:00:00Z"
OLD_AT = "2026-08-20T09:30:00Z"


def installation(**overrides: object) -> Installation:
    base = Installation(
        tool=RTK,
        install_root="/Users/devon/.cargo",
        installed_revision=HEAD,
        installed_version="0.48.0",
        installed_committed_at=HEAD_AT,
        available_revision=HEAD,
        available_committed_at=HEAD_AT,
        action=ACTION_NONE,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def test_the_lane_and_the_claim_are_named_by_their_own_members() -> None:
    """`machine_activation`/`activation` is the near miss: it asserts what a WORKING COPY will
    execute at its next start, where this asserts what a compiled BINARY on the machine IS."""
    assert SOURCE_SYSTEM == "tool_installer"
    assert OBSERVATION_TYPE == "tool_revision"


@pytest.mark.parametrize(
    ("row", "state"),
    [
        (installation(), STATE_CURRENT),
        (installation(installed_revision=OLD, installed_committed_at=OLD_AT), STATE_BEHIND),
        (
            installation(
                installed_revision=None, installed_version=None, installed_committed_at=None
            ),
            STATE_ABSENT,
        ),
    ],
)
def test_state_is_derived_from_the_two_revisions_and_nothing_else(
    row: Installation, state: str
) -> None:
    assert row.state == state


def test_a_tool_is_called_current_only_when_it_IS_current() -> None:
    """The guard is structural rather than a clause that remembers to check."""
    assert "head" in summary_of(installation())
    behind = summary_of(installation(installed_revision=OLD, installed_committed_at=OLD_AT))
    assert "not" in behind
    assert OLD[:7] in behind


def test_absent_is_a_finding_because_a_first_install_is_what_this_lane_exists_to_end() -> None:
    row = installation(installed_revision=None, installed_version=None, installed_committed_at=None)
    assert row.is_finding
    assert status_of(row) == ("degraded", "warning")


@pytest.mark.parametrize(
    ("action", "status", "severity"),
    [
        (ACTION_INSTALLED, "passed", "info"),
        (ACTION_NONE, "passed", "info"),
        (ACTION_ROLLED_BACK, "failed", "critical"),
        (ACTION_INSTALL_FAILED, "failed", "critical"),
    ],
)
def test_a_failed_act_is_FAILED_where_merely_being_behind_is_DEGRADED(
    action: str, status: str, severity: str
) -> None:
    """A tool that is behind is an ordinary, recoverable state; an install that went wrong is not.

    The two failing actions are asserted at a CURRENT revision so the verdict can only come from
    the action -- otherwise `behind` would carry the assertion and the action would be untested.
    """
    assert status_of(installation(action=action)) == (status, severity)


def test_being_behind_and_not_permitted_is_degraded_rather_than_failed() -> None:
    """The ordinary state between a merge and the next window."""
    row = installation(
        installed_revision=OLD, installed_committed_at=OLD_AT, action=ACTION_NOT_PERMITTED
    )
    assert status_of(row) == ("degraded", "warning")
    assert "not permitted" in summary_of(row)


# ---------------------------------------------------------------------------------------------
# Replay. The two rules the landing ledger established, by way of the activation sweep.
# ---------------------------------------------------------------------------------------------


def test_observed_at_is_a_REVISIONS_clock_and_never_a_wall_clock() -> None:
    """The orchestrator's replay check hashes the whole command, `observed_at` included, so a wall
    clock gives unchanged reality a new fact hash every pass -- and because the source reference
    is the same, that reaches the same-source/different-facts branch and raises
    `observation_conflict` PERMANENTLY, from the second pass onward.
    """
    assert observed_at_for(installation()) == HEAD_AT
    behind = installation(installed_revision=OLD, installed_committed_at=OLD_AT)
    assert observed_at_for(behind) == OLD_AT


def test_a_revision_with_no_date_falls_back_to_the_available_ones_still_fact_derived() -> None:
    """A first install has no installed revision; a force-pushed one has no date. Both fall back
    to a clock that is still a function of the facts and moves only when the fork's head moves."""
    absent = installation(
        installed_revision=None, installed_version=None, installed_committed_at=None
    )
    assert observed_at_for(absent) == HEAD_AT
    forced = installation(installed_revision=OLD, installed_committed_at=None)
    assert observed_at_for(forced) == HEAD_AT


def test_an_unchanged_machine_composes_a_BYTE_IDENTICAL_record() -> None:
    """The replay property, asserted on the whole payload rather than on the reference alone."""
    first = installation_observation(installation())
    second = installation_observation(installation())
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_reference_IS_the_idempotency_key() -> None:
    """Section 5.2's decision: the identity IS the content, so spelling two strings for one
    concept would be a second copy of it."""
    record = installation_observation(installation())
    assert record["idempotency_key"] == record["source_reference"]


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"action": ACTION_INSTALLED}, id="the action"),
        pytest.param({"installed_version": "0.49.0"}, id="the version"),
        pytest.param({"available_revision": "c" * 40}, id="the available revision"),
        pytest.param({"install_root": "/tmp/elsewhere"}, id="the install root"),
    ],
)
def test_any_change_at_all_APPENDS_rather_than_conflicting(changed: dict[str, object]) -> None:
    """THE DIGEST COVERS THE WHOLE COMPOSED RECORD, NOT JUST `facts`, AND THIS IS WHY.

    Because the reference is also the idempotency key, the server's FIRST lookup is by that key,
    and on a hit it compares the ENTIRE stored command -- `summary`, `status`, `severity`,
    `source_url` and more, every one producer-derived and none of them in `facts`. Digesting
    `facts` alone is the obvious reading and is a strict subset of what the orchestrator compares,
    so rewording one clause of `summary_of` would make the next pass an `idempotency_conflict` for
    a machine whose tool had not moved -- which for a healthy machine is every pass.
    """
    base = installation_observation(installation())
    moved = installation_observation(installation(**changed))  # type: ignore[arg-type]
    assert moved["source_reference"] != base["source_reference"]


def test_a_field_OUTSIDE_facts_moves_the_reference() -> None:
    """THE DISCRIMINATING CONTROL for whole-record digesting, and the one a mutation review found
    missing.

    Every field a real `Installation` can move -- action, version, revision, install root -- is
    ALSO in `facts`, so the test above passes just as well against a digest over `facts` alone: it
    is correct about the wrong noun. Mutating `reference_for` to digest `record["facts"]` survived
    it, and survives any test built from Installations.

    The property is about `reference_for`'s CONTRACT, so it is asserted there: two records
    differing only in `summary` -- producer-derived, compared by the server, and absent from
    `facts` -- must not share a reference. Rewording one clause of `summary_of` would otherwise
    make the next pass an `idempotency_conflict` for a machine whose tool had not moved.
    """
    row = installation()
    record = {
        key: value
        for key, value in installation_observation(row).items()
        if key not in {"idempotency_key", "source_reference"}
    }
    reworded = {**record, "summary": "a different sentence entirely"}
    assert record["facts"] == reworded["facts"]
    assert reference_for(row, record) != reference_for(row, reworded)


def test_the_record_carries_no_key_the_secret_detector_reads_as_metadata() -> None:
    """`services/observations.py::SECRET_KEY_PARTS` matches nine substrings against key NAMES, so
    a key merely CALLED something like `install_log` is refused on its name alone."""
    forbidden = ("token", "credential", "key", "secret", "password", "log", "auth")
    record = installation_observation(installation(action=ACTION_INSTALLED))

    def walk(value: object) -> list[str]:
        if isinstance(value, dict):
            return [k for k in value] + [n for v in value.values() for n in walk(v)]
        if isinstance(value, list):
            return [n for v in value for n in walk(v)]
        return []

    offenders = [
        name for name in walk(record["facts"]) if any(part in name.lower() for part in forbidden)
    ]
    assert offenders == []


def test_the_encoded_facts_are_inside_the_orchestrators_bound() -> None:
    """Small by SHAPE rather than by trimming: every value is a name, a path, a revision, a
    version, a state, an action or a bounded probe line."""
    row = installation(
        action=ACTION_ROLLED_BACK,
        probes=tuple(
            ProbeResult(command=("gain",), passed=False, detail="x" * 200) for _ in range(6)
        ),
        detail="y" * 600,
    )
    encoded = json.dumps(installation_observation(row)["facts"], separators=(",", ":"))
    assert len(encoded.encode("utf-8")) <= MAX_FACT_BYTES
