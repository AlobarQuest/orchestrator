"""The Keychain store (ADR-0055 amendment 12), against a stand-in for `/usr/bin/security`.

The stand-in keeps items in memory and fails the test if a value ever appears in an argument
list: values go to `security -i` on stdin and come back on stdout, nowhere else.
"""

from __future__ import annotations

import shlex
import subprocess
from datetime import UTC, datetime
from typing import Any

import pytest

from rotation_worker.secret_store import SECURITY, KeychainStore, StoreError
from rotation_worker.values import Value

TOKEN = "0.11111111-2222-4333-8444-555555555555.Abc123:ZGVmZ2hp=="
OTHER = "0.11111111-2222-4333-8444-555555555555.Xyz789:amtsbW5v=="
ITEM = 'keychain service="Claude" account="BWS_ACCESS_TOKEN_BRAIN_OPS"'


class FakeSecurity:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], tuple[str, str]] = {}
        self.secrets: set[str] = {TOKEN, OTHER}
        self.calls: list[list[str]] = []
        self.prompt = False
        self.fail_writes = False

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv[0] == SECURITY
        for secret in self.secrets:
            assert all(secret not in argument for argument in argv), "a value reached argv"
        self.calls.append(argv)
        if self.prompt:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        command, *rest = argv[1:]
        if command == "-i":
            return self._interactive(kwargs["input"])
        options = _options(rest)
        key = (options.get("-s", ""), options.get("-a", ""))
        if command == "find-generic-password":
            if key not in self.items:
                return self._done(44)
            value, stamp = self.items[key]
            if "-w" in options:
                return self._done(0, value + "\n")
            return self._done(0, f'    "mdat"<timedate>=0x3230  "{stamp}Z\\000"\n')
        if command == "delete-generic-password":
            return self._done(0 if self.items.pop(key, None) else 44)
        raise AssertionError(f"unexpected security command {command}")

    def _interactive(self, line: str) -> subprocess.CompletedProcess[str]:
        words = shlex.split(line)
        assert words[0] == "add-generic-password"
        if self.fail_writes:
            return self._done(1, err="security: SecKeychainItemCreateFromContent")
        options = _options(words[1:])
        update = "-U" in options
        key = (options["-s"], options["-a"])
        value = options["-w"]
        if key in self.items and not update:
            return self._done(45, err="already exists")
        self.items[key] = (value, "20261022130000")
        return self._done(0)

    @staticmethod
    def _done(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], code, out, err)


def _options(words: list[str]) -> dict[str, str]:
    """`security`'s options: `-U` and a bare `-w` stand alone, the rest take a value."""
    options: dict[str, str] = {}
    position = 0
    while position < len(words):
        flag = words[position]
        if flag == "-U" or (flag == "-w" and position + 1 == len(words)):
            options[flag] = ""
            position += 1
        else:
            options[flag] = words[position + 1]
            position += 2
    return options


@pytest.fixture
def security() -> FakeSecurity:
    return FakeSecurity()


def test_an_item_is_created_read_updated_in_place_dated_and_deleted(security: FakeSecurity) -> None:
    store = KeychainStore(run=security)
    created = store.create(
        "rotation-executor", "rotation/x/2026-10-22-requested/staging", Value(TOKEN)
    )
    assert created.id == (
        'keychain service="rotation-executor" account="rotation/x/2026-10-22-requested/staging"'
    )
    assert store.read(created.id).reveal() == TOKEN
    store.write(created.id, Value(OTHER))
    assert store.read(created.id).reveal() == OTHER
    assert store.revised(created.id) == datetime(2026, 10, 22, 13, 0, tzinfo=UTC)
    store.delete(created.id)
    assert store.find("rotation-executor", "rotation/x/2026-10-22-requested/staging") is None


def test_a_create_trusts_security_and_an_update_never_restates_the_access_list(
    security: FakeSecurity,
) -> None:
    # Restating the access list on an update makes macOS ask a person (measured 2026-10-22).
    store = KeychainStore(run=security)
    store.create("Claude", "BWS_ACCESS_TOKEN_BRAIN_OPS", Value(TOKEN))
    store.write(ITEM, Value(OTHER))
    created, updated = [call for call in security.calls if call[1:] == ["-i"]]
    assert created == updated == [SECURITY, "-i"]


def test_the_written_command_carries_the_access_list_only_on_create(
    security: FakeSecurity, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines: list[str] = []
    original = security._interactive

    def record(line: str) -> subprocess.CompletedProcess[str]:
        lines.append(line)
        return original(line)

    monkeypatch.setattr(security, "_interactive", record)
    store = KeychainStore(run=security)
    store.create("Claude", "BWS_ACCESS_TOKEN_BRAIN_OPS", Value(TOKEN))
    store.write(ITEM, Value(OTHER))
    assert f"-T {SECURITY}" in lines[0] and "-U" not in lines[0]
    assert "-U" in lines[1] and "-T" not in lines[1]


def test_locate_names_only_keychain_items() -> None:
    store = KeychainStore()
    assert store.read_back_kind == "keychain"
    assert store.locate({"kind": "keychain", "service": "Claude", "account": "A"}) == (
        'keychain service="Claude" account="A"'
    )
    assert store.locate({"kind": "bws-secret", "uuid": "u"}) is None


@pytest.mark.parametrize(
    ("label", "action"),
    [
        ("a missing item to read", lambda s: s.read(ITEM)),
        ("a missing item to update", lambda s: s.write(ITEM, Value(TOKEN))),
        ("a missing item to date", lambda s: s.revised(ITEM)),
        ("a value with a quote", lambda s: s.create("Claude", "A", Value('a"b'))),
        ("a value with a space", lambda s: s.create("Claude", "A", Value("a b"))),
        ("an account with a quote", lambda s: s.create("Claude", 'A"', Value(TOKEN))),
        ("a BWS id", lambda s: s.read('bws-secret uuid="u"')),
    ],
)
def test_each_refusal_is_a_store_error_naming_no_value(
    security: FakeSecurity, label: str, action: Any
) -> None:
    with pytest.raises(StoreError) as refused:
        action(KeychainStore(run=security))
    assert TOKEN not in str(refused.value)


def test_a_second_create_is_refused_rather_than_overwriting(security: FakeSecurity) -> None:
    store = KeychainStore(run=security)
    store.create("Claude", "A", Value(TOKEN))
    with pytest.raises(StoreError, match="already exists"):
        store.create("Claude", "A", Value(OTHER))
    assert store.read('keychain service="Claude" account="A"').reveal() == TOKEN


def test_a_refused_write_shows_nothing_security_said(security: FakeSecurity) -> None:
    store = KeychainStore(run=security)
    store.create("Claude", "A", Value(TOKEN))
    security.fail_writes = True
    with pytest.raises(StoreError) as refused:
        store.write('keychain service="Claude" account="A"', Value(OTHER))
    assert "SecKeychain" not in str(refused.value)


def test_a_prompt_waiting_for_a_person_is_a_store_error(security: FakeSecurity) -> None:
    security.prompt = True
    with pytest.raises(StoreError, match="asking a person"):
        KeychainStore(run=security).find("Claude", "A")
