"""The BWS machine-token adapter (ADR-0055 amendment 12): its probe, its waits, and a rotation
through the contract on a Keychain-shaped store."""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from rotation_worker.adapters import ADAPTERS
from rotation_worker.adapters import bws_machine_token as bws
from rotation_worker.contract import ContractError, Rotation, StepKind, UnitRun
from rotation_worker.destinations import render
from rotation_worker.evidence import StepEvidence
from rotation_worker.secret_store import StoredSecret, StoreError
from rotation_worker.values import Value

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "rotation_evidence_schema.json").read_text()
)
TOKEN = "0.11111111-2222-4333-8444-555555555555.Abc123:ZGVmZ2hp=="
WORK = "rotation-executor"
KEEPER = 'keychain service="Claude" account="BWS_ACCESS_TOKEN_BRAIN_OPS"'
COPY = 'keychain service="Claude" account="BWS_ACCESS_TOKEN_BRAIN_OPS_COPY"'
NY = bws.QUIET_ZONE


# ---- the probe ------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    # A test that ran alembic's `fileConfig` on this xdist worker disabled the existing loggers.
    monkeypatch.setattr(bws.LOG, "disabled", False)
    pauses: list[float] = []
    monkeypatch.setattr(bws, "sleep", pauses.append)
    return pauses


def _child(monkeypatch: pytest.MonkeyPatch, stdout: str | None) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append({"argv": argv, **kwargs})
        if stdout is None:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(bws.subprocess, "run", run)
    return seen


@pytest.mark.parametrize(
    ("stdout", "status"),
    [("live\n", 200), ("refused\n", 400), ("other\n", None), ("", None), (None, None)],
)
def test_the_probe_answers_live_dead_or_indeterminate(
    monkeypatch: pytest.MonkeyPatch, stdout: str | None, status: int | None
) -> None:
    _child(monkeypatch, stdout)
    assert bws.BwsMachineTokenProvider("/py").probe(Value(TOKEN)) == status


def test_the_probe_is_a_fresh_login_with_the_token_only_on_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _child(monkeypatch, "live\n")
    bws.BwsMachineTokenProvider("/py").probe(Value(TOKEN))
    [call] = seen
    assert call["argv"][:3] == ["/py", "-I", "-c"]
    assert all(TOKEN not in argument for argument in call["argv"])
    assert call["input"] == TOKEN
    assert set(call["env"]) == {"PATH", "HOME"}
    # A throwaway HOME, gone once the probe answers: no cached session to reuse (amendment 1).
    assert "rotation-login-" in call["env"]["HOME"] and not Path(call["env"]["HOME"]).exists()


def test_the_control_is_a_well_formed_token_that_differs_each_time() -> None:
    from rotation_worker.cli import BWS_TOKEN

    provider = bws.BwsMachineTokenProvider()
    first, second = provider.known_bad(), provider.known_bad()
    assert BWS_TOKEN.fullmatch(first.reveal()) and not first.same_as(second)


def test_a_person_creates_and_revokes_the_token() -> None:
    provider = bws.BwsMachineTokenProvider()
    with pytest.raises(ContractError, match="a person creates"):
        provider.mint()
    with pytest.raises(ContractError, match="a person revokes"):
        provider.revoke(Value(TOKEN))


def test_the_adapter_runs_on_the_keychain_instance() -> None:
    adapter = ADAPTERS[bws.NAME]
    assert adapter.store_kind == "keychain"
    assert adapter.provider_name == "bitwarden-sm"
    assert {segment.name for segment in adapter.plan.segments} == {
        "mint",
        "store",
        "verify",
        "revoke",
        "confirm",
    }
    assert all(
        other.store_kind == "bws-secret" for name, other in ADAPTERS.items() if name != bws.NAME
    )


# ---- the waits ------------------------------------------------------------------------------


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 10, 22, hour, minute, tzinfo=NY).astimezone(UTC)


@pytest.mark.parametrize(
    ("local", "waits"),
    [((1, 59), False), ((2, 0), True), ((7, 34), True), ((7, 35), False), ((14, 0), False)],
)
def test_the_switch_waits_out_the_scheduled_lanes(local: tuple[int, int], waits: bool) -> None:
    reason = bws.wait_reason(bws.PLAN.unit("store"), [], _at(*local))
    assert (reason is not None) is waits
    # Only the switch waits for the hour.
    assert bws.wait_reason(bws.PLAN.unit("verify"), [], _at(*local)) is None


# ---- a rotation through the contract --------------------------------------------------------


class KeychainShaped:
    """The Keychain store's shape in memory: ids are destination strings, items update in place."""

    read_back_kind = "keychain"

    def __init__(self) -> None:
        self.items: dict[str, Value] = {}
        self.revisions: dict[str, datetime] = {}
        self.deleted: list[str] = []

    def locate(self, consumer: Mapping[str, Any]) -> str | None:
        return render(consumer) if consumer.get("kind") == "keychain" else None

    def read(self, secret_id: str) -> Value:
        if secret_id not in self.items:
            raise StoreError("no such item")
        return self.items[secret_id]

    def read_key(self, secret_id: str) -> Value:
        return self.read(secret_id)

    def revised(self, secret_id: str) -> datetime:
        return self.revisions[secret_id]

    def find(self, project_id: str, key: str) -> StoredSecret | None:
        secret_id = render({"kind": "keychain", "service": project_id, "account": key})
        return StoredSecret(secret_id, key, project_id) if secret_id in self.items else None

    def create(self, project_id: str, key: str, value: Value) -> StoredSecret:
        secret_id = render({"kind": "keychain", "service": project_id, "account": key})
        assert secret_id not in self.items
        self.put(secret_id, value)
        return StoredSecret(secret_id, key, project_id)

    def write(self, secret_id: str, value: Value) -> None:
        assert secret_id in self.items, "an update in place, never a create"
        self.put(secret_id, value)

    def put(self, secret_id: str, value: Value) -> None:
        self.items[secret_id] = value
        self.revisions[secret_id] = datetime.now(UTC)

    def delete(self, secret_id: str) -> None:
        del self.items[secret_id]
        self.deleted.append(secret_id)


@dataclass
class Bitwarden:
    """The provider: a set of live tokens, and a revoke only the test (the person) performs."""

    live: set[str] = field(default_factory=set)
    name: str = bws.PROVIDER
    probe_target: str = "a fresh login"
    revoke_probe_target: str = "a fresh login"
    dead_statuses: frozenset[int] = frozenset({400})
    hosts_destinations: bool = False

    def probe(self, value: Value) -> int | None:
        return 200 if value.reveal() in self.live else 400

    probe_before_revoke = probe

    def known_bad(self) -> Value:
        return bws.BwsMachineTokenProvider().known_bad()

    def mint(self) -> Value:
        raise ContractError("a person mints")

    def host(self, new: Value, old: Value) -> str:
        raise ContractError("no host")

    def consumer_loaded(self, consumer: Mapping[str, Any], new: Value) -> bool:
        raise ContractError("no hosted consumer")

    def revoke(self, value: Value) -> str:
        raise ContractError("a person revokes")


class Rotating:
    def __init__(self) -> None:
        self.store = KeychainShaped()
        self.old = Value(TOKEN)
        self.new = Value("0.66666666-7777-4888-8999-000000000000.Def456:cXJzdHV2==")
        self.bitwarden = Bitwarden({self.old.reveal()})
        self.store.put(KEEPER, self.old)
        self.store.put(COPY, self.old)
        self.rotation = Rotation(
            "bws-tok-brain-ops",
            "2026-10-22-requested",
            KEEPER,
            WORK,
            (COPY, KEEPER, 'provider name="bitwarden-sm"'),
        )
        self.filed: list[StepEvidence] = []

    def stage(self) -> None:
        # The person's mint act: the token exists in Bitwarden and is staged by prompt.
        self.bitwarden.live.add(self.new.reveal())
        self.store.create(WORK, self.rotation.staging_key, self.new)

    def run(self, unit: str, **changes: Any) -> None:
        run = UnitRun(
            rotation=self.rotation,
            store=self.store,
            provider=self.bitwarden,
            attestations=(),
            record=lambda _step, evidence: self.filed.append(evidence),
            before_step=lambda: None,
            **changes,
        )
        run.run(bws.PLAN.unit(unit))


def test_a_token_rotates_through_every_keychain_item_and_is_proved_dead() -> None:
    r = Rotating()
    r.stage()
    r.run("store")
    assert r.store.read(KEEPER).same_as(r.new) and r.store.read(COPY).same_as(r.new)
    r.run("verify", person_revokes_next=True)
    [verify] = [e for e in r.filed if e.step == StepKind.VERIFY_CONSUMERS]
    assert [(p.subject, p.outcome) for p in verify.probes] == [
        ("known_bad", "dead"),
        ("old", "live"),
    ]
    r.bitwarden.live.discard(r.old.reveal())
    r.run("confirm")
    assert [e.step for e in r.filed] == [
        "verify_new",
        "store",
        "distribute",
        "verify_consumers",
        "confirm_dead",
        "confirm_dead",
    ]
    assert set(r.store.items) == {KEEPER, COPY}
    assert len(r.store.deleted) == 2
    for evidence in r.filed:
        jsonschema.validate(evidence.as_payload(), SCHEMA)
        assert TOKEN not in json.dumps(evidence.as_payload())


def test_an_item_the_switch_missed_stops_the_rotation_before_the_revoke() -> None:
    r = Rotating()
    r.stage()
    r.run("store")
    r.store.put(COPY, r.old)
    with pytest.raises(ContractError, match="stored consumer doesn't read back"):
        r.run("verify", person_revokes_next=True)


def test_an_old_token_already_dead_before_the_revoke_stops_the_rotation() -> None:
    # Confirm-dead's "dead" would then prove nothing about the person's revoke.
    r = Rotating()
    r.stage()
    r.run("store")
    r.bitwarden.live.discard(r.old.reveal())
    with pytest.raises(ContractError, match="old value probed dead"):
        r.run("verify", person_revokes_next=True)


def test_without_a_person_revoking_next_verify_probes_nothing() -> None:
    r = Rotating()
    r.stage()
    r.run("store")
    r.bitwarden.live.discard(r.old.reveal())
    r.run("verify")
    assert r.filed[-1].probes == ()


def test_confirm_refuses_an_old_token_still_live() -> None:
    r = Rotating()
    r.stage()
    r.run("store")
    r.run("verify", person_revokes_next=True)
    with pytest.raises(ContractError, match="old value probed live"):
        r.run("confirm")
    assert set(r.store.items) > {KEEPER, COPY}


def test_the_keychain_adapters_are_the_ones_the_orchestrator_confines() -> None:
    # Two lists, one in each program: if they drift, the units of an adapter one side calls
    # Keychain and the other doesn't are never claimed, and nothing reports it.
    from orchestrator.services.lifecycle.rotation_claims import KEYCHAIN_ADAPTERS

    keychain = {name for name, adapter in ADAPTERS.items() if adapter.store_kind == "keychain"}
    assert keychain == KEYCHAIN_ADAPTERS


# ---- the login's scrubbed answer and its one retry (amendment 37) ---------------------------

SDK_TEXT = "Received error message from server"


class _FakeClient:
    """`bitwarden_sdk.BitwardenClient` as `_LOGIN` uses it: the login raises `error`, if any."""

    error: BaseException | None = None

    def auth(self) -> _FakeClient:
        return self

    def login_access_token(self, token: str) -> None:
        if self.error is not None:
            raise self.error


class SdkError(Exception):
    pass


def _run_login(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: BaseException | None
) -> tuple[str, str]:
    """`_LOGIN`'s own text, run in this process against a fake SDK: what it prints."""
    import io
    import sys
    import types

    sdk = types.ModuleType(bws.CHILD_SDK)
    _FakeClient.error = error
    vars(sdk)["BitwardenClient"] = _FakeClient
    monkeypatch.setitem(sys.modules, bws.CHILD_SDK, sdk)
    monkeypatch.setattr(sys, "stdin", io.StringIO("placeholder"))
    capsys.readouterr()
    exec(bws._LOGIN, {"__name__": "__main__"})
    captured = capsys.readouterr()
    return captured.out, captured.err


def _sdk_answers(token: str) -> list[tuple[BaseException | None, str]]:
    """Each shape the SDK answers in, and the category the child must print for it. Every
    message carries the token and the SDK's own text, neither of which may reach stdout."""
    return [
        (None, "live"),
        (SdkError(f'[400 Bad Request] {{"error":"invalid_client"}} {SDK_TEXT} {token}'), "refused"),
        (
            SdkError(f'[400 Bad Request] {{"error":"invalid_grant"}} {SDK_TEXT} {token}'),
            "status:400",
        ),
        (SdkError(f"[429 Too Many Requests] {SDK_TEXT} {token}"), "status:429"),
        (SdkError(f"[503 Service Unavailable] {SDK_TEXT} {token}"), "status:503"),
        # `invalid_client` without the 400 isn't a refusal.
        (SdkError(f'{{"error":"invalid_client"}} {SDK_TEXT} {token}'), "error:SdkError"),
        (ConnectionError(f"{SDK_TEXT} {token} 400"), "error:ConnectionError"),
        (RuntimeError(f"{SDK_TEXT} {token}"), "error:RuntimeError"),
    ]


def test_the_login_child_prints_only_its_category(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token = bws.BwsMachineTokenProvider().known_bad().reveal()
    for error, category in _sdk_answers(token):
        out, err = _run_login(monkeypatch, capsys, error)
        assert out == f"{category}\n", category
        assert bws.ANSWER.fullmatch(out.strip())
        assert err == ""
        for text in (token, SDK_TEXT, "invalid_client", "Bad Request"):
            assert text not in out + err


def _answers(monkeypatch: pytest.MonkeyPatch, *children: Any) -> list[dict[str, Any]]:
    """The child's successive answers: a stdout string, a `(stdout, returncode)` pair, or `None`
    for a timeout. Fails if asked more often than given."""
    seen: list[dict[str, Any]] = []
    queue = list(children)

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append({"argv": argv, **kwargs})
        child = queue.pop(0)
        if child is None:
            raise subprocess.TimeoutExpired(
                argv, kwargs["timeout"], output=SDK_TEXT, stderr=SDK_TEXT
            )
        stdout, code = child if isinstance(child, tuple) else (child, 0)
        return subprocess.CompletedProcess(argv, code, stdout, f"{SDK_TEXT} on stderr")

    monkeypatch.setattr(bws.subprocess, "run", run)
    return seen


def _logins(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """Each `bws_login` line's fields, as `extra=` set them."""
    return [vars(r) for r in caplog.records if r.getMessage() == "bws_login"]


INDETERMINATE = [
    (None, "timeout"),
    ("", "empty"),
    (("", 1), "empty"),
    ("status:429\n", "status:429"),
    ("status:400\n", "status:400"),
    ("error:ConnectionError\n", "error:ConnectionError"),
    ("other\n", "unparsed"),
    (f"refused\n{SDK_TEXT}\n", "unparsed"),
]


@pytest.mark.parametrize(("first", "category"), INDETERMINATE)
@pytest.mark.parametrize(("second", "status"), [("live\n", 200), ("refused\n", 400), ("", None)])
def test_an_indeterminate_login_is_tried_once_more_and_its_second_answer_stands(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    _no_pause: list[float],
    first: Any,
    category: str,
    second: str,
    status: int | None,
) -> None:
    seen = _answers(monkeypatch, first, second)
    provider = bws.BwsMachineTokenProvider("/py")
    with caplog.at_level(logging.INFO, logger="rotation_worker"):
        assert provider.probe(provider.known_bad()) == status
    assert len(seen) == 2 and _no_pause == [bws.RETRY_PAUSE]
    lines = _logins(caplog)
    assert [(r["role"], r["attempt"]) for r in lines] == [("control", 1), ("control", 2)]
    assert lines[0]["answer"] == category


@pytest.mark.parametrize(("stdout", "status"), [("live\n", 200), ("refused\n", 400)])
def test_a_determinate_login_is_never_retried(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    _no_pause: list[float],
    stdout: str,
    status: int,
) -> None:
    # A known-bad value that logs in fails at once: the contract refuses it, with no retry.
    seen = _answers(monkeypatch, stdout)
    provider = bws.BwsMachineTokenProvider("/py")
    with caplog.at_level(logging.INFO, logger="rotation_worker"):
        assert provider.probe(provider.known_bad()) == status
    assert len(seen) == 1 and _no_pause == []
    [line] = _logins(caplog)
    assert (line["role"], line["attempt"], line["answer"]) == ("control", 1, stdout.strip())


def test_never_more_than_one_retry(monkeypatch: pytest.MonkeyPatch, _no_pause: list[float]) -> None:
    seen = _answers(monkeypatch, None, None)
    assert (
        bws.BwsMachineTokenProvider("/py").probe(bws.BwsMachineTokenProvider().known_bad()) is None
    )
    assert len(seen) == 2 and len(_no_pause) == 1


def test_the_log_line_names_the_subject_and_never_a_value_or_the_sdk_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from rotation_worker.logs import JsonFormatter

    provider = bws.BwsMachineTokenProvider("/py")
    control = provider.known_bad()
    subject = bws.BwsMachineTokenProvider().known_bad()
    _answers(monkeypatch, f"{SDK_TEXT} {subject.reveal()}\n", ("", 3), None, "refused\n")
    with caplog.at_level(logging.INFO, logger="rotation_worker"):
        assert provider.probe(subject) is None
        assert provider.probe(control) == 400
    records = [r for r in caplog.records if r.getMessage() == "bws_login"]
    lines = _logins(caplog)
    assert [(r["role"], r["attempt"], r["answer"]) for r in lines] == [
        ("subject", 1, "unparsed"),
        ("subject", 2, "empty"),
        ("control", 1, "timeout"),
        ("control", 2, "refused"),
    ]
    assert lines[0]["stdout_length"] == len(f"{SDK_TEXT} {subject.reveal()}")
    assert lines[1]["returncode"] == 3
    assert "returncode" not in lines[2]
    assert all(isinstance(r["elapsed_seconds"], float) for r in lines)
    assert [r["levelname"] for r in lines] == ["WARNING", "WARNING", "WARNING", "INFO"]
    rendered = "\n".join(JsonFormatter().format(r) for r in records)
    raw = json.dumps(lines, default=str)
    for text in (subject.reveal(), control.reveal(), SDK_TEXT):
        assert text not in rendered and text not in raw
    for value in (subject, control):
        assert value.fingerprint.sha256_prefix not in rendered


def test_the_other_fresh_logins_keep_their_answers_with_no_retry_or_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, _no_pause: list[float]
) -> None:
    provider = bws.BwsMachineTokenProvider("/py", organization_id="org")
    new, old = provider.known_bad(), provider.known_bad()
    ids = json.dumps({"projects": ["p"], "secrets": ["s"]})
    cases: list[tuple[Any, tuple[Any, ...], Any]] = [
        (lambda: provider.reach(new), ("a" * 64 + "\n",), "a" * 64),
        (lambda: provider.reach(new), ("unreadable\n",), None),
        (lambda: provider.reach(new), (None,), None),
        (lambda: provider.reach_sets(new), (ids + "\n",), (frozenset({"p"}), frozenset({"s"}))),
        (lambda: provider.reach_sets(new), ("unreadable\n",), None),
        (lambda: provider.reach_sets(new), (None,), None),
        (lambda: provider.same_identity(new, old), ("a" * 64, "a" * 64), True),
        (lambda: provider.same_identity(new, old), ("unreadable", "unreadable"), False),
        (lambda: provider.same_identity(new, old), (None, None), False),
    ]
    with caplog.at_level(logging.DEBUG):
        for call, children, expected in cases:
            seen = _answers(monkeypatch, *children)
            assert call() == expected
            assert len(seen) == len(children)
    assert _no_pause == []
    assert caplog.records == []
    # The capture sees a login's line, so the empty list above is no blind spot.
    _answers(monkeypatch, "refused\n")
    with caplog.at_level(logging.DEBUG):
        provider.probe(new)
    assert [r["answer"] for r in _logins(caplog)] == ["refused"]
