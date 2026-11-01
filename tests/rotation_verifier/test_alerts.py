"""What the verifier lane tells a person, and that telling never gets in the way of verifying."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from rotation_verifier import alerts, cli
from rotation_verifier.alerts import (
    CHECKS_UNREADABLE,
    NO_REQUIRED_CHECK,
    REFUSED,
    Outcome,
    sync_alerts,
)
from rotation_verifier.client import VerifierClient
from rotation_verifier.required_checks import RequiredChecksUnreadable

UNIT = "11111111-1111-4111-8111-111111111111"
ROTATION = "22222222-2222-4222-8222-222222222222"
REPOSITORY = "AlobarQuest/sds-canary"


class Hub:
    def __init__(self, status: int = 202) -> None:
        self.status = status
        self.sent: list[tuple[str, str]] = []
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.sent.append((body["key"], body["status"]))
        self.bodies.append(body)
        return httpx.Response(self.status, json={})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


class Orchestrator:
    """A queue of one bump unit and one rotation unit, or an unreadable queue."""

    def __init__(
        self, *, readable: bool = True, bump_answer: int = 200, bump_queued: bool = True
    ) -> None:
        self.readable = readable
        self.bump_answer = bump_answer
        self.bump_queued = bump_queued
        self.verified: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/status-ledger":
            if not self.readable:
                return httpx.Response(503)
            if request.url.params["state"] != "submitted":
                return httpx.Response(200, json=[])
            rows = [{"unit_id": ROTATION, "unit_key": "rot"}]
            if self.bump_queued:
                rows.append({"unit_id": UNIT, "unit_key": "bump"})
            return httpx.Response(200, json=rows)
        unit_id = path.split("/")[4]
        if path.endswith("/verify"):
            self.verified.append(unit_id)
            return httpx.Response(200, json={"state": "completed"})
        if unit_id == ROTATION:
            return httpx.Response(
                200,
                json={
                    "unit_key": "rot",
                    "version": 2,
                    "authority": {"constraints": {"rotation_unit": "retire"}},
                },
            )
        return httpx.Response(
            self.bump_answer,
            json={
                "unit_key": "bump",
                "version": 4,
                "authority": {
                    "change_class": "dependency-update",
                    "constraints": {"target_repository": REPOSITORY},
                },
            },
        )


def _client(orchestrator: Orchestrator) -> VerifierClient:
    return VerifierClient(
        base_url="https://sds.example",
        credential_key_id="orchestrator-verifier",
        token="verifier-token",
        transport=httpx.MockTransport(orchestrator),
    )


def _run(
    orchestrator: Orchestrator,
    hub: Hub,
    state: Path,
    *,
    token: str = "alert-token",
    dry_run: bool = False,
    read=None,
) -> int:
    return cli.run(
        _client(orchestrator),
        dry_run=dry_run,
        read_required_checks=read,
        alert_token=token,
        state_dir=state,
        hub_transport=hub.transport,
    )


def _unreadable(repository: str) -> tuple[str, ...]:
    raise RequiredChecksUnreadable("GitHub answered 404")


@pytest.mark.parametrize(
    ("result", "kind"),
    [
        (REFUSED, "verify-refused"),
        (CHECKS_UNREADABLE, "verify-checks-unreadable"),
        (NO_REQUIRED_CHECK, "verify-no-required-check"),
    ],
)
def test_each_refusal_fires_one_key_with_the_next_step(
    tmp_path: Path, result: str, kind: str
) -> None:
    hub = Hub()

    problem = sync_alerts(
        [Outcome("bump", result, "why", UNIT)],
        token="t",
        state_dir=tmp_path,
        transport=hub.transport,
    )

    assert problem is None
    assert hub.sent == [(f"{kind}:{UNIT}", "firing")]
    assert "bump" in hub.bodies[0]["title"]
    assert "why" in hub.bodies[0]["message"]
    assert len(hub.bodies[0]["message"].split(". ")) >= 2


@pytest.mark.parametrize("result", ["completed", "awaiting_review", "waiting", "would-verify"])
def test_an_outcome_that_is_not_a_refusal_fires_nothing(tmp_path: Path, result: str) -> None:
    hub = Hub()
    sync_alerts(
        [Outcome("bump", result, "", UNIT)],
        token="t",
        state_dir=tmp_path,
        transport=hub.transport,
    )
    assert hub.sent == []


def test_a_refusal_that_clears_resolves(tmp_path: Path) -> None:
    hub = Hub()
    for outcomes in ([Outcome("bump", REFUSED, "x", UNIT)], []):
        sync_alerts(outcomes, token="t", state_dir=tmp_path, transport=hub.transport)
    assert hub.sent == [
        (f"verify-refused:{UNIT}", "firing"),
        (f"verify-refused:{UNIT}", "resolved"),
    ]


def test_no_kind_is_a_substring_of_another() -> None:
    kinds = list(alerts.KINDS.values())
    assert len(set(kinds)) == 3
    for kind in kinds:
        assert not any(kind != other and kind in other for other in kinds)


def test_a_refused_unit_is_a_finding_with_one_alert_and_no_failure(tmp_path: Path) -> None:
    """2 is the launcher's `--finding` code, so this cause pings success and raises the alert."""
    hub = Hub()

    code = _run(Orchestrator(bump_answer=500), hub, tmp_path)

    assert code == 2
    assert hub.sent == [(f"verify-refused:{UNIT}", "firing")]


def test_a_finding_keeps_its_code_when_the_rest_of_the_queue_is_clean(tmp_path: Path) -> None:
    orchestrator = Orchestrator()
    hub = Hub()
    code = _run(orchestrator, hub, tmp_path, read=_unreadable)
    assert code == 2
    assert orchestrator.verified == [ROTATION]
    assert hub.sent == [(f"verify-checks-unreadable:{UNIT}", "firing")]


def test_a_failed_send_leaves_every_verify_done_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    orchestrator = Orchestrator()

    code = _run(orchestrator, Hub(status=500), tmp_path, read=_unreadable)

    assert orchestrator.verified == [ROTATION]
    assert code == 3  # the alert did not reach anyone, so the dead-man check must
    assert "alert-hub" in capsys.readouterr().err


def test_no_alert_token_verifies_everything_it_can(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    orchestrator = Orchestrator()
    hub = Hub()

    code = _run(orchestrator, hub, tmp_path, token="", read=_unreadable)

    assert orchestrator.verified == [ROTATION]
    assert hub.sent == []
    assert code == 3
    assert "alert" in capsys.readouterr().err


def test_no_alert_token_keeps_what_was_fired(tmp_path: Path) -> None:
    hub = Hub()
    _run(Orchestrator(bump_answer=500), hub, tmp_path)
    remembered = (tmp_path / alerts.ALERTS_FILE).read_text()

    _run(Orchestrator(), hub, tmp_path, token="", read=lambda repository: ())

    assert (tmp_path / alerts.ALERTS_FILE).read_text() == remembered


def test_an_unusable_state_directory_verifies_everything_it_can(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("")
    orchestrator = Orchestrator()

    code = _run(orchestrator, Hub(), blocked / "state", read=_unreadable)

    assert orchestrator.verified == [ROTATION]
    assert code == 3


def test_an_unread_queue_holds_every_key(tmp_path: Path) -> None:
    hub = Hub()
    _run(Orchestrator(bump_answer=500), hub, tmp_path)
    hub.sent.clear()

    code = _run(Orchestrator(readable=False), hub, tmp_path)

    assert code == 3
    assert hub.sent == []
    assert json.loads((tmp_path / alerts.ALERTS_FILE).read_text()) == [f"verify-refused:{UNIT}"]


def test_a_clean_pass_after_an_unread_one_resolves(tmp_path: Path) -> None:
    hub = Hub()
    _run(Orchestrator(bump_answer=500), hub, tmp_path)
    _run(Orchestrator(readable=False), hub, tmp_path)
    hub.sent.clear()

    code = _run(Orchestrator(bump_queued=False), hub, tmp_path)

    assert hub.sent == [(f"verify-refused:{UNIT}", "resolved")]
    assert code == 0


def test_a_dry_run_tells_nobody_and_keeps_what_was_fired(tmp_path: Path) -> None:
    hub = Hub()
    _run(Orchestrator(bump_answer=500), hub, tmp_path)
    hub.sent.clear()

    code = _run(Orchestrator(), hub, tmp_path, token="", dry_run=True)

    assert code == 0
    assert hub.sent == []
    assert json.loads((tmp_path / alerts.ALERTS_FILE).read_text()) == [f"verify-refused:{UNIT}"]


def test_the_state_directory_is_outside_the_lanes_name() -> None:
    """Part 2 renames the lane; the fired keys must survive that."""
    assert alerts.state_directory({}, Path("/h")) == Path(
        "/h/Library/Application Support/sds/verifier"
    )
    assert alerts.state_directory({alerts.STATE_DIR_ENV: "/s"}, Path("/h")) == Path("/s")
