"""Keyed alerts: a finding fires once, an approval wait fires after 48 hours, both resolve."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from rotation_proposer.alerts import (
    APPROVAL_STATUSES,
    CRITICAL_ALERTS_FILE,
    STATE_DIR_ENV,
    approval_key,
    finding_key,
    sync_alerts,
)
from rotation_proposer.outcome import FINDING_STATUSES, Outcome

NOW = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
CREDENTIAL = "openrouter-generic"


class Hub:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.status = 202

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(json.loads(request.content))
        return httpx.Response(self.status, json={})


@pytest.fixture
def hub() -> Hub:
    return Hub()


def _sync(hub: Hub, tmp_path: Path, outcomes: list[Outcome], hours: float = 0) -> str | None:
    return sync_alerts(
        outcomes,
        token="t",
        state_dir=tmp_path,
        now=NOW + timedelta(hours=hours),
        transport=httpx.MockTransport(hub),
    )


def _sent(hub: Hub) -> list[tuple[str, str]]:
    return [(row["key"], row["status"]) for row in hub.sent]


@pytest.mark.parametrize("status", sorted(FINDING_STATUSES))
def test_each_finding_status_raises_one_keyed_alert(hub: Hub, tmp_path: Path, status: str) -> None:
    outcome = Outcome(CREDENTIAL, status, "detail")
    _sync(hub, tmp_path, [outcome, outcome])
    assert _sent(hub) == [(finding_key(CREDENTIAL, status), "firing")]
    assert hub.sent[0]["message"] == "detail"


def test_a_finding_that_stops_resolves(hub: Hub, tmp_path: Path) -> None:
    key = finding_key(CREDENTIAL, "stacked")
    _sync(hub, tmp_path, [Outcome(CREDENTIAL, "stacked", "x")])
    _sync(hub, tmp_path, [Outcome(CREDENTIAL, "stacked", "x")])
    _sync(hub, tmp_path, [Outcome(CREDENTIAL, "revised", "x")])
    assert _sent(hub) == [(key, "firing"), (key, "resolved")]


def test_a_status_that_is_not_a_finding_raises_nothing(hub: Hub, tmp_path: Path) -> None:
    for status in ("revised", "proposed", "replayed", "recorded", "legacy", "would-advance"):
        _sync(hub, tmp_path, [Outcome(CREDENTIAL, status, "x")])
    assert hub.sent == []


@pytest.mark.parametrize(
    "status",
    [
        pytest.param("awaiting-approval", id="test_awaiting_approval_waits_48_hours"),
        pytest.param("declined", id="test_declined_waits_48_hours"),
    ],
)
def test_approval_wait_48_hours(hub: Hub, tmp_path: Path, status: str) -> None:
    key = approval_key(CREDENTIAL)
    outcome = Outcome(CREDENTIAL, status, "needs a name")
    _sync(hub, tmp_path, [outcome])
    _sync(hub, tmp_path, [outcome], 47.98)
    assert hub.sent == []
    _sync(hub, tmp_path, [outcome], 48)
    _sync(hub, tmp_path, [outcome], 60)
    assert _sent(hub) == [(key, "firing")]
    assert "48 hours" in hub.sent[0]["message"]


def test_an_approval_wait_that_ends_resolves_and_restarts_its_clock(
    hub: Hub, tmp_path: Path
) -> None:
    key = approval_key(CREDENTIAL)
    waiting = Outcome(CREDENTIAL, "awaiting-approval", "x")
    _sync(hub, tmp_path, [waiting])
    _sync(hub, tmp_path, [waiting], 49)
    _sync(hub, tmp_path, [Outcome(CREDENTIAL, "proposed", "x")], 50)
    _sync(hub, tmp_path, [waiting], 51)
    assert _sent(hub) == [(key, "firing"), (key, "resolved")]


def test_the_first_sighting_survives_an_alert_hub_outage(hub: Hub, tmp_path: Path) -> None:
    waiting = Outcome(CREDENTIAL, "awaiting-approval", "x")
    hub.status = 500
    _sync(hub, tmp_path, [waiting])
    _sync(hub, tmp_path, [waiting], 49)
    hub.status = 202
    hub.sent.clear()
    _sync(hub, tmp_path, [waiting], 50)
    assert _sent(hub) == [(approval_key(CREDENTIAL), "firing")]


ERRORED = Outcome(CREDENTIAL, "error", "the registry: unreadable")


def _about(hub: Hub, *keys: str) -> list[tuple[str, str]]:
    """What was sent for `keys`, leaving out the `error` finding the failed pass itself raises."""
    return [row for row in _sent(hub) if row[0] in keys]


def test_a_credential_that_errored_keeps_its_other_alerts(hub: Hub, tmp_path: Path) -> None:
    """A failed fetch turns every due credential into `error` for one pass. That says nothing
    about the findings the credential had, so they are not resolved; another credential's are."""
    stacked, other = finding_key(CREDENTIAL, "stacked"), finding_key("other", "stacked")
    both = [Outcome(CREDENTIAL, "stacked", "x"), Outcome("other", "stacked", "x")]
    _sync(hub, tmp_path, both)
    _sync(hub, tmp_path, [ERRORED], 1)
    assert _about(hub, stacked, other) == [
        (stacked, "firing"),
        (other, "firing"),
        (other, "resolved"),
    ]
    _sync(hub, tmp_path, [Outcome(CREDENTIAL, "stacked", "x")], 2)
    assert _about(hub, stacked).count((stacked, "firing")) == 1


def test_a_wait_survives_one_error_pass_with_its_original_clock(hub: Hub, tmp_path: Path) -> None:
    key = approval_key(CREDENTIAL)
    waiting = Outcome(CREDENTIAL, "awaiting-approval", "x")
    _sync(hub, tmp_path, [waiting])
    _sync(hub, tmp_path, [ERRORED], 10)
    _sync(hub, tmp_path, [waiting], 49)
    assert _about(hub, key) == [(key, "firing")]
    assert "49 hours" in next(row for row in hub.sent if row["key"] == key)["message"]


def test_a_fired_approval_wait_is_not_resolved_by_an_error_pass(hub: Hub, tmp_path: Path) -> None:
    key = approval_key(CREDENTIAL)
    waiting = Outcome(CREDENTIAL, "awaiting-approval", "x")
    _sync(hub, tmp_path, [waiting])
    _sync(hub, tmp_path, [waiting], 5 * 24)
    _sync(hub, tmp_path, [ERRORED], 5 * 24 + 1)
    _sync(hub, tmp_path, [waiting], 5 * 24 + 2)
    assert _about(hub, key) == [(key, "firing")]


def test_a_clock_that_can_t_be_saved_holds_an_open_wait(hub: Hub, tmp_path: Path) -> None:
    outcome = Outcome(CREDENTIAL, "awaiting-approval", "detail")
    _sync(hub, tmp_path, [outcome])
    _sync(hub, tmp_path, [outcome], 48)
    assert _sent(hub) == [(approval_key(CREDENTIAL), "firing")]
    (tmp_path / "approval-since.json.tmp").mkdir()
    reason = _sync(hub, tmp_path, [outcome], 49)
    assert reason is not None and "state file unwritable" in reason
    assert _sent(hub) == [(approval_key(CREDENTIAL), "firing")]


def test_a_clock_that_can_t_be_saved_still_sends_findings(hub: Hub, tmp_path: Path) -> None:
    (tmp_path / "approval-since.json.tmp").mkdir()
    outcomes = [
        Outcome(CREDENTIAL, "awaiting-approval", "detail"),
        Outcome("other", sorted(FINDING_STATUSES)[0], "detail"),
    ]
    assert _sync(hub, tmp_path, outcomes) is not None
    assert _sent(hub) == [(finding_key("other", sorted(FINDING_STATUSES)[0]), "firing")]


def test_a_long_detail_stays_within_alert_hub_s_limit(hub: Hub, tmp_path: Path) -> None:
    long = Outcome(CREDENTIAL, "awaiting-approval", "x" * 5000)
    finding = Outcome("other", sorted(FINDING_STATUSES)[0], "y" * 5000)
    _sync(hub, tmp_path, [long, finding])
    _sync(hub, tmp_path, [long, finding], 48)
    assert len(hub.sent) == 2
    assert all(len(row["message"]) <= 4000 for row in hub.sent)


def test_a_hub_that_refuses_is_named(hub: Hub, tmp_path: Path) -> None:
    hub.status = 500
    assert _sync(hub, tmp_path, [Outcome(CREDENTIAL, "stacked", "x")]) is not None


def test_an_unusable_state_directory_is_named(hub: Hub, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    reason = sync_alerts(
        [], token="t", state_dir=blocker / "sub", now=NOW, transport=httpx.MockTransport(hub)
    )
    assert reason is not None
    assert hub.sent == []


def test_no_key_is_a_substring_of_another_finding_kind() -> None:
    kinds = sorted(FINDING_STATUSES | APPROVAL_STATUSES)
    assert [(a, b) for a in kinds for b in kinds if a != b and a in b] == []


def test_the_state_directory_variable_is_the_proposers_own() -> None:
    assert STATE_DIR_ENV == "ROTATION_PROPOSER_STATE_DIR"


# ---- credentials not handed over: severity, kind, and the second state file ---------------------

PERSON = "openai-project"
MANUAL = ("manual-exposure", "manual-request", "manual-age")


@pytest.mark.parametrize(
    ("status", "severity"),
    [
        pytest.param("manual-exposure", "critical", id="exposure"),
        pytest.param("manual-request", "warning", id="request"),
        pytest.param("manual-age", "warning", id="age"),
    ],
)
def test_a_credential_a_person_rotates_fires_an_alert_at_its_severity(
    hub: Hub, tmp_path: Path, status: str, severity: str
) -> None:
    _sync(hub, tmp_path, [Outcome(PERSON, status, "the checklist")])
    assert hub.sent == [
        {
            "key": f"rotation-finding:{PERSON}:{status}",
            "severity": severity,
            "kind": "alert",
            "status": "firing",
            "title": f"Rotation {status}: {PERSON}",
            "message": "the checklist",
        }
    ]


@pytest.mark.parametrize("status", MANUAL)
def test_it_stays_while_reported_and_resolves_on_the_first_pass_that_does_not(
    hub: Hub, tmp_path: Path, status: str
) -> None:
    key = finding_key(PERSON, status)
    _sync(hub, tmp_path, [Outcome(PERSON, status, "x")])
    _sync(hub, tmp_path, [Outcome(PERSON, status, "x")], 24)
    _sync(hub, tmp_path, [Outcome(PERSON, "legacy", "x")], 48)
    _sync(hub, tmp_path, [], 72)
    assert _sent(hub) == [(key, "firing"), (key, "resolved")]
    severities = {row["severity"] for row in hub.sent}
    assert severities == {"critical" if status == "manual-exposure" else "warning"}


def test_the_critical_alerts_have_their_own_state_file(hub: Hub, tmp_path: Path) -> None:
    _sync(hub, tmp_path, [Outcome(PERSON, "manual-exposure", "x"), Outcome(PERSON, "stacked", "x")])
    critical = json.loads((tmp_path / CRITICAL_ALERTS_FILE).read_text())
    warning = json.loads((tmp_path / "alerts.json").read_text())
    assert critical == [finding_key(PERSON, "manual-exposure")]
    assert warning == [finding_key(PERSON, "stacked")]


def test_a_pass_that_never_reports_a_critical_status_opens_no_second_file(
    hub: Hub, tmp_path: Path
) -> None:
    """The Mac's passes: nothing about them changes, not even a new empty file."""
    _sync(hub, tmp_path, [Outcome(PERSON, "legacy", "x"), Outcome(CREDENTIAL, "stacked", "x")])
    _sync(hub, tmp_path, [], 1)
    assert not (tmp_path / CRITICAL_ALERTS_FILE).exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "alerts.json",
        "approval-since.json",
    ]


def test_an_error_holds_the_credentials_critical_alert_too(hub: Hub, tmp_path: Path) -> None:
    """Infra Brain rule 1032: an exposure clears only when the finding stops, never on a pass
    that could not read the credential."""
    exposure, age = finding_key(PERSON, "manual-exposure"), finding_key("other", "manual-age")
    _sync(
        hub,
        tmp_path,
        [Outcome(PERSON, "manual-exposure", "x"), Outcome("other", "manual-age", "x")],
    )
    _sync(hub, tmp_path, [Outcome(PERSON, "error", "the registry: unreadable")], 1)
    assert _about(hub, exposure) == [(exposure, "firing")]
    assert _about(hub, age) == [(age, "firing"), (age, "resolved")]
    _sync(hub, tmp_path, [], 2)
    assert _about(hub, exposure)[-1] == (exposure, "resolved")


def test_an_error_on_a_credential_holds_its_warning_beside_its_critical(
    hub: Hub, tmp_path: Path
) -> None:
    held = [finding_key(PERSON, "manual-exposure"), finding_key(PERSON, "not-handed-over")]
    _sync(
        hub,
        tmp_path,
        [Outcome(PERSON, "manual-exposure", "x"), Outcome(PERSON, "not-handed-over", "x")],
    )
    _sync(hub, tmp_path, [Outcome(PERSON, "error", "x")], 1)
    assert [row for row in _sent(hub) if row[1] == "resolved"] == []
    assert sorted(_about(hub, *held)) == sorted((key, "firing") for key in held)


def test_a_failed_critical_send_is_named_and_kept_for_the_next_pass(
    hub: Hub, tmp_path: Path
) -> None:
    key = finding_key(PERSON, "manual-exposure")
    hub.status = 500
    reason = _sync(hub, tmp_path, [Outcome(PERSON, "manual-exposure", "x")])
    assert reason is not None and reason.startswith("critical: ")
    hub.status = 202
    _sync(hub, tmp_path, [Outcome(PERSON, "manual-exposure", "x")], 1)
    assert _sent(hub) == [(key, "firing"), (key, "firing")]


def test_a_failed_warning_send_still_sends_the_critical_one(hub: Hub, tmp_path: Path) -> None:
    def refuse_warnings(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        hub.sent.append(body)
        return httpx.Response(500 if body["severity"] == "warning" else 202, json={})

    outcomes = [Outcome(PERSON, "manual-exposure", "x"), Outcome("other", "manual-age", "x")]
    reason = sync_alerts(
        outcomes,
        token="t",
        state_dir=tmp_path,
        now=NOW,
        transport=httpx.MockTransport(refuse_warnings),
    )
    assert reason is not None
    assert json.loads((tmp_path / CRITICAL_ALERTS_FILE).read_text()) == [
        finding_key(PERSON, "manual-exposure")
    ]


def test_the_existing_finding_keys_keep_their_warning_severity(hub: Hub, tmp_path: Path) -> None:
    existing = sorted(FINDING_STATUSES - set(MANUAL))
    _sync(hub, tmp_path, [Outcome(CREDENTIAL, status, "x") for status in existing])
    assert sorted(row["key"] for row in hub.sent) == sorted(
        finding_key(CREDENTIAL, status) for status in existing
    )
    assert {row["severity"] for row in hub.sent} == {"warning"}
    assert not (tmp_path / CRITICAL_ALERTS_FILE).exists()
