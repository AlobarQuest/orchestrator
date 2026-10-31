"""The walk of rotation records, due or not: a failed rotation shows, and `lastRotated` follows.

Every pass here but the first is a repeat, because the property that matters is what a pass does
NOT do: a repeat never moves an age clock, whatever infraops' state says.
"""

from __future__ import annotations

import pytest

from rotation_proposer import cli
from rotation_proposer.cli import EXIT_FINDINGS, EXIT_OK, RETIREMENT_ACTOR, run
from rotation_proposer.findings import Due, FindingsError
from rotation_proposer.outcome import FINDING_STATUSES
from tests.rotation_proposer.test_pass import COMPLETED, CREDENTIAL, UNIT_ID

PACKAGE = f"rotation-{CREDENTIAL}"
OTHER_UNIT = "0c7a484c-2222-4222-8222-222222222222"
RETIRED_DATE = COMPLETED[:10]
EXPOSED = Due(CREDENTIAL, "openrouter-key", "exposure", "exp-7", "2026-10-07", True)


def _record(status: str, *, record_id: int = 500, revision: int = 1, **fields) -> dict:
    return {
        "id": record_id,
        "package_id": PACKAGE,
        "package_revision": revision,
        "status": status,
        "decided_by": RETIREMENT_ACTOR if status == "resolved" else "work-policy",
        **fields,
    }


def _units(*states: str) -> dict:
    return {
        "units": [
            {"unit_id": UNIT_ID, "unit_key": f"{CREDENTIAL}-{index}", "state": state}
            for index, state in enumerate(states)
        ]
    }


@pytest.fixture
def idle(rig, monkeypatch):
    """Nothing is due, the credential is on the registry, and `recorded` collects each write."""
    rig.due = []
    rig.rows = [CREDENTIAL]
    rig.recorded = []

    def record(credential_id: str, date: str) -> None:
        rig.recorded.append((credential_id, date))
        if rig.last_rotated is not None:
            rig.last_rotated[credential_id] = f"{date}T00:00:00.000Z"

    monkeypatch.setattr(cli, "record_rotation", record)
    return rig


def _outcomes(rig) -> list:
    return rig.synced[-1][0]


def _statuses(rig) -> list[str]:
    return [outcome.status for outcome in _outcomes(rig)]


def test_stalled_is_a_finding() -> None:
    assert "stalled" in FINDING_STATUSES


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_a_failed_or_cancelled_unit_reports_stalled_naming_the_unit(idle, state) -> None:
    idle.estate.records.append(_record("approved"))
    idle.work[500] = _units("completed", state)

    assert run(["--submit"]) == EXIT_FINDINGS

    (outcome,) = _outcomes(idle)
    assert (outcome.credential_id, outcome.status) == (CREDENTIAL, "stalled")
    assert f"{CREDENTIAL}-1" in outcome.detail
    assert f"{CREDENTIAL}-0" not in outcome.detail


def test_a_failed_unit_reports_stalled(idle) -> None:
    idle.estate.records.append(_record("approved"))
    idle.work[500] = _units("failed")
    assert run(["--submit"]) == EXIT_FINDINGS
    assert _statuses(idle) == ["stalled"]


def test_a_cancelled_unit_reports_stalled(idle) -> None:
    idle.estate.records.append(_record("approved"))
    idle.work[500] = _units("cancelled")
    assert run(["--submit"]) == EXIT_FINDINGS
    assert _statuses(idle) == ["stalled"]


@pytest.mark.parametrize("state", ["completed", "in_progress", "ready"])
def test_all_completed_is_not_stalled(idle, state) -> None:
    idle.estate.records.append(_record("approved"))
    idle.work[500] = _units("completed", state)
    assert run(["--submit"]) == EXIT_OK
    assert _statuses(idle) == []


def test_a_credential_not_due_is_still_walked(idle) -> None:
    """Kills: walking the due list, which is how the rotation that failed went unseen."""
    assert idle.due == []
    idle.estate.records.append(_record("approved"))
    idle.work[500] = _units("failed")
    run(["--submit"])
    assert _statuses(idle) == ["stalled"]


def test_an_earlier_revisions_record_never_reports_stalled(idle) -> None:
    """A cancelled unit and a later revision: only the tip's record can stall."""
    idle.estate.records.append(_record("approved", revision=0))
    idle.work[500] = _units("cancelled")
    assert run(["--submit"]) == EXIT_OK
    assert _statuses(idle) == []


@pytest.mark.parametrize("status", ["pending", "wontfix"])
def test_only_an_approved_record_can_stall(idle, status) -> None:
    idle.estate.records.append(_record(status))
    idle.work[500] = _units("failed")
    run(["--submit"])
    assert _statuses(idle) == []


def test_a_record_of_no_standing_package_is_not_walked(idle) -> None:
    idle.estate.records.append(_record("approved", package_id="bump-something"))
    idle.work[500] = _units("failed")
    run(["--submit"])
    assert _statuses(idle) == []


def test_an_unreadable_record_is_that_credentials_error(idle) -> None:
    idle.estate.records.append(_record("approved"))
    idle.work[500] = None
    assert run(["--submit"]) == EXIT_FINDINGS
    assert _statuses(idle) == ["error"]


def test_a_retired_record_records_its_date(idle, capsys) -> None:
    idle.estate.records.append(_record("resolved"))
    assert run(["--submit"]) == EXIT_OK
    assert idle.recorded == [(CREDENTIAL, RETIRED_DATE)]
    assert _statuses(idle) == ["recorded"]


def test_the_date_is_the_newest_completion_across_the_units(idle) -> None:
    idle.estate.records.append(_record("resolved"))
    idle.work[500] = {
        "units": [
            {"unit_id": UNIT_ID, "unit_key": "a", "state": "completed"},
            {"unit_id": OTHER_UNIT, "unit_key": "b", "state": "completed"},
        ]
    }
    idle.histories[OTHER_UNIT] = [
        {"to_state": "completed", "occurred_at": "2026-10-31T01:00:00+00:00"},
        {"to_state": "failed", "occurred_at": "2026-11-05T01:00:00+00:00"},
    ]
    run(["--submit"])
    assert idle.recorded == [(CREDENTIAL, "2026-10-31")]


def test_a_date_is_the_utc_day(idle) -> None:
    idle.estate.records.append(_record("resolved"))
    idle.histories[UNIT_ID] = [
        {"to_state": "completed", "occurred_at": "2026-10-30T23:30:00-05:00"}
    ]
    run(["--submit"])
    assert idle.recorded == [(CREDENTIAL, "2026-10-31")]


def test_a_repeat_pass_records_nothing(idle) -> None:
    idle.estate.records.append(_record("resolved"))
    run(["--submit"])
    assert run(["--submit"]) == EXIT_OK
    assert idle.recorded == [(CREDENTIAL, RETIRED_DATE)]
    assert _statuses(idle) == []


def test_a_later_recorded_rotation_is_never_moved_back(idle) -> None:
    """infraops accepts an earlier date, so this comparison alone guards the age clock."""
    idle.estate.records.append(_record("resolved"))
    idle.last_rotated = {CREDENTIAL: "2026-10-31T04:00:00.000Z"}
    run(["--submit"])
    assert idle.recorded == []


def test_the_same_day_is_not_newer(idle) -> None:
    idle.estate.records.append(_record("resolved"))
    idle.last_rotated = {CREDENTIAL: f"{RETIRED_DATE}T00:00:00.000Z"}
    run(["--submit"])
    assert idle.recorded == []


def test_a_registry_date_newer_than_its_record_records_nothing(idle) -> None:
    """infraops serves the registry's `last_rotated` when state has none, so a date a person wrote
    down holds the clock exactly as a recorded one does."""
    idle.estate.records.append(_record("resolved"))
    idle.last_rotated = {CREDENTIAL: "2026-10-31"}
    assert run(["--submit"]) == EXIT_OK
    assert idle.recorded == []


def test_an_id_infraops_does_not_serve_is_an_error(idle) -> None:
    """Every handed-over row has an anchor, so a missing one is a malformed answer."""
    idle.estate.records.append(_record("resolved"))
    idle.last_rotated = {}
    assert run(["--submit"]) == EXIT_FINDINGS
    assert idle.recorded == []
    assert _statuses(idle) == ["error"]


def test_only_the_newest_retired_revision_counts(idle) -> None:
    idle.estate.records.extend(
        [_record("resolved", record_id=500, revision=3), _record("resolved", record_id=400)]
    )
    idle.work[400] = None  # Asking the older record would be an error.
    run(["--submit"])
    assert idle.recorded == [(CREDENTIAL, RETIRED_DATE)]


def test_a_person_resolved_record_records_nothing(idle) -> None:
    idle.estate.records.append(_record("resolved", decided_by="devon"))
    assert run(["--submit"]) == EXIT_OK
    assert idle.recorded == []


def test_a_person_resolving_a_later_revision_leaves_the_retired_one_recording(idle) -> None:
    idle.estate.records.extend(
        [
            _record("resolved", record_id=400),
            _record("resolved", record_id=500, revision=2, decided_by="devon"),
        ]
    )
    run(["--submit"])
    assert idle.recorded == [(CREDENTIAL, RETIRED_DATE)]


def test_an_exposure_records_nothing(idle) -> None:
    """An open exposure stays due: recording here would move lastRotated on every new record."""
    idle.due = [EXPOSED]
    idle.estate.records.append(_record("resolved", revision=0))
    run(["--submit"])
    assert idle.recorded == []


def test_a_closed_exposures_rotation_records_nothing(idle, rig) -> None:
    """Its record is the tip's and the tip names the trigger: closing it stays a person's."""
    text = rig.package_yaml.read_text()
    rig.package_yaml.write_text(text.replace("2025-01-02-age", "exp-7-exposure"))
    idle.estate.records.append(_record("resolved"))
    run(["--submit"])
    assert idle.recorded == []


def test_an_earlier_revisions_retired_rotation_records_beside_an_exposure_tip(idle, rig) -> None:
    """The tip names only its own revision's trigger; the rotation before it did happen."""
    text = rig.package_yaml.read_text()
    rig.package_yaml.write_text(text.replace("2025-01-02-age", "exp-7-exposure"))
    idle.estate.records.append(_record("resolved", revision=0))
    run(["--submit"])
    assert idle.recorded == [(CREDENTIAL, RETIRED_DATE)]


def test_a_row_not_handed_over_records_nothing(idle, monkeypatch) -> None:
    idle.estate.records.append(_record("resolved"))
    idle.rows = []
    run(["--submit"])
    assert idle.recorded == []


def test_a_row_the_registry_marks_legacy_records_nothing(idle, monkeypatch) -> None:
    idle.estate.records.append(_record("resolved"))
    idle.legacy = {CREDENTIAL}
    run(["--submit"])
    assert idle.recorded == []


def test_an_absent_field_skips_the_walk(idle) -> None:
    idle.estate.records.append(_record("resolved"))
    idle.last_rotated = None
    idle.work[500] = None  # Reading it would be an error, so this also proves nothing is asked.
    assert run(["--submit"]) == EXIT_OK
    assert idle.recorded == []


def test_a_failed_record_is_an_error(idle, monkeypatch) -> None:
    def refuse(credential_id: str, date: str) -> None:
        raise FindingsError("infraops refused record-rotation: unknown credential id")

    idle.estate.records.append(_record("resolved"))
    monkeypatch.setattr(cli, "record_rotation", refuse)
    assert run(["--submit"]) == EXIT_FINDINGS
    assert _statuses(idle) == ["error"]


def test_a_failed_record_is_retried_next_pass(idle, monkeypatch) -> None:
    written: list[str] = []

    def refuse(credential_id: str, date: str) -> None:
        written.append(date)
        raise FindingsError("infraops refused record-rotation")

    idle.estate.records.append(_record("resolved"))
    monkeypatch.setattr(cli, "record_rotation", refuse)
    run(["--submit"])
    run(["--submit"])
    assert written == [RETIRED_DATE, RETIRED_DATE]


def test_an_unreadable_history_is_an_error_and_records_nothing(idle) -> None:
    idle.estate.records.append(_record("resolved"))
    idle.histories[UNIT_ID] = None
    assert run(["--submit"]) == EXIT_FINDINGS
    assert idle.recorded == []
    assert _statuses(idle) == ["error"]


@pytest.mark.parametrize(
    "history",
    [
        [],
        [{"to_state": "failed", "occurred_at": COMPLETED}],
        [{"to_state": "completed", "occurred_at": "soon"}],
        [{"to_state": "completed"}],
    ],
)
def test_no_readable_completion_is_an_error(idle, history) -> None:
    idle.estate.records.append(_record("resolved"))
    idle.histories[UNIT_ID] = history
    assert run(["--submit"]) == EXIT_FINDINGS
    assert idle.recorded == []
    assert _statuses(idle) == ["error"]


def test_the_two_false_rows_repair(idle) -> None:
    """Two rows rotated in October whose state still says July, because they were rotated while
    nothing reported them due."""
    idle.rows = [CREDENTIAL, "orchestrator-observer-bearer"]
    idle.last_rotated = {
        CREDENTIAL: "2026-07-14T00:00:00.000Z",
        "orchestrator-observer-bearer": "2026-07-14T00:00:00.000Z",
    }
    idle.estate.records.extend(
        [
            _record("resolved", record_id=500),
            _record("resolved", record_id=501, package_id="rotation-orchestrator-observer-bearer"),
        ]
    )
    run(["--submit"])
    assert idle.recorded == [
        (CREDENTIAL, RETIRED_DATE),
        ("orchestrator-observer-bearer", RETIRED_DATE),
    ]
    assert run(["--submit"]) == EXIT_OK
    assert len(idle.recorded) == 2


def test_a_dry_run_walks_nothing(idle, monkeypatch) -> None:
    idle.estate.records.append(_record("resolved"))
    monkeypatch.delenv("ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN")
    assert run([]) == EXIT_OK
    assert idle.recorded == []
