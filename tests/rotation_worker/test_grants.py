"""The executor acts only on a repository the member record grants `credential_rotation`.

The grant only narrows: it is asked about the repository the registry names, before the claim and
before each step. A record that can't be read is never an answer. Before the claim it leaves the
unit for the next pass; after the claim the unit carries on, since failing it mid-overlap could
leave two values live. An explicit withdrawal stops it, as a handed-back flag does.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

import rotation_worker.worker as worker
from orchestrator.persistence.models import GRANT_STATES
from rotation_worker import cli
from rotation_worker.config import Config, ConfigError
from rotation_worker.grants import GrantUnread, rotation_granted
from rotation_worker.notify import Act, Notifier, unit_waiting_key
from rotation_worker.orchestrator_client import Lease, OrchestratorError
from rotation_worker.registry import REGISTRY_REPOSITORIES
from rotation_worker.secret_store import StoreError
from rotation_worker.values import Value
from rotation_worker.worker import run_pass
from tests.rotation_worker.stores import MemoryStore, member_record
from tests.rotation_worker.test_worker import Harness

REPOSITORY = "AlobarQuest/orchestrator"
# A record that reads and doesn't name the repository: a record with no rows is unread.
UNGRANTED = member_record("AlobarQuest/other")


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(tmp_path, monkeypatch)


def _after_claim(h: Harness, monkeypatch: pytest.MonkeyPatch, change: Callable[[], None]) -> None:
    claim = h.client.claim

    def claimed(unit_id: str, version: int, key: str) -> Lease:
        lease = claim(unit_id, version, key)
        change()
        return lease

    monkeypatch.setattr(h.client, "claim", claimed)


def test_a_granted_repository_answers_true() -> None:
    assert rotation_granted(member_record(REPOSITORY), REPOSITORY) is True


def test_the_match_folds_case() -> None:
    assert rotation_granted(member_record("alobarquest/ORCHESTRATOR"), REPOSITORY) is True


def test_a_retired_member_is_not_granted() -> None:
    record = member_record(REPOSITORY, retired=[REPOSITORY])
    assert rotation_granted(record, REPOSITORY) is False


def test_an_absent_repository_is_not_granted() -> None:
    assert rotation_granted(member_record("AlobarQuest/other"), REPOSITORY) is False


def _in_state(state: str) -> dict[str, Any]:
    record = member_record(REPOSITORY)
    record["members"][0]["grants"]["credential_rotation"]["state"] = state
    return record


# The orchestrator's `GRANT_STATES`, less `granted`.
@pytest.mark.parametrize("state", ["not_assessed", "remediating", "not_applicable", "withdrawn"])
def test_any_other_grant_state_is_not_granted(state: str) -> None:
    assert rotation_granted(_in_state(state), REPOSITORY) is False


def test_the_states_tested_are_the_orchestrators() -> None:
    assert GRANT_STATES == (
        "not_assessed",
        "remediating",
        "granted",
        "not_applicable",
        "withdrawn",
    )


def _without(field: str) -> dict[str, Any]:
    record = member_record(REPOSITORY)
    del record["members"][0][field]
    return record


def _with_grants(grants: Any) -> dict[str, Any]:
    record = member_record(REPOSITORY)
    record["members"][0]["grants"] = grants
    return record


@pytest.mark.parametrize(
    "body",
    [
        [],
        "members",
        None,
        {},
        {"members": None},
        {"members": {}},
        {"members": []},
        {"members": [REPOSITORY]},
        {"members": [{"repository": 7, "state": "member", "grants": {}}]},
        _without("repository"),
        _without("state"),
        _without("grants"),
        _with_grants({"backups": {"state": "granted"}}),
        _with_grants({"credential_rotation": "granted"}),
        _with_grants({"credential_rotation": {}}),
        _with_grants({"credential_rotation": {"state": 1}}),
    ],
)
def test_a_malformed_record_is_unread_not_ungranted(body: Any) -> None:
    with pytest.raises(GrantUnread, match="member record"):
        rotation_granted(body, REPOSITORY)


def test_a_malformed_row_for_another_repository_still_leaves_the_record_unread() -> None:
    record = member_record(REPOSITORY)
    record["members"].append({"repository": "AlobarQuest/other"})
    with pytest.raises(GrantUnread, match="member record"):
        rotation_granted(record, REPOSITORY)


def test_the_reader_imports_no_client() -> None:
    tree = ast.parse(Path("src/rotation_worker/grants.py").read_text())
    imported = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [a.name for a in node.names]
        )
    }
    assert imported == {"__future__", "collections.abc", "typing"}


def test_the_worker_turns_an_unread_record_into_its_own_transient_error(h: Harness) -> None:
    h.client.record = {"members": []}
    with pytest.raises(OrchestratorError, match="no rows"):
        worker._granted(h.setup(), REPOSITORY)


def test_no_grant_waits_unclaimed_and_starts_the_clock(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h.add("u1", "mint")
    h.client.record = UNGRANTED
    found = worker.PassActs()
    with caplog.at_level(logging.INFO, logger="rotation_worker"):
        assert worker._take(h.setup(), "u1", found) == 0
    assert h.client.calls == []
    wait = found.waits["u1"]
    assert wait.reason == f"{REPOSITORY} has no credential_rotation grant"
    assert (wait.reader, wait.stuck) == (False, False)
    assert worker._clock_key("u1", wait) == unit_waiting_key("u1")
    assert any(r.message == "unit_waiting" for r in caplog.records)


def test_a_unit_waiting_for_a_grant_holds_every_open_alert(h: Harness) -> None:
    announced: list[tuple[dict[str, Act], set[str]]] = []

    class Listening:
        def announce(self, acts: dict[str, Act], hold: set[str]) -> None:
            announced.append((acts, set(hold)))

    h.add("u1", "mint")
    h.client.record = UNGRANTED
    run_pass(replace(h.setup(), notifier=cast(Notifier, Listening())))
    acts, hold = announced[0]
    assert not any("u1" in key for key in acts)
    assert {
        worker._mint_key("u1"),
        worker._revoke_key("u1"),
        worker._inbox_key("u1"),
        unit_waiting_key("u1"),
    } <= hold


def test_an_unread_grant_before_the_claim_skips_the_unit(h: Harness) -> None:
    h.add("u1", "mint")
    h.add("u2", "mint")
    h.client.unreachable.add("members")
    assert run_pass(h.setup()) == 0
    assert h.client.calls == []
    assert h.client.failures == {}


def test_a_malformed_record_before_the_claim_is_a_skip_not_a_grant_wait(h: Harness) -> None:
    h.add("u1", "mint")
    h.client.record = {"members": [{"repository": REPOSITORY}]}
    found = worker.PassActs()
    with pytest.raises(OrchestratorError):
        worker._take(h.setup(), "u1", found)
    assert h.client.calls == []
    assert found.waits == {}


def test_an_empty_record_before_the_claim_skips_the_unit(h: Harness) -> None:
    h.add("u1", "mint")
    h.client.record = {"members": []}
    assert run_pass(h.setup()) == 0
    assert h.client.calls == []
    assert h.client.failures == {}


def test_a_withdrawn_grant_stops_before_the_next_step(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.add("u1", "mint")

    def withdraw() -> None:
        h.client.record = _in_state("withdrawn")

    _after_claim(h, monkeypatch, withdraw)
    assert run_pass(h.setup()) == 0
    assert h.client.failures["u1"] == (
        "ContractError: canary: the credential_rotation grant was withdrawn mid-rotation"
    )
    assert ("evidence", "u1") not in h.client.calls


def test_an_unread_grant_mid_rotation_carries_on(
    h: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    h.add("u1", "mint")
    _after_claim(h, monkeypatch, lambda: h.client.unreachable.add("members"))
    with caplog.at_level(logging.WARNING, logger="rotation_worker"):
        assert run_pass(h.setup()) == 1
    assert ("submit", "u1") in h.client.calls
    assert h.client.failures == {}
    unread = next(r for r in caplog.records if r.message == "grant_unread")
    assert unread.__dict__["credential"] == "canary"
    assert "unit" not in unread.__dict__


def test_a_malformed_record_mid_rotation_carries_on(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.add("u1", "mint")

    def garble() -> None:
        h.client.record = {"members": "soon"}

    _after_claim(h, monkeypatch, garble)
    assert run_pass(h.setup()) == 1
    assert h.client.failures == {}


def test_an_empty_record_mid_rotation_carries_on(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.add("u1", "mint")

    def empty() -> None:
        h.client.record = {"members": []}

    _after_claim(h, monkeypatch, empty)
    assert run_pass(h.setup()) == 1
    assert h.client.failures == {}


def test_each_step_reads_the_grant_once(h: Harness) -> None:
    h.add("u1", "mint")
    assert run_pass(h.setup()) == 1
    renewals = sum(call == "renew" for call, _ in h.client.calls)
    assert h.client.member_reads == renewals + 1


def _config(tmp_path: Path) -> Config:
    return Config(
        orchestrator_url="https://sds.example",
        credential_key_id="rotation-executor-1",
        organization_id="11111111-1111-4111-8111-111111111111",
        work_project_id="22222222-2222-4222-8222-222222222222",
        bearer_secret_id="33333333-3333-4333-8333-333333333333",
        root_token_file=tmp_path / "root-token",
        state_dir=tmp_path,
        source_revision="0123456789ab",
    )


def _answers_with(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(cli, "open_store", lambda config: MemoryStore())
    monkeypatch.setattr(cli, "open_client", lambda config, store: client)


def test_a_grant_outside_the_ceiling_adds_nothing(
    h: Harness,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outside = "AlobarQuest/somewhere-else"
    h.add("u1", "mint")
    h.client.record = member_record(outside)
    assert run_pass(h.setup()) == 0
    assert h.client.calls == []
    _answers_with(monkeypatch, h.client)
    assert cli.grants(_config(tmp_path)) == cli.EXIT_EQUAL
    assert outside not in capsys.readouterr().out


def test_no_ready_unit_reads_no_members(h: Harness) -> None:
    assert run_pass(h.setup()) == 0
    assert h.client.member_reads == 0


def test_another_kind_of_unit_reads_no_members(h: Harness) -> None:
    h.client.units["other"] = {"id": "other", "authority": {"constraints": {}}}
    assert run_pass(h.setup()) == 0
    assert h.client.member_reads == 0


def test_a_unit_the_checks_refuse_reads_no_members(h: Harness) -> None:
    h.add("u1", "mint")
    h.refusal = worker.ContractError("the registry's destinations differ")
    run_pass(h.setup())
    assert h.client.member_reads == 0


def test_grants_command_prints_each_registry_repository(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _answers_with(monkeypatch, SimpleNamespace(members=lambda: member_record(REPOSITORY.lower())))
    assert cli.grants(_config(tmp_path)) == cli.EXIT_EQUAL
    assert capsys.readouterr().out.splitlines() == [
        f"{repository}  {'granted' if repository == REPOSITORY else 'not granted'}"
        for repository in REGISTRY_REPOSITORIES
    ]


@pytest.mark.parametrize(
    "failing",
    [
        OrchestratorError("no answer"),
        ConfigError("bearer_secret_id is needed"),
        StoreError("the store didn't answer"),
    ],
)
def test_grants_command_exits_unreadable_and_prints_no_verdict(
    failing: Exception,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def refuse(config: Config, store: Any) -> Any:
        raise failing

    monkeypatch.setattr(cli, "open_store", lambda config: MemoryStore())
    monkeypatch.setattr(cli, "open_client", refuse)
    assert cli.grants(_config(tmp_path)) == cli.EXIT_UNREADABLE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(failing) in captured.err


def test_grants_command_says_unreadable_of_a_malformed_record(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _answers_with(monkeypatch, SimpleNamespace(members=lambda: {"x": 1}))
    assert cli.grants(_config(tmp_path)) == cli.EXIT_UNREADABLE
    assert capsys.readouterr().out == ""


def test_grants_command_says_unreadable_of_an_empty_record(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _answers_with(monkeypatch, SimpleNamespace(members=lambda: {"members": []}))
    assert cli.grants(_config(tmp_path)) == cli.EXIT_UNREADABLE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no rows" in captured.err


def test_the_command_uses_the_instance_s_bearer_and_prints_no_value(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bearer = "the-instances-own-bearer-value"
    store = MemoryStore()
    bearer_id = store.put("anywhere", "bearer", Value(bearer))
    config = replace(_config(tmp_path), bearer_secret_id=bearer_id)
    monkeypatch.setattr(cli, "load", lambda path: config)
    monkeypatch.setattr(cli, "configure", lambda: None)
    monkeypatch.setattr(cli, "open_store", lambda config: store)
    seen: list[httpx.Request] = []

    def answer(_transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=member_record(REPOSITORY))

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", answer)
    assert cli.main(["grants"]) == cli.EXIT_EQUAL
    captured = capsys.readouterr()
    assert f"{REPOSITORY}  granted" in captured.out
    assert bearer not in captured.out + captured.err
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/api/v1/members")]
    assert seen[0].headers["Authorization"] == f"Bearer {bearer}"
