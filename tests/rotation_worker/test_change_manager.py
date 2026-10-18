"""The change-manager adapter, against a simulated change-manager and Coolify.

The simulation holds what each side holds in production: Coolify stores each bearer variable's two
rows, and change-manager loads the production rows at its restart and authenticates a bearer by
`app/bearers.py`'s rules. A setting that breaks a rule stops it booting, which leaves it answering
nothing, as Coolify leaves it. The variable's form is held to change-manager's pinned fixture,
read from the file, never from a copy. Nothing reaches a live endpoint.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from rotation_worker.adapters import ADAPTERS, AdapterContext, change_manager
from rotation_worker.adapters.change_manager import (
    ATTESTED_PLAN,
    BASE_URL,
    GATE_WAIT,
    PLAN,
    PROBE_PATH,
    SWAP_WAIT,
    VARIABLES,
    ChangeManagerProvider,
    build_provider,
    registry_reads,
    setting,
)
from rotation_worker.contract import ContractError, HumanAct, Plan, Rotation, Step, UnitRun
from rotation_worker.evidence import StepEvidence
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.m2m_setting import MAX_OVERLAP
from rotation_worker.readers import Healthchecks, ReadError, ReadUnavailable
from rotation_worker.registry import REGISTRY_PATH, REGISTRY_REPOSITORIES
from rotation_worker.values import Value
from tests.rotation_worker.conftest import Origins
from tests.rotation_worker.stores import MemoryStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "m2m_bearer_setting.json"
COOLIFY = "http://100.112.188.96:8000"
APP = "re45tafypao3nly3qa9a79dp"
VARIABLE = "M2M_TOKEN_READ"
SCOPE = "read"
WORK = "work-project"
START = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _hosted(app: str = APP, variable: str = VARIABLE, scope: str = SCOPE) -> str:
    return (
        f'coolify-env-hash instance="prod" resource_type="application" uuid="{app}" '
        f'key="{variable}" entry="{scope}"'
    )


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class ChangeManager:
    """Coolify's stored rows and what change-manager loaded, behind two transports."""

    def __init__(self, clock: Clock, current: dict[str, str]) -> None:
        self.clock = clock
        # Each variable's production and preview rows, and what the running process loaded.
        self.rows = {
            (variable, preview): setting(scope, _hash(current[scope]))
            for variable, scope in VARIABLES.items()
            for preview in (True, False)
        }
        self.loaded: dict[str, str] | None = {v: self.rows[(v, False)] for v in VARIABLES}
        self.sent: list[tuple[str, str]] = []
        self.coolify_status = 201
        self.restart_status = 200
        self.restart_applies = True
        # Requests the old container answers after a restart, with what it loaded before.
        self.lag = 0
        self._before: dict[str, str] | None = None
        # Statuses the probe answers before it consults what loaded, one per request.
        self.scripted: list[int | None] = []
        self.asked = 0

    def coolify(self, request: httpx.Request) -> httpx.Response:
        self.sent.append((request.method, request.url.path))
        if request.method == "PATCH":
            if self.coolify_status >= 300:
                return httpx.Response(self.coolify_status)
            body = json.loads(request.content)
            self.rows[(body["key"], body["is_preview"])] = body["value"]
            return httpx.Response(201)
        if self.restart_status >= 300:
            return httpx.Response(self.restart_status)
        if not self.restart_applies:
            return httpx.Response(200)
        loaded = {v: self.rows[(v, False)] for v in VARIABLES}
        self._before = self.loaded
        # `app/main.py` runs the boot check at import: a broken rule means no process at all.
        self.loaded = None if self._rule_broken(loaded) else loaded
        return httpx.Response(200)

    def probe(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == PROBE_PATH
        self.asked += 1
        assert request.headers["User-Agent"].startswith("rotation-worker/")
        if self.scripted:
            status = self.scripted.pop(0)
            if status is None:
                raise httpx.ConnectError("mid-swap")
            return httpx.Response(status, text="<html>cloudflare</html>")
        loaded = self.loaded
        if self.lag and self._before is not None:
            self.lag -= 1
            loaded = self._before
        if loaded is None:
            return httpx.Response(502, text="<html>Bad gateway</html>")
        presented = _hash(request.headers["Authorization"].removeprefix("Bearer ").strip())
        accepted = any(self._accepts(entry, presented) for entry in self._entries(loaded))
        return httpx.Response(200 if accepted else 401, json={})

    def entry(self, variable: str = VARIABLE) -> dict[str, Any]:
        return json.loads(self.rows[(variable, False)])[VARIABLES[variable]]

    @staticmethod
    def _entries(loaded: dict[str, str]) -> list[dict[str, Any]]:
        return [json.loads(text)[VARIABLES[variable]] for variable, text in loaded.items()]

    def _accepts(self, entry: dict[str, Any], presented: str) -> bool:
        previous = entry.get("previous")
        if entry["token_hash"] == presented:
            return True
        return bool(
            previous
            and previous["token_hash"] == presented
            and self.clock() < datetime.fromisoformat(previous["until"])
        )

    def _rule_broken(self, loaded: dict[str, str]) -> bool:
        entries = self._entries(loaded)
        current = {entry["token_hash"] for entry in entries}
        for entry in entries:
            previous = entry.get("previous")
            if previous and (
                previous["token_hash"] in current
                or datetime.fromisoformat(previous["until"]) > self.clock() + MAX_OVERLAP
            ):
                return True
        return False


class Harness:
    def __init__(self) -> None:
        self.clock = Clock()
        self.store = MemoryStore()
        self.values = {scope: f"the-original-{scope}-bearer" for scope in VARIABLES.values()}
        self.original = Value(self.values[SCOPE])
        self.keeper_id = self.store.put(WORK, "change-manager-read-bearer", self.original)
        self.token_id = self.store.put(
            WORK, "rotation-coolify-token", Value("throwaway-coolify-token-for-tests")
        )
        self.cm = ChangeManager(self.clock, self.values)
        self.siblings = [Value(v) for s, v in self.values.items() if s != SCOPE]
        self.mid_run: list[list[str]] = []
        self.rotation = Rotation(
            credential_id="change-manager-read-bearer",
            occurrence="2026-10-19-requested",
            keeper_id=self.keeper_id,
            work_project_id=WORK,
            destinations=(
                f'bws-secret uuid="{self.keeper_id}"',
                _hosted(),
                'provider name="change-manager"',
            ),
        )
        self.provider = self.build()
        self.filed: list[tuple[Step, StepEvidence]] = []

    def readers_mid_run(self) -> list[str]:
        return self.mid_run.pop(0) if self.mid_run else []

    def build(self, **changes: Any) -> ChangeManagerProvider:
        arguments: dict[str, Any] = {
            "rotation": self.rotation,
            "store": self.store,
            "coolify_token_id": self.token_id,
            "coolify_url": COOLIFY,
            "change_manager_application": APP,
            "siblings": lambda: self.siblings,
            "readers_mid_run": self.readers_mid_run,
            "transport": httpx.MockTransport(self.cm.probe),
            "coolify_transport": httpx.MockTransport(self.cm.coolify),
            "clock": self.clock,
            "sleep": self.clock.sleep,
            **changes,
        }
        return build_provider(**arguments)

    def run(
        self, unit: str, before_step: Callable[[], None] = lambda: None, plan: Plan = PLAN
    ) -> None:
        UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.provider,
            attestations=(),
            record=lambda step, evidence: self.filed.append((step, evidence)),
            before_step=before_step,
            filed=frozenset(step.kind for step, _ in self.filed),
        ).run(plan.unit(unit))

    def keeper(self) -> Value:
        return self.store.read(self.keeper_id)


@pytest.fixture
def h() -> Harness:
    return Harness()


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ---- the form, held to change-manager's pinned fixture ------------------------------------------


def test_the_variables_and_scopes_are_change_managers() -> None:
    assert dict(VARIABLES) == _fixture()["variables"]


def test_a_hosted_setting_is_byte_for_byte_the_fixtures_example() -> None:
    example = _fixture()["example"]
    entry = json.loads(example["value"])[VARIABLES[example["variable"]]]
    previous = entry["previous"]
    until = datetime.fromisoformat(previous["until"])
    built = setting(
        VARIABLES[example["variable"]], entry["token_hash"], (previous["token_hash"], until)
    )
    assert built == example["value"]


def test_the_overlap_ceiling_is_change_managers() -> None:
    assert MAX_OVERLAP.total_seconds() == _fixture()["max_previous_overlap_seconds"]


def test_a_retired_setting_is_one_entry_with_no_previous() -> None:
    assert json.loads(setting("full", "a" * 64)) == {"full": {"token_hash": "a" * 64}}


# ---- the whole rotation ------------------------------------------------------------------------


def test_a_rotation_hosts_stores_and_retires_through_the_contract(h: Harness) -> None:
    h.run("mint")
    new = h.keeper()
    assert not new.same_as(h.original)
    entry = h.cm.entry()
    assert entry["token_hash"] == _hash(new.reveal())
    assert entry["previous"]["token_hash"] == _hash(h.original.reveal())
    assert datetime.fromisoformat(entry["previous"]["until"]) == START + timedelta(days=7)
    h.run("retire")
    assert h.cm.entry() == {"token_hash": _hash(new.reveal())}
    assert (
        h.cm.sent
        == [
            ("PATCH", f"/api/v1/applications/{APP}/envs"),
            ("PATCH", f"/api/v1/applications/{APP}/envs"),
            ("POST", f"/api/v1/applications/{APP}/restart"),
        ]
        * 2
    )
    assert h.cm.rows[(VARIABLE, False)] == h.cm.rows[(VARIABLE, True)]
    # No other variable was touched, and the sibling bearers still work.
    for variable, scope in VARIABLES.items():
        if scope != SCOPE:
            assert h.cm.entry(variable) == {"token_hash": _hash(h.values[scope])}
    assert [evidence.step for _, evidence in h.filed] == [
        "mint",
        "stage",
        "host",
        "verify_new",
        "store",
        "verify_consumers",
        "verify_consumers",
        "retire_old",
        "confirm_dead",
        "confirm_dead",
    ]
    for _, evidence in h.filed:
        assert new.reveal() not in json.dumps(evidence.as_payload())
        assert h.original.reveal() not in json.dumps(evidence.as_payload())


def test_a_mint_stopped_after_host_resumes_without_writing_again(h: Harness) -> None:
    def stop_after_host() -> None:
        if [evidence.step for _, evidence in h.filed][-1:] == ["host"]:
            raise RuntimeError("the pass stopped")

    with pytest.raises(RuntimeError):
        h.run("mint", before_step=stop_after_host)
    assert h.keeper().same_as(h.original)
    writes = list(h.cm.sent)
    h.provider = h.build()
    h.run("mint")
    assert h.cm.sent == writes
    notes = [evidence.note for _, evidence in h.filed if evidence.step == "host"]
    assert notes[-1] == "change-manager already accepts the new value"
    h.run("retire")
    assert h.cm.entry() == {"token_hash": _hash(h.keeper().reveal())}


def test_a_restart_that_failed_after_the_write_is_written_and_restarted_again(h: Harness) -> None:
    h.cm.restart_status = 500
    with pytest.raises(ContractError, match="HTTP 500"):
        h.run("mint")
    h.cm.restart_status = 200
    h.provider = h.build()
    h.run("mint")
    assert h.cm.sent.count(("POST", f"/api/v1/applications/{APP}/restart")) == 2
    assert h.cm.entry()["token_hash"] == _hash(h.keeper().reveal())


def test_the_attested_plan_runs_the_same_units_around_a_human_act() -> None:
    assert [s.name for s in ATTESTED_PLAN.segments] == ["mint", "attest", "retire"]
    assert isinstance(ATTESTED_PLAN.segments[1], HumanAct)
    assert ATTESTED_PLAN.unit("mint") == PLAN.unit("mint")
    assert ATTESTED_PLAN.unit("retire") == PLAN.unit("retire")


# ---- refusals that keep change-manager booting -------------------------------------------------


def test_host_refuses_an_old_value_another_scope_holds_as_current(h: Harness) -> None:
    h.siblings = [*h.siblings, h.original]
    with pytest.raises(ContractError, match="another change-manager bearer"):
        h.run("mint")
    assert h.cm.sent == []
    assert h.cm.loaded is not None


def test_what_that_refusal_saves_change_manager_from(h: Harness) -> None:
    # The same write, made without the check, leaves change-manager unable to boot.
    shared = h.values["full"]
    h.cm.rows[("M2M_TOKEN_READ", False)] = setting(
        "read", _hash("new"), (_hash(shared), START + timedelta(days=7))
    )
    h.cm.coolify(httpx.Request("POST", f"{COOLIFY}/api/v1/applications/{APP}/restart"))
    assert h.cm.loaded is None


def test_host_refuses_a_new_value_another_scope_holds(h: Harness) -> None:
    new = Value("a-value-another-scope-holds")
    h.siblings = [*h.siblings, new]
    with pytest.raises(ContractError, match="another change-manager bearer"):
        h.provider.host(new, h.original)
    assert h.cm.sent == []


def test_retire_refuses_a_new_value_another_scope_holds(h: Harness) -> None:
    h.run("mint")
    h.siblings = [*h.siblings, h.keeper()]
    with pytest.raises(ContractError, match="another change-manager bearer"):
        h.run("retire")
    assert len(h.cm.sent) == 3


def test_host_refuses_when_change_manager_doesn_t_accept_the_keepers_value(h: Harness) -> None:
    h.cm.rows[(VARIABLE, False)] = setting(SCOPE, _hash("a-hand-edited-value"))
    h.cm.coolify(httpx.Request("POST", f"{COOLIFY}/api/v1/applications/{APP}/restart"))
    h.cm.sent.clear()
    with pytest.raises(ContractError, match="doesn't accept the keeper's old value"):
        h.run("mint")
    assert h.cm.sent == []


@pytest.mark.parametrize(
    ("hosted", "problem"),
    [
        (_hosted(app="another0application0uuid0"), "configured change-manager application"),
        (_hosted(variable="ORCHESTRATOR_M2M_CREDENTIALS"), "bearer variable and its scope"),
        (_hosted(scope="full"), "bearer variable and its scope"),
        (_hosted().replace('"prod"', '"dev"'), "exactly one production"),
        (_hosted().replace('"application"', '"service"'), "application variables"),
    ],
)
def test_a_destination_the_adapter_may_not_write_is_refused(
    h: Harness, hosted: str, problem: str
) -> None:
    h.rotation = Rotation(
        h.rotation.credential_id,
        h.rotation.occurrence,
        h.keeper_id,
        WORK,
        (f'bws-secret uuid="{h.keeper_id}"', hosted, 'provider name="change-manager"'),
    )
    with pytest.raises(ContractError, match=problem):
        h.build()


def test_an_unconfigured_application_is_refused(h: Harness) -> None:
    with pytest.raises(ContractError, match="configured change-manager application"):
        h.build(change_manager_application=None)


def test_a_declared_host_without_an_overlap_is_refused(h: Harness) -> None:
    context = AdapterContext(
        Path("/nonexistent"),
        h.store,
        h.token_id,
        rotation=h.rotation,
        coolify_url=COOLIFY,
        change_manager_application=APP,
        host_without_overlap=True,
    )
    for name in (change_manager.NAME, change_manager.ATTESTED_NAME):
        with pytest.raises(ContractError, match="always hosts with an overlap"):
            ADAPTERS[name].build(context)


# ---- the probe ---------------------------------------------------------------------------------


def test_a_probe_that_can_t_tell_200_from_401_stops_the_rotation(h: Harness) -> None:
    # A route that lets every bearer through would answer the known-bad control 200.
    h.cm.scripted = [200]
    h.provider = h.build()
    with pytest.raises(ContractError, match="didn't refuse a known-bad value"):
        h.run("mint")


def test_a_probe_that_only_ever_hits_cloudflare_is_indeterminate(h: Harness) -> None:
    h.cm.scripted = [502] * 1000
    assert h.provider.probe(h.original) == 502
    assert h.clock.now >= START + SWAP_WAIT


def test_a_probe_waits_out_a_swap(h: Harness) -> None:
    h.cm.scripted = [None, 502]
    assert h.provider.probe(h.original) == 200


def test_the_probe_reaches_only_the_landing_policy(h: Harness) -> None:
    with pytest.raises(ContractError, match="may not"):
        h.provider._probe_client.request("GET", "/api/items")
    assert BASE_URL == "https://change-mgr.alobar.net"


# ---- the restart waits for change-manager's readers --------------------------------------------


def test_the_restart_waits_for_a_reader_to_finish(h: Harness) -> None:
    h.mid_run = [["sds-estate-landing"], ["sds-estate-landing"], []]
    h.run("mint")
    assert h.clock.now > START
    assert h.cm.entry()["token_hash"] == _hash(h.keeper().reveal())


def test_a_reader_that_never_finishes_stops_the_write(h: Harness) -> None:
    h.mid_run = [["sds-inert-landing"]] * 1000
    with pytest.raises(ContractError, match="cut short change-manager's readers: sds-inert"):
        h.run("mint")
    assert h.cm.sent == []
    assert h.clock.now >= START + GATE_WAIT


def test_a_reader_that_starts_during_the_write_stops_the_restart(h: Harness) -> None:
    h.mid_run = [[]] + [["sds-change-proposer"]] * 1000
    with pytest.raises(ContractError, match="sds-change-proposer"):
        h.run("mint")
    assert ("POST", f"/api/v1/applications/{APP}/restart") not in h.cm.sent


def test_a_listing_that_can_t_be_read_holds_the_restart(h: Harness) -> None:
    def unreadable() -> list[str]:
        raise ReadUnavailable("Healthchecks answered 503")

    h.provider = h.build(readers_mid_run=unreadable)
    with pytest.raises(ContractError, match="unreadable"):
        h.run("mint")
    assert h.cm.sent == []


# ---- the registry and Healthchecks -------------------------------------------------------------


def _bearer_toml(scope: str, keeper: str, app: str = APP, read: str = "") -> str:
    variable = next(v for v, s in VARIABLES.items() if s == scope)
    return f"""
[[credential]]
id = "change-manager-{scope}-bearer{"" if app == APP else "-elsewhere"}"
provider = "change-manager"
bws_uuid = "{keeper}"

  [[credential.consumer]]
  kind = "bws-secret"
  uuid = "{keeper}"

  [[credential.consumer]]
  kind = "coolify-env-hash"
  instance = "prod"
  resource_type = "application"
  uuid = "{app}"
  key = "{variable}"
  entry = "{scope}"

  [[credential.consumer]]
  kind = "runtime-fetch"
  repo = "orchestrator"
  ref = "scripts/run-{scope}.sh"
  {read}
"""


# Neither of these may count: a change-manager bearer on another application isn't a sibling, and
# a check on another provider's credential isn't change-manager's reader.
_ELSEWHERE = """
[[credential]]
id = "another-providers-bearer"
provider = "orchestrator"
bws_uuid = "00000000-0000-0000-0000-000000000000"

  [[credential.consumer]]
  kind = "coolify-env-hash"
  instance = "prod"
  resource_type = "application"
  uuid = "re45tafypao3nly3qa9a79dp"
  key = "M2M_TOKEN"
  entry = "full"

  [[credential.consumer]]
  kind = "runtime-fetch"
  repo = "orchestrator"
  ref = "scripts/run-elsewhere.sh"
  healthchecks_check = "another-providers-check"
"""


def _registry(origins: Origins, text: str) -> None:
    for repository in REGISTRY_REPOSITORIES:
        origins.commit(
            repository, {REGISTRY_PATH: text if repository.endswith("change-manager") else ""}
        )


def test_the_registry_names_the_siblings_and_every_readers_check(
    origins: Origins, tmp_path: Path, h: Harness
) -> None:
    reads = {
        "read": 'healthchecks_check = "check-read"',
        "propose": 'healthchecks_check = "check-propose"',
        "observe": 'observation_source = "drift_digest"\n  observation_subject = "coolify:prod"',
        "full": "",
    }
    text = "".join(
        _bearer_toml(
            scope,
            h.keeper_id if scope == SCOPE else h.store.put(WORK, scope, Value(value)),
            read=reads[scope],
        )
        for scope, value in h.values.items()
    )
    elsewhere = h.store.put(WORK, "elsewhere", Value("a-bearer-on-another-application"))
    _registry(origins, text + _bearer_toml("full", elsewhere, app="another0app") + _ELSEWHERE)
    asked: list[set[str]] = []

    class Checks:
        def running(self, names: set[str]) -> list[str]:
            asked.append(set(names))
            return ["check-propose"]

    mirror = RepoMirror(tmp_path / "m", origin=origins.url)
    siblings, readers_mid_run = registry_reads(
        h.rotation, h.store, mirror, cast(Healthchecks, Checks())
    )
    assert sorted(v.reveal() for v in siblings()) == sorted(
        v for s, v in h.values.items() if s != SCOPE
    )
    assert readers_mid_run() == ["check-propose"]
    assert asked == [{"check-read", "check-propose"}]


def test_readers_with_checks_and_no_healthchecks_key_are_refused(
    origins: Origins, tmp_path: Path, h: Harness
) -> None:
    _registry(origins, _bearer_toml(SCOPE, h.keeper_id, read='healthchecks_check = "check-read"'))
    _, readers_mid_run = registry_reads(
        h.rotation, h.store, RepoMirror(tmp_path / "m", origin=origins.url), None
    )
    with pytest.raises(ReadError, match="no Healthchecks key"):
        readers_mid_run()


def test_readers_with_no_checks_need_no_healthchecks_key(
    origins: Origins, tmp_path: Path, h: Harness
) -> None:
    _registry(origins, _bearer_toml(SCOPE, h.keeper_id))
    _, readers_mid_run = registry_reads(
        h.rotation, h.store, RepoMirror(tmp_path / "m", origin=origins.url), None
    )
    assert readers_mid_run() == []


def _listing(checks: list[dict[str, Any]], calls: list[int]) -> Healthchecks:
    def answer(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"checks": checks})

    return Healthchecks(lambda: Value("read-only-key"), httpx.MockTransport(answer))


def test_running_names_the_checks_mid_run_and_reads_afresh_each_time() -> None:
    checks = [{"name": "a", "started": True}, {"name": "b", "started": False}]
    calls: list[int] = []
    healthchecks = _listing(checks, calls)
    assert healthchecks.running({"a", "b"}) == ["a"]
    checks[0]["started"] = False
    assert healthchecks.running({"a", "b"}) == []
    assert len(calls) == 2


@pytest.mark.parametrize(
    "checks",
    [
        [],
        [{"name": "a", "started": False}, {"name": "a", "started": False}],
        [{"name": "a"}],
        [{"name": "a", "started": "yes"}],
    ],
)
def test_running_refuses_a_check_it_can_t_find_exactly_once(checks: list[dict[str, Any]]) -> None:
    with pytest.raises(ReadError):
        _listing(checks, []).running({"a"})


def test_host_waits_out_a_swap_before_judging_what_change_manager_accepts(h: Harness) -> None:
    h.cm.scripted = [None, 502]
    h.run("mint")
    assert h.cm.entry()["token_hash"] == _hash(h.keeper().reveal())


def test_host_refuses_when_change_manager_never_answers(h: Harness) -> None:
    h.cm.scripted = [401, *[502] * 1000]
    with pytest.raises(ContractError, match="no answer to tell live from dead"):
        h.run("mint")
    assert h.cm.sent == []


def test_the_adapter_registry_builds_both_plans_from_the_executor_s_context(
    h: Harness, tmp_path: Path
) -> None:
    context = AdapterContext(
        tmp_path,
        h.store,
        h.token_id,
        rotation=h.rotation,
        coolify_url=COOLIFY,
        change_manager_application=APP,
        mirror=RepoMirror(tmp_path / "m"),
    )
    for name, plan in ((change_manager.NAME, PLAN), (change_manager.ATTESTED_NAME, ATTESTED_PLAN)):
        adapter = ADAPTERS[name]
        assert adapter.provider_name == "change-manager"
        assert adapter.plan is plan
        assert isinstance(adapter.build(context), ChangeManagerProvider)
    with pytest.raises(ContractError, match="the registry"):
        ADAPTERS[change_manager.NAME].build(replace(context, mirror=None))


def test_a_hosted_value_the_old_container_still_refuses_is_asked_again(h: Harness) -> None:
    h.cm.lag = 3
    h.run("mint")
    assert h.cm.entry()["token_hash"] == _hash(h.keeper().reveal())


def test_a_retired_value_the_old_container_still_accepts_is_asked_again(h: Harness) -> None:
    h.run("mint")
    h.cm.lag = 3
    h.run("retire")
    assert "previous" not in h.cm.entry()


def test_a_restart_that_never_loads_the_host_is_refused(h: Harness) -> None:
    h.cm.restart_applies = False
    with pytest.raises(ContractError, match="didn't accept the new value in time"):
        h.run("mint")
    assert h.keeper().same_as(h.original)


def test_a_restart_that_never_loads_the_retire_is_refused(h: Harness) -> None:
    h.run("mint")
    h.cm.restart_applies = False
    with pytest.raises(ContractError, match="still accepts the old value"):
        h.run("retire")


def test_retire_refuses_a_keeper_still_holding_the_old_value(h: Harness) -> None:
    with pytest.raises(ContractError, match="keeper still holds the old value"):
        h.provider.revoke(h.original)
    assert h.cm.sent == []


def test_retire_refuses_when_change_manager_doesn_t_accept_the_new_value(h: Harness) -> None:
    h.run("mint")
    h.cm.rows[(VARIABLE, False)] = setting(SCOPE, _hash(h.original.reveal()))
    h.cm.coolify(httpx.Request("POST", f"{COOLIFY}/api/v1/applications/{APP}/restart"))
    h.cm.sent.clear()
    with pytest.raises(ContractError, match="doesn't accept the keeper's new value"):
        h.provider.revoke(h.original)
    assert h.cm.sent == []


def test_a_retire_waits_until_the_old_value_is_refused_several_times_running(h: Harness) -> None:
    h.run("mint")
    h.provider = h.build()
    # Control and new value first; then the old value, answered by both containers in turn.
    h.cm.scripted = [401, 200, 401, 200, 401, 401]
    h.cm.asked = 0
    h.provider.revoke(h.original)
    assert h.cm.asked >= 7


def test_a_swap_that_never_settles_is_refused(h: Harness) -> None:
    h.run("mint")
    h.provider = h.build()
    h.cm.scripted = [401, 200, *[401, 200] * 1000]
    with pytest.raises(ContractError, match="still accepts the old value"):
        h.provider.revoke(h.original)


def test_a_value_nobody_hosted_or_retired_is_asked_once(h: Harness) -> None:
    h.cm.asked = 0
    assert h.provider.probe(h.original) == 200
    assert h.cm.asked == 1


def test_the_registry_must_name_a_keeper_for_every_other_variable(
    origins: Origins, tmp_path: Path, h: Harness
) -> None:
    text = "".join(
        _bearer_toml(scope, h.store.put(WORK, scope, Value(value)))
        for scope, value in h.values.items()
        if scope not in (SCOPE, "observe")
    )
    _registry(origins, text)
    siblings, _ = registry_reads(
        h.rotation, h.store, RepoMirror(tmp_path / "m", origin=origins.url), None
    )
    with pytest.raises(ContractError, match="exactly one keeper for M2M_TOKEN_OBSERVE"):
        siblings()


def test_the_registry_is_read_once_however_often_the_readers_are_asked(
    origins: Origins, tmp_path: Path, h: Harness
) -> None:
    _registry(origins, _bearer_toml(SCOPE, h.keeper_id, read='healthchecks_check = "check-read"'))
    calls: list[int] = []
    mirror = RepoMirror(tmp_path / "m", origin=origins.url)
    _, readers_mid_run = registry_reads(
        h.rotation, h.store, mirror, _listing([{"name": "check-read", "started": False}], calls)
    )
    readers_mid_run()
    # Every registry repository is gone: a second read would fail to fetch.
    import shutil

    shutil.rmtree(origins.root)
    assert readers_mid_run() == []
    assert len(calls) == 2
