"""Taking a person's mint from the inbox (ADR-0055 amendment 15, increment 3c)."""

from __future__ import annotations

import base64
import subprocess
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from rotation_worker import cli, worker
from rotation_worker.adapters import ADAPTERS, bitbucket, github_pat
from rotation_worker.adapters import bws_machine_token as bws
from rotation_worker.checks import Approved
from rotation_worker.config import ConfigError, load
from rotation_worker.contract import ContractError, Provider, Rotation
from rotation_worker.inbox import (
    EMPTY,
    Inbox,
    Refusals,
    bind,
    other_mints,
    person_mints_before,
)
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.registry import RegistryEntry
from rotation_worker.secret_store import StoreError
from rotation_worker.values import Value
from tests.rotation_worker.stores import MemoryStore

WORK = "work-project"
INBOX_PROJECT = "inbox-project"
OLD = Value("the-old-value-for-tests")
NEW = Value("the-new-value-for-tests")
CONTROL = Value("the-known-bad-control")


# ---- the inbox ------------------------------------------------------------------------------


def _inbox(store: MemoryStore, text: str = EMPTY) -> tuple[Inbox, str, list[str]]:
    secret_id = store.put(INBOX_PROJECT, "rotation-inbox", Value(text))
    opened: list[str] = []

    def open_store() -> MemoryStore:
        opened.append("opened")
        return store

    return Inbox(open_store, secret_id), secret_id, opened


def test_the_inbox_is_opened_only_when_read_and_reads_the_word_empty_as_nothing() -> None:
    store = MemoryStore()
    inbox, _, opened = _inbox(store)
    assert opened == []
    assert inbox.read() is None
    assert inbox.read() is None
    assert opened == ["opened"]


def test_a_pasted_value_is_read_and_emptying_writes_the_word_back() -> None:
    store = MemoryStore()
    inbox, secret_id, _ = _inbox(store, NEW.reveal())
    value = inbox.read()
    assert value is not None and value.same_as(NEW)
    inbox.empty()
    assert store.read(secret_id).reveal() == EMPTY
    assert inbox.read() is None


def test_an_inbox_that_can_t_be_opened_is_an_error_not_an_empty_inbox() -> None:
    def malformed() -> MemoryStore:
        raise ValueError("a credential value is non-empty and has no surrounding whitespace")

    with pytest.raises(ValueError):
        Inbox(malformed, "id").read()


def test_a_cleared_inbox_reads_as_empty() -> None:
    class Cleared(MemoryStore):
        def read_key(self, secret_id: str) -> Value:
            # BWS hands back a blank value, which no `Value` may hold.
            return Value("")

    store = Cleared()
    inbox, _, _ = _inbox(store)
    assert inbox.read() is None


# ---- which rotations wait on a mint ---------------------------------------------------------


def test_the_unit_straight_after_a_person_s_mint_is_the_one_that_waits() -> None:
    for adapter in ("github-pat", "bitbucket", "bws-machine-token"):
        assert person_mints_before("store", adapter)
    assert not person_mints_before("retire", "github-pat")
    assert not person_mints_before("verify", "bws-machine-token")
    assert not person_mints_before("confirm", "bitbucket")
    # A machine mint waits on nobody.
    assert not person_mints_before("mint", "openrouter")
    # What this executor doesn't know errs toward binding nothing.
    assert person_mints_before("store", "no-such-adapter")
    assert person_mints_before("no-such-unit", "github-pat")


class Ledger:
    def __init__(self, units: dict[str, dict[str, Any]]) -> None:
        self.units = units

    def active_units(self) -> list[dict[str, Any]]:
        return [{"unit_id": unit_id, "unit_key": f"key-{unit_id}"} for unit_id in self.units]

    def unit(self, unit_id: str) -> dict[str, Any]:
        return self.units[unit_id]


def _listed(adapter: str, name: str, revision: str, attempts: int = 0) -> dict[str, Any]:
    constraints = {"rotation_adapter": adapter, "rotation_unit": name}
    return {
        "work_package_revision_id": revision,
        "attempt_count": attempts,
        "authority": {"constraints": constraints},
    }


def test_every_other_rotation_s_unclaimed_unit_after_a_mint_counts_on_either_instance() -> None:
    ledger = Ledger(
        {
            "mine": _listed("github-pat", "store", "this"),
            "pat": _listed("github-pat", "store", "r1"),
            "token": _listed("bws-machine-token", "store", "r2"),
            "claimed": _listed("bitbucket", "store", "r3", attempts=1),
            "later": _listed("bitbucket", "confirm", "r4"),
            "machine": _listed("openrouter", "mint", "r5"),
            "plain": {"work_package_revision_id": "r6", "authority": {"constraints": {}}},
        }
    )
    assert other_mints(cast(OrchestratorClient, ledger), "this") == ["key-pat", "key-token"]


# ---- binding --------------------------------------------------------------------------------


class Provides:
    """A provider: the control is refused, a live value answers 200, and `reach` says whose
    each value is."""

    dead_statuses = frozenset({401})

    def __init__(self, live: Iterable[Value], reach: dict[str, str] | None = None) -> None:
        self.live = {value.reveal() for value in live}
        self.reach = reach or {}
        self.asked: list[str] = []

    def known_bad(self) -> Value:
        return CONTROL

    def probe(self, value: Value) -> int:
        self.asked.append(value.reveal())
        return 200 if value.reveal() in self.live else 401

    def same_identity(self, new: Value, old: Value) -> bool:
        return self.reach.get(new.reveal(), "me") == self.reach.get(old.reveal(), "me")


class NoIdentity:
    dead_statuses = frozenset({401})

    def known_bad(self) -> Value:
        return CONTROL

    def probe(self, value: Value) -> int:
        return 200 if value.same_as(NEW) else 401


def _rotation(store: MemoryStore, old: Value = OLD) -> Rotation:
    keeper = store.put(WORK, "keeper", old)
    return Rotation("cred", "2026-10-28-requested", keeper, WORK, ())


def _bind(
    store: MemoryStore,
    rotation: Rotation,
    provider: object,
    *,
    new: Value = NEW,
    waiting: tuple[str, ...] = (),
    keepers: tuple[tuple[str, Value], ...] = (),
) -> str | None:
    return bind(
        new,
        rotation=rotation,
        store=store,
        provider=cast(Provider, provider),
        others_waiting=waiting,
        other_keepers=lambda: keepers,
    )


def test_a_value_that_passes_every_check_is_staged() -> None:
    store = MemoryStore()
    rotation = _rotation(store)
    provider = Provides([NEW, OLD])
    assert _bind(store, rotation, provider) is None
    staged = store.find(WORK, rotation.staging_key)
    assert staged is not None and store.read(staged.id).same_as(NEW)
    # Proved beside a refused control.
    assert provider.asked[:2] == [CONTROL.reveal(), NEW.reveal()]


def test_another_rotation_waiting_on_a_mint_binds_nothing_before_anything_is_asked() -> None:
    store = MemoryStore()
    rotation = _rotation(store)
    provider = Provides([NEW, OLD])
    reason = _bind(store, rotation, provider, waiting=("key-pat",))
    assert reason is not None and "key-pat" in reason
    assert provider.asked == []
    assert store.find(WORK, rotation.staging_key) is None


@pytest.mark.parametrize(
    ("provider", "new", "keepers", "reason"),
    [
        (Provides([OLD]), OLD, (), "it is the value the keeper already holds"),
        (Provides([OLD]), NEW, (), "the provider doesn't accept it"),
        (Provides([NEW, OLD], {NEW.reveal(): "them"}), NEW, (), "isn't for the same account"),
        (NoIdentity(), NEW, (), "can't tell whose a value is"),
        (Provides([NEW, OLD]), NEW, (("twin", NEW),), "it is twin's value"),
    ],
)
def test_a_value_that_fails_a_check_is_left_where_it_is(
    provider: object, new: Value, keepers: tuple[tuple[str, Value], ...], reason: str
) -> None:
    store = MemoryStore()
    rotation = _rotation(store)
    found = _bind(store, rotation, provider, new=new, keepers=keepers)
    assert found is not None and reason in found
    assert store.find(WORK, rotation.staging_key) is None


def test_a_control_the_probe_doesn_t_refuse_proves_nothing() -> None:
    store = MemoryStore()
    rotation = _rotation(store)
    provider = Provides([NEW, OLD, CONTROL])
    assert _bind(store, rotation, provider) == (
        "the provider doesn't accept it beside a refused known-bad value"
    )


def test_a_refusal_is_reported_only_once_the_same_value_fails_again(tmp_path: Path) -> None:
    refusals = Refusals(tmp_path)
    assert not refusals.again(NEW)
    assert refusals.again(NEW)
    # A new paste starts again.
    assert not Refusals(tmp_path).again(OLD)
    assert Refusals(tmp_path).again(OLD)


def test_an_unwritable_refusal_record_only_delays_the_report(tmp_path: Path) -> None:
    refusals = Refusals(tmp_path / "missing")
    assert not refusals.again(NEW)
    assert not refusals.again(NEW)


# ---- the wait before the claim --------------------------------------------------------------


class Pass:
    """One rotation whose `store` unit follows a person's mint, on a store-and-inbox setup."""

    def __init__(self, tmp_path: Path, inbox_text: str | None = EMPTY) -> None:
        self.store = MemoryStore()
        self.rotation = _rotation(self.store)
        adapter = ADAPTERS["github-pat"]
        entry = RegistryEntry(
            "o/r", "c" * 40, {"id": "cred", "provider_identity": "AlobarQuest (fine-grained PAT)"}
        )
        self.approved = Approved(
            adapter, adapter.plan, adapter.plan.unit("store"), self.rotation, entry, {}
        )
        self.inbox: Inbox | None = None
        self.inbox_id = ""
        if inbox_text is not None:
            self.inbox, self.inbox_id, _ = _inbox(self.store, inbox_text)
        self.ledger = Ledger({})
        self.state = tmp_path
        self.provider = Provides([NEW, OLD])
        self.found = worker.PassActs()

    def setup(self) -> worker.Setup:
        return cast(
            worker.Setup,
            SimpleNamespace(
                store=self.store,
                inbox=self.inbox,
                client=self.ledger,
                state_dir=self.state,
                mirror=None,
            ),
        )

    def waits(self, monkeypatch: pytest.MonkeyPatch, keepers: list[tuple[str, Value]]) -> bool:
        monkeypatch.setattr(worker, "_other_keepers", lambda _setup, _approved: keepers)
        unit = {"id": "u1", "work_package_revision_id": "this"}
        return worker._mint_waits(
            self.setup(), self.approved, cast(Provider, self.provider), unit, self.found
        )

    def staged(self) -> Value | None:
        found = self.store.find(WORK, self.rotation.staging_key)
        return self.store.read(found.id) if found else None


def test_an_empty_inbox_waits_and_asks_the_person_for_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Pass(tmp_path)
    assert run.waits(monkeypatch, [])
    act = run.found.due["rotation-mint:u1"]
    assert act.title == "Create a new cred value"
    assert "AlobarQuest (fine-grained PAT)" in act.message
    assert "Rotation / Inbox." in act.message
    assert act.link == "https://sds.alobar.net/review/units/u1"


def test_a_value_that_binds_is_staged_the_inbox_emptied_and_the_unit_goes_ahead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Pass(tmp_path, NEW.reveal())
    assert not run.waits(monkeypatch, [])
    staged = run.staged()
    assert staged is not None and staged.same_as(NEW)
    assert run.store.read(run.inbox_id).reveal() == EMPTY
    assert run.found.due == {}


def test_a_value_that_doesn_t_bind_stays_and_is_reported_on_its_second_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Pass(tmp_path, NEW.reveal())
    keepers = [("twin", NEW)]
    assert run.waits(monkeypatch, keepers)
    assert set(run.found.due) == {"rotation-mint:u1"}
    run.found = worker.PassActs()
    assert run.waits(monkeypatch, keepers)
    assert set(run.found.due) == {"rotation-mint:u1", "rotation-inbox:u1"}
    assert "it is twin's value" in run.found.due["rotation-inbox:u1"].message
    assert run.staged() is None
    assert run.store.read(run.inbox_id).same_as(NEW)


def test_two_rotations_waiting_on_a_mint_bind_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Pass(tmp_path, NEW.reveal())
    run.ledger = Ledger({"other": _listed("bitbucket", "store", "r1")})
    assert run.waits(monkeypatch, [])
    assert run.staged() is None


def test_an_inbox_still_holding_the_staged_value_is_emptied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Pass(tmp_path, NEW.reveal())
    run.store.put(WORK, run.rotation.staging_key, NEW)
    assert not run.waits(monkeypatch, [])
    assert run.store.read(run.inbox_id).reveal() == EMPTY
    assert run.found.due == {}


def test_an_inbox_holding_another_value_beside_a_staged_one_is_reported_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Pass(tmp_path, OLD.reveal())
    run.store.put(WORK, run.rotation.staging_key, NEW)
    assert not run.waits(monkeypatch, [])
    assert set(run.found.due) == {"rotation-inbox:u1"}
    assert run.store.read(run.inbox_id).same_as(OLD)


def test_without_an_inbox_the_unit_waits_for_a_value_staged_by_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Pass(tmp_path, None)
    assert run.waits(monkeypatch, [])
    assert "stage the value by hand" in run.found.due["rotation-mint:u1"].message
    run.found = worker.PassActs()
    run.store.put(WORK, run.rotation.staging_key, NEW)
    assert not run.waits(monkeypatch, [])


def test_a_value_that_can_t_be_checked_stays_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Pass(tmp_path, NEW.reveal())

    def unreadable(_setup: object, _approved: object) -> list[tuple[str, Value]]:
        raise StoreError("the Keychain has no readable item x (44)")

    monkeypatch.setattr(worker, "_other_keepers", unreadable)
    unit = {"id": "u1", "work_package_revision_id": "this"}
    for _ in range(2):
        run.found = worker.PassActs()
        assert worker._mint_waits(
            run.setup(), run.approved, cast(Provider, run.provider), unit, run.found
        )
    assert "it couldn't be checked" in run.found.due["rotation-inbox:u1"].message
    assert run.staged() is None
    assert run.store.read(run.inbox_id).same_as(NEW)


def test_an_inbox_that_can_t_be_read_keeps_its_alerts_as_they_were(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Pass(tmp_path)

    def unreadable() -> MemoryStore:
        raise StoreError("BWS refused: 429")

    run.inbox = Inbox(unreadable, run.inbox_id)
    assert run.waits(monkeypatch, [])
    assert run.found.keep == {"rotation-mint:u1", "rotation-inbox:u1"}


def test_a_unit_not_after_a_person_s_mint_never_reads_the_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Pass(tmp_path, NEW.reveal())
    adapter = ADAPTERS["github-pat"]
    run.approved = Approved(
        adapter, adapter.plan, adapter.plan.unit("retire"), run.rotation, run.approved.registry, {}
    )
    assert not run.waits(monkeypatch, [])
    assert run.store.read(run.inbox_id).same_as(NEW)


def test_other_keepers_are_this_type_s_sds_rotated_credentials_this_store_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Pass(tmp_path)
    twin = run.store.put("keepers", "twin", NEW)
    entries = [
        RegistryEntry(
            "o/r", "c", {"id": "cred", "bws_uuid": run.rotation.keeper_id, "rotated_by_sds": True}
        ),
        RegistryEntry("o/r", "c", {"id": "twin", "bws_uuid": twin, "rotated_by_sds": True}),
        RegistryEntry("o/r", "c", {"id": "legacy", "bws_uuid": twin}),
        RegistryEntry(
            "o/r",
            "c",
            {"id": "mac", "keeper_service": "s", "keeper_account": "a", "rotated_by_sds": True},
        ),
    ]
    asked: list[str] = []

    def entries_of(_mirror: object, provider: str) -> list[RegistryEntry]:
        asked.append(provider)
        return entries

    monkeypatch.setattr(worker, "provider_entries", entries_of)
    found = worker._other_keepers(run.setup(), run.approved)
    assert asked == ["github"]
    assert [(credential, value.reveal()) for credential, value in found] == [("twin", NEW.reveal())]


# ---- each type's identity check -------------------------------------------------------------


def _github(ids: dict[str, Any]) -> github_pat.GitHubPatProvider:
    def handle(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        found = ids.get(token)
        if found is None:
            return httpx.Response(401)
        return httpx.Response(200, json={"id": found, "login": "AlobarQuest"})

    return github_pat.build_provider(
        rotation=Rotation("c", "o", "k", WORK, ()),
        orchestrator=cast(OrchestratorClient, object()),
        transport=httpx.MockTransport(handle),
    )


@pytest.mark.parametrize(
    ("ids", "same"),
    [
        ({NEW.reveal(): 7, OLD.reveal(): 7}, True),
        ({NEW.reveal(): 7, OLD.reveal(): 8}, False),
        ({OLD.reveal(): 7}, False),
        ({NEW.reveal(): 7}, False),
        ({NEW.reveal(): "7", OLD.reveal(): "7"}, False),
        ({NEW.reveal(): True, OLD.reveal(): True}, False),
    ],
)
def test_a_github_pat_is_the_same_account_s_by_its_numeric_id(
    ids: dict[str, Any], same: bool
) -> None:
    assert _github(ids).same_identity(NEW, OLD) is same


def test_a_bitbucket_token_is_the_same_account_s_when_both_sign_in_as_its_email() -> None:
    live: set[str] = {NEW.reveal(), OLD.reveal()}

    def handle(request: httpx.Request) -> httpx.Response:
        user, _, token = (
            base64.b64decode(request.headers["Authorization"].removeprefix("Basic "))
            .decode()
            .partition(":")
        )
        return httpx.Response(200 if user == "d@example.invalid" and token in live else 401)

    provider = bitbucket.build_provider(
        credential={"probe_email": "d@example.invalid", "probe_workspace": "ws"},
        transport=httpx.MockTransport(handle),
    )
    assert provider.same_identity(NEW, OLD)
    live.discard(OLD.reveal())
    assert not provider.same_identity(NEW, OLD)
    live = {OLD.reveal()}
    assert not provider.same_identity(NEW, OLD)


def _reach(monkeypatch: pytest.MonkeyPatch, answers: dict[str, str]) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append({"argv": argv, **kwargs})
        token = kwargs["input"].split("\n", 1)[1]
        return subprocess.CompletedProcess(argv, 0, answers[token] + "\n", "")

    monkeypatch.setattr(bws.subprocess, "run", run)
    return seen


@pytest.mark.parametrize(
    ("new", "old", "same"),
    [
        ("a" * 64, "a" * 64, True),
        ("a" * 64, "b" * 64, False),
        ("unreadable", "unreadable", False),
        # A child that died before printing proves nothing.
        ("", "", False),
    ],
)
def test_a_bws_token_is_the_same_account_s_when_it_sees_the_same_projects_and_secrets(
    monkeypatch: pytest.MonkeyPatch, new: str, old: str, same: bool
) -> None:
    seen = _reach(monkeypatch, {NEW.reveal(): new, OLD.reveal(): old})
    provider = bws.BwsMachineTokenProvider("/py", organization_id="org-id")
    assert provider.same_identity(NEW, OLD) is same
    # Each a fresh login in a throwaway HOME, the organization and token only on stdin.
    assert [call["input"] for call in seen] == [
        f"org-id\n{NEW.reveal()}",
        f"org-id\n{OLD.reveal()}",
    ]
    assert all(NEW.reveal() not in " ".join(call["argv"]) for call in seen)
    assert all(set(call["env"]) == {"PATH", "HOME"} for call in seen)


def test_a_bws_token_s_reach_needs_the_organization() -> None:
    with pytest.raises(ContractError, match="no organization"):
        bws.BwsMachineTokenProvider("/py").same_identity(NEW, OLD)


def test_the_keychain_instance_builds_its_bws_provider_with_the_organization() -> None:
    from rotation_worker.adapters import AdapterContext

    context = AdapterContext(Path("/s"), MemoryStore(), None, organization_id="org-id")
    provider = ADAPTERS["bws-machine-token"].build(context)
    assert isinstance(provider, bws.BwsMachineTokenProvider)
    assert provider._organization_id == "org-id"


# ---- configuration --------------------------------------------------------------------------

UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
KEYCHAIN = """store = "keychain"
orchestrator_url = "https://sds.example.invalid"
credential_key_id = "rotation-executor-keychain-1"
work_project_id = "rotation-executor"
bearer_secret_id = "worker-bearer"
state_dir = "/state"
source_revision = "0123456789ab"
"""
VM = f"""orchestrator_url = "https://sds.example.invalid"
credential_key_id = "rotation-executor-1"
organization_id = "{UUID_A}"
work_project_id = "{UUID_B}"
root_token_file = "/etc/rotation-worker/root-token"
state_dir = "/state"
source_revision = "0123456789ab"
"""


def _load(tmp_path: Path, text: str) -> Any:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return load(path)


def test_the_vm_reads_its_inbox_with_the_root(tmp_path: Path) -> None:
    config = _load(tmp_path, VM + f'inbox_secret_id = "{UUID_B}"\n')
    store = MemoryStore()
    inbox = cli.open_inbox(config, store)
    assert inbox is not None and inbox._opened() is store
    assert cli.open_inbox(_load(tmp_path, VM), store) is None
    with pytest.raises(ConfigError, match="names no inbox_token_id"):
        _load(tmp_path, VM + 'inbox_token_id = "inbox-token"\n')


def test_the_keychain_instance_logs_in_with_its_own_inbox_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _load(
        tmp_path,
        KEYCHAIN
        + f'organization_id = "{UUID_A}"\ninbox_secret_id = "{UUID_B}"\n'
        + 'inbox_token_id = "inbox-token"\n',
    )
    assert (config.organization_id, config.inbox_secret_id) == (UUID_A, UUID_B)
    store = MemoryStore()
    token_id = 'keychain service="rotation-executor" account="inbox-token"'
    store.secrets[token_id] = ("rotation-executor", "inbox-token", Value("the-inbox-token"))
    logins: list[tuple[str | None, str]] = []

    class Logged:
        def __init__(self, organization_id: str | None, token: Value) -> None:
            logins.append((organization_id, token.reveal()))

    monkeypatch.setattr(cli, "BwsStore", Logged)
    inbox = cli.open_inbox(config, store)
    assert inbox is not None and logins == []
    inbox._opened()
    # No organization: the inbox identity only reads and writes the one secret.
    assert logins == [(None, "the-inbox-token")]
    assert cli.open_inbox(_load(tmp_path, KEYCHAIN), store) is None
