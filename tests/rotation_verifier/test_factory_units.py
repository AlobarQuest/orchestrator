"""The lane's pass over app-change units, against a fake orchestrator and a fake GitHub.

Bump and rotation units have their own files, which this widening leaves as they were.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from rotation_verifier import cli
from rotation_verifier.alerts import KINDS, REFUSED, UNREAD, Outcome, sync_alerts
from rotation_verifier.cli import exit_code, run_pass
from rotation_verifier.client import VerifierClient
from rotation_verifier.required_checks import NoRequiredChecks, RequiredChecksUnreadable

UNIT = "11111111-1111-4111-8111-111111111111"
BUMP = "22222222-2222-4222-8222-222222222222"
ROTATION = "33333333-3333-4333-8333-333333333333"
HEAD = "a" * 40
REPOSITORY = "AlobarQuest/sds-canary"
SERVICES = (
    "dependency_updates",
    "security_patches",
    "app_changes",
    "backups",
    "monitoring",
    "rollouts",
    "credential_rotation",
)
NOT_CONCLUDED = {"error": {"code": "named_check_not_concluded", "message": "not finished"}}


def _member(repository: str = REPOSITORY, *, app_changes: str = "granted", state: str = "member"):
    return {
        "repository": repository,
        "owner": "AlobarQuest",
        "state": state,
        "working_copy": None,
        "machine_local": False,
        "apps": [],
        "holds": [],
        "grants": {
            service: {"state": app_changes if service == "app_changes" else "absent"}
            for service in SERVICES
        },
    }


def _filed(conclusion: str = "success") -> httpx.Response:
    return httpx.Response(200, json={"payload": {"conclusion": conclusion}})


class Orchestrator:
    def __init__(
        self,
        units: dict[str, dict[str, Any]],
        *,
        checks: dict[str, httpx.Response] | None = None,
        members: httpx.Response | None = None,
        verdict: str = "completed",
    ) -> None:
        self.units = units
        self.checks = checks or {}
        self.members = members or httpx.Response(200, json={"members": [_member()]})
        self.verdict = verdict
        self.filed: list[dict[str, Any]] = []
        self.verified: list[str] = []
        self.member_reads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/status-ledger":
            rows = [
                {"unit_id": unit_id, "unit_key": unit["unit_key"]}
                for unit_id, unit in self.units.items()
                if request.url.params["state"] == "submitted"
            ]
            return httpx.Response(200, json=rows)
        if path == "/api/v1/members":
            self.member_reads += 1
            return self.members
        unit_id = path.split("/")[4]
        if path.endswith("/verifier-evidence/named-check"):
            command = json.loads(request.content)
            self.filed.append(command)
            return self.checks.get(command["check_name"], _filed())
        if path.endswith("/verify"):
            self.verified.append(unit_id)
            return httpx.Response(200, json={"state": self.verdict})
        return httpx.Response(200, json=self.units[unit_id])


def _app_change(**overrides: Any) -> dict[str, Any]:
    unit = {
        "unit_key": "client-request",
        "version": 4,
        "required_capability": "repo.edit",
        "work_package_revision_id": "44444444-4444-4444-8444-444444444444",
        "authority": {
            "change_class": "app-change",
            "constraints": {"target_repository": REPOSITORY},
        },
        "latest_dispatch_id": "55555555-5555-4555-8555-555555555555",
        "pr_number": 9,
        "armed_head_sha": HEAD,
        "automated_check_ac_ids": ["AC-001"],
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


def _pairs(orchestrator: Orchestrator) -> list[tuple[str, str]]:
    return [(c["ac_id"], c["check_name"]) for c in orchestrator.filed]


def test_every_criterion_gets_every_required_check_then_the_unit_is_verified() -> None:
    orchestrator = Orchestrator({UNIT: _app_change(automated_check_ac_ids=["AC-001", "AC-003"])})

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("scan", "Quality"))

    assert [o.result for o in outcomes] == ["completed"]
    assert _pairs(orchestrator) == [
        ("AC-001", "Quality"),
        ("AC-001", "scan"),
        ("AC-003", "Quality"),
        ("AC-003", "scan"),
    ]
    assert orchestrator.verified == [UNIT]


def test_a_second_criterion_never_shares_the_first_s_key() -> None:
    orchestrator = Orchestrator({UNIT: _app_change(automated_check_ac_ids=["AC-001", "AC-002"])})

    run_pass(_client(orchestrator), read_required_checks=_required("Quality"), pass_id="one")

    first, second = (c["idempotency_key"] for c in orchestrator.filed)
    assert first != second


def test_a_failed_check_stops_its_criterion_and_the_next_criterion_is_still_told() -> None:
    orchestrator = Orchestrator(
        {UNIT: _app_change(automated_check_ac_ids=["AC-001", "AC-002"])},
        checks={"Quality": _filed("failure")},
    )

    run_pass(_client(orchestrator), read_required_checks=_required("Quality", "scan"))

    assert _pairs(orchestrator) == [("AC-001", "Quality"), ("AC-002", "Quality")]
    assert orchestrator.verified == [UNIT]


def test_a_check_still_running_never_reaches_verify() -> None:
    orchestrator = Orchestrator(
        {UNIT: _app_change()},
        checks={"scan": httpx.Response(409, json=NOT_CONCLUDED)},
    )

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality", "scan"))

    assert [o.result for o in outcomes] == ["waiting"]
    assert orchestrator.verified == []
    assert exit_code(outcomes) == 0


def test_a_check_the_orchestrator_refuses_never_reaches_verify() -> None:
    refused = {"error": {"code": "named_check_not_found", "message": "no such job"}}
    orchestrator = Orchestrator(
        {UNIT: _app_change()}, checks={"scan": httpx.Response(409, json=refused)}
    )

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality", "scan"))

    assert [o.result for o in outcomes] == ["refused"]
    assert orchestrator.verified == []


def test_a_unit_with_no_automated_check_goes_straight_to_verify() -> None:
    orchestrator = Orchestrator({UNIT: _app_change(automated_check_ac_ids=[])})

    def forbidden(repository: str) -> tuple[str, ...]:
        raise AssertionError("no criterion takes a check, so no check is read")

    outcomes = run_pass(_client(orchestrator), read_required_checks=forbidden)

    assert [o.result for o in outcomes] == ["completed"]
    assert orchestrator.filed == []
    assert orchestrator.verified == [UNIT]


def test_a_unit_with_no_automated_check_needs_no_github_token() -> None:
    orchestrator = Orchestrator({UNIT: _app_change(automated_check_ac_ids=[])})

    outcomes = run_pass(_client(orchestrator))

    assert [o.result for o in outcomes] == ["completed"]


def test_a_server_that_serves_no_ids_goes_to_verify_unfiled() -> None:
    """A lane ahead of its server files nothing, so the criterion falls to a person."""
    unit = _app_change()
    del unit["automated_check_ac_ids"]
    orchestrator = Orchestrator({UNIT: unit}, verdict="awaiting_review")

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert [o.result for o in outcomes] == ["awaiting_review"]
    assert orchestrator.filed == []


def test_a_repository_requiring_nothing_goes_to_verify_unfiled() -> None:
    orchestrator = Orchestrator({UNIT: _app_change()}, verdict="awaiting_review")

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required())

    assert [o.result for o in outcomes] == ["awaiting_review"]
    assert orchestrator.filed == []
    assert orchestrator.verified == [UNIT]
    assert exit_code(outcomes) == 2


def test_an_unprotected_branch_goes_to_verify_unfiled() -> None:
    def unprotected(repository: str) -> tuple[str, ...]:
        raise NoRequiredChecks("GitHub answered 404")

    orchestrator = Orchestrator({UNIT: _app_change()}, verdict="awaiting_review")

    outcomes = run_pass(_client(orchestrator), read_required_checks=unprotected)

    assert [o.result for o in outcomes] == ["awaiting_review"]
    assert orchestrator.verified == [UNIT]


def test_unreadable_required_checks_hold_the_unit() -> None:
    def unreadable(repository: str) -> tuple[str, ...]:
        raise RequiredChecksUnreadable("GitHub answered 500")

    orchestrator = Orchestrator({UNIT: _app_change()})

    outcomes = run_pass(_client(orchestrator), read_required_checks=unreadable)

    assert [o.result for o in outcomes] == ["required_checks_unreadable"]
    assert orchestrator.verified == []


def test_post_deploy_units_stay_with_image_release() -> None:
    orchestrator = Orchestrator({UNIT: _app_change(required_capability="post_deploy_verification")})

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert outcomes == []
    assert orchestrator.verified == [] and orchestrator.filed == []


@pytest.mark.parametrize(
    "gap",
    [
        {"latest_dispatch_id": None},
        {"armed_head_sha": None},
    ],
)
def test_a_session_unit_with_no_dispatch_or_head_is_skipped(gap: dict[str, Any]) -> None:
    orchestrator = Orchestrator({UNIT: _app_change(**gap)})

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert outcomes == []
    assert orchestrator.member_reads == 0


def test_a_unit_missing_both_fields_is_skipped_without_a_key_error() -> None:
    unit = _app_change()
    del unit["latest_dispatch_id"], unit["armed_head_sha"], unit["required_capability"]
    orchestrator = Orchestrator({UNIT: unit})

    assert run_pass(_client(orchestrator), read_required_checks=_required("Quality")) == []


def test_a_dry_run_names_an_app_change_unit_and_reads_nothing_else() -> None:
    orchestrator = Orchestrator({UNIT: _app_change()})

    outcomes = run_pass(_client(orchestrator), dry_run=True)

    assert [o.result for o in outcomes] == ["would-verify"]
    assert orchestrator.member_reads == 0
    assert orchestrator.verified == [] and orchestrator.filed == []


def test_a_withdrawn_grant_holds_the_unit() -> None:
    orchestrator = Orchestrator(
        {UNIT: _app_change()},
        members=httpx.Response(200, json={"members": [_member(app_changes="withdrawn")]}),
    )

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert [(o.result, o.unit_id) for o in outcomes] == [(REFUSED, UNIT)]
    assert f"app_changes on {REPOSITORY}" in outcomes[0].detail
    assert orchestrator.filed == [] and orchestrator.verified == []
    assert exit_code(outcomes) == 2


def test_a_retired_member_s_grant_does_not_count() -> None:
    orchestrator = Orchestrator(
        {UNIT: _app_change()},
        members=httpx.Response(200, json={"members": [_member(state="retired")]}),
    )

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert [o.result for o in outcomes] == [REFUSED]


def test_a_repository_absent_from_a_record_that_reads_is_withdrawn() -> None:
    orchestrator = Orchestrator(
        {UNIT: _app_change()},
        members=httpx.Response(200, json={"members": [_member("AlobarQuest/other")]}),
    )

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert [o.result for o in outcomes] == [REFUSED]


def test_a_repository_compares_case_folded() -> None:
    orchestrator = Orchestrator(
        {UNIT: _app_change()},
        members=httpx.Response(200, json={"members": [_member(REPOSITORY.upper())]}),
    )

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert [o.result for o in outcomes] == ["completed"]


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(503),
        httpx.Response(200, json={"members": []}),
        httpx.Response(200, json={"members": [{"repository": REPOSITORY}]}),
        httpx.Response(200, json=["not the record"]),
    ],
)
def test_an_unread_record_holds_the_unit_and_is_no_withdrawal(answer: httpx.Response) -> None:
    orchestrator = Orchestrator({UNIT: _app_change()}, members=answer)

    outcomes = run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert [o.result for o in outcomes] == [UNREAD]
    assert orchestrator.filed == [] and orchestrator.verified == []


def test_rotation_and_bump_units_read_no_grant() -> None:
    bump = _app_change(
        unit_key="bump",
        authority={
            "change_class": "dependency-update",
            "constraints": {"target_repository": REPOSITORY},
        },
    )
    rotation = {
        "unit_key": "rot",
        "version": 2,
        "authority": {"constraints": {"rotation_unit": "retire"}},
    }
    orchestrator = Orchestrator({BUMP: bump, ROTATION: rotation})

    run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert orchestrator.member_reads == 0
    assert sorted(orchestrator.verified) == sorted([BUMP, ROTATION])


def test_a_bump_ignores_the_served_ids() -> None:
    bump = _app_change(
        automated_check_ac_ids=["AC-009"],
        authority={
            "change_class": "dependency-update",
            "constraints": {"target_repository": REPOSITORY},
        },
    )
    orchestrator = Orchestrator({BUMP: bump})

    run_pass(_client(orchestrator), read_required_checks=_required("Quality"))

    assert _pairs(orchestrator) == [("AC-001", "Quality")]


def test_the_grant_alert_says_why_survives_an_unread_record_and_resolves_on_the_same_key(
    tmp_path: Any,
) -> None:
    sent: list[dict[str, Any]] = []

    def hub(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(202, json={})

    def one_pass(members: httpx.Response) -> None:
        orchestrator = Orchestrator({UNIT: _app_change()}, members=members)
        cli.run(
            _client(orchestrator),
            dry_run=False,
            read_required_checks=_required("Quality"),
            alert_token="alert-token",
            state_dir=tmp_path,
            hub_transport=httpx.MockTransport(hub),
        )

    key = f"{KINDS[REFUSED]}:{UNIT}"
    one_pass(httpx.Response(200, json={"members": [_member(app_changes="withdrawn")]}))
    assert [(body["key"], body["status"]) for body in sent] == [(key, "firing")]
    assert f"does not grant app_changes on {REPOSITORY}" in json.dumps(sent[0])

    one_pass(httpx.Response(503))
    one_pass(httpx.Response(200, json={"members": []}))
    assert len(sent) == 1  # an unread record neither raises nor resolves it

    one_pass(httpx.Response(200, json={"members": [_member()]}))
    assert [(body["key"], body["status"]) for body in sent[1:]] == [(key, "resolved")]


def test_an_unread_record_raises_nothing_on_a_clean_slate(tmp_path: Any) -> None:
    sent: list[dict[str, Any]] = []

    def hub(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(202, json={})

    orchestrator = Orchestrator({UNIT: _app_change()}, members=httpx.Response(503))
    cli.run(
        _client(orchestrator),
        dry_run=False,
        read_required_checks=_required("Quality"),
        alert_token="alert-token",
        state_dir=tmp_path,
        hub_transport=httpx.MockTransport(hub),
    )

    assert sent == []


def test_a_withdrawn_grant_is_one_keyed_alert_with_a_next_step(tmp_path: Any) -> None:
    sent: list[dict[str, Any]] = []

    def hub(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(202, json={})

    problem = sync_alerts(
        [Outcome("client-request", REFUSED, f"no grant on {REPOSITORY}", UNIT)],
        token="alert-token",
        state_dir=tmp_path,
        transport=httpx.MockTransport(hub),
    )

    assert problem is None
    assert [body["key"] for body in sent] == [f"{KINDS[REFUSED]}:{UNIT}"]
    assert REPOSITORY in json.dumps(sent[0]) and "/review" in json.dumps(sent[0])
