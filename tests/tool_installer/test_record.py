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
    ACTION_ROLLBACK_FAILED,
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
from tool_installer.tools import OCTO, RTK

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


# ------------------------------------------------------------------------------------------------
# The second row, and the one thing it must NOT do to the first.
# ------------------------------------------------------------------------------------------------

# THE REFERENCE THE HEALTHY rtk ROW COMPOSES TODAY, PINNED AS A LITERAL. The record is
# content-addressed over the WHOLE composed observation, `summary` included, and a healthy machine
# writes this same row every pass -- so a reworded rtk clause is an `idempotency_conflict` in
# production for a binary that never moved, on a table with no supersession model and no delete
# route. A second row arriving is exactly the change most likely to reword one by accident, which
# is why this is a literal a reader must deliberately update rather than a value regenerated from
# the producer.
RTK_CURRENT_REFERENCE = (
    "tool-revision:AlobarQuest/rtk@4be91ebe1fc4eaf73688e28cd62782eadb355379:"
    "518e32f7891ac5fd6913236d6e56a79ce846e30249c6f74b8adc48baa4cca2f2"
)


def plugin_installation(**overrides: object) -> Installation:
    base = Installation(
        tool=OCTO,
        install_root="/Users/devon/.claude/plugins",
        installed_revision=HEAD,
        installed_version="11.0.1",
        installed_committed_at=HEAD_AT,
        available_revision=HEAD,
        available_committed_at=HEAD_AT,
        action=ACTION_NONE,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def test_the_rtk_rows_reference_did_not_move_when_the_second_row_arrived() -> None:
    """See `RTK_CURRENT_REFERENCE`. If this fails, an rtk clause was reworded: either put it back,
    or accept that production files one extra row for a machine nothing happened to."""
    record = installation_observation(installation())
    assert record["source_reference"] == RTK_CURRENT_REFERENCE


def test_the_plugin_row_is_a_DIFFERENT_subject_and_cannot_collide() -> None:
    """Uniqueness is on `(source_system, source_reference)` and both rows share the lane, so the
    repository in the reference is what keeps them apart."""
    rtk = installation_observation(installation())
    octo = installation_observation(plugin_installation())
    assert octo["subject_reference"] == "AlobarQuest/claude-octopus"
    assert rtk["source_system"] == octo["source_system"]
    assert rtk["source_reference"] != octo["source_reference"]


def test_an_unchanged_plugin_composes_a_BYTE_IDENTICAL_record() -> None:
    """The replay property, for the second row: the pass runs nightly over a machine that has not
    moved, and `observed_at` is a revision's clock rather than the moment the pass ran."""
    first = installation_observation(plugin_installation())
    second = installation_observation(plugin_installation())
    assert first == second


def test_an_installed_plugin_is_described_as_INSTALLED_and_never_as_running() -> None:
    """THE RESTART GAP, and the summary is where it is either honest or not.

    Claude Code loads plugins at session start, so after a successful pass the new plugin is
    installed and the running session is on whatever it loaded -- for hours. The record must not
    say the machine is running it, because nothing checked that, and it must say when it will
    load, because otherwise a reader supplies the missing half themselves.
    """
    text = summary_of(plugin_installation(action=ACTION_INSTALLED))
    assert "was installed" in text
    assert "loads it at its next start" in text
    assert "running" not in text


def test_a_current_plugin_row_says_INSTALLED_rather_than_running_either() -> None:
    """The same honesty on the ordinary pass, where it is easier to forget: this lane reads what
    Claude Code RECORDED as installed and has no way to know what a live session loaded."""
    text = summary_of(plugin_installation())
    assert "installed on the operator machine is 4be91eb" in text
    assert "running" not in text


def test_the_plugin_row_never_borrows_cargos_wording() -> None:
    """`built`, and "cargo replaces a binary only on success", are claims about a compiled artifact.
    A plugin is copied rather than built, and no equivalent guarantee holds -- so a shared sentence
    would put an assertion nobody checked into the record."""
    for action in (ACTION_INSTALLED, ACTION_ROLLED_BACK, ACTION_INSTALL_FAILED, ACTION_NONE):
        text = summary_of(plugin_installation(action=action))
        assert "built" not in text
        assert "cargo" not in text


def test_the_CARGO_row_still_says_built(  # noqa: D103
) -> None:
    """The control for the test above: the assertion must be about the plugin branch rather than
    about a word that has been removed from both."""
    assert "built from" in summary_of(installation())


def test_a_plugin_that_is_behind_reports_the_same_finding_shape_as_a_binary() -> None:
    """The two rows differ in their wording and must not differ in their VERDICT: `degraded`, and
    the action a permitted pass would have taken."""
    row = plugin_installation(
        installed_revision=OLD, installed_committed_at=OLD_AT, action=ACTION_NOT_PERMITTED
    )
    record = installation_observation(row)
    assert record["status"] == "degraded"
    assert record["facts"]["state"] == "behind"
    assert "this pass was not permitted to act" in record["summary"]


# ---------------------------------------------------------------------------------------------
# 2026-09-07 review fixes: what the record SAYS about a rollback.
# ---------------------------------------------------------------------------------------------

_PASSED = (ProbeResult(command=("installed == pinned",), passed=True, detail=""),)
_FAILED = (ProbeResult(command=("installed == pinned",), passed=False, detail="mismatch"),)


def test_a_rolled_back_PUBLISH_does_not_claim_the_update_failed_to_verify() -> None:
    """`ACTION_ROLLED_BACK` has TWO producers and the summary described only one.

    A publish failure rolls back with every probe PASSING, and the sentence filed said "the
    update did not verify" -- a durable observation contradicting its own evidence in the same
    record, and the first thing a reader sees. Keyed on the probes now, which ARE the evidence.
    """
    row = plugin_installation(action=ACTION_ROLLED_BACK, probes=_PASSED)

    summary = summary_of(row)

    assert "could not be published" in summary
    assert "did not verify" not in summary


def test_a_rolled_back_VERIFICATION_still_says_the_update_did_not_verify() -> None:
    """The control for the pair: with a FAILING probe the original sentence is the true one, so
    the fix must not have replaced one wrong wording with another."""
    row = plugin_installation(action=ACTION_ROLLED_BACK, probes=_FAILED)

    assert "did not verify" in summary_of(row)


def test_a_failed_rollback_says_the_machine_may_be_between_two_versions() -> None:
    """The worst state this lane can reach needs a RECORD, not a traceback. Before 2026-09-07
    `RollbackFailed` escaped the CLI uncaught, so the one pass most needing to say what happened
    said nothing -- and took the other tool's row with it."""
    row = plugin_installation(action=ACTION_ROLLBACK_FAILED, detail="claude exited 9")

    summary = summary_of(row)

    assert "may be between two versions" in summary
    assert "claude exited 9" in summary


def test_a_failed_rollback_is_a_finding() -> None:
    row = plugin_installation(action=ACTION_ROLLBACK_FAILED)

    assert row.is_finding
    assert status_of(row) == ("failed", "critical")
