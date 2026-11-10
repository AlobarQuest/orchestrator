"""ADR-0055 amendment 19: a lane's BWS access token and its Healthchecks ping URL.

Each invariant the amendment adds has a test that fails when its check is removed: the issue
plan's own rule, the marker as the old value, the issue's store and cleanup, the lane token's
reach bind, the one `PATCH` with no restart, and the keeper command. The ping URL's read from the
Healthchecks listing (amendment 23) is `test_ping_url_read.py`'s.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import re
import sys
import textwrap
import types
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest

from rotation_worker import cli
from rotation_worker.adapters import ADAPTERS
from rotation_worker.adapters import bws_lane_token as lane
from rotation_worker.adapters import bws_machine_token as bws
from rotation_worker.adapters import healthchecks_ping_url as ping
from rotation_worker.checks import _check_destination_fit, check_unit
from rotation_worker.config import Config
from rotation_worker.contract import (
    EMPTY,
    Actor,
    ContractError,
    HumanAct,
    Plan,
    Rotation,
    Step,
    StepKind,
    UnitPlan,
    UnitRun,
    validate_issue_plan,
    validate_plan,
)
from rotation_worker.coolify import CoolifyClient, CoolifyRefused
from rotation_worker.destinations import credential_destinations
from rotation_worker.evidence import StepEvidence
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.inbox import bind
from rotation_worker.plans import candidates, derive, derive_issue, writes_hosted_service
from rotation_worker.readers import CHECKS, HEALTHCHECKS_URL, Healthchecks, PingUrls, ReadError
from rotation_worker.registry import RegistryEntry
from rotation_worker.types import CLASS_TYPES, TYPES
from rotation_worker.values import Value
from tests.rotation_worker.conftest import Origins
from tests.rotation_worker.stores import MemoryStore
from tests.rotation_worker.test_checks import WORK as CHECKS_WORK
from tests.rotation_worker.test_checks import World, _acceptance, _package

WORK = "work-project"
KEEPER = "11111111-2222-4333-8444-555555555555"
SECRET_A = "aaaaaaaa-0000-4000-8000-000000000001"
SECRET_B = "aaaaaaaa-0000-4000-8000-000000000002"
SERVICE = "lanesvc0000000000000000"
COOLIFY_URL = "https://coolify.example.test"
VARIABLE = (
    f'coolify-env instance="prod" resource_type="service" uuid="{SERVICE}" '
    'key="EXECUTION_WATCH_BWS_ACCESS_TOKEN" redeploy=false'
)
KEEPER_DESTINATION = f'bws-secret uuid="{KEEPER}"'
LANE_DESTINATIONS = (KEEPER_DESTINATION, 'provider name="bws-lane-token"', VARIABLE)
PING_DESTINATIONS = (KEEPER_DESTINATION, 'provider name="healthchecks-ping-url"')
# Placeholders: no BWS token's shape, and no real check.
NEW_TOKEN = Value("lane-token-placeholder-new")
OLD_TOKEN = Value("lane-token-placeholder-old")
PING_URL = Value(f"{ping.PING_BASE}00000000-0000-4000-8000-0000000000aa")
# Its check's `unique_key`, worked by hand from Healthchecks `v4.4` `Check.unique_key`
# (`hashlib.sha1(self.code.hex[:16].encode()).hexdigest()`): `code.hex[:16]` is
# "0000000000004000", and `printf %s 0000000000004000 | shasum -a 1` gives this.
PING_KEY = "07c99f22ce44ac16c776ebcecbb911799bcc675c"
# Its first 16 hex characters differ from the URL's: a `unique_key` reads only those.
CONTROL = uuid.UUID("ffffffff-0000-4000-8000-0000000000bb")
HC_KEY = Value("hc-read-only-key-placeholder")
PING_CHECK = "sds-example-lane"
M, H = Actor.MACHINE, Actor.HUMAN


class HealthchecksStub:
    """The self-hosted Healthchecks behind a mock transport: the URL's check answers `found`,
    every other `unique_key` answers `control`, or every request raises `error`."""

    def __init__(
        self, found: int = 200, control: int = 404, error: Exception | None = None
    ) -> None:
        self.found, self.control, self.error = found, control, error
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        key = request.url.path.removeprefix(CHECKS)
        return httpx.Response(self.found if key == PING_KEY else self.control, json={})

    def client(self) -> Healthchecks:
        return Healthchecks(lambda: HC_KEY, transport=httpx.MockTransport(self))


def _listing(request: httpx.Request) -> httpx.Response:
    """The Management key's listing: one check, the lane's, with `PING_URL`."""
    checks = [{"name": PING_CHECK, "ping_url": PING_URL.reveal()}]
    return httpx.Response(200, json={"checks": checks})


def _ping(stub: HealthchecksStub | None = None) -> ping.HealthchecksPingUrlProvider:
    healthchecks = (stub or HealthchecksStub()).client()
    urls = PingUrls(lambda: Value("hc-management-key-placeholder"), httpx.MockTransport(_listing))
    return ping.HealthchecksPingUrlProvider(
        healthchecks, random_uuid=lambda: CONTROL, ping_urls=urls, check=PING_CHECK
    )


def _row(**changes: Any) -> dict[str, Any]:
    return {
        "id": "execution-watch-bws-token",
        "class": "bws-lane-token",
        "provider": "bws-lane-token",
        "bws_uuid": KEEPER,
        "rotated_by_sds": True,
        "reach_secrets": [SECRET_A, SECRET_B],
        "consumer": [
            {"kind": "bws-secret", "uuid": KEEPER},
            {
                "kind": "coolify-env",
                "instance": "prod",
                "resource_type": "service",
                "uuid": SERVICE,
                "key": "EXECUTION_WATCH_BWS_ACCESS_TOKEN",
                "redeploy": False,
            },
        ],
        **changes,
    }


# ---- the types and their plans -------------------------------------------------------------


def _shape(plan: Plan) -> list[tuple[str, list[str]]]:
    return [(s.name, [step.kind.value for step in s.steps]) for s in plan.segments]


def test_both_classes_map_to_their_own_type_kept_on_the_vm() -> None:
    for name in ("bws-lane-token", "healthchecks-ping-url"):
        assert CLASS_TYPES[name] == name
        assert TYPES[name].store_kind == "bws-secret"
        assert ADAPTERS[name].provider_name == name
        assert ADAPTERS[name].capabilities == TYPES[name].capabilities


def test_a_lane_token_issues_and_reissues_and_writes_its_variable_at_distribute() -> None:
    capabilities = TYPES["bws-lane-token"].capabilities
    plans = candidates(capabilities, LANE_DESTINATIONS, KEEPER_DESTINATION, "bws-secret")
    assert [_shape(plan) for plan in plans] == [
        [
            ("mint", ["mint", "stage"]),
            ("store", ["verify_new", "store", "distribute"]),
            ("verify", ["verify_consumers"]),
            ("revoke", ["retire_old"]),
            ("confirm", ["confirm_dead"]),
        ],
        [
            ("mint", ["mint", "stage"]),
            ("store", ["verify_new", "store", "distribute", "verify_consumers"]),
        ],
    ]
    assert [plan.issue for plan in plans] == [False, True]


def test_a_ping_url_is_only_ever_issued_and_has_no_copy_to_distribute() -> None:
    capabilities = TYPES["healthchecks-ping-url"].capabilities
    plans = candidates(capabilities, PING_DESTINATIONS, KEEPER_DESTINATION, "bws-secret")
    assert [_shape(plan) for plan in plans] == [
        [("store", ["mint", "stage", "verify_new", "store", "verify_consumers"])]
    ]
    assert TYPES["healthchecks-ping-url"].verify_by_shape
    # It reads a URL Healthchecks already holds: its mint creates nothing (amendment 23).
    assert capabilities.machine_mint and not capabilities.creates


def test_a_type_is_declared_proved_by_shape_exactly_when_its_provider_is() -> None:
    """The package's wording reads the type; the executor's verify_new reads the provider."""
    providers = {
        lane.NAME: lane.BwsLaneTokenProvider,
        ping.NAME: ping.HealthchecksPingUrlProvider,
        bws.NAME: bws.BwsMachineTokenProvider,
    }
    for name, provider in providers.items():
        assert TYPES[name].verify_by_shape is hasattr(provider, "shape_problem"), name


def test_no_existing_type_issues_or_writes_a_variable_at_distribute() -> None:
    for name, declared in TYPES.items():
        if name in ("bws-lane-token", "healthchecks-ping-url"):
            continue
        capabilities = declared.capabilities
        assert capabilities.rotates and not capabilities.issues, name
        assert not capabilities.env_at_distribute, name


def test_a_coolify_variable_is_distributed_only_by_a_type_that_writes_it_there() -> None:
    plain = TYPES["github-pat"].capabilities
    plan = derive(plain, LANE_DESTINATIONS, KEEPER_DESTINATION, "bws-secret")
    assert StepKind.DISTRIBUTE not in {step.kind for step in plan.steps()}


def test_only_an_unhosted_type_that_declares_an_issue_has_one() -> None:
    for name in ("openrouter", "orchestrator", "bitbucket"):
        with pytest.raises(ContractError, match="declares an issue"):
            derive_issue(TYPES[name].capabilities, (), "", "bws-secret")


ISSUE = [
    (StepKind.MINT, H),
    (StepKind.STAGE, H),
    (StepKind.VERIFY_NEW, M),
    (StepKind.STORE, M),
    (StepKind.VERIFY_CONSUMERS, M),
]


def _issue(steps: list[tuple[StepKind, Actor]]) -> Plan:
    human = [Step(kind, actor) for kind, actor in steps if actor is H]
    machine = [
        Step(kind, actor, f"AC-00{n}")
        for n, (kind, actor) in enumerate((s for s in steps if s[1] is M), start=1)
    ]
    return Plan((HumanAct("mint", tuple(human)), UnitPlan("store", tuple(machine))), issue=True)


def test_the_issue_plan_is_valid_by_its_own_rule_and_never_by_a_rotation_s() -> None:
    plan = _issue(ISSUE)
    validate_issue_plan(plan)
    with pytest.raises(ContractError, match="its own rule"):
        validate_plan(plan)
    undeclared = Plan(plan.segments)
    with pytest.raises(ContractError, match="declared an issue"):
        validate_issue_plan(undeclared)
    with pytest.raises(ContractError, match="exactly one retire_old"):
        validate_plan(undeclared)


@pytest.mark.parametrize(
    "steps",
    [
        [*ISSUE, (StepKind.CONFIRM_DEAD, M)],
        [*ISSUE[:4], (StepKind.RETIRE_OLD, M), ISSUE[4]],
        [ISSUE[0], ISSUE[1], ISSUE[3], ISSUE[2], ISSUE[4]],
        ISSUE[:4],
        [*ISSUE, (StepKind.VERIFY_CONSUMERS, M)],
        [*ISSUE[:2], (StepKind.HOST, M), *ISSUE[2:]],
        [*ISSUE[:4], (StepKind.VERIFY_CONSUMERS, H)],
    ],
)
def test_an_issue_plan_that_could_break_an_invariant_is_refused(
    steps: list[tuple[StepKind, Actor]],
) -> None:
    with pytest.raises(ContractError):
        validate_issue_plan(_issue(steps))


def test_an_issue_whose_consumers_a_person_verifies_is_refused() -> None:
    plan = Plan(
        (
            HumanAct("mint", (Step(StepKind.MINT, H), Step(StepKind.STAGE, H))),
            UnitPlan(
                "store",
                (Step(StepKind.VERIFY_NEW, M, "AC-001"), Step(StepKind.STORE, M, "AC-002")),
            ),
            HumanAct("verify", (Step(StepKind.VERIFY_CONSUMERS, H),)),
        ),
        issue=True,
    )
    with pytest.raises(ContractError, match="after the mint is a machine"):
        validate_issue_plan(plan)


MACHINE_ISSUE = [
    Step(StepKind.MINT, M, "AC-001"),
    Step(StepKind.STAGE, M, "AC-002"),
    Step(StepKind.VERIFY_NEW, M, "AC-003"),
    Step(StepKind.STORE, M, "AC-004"),
    Step(StepKind.VERIFY_CONSUMERS, M, "AC-005"),
]


def test_an_issue_minted_by_a_machine_is_one_unit_by_the_issue_s_rule() -> None:
    """ADR-0055 amendment 23: a machine's mint and stage share the issue's one unit."""
    validate_issue_plan(Plan((UnitPlan("store", tuple(MACHINE_ISSUE)),), issue=True))
    with pytest.raises(ContractError, match="its own rule"):
        validate_plan(Plan((UnitPlan("store", tuple(MACHINE_ISSUE)),), issue=True))


@pytest.mark.parametrize(
    "segments",
    [
        # Stage in a unit after the machine's mint: the minted value would exist only in memory.
        (
            UnitPlan("mint", tuple(MACHINE_ISSUE[:1])),
            UnitPlan("store", tuple(MACHINE_ISSUE[1:])),
        ),
        # A machine's mint before a person's stage.
        (
            UnitPlan("mint", tuple(MACHINE_ISSUE[:1])),
            HumanAct("stage", (Step(StepKind.STAGE, H),)),
            UnitPlan("store", tuple(MACHINE_ISSUE[2:])),
        ),
        # A machine-minted issue with a retire, or with its steps out of order.
        (UnitPlan("store", (*MACHINE_ISSUE, Step(StepKind.CONFIRM_DEAD, M, "AC-006"))),),
        (
            UnitPlan(
                "store", (*MACHINE_ISSUE[:2], MACHINE_ISSUE[3], MACHINE_ISSUE[2], MACHINE_ISSUE[4])
            ),
        ),
    ],
    ids=["split-stage", "mixed-actors", "confirm-dead", "out-of-order"],
)
def test_a_machine_minted_issue_that_could_break_an_invariant_is_refused(
    segments: tuple[UnitPlan | HumanAct, ...],
) -> None:
    with pytest.raises(ContractError):
        validate_issue_plan(Plan(segments, issue=True))


def test_a_unit_writing_the_lane_s_variable_is_never_marked_non_hosted() -> None:
    capabilities = TYPES["bws-lane-token"].capabilities
    reissue, issue = candidates(capabilities, LANE_DESTINATIONS, KEEPER_DESTINATION, "bws-secret")
    for plan in (reissue, issue):
        for unit in (s for s in plan.segments if isinstance(s, UnitPlan)):
            writes = {StepKind.DISTRIBUTE, StepKind.VERIFY_CONSUMERS} & {
                step.kind for step in unit.steps
            }
            assert writes_hosted_service(LANE_DESTINATIONS, plan, unit) is bool(writes)


# ---- the issue, through the contract --------------------------------------------------------


class Recorded:
    def __init__(self) -> None:
        self.filed: list[tuple[Step, StepEvidence]] = []

    def __call__(self, step: Step, evidence: StepEvidence) -> None:
        self.filed.append((step, evidence))

    def kinds(self) -> list[str]:
        return [evidence.step for _, evidence in self.filed]


class Issue:
    """One credential's issue: a keeper holding the marker and a value staged for it."""

    def __init__(self, provider: Any, destinations: tuple[str, ...], staged: Value | None) -> None:
        self.store = MemoryStore()
        self.store.secrets[KEEPER] = ("keepers", "keeper", Value(EMPTY))
        self.rotation = Rotation("lane", "2026-11-07-requested", KEEPER, WORK, destinations)
        if staged is not None:
            self.store.create(WORK, self.rotation.staging_key, staged)
        self.provider = provider
        self.recorded = Recorded()

    def run(self, plan: Plan, unit: str = "store", **changes: Any) -> None:
        run = UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.provider,
            attestations=(),
            record=self.recorded,
            before_step=lambda: None,
            filed=frozenset(StepKind(kind) for kind in self.recorded.kinds()),
            issue=plan.issue,
        )
        for name, value in changes.items():
            setattr(run, name, value)
        run.run(plan.unit(unit))

    def keeper(self) -> Value:
        return self.store.read(KEEPER)


PING_PLAN = candidates(
    TYPES["healthchecks-ping-url"].capabilities, PING_DESTINATIONS, KEEPER_DESTINATION, "bws-secret"
)[0]


def test_a_ping_url_issue_reads_stores_and_proves_it_and_deletes_staging() -> None:
    issue = Issue(_ping(), PING_DESTINATIONS, None)
    issue.run(PING_PLAN)
    assert issue.keeper().same_as(PING_URL)
    assert issue.store.find(WORK, issue.rotation.quarantine_key) is None
    assert issue.store.find(WORK, issue.rotation.staging_key) is None
    assert issue.recorded.kinds() == [
        "mint",
        "stage",
        "verify_new",
        "store",
        "verify_consumers",
        "verify_consumers",
    ]
    verify_new = issue.recorded.filed[2][1]
    assert [(p.subject, p.http_status, p.outcome) for p in verify_new.probes] == [
        ("known_bad", 404, "dead"),
        ("new", 200, "live"),
    ]
    assert verify_new.note is not None and PING_KEY[:12] in verify_new.note
    assert "deleted staging" in str(issue.recorded.filed[-1][1].note)
    # The marker stands as the old value in every fingerprint pair.
    for _, evidence in issue.recorded.filed:
        assert dict(evidence.fingerprints)["old"] == Value(EMPTY).fingerprint
    # Finished: run again, nothing happens and nothing is filed.
    issue.run(PING_PLAN)
    assert len(issue.recorded.filed) == 6


SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "rotation_evidence_schema.json").read_text()
)


def test_every_issue_payload_conforms_to_the_orchestrator_s_schema() -> None:
    issues = [
        (Issue(_ping(), PING_DESTINATIONS, PING_URL), PING_PLAN),
        (Issue(_lane([]), LANE_DESTINATIONS, NEW_TOKEN), LANE_ISSUE),
    ]
    for issue, plan in issues:
        issue.run(plan)
        for _, evidence in issue.recorded.filed:
            jsonschema.validate(evidence.as_payload(), SCHEMA)
            filed = json.dumps(evidence.as_payload())
            assert PING_URL.reveal() not in filed and NEW_TOKEN.reveal() not in filed
            assert PING_URL.reveal().removeprefix(ping.PING_BASE) not in filed
            assert PING_KEY not in filed and HC_KEY.reveal() not in filed


def test_a_ping_url_of_another_shape_is_never_stored() -> None:
    wrong = Value("https://hc-ping.com/00000000-0000-4000-8000-0000000000aa")
    issue = Issue(_ping(), PING_DESTINATIONS, wrong)
    with pytest.raises(ContractError, match="ping base"):
        issue.run(PING_PLAN)
    assert issue.keeper().reveal() == EMPTY


def test_a_staged_value_equal_to_the_marker_is_refused() -> None:
    issue = Issue(_ping(), PING_DESTINATIONS, Value(EMPTY))
    with pytest.raises(ContractError, match="equals the old"):
        issue.run(PING_PLAN)


def test_a_retried_store_finding_the_staged_value_in_the_keeper_counts_as_done() -> None:
    issue = Issue(_ping(), PING_DESTINATIONS, PING_URL)
    issue.store.write(KEEPER, PING_URL)
    issue.run(PING_PLAN)
    store = issue.recorded.filed[3][1]
    assert store.step == "store" and "earlier attempt" in str(store.note)


def test_an_issue_store_on_a_keeper_holding_another_value_fails_and_writes_nothing() -> None:
    issue = Issue(_ping(), PING_DESTINATIONS, PING_URL)
    issue.store.write(KEEPER, Value("a-value-nobody-staged"))
    with pytest.raises(ContractError, match="neither the marker"):
        issue.run(PING_PLAN)
    assert issue.keeper().reveal() == "a-value-nobody-staged"


def test_an_issue_with_a_quarantine_is_refused() -> None:
    issue = Issue(_ping(), PING_DESTINATIONS, PING_URL)
    issue.store.create(WORK, issue.rotation.quarantine_key, Value("quarantined"))
    with pytest.raises(ContractError, match="quarantines nothing"):
        issue.run(PING_PLAN)


def test_an_issue_store_quarantines_nothing() -> None:
    issue = Issue(_ping(), PING_DESTINATIONS, PING_URL)
    issue.run(PING_PLAN)
    assert all("quarantine" not in key for _, key, _ in issue.store.secrets.values())


# ---- a lane token: one PATCH, no restart ----------------------------------------------------


class Login(bws.BwsMachineTokenProvider):
    """A fresh login and `_REACH`, answered from tables instead of a child process."""

    def __init__(self, live: set[str], reach: dict[str, str]) -> None:
        super().__init__(organization_id="org")
        self.live, self.reaches = live, reach

    def probe(self, value: Value) -> int | None:
        return 200 if value.reveal() in self.live else 400

    def known_bad(self) -> Value:
        return Value("known-bad-placeholder")

    def reach(self, value: Value) -> str | None:
        return self.reaches.get(value.reveal())


DECLARED = bws.reach_digest((), (SECRET_A, SECRET_B))


def _coolify(answers: list[int], seen: list[httpx.Request]) -> Callable[[], CoolifyClient]:
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(answers.pop(0) if answers else 200, json={})

    return lambda: CoolifyClient(
        COOLIFY_URL,
        Value("throwaway-coolify-token"),
        "service",
        SERVICE,
        httpx.MockTransport(answer),
        restarts=False,
    )


def _lane(
    seen: list[httpx.Request],
    *,
    answers: list[int] | None = None,
    live: set[str] | None = None,
    reach: dict[str, str] | None = None,
    siblings: list[str] | None = None,
) -> lane.BwsLaneTokenProvider:
    return lane.BwsLaneTokenProvider(
        Login(live or {NEW_TOKEN.reveal()}, reach or {NEW_TOKEN.reveal(): DECLARED}),
        DECLARED,
        lane.lane_variable(LANE_DESTINATIONS, KEEPER_DESTINATION),
        _coolify(answers or [], seen),
        lambda: siblings or [],
    )


LANE_REISSUE, LANE_ISSUE = candidates(
    TYPES["bws-lane-token"].capabilities, LANE_DESTINATIONS, KEEPER_DESTINATION, "bws-secret"
)


def test_a_lane_token_issue_writes_the_variable_once_and_never_restarts() -> None:
    seen: list[httpx.Request] = []
    issue = Issue(_lane(seen), LANE_DESTINATIONS, NEW_TOKEN)
    issue.run(LANE_ISSUE)
    assert [(r.method, r.url.path) for r in seen] == [("PATCH", f"/api/v1/services/{SERVICE}/envs")]
    assert json.loads(seen[0].content)["key"] == "EXECUTION_WATCH_BWS_ACCESS_TOKEN"
    assert issue.keeper().same_as(NEW_TOKEN)
    assert issue.store.find(WORK, issue.rotation.staging_key) is None
    assert "and 1 Coolify variable(s), with no restart" in str(issue.recorded.filed[2][1].note)


def test_a_variable_with_no_row_fails_the_unit_and_is_never_skipped() -> None:
    seen: list[httpx.Request] = []
    issue = Issue(_lane(seen, answers=[404]), LANE_DESTINATIONS, NEW_TOKEN)
    with pytest.raises(CoolifyRefused):
        issue.run(LANE_ISSUE)
    assert "verify_consumers" not in issue.recorded.kinds()
    assert issue.store.find(WORK, issue.rotation.staging_key) is not None


def test_a_reissue_s_verify_unit_proves_the_variable_by_its_patch() -> None:
    seen: list[httpx.Request] = []
    issue = Issue(_lane(seen, live={NEW_TOKEN.reveal(), OLD_TOKEN.reveal()}), (), None)
    issue.rotation = Rotation("lane", "2026-11-07-requested", KEEPER, WORK, LANE_DESTINATIONS)
    issue.store.write(KEEPER, NEW_TOKEN)
    issue.store.create(WORK, issue.rotation.staging_key, NEW_TOKEN)
    issue.store.create(WORK, issue.rotation.quarantine_key, OLD_TOKEN)
    issue.run(LANE_REISSUE, "verify", person_revokes_next=True, old_may_be_revoked=True)
    assert [r.method for r in seen] == ["PATCH"]
    assert "the old value is live before the revoke" in str(issue.recorded.filed[-1][1].note)


def test_after_a_leak_the_old_token_may_already_be_refused_before_the_person_s_revoke() -> None:
    seen: list[httpx.Request] = []
    issue = Issue(_lane(seen), (), None)
    issue.rotation = Rotation("lane", "2026-11-07-requested", KEEPER, WORK, LANE_DESTINATIONS)
    issue.store.write(KEEPER, NEW_TOKEN)
    issue.store.create(WORK, issue.rotation.staging_key, NEW_TOKEN)
    issue.store.create(WORK, issue.rotation.quarantine_key, OLD_TOKEN)
    issue.run(LANE_REISSUE, "verify", person_revokes_next=True, old_may_be_revoked=True)
    assert "the old value is dead before the revoke" in str(issue.recorded.filed[-1][1].note)
    # Without the type's declaration, a refused old value stops the unit as before.
    with pytest.raises(ContractError, match="expected live"):
        issue.run(LANE_REISSUE, "verify", person_revokes_next=True)


def test_the_lane_token_s_coolify_client_cannot_restart() -> None:
    seen: list[httpx.Request] = []
    client = _coolify([], seen)()
    assert not client.permits("POST", f"/api/v1/services/{SERVICE}/restart")
    with pytest.raises(ContractError, match="may not POST"):
        client.restart()
    assert seen == []


def test_only_the_approved_variable_is_written() -> None:
    seen: list[httpx.Request] = []
    provider = _lane(seen)
    other = {**lane.lane_variable(LANE_DESTINATIONS, KEEPER_DESTINATION), "key": "OTHER"}
    with pytest.raises(ContractError, match="only variable"):
        provider.write_variable(other, NEW_TOKEN)
    assert seen == []


# ---- binding a pasted value ------------------------------------------------------------------


def _bind(
    provider: Any, keeper: Value, new: Value, others: list[tuple[str, Value]] | None = None
) -> tuple[str | None, MemoryStore, Rotation]:
    store = MemoryStore()
    store.secrets[KEEPER] = ("keepers", "keeper", keeper)
    rotation = Rotation("lane", "2026-11-07-requested", KEEPER, WORK, LANE_DESTINATIONS)
    reason = bind(
        new,
        rotation=rotation,
        store=store,
        provider=provider,
        others_waiting=[],
        other_keepers=lambda: others or [],
    )
    return reason, store, rotation


def test_a_lane_token_seeing_exactly_its_declared_reach_binds_to_its_issue() -> None:
    reason, store, rotation = _bind(_lane([]), Value(EMPTY), NEW_TOKEN)
    assert reason is None
    staged = store.find(WORK, rotation.staging_key)
    assert staged is not None and store.read(staged.id).same_as(NEW_TOKEN)


def test_a_lane_token_binds_after_a_leak_s_early_revoke_without_asking_the_old_one() -> None:
    # The old token is refused: `same_identity` would refuse, the declared reach doesn't.
    reason, _, _ = _bind(_lane([]), OLD_TOKEN, NEW_TOKEN)
    assert reason is None


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"reach": {NEW_TOKEN.reveal(): bws.reach_digest((), (SECRET_A,))}}, "declares"),
        ({"reach": {"another-token": DECLARED}}, "declares"),
        ({"live": {"something-else"}}, "fresh login"),
        ({"siblings": ["sync-watch-bws-token"]}, "same reach for sync-watch-bws-token"),
    ],
)
def test_a_lane_token_that_isn_t_the_lane_s_stays_in_the_inbox(
    changes: dict[str, Any], match: str
) -> None:
    reason, store, rotation = _bind(_lane([], **changes), Value(EMPTY), NEW_TOKEN)
    assert reason is not None and match in reason
    assert store.find(WORK, rotation.staging_key) is None
    assert NEW_TOKEN.reveal() not in reason


def test_a_lane_token_another_keeper_holds_is_refused() -> None:
    reason, _, _ = _bind(_lane([]), Value(EMPTY), NEW_TOKEN, [("other-lane", NEW_TOKEN)])
    assert reason == "it is other-lane's value"


@pytest.mark.parametrize(
    "text",
    [
        "http://healthcheck.alobar.net/ping/00000000-0000-4000-8000-0000000000aa",
        "https://healthcheck.alobar.net/ping/00000000-0000-4000-8000-0000000000AA",
        "https://healthcheck.alobar.net/ping/00000000-0000-4000-8000-0000000000aa/start",
        "https://healthcheck.alobar.net/ping/key/slug",
        "https://hc-ping.com/00000000-0000-4000-8000-0000000000aa",
        "https://healthcheck.alobar.net.example/ping/00000000-0000-4000-8000-0000000000aa",
    ],
)
def test_a_staged_value_that_isn_t_a_self_hosted_ping_url_is_refused_without_naming_it(
    text: str,
) -> None:
    issue = Issue(_ping(), PING_DESTINATIONS, Value(text))
    with pytest.raises(ContractError, match="ping base") as refused:
        issue.run(PING_PLAN)
    assert text not in str(refused.value)
    assert issue.keeper().reveal() == EMPTY


def test_nothing_ever_sends_a_ping_url_anything() -> None:
    """Kills: a probe that pings the URL, which would mark the check up."""
    stub = HealthchecksStub()
    issue = Issue(_ping(stub), PING_DESTINATIONS, None)
    issue.run(PING_PLAN)
    assert stub.requests
    for request in stub.requests:
        assert (request.method, request.url.host) == ("GET", "healthcheck.alobar.net")
        assert request.url.path.startswith(CHECKS) and "/ping/" not in str(request.url)
        assert request.headers["X-Api-Key"] == HC_KEY.reveal()
    with pytest.raises(ContractError):
        _ping(stub).probe_before_revoke(PING_URL)


def test_the_unique_key_is_healthchecks_own_derivation() -> None:
    assert ping.unique_key(PING_URL) == PING_KEY
    # Independently of the module: the hyphenless hex form's first 16 characters, SHA-1'd.
    code = uuid.UUID(PING_URL.reveal().removeprefix(ping.PING_BASE))
    assert hashlib.sha1(code.hex[:16].encode()).hexdigest() == PING_KEY
    assert ping.unique_key(Value(f"{ping.PING_BASE}{CONTROL}")) != PING_KEY
    # Only the first half counts: a UUID that differs only in its second half is the same check.
    same_half = Value(f"{ping.PING_BASE}00000000-0000-4000-ffff-ffffffffffff")
    assert ping.unique_key(same_half) == PING_KEY


def test_the_lookup_and_its_control_are_one_target_on_the_listing_s_host() -> None:
    stub = HealthchecksStub()
    issue = Issue(_ping(stub), PING_DESTINATIONS, PING_URL)
    issue.run(PING_PLAN)
    assert [r.url.path for r in stub.requests] == [
        CHECKS + ping.unique_key(Value(f"{ping.PING_BASE}{CONTROL}")),
        CHECKS + PING_KEY,
    ]
    assert all(str(r.url).startswith(HEALTHCHECKS_URL) for r in stub.requests)
    targets = {probe.target for probe in issue.recorded.filed[2][1].probes}
    assert targets == {ping.HealthchecksPingUrlProvider.probe_target}


@pytest.mark.parametrize(
    ("stub", "reason"),
    [
        (HealthchecksStub(found=404), "probed dead (404)"),
        (HealthchecksStub(control=200), "didn't refuse a known-bad value (200)"),
        (HealthchecksStub(found=500), "probed indeterminate (500)"),
        (HealthchecksStub(error=httpx.ConnectError("unreachable")), "known-bad value (None)"),
    ],
    ids=["no-such-check", "control-found", "server-error", "no-answer"],
)
def test_a_lookup_that_doesn_t_prove_the_check_fails_verify_new_and_stores_nothing(
    stub: HealthchecksStub, reason: str
) -> None:
    issue = Issue(_ping(stub), PING_DESTINATIONS, None)
    with pytest.raises(ContractError, match=re.escape(reason)):
        issue.run(PING_PLAN)
    assert issue.keeper().reveal() == EMPTY
    assert issue.recorded.kinds() == ["mint", "stage"]


def test_a_ping_url_with_no_healthchecks_key_configured_is_refused() -> None:
    provider = ping.HealthchecksPingUrlProvider(None)
    with pytest.raises(ContractError, match="read-only Healthchecks key"):
        provider.probe(PING_URL)
    issue = Issue(provider, PING_DESTINATIONS, PING_URL)
    with pytest.raises(ContractError, match="read-only Healthchecks key"):
        issue.run(PING_PLAN)
    assert issue.keeper().reveal() == EMPTY


def test_a_lookup_with_no_answer_logs_the_check_by_its_key_s_start_only(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A test that ran alembic's `fileConfig` on this xdist worker disabled the existing loggers.
    monkeypatch.setattr(logging.getLogger("rotation_worker"), "disabled", False)
    stub = HealthchecksStub(error=httpx.ConnectError("unreachable"))
    with caplog.at_level("WARNING", logger="rotation_worker"):
        assert stub.client().check_status(PING_KEY) is None
    (record,) = [r for r in caplog.records if r.getMessage() == "healthchecks_unanswered"]
    assert vars(record)["check"] == PING_KEY[:12]
    assert vars(record)["error_type"] == "ConnectError"
    logged = json.dumps([vars(r) for r in caplog.records], default=str)
    assert PING_KEY not in logged and HC_KEY.reveal() not in logged


def test_the_client_looks_up_only_a_unique_key_shaped_path() -> None:
    client = HealthchecksStub().client()
    for bad in (
        "../ping/x",
        PING_KEY.upper(),
        PING_KEY[:-1],
        "00000000-0000-4000-8000-0000000000aa",
    ):
        with pytest.raises(ReadError):
            client.check_status(bad)


# ---- what `_REACH` lists for a lane's account ----------------------------------------------


@contextlib.contextmanager
def _sdk(projects: list[str], secrets: list[str]) -> Iterator[None]:
    """A `bitwarden_sdk` whose account sees these ids, imported by `_REACH` in-process."""

    def listing(ids: list[str]) -> Any:
        rows = [types.SimpleNamespace(id=i) for i in ids]
        return types.SimpleNamespace(data=types.SimpleNamespace(data=rows))

    class Client:
        def auth(self) -> Any:
            return types.SimpleNamespace(login_access_token=lambda token: None)

        def projects(self) -> Any:
            return types.SimpleNamespace(list=lambda organization: listing(projects))

        def secrets(self) -> Any:
            return types.SimpleNamespace(list=lambda organization: listing(secrets))

    module = types.ModuleType(bws.CHILD_SDK)
    setattr(module, "BitwardenClient", Client)
    saved = sys.modules.get(bws.CHILD_SDK)
    sys.modules[bws.CHILD_SDK] = module
    try:
        yield
    finally:
        if saved is None:
            del sys.modules[bws.CHILD_SDK]
        else:
            sys.modules[bws.CHILD_SDK] = saved


def _run_reach(monkeypatch: pytest.MonkeyPatch, projects: list[str], secrets: list[str]) -> str:
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("org\nlane-token-placeholder"))
    with _sdk(projects, secrets), contextlib.redirect_stdout(out):
        exec(compile(bws._REACH, "<_REACH>", "exec"), {})
    return out.getvalue().strip()


def test_reach_lists_a_direct_grant_account_as_its_secrets_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lane's account has direct secret grants only (amendment 6). If the SDK lists no project
    for it, `_REACH` hashes the secrets alone, which the row declares with `reach_projects`
    empty; if the SDK lists the granted secrets' projects too, the row names them there."""
    assert _run_reach(monkeypatch, [], [SECRET_B, SECRET_A]) == bws.reach_digest(
        (), (SECRET_A, SECRET_B)
    )
    assert _run_reach(monkeypatch, ["p1"], [SECRET_A]) == bws.reach_digest(("p1",), (SECRET_A,))
    assert bws.reach_digest(("p1",), (SECRET_A,)) != bws.reach_digest((), (SECRET_A,))
    assert bws.reach_digest((), ()) == hashlib.sha256(b"").hexdigest()


def test_a_row_declaring_projects_beside_secrets_binds_on_both() -> None:
    projects, secrets = lane.declared_reach(
        _row(reach_projects=[SECRET_B], reach_secrets=[SECRET_A])
    )
    assert (projects, secrets) == ({SECRET_B}, {SECRET_A})


# ---- the row, refused before a claim --------------------------------------------------------


def test_a_lane_token_row_with_its_one_variable_passes() -> None:
    lane.check_row(_row())
    assert sorted(credential_destinations(_row())) == sorted(LANE_DESTINATIONS)


def _variable(**changes: Any) -> list[dict[str, Any]]:
    consumers = _row()["consumer"]
    return [consumers[0], {**consumers[1], **changes}]


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"consumer": _variable(redeploy=True)}, "redeploy false"),
        ({"consumer": _variable(resource_type="application")}, "production service"),
        ({"consumer": _variable(instance="dev")}, "production service"),
        ({"consumer": _variable(key="BWS_TOKEN")}, "_BWS_ACCESS_TOKEN"),
        ({"consumer": _row()["consumer"][:1]}, "exactly one coolify-env"),
        (
            {"consumer": [*_row()["consumer"], {"kind": "bws-secret", "uuid": SECRET_A}]},
            "exactly one coolify-env",
        ),
        ({"reach_secrets": []}, "reach_secrets"),
        ({"reach_secrets": [SECRET_A.upper()]}, "lowercase"),
        ({"reach_secrets": [SECRET_A, SECRET_A]}, "twice"),
        ({"reach_projects": "p"}, "reach_projects"),
    ],
)
def test_a_lane_token_row_the_adapter_couldn_t_act_on_is_refused(
    changes: dict[str, Any], match: str
) -> None:
    with pytest.raises(ContractError, match=match):
        lane.check_row(_row(**changes))


def test_two_rows_declaring_the_same_reach_are_named() -> None:
    def entry(**changes: Any) -> RegistryEntry:
        return RegistryEntry("AlobarQuest/orchestrator", "c" * 40, _row(**changes))

    rows = [entry(), entry(id="sync-watch"), entry(id="other", reach_secrets=[SECRET_A])]
    assert lane.sharing_reach(rows, _row()) == ["sync-watch"]
    rows.append(entry(id="unreadable", reach_secrets=[]))
    assert lane.sharing_reach(rows, _row()) == ["sync-watch", "unreadable"]


def test_a_ping_url_row_names_only_its_keeper() -> None:
    row = {
        "id": "x",
        "provider": "healthchecks-ping-url",
        "healthchecks_check": PING_CHECK,
        "consumer": [{"kind": "bws-secret", "uuid": KEEPER}],
    }
    ping.check_row(row)
    with pytest.raises(ContractError, match="one destination"):
        ping.check_row(
            {**row, "consumer": [*row["consumer"], {"kind": "bws-secret", "uuid": SECRET_A}]}
        )


# ---- the keeper command ---------------------------------------------------------------------


KEEPERS = "33333333-3333-4333-8333-333333333333"


def _config(tmp_path: Path, keepers: str | None = KEEPERS) -> Config:
    return Config(
        orchestrator_url="https://sds.example",
        credential_key_id="rotation-executor-1",
        organization_id="11111111-1111-4111-8111-111111111111",
        work_project_id=WORK,
        bearer_secret_id=None,
        root_token_file=tmp_path / "root-token",
        state_dir=tmp_path,
        source_revision="0123456789ab",
        keepers_project_id=keepers,
    )


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    memory = MemoryStore()
    monkeypatch.setattr(cli, "open_store", lambda config: memory)
    return memory


def test_create_keeper_holds_the_marker_and_prints_only_its_id(
    tmp_path: Path, opened: MemoryStore, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.create_keeper(_config(tmp_path), "execution-watch-bws-token") == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed.keys() == {"secret_id"}
    assert opened.secrets[printed["secret_id"]][:2] == (KEEPERS, "execution-watch-bws-token")
    assert opened.secrets[printed["secret_id"]][2].reveal() == EMPTY


def test_create_keeper_refuses_an_existing_key_and_overwrites_nothing(
    tmp_path: Path, opened: MemoryStore, capsys: pytest.CaptureFixture[str]
) -> None:
    opened.put(KEEPERS, "execution-watch-bws-token", Value("an-issued-value"))
    assert cli.create_keeper(_config(tmp_path), "execution-watch-bws-token") == cli.EXIT_UNREADABLE
    assert "already exists" in capsys.readouterr().err
    assert [v.reveal() for _, _, v in opened.secrets.values()] == ["an-issued-value"]


@pytest.mark.parametrize(
    ("keepers", "credential"),
    [(None, "execution-watch-bws-token"), (KEEPERS, "Execution Watch"), (KEEPERS, "a--b")],
)
def test_create_keeper_refuses_a_missing_project_or_a_malformed_id(
    tmp_path: Path, opened: MemoryStore, keepers: str | None, credential: str
) -> None:
    assert cli.create_keeper(_config(tmp_path, keepers), credential) == cli.EXIT_UNREADABLE
    assert opened.secrets == {}


def test_create_keeper_is_a_command_that_takes_no_value(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(cli, "load", lambda path: _config(Path("/nonexistent")))
    monkeypatch.setattr(
        cli, "create_keeper", lambda config, credential: seen.append(credential) or 0
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("never read"))
    assert cli.main(["create-keeper", "execution-watch-bws-token"]) == 0
    assert seen == ["execution-watch-bws-token"]
    assert sys.stdin.read() == "never read"
    with pytest.raises(SystemExit):
        cli.main(["create-keeper", "execution-watch-bws-token", "a-value"])


# ---- the checks before a claim --------------------------------------------------------------


def _lane_registry() -> str:
    return textwrap.dedent(
        f"""
        [[credential]]
        id = "canary"
        class = "bws-lane-token"
        provider = "bws-lane-token"
        bws_uuid = "{KEEPER}"
        rotated_by_sds = true
        reach_secrets = ["{SECRET_A}", "{SECRET_B}"]

          [[credential.consumer]]
          kind = "bws-secret"
          uuid = "{KEEPER}"

          [[credential.consumer]]
          kind = "coolify-env"
          instance = "prod"
          resource_type = "service"
          uuid = "{SERVICE}"
          key = "EXECUTION_WATCH_BWS_ACCESS_TOKEN"
          redeploy = false
        """
    ).replace("\n        ", "\n")


@pytest.fixture
def world(origins: Origins, mirror: RepoMirror) -> World:
    world = World(origins, mirror)
    world.registry(_lane_registry())
    return world


def _check(
    world: World,
    plan: Plan,
    unit: str,
    keeper: Value,
    stage: Callable[[MemoryStore, Rotation], None] | None = None,
    **constraints: Any,
) -> Any:
    destinations = sorted(LANE_DESTINATIONS)
    world.publish(_package(_acceptance(plan), destinations=destinations))
    store = MemoryStore()
    store.secrets[KEEPER] = ("keepers", "keeper", keeper)
    if stage is not None:
        stage(store, Rotation("canary", "2026-10-11-requested", KEEPER, CHECKS_WORK, ()))
    view = world.unit(
        rotation_adapter="bws-lane-token",
        rotation_unit=unit,
        destinations=destinations,
        **{"touches_no_hosted_service": None, **constraints},
    )
    return check_unit(view, world.intake(), world.mirror, CHECKS_WORK, store)


def test_a_lane_token_issue_passes_on_a_keeper_holding_the_marker(world: World) -> None:
    approved = _check(world, LANE_ISSUE, "store", Value(EMPTY))
    assert approved.plan == LANE_ISSUE and approved.plan.issue


def test_an_issue_on_a_keeper_holding_another_value_than_staged_is_refused(
    world: World,
) -> None:
    def staged(store: MemoryStore, rotation: Rotation) -> None:
        store.create(CHECKS_WORK, rotation.staging_key, NEW_TOKEN)

    with pytest.raises(ContractError, match="only on a keeper holding only the marker"):
        _check(world, LANE_ISSUE, "store", OLD_TOKEN, stage=staged)


def test_a_finished_issue_passes_the_checks_so_its_unit_run_can_say_so(world: World) -> None:
    # The issue's last step deleted staging; the keeper holds what it stored.
    assert _check(world, LANE_ISSUE, "store", NEW_TOKEN).plan == LANE_ISSUE


def test_a_reissue_on_a_keeper_holding_the_marker_is_refused(world: World) -> None:
    with pytest.raises(ContractError, match="nothing is issued to rotate"):
        _check(world, LANE_REISSUE, "store", Value(EMPTY))


def test_a_reissue_passes_with_its_variable_proved_by_patch(world: World) -> None:
    approved = _check(world, LANE_REISSUE, "verify", OLD_TOKEN)
    assert approved.unit.name == "verify"


def test_a_lane_unit_marked_non_hosted_is_refused(world: World) -> None:
    with pytest.raises(ContractError, match="non-hosted"):
        _check(world, LANE_ISSUE, "store", Value(EMPTY), touches_no_hosted_service=True)


def test_another_type_with_a_coolify_variable_and_no_host_step_is_still_refused(
    world: World,
) -> None:
    # It has a distribute step, for its Actions secret, and still doesn't write the variable.
    destinations = (*LANE_DESTINATIONS, 'gh-actions-secret repo="AlobarQuest/x" name="T"')
    reissue = derive(
        TYPES["github-pat"].capabilities, destinations, KEEPER_DESTINATION, "bws-secret"
    )
    assert StepKind.DISTRIBUTE in {step.kind for step in reissue.steps()}
    with pytest.raises(ContractError, match="reaches no hosted consumer"):
        _check_destination_fit(
            list(destinations), reissue, reissue.unit("store"), marked_non_hosted=False
        )
