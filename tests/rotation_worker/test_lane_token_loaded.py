"""ADR-0055 amendment 29 item 3: a lane token's reissue proves the lane loaded the new token.

A row naming its `helper_target` proves its Coolify variable, in a reissue's `verify` unit, by the
fingerprint the VPS helper's `loaded` verb reads from the restarted lane container. The unit waits
unclaimed until that fingerprint equals the keeper's, and the claimed step reads once more and
fails the unit on anything else. A row without `helper_target`, and every issue, keep the `PATCH`
proof exactly. Driven through `worker._take` with the real adapter and contract; the checks, the
registry read and the helper are replaced.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

import rotation_worker.worker as worker
from rotation_worker.adapters import ADAPTERS, AdapterContext
from rotation_worker.adapters import bws_lane_token as lane
from rotation_worker.adapters import bws_machine_token as bws
from rotation_worker.checks import Approved
from rotation_worker.contract import EMPTY, ContractError, Plan, Rotation
from rotation_worker.coolify import CoolifyClient
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.helper_client import HelperClient, HelperRefused
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.registry import RegistryEntry
from rotation_worker.secret_store import StoreError
from rotation_worker.values import Fingerprint, Value
from rotation_worker.worker import PassActs, Setup
from tests.rotation_worker.stores import MemoryStore
from tests.rotation_worker.test_keychain_instance import Recording
from tests.rotation_worker.test_lane_credentials import (
    COOLIFY_URL,
    KEEPER,
    LANE_DESTINATIONS,
    LANE_ISSUE,
    LANE_REISSUE,
    NEW_TOKEN,
    OLD_TOKEN,
    SERVICE,
    WORK,
    _row,
)
from tests.rotation_worker.test_worker import FakeClient

TARGET = "lane-execution-watch"
CONTAINER = "execution-watch-lane0000"
PRINT_KEY = f"{CONTAINER}/BWS_ACCESS_TOKEN"
COOLIFY_TOKEN = "22222222-3333-4444-8555-666666666666"
LIVE = {NEW_TOKEN.reveal(), OLD_TOKEN.reveal()}


def printed(value: Value) -> str:
    return lane.printed(value.fingerprint)


class Helper:
    """The VPS helper's client: `loaded` answers from `answers` in turn (a dict, or an exception
    to raise), the last answer repeating. Counts every call."""

    def __init__(self, *answers: dict[str, str] | Exception) -> None:
        self.answers = list(answers)
        self.targets: list[str] = []

    def loaded(self, target: str) -> dict[str, str]:
        self.targets.append(target)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _never_built() -> HelperClient:
    raise AssertionError("a row without helper_target never builds the helper")


class Unit:
    """One lane-token unit of a reissue or an issue, through `worker._take`."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.store = MemoryStore()
        self.store.secrets[KEEPER] = ("keepers", "keeper", NEW_TOKEN)
        self.store.secrets[COOLIFY_TOKEN] = ("admin", "coolify", Value("throwaway-coolify-token"))
        self.rotation = Rotation(
            "execution-watch-bws-token", "2026-11-22-requested", KEEPER, WORK, LANE_DESTINATIONS
        )
        self.store.create(WORK, self.rotation.staging_key, NEW_TOKEN)
        self.store.create(WORK, self.rotation.quarantine_key, OLD_TOKEN)
        self.row: dict[str, Any] = _row(helper_target=TARGET)
        self.plan: Plan = LANE_REISSUE
        self.name = "verify"
        self.helper: Helper | None = Helper({PRINT_KEY: printed(NEW_TOKEN)})
        self.requests: list[httpx.Request] = []
        self.state = tmp_path
        self.client = FakeClient({})
        self.client.units["u1"] = {
            "id": "u1",
            "version": 4,
            "work_package_revision_id": "revision",
            "authority": {
                "constraints": {"rotation_adapter": "bws-lane-token", "rotation_unit": "verify"}
            },
        }
        monkeypatch.setattr(worker, "check_unit", self._approved)
        monkeypatch.setattr(worker, "find_credential", lambda _mirror, _id: self._entry())
        # A fresh Bitwarden login, answered from a table: both tokens live, the control refused.
        monkeypatch.setattr(
            bws.BwsMachineTokenProvider,
            "probe",
            lambda _self, value: 200 if value.reveal() in LIVE else 400,
        )
        monkeypatch.setattr(
            bws.BwsMachineTokenProvider, "known_bad", lambda _self: Value("known-bad-placeholder")
        )
        transport = httpx.MockTransport(self._coolify)
        monkeypatch.setattr(
            lane,
            "CoolifyClient",
            lambda url, token, kind, uuid, **options: CoolifyClient(
                url, token, kind, uuid, transport, **options
            ),
        )

    def _coolify(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={})

    def _entry(self) -> RegistryEntry:
        return RegistryEntry("AlobarQuest/orchestrator", "c" * 40, self.row)

    def _approved(self, *_arguments: Any) -> Approved:
        adapter = ADAPTERS["bws-lane-token"]
        return Approved(
            adapter, self.plan, self.plan.unit(self.name), self.rotation, self._entry(), {}
        )

    def setup(self) -> Setup:
        helper = self.helper
        factory: Callable[[], HelperClient] = (
            _never_built if helper is None else (lambda: cast(HelperClient, helper))
        )
        return Setup(
            client=cast(OrchestratorClient, self.client),
            # Never fetched: the checks and the registry reads are replaced.
            mirror=RepoMirror(self.state / "mirrors"),
            store=self.store,
            work_project_id=WORK,
            state_dir=self.state,
            source_revision="rotation-worker@0123456789ab",
            coolify_url=COOLIFY_URL,
            admin_secrets={"bws-lane-token": COOLIFY_TOKEN},
            organization_id="org",
            pg_helper=factory,
        )

    def drop_quarantine(self) -> None:
        quarantine = self.store.find(WORK, self.rotation.quarantine_key)
        assert quarantine is not None
        del self.store.secrets[quarantine.id]

    def take(self) -> PassActs:
        found = PassActs()
        worker._take(self.setup(), "u1", found)
        return found

    def calls(self) -> list[str]:
        return [call for call, _ in self.client.calls]

    def loaded_reads(self) -> int:
        return 0 if self.helper is None else len(self.helper.targets)


@pytest.fixture
def unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Unit:
    return Unit(tmp_path, monkeypatch)


def _verify_note(unit: Unit) -> str:
    (body,) = [b for b in unit.client.evidence_bodies if b["payload"]["step"] == "verify_consumers"]
    return str(body["payload"]["note"])


# ---- a reissue on a row naming its helper target --------------------------------------------


def test_a_reissue_s_verify_passes_on_the_keeper_s_fingerprint_with_no_patch(unit: Unit) -> None:
    found = unit.take()
    assert "u1" not in found.waits
    assert unit.calls()[:2] == ["claim", "start"] and unit.calls()[-1] == "submit"
    # One read before the claim, one in the claimed step.
    assert unit.helper is not None and unit.helper.targets == [TARGET, TARGET]
    assert unit.requests == []
    assert "2 of 2 consumers hold or attest the new value" in _verify_note(unit)


@pytest.mark.parametrize(
    "answer",
    [
        {PRINT_KEY: printed(OLD_TOKEN)},
        {PRINT_KEY: ""},
        {PRINT_KEY: printed(NEW_TOKEN), f"{CONTAINER}-2/BWS_ACCESS_TOKEN": printed(OLD_TOKEN)},
        {PRINT_KEY: f"{NEW_TOKEN.fingerprint.sha256_prefix[:8]}:{NEW_TOKEN.fingerprint.length}"},
        {},
        HelperRefused("loaded", "unknown_target"),
        ContractError("the helper's loaded gave no answer (exit 255)"),
        StoreError("the root holds no grant on the helper's key"),
    ],
    ids=[
        "old-token",
        "nothing-loaded",
        "one-of-two",
        "short-prefix",
        "no-consumer",
        "refused",
        "no-answer",
        "key-unreadable",
    ],
)
def test_a_reissue_s_verify_waits_unclaimed_on_anything_but_the_keeper_s_fingerprint(
    unit: Unit, answer: dict[str, str] | Exception
) -> None:
    unit.helper = Helper(answer)
    found = unit.take()
    assert unit.client.calls == []
    wait = found.waits["u1"]
    assert not wait.reader and not wait.stuck and wait.hours == 48
    assert unit.requests == []


def test_a_wait_is_logged_with_its_unit_and_reason(
    unit: Unit, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    unit.helper = Helper({PRINT_KEY: printed(OLD_TOKEN)})
    unit.take()
    (record,) = [r for r in caplog.records if r.getMessage() == "unit_waiting"]
    assert getattr(record, "unit") == "u1"
    assert printed(OLD_TOKEN) in getattr(record, "reason")


def test_an_unreadable_keeper_waits_unclaimed_and_asks_no_helper(unit: Unit) -> None:
    del unit.store.secrets[KEEPER]
    found = unit.take()
    assert unit.client.calls == []
    assert "can't be read" in found.waits["u1"].reason
    assert unit.loaded_reads() == 0


@pytest.mark.parametrize(
    "after_claim",
    [{PRINT_KEY: printed(OLD_TOKEN)}, HelperRefused("loaded", "docker_unavailable")],
    ids=["mismatch", "unreadable"],
)
def test_the_claimed_read_fails_the_unit_on_anything_but_a_match(
    unit: Unit, after_claim: dict[str, str] | Exception
) -> None:
    unit.helper = Helper({PRINT_KEY: printed(NEW_TOKEN)}, after_claim)
    unit.take()
    assert unit.calls()[:2] == ["claim", "start"] and unit.calls()[-1] == "fail"
    assert "submit" not in unit.calls()
    assert unit.loaded_reads() == 2
    assert not [
        b for b in unit.client.evidence_bodies if b["payload"]["step"] == "verify_consumers"
    ]


def test_a_reissue_s_store_unit_with_a_helper_target_completes_and_reads_nothing(
    unit: Unit,
) -> None:
    unit.name = "store"
    unit.store.secrets[KEEPER] = ("keepers", "keeper", OLD_TOKEN)
    unit.drop_quarantine()
    unit.take()
    assert unit.calls()[-1] == "submit"
    assert unit.loaded_reads() == 0
    assert [r.method for r in unit.requests] == ["PATCH"]
    assert unit.store.read(KEEPER).same_as(NEW_TOKEN)


# ---- an issue keeps the PATCH proof ---------------------------------------------------------


def test_an_issue_s_store_unit_with_a_helper_target_keeps_the_patch_proof(unit: Unit) -> None:
    unit.plan, unit.name = LANE_ISSUE, "store"
    unit.store.secrets[KEEPER] = ("keepers", "keeper", Value(EMPTY))
    unit.drop_quarantine()
    unit.helper = Helper(AssertionError("an issue never reads loaded"))
    unit.take()
    assert unit.calls()[-1] == "submit"
    assert unit.loaded_reads() == 0
    assert [(r.method, r.url.path) for r in unit.requests] == [
        ("PATCH", f"/api/v1/services/{SERVICE}/envs")
    ]


# ---- a row without helper_target: today's behavior, pinned -----------------------------------


def test_a_row_without_a_helper_target_proves_its_reissue_by_the_patch_and_builds_no_helper(
    unit: Unit,
) -> None:
    unit.row = _row()
    unit.helper = None
    found = unit.take()
    assert "u1" not in found.waits
    assert unit.calls()[-1] == "submit"
    assert [(r.method, r.url.path) for r in unit.requests] == [
        ("PATCH", f"/api/v1/services/{SERVICE}/envs")
    ]
    assert "2 of 2 consumers hold or attest the new value" in _verify_note(unit)


def test_a_row_without_a_helper_target_never_waits_on_a_load(unit: Unit) -> None:
    unit.row = _row()
    provider = ADAPTERS["bws-lane-token"].build(_context(unit, pg_helper=None))
    assert isinstance(provider, worker.WaitsForLoad)

    def unread() -> Fingerprint:
        raise AssertionError("a row without helper_target reads no keeper before the claim")

    assert provider.loaded_wait(LANE_REISSUE.unit("verify"), unread) is None


# ---- building the provider -------------------------------------------------------------------


def _context(unit: Unit, **changes: Any) -> AdapterContext:
    options: dict[str, Any] = {
        "rotation": unit.rotation,
        "coolify_url": COOLIFY_URL,
        "credential": unit.row,
        "organization_id": "org",
        "pg_helper": lambda: cast(HelperClient, unit.helper),
        **changes,
    }
    return AdapterContext(unit.state, unit.store, COOLIFY_TOKEN, **options)


def test_a_context_built_without_issue_takes_the_loaded_read(unit: Unit) -> None:
    """`issue` defaults to the strict path: never the `PATCH` proof on a helper-target row."""
    unit.helper = Helper({PRINT_KEY: printed(OLD_TOKEN)})
    provider = ADAPTERS["bws-lane-token"].build(_context(unit))
    variable = lane.lane_variable(LANE_DESTINATIONS, f'bws-secret uuid="{KEEPER}"')
    with pytest.raises(ContractError, match="hasn't loaded") as raised:
        provider.consumer_loaded(variable, NEW_TOKEN)
    assert unit.requests == []
    # The raised text names fingerprints only: no scrub stands between it and a test here.
    assert printed(OLD_TOKEN) in str(raised.value)
    for token in (NEW_TOKEN, OLD_TOKEN):
        assert token.reveal() not in str(raised.value)


def test_the_loaded_read_answers_only_for_the_approved_variable(unit: Unit) -> None:
    provider = ADAPTERS["bws-lane-token"].build(_context(unit))
    variable = lane.lane_variable(LANE_DESTINATIONS, f'bws-secret uuid="{KEEPER}"')
    with pytest.raises(ContractError, match="only variable"):
        provider.consumer_loaded({**variable, "key": "OTHER_BWS_ACCESS_TOKEN"}, NEW_TOKEN)
    assert unit.loaded_reads() == 0 and unit.requests == []


def test_a_helper_target_with_no_helper_configured_is_refused_at_build(unit: Unit) -> None:
    with pytest.raises(ContractError, match="needs the helper"):
        ADAPTERS["bws-lane-token"].build(_context(unit, pg_helper=None))


@pytest.mark.parametrize("target", ["", "lane target", "lane;id", 7, None])
def test_a_malformed_helper_target_is_refused_before_a_claim(target: object) -> None:
    check_row = ADAPTERS["bws-lane-token"].check_row
    assert check_row is not None
    with pytest.raises(ContractError, match="helper_target"):
        check_row(_row(helper_target=target))


def test_a_row_without_a_helper_target_passes_the_row_check() -> None:
    check_row = ADAPTERS["bws-lane-token"].check_row
    assert check_row is not None
    check_row(_row())
    assert lane.helper_target(_row()) is None


# ---- no value is ever logged or raised -------------------------------------------------------


def test_no_log_line_or_failure_reason_holds_a_token(
    unit: Unit, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    unit.helper = Helper({PRINT_KEY: printed(OLD_TOKEN)})
    unit.take()
    unit.helper = Helper({PRINT_KEY: printed(NEW_TOKEN)}, {PRINT_KEY: printed(OLD_TOKEN)})
    unit.take()
    assert unit.client.failures["u1"]
    text = caplog.text + " ".join(unit.client.failures.values())
    for token in (NEW_TOKEN, OLD_TOKEN):
        assert token.reveal() not in text
    assert printed(OLD_TOKEN) in text


def test_only_the_vm_instance_takes_a_lane_token_unit() -> None:
    """The Keychain instance on the Mac never claims or waits on one, so this proof can't reach
    it (ADR-0055 amendment 19 item 1)."""
    unit = {"id": "u1", "authority": {"constraints": {"rotation_adapter": "bws-lane-token"}}}
    assert worker._ours(cast(Setup, SimpleNamespace(store=MemoryStore())), unit) is True
    assert worker._ours(cast(Setup, SimpleNamespace(store=Recording())), unit) is False
