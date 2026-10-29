"""A pull request a lander holds for a day raises one keyed alert-hub alert.

`firing` is driven with fixed datetimes; `sync_held_alerts` through an `httpx.MockTransport` and a
state directory under `tmp_path`, so nothing is sent anywhere and nothing is written outside it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from lander.core import Outcome
from lander.held_alerts import (
    ALERTING_STATUSES,
    HELD_ALERT_HOURS,
    STATE_DIR_ENV,
    TOKEN_ENV,
    firing,
    held_key,
    sync_held_alerts,
)

REPOSITORY = "AlobarQuest/orchestrator"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
KEY = held_key(REPOSITORY, 506)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _held(status: str = "held", number: int = 506, detail: str = "landing_checks_not_clean"):
    return Outcome(REPOSITORY, number, status, detail)


def test_the_key_is_the_lowercased_repository_and_the_number() -> None:
    assert KEY == "landing-held:alobarquest/orchestrator:506"


def test_a_pr_held_for_less_than_the_threshold_fires_nothing() -> None:
    seen = {KEY: _iso(NOW - timedelta(hours=HELD_ALERT_HOURS - 1))}

    alerts, kept = firing([_held()], seen, NOW)

    assert alerts == {}
    assert kept == seen


def test_a_pr_seen_for_the_first_time_is_remembered_from_now() -> None:
    alerts, kept = firing([_held()], {}, NOW)

    assert alerts == {}
    assert kept == {KEY: _iso(NOW)}


def test_a_pr_held_past_the_threshold_fires_one_keyed_alert() -> None:
    seen = {KEY: _iso(NOW - timedelta(hours=HELD_ALERT_HOURS))}

    alerts, kept = firing([_held()], seen, NOW)

    assert list(alerts) == [held_key("AlobarQuest/orchestrator", 506)]
    alert = alerts[KEY]
    assert alert.title == "Pull request held: AlobarQuest/orchestrator#506"
    assert alert.message == "held for 24 hours: landing_checks_not_clean"
    assert alert.link == "https://github.com/AlobarQuest/orchestrator/pull/506"
    assert kept == seen


def test_a_status_change_inside_the_set_keeps_the_first_sighting() -> None:
    first = NOW - timedelta(hours=HELD_ALERT_HOURS + 2)
    _, kept = firing([_held("held")], {KEY: _iso(first)}, NOW - timedelta(hours=1))

    alerts, kept = firing([_held("unreadable", detail="404")], kept, NOW)

    assert kept == {KEY: _iso(first)}
    assert alerts[KEY].message == "unreadable for 26 hours: 404"


def test_a_pr_that_leaves_the_set_or_the_pass_is_forgotten() -> None:
    old = _iso(NOW - timedelta(hours=48))
    seen = {
        held_key(REPOSITORY, 1): old,
        held_key(REPOSITORY, 2): old,
        held_key(REPOSITORY, 3): old,
    }
    outcomes = [_held("landed", number=1), _held("waiting", number=2)]

    alerts, kept = firing(outcomes, seen, NOW)

    assert alerts == {}
    assert kept == {}


def test_deliberate_waiting_exception_and_settled_never_alert() -> None:
    quiet = ["deliberate", "waiting", "exception", "settled", "landed", "updated"]
    outcomes = [_held(status, number=n) for n, status in enumerate(quiet, start=1)]
    long_ago = _iso(NOW - timedelta(days=30))
    seen = {held_key(REPOSITORY, n): long_ago for n in range(1, len(quiet) + 1)}

    alerts, kept = firing(outcomes, seen, NOW)

    assert alerts == {}
    assert kept == {}
    assert not set(quiet) & ALERTING_STATUSES


def test_a_pr_reported_twice_in_one_pass_alerts_once_on_its_alerting_outcome() -> None:
    """The landing pass and the branch-update pass both report the same subjects, so one pull
    request can be `held` by the first and `updated` by the second. It is still held."""
    seen = {KEY: _iso(NOW - timedelta(hours=30))}
    outcomes = [_held("held", detail="landing_head_behind_base"), _held("updated", detail="ok")]

    alerts, kept = firing(outcomes, seen, NOW)

    assert list(alerts) == [KEY]
    assert alerts[KEY].message == "held for 30 hours: landing_head_behind_base"
    assert kept == seen


def test_an_unparseable_first_sighting_restarts_from_now() -> None:
    alerts, kept = firing([_held()], {KEY: "not a date"}, NOW)

    assert alerts == {}
    assert kept == {KEY: _iso(NOW)}


# ------------------------------------------------------------------------------------------------
# `sync_held_alerts`: the state file and alert-hub.
# ------------------------------------------------------------------------------------------------


class Hub:
    def __init__(self, status: int = 202) -> None:
        self.sent: list[dict[str, object]] = []
        self.status = status

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.sent.append(json.loads(request.content))
            return httpx.Response(self.status)

        return httpx.MockTransport(handle)


def _env(tmp_path: Path) -> dict[str, str]:
    return {TOKEN_ENV: "sender-token", STATE_DIR_ENV: str(tmp_path / "state")}


def test_the_first_sighting_survives_across_passes(tmp_path) -> None:
    hub = Hub()
    env = _env(tmp_path)
    first = NOW - timedelta(hours=HELD_ALERT_HOURS)

    assert (
        sync_held_alerts(
            "inert-landing", [_held()], now=first, environ=env, transport=hub.transport()
        )
        is None
    )
    assert hub.sent == []
    assert (
        sync_held_alerts(
            "inert-landing", [_held()], now=NOW, environ=env, transport=hub.transport()
        )
        is None
    )
    assert (
        sync_held_alerts(
            "inert-landing",
            [_held()],
            now=NOW + timedelta(hours=1),
            environ=env,
            transport=hub.transport(),
        )
        is None
    )

    assert [(body["key"], body["status"]) for body in hub.sent] == [(KEY, "firing")]
    assert hub.sent[0]["title"] == "Pull request held: AlobarQuest/orchestrator#506"
    state = json.loads((tmp_path / "state" / "held-since.json").read_text())
    assert state == {KEY: _iso(first)}


def test_a_resolved_pr_sends_resolved(tmp_path) -> None:
    hub = Hub()
    env = _env(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "held-since.json").write_text(
        json.dumps({KEY: _iso(NOW - timedelta(hours=30))})
    )
    assert (
        sync_held_alerts(
            "estate-landing", [_held()], now=NOW, environ=env, transport=hub.transport()
        )
        is None
    )

    assert (
        sync_held_alerts("estate-landing", [], now=NOW, environ=env, transport=hub.transport())
        is None
    )

    assert [(body["key"], body["status"]) for body in hub.sent] == [
        (KEY, "firing"),
        (KEY, "resolved"),
    ]
    assert hub.sent[1]["title"] == "Pull request no longer held"
    assert json.loads((tmp_path / "state" / "held-since.json").read_text()) == {}


def test_a_missing_token_or_unusable_state_dir_is_reported_not_raised(tmp_path) -> None:
    hub = Hub()
    no_token = {STATE_DIR_ENV: str(tmp_path / "state")}
    reason = sync_held_alerts(
        "inert-landing", [_held()], now=NOW, environ=no_token, transport=hub.transport()
    )
    assert reason is not None and TOKEN_ENV in reason
    assert not (tmp_path / "state").exists(), "a pass with no token writes nothing"

    blocker = tmp_path / "a-file"
    blocker.write_text("")
    unusable = {TOKEN_ENV: "sender-token", STATE_DIR_ENV: str(blocker / "state")}
    reason = sync_held_alerts(
        "inert-landing", [_held()], now=NOW, environ=unusable, transport=hub.transport()
    )
    assert reason is not None and "state directory" in reason
    assert "sender-token" not in reason
    assert hub.sent == []


def test_an_alert_hub_failure_is_reported_and_the_first_sightings_are_still_kept(
    tmp_path,
) -> None:
    """An alert-hub outage must not restart every pull request's clock."""
    hub = Hub(status=500)
    env = _env(tmp_path)
    first = _iso(NOW - timedelta(hours=30))
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "held-since.json").write_text(json.dumps({KEY: first}))

    reason = sync_held_alerts(
        "inert-landing", [_held()], now=NOW, environ=env, transport=hub.transport()
    )

    assert reason is not None and "not sent" in reason
    assert not reason.startswith("alert-hub:"), "the caller adds the prefix"
    assert json.loads((tmp_path / "state" / "held-since.json").read_text()) == {KEY: first}


def test_the_default_state_directory_is_per_lane(tmp_path) -> None:
    from lander import held_alerts

    assert held_alerts.state_dir("inert-landing", {}) == (
        Path.home() / "Library" / "Application Support" / "sds" / "inert-landing"
    )
    assert held_alerts.state_dir("estate-landing", {STATE_DIR_ENV: str(tmp_path)}) == tmp_path
