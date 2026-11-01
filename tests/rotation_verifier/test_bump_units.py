"""The lane's pass over dependency-bump units, against a fake orchestrator and a fake GitHub."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from rotation_verifier.cli import exit_code, run_pass
from rotation_verifier.client import VerifierClient
from rotation_verifier.required_checks import RequiredChecksUnreadable

BUMP = "11111111-1111-4111-8111-111111111111"
ROTATION = "22222222-2222-4222-8222-222222222222"
HEAD = "a" * 40
NOT_CONCLUDED = {"error": {"code": "named_check_not_concluded", "message": "not finished"}}


def _filed(conclusion: str) -> httpx.Response:
    return httpx.Response(200, json={"payload": {"conclusion": conclusion}})


class Orchestrator:
    """Answers the routes the lane may reach; `checks` maps a check name to its filing answer."""

    def __init__(self, units: dict[str, dict[str, Any]], checks: dict[str, httpx.Response]):
        self.units = units
        self.checks = checks
        self.filed: list[dict[str, Any]] = []
        self.verified: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/status-ledger":
            state = request.url.params["state"]
            rows = [
                {"unit_id": i, "unit_key": u["unit_key"]}
                for i, u in self.units.items()
                if state == "submitted"
            ]
            return httpx.Response(200, json=rows)
        unit_id = path.split("/")[4]
        if path.endswith("/verifier-evidence/named-check"):
            command = json.loads(request.content)
            self.filed.append(command)
            return self.checks[command["check_name"]]
        if path.endswith("/verify"):
            self.verified.append(unit_id)
            return httpx.Response(200, json={"state": "completed"})
        return httpx.Response(200, json=self.units[unit_id])


def _bump(**overrides: Any) -> dict[str, Any]:
    unit = {
        "unit_key": "canary-bump",
        "version": 4,
        "work_package_revision_id": "33333333-3333-4333-8333-333333333333",
        "authority": {
            "change_class": "dependency-update",
            "constraints": {"target_repository": "AlobarQuest/sds-canary"},
        },
        "latest_dispatch_id": "44444444-4444-4444-8444-444444444444",
        "pr_number": 9,
        "armed_head_sha": HEAD,
    }
    return {**unit, **overrides}


def _client(orchestrator: Orchestrator) -> VerifierClient:
    return VerifierClient(
        base_url="https://sds.example",
        credential_key_id="orchestrator-verifier",
        token="verifier-token",
        transport=httpx.MockTransport(orchestrator),
    )


def _required(*names: str):
    return lambda repository: names


def test_a_bump_unit_files_each_required_check_in_sorted_order_then_verifies() -> None:
    orchestrator = Orchestrator(
        {BUMP: _bump()}, {"Quality": _filed("success"), "scan": _filed("success")}
    )

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("scan", "Quality"))

    assert [(o.unit_key, o.result) for o in outcomes] == [("canary-bump", "completed")]
    assert [c["check_name"] for c in orchestrator.filed] == ["Quality", "scan"]
    first = orchestrator.filed[0]
    assert first == {
        "work_package_revision_id": "33333333-3333-4333-8333-333333333333",
        "ac_id": "AC-001",
        "dispatch_id": "44444444-4444-4444-8444-444444444444",
        "repository": "AlobarQuest/sds-canary",
        "pr_number": 9,
        "pr_url": "https://github.com/AlobarQuest/sds-canary/pull/9",
        "head_sha": HEAD,
        "check_name": "Quality",
        "expected_conclusion": "success",
        "expected_version": 4,
        "idempotency_key": first["idempotency_key"],
    }
    assert orchestrator.verified == [BUMP]


def test_a_check_still_running_waits_and_does_not_verify() -> None:
    orchestrator = Orchestrator(
        {BUMP: _bump()}, {"Quality": httpx.Response(409, json=NOT_CONCLUDED)}
    )

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert [(o.unit_key, o.result) for o in outcomes] == [("canary-bump", "waiting")]
    assert orchestrator.verified == []
    assert exit_code(outcomes) == 0


def test_a_wait_after_a_success_stops_at_the_waiting_check() -> None:
    orchestrator = Orchestrator(
        {BUMP: _bump()},
        {"Quality": _filed("success"), "scan": httpx.Response(409, json=NOT_CONCLUDED)},
    )

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality", "scan"))

    assert [o.result for o in outcomes] == ["waiting"]
    assert orchestrator.verified == []


def test_a_failed_check_stops_filing_and_the_unit_goes_to_the_verifier() -> None:
    orchestrator = Orchestrator(
        {BUMP: _bump()}, {"Quality": _filed("failure"), "scan": _filed("success")}
    )

    run_pass(_client(orchestrator), read_required_checks=_required("Quality", "scan"))

    assert [c["check_name"] for c in orchestrator.filed] == ["Quality"]
    assert orchestrator.verified == [BUMP]


def test_keys_are_distinct_per_check_and_per_pass() -> None:
    orchestrator = Orchestrator(
        {BUMP: _bump()}, {"Quality": _filed("success"), "scan": _filed("success")}
    )
    client = _client(orchestrator)

    run_pass(client, read_required_checks=_required("Quality", "scan"), pass_id="one")
    run_pass(client, read_required_checks=_required("Quality", "scan"), pass_id="one")
    run_pass(client, read_required_checks=_required("Quality", "scan"), pass_id="two")

    keys = [c["idempotency_key"] for c in orchestrator.filed]
    assert keys[0] == keys[2]  # inside a pass id a filing is the same claim, so it replays
    assert keys[1] == keys[3]
    assert keys[0] != keys[1]
    assert keys[4] != keys[0]  # a new pass observes afresh
    assert all(len(key) <= 200 for key in keys)


def test_each_pass_without_an_injected_id_gets_its_own() -> None:
    orchestrator = Orchestrator({BUMP: _bump()}, {"Quality": _filed("success")})
    client = _client(orchestrator)

    run_pass(client, read_required_checks=_required("Quality"))
    run_pass(client, read_required_checks=_required("Quality"))

    assert orchestrator.filed[0]["idempotency_key"] != orchestrator.filed[1]["idempotency_key"]


@pytest.mark.parametrize("conclusion", ["skipped", "neutral", "cancelled"])
def test_a_check_that_did_not_succeed_stops_filing(conclusion: str) -> None:
    orchestrator = Orchestrator(
        {BUMP: _bump()}, {"Quality": _filed(conclusion), "scan": _filed("success")}
    )

    run_pass(_client(orchestrator), read_required_checks=_required("Quality", "scan"))

    assert [c["check_name"] for c in orchestrator.filed] == ["Quality"]
    assert orchestrator.verified == [BUMP]


def test_a_new_head_files_under_new_keys() -> None:
    first = Orchestrator({BUMP: _bump()}, {"Quality": _filed("success")})
    second = Orchestrator({BUMP: _bump(armed_head_sha="b" * 40)}, {"Quality": _filed("success")})

    run_pass(_client(first), read_required_checks=_required("Quality"))
    run_pass(_client(second), read_required_checks=_required("Quality"))

    assert first.filed[0]["idempotency_key"] != second.filed[0]["idempotency_key"]


def test_zero_required_checks_is_a_finding_and_the_unit_is_not_verified() -> None:
    orchestrator = Orchestrator({BUMP: _bump()}, {})

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required())

    assert [(o.result, o.detail) for o in outcomes] == [
        ("bump_unit_no_required_check", "AlobarQuest/sds-canary")
    ]
    assert orchestrator.verified == []
    assert orchestrator.filed == []
    assert exit_code(outcomes) == 2


def test_unreadable_required_checks_is_a_finding_and_the_unit_is_not_verified() -> None:
    def unreadable(repository: str) -> tuple[str, ...]:
        raise RequiredChecksUnreadable("GitHub answered 404")

    orchestrator = Orchestrator({BUMP: _bump()}, {})

    outcomes = run_pass(_client(orchestrator), read_required_checks=unreadable)

    assert [o.result for o in outcomes] == ["required_checks_unreadable"]
    assert orchestrator.verified == []
    assert exit_code(outcomes) == 2


def test_a_unit_with_no_dispatch_yet_is_refused_not_filed() -> None:
    orchestrator = Orchestrator(
        {BUMP: _bump(latest_dispatch_id=None)}, {"Quality": _filed("success")}
    )

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert [o.result for o in outcomes] == ["refused"]
    assert orchestrator.filed == []
    assert exit_code(outcomes) == 2


def test_no_github_token_refuses_a_bump_unit_and_leaves_a_rotation_unit_alone() -> None:
    rotation = {
        "unit_key": "rot",
        "version": 2,
        "authority": {"constraints": {"rotation_unit": "retire"}},
    }
    orchestrator = Orchestrator({BUMP: _bump(), ROTATION: rotation}, {})

    outcomes = run_pass(_client(orchestrator))

    assert sorted((o.unit_key, o.result) for o in outcomes) == [
        ("canary-bump", "refused"),
        ("rot", "completed"),
    ]
    assert orchestrator.verified == [ROTATION]


def test_a_rotation_unit_is_verified_without_reading_any_check() -> None:
    rotation = {
        "unit_key": "rot",
        "version": 2,
        "authority": {"constraints": {"rotation_unit": "retire"}},
    }
    orchestrator = Orchestrator({ROTATION: rotation}, {})

    def forbidden(repository: str) -> tuple[str, ...]:
        raise AssertionError("a rotation unit has no required checks to read")

    outcomes = run_pass(_client(orchestrator), read_required_checks=forbidden)

    assert [(o.unit_key, o.result) for o in outcomes] == [("rot", "completed")]
    assert orchestrator.filed == []


def test_a_dry_run_files_and_verifies_nothing() -> None:
    orchestrator = Orchestrator({BUMP: _bump()}, {})

    outcomes = run_pass(_client(orchestrator), dry_run=True)

    assert [o.result for o in outcomes] == ["would-verify"]
    assert orchestrator.filed == [] and orchestrator.verified == []
