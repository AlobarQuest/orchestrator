"""Which instance owns a unit: the orchestrator's rule, held in one place (rotation Part 1)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from rotation_worker import worker
from rotation_worker.envelope import KEYCHAIN_ADAPTERS
from rotation_worker.worker import Setup
from tests.rotation_worker.stores import MemoryStore
from tests.rotation_worker.test_keychain_instance import Recording


def _unit(adapter: str) -> dict[str, Any]:
    return {"id": "u1", "authority": {"constraints": {"rotation_adapter": adapter}}}


def test_the_copy_equals_the_orchestrators() -> None:
    from orchestrator.services.lifecycle.rotation_claims import (
        KEYCHAIN_ADAPTERS as SERVER,
    )

    assert KEYCHAIN_ADAPTERS == SERVER


def test_an_adapter_this_build_does_not_know_is_the_vms() -> None:
    unit = _unit("brand-new-adapter")
    assert worker._ours(cast(Setup, SimpleNamespace(store=MemoryStore())), unit) is True
    assert worker._ours(cast(Setup, SimpleNamespace(store=Recording())), unit) is False


def test_the_october_30_case_stays_quiet_on_the_keychain_instance(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Client:
        def unit(self, unit_id: str) -> dict[str, Any]:
            return _unit("brand-new-adapter")

        def package_intake(self, revision: str) -> dict[str, Any]:
            raise AssertionError("an intake was read")

        def claim(self, *arguments: Any) -> None:
            raise AssertionError("a claim was made")

    setup = cast(Setup, SimpleNamespace(store=Recording(), client=Client()))
    assert worker._take(setup, "u1", worker.PassActs()) == 0
    assert "unit_refused" not in caplog.text


def test_an_unknown_adapter_on_the_vm_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    refused: list[str] = []

    class Client:
        def unit(self, unit_id: str) -> dict[str, Any]:
            return {**_unit("brand-new-adapter"), "work_package_revision_id": "r1"}

        def package_intake(self, revision: str) -> dict[str, Any]:
            return {}

    def check(*arguments: Any) -> None:
        raise worker.ContractError("unknown adapter")

    monkeypatch.setattr(worker, "check_unit", check)
    monkeypatch.setattr(worker, "_refuse", lambda s, u, reason: refused.append(reason))
    setup = cast(
        Setup,
        SimpleNamespace(store=MemoryStore(), client=Client(), mirror=None, work_project_id="p"),
    )
    assert worker._take(setup, "u1", worker.PassActs()) == 0
    assert refused == ["unknown adapter"]
