"""Telling a person a rotation waits on them: one alert per act, resolved once it's done, and never
a failed unit or pass (ADR-0055 amendment 15, increment 3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from rotation_worker.contract import ContractError
from rotation_worker.notify import ALERTS, Act, Notifier
from rotation_worker.orchestrator_client import OrchestratorError
from rotation_worker.secret_store import StoreError
from rotation_worker.values import Value
from rotation_worker.worker import Setup, run_pass, waiting_acts

TOKEN = "alert-hub-sender-token-for-tests"


class Hub:
    """alert-hub's `/v1/alerts`, answering each request with the next status in `answers`."""

    def __init__(self, *answers: int) -> None:
        self.sent: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self._answers = list(answers)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", ALERTS)
        self.sent.append(json.loads(request.content))
        self.headers.append(request.headers)
        return httpx.Response(self._answers.pop(0) if self._answers else 202)

    def notifier(self, state: Path) -> Notifier:
        return Notifier(lambda: Value(TOKEN), state, httpx.MockTransport(self))


ACT = Act(
    "rotation-act:d1",
    "A rotation waits on you: x-confirm",
    "Revoke the old token.",
    "https://sds.alobar.net/review/units/x",
)


def test_a_waiting_act_fires_with_the_sender_token_and_resolves_once_done(tmp_path: Path) -> None:
    hub = Hub()
    hub.notifier(tmp_path).announce({ACT.key: ACT})
    assert hub.sent == [
        {
            "key": "rotation-act:d1",
            "severity": "warning",
            "kind": "alert",
            "status": "firing",
            "title": "A rotation waits on you: x-confirm",
            "message": "Revoke the old token.",
            "link": "https://sds.alobar.net/review/units/x",
        }
    ]
    assert hub.headers[0]["Authorization"] == f"Bearer {TOKEN}"
    assert hub.headers[0]["User-Agent"].startswith("rotation-worker/")
    hub.notifier(tmp_path).announce({})
    assert [(m["key"], m["status"]) for m in hub.sent[1:]] == [("rotation-act:d1", "resolved")]
    hub.notifier(tmp_path).announce({})
    assert len(hub.sent) == 2


def test_an_open_act_is_resent_every_pass(tmp_path: Path) -> None:
    hub = Hub()
    for _ in range(3):
        hub.notifier(tmp_path).announce({ACT.key: ACT})
    assert [m["status"] for m in hub.sent] == ["firing"] * 3


@pytest.mark.parametrize("refused", [400, 403, 500])
def test_an_act_never_announced_is_never_resolved(tmp_path: Path, refused: int) -> None:
    hub = Hub(refused)
    hub.notifier(tmp_path).announce({ACT.key: ACT})
    hub.notifier(tmp_path).announce({})
    assert [m["status"] for m in hub.sent] == ["firing"]


def test_a_failed_resolve_is_tried_again_next_pass(tmp_path: Path) -> None:
    hub = Hub(202, 503)
    hub.notifier(tmp_path).announce({ACT.key: ACT})
    hub.notifier(tmp_path).announce({})
    hub.notifier(tmp_path).announce({})
    assert [m["status"] for m in hub.sent] == ["firing", "resolved", "resolved"]


def test_a_failed_resend_keeps_an_announced_act_open(tmp_path: Path) -> None:
    hub = Hub(202, 500)
    hub.notifier(tmp_path).announce({ACT.key: ACT})
    hub.notifier(tmp_path).announce({ACT.key: ACT})
    hub.notifier(tmp_path).announce({})
    assert [m["status"] for m in hub.sent] == ["firing", "firing", "resolved"]


def test_an_unreadable_token_sends_nothing_and_raises_nothing(tmp_path: Path) -> None:
    hub = Hub()

    def unreadable() -> Value:
        raise StoreError("no item")

    Notifier(unreadable, tmp_path, httpx.MockTransport(hub)).announce({ACT.key: ACT})
    assert hub.sent == []


def test_a_transport_failure_raises_nothing(tmp_path: Path) -> None:
    def refused(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    Notifier(lambda: Value(TOKEN), tmp_path, httpx.MockTransport(refused)).announce({ACT.key: ACT})


def test_a_corrupt_state_file_is_read_as_nothing_announced(tmp_path: Path) -> None:
    (tmp_path / "announced-acts.json").write_text("{not json")
    hub = Hub()
    hub.notifier(tmp_path).announce({})
    assert hub.sent == []


class Ledger:
    """The status ledger and unit reads, as `waiting_acts` asks them."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.units: dict[str, dict[str, Any]] = {}

    def add(self, unit_id: str, adapter: str | None, *blockers: dict[str, Any]) -> None:
        constraints = {"rotation_adapter": adapter} if adapter else {}
        self.units[unit_id] = {"id": unit_id, "authority": {"constraints": constraints}}
        self.rows.append(
            {
                "unit_id": unit_id,
                "unit_key": unit_id,
                "unit_title": f"{unit_id}: title",
                "blockers": list(blockers),
            }
        )

    def active_units(self) -> list[dict[str, Any]]:
        return self.rows

    def unit(self, unit_id: str) -> dict[str, Any]:
        return self.units[unit_id]

    def ready_units(self) -> list[str]:
        return []


def _act(
    dependency: str, ref: str | None, status: str = "pending", condition: str = "satisfied"
) -> dict[str, Any]:
    return {
        "dependency_id": dependency,
        "kind": "external_system",
        "external_ref": ref,
        "required_state_or_condition": condition,
        "status": status,
    }


def _setup(ledger: object, notifier: Notifier | None = None, kind: str = "bws-secret") -> Setup:
    store = type("Store", (), {"read_back_kind": kind})()
    return cast(
        Setup,
        type("S", (), {"client": ledger, "store": store, "notifier": notifier})(),
    )


def test_only_this_instance_s_rotation_units_blocked_on_a_person_are_announced() -> None:
    ledger = Ledger()
    ledger.add("pat-store", "github-pat", _act("d1", "Devon mints a PAT"))
    ledger.add("bws-confirm", "bws-machine-token", _act("d2", "Devon revokes the token"))
    ledger.add("factory-unit", None, _act("d3", "someone approves"))
    work = {"dependency_id": "d4", "kind": "work_unit", "external_ref": None, "status": "pending"}
    ledger.add("pat-retire", "github-pat", work)
    ledger.add("canary-store", "fake")
    ledger.add("misspelt", "github-pa", _act("d5", "an adapter nobody knows"))
    ledger.add("pat-failed", "github-pat", _act("d6", "a recorded failure", status="failed"))
    acts, unread = waiting_acts(_setup(ledger))
    assert (list(acts), unread) == (["rotation-act:d1"], set())
    assert acts["rotation-act:d1"] == Act(
        "rotation-act:d1",
        "A rotation waits on you: pat-store: title",
        "Devon mints a PAT. Record it on the unit's review page.",
        "https://sds.alobar.net/review/units/pat-store",
    )
    assert list(waiting_acts(_setup(ledger, kind="keychain"))[0]) == ["rotation-act:d2"]


def test_an_act_with_no_text_says_its_condition_and_a_long_title_fits() -> None:
    ledger = Ledger()
    ledger.add("x" * 300, "github-pat", _act("d1", None, condition="the PAT is minted"))
    act = waiting_acts(_setup(ledger))[0]["rotation-act:d1"]
    assert act.message.startswith("the PAT is minted.")
    assert len(act.title) == 200


def test_an_unreadable_unit_keeps_its_alert_open_and_others_are_announced(tmp_path: Path) -> None:
    class Flaky(Ledger):
        def unit(self, unit_id: str) -> dict[str, Any]:
            if unit_id == "flaky":
                raise OrchestratorError("the unit didn't answer")
            return super().unit(unit_id)

    ledger = Flaky()
    ledger.add("flaky", "github-pat", _act("d1", "Devon mints a PAT"))
    ledger.add("pat-store", "github-pat", _act("d2", "Devon mints another"))
    hub = Hub()
    notifier = hub.notifier(tmp_path)
    notifier.announce({"rotation-act:d1": Act("rotation-act:d1", "t", "m")})
    acts, unread = waiting_acts(_setup(ledger))
    assert (list(acts), unread) == (["rotation-act:d2"], {"rotation-act:d1"})
    notifier.announce(acts, unread)
    notifier.announce({}, {"rotation-act:d1"})
    # d1 was never resolved while unread; d2 was fired, then resolved once gone.
    assert [(m["key"], m["status"]) for m in hub.sent] == [
        ("rotation-act:d1", "firing"),
        ("rotation-act:d2", "firing"),
        ("rotation-act:d2", "resolved"),
    ]


def test_an_unread_ledger_announces_nothing_and_fails_no_pass(tmp_path: Path) -> None:
    class Broken(Ledger):
        def active_units(self) -> list[dict[str, Any]]:
            raise RuntimeError("the ledger didn't answer")

    hub = Hub()
    notifier = hub.notifier(tmp_path)
    notifier.announce({ACT.key: ACT})
    assert run_pass(_setup(Broken(), notifier)) == 0
    # Nothing was resolved: an unread ledger isn't an empty one.
    assert [m["status"] for m in hub.sent] == ["firing"]


def test_a_pass_without_a_sender_token_reads_no_acts(caplog: pytest.LogCaptureFixture) -> None:
    class Counted(Ledger):
        asked = 0

        def active_units(self) -> list[dict[str, Any]]:
            self.asked += 1
            return super().active_units()

    ledger = Counted()
    assert run_pass(_setup(ledger)) == 0
    assert ledger.asked == 0
    assert "acts_unread" not in caplog.text


@pytest.mark.parametrize(("method", "path"), [("GET", ALERTS), ("POST", "/admin/senders")])
def test_the_client_may_send_nothing_but_an_alert(tmp_path: Path, method: str, path: str) -> None:
    client = Hub().notifier(tmp_path)._client()
    assert client is not None
    with pytest.raises(ContractError, match="alert-hub may not"):
        client.request(method, path)


def test_a_state_file_that_can_t_be_written_raises_nothing(tmp_path: Path) -> None:
    hub = Hub()
    hub.notifier(tmp_path / "missing-directory").announce({ACT.key: ACT})
    assert [m["status"] for m in hub.sent] == ["firing"]
