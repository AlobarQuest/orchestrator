"""The lanes' one alert-hub client: each key fires once, a cleared key resolves, and the state
file is the only memory (Bridge Part 3, task 6)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from alert_hub_client import ALERTS, Alert, AlertHubError, KeyedAlerts, alert_key

TOKEN = "sender-token-that-must-never-be-printed"
KEY_GRAMMAR = re.compile(r"[A-Za-z0-9._:/-]{1,200}")


class Hub:
    """alert-hub's `/v1/alerts`: each request gets the next scripted answer, then 202."""

    def __init__(self, *answers: httpx.Response) -> None:
        self.sent: list[dict[str, Any]] = []
        self._answers = list(answers)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", ALERTS)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        self.sent.append(json.loads(request.content))
        return self._answers.pop(0) if self._answers else httpx.Response(202)

    def alerts(self, state: Path, sleeps: list[float] | None = None, **kwargs: Any) -> KeyedAlerts:
        pauses = sleeps if sleeps is not None else []
        return KeyedAlerts(
            lambda: TOKEN,
            state,
            user_agent="test-lane/1",
            transport=httpx.MockTransport(self),
            sleep=pauses.append,
            **kwargs,
        )


A = Alert("Bump a", "Review it.", "https://sds.alobar.net/review/a")


def statuses(hub: Hub) -> list[tuple[str, str]]:
    return [(m["key"], m["status"]) for m in hub.sent]


def test_a_key_fires_once_across_passes(tmp_path: Path) -> None:
    hub = Hub()
    for _ in range(3):
        hub.alerts(tmp_path / "s.json").sync({"k:a": A})
    assert statuses(hub) == [("k:a", "firing")]
    assert hub.sent[0] == {
        "key": "k:a",
        "severity": "warning",
        "kind": "alert",
        "status": "firing",
        "title": "Bump a",
        "message": "Review it.",
        "link": "https://sds.alobar.net/review/a",
    }


def test_a_cleared_key_is_resolved_and_forgotten(tmp_path: Path) -> None:
    hub = Hub()
    state = tmp_path / "s.json"
    hub.alerts(state).sync({"k:a": A})
    hub.alerts(state).sync({})
    hub.alerts(state).sync({})
    assert statuses(hub) == [("k:a", "firing"), ("k:a", "resolved")]
    assert json.loads(state.read_text()) == []


def test_a_key_fires_again_after_it_resolved(tmp_path: Path) -> None:
    hub = Hub()
    state = tmp_path / "s.json"
    for firing in ({"k:a": A}, {}, {"k:a": A}):
        hub.alerts(state).sync(firing)
    assert [s for _, s in statuses(hub)] == ["firing", "resolved", "firing"]


def test_refire_sends_firing_every_pass_and_still_resolves(tmp_path: Path) -> None:
    hub = Hub()
    for firing in ({"k:a": A}, {"k:a": A}, {}):
        hub.alerts(tmp_path / "s.json", refire=True).sync(firing)
    assert [s for _, s in statuses(hub)] == ["firing", "firing", "resolved"]


def test_a_failed_fire_is_retried_and_a_failed_resolve_stays_remembered(tmp_path: Path) -> None:
    hub = Hub(httpx.Response(500), httpx.Response(202), httpx.Response(503))
    state = tmp_path / "s.json"
    with pytest.raises(AlertHubError):
        hub.alerts(state).sync({"k:a": A})
    hub.alerts(state).sync({"k:a": A})
    with pytest.raises(AlertHubError):
        hub.alerts(state).sync({})
    hub.alerts(state).sync({})
    assert [s for _, s in statuses(hub)] == ["firing", "firing", "resolved", "resolved"]


def test_an_unread_key_is_neither_fired_nor_resolved(tmp_path: Path) -> None:
    hub = Hub()
    state = tmp_path / "s.json"
    hub.alerts(state).sync({"k:a": A})
    hub.alerts(state).sync({}, unread={"k:a"})
    assert statuses(hub) == [("k:a", "firing")]
    hub.alerts(state).sync({})
    assert statuses(hub)[-1] == ("k:a", "resolved")


def test_a_429_is_retried_after_retry_after(tmp_path: Path) -> None:
    hub = Hub(httpx.Response(429, headers={"Retry-After": "7"}))
    sleeps: list[float] = []
    hub.alerts(tmp_path / "s.json", sleeps).sync({"k:a": A})
    assert sleeps == [7.0]
    assert [s for _, s in statuses(hub)] == ["firing", "firing"]


def test_a_429_without_a_usable_retry_after_waits_a_bounded_default(tmp_path: Path) -> None:
    for header in ({}, {"Retry-After": "soon"}, {"Retry-After": "86400"}):
        hub = Hub(httpx.Response(429, headers=header))
        sleeps: list[float] = []
        hub.alerts(tmp_path / f"{len(header)}{header.get('Retry-After', '')}.json", sleeps).sync(
            {"k:a": A}
        )
        assert len(sleeps) == 1 and 0 < sleeps[0] <= 60


def test_a_second_429_raises_and_does_not_remember_the_key(tmp_path: Path) -> None:
    hub = Hub(httpx.Response(429), httpx.Response(429))
    state = tmp_path / "s.json"
    with pytest.raises(AlertHubError):
        hub.alerts(state).sync({"k:a": A})
    assert json.loads(state.read_text()) == []


def test_an_unwritable_state_file_raises_before_anything_is_sent(tmp_path: Path) -> None:
    hub = Hub()
    with pytest.raises(AlertHubError, match="state"):
        hub.alerts(tmp_path / "missing" / "s.json").sync({"k:a": A})
    assert hub.sent == []


def test_a_refiring_client_with_an_unwritable_state_file_still_sends_then_raises(
    tmp_path: Path,
) -> None:
    hub = Hub()
    with pytest.raises(AlertHubError, match="state"):
        hub.alerts(tmp_path / "missing" / "s.json", refire=True).sync({"k:a": A})
    assert statuses(hub) == [("k:a", "firing")]


def test_the_state_is_written_aside_and_renamed(tmp_path: Path) -> None:
    state = tmp_path / "s.json"
    Hub().alerts(state).sync({"k:b": A, "k:a": A})
    assert json.loads(state.read_text()) == ["k:a", "k:b"]
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


def test_a_corrupt_state_file_reads_as_nothing_remembered(tmp_path: Path) -> None:
    state = tmp_path / "s.json"
    state.write_text("{not json")
    hub = Hub()
    hub.alerts(state).sync({})
    assert hub.sent == []


def test_the_token_never_appears_in_an_error(tmp_path: Path) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach with Bearer {TOKEN}")

    def broken() -> str:
        raise RuntimeError(f"token store said {TOKEN}")

    state = tmp_path / "s.json"
    for token, transport in ((lambda: TOKEN, httpx.MockTransport(refuse)), (broken, None)):
        alerts = KeyedAlerts(token, state, user_agent="test-lane/1", transport=transport)
        with pytest.raises(AlertHubError) as caught:
            alerts.sync({"k:a": A})
        assert TOKEN not in str(caught.value)
        assert TOKEN not in repr(caught.value)
        assert TOKEN not in repr(alerts)


def test_a_key_outside_the_grammar_is_refused_before_any_send(tmp_path: Path) -> None:
    hub = Hub()
    for bad in ("@types/node", "", "x" * 201, "has space"):
        with pytest.raises(ValueError, match="alert-hub key"):
            hub.alerts(tmp_path / "s.json").sync({bad: A})
    assert hub.sent == []


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (
            ("dependency-bump", "owner/repo", "npm", "@types/node"),
            "dependency-bump:owner/repo:npm:_40types/node",
        ),
        (("a", "x:y"), "a:x_3Ay"),
        (("a", "snake_case"), "a:snake__case"),
        (("a", "ü"), "a:_C3_BC"),
    ],
)
def test_alert_key_encodes_disallowed_characters_deterministically(
    parts: tuple[str, ...], expected: str
) -> None:
    assert alert_key(*parts) == expected


def test_alert_key_is_injective_over_the_escape_character() -> None:
    assert alert_key("a", "_40") != alert_key("a", "@")
    assert len({alert_key("p", x) for x in ("@", "_40", "__40", "_", "__")}) == 5


def test_a_long_key_is_shortened_with_a_digest_and_stays_distinct() -> None:
    one, two = alert_key("p", "d" * 300 + "1"), alert_key("p", "d" * 300 + "2")
    assert len(one) == len(two) == 200
    assert one != two
    assert KEY_GRAMMAR.fullmatch(one)
    assert alert_key("p", "d" * 300 + "1") == one
