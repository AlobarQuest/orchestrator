"""The `bind` command's own entry point: its credential, its refusals, its exit codes.

Exercised through the CLI rather than only through `bind_checkout`, because a Typer command's
options, its credential check and its exit code are not reachable from the function beneath it --
and this program is invoked by a launcher script that reads nothing but the exit code.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from activation_sweep.bind import BOUND, RECORDED, WAITING
from activation_sweep.binding_client import BindingCallError
from activation_sweep.cli import (
    BINDING_TOKEN_VARIABLE,
    EXIT_INCOMPLETE,
    EXIT_OK,
    TOKEN_VARIABLE,
    app,
)
from tests.activation_sweep.conftest import Estate, git

runner = CliRunner()
UNIT_ID = "eb7c36f7-4f7e-5d00-9709-779c0c1152a4"


class Binder:
    def __init__(self, rows: list[dict[str, Any]], error: Exception | None = None) -> None:
        self.rows = rows
        self.error = error
        self.bound: list[dict[str, Any]] = []
        self.opened: dict[str, Any] = {}

    def candidates(self, repository: str) -> list[dict[str, Any]]:
        return list(self.rows)

    def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.bound.append(payload)
        return {"id": "binding-1"}


def _install(monkeypatch: pytest.MonkeyPatch, binder: Binder) -> Binder:
    seen: dict[str, Any] = {}

    class Client:
        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_: object) -> None: ...

        def candidates(self, repository: str) -> list[dict[str, Any]]:
            return binder.candidates(repository)

        def bind(self, work_unit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            return binder.bind(work_unit_id, payload)

    def opened(**kwargs: Any) -> Client:
        seen.update(kwargs)
        return Client()

    monkeypatch.setattr("activation_sweep.cli.open_binding_client", opened)
    monkeypatch.setenv(BINDING_TOKEN_VARIABLE, "system-bearer-stand-in")
    binder.opened = seen
    return binder


def _row(commit: str, *, binding_id: str | None = None) -> dict[str, Any]:
    return {
        "work_unit_id": UNIT_ID,
        "work_package_revision_id": "11111111-2222-3333-4444-555555555555",
        "package_revision_hash": "sha256:package",
        "unit_key": "example-ac-001",
        "work_unit_version": 3,
        "source_repository": "AlobarQuest/example",
        "pr_number": 81,
        "source_commit": "f" * 40,
        "merge_commit": commit,
        "binding_id": binding_id,
    }


def _invoke(*args: str) -> Any:
    return runner.invoke(app, ["bind", "--no-fetch", *args])


def test_binding_an_activated_unit_exits_zero(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = git(estate.local, "rev-parse", "HEAD").strip()
    binder = _install(monkeypatch, Binder([_row(head)]))

    result = _invoke("--checkout", str(estate.local))

    assert result.exit_code == EXIT_OK
    assert json.loads(result.stdout)[0]["units"][0]["outcome"] == RECORDED
    assert len(binder.bound) == 1


def test_a_unit_still_waiting_exits_zero(estate: Estate, monkeypatch: pytest.MonkeyPatch) -> None:
    """WAITING is an answer, not a finding: the pass is complete and the machine is not there."""
    upstream = estate.land_upstream()
    binder = _install(monkeypatch, Binder([_row(upstream)]))

    result = _invoke("--checkout", str(estate.local))

    assert result.exit_code == EXIT_OK
    assert json.loads(result.stdout)[0]["units"][0]["outcome"] == WAITING
    assert binder.bound == []


def test_an_already_bound_unit_exits_zero(estate: Estate, monkeypatch: pytest.MonkeyPatch) -> None:
    head = git(estate.local, "rev-parse", "HEAD").strip()
    binder = _install(monkeypatch, Binder([_row(head, binding_id="already-there")]))

    result = _invoke("--checkout", str(estate.local))

    assert result.exit_code == EXIT_OK
    assert json.loads(result.stdout)[0]["units"][0]["outcome"] == BOUND
    assert binder.bound == []


def test_a_refused_binding_exits_incomplete(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = git(estate.local, "rev-parse", "HEAD").strip()
    _install(monkeypatch, Binder([_row(head)], error=BindingCallError("rejected: 409")))

    result = _invoke("--checkout", str(estate.local))

    assert result.exit_code == EXIT_INCOMPLETE


def test_one_broken_checkout_costs_only_itself(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-checkout isolation: a pass that died on the first would discard the second."""
    head = git(estate.local, "rev-parse", "HEAD").strip()
    binder = _install(monkeypatch, Binder([_row(head)]))

    result = _invoke("--checkout", "/nonexistent/checkout", "--checkout", str(estate.local))

    assert result.exit_code == EXIT_INCOMPLETE
    summaries = json.loads(result.stdout)
    assert summaries[0]["unavailable"] is True
    assert summaries[1]["units"][0]["outcome"] == RECORDED
    assert len(binder.bound) == 1


def test_a_dry_run_writes_nothing_and_shows_what_would_be_bound(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run to make FIRST: the units about to be bound are seen before anything permanent."""
    head = git(estate.local, "rev-parse", "HEAD").strip()
    binder = _install(monkeypatch, Binder([_row(head)]))

    result = _invoke("--checkout", str(estate.local), "--dry-run")

    assert result.exit_code == EXIT_OK
    assert binder.bound == []
    unit = json.loads(result.stdout)[0]["units"][0]
    assert unit["dry_run"] is True
    assert unit["record"]["kind"] == "machine_local"


def test_the_lane_refuses_to_run_without_its_own_credential(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ITS OWN, and the sweep's is not a substitute.

    One ambient bearer serving two identities is the failure this estate has already paid for in
    the launchers and in `factory decompose`. Binding needs SYSTEM; the sweep beside it is
    OBSERVER, and an OBSERVER bearer here would 403 at the orchestrator after the pass had already
    measured everything.
    """
    monkeypatch.delenv(BINDING_TOKEN_VARIABLE, raising=False)
    monkeypatch.setenv(TOKEN_VARIABLE, "the-observer-bearer")

    result = _invoke("--checkout", str(estate.local))

    assert result.exit_code == 1
    assert BINDING_TOKEN_VARIABLE in result.output


def test_an_unusable_orchestrator_url_is_the_tool_failing(
    estate: Estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 1, not the incomplete code: one typo makes the tool unusable for every checkout."""
    monkeypatch.setenv(BINDING_TOKEN_VARIABLE, "system-bearer-stand-in")

    result = _invoke("--checkout", str(estate.local), "--orchestrator-url", "https://host..example")

    assert result.exit_code == 1
