"""The GitHub PAT and Bitbucket adapters, driven through the contract against simulated providers.

A person mints and stages (a human act), so each test stages the new value in `Rotation / Work`
by hand, as Devon does in the console, then runs the machine units.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from rotation_worker.adapters import bitbucket, github_pat
from rotation_worker.checks import Approved, _check_destination_fit
from rotation_worker.contract import ContractError, Plan, Rotation, Step, UnitRun
from rotation_worker.evidence import StepEvidence
from rotation_worker.github_actions import (
    FingerprintReports,
    GitHubUnavailable,
    Report,
    ReportMissing,
)
from rotation_worker.orchestrator_client import OrchestratorClient, OrchestratorError
from rotation_worker.readers import ActionsSecretRead, ConsumerReads, ReadError, scheduled_reads
from rotation_worker.values import Fingerprint, Value
from tests.rotation_worker.stores import MemoryStore

WORK = "work-project"
ACTIONS = 'gh-actions-secret repo="AlobarQuest/orchestrator" name="FACTORY_PR_TOKEN"'
NOW = datetime(2026, 10, 19, 16, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class GitHub:
    """Live PATs, a revoke that lands after `lag` more probes, and the requests it saw."""

    def __init__(self, *live: str) -> None:
        self.live = set(live)
        self.lag = 0
        self.revoked: list[str] = []
        self.revoke_status = 202
        self.requests: list[httpx.Request] = []
        self._pending: str | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == github_pat.REVOKE_PATH:
            assert "authorization" not in request.headers
            [value] = json.loads(request.content)["credentials"]
            self.revoked.append(value)
            self._pending = value
            return httpx.Response(self.revoke_status)
        if self._pending is not None:
            if self.lag == 0:
                self.live.discard(self._pending)
                self._pending = None
            else:
                self.lag -= 1
        presented = request.headers["Authorization"].removeprefix("Bearer ")
        return httpx.Response(200 if presented in self.live else 401)


class Writer:
    def __init__(self) -> None:
        self.written: dict[tuple[str, str], Value] = {}

    def write(self, repository: str, name: str, value: Value) -> None:
        self.written[(repository, name)] = value


class Reports:
    """What each repository's report says, from what the writer wrote."""

    def __init__(self, writer: Writer) -> None:
        self.writer = writer
        self.missing = False

    def latest(self, repository: str, name: str) -> Report:
        if self.missing:
            raise ReportMissing(f"{repository} has no successful report")
        value = self.writer.written.get((repository, name))
        return Report(value.fingerprint if value else Fingerprint("00000000", 1), NOW)


class Runs:
    """The orchestrator's view the live-run guard reads: nothing live unless told."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.down = False

    def active_units(self) -> list[dict[str, Any]]:
        if self.down:
            raise OrchestratorError("no answer")
        return self.rows

    def unit(self, _unit_id: str) -> dict[str, Any]:
        return {"authority_envelope": {}}

    def history(self, _unit_id: str) -> list[dict[str, Any]]:
        return []


class Harness:
    def __init__(self, destinations: tuple[str, ...] = (ACTIONS,)) -> None:
        self.store = MemoryStore()
        self.old = Value("github_pat_" + "o" * 82)
        self.new = Value("github_pat_" + "n" * 82)
        self.keeper_id = self.store.put(WORK, "factory-pr-token keeper", self.old)
        self.rotation = Rotation(
            "factory-pr-token",
            "2026-10-20-requested",
            self.keeper_id,
            WORK,
            (f'bws-secret uuid="{self.keeper_id}"', 'provider name="github"', *destinations),
        )
        self.github = GitHub(self.old.reveal(), self.new.reveal())
        self.clock = Clock()
        self.runs = Runs()
        self.writer = Writer()
        self.reports = Reports(self.writer)
        self.filed: list[tuple[Step, StepEvidence]] = []
        self.provider = github_pat.build_provider(
            rotation=self.rotation,
            orchestrator=cast(OrchestratorClient, self.runs),
            transport=httpx.MockTransport(self.github.handle),
            clock=self.clock,
            sleep=self.clock.sleep,
        )

    def stage(self) -> None:
        # The human act: Devon pastes the minted value into the staging secret.
        self.store.put(WORK, self.rotation.staging_key, self.new)

    def reads(self) -> ConsumerReads:
        return ConsumerReads(
            scheduled_reads({"consumer": [_consumer(d) for d in self.rotation.destinations]}),
            None,
            cast(OrchestratorClient, None),
            reports=cast(FingerprintReports, self.reports),
            keeper=lambda: self.store.read(self.keeper_id).fingerprint,
        )

    def run(self, plan: Plan, unit: str, before_step: Callable[[], None] = lambda: None) -> None:
        UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.provider,
            attestations=(),
            record=lambda step, evidence: self.filed.append((step, evidence)),
            before_step=before_step,
            filed=frozenset(step.kind for step, _ in self.filed),
            scheduled=self.reads(),
            actions_secrets=self.writer,
        ).run(plan.unit(unit))


def _consumer(destination: str) -> dict[str, Any]:
    from rotation_worker.destinations import parse

    return parse(destination)


@pytest.fixture
def h() -> Harness:
    return Harness()


def test_a_pat_rotation_stores_writes_every_copy_and_revokes_by_value(h: Harness) -> None:
    h.stage()
    h.run(github_pat.PLAN, "store")
    assert h.store.read(h.keeper_id).same_as(h.new)
    assert h.writer.written == {("AlobarQuest/orchestrator", "FACTORY_PR_TOKEN"): h.new}
    h.run(github_pat.PLAN, "retire")
    assert h.github.revoked == [h.old.reveal()]
    assert h.store.secrets.keys() == {h.keeper_id}
    assert [e.step for _, e in h.filed] == [
        "verify_new",
        "store",
        "distribute",
        "verify_consumers",
        "verify_consumers",
        "retire_old",
        "confirm_dead",
        "confirm_dead",
    ]
    for _, evidence in h.filed:
        payload = json.dumps(evidence.as_payload())
        assert h.new.reveal() not in payload and h.old.reveal() not in payload


def test_retire_waits_while_a_copy_still_reports_the_old_value(h: Harness) -> None:
    h.stage()
    h.run(github_pat.PLAN, "store")
    h.writer.written.clear()
    with pytest.raises(ContractError, match="hasn't verified"):
        h.run(github_pat.PLAN, "retire")
    assert h.github.revoked == []


def test_a_missing_report_is_a_refusal_not_a_wait(h: Harness) -> None:
    h.stage()
    h.run(github_pat.PLAN, "store")
    h.reports.missing = True
    with pytest.raises(ReadError, match="no successful report"):
        h.reads().pending(datetime.now(UTC))


class Down(Reports):
    def latest(self, repository: str, name: str) -> Report:
        raise GitHubUnavailable("GitHub GET answered HTTP 503")


def test_a_report_github_couldn_t_answer_is_awaited(h: Harness) -> None:
    h.reports = Down(h.writer)
    assert h.reads().pending(datetime.now(UTC)) == [ACTIONS]


class Unnamed(Reports):
    def latest(self, repository: str, name: str) -> Report:
        return Report(None, NOW)


def test_a_report_that_doesn_t_name_the_copy_is_refused(h: Harness) -> None:
    h.reports = Unnamed(h.writer)
    with pytest.raises(ReadError, match="doesn't name FACTORY_PR_TOKEN"):
        h.reads().pending(datetime.now(UTC))


def test_an_actions_copy_with_no_app_configured_is_refused(h: Harness) -> None:
    reads = ConsumerReads(
        scheduled_reads({"consumer": [_consumer(ACTIONS)]}),
        None,
        cast(OrchestratorClient, None),
    )
    with pytest.raises(ReadError, match="no rotation App"):
        reads.pending(datetime.now(UTC))


def test_a_staged_value_equal_to_the_old_one_is_refused(h: Harness) -> None:
    h.store.put(WORK, h.rotation.staging_key, h.old)
    with pytest.raises(ContractError, match="equals the old one"):
        h.run(github_pat.PLAN, "store")
    assert h.writer.written == {}


def test_store_with_nothing_staged_is_refused(h: Harness) -> None:
    with pytest.raises(ContractError, match="no staged value"):
        h.run(github_pat.PLAN, "store")


def test_a_store_that_stops_after_distribute_resumes(h: Harness) -> None:
    h.stage()
    steps = 0

    def stop() -> None:
        nonlocal steps
        steps += 1
        if steps == 4:
            raise ContractError("stopped after distribute")

    with pytest.raises(ContractError, match="stopped after distribute"):
        h.run(github_pat.PLAN, "store", stop)
    h.run(github_pat.PLAN, "store")
    h.run(github_pat.PLAN, "retire")
    assert h.github.revoked == [h.old.reveal()]


def test_the_revoke_refuses_while_a_dispatched_run_is_live(h: Harness) -> None:
    h.stage()
    h.run(github_pat.PLAN, "store")
    h.runs.rows = [{"unit_state": "executing", "unit_id": "u1", "unit_key": "factory-1"}]
    with pytest.raises(ContractError, match="would fail a live run"):
        h.run(github_pat.PLAN, "retire")
    assert h.github.revoked == []


def test_a_pat_without_actions_copies_doesn_t_ask_about_runs() -> None:
    h = Harness(destinations=('runtime-fetch repo="mirror" ref="mirror.py"',))
    h.runs.rows = [{"unit_state": "executing", "unit_id": "u1", "unit_key": "factory-1"}]
    h.provider.revoke(h.old)
    assert h.github.revoked == [h.old.reveal()]


def test_a_revoke_github_doesn_t_accept_is_refused_without_the_value(h: Harness) -> None:
    h.github.revoke_status = 403
    with pytest.raises(ContractError, match="HTTP 403") as refused:
        h.provider.revoke(h.old)
    assert h.old.reveal() not in str(refused.value)


def test_a_revoked_value_still_live_is_asked_again_until_refused(h: Harness) -> None:
    h.github.lag = 3
    h.provider.revoke(h.old)
    assert h.provider.probe(h.old) == 401
    assert h.clock.now - NOW == timedelta(seconds=30)


def test_a_revoked_value_live_past_the_lag_answers_live(h: Harness) -> None:
    h.github.lag = 100
    h.provider.revoke(h.old)
    assert h.provider.probe(h.old) == 200
    assert h.clock.now - NOW >= github_pat.REVOKE_LAG


def test_a_value_nobody_revoked_is_asked_once(h: Harness) -> None:
    before = len(h.github.requests)
    assert h.provider.probe(h.new) == 200
    assert len(h.github.requests) == before + 1


def test_a_person_mints_never_the_executor(h: Harness) -> None:
    with pytest.raises(ContractError, match="person mints"):
        h.provider.mint()


def test_an_actions_copy_on_a_plan_without_distribute_is_refused() -> None:
    from rotation_worker.adapters import orchestrator_bearer

    destinations = [ACTIONS, 'provider name="orchestrator"']
    with pytest.raises(ContractError, match="writes no Actions secret"):
        _check_destination_fit(
            destinations,
            orchestrator_bearer.PLAN,
            orchestrator_bearer.PLAN.unit("mint"),
            marked_non_hosted=False,
        )


def test_an_actions_copy_is_read_through_its_report() -> None:
    reads = scheduled_reads({"consumer": [_consumer(ACTIONS)]})
    assert reads == {ACTIONS: ActionsSecretRead("AlobarQuest/orchestrator", "FACTORY_PR_TOKEN")}


# ---- Bitbucket -------------------------------------------------------------------------------

EMAIL = "devon@example.invalid"
WORKSPACE = "alobarquest"


class Atlassian:
    def __init__(self, *live: str) -> None:
        self.live = set(live)
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.path == f"/2.0/repositories/{WORKSPACE}"
        user, _, token = (
            base64.b64decode(request.headers["Authorization"].removeprefix("Basic "))
            .decode()
            .partition(":")
        )
        return httpx.Response(200 if user == EMAIL and token in self.live else 401)


def _bitbucket(atlassian: Atlassian) -> bitbucket.BitbucketProvider:
    return bitbucket.build_provider(
        credential={"probe_email": EMAIL, "probe_workspace": WORKSPACE},
        transport=httpx.MockTransport(atlassian.handle),
    )


def test_a_bitbucket_rotation_waits_for_the_person_s_revoke() -> None:
    store = MemoryStore()
    old, new = Value("ATATT-old-token"), Value("ATATT-new-token")
    keeper = store.put(WORK, "bitbucket keeper", old)
    rotation = Rotation(
        "bitbucket-mirror-token",
        "2026-10-20-requested",
        keeper,
        WORK,
        (f'bws-secret uuid="{keeper}"', 'provider name="bitbucket"'),
    )
    atlassian = Atlassian(old.reveal(), new.reveal())
    provider = _bitbucket(atlassian)
    filed: list[tuple[Step, StepEvidence]] = []

    def run(unit: str) -> None:
        UnitRun(
            rotation=rotation,
            store=store,
            provider=provider,
            attestations=(),
            record=lambda step, evidence: filed.append((step, evidence)),
            before_step=lambda: None,
            filed=frozenset(step.kind for step, _ in filed),
        ).run(bitbucket.PLAN.unit(unit))

    store.put(WORK, rotation.staging_key, new)
    run("store")
    run("verify")
    # Before the person revokes, confirm-dead finds the old token alive and stops.
    with pytest.raises(ContractError, match="probed live"):
        run("confirm")
    atlassian.live.discard(old.reveal())
    run("confirm")
    assert store.secrets.keys() == {keeper}
    assert store.read(keeper).same_as(new)


@pytest.mark.parametrize(
    "credential",
    [
        {"probe_workspace": WORKSPACE},
        {"probe_email": EMAIL},
        {"probe_email": EMAIL, "probe_workspace": "../evil"},
    ],
)
def test_bitbucket_needs_its_probe_account_and_workspace(credential: dict[str, str]) -> None:
    with pytest.raises(ContractError, match="probe_"):
        bitbucket.build_provider(credential=credential)


def test_a_person_revokes_an_atlassian_token() -> None:
    with pytest.raises(ContractError, match="person revokes"):
        _bitbucket(Atlassian()).revoke(Value("an-atlassian-token-for-tests"))


def test_the_verify_unit_gates_the_person_s_revoke() -> None:
    from rotation_worker.adapters import ADAPTERS
    from rotation_worker.worker import _gates_retire

    def gates(adapter: str, unit: str) -> bool:
        plan = ADAPTERS[adapter].plan
        approved = SimpleNamespace(adapter=ADAPTERS[adapter], unit=plan.unit(unit))
        return _gates_retire(cast(Approved, approved))

    assert gates("bitbucket", "verify")
    assert not gates("bitbucket", "store")
    assert not gates("bitbucket", "confirm")
    assert gates("github-pat", "retire")
    assert not gates("github-pat", "store")


def test_a_retire_waits_unclaimed_while_a_factory_run_is_live(h: Harness) -> None:
    from rotation_worker.worker import GatedRestarts

    assert isinstance(h.provider, GatedRestarts)
    assert h.provider.readers_mid_run() == []
    h.runs.rows = [{"unit_state": "executing", "unit_id": "u1", "unit_key": "factory-1"}]
    assert h.provider.readers_mid_run() == ["factory-1 (executing)"]


def test_a_human_mint_doesn_t_attest_an_unread_reader() -> None:
    from rotation_worker.checks import _check_runtime_fetch_verifiable

    reader = 'runtime-fetch repo="x" ref="y.sh"'
    with pytest.raises(ContractError, match="no read and no human act verifies"):
        _check_runtime_fetch_verifiable([reader], github_pat.PLAN, {})


def test_a_person_s_revoke_needs_every_consumer_machine_verified() -> None:
    from rotation_worker.checks import _check_human_retire_verifiable

    keeper = 'bws-secret uuid="00000000-0000-0000-0000-000000000000"'
    unread = 'keychain service="s" account="a"'
    _check_human_retire_verifiable(
        [keeper, 'provider name="bitbucket"'], bitbucket.PLAN, {}, MemoryStore()
    )
    with pytest.raises(ContractError, match="nothing machine-verifies"):
        _check_human_retire_verifiable([keeper, unread], bitbucket.PLAN, {}, MemoryStore())
    _check_human_retire_verifiable([keeper, unread], github_pat.PLAN, {}, MemoryStore())


def test_an_orchestrator_outage_during_the_gate_is_a_wait() -> None:
    from rotation_worker.readers import ReadUnavailable

    h = Harness()
    h.runs.down = True
    with pytest.raises(ReadUnavailable, match="live runs can't be read"):
        h.provider.readers_mid_run()
