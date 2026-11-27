"""`rotation-worker probe`: a one-off proof of a key already kept (ADR-0055 amendment 36 exit check
1, amendment 35 exit check 2).

It reads the row's keeper through the instance's own store, sends only its adapter's confined
requests (Anthropic's `GET /v1/models`, once per header form for the key and a control; a GitHub
App key's working proof), prints one JSON line of statuses and non-secret ids, and writes
nothing. Keys are built at run time; none is committed.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from rotation_worker import cli, values
from rotation_worker.adapters import anthropic_key, github_app_key
from rotation_worker.config import Config
from rotation_worker.contract import ContractError, Rotation
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.registry import REGISTRY_PATH, REGISTRY_REPOSITORIES
from rotation_worker.secret_store import StoredSecret, StoreError
from rotation_worker.values import Value
from tests.rotation_worker.conftest import Origins
from tests.rotation_worker.stores import MemoryStore
from tests.rotation_worker.test_anthropic_key import WORKSPACE, Anthropic, _key, _sent
from tests.rotation_worker.test_github_app_key import (
    APP,
    INSTALLATION,
    PROOF,
    GitHub,
    _stored,
)

KEEPER = "0e9ebf2d-5b62-4f81-a731-b50a00e0df18"
WORK = "22222222-2222-4222-8222-222222222222"
ANTHROPIC_ID = "anthropic-change-window-key"
GITHUB_ID = "lanes-read-app-private-key"
# The row as it stands before exit check 1: no `workspace_id`, no `rotated_by_sds`.
ANTHROPIC_ROW = f"""
[[credential]]
id = "{ANTHROPIC_ID}"
class = "anthropic-api-key"
provider = "anthropic"
bws_uuid = "{KEEPER}"

  [[credential.consumer]]
  kind = "bws-secret"
  uuid = "{KEEPER}"
"""
GITHUB_ROW = f"""
[[credential]]
id = "{GITHUB_ID}"
class = "github-app-private-key"
provider = "github"
bws_uuid = "{KEEPER}"
app_id = {APP}
installation_id = {INSTALLATION}
proof_repository = "{PROOF}"

  [[credential.consumer]]
  kind = "bws-secret"
  uuid = "{KEEPER}"
"""
OTHER_ROW = f"""
[[credential]]
id = "rotation-canary"
class = "rotation-canary"
provider = "fake"
bws_uuid = "{KEEPER}"
"""


class WatchedStore(MemoryStore):
    """A store that records every write, so a test can show the probe made none."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def create(self, project_id: str, key: str, value: Value) -> StoredSecret:
        self.writes.append(f"create {key}")
        return super().create(project_id, key, value)

    def write(self, secret_id: str, value: Value) -> None:
        self.writes.append(f"write {secret_id}")
        super().write(secret_id, value)

    def delete(self, secret_id: str) -> None:
        self.writes.append(f"delete {secret_id}")
        super().delete(secret_id)


class UnreadableStore(MemoryStore):
    """The root can't read the keeper: its refusal carries the value, which must never show."""

    def __init__(self, held: str) -> None:
        super().__init__()
        self.held = held

    def read(self, secret_id: str) -> Value:
        raise StoreError(f"BWS refused {secret_id}: {self.held}")


def _config(tmp_path: Path) -> Config:
    return Config(
        orchestrator_url="https://sds.example",
        credential_key_id="rotation-executor-1",
        organization_id="11111111-1111-4111-8111-111111111111",
        work_project_id=WORK,
        bearer_secret_id=None,
        root_token_file=tmp_path / "root-token",
        state_dir=tmp_path,
        source_revision="0123456789ab",
    )


def _registry(origins: Origins, rows: str) -> None:
    for repository in REGISTRY_REPOSITORIES:
        text = rows if repository.endswith("/orchestrator") else ""
        origins.commit(repository, {REGISTRY_PATH: "version = 1\n" + text})


def _keep(store: MemoryStore, value: Value) -> None:
    store.secrets[KEEPER] = (WORK, "keeper", value)


@pytest.fixture(autouse=True)
def own_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """`scrub` redacts every value the process has wrapped. In a test run that includes other
    tests' short placeholders (a one-letter value redacts that letter from the line); the probe's
    own process holds only the key and its control."""
    monkeypatch.setattr(values, "_TOUCHED", set())


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> WatchedStore:
    watched = WatchedStore()
    monkeypatch.setattr(cli, "open_store", lambda config: watched)

    def no_client(*_: object) -> None:
        raise AssertionError("the probe opened the orchestrator client")

    # The probe reads no bearer and records nothing: it never opens the orchestrator.
    monkeypatch.setattr(cli, "open_client", no_client)
    monkeypatch.setattr(cli, "build_setup", no_client)
    return watched


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The simulated service each request reaches: the probe builds its adapters' real clients,
    and only their transport's last hop is answered here instead of by the network."""
    handlers: list[Any] = []

    def answer(_transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        return handlers[0](request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", answer)
    return handlers


def _probe(
    tmp_path: Path, mirror: RepoMirror, credential_id: str, handle: Any, answers: list[Any]
) -> tuple[int, str, list[dict[str, Any]]]:
    answers[:] = [handle]
    out = io.StringIO()
    code = cli.probe(_config(tmp_path), credential_id, mirror, out)
    text = out.getvalue()
    return code, text, [json.loads(line) for line in text.splitlines()]


# ---- Anthropic ------------------------------------------------------------------------------


def test_the_anthropic_probe_sends_get_v1_models_alone_once_per_header_form(
    tmp_path: Path, origins: Origins, mirror: RepoMirror, store: WatchedStore, answers: list[Any]
) -> None:
    anthropic = Anthropic()
    key = anthropic.issue()
    _keep(store, Value(key))
    _registry(origins, ANTHROPIC_ROW)
    code, _, _ = _probe(tmp_path, mirror, ANTHROPIC_ID, anthropic.handle, answers)
    assert code == 0
    sent = anthropic.requests
    assert [(r.method, str(r.url)) for r in sent] == [
        ("GET", "https://api.anthropic.com/v1/models")
    ] * 4
    forms = [
        ("x-api-key" in r.headers, "Authorization" in r.headers, _sent(r) == key) for r in sent
    ]
    # The key, then a control, each as `x-api-key` and as a bearer; never both in one request.
    assert forms == [(True, False, True), (False, True, True), (True, False, False)] + [
        (False, True, False)
    ]
    for request in sent:
        assert request.content == b""
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "anthropic-workspace-id" not in request.headers
    assert sent[1].headers["Authorization"] == f"Bearer {key}"


def test_the_anthropic_line_is_statuses_and_the_workspace_header_only(
    tmp_path: Path, origins: Origins, mirror: RepoMirror, store: WatchedStore, answers: list[Any]
) -> None:
    anthropic = Anthropic()
    _keep(store, Value(anthropic.issue()))
    _registry(origins, ANTHROPIC_ROW)
    _, text, lines = _probe(tmp_path, mirror, ANTHROPIC_ID, anthropic.handle, answers)
    assert text.endswith("\n") and len(lines) == 1
    assert lines[0] == {
        "credential_id": ANTHROPIC_ID,
        "class": "anthropic-api-key",
        "keeper": KEEPER,
        "key_x_api_key": {"status": 200, "anthropic-workspace-id": WORKSPACE},
        "key_bearer": {"status": 401, "anthropic-workspace-id": None},
        "control_x_api_key": {"status": 401, "anthropic-workspace-id": None},
        "control_bearer": {"status": 401, "anthropic-workspace-id": None},
        "passed": True,
    }


def test_both_header_forms_are_measured_when_anthropic_takes_both(
    tmp_path: Path, origins: Origins, mirror: RepoMirror, store: WatchedStore, answers: list[Any]
) -> None:
    anthropic = Anthropic(takes_bearer=True)
    _keep(store, Value(anthropic.issue()))
    _registry(origins, ANTHROPIC_ROW)
    _, _, [line] = _probe(tmp_path, mirror, ANTHROPIC_ID, anthropic.handle, answers)
    assert line["key_x_api_key"]["status"] == line["key_bearer"]["status"] == 200
    assert line["control_x_api_key"]["status"] == line["control_bearer"]["status"] == 401


def test_a_malformed_workspace_header_is_never_echoed(
    tmp_path: Path, origins: Origins, mirror: RepoMirror, store: WatchedStore, answers: list[Any]
) -> None:
    anthropic = Anthropic()
    _keep(store, Value(anthropic.issue("not a workspace id <script>")))
    _registry(origins, ANTHROPIC_ROW)
    _, text, [line] = _probe(tmp_path, mirror, ANTHROPIC_ID, anthropic.handle, answers)
    assert line["key_x_api_key"]["anthropic-workspace-id"] == "(a malformed id)"
    assert "<script>" not in text


def test_no_key_control_or_header_reaches_stdout_stderr_or_a_log_line(
    tmp_path: Path,
    origins: Origins,
    mirror: RepoMirror,
    store: WatchedStore,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    answers: list[Any],
) -> None:
    caplog.set_level(logging.DEBUG)
    anthropic = Anthropic()
    key = anthropic.issue()
    _keep(store, Value(key))
    _registry(origins, ANTHROPIC_ROW)
    _, text, _ = _probe(tmp_path, mirror, ANTHROPIC_ID, anthropic.handle, answers)
    captured = capsys.readouterr()
    shown = " ".join([text, captured.out, captured.err, caplog.text])
    sent = {_sent(request) for request in anthropic.requests}
    assert key in sent and len(sent) == 2
    for value in sent:
        assert value not in shown
        assert value[len(anthropic_key.KEY_PREFIX) :][:16] not in shown
    assert "Bearer" not in shown and "x-api-key" not in shown.replace("x_api_key", "")


def test_a_dead_key_or_an_accepted_control_does_not_pass(
    tmp_path: Path,
    origins: Origins,
    mirror: RepoMirror,
    store: WatchedStore,
    answers: list[Any],
) -> None:
    """Exit 0 means the exit check passed, not only that every request got an answer."""
    _registry(origins, ANTHROPIC_ROW)
    _keep(store, Value(_key()))
    code, _, [line] = _probe(tmp_path, mirror, ANTHROPIC_ID, Anthropic().handle, answers)
    assert (code, line["key_x_api_key"]["status"], line["passed"]) == (1, 401, False)
    anthropic = Anthropic()
    _keep(store, Value(anthropic.issue()))
    code, _, [line] = _probe(tmp_path, mirror, ANTHROPIC_ID, Anthropic(every=200).handle, answers)
    assert (code, line["control_x_api_key"]["status"], line["passed"]) == (1, 200, False)


def test_a_request_with_no_answer_names_its_failure_s_type_and_exits_1(
    tmp_path: Path, origins: Origins, mirror: RepoMirror, store: WatchedStore, answers: list[Any]
) -> None:
    anthropic = Anthropic(down=True)
    _keep(store, Value(_key()))
    _registry(origins, ANTHROPIC_ROW)
    code, _, [line] = _probe(tmp_path, mirror, ANTHROPIC_ID, anthropic.handle, answers)
    assert code == 1
    assert line["key_x_api_key"] == {"status": None, "no_answer": "ConnectError"}


def test_only_the_bearer_request_lost_still_fails(
    tmp_path: Path, origins: Origins, mirror: RepoMirror, store: WatchedStore, answers: list[Any]
) -> None:
    """Kills: a verdict that reads only the rotation's form, so a lost request exits 0."""
    anthropic = Anthropic()
    _keep(store, Value(anthropic.issue()))
    _registry(origins, ANTHROPIC_ROW)

    def lose_the_bearer(request: httpx.Request) -> httpx.Response:
        if "Authorization" in request.headers and _sent(request) in anthropic.keys:
            raise httpx.ReadTimeout("lost", request=request)
        return anthropic.handle(request)

    code, _, [line] = _probe(tmp_path, mirror, ANTHROPIC_ID, lose_the_bearer, answers)
    assert line["key_x_api_key"]["status"] == 200
    assert line["key_bearer"] == {"status": None, "no_answer": "ReadTimeout"}
    assert (code, line["passed"]) == (1, False)


@pytest.mark.parametrize("case", ["a control's 429", "a 200 naming no workspace"])
def test_an_indeterminate_answer_does_not_pass(
    tmp_path: Path,
    origins: Origins,
    mirror: RepoMirror,
    store: WatchedStore,
    answers: list[Any],
    case: str,
) -> None:
    """Amendment 36: a 429 or 5xx is indeterminate (item 9), and a 200 without the workspace
    header can't bind (exit check 1)."""
    anthropic = Anthropic(no_header=case == "a 200 naming no workspace")
    key = anthropic.issue()
    _keep(store, Value(key))
    _registry(origins, ANTHROPIC_ROW)

    def answer(request: httpx.Request) -> httpx.Response:
        if case == "a control's 429" and _sent(request) != key:
            return httpx.Response(429)
        return anthropic.handle(request)

    code, _, [line] = _probe(tmp_path, mirror, ANTHROPIC_ID, answer, answers)
    assert line["key_x_api_key"]["status"] == 200
    assert (code, line["passed"]) == (1, False)


def test_an_unforeseen_failure_exits_2_naming_only_its_class(
    tmp_path: Path,
    origins: Origins,
    mirror: RepoMirror,
    store: WatchedStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answers: list[Any],
) -> None:
    key = _key()
    _keep(store, Value(key))
    _registry(origins, ANTHROPIC_ROW)

    def broken(_row: Any, value: Value) -> dict[str, Any]:
        raise RuntimeError(f"failed holding {value.reveal()}")

    monkeypatch.setitem(cli.PROBES, "anthropic-api-key", broken)
    code, text, _ = _probe(tmp_path, mirror, ANTHROPIC_ID, Anthropic().handle, answers)
    captured = capsys.readouterr()
    assert (code, text) == (2, "")
    assert captured.err == f"probe: {ANTHROPIC_ID} unprobed: RuntimeError\n"
    assert key not in captured.err + captured.out


def test_build_provider_and_check_row_still_refuse_the_row_without_a_workspace() -> None:
    """The probe builds no provider: the rotation's refusals stand for the row it probes."""
    row: dict[str, Any] = {
        "id": ANTHROPIC_ID,
        "class": "anthropic-api-key",
        "provider": "anthropic",
        "consumer": [{"kind": "bws-secret", "uuid": KEEPER}],
    }
    rotation = Rotation(ANTHROPIC_ID, "o", KEEPER, WORK, ())
    with pytest.raises(ContractError, match="declares its workspace_id"):
        anthropic_key.build_provider(rotation=rotation, credential=row, store=MemoryStore())
    with pytest.raises(ContractError, match="declares its workspace_id"):
        anthropic_key.check_row(row)


# ---- GitHub App keys ------------------------------------------------------------------------


def test_the_github_probe_sends_the_working_proof_s_calls_alone(
    tmp_path: Path, origins: Origins, mirror: RepoMirror, store: WatchedStore, answers: list[Any]
) -> None:
    github = GitHub()
    _keep(store, _stored(github.issue()))
    _registry(origins, GITHUB_ROW)
    code, _, [line] = _probe(tmp_path, mirror, GITHUB_ID, github.handle, answers)
    assert code == 0
    # `check_scope`'s calls as the rotation sends them; the mint resolves the installation itself.
    assert [(r.method, r.url.path) for r in github.requests] == [
        ("GET", f"/repos/{PROOF}/installation"),
        ("GET", f"/repos/{PROOF}/installation"),
        ("POST", f"/app/installations/{INSTALLATION}/access_tokens"),
        ("GET", github_app_key.REPOSITORIES_PATH),
        ("DELETE", github_app_key.TOKEN_PATH),
    ]
    assert {r.url.host for r in github.requests} == {"api.github.com"}
    assert github.tokens == {} and len(github.revoked) == 1
    assert line == {
        "credential_id": GITHUB_ID,
        "class": "github-app-private-key",
        "keeper": KEEPER,
        "app_id": APP,
        "installation_id": INSTALLATION,
        "proof_repository": PROOF,
        "status": "ok",
        "passed": True,
    }


def test_a_refused_github_proof_names_statuses_and_ids_only_and_exits_1(
    tmp_path: Path,
    origins: Origins,
    mirror: RepoMirror,
    store: WatchedStore,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    answers: list[Any],
) -> None:
    caplog.set_level(logging.DEBUG)
    github = GitHub(reach=[PROOF, "AlobarQuest/other"])
    stored = _stored(github.issue())
    _keep(store, stored)
    _registry(origins, GITHUB_ROW)
    code, text, [line] = _probe(tmp_path, mirror, GITHUB_ID, github.handle, answers)
    assert code == 1
    assert line["status"] == "refused"
    assert line["refusal"] == f"the proof token doesn't reach {PROOF} alone"
    # The token was revoked all the same.
    assert github.tokens == {}
    captured = capsys.readouterr()
    shown = " ".join([text, captured.out, captured.err, caplog.text])
    tokens = [r.headers["Authorization"].removeprefix("Bearer ") for r in github.requests]
    for secret in (stored.reveal(), *tokens):
        assert secret not in shown
    assert "PRIVATE KEY" not in shown


def test_a_github_row_without_its_ids_refuses_with_nothing_sent(
    tmp_path: Path, origins: Origins, mirror: RepoMirror, store: WatchedStore, answers: list[Any]
) -> None:
    github = GitHub()
    _keep(store, _stored(github.issue()))
    _registry(origins, GITHUB_ROW.replace(f"app_id = {APP}\n", ""))
    code, text, _ = _probe(tmp_path, mirror, GITHUB_ID, github.handle, answers)
    assert (code, text, github.requests) == (2, "", [])


# ---- what the probe never does --------------------------------------------------------------


@pytest.mark.parametrize(
    ("credential_id", "rows"), [(ANTHROPIC_ID, ANTHROPIC_ROW), (GITHUB_ID, GITHUB_ROW)]
)
def test_an_unreadable_keeper_exits_2_saying_so_and_nothing_else(
    tmp_path: Path,
    origins: Origins,
    mirror: RepoMirror,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    credential_id: str,
    rows: str,
    answers: list[Any],
) -> None:
    held = _key()
    monkeypatch.setattr(cli, "open_store", lambda config: UnreadableStore(held))
    _registry(origins, rows)
    sent: list[httpx.Request] = []
    code, text, _ = _probe(tmp_path, mirror, credential_id, sent.append, answers)
    captured = capsys.readouterr()
    assert (code, text, captured.out, sent) == (2, "", "", [])
    assert captured.err == f"probe: {credential_id}'s keeper is not readable by this identity\n"


class UnlocatableStore(MemoryStore):
    """A keeper destination the store can't parse: `locate` itself raises."""

    def locate(self, consumer: Any) -> str | None:
        raise KeyError("uuid")


def test_a_keeper_the_store_can_t_locate_exits_2_without_a_traceback(
    tmp_path: Path,
    origins: Origins,
    mirror: RepoMirror,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answers: list[Any],
) -> None:
    monkeypatch.setattr(cli, "open_store", lambda config: UnlocatableStore())
    _registry(origins, ANTHROPIC_ROW)
    code, _, _ = _probe(tmp_path, mirror, ANTHROPIC_ID, Anthropic().handle, answers)
    assert code == 2
    assert capsys.readouterr().err == (
        f"probe: {ANTHROPIC_ID}'s keeper is not readable by this identity\n"
    )


def test_a_keeper_another_store_keeps_is_not_readable_by_this_identity(
    tmp_path: Path,
    origins: Origins,
    mirror: RepoMirror,
    store: WatchedStore,
    capsys: pytest.CaptureFixture[str],
    answers: list[Any],
) -> None:
    """The Keychain instance's store locates no BWS keeper, and reads through no other identity."""
    store.read_back_kind = "keychain"
    _registry(origins, ANTHROPIC_ROW)
    sent: list[httpx.Request] = []
    code, _, _ = _probe(tmp_path, mirror, ANTHROPIC_ID, sent.append, answers)
    assert (code, sent) == (2, [])
    assert "not readable by this identity" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("credential_id", "rows", "service"),
    [(ANTHROPIC_ID, ANTHROPIC_ROW, "anthropic"), (GITHUB_ID, GITHUB_ROW, "github")],
)
def test_the_probe_writes_nothing(
    tmp_path: Path,
    origins: Origins,
    mirror: RepoMirror,
    store: WatchedStore,
    credential_id: str,
    rows: str,
    service: str,
    answers: list[Any],
) -> None:
    if service == "anthropic":
        anthropic = Anthropic()
        _keep(store, Value(anthropic.issue()))
        handle = anthropic.handle
    else:
        github = GitHub()
        _keep(store, _stored(github.issue()))
        handle = github.handle
    before = dict(store.secrets)
    _registry(origins, rows)
    code, _, _ = _probe(tmp_path, mirror, credential_id, handle, answers)
    assert code == 0
    assert store.writes == [] and store.secrets == before and store.deleted == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mirrors", "origins"]


def test_a_row_of_another_class_is_refused_before_the_store_opens(
    tmp_path: Path,
    origins: Origins,
    mirror: RepoMirror,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answers: list[Any],
) -> None:
    def no_store(config: Config) -> MemoryStore:
        raise AssertionError("the store was opened")

    monkeypatch.setattr(cli, "open_store", no_store)
    _registry(origins, OTHER_ROW)
    sent: list[httpx.Request] = []
    code, _, _ = _probe(tmp_path, mirror, "rotation-canary", sent.append, answers)
    assert (code, sent) == (2, [])
    assert "the probe reads anthropic-api-key and github-app-private-key rows" in (
        capsys.readouterr().err
    )


def test_the_entry_point_runs_the_probe_with_the_configured_store(
    answers: list[Any],
    tmp_path: Path,
    origins: Origins,
    mirror: RepoMirror,
    store: WatchedStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    anthropic = Anthropic()
    _keep(store, Value(anthropic.issue()))
    _registry(origins, ANTHROPIC_ROW)
    monkeypatch.setattr(cli, "load", lambda path: _config(tmp_path))
    monkeypatch.setattr(cli, "configure", lambda: None)
    monkeypatch.setattr(cli, "RepoMirror", lambda root: mirror)

    answers[:] = [anthropic.handle]
    assert cli.main(["probe", ANTHROPIC_ID, "--config", str(tmp_path / "c.toml")]) == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert json.loads(line)["key_x_api_key"]["status"] == 200
    assert len(anthropic.requests) == 4
