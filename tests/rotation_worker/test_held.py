"""The orchestrator's held copies of other services' credentials (ADR-0055 amendment 13).

A simulated orchestrator holds what production holds: Coolify's stored rows, and the fingerprint of
what the running process loaded at its last restart, answered on `held-credentials`. Coolify is the
real client behind a mock transport, so the requests are the ones production would get.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest

from rotation_worker.contract import ContractError
from rotation_worker.coolify import CoolifyClient
from rotation_worker.held import RESTART_WAIT, HeldSettings
from rotation_worker.orchestrator_client import OrchestratorClient, OrchestratorError
from rotation_worker.values import Value

COOLIFY = "http://100.112.188.96:8000"
APP = "eqj5l7k705fhi12x9i74fqf0"
KEY = "ORCHESTRATOR_CHANGE_RECORD_TOKEN"
CONSUMER = {
    "kind": "coolify-env-held",
    "instance": "prod",
    "resource_type": "application",
    "uuid": APP,
    "key": KEY,
}
OLD, NEW = Value("the-retiring-read-bearer"), Value("the-new-read-bearer")
START = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)


def _fingerprint(value: str) -> dict[str, Any]:
    return {"sha256_prefix": hashlib.sha256(value.encode()).hexdigest()[:16], "length": len(value)}


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Orchestrator:
    """Coolify's rows for the orchestrator's application and what its process loaded."""

    def __init__(self) -> None:
        self.rows = {(KEY, preview): OLD.reveal() for preview in (True, False)}
        self.loaded: dict[str, str] = {KEY: OLD.reveal()}
        self.sent: list[tuple[str, str]] = []
        self.restart_loads = True
        # Answers `held()` gives before consulting what loaded: an exception or a raw body.
        self.scripted: list[Any] = []
        # Units `active_units` lists, by what a restart would strand.
        self.live: list[dict[str, Any]] = []
        self.live_after_write: list[dict[str, Any]] = []

    def coolify(self, request: httpx.Request) -> httpx.Response:
        self.sent.append((request.method, request.url.path))
        if request.method == "PATCH":
            body = json.loads(request.content)
            self.rows[(body["key"], body["is_preview"])] = body["value"]
            self.live = self.live_after_write or self.live
            return httpx.Response(201)
        if self.restart_loads:
            self.loaded = {KEY: self.rows[(KEY, False)]}
        return httpx.Response(200)

    # The orchestrator client's surface that `held.py` and `live_runs` use.
    def held(self) -> dict[str, dict[str, Any]]:
        if self.scripted:
            answer = self.scripted.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return {key: _fingerprint(value) for key, value in self.loaded.items()}

    def active_units(self) -> list[dict[str, Any]]:
        return self.live

    def unit(self, unit_id: str) -> dict[str, Any]:
        return {"id": unit_id, "authority": {"constraints": {}}}

    def history(self, unit_id: str) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def o() -> Orchestrator:
    return Orchestrator()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _held(o: Orchestrator, clock: Clock) -> HeldSettings:
    token = Value("throwaway-coolify-token-for-tests")
    return HeldSettings(
        cast(OrchestratorClient, o),
        lambda app: CoolifyClient(COOLIFY, token, app, transport=httpx.MockTransport(o.coolify)),
        APP,
        clock=clock,
        sleep=clock.sleep,
    )


LIVE_RUN = {"unit_id": "u1", "unit_key": "deps-bump", "unit_state": "executing"}


def test_a_write_sets_both_rows_restarts_once_and_waits_for_the_new_value(
    o: Orchestrator, clock: Clock
) -> None:
    _held(o, clock).write(CONSUMER, NEW)
    envs, restart = f"/api/v1/applications/{APP}/envs", f"/api/v1/applications/{APP}/restart"
    assert o.sent == [("PATCH", envs), ("PATCH", envs), ("POST", restart)]
    assert o.rows == {(KEY, True): NEW.reveal(), (KEY, False): NEW.reveal()}
    assert _held(o, clock).holds(CONSUMER, NEW)


def test_a_copy_already_loaded_with_the_value_is_not_written_again(
    o: Orchestrator, clock: Clock
) -> None:
    o.loaded = {KEY: NEW.reveal()}
    _held(o, clock).write(CONSUMER, NEW)
    assert o.sent == []


def test_a_live_run_before_the_write_stops_it_before_anything_is_sent(
    o: Orchestrator, clock: Clock
) -> None:
    o.live = [LIVE_RUN]
    with pytest.raises(ContractError, match="would strand a run: deps-bump"):
        _held(o, clock).write(CONSUMER, NEW)
    assert o.sent == []


def test_a_run_that_goes_live_after_the_write_stops_the_restart(
    o: Orchestrator, clock: Clock
) -> None:
    o.live_after_write = [LIVE_RUN]
    with pytest.raises(ContractError, match="would strand a run"):
        _held(o, clock).write(CONSUMER, NEW)
    assert [method for method, _ in o.sent] == ["PATCH", "PATCH"]
    assert o.loaded == {KEY: OLD.reveal()}


def test_an_orchestrator_that_never_loads_the_value_is_refused_after_the_wait(
    o: Orchestrator, clock: Clock
) -> None:
    o.restart_loads = False
    with pytest.raises(ContractError, match="didn't load the held copy in time"):
        _held(o, clock).write(CONSUMER, NEW)
    assert clock.now >= START + RESTART_WAIT


def test_a_restart_answered_only_after_it_comes_back_counts(o: Orchestrator, clock: Clock) -> None:
    # Already holding OLD is asked first, then two answers while the process restarts.
    o.scripted = [{KEY: _fingerprint(OLD.reveal())}, OrchestratorError("502"), {}]
    _held(o, clock).write(CONSUMER, NEW)
    assert clock.now > START


@pytest.mark.parametrize(
    "answer",
    [
        OrchestratorError("unreachable"),
        {},
        {KEY: "not a mapping"},
        {KEY: {"sha256_prefix": 7, "length": 19}},
        {KEY: {"sha256_prefix": _fingerprint(NEW.reveal())["sha256_prefix"], "length": "19"}},
        {KEY: _fingerprint(OLD.reveal())},
        {"ANOTHER_SETTING": _fingerprint(NEW.reveal())},
    ],
)
def test_holds_answers_false_and_never_raises_for_anything_but_the_value(
    o: Orchestrator, clock: Clock, answer: Any
) -> None:
    o.scripted = [answer]
    assert not _held(o, clock).holds(CONSUMER, NEW)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"instance": "dev"}, "only in a production application"),
        ({"resource_type": "service"}, "only in a production application"),
        ({"uuid": "re45tafypao3nly3qa9a79dp"}, "only the orchestrator reports"),
        ({"key": "ORCHESTRATOR_SOMETHING_ELSE"}, "doesn't report ORCHESTRATOR_SOMETHING_ELSE"),
    ],
)
def test_check_refuses_a_copy_it_could_not_write_or_confirm(
    o: Orchestrator, clock: Clock, change: dict[str, str], message: str
) -> None:
    with pytest.raises(ContractError, match=message):
        _held(o, clock).check({**CONSUMER, **change})
    with pytest.raises(ContractError, match=message):
        _held(o, clock).write({**CONSUMER, **change}, NEW)
    assert o.sent == []


def test_check_accepts_the_orchestrator_s_reported_setting(o: Orchestrator, clock: Clock) -> None:
    _held(o, clock).check(CONSUMER)


def test_no_value_reaches_an_error(o: Orchestrator, clock: Clock) -> None:
    o.restart_loads = False
    with pytest.raises(ContractError) as raised:
        _held(o, clock).write(CONSUMER, NEW)
    assert NEW.reveal() not in str(raised.value)
    assert OLD.reveal() not in str(raised.value)
