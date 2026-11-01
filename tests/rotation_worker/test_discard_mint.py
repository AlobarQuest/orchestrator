"""A mint's failure removes its key: nothing else can find it, because evidence may not hold the id.

Asserted at both ends: the contract asks the provider to discard only what this process minted and
nobody holds, and OpenRouter deletes only the id its own mint returned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rotation_worker.adapters import openrouter as adapter
from rotation_worker.adapters.fake import FakeProvider
from rotation_worker.contract import ContractError
from rotation_worker.evidence import StepEvidence
from rotation_worker.secret_store import StoreError
from rotation_worker.values import Value
from tests.rotation_worker.stores import MemoryStore
from tests.rotation_worker.test_contract import WORK, Harness
from tests.rotation_worker.test_openrouter import Rig


class Discarding(FakeProvider):
    """A provider that mints remotely, so what it minted can be left behind."""

    def __init__(self, state_path: Path) -> None:
        super().__init__(state_path)
        self.discarded: list[Value] = []
        self.refuse = False

    def discard_minted(self, value: Value) -> None:
        if self.refuse:
            raise ContractError("deleting the minted key answered 500")
        self.discarded.append(value)


class FailingStore(MemoryStore):
    """A store whose next `create` raises, having written first when `lands`."""

    fail = False
    lands = False
    # The store goes dark with the failed write, so nothing can read what it holds afterwards.
    goes_dark = False
    dark = False

    def create(self, project_id: str, key: str, value: Value) -> Any:
        if not self.fail:
            return super().create(project_id, key, value)
        if self.lands:
            super().create(project_id, key, value)
        self.dark = self.goes_dark
        raise StoreError("the store refused the write")

    def find(self, project_id: str, key: str) -> Any:
        if self.dark:
            raise StoreError("the store can't be read")
        return super().find(project_id, key)


def _harness(tmp_path: Path, provider: FakeProvider) -> Any:
    harness = Harness(tmp_path)
    store = FailingStore()
    store.secrets = harness.store.secrets
    store.revisions = harness.store.revisions
    harness.store = store
    harness.provider = provider
    return harness


@pytest.fixture
def h(tmp_path: Path) -> Any:
    return _harness(tmp_path, Discarding(tmp_path / "provider.json"))


def _record_failing_on(h: Any, step_kind: str):
    def record(step: Any, evidence: StepEvidence) -> None:
        if evidence.step == step_kind:
            raise StoreError("the evidence write failed")
        h.filed.append((step, evidence))

    return record


def test_a_failed_stage_deletes_the_minted_key(h: Any) -> None:
    h.store.fail = True
    with pytest.raises(StoreError, match="refused the write"):
        h.run("mint")
    assert len(h.provider.discarded) == 1
    assert h.filed[0][1].step == "mint"
    assert h.provider.discarded[0].fingerprint == h.filed[0][1].fingerprints[1][1]


def test_a_failed_evidence_write_deletes_it(h: Any) -> None:
    with pytest.raises(StoreError, match="evidence write"):
        h.run("mint", record=_record_failing_on(h, "mint"))
    assert len(h.provider.discarded) == 1


def test_a_failed_check_between_mint_and_stage_deletes_it(h: Any) -> None:
    """The check that runs before every step, which is outside both steps' handlers."""
    calls: list[int] = []

    def check() -> None:
        calls.append(1)
        if len(calls) == 2:
            raise ContractError("the registry took the credential back")

    with pytest.raises(ContractError, match="took the credential back"):
        h.run("mint", before_step=check)
    assert len(h.provider.discarded) == 1


def test_a_failure_after_the_stage_reads_nothing_and_says_nothing_of_a_key(h: Any) -> None:
    """A store outage later in the unit must not report an orphan that doesn't exist."""

    def record(step: Any, evidence: StepEvidence) -> None:
        if evidence.step == "stage":
            h.store.dark = True
            raise StoreError("the evidence write failed")
        h.filed.append((step, evidence))

    with pytest.raises(StoreError, match="evidence write"):
        h.run("mint", record=record)
    assert h.provider.discarded == []


def test_a_failed_delete_reports_both(h: Any) -> None:
    h.store.fail = True
    h.provider.refuse = True
    with pytest.raises(ContractError) as caught:
        h.run("mint")
    message = str(caught.value)
    assert "refused the write" in message
    assert "answered 500" in message
    assert "a key may be left" in message


def test_a_staged_key_is_never_deleted(h: Any) -> None:
    with pytest.raises(StoreError):
        h.run("mint", record=_record_failing_on(h, "stage"))
    assert h.provider.discarded == []


def test_a_write_that_landed_before_it_raised_is_never_deleted(h: Any) -> None:
    h.store.fail = h.store.lands = True
    with pytest.raises(StoreError):
        h.run("mint")
    assert h.provider.discarded == []


def test_a_store_that_cant_say_what_it_holds_leaves_the_key_and_says_so(h: Any) -> None:
    """Deleting a value that may be staged could strand the rotation on a dead key; an orphan is
    bounded by its credit limit and expiry."""
    h.store.fail = h.store.goes_dark = True
    with pytest.raises(ContractError, match="a key may be left"):
        h.run("mint")
    assert h.provider.discarded == []


def test_a_different_value_already_staged_deletes_only_the_new_mint(h: Any) -> None:
    """Someone staged between the mint and the stage: what this process minted is held by nobody."""
    held = Value("a-value-someone-else-staged")

    def record(step: Any, evidence: StepEvidence) -> None:
        h.filed.append((step, evidence))
        if evidence.step == "mint":
            h.store.put(WORK, h.rotation.staging_key, held)

    with pytest.raises(ContractError, match="different value"):
        h.run("mint", record=record)
    (discarded,) = h.provider.discarded
    assert not discarded.same_as(held)


def test_a_mint_equal_to_the_old_value_is_never_discarded(h: Any, monkeypatch) -> None:
    monkeypatch.setattr(h.provider, "mint", lambda: h.original)
    with pytest.raises(ContractError, match="equals the old one"):
        h.run("mint")
    assert h.provider.discarded == []


def test_a_later_attempt_that_minted_nothing_discards_nothing(h: Any) -> None:
    """Staged by an earlier attempt: this process minted nothing, so a failure deletes nothing."""
    h.run("mint")
    h.filed.clear()
    with pytest.raises(StoreError):
        h.run("mint", record=_record_failing_on(h, "mint"))
    assert h.provider.discarded == []


def test_a_provider_with_no_discard_is_unchanged(tmp_path: Path) -> None:
    h = _harness(tmp_path, FakeProvider(tmp_path / "provider.json"))
    h.store.fail = True
    with pytest.raises(StoreError, match="refused the write"):
        h.run("mint")


def _deletes(rig: Rig) -> list[str]:
    return [path for method, path, _ in rig.server.requests if method == "DELETE"]


def test_only_the_minted_keys_derived_id_is_deleted() -> None:
    rig = Rig()
    other = rig.server.add()
    value = rig.provider.mint()
    assert rig.server.live(value)

    rig.provider.discard_minted(value)

    (path,) = _deletes(rig)
    assert path.endswith(adapter._key_hash(value))
    assert not rig.server.live(value)
    assert rig.server.live(other)


def test_a_value_this_instance_did_not_mint_is_refused() -> None:
    rig = Rig()
    other = rig.server.add()
    with pytest.raises(ContractError, match="did not mint"):
        rig.provider.discard_minted(other)
    assert _deletes(rig) == [] and rig.server.live(other)


def test_a_key_is_discarded_once() -> None:
    rig = Rig()
    value = rig.provider.mint()
    rig.provider.discard_minted(value)
    with pytest.raises(ContractError, match="did not mint"):
        rig.provider.discard_minted(value)
    assert len(_deletes(rig)) == 1


def test_a_delete_the_provider_refuses_is_a_refusal() -> None:
    rig = Rig()
    value = rig.provider.mint()
    rig.server.delete_status = 500
    with pytest.raises(ContractError, match="answered 500") as caught:
        rig.provider.discard_minted(value)
    assert adapter._key_hash(value) not in str(caught.value)
