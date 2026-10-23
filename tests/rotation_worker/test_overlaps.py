"""Which other rotations are mid-overlap (ADR-0055 amendment 12, "Switch timing")."""

from __future__ import annotations

from typing import Any, cast

from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.overlaps import mid_overlap

OWN = "own-revision"


class Ledger:
    """The status ledger and unit reads, as `mid_overlap` asks them."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.units: dict[str, dict[str, Any]] = {}

    def add(
        self,
        key: str,
        revision: str,
        adapter: str | None,
        segment: str,
        state: str = "ready",
        attempts: int = 0,
    ) -> None:
        constraints = {"rotation_adapter": adapter, "rotation_unit": segment} if adapter else {}
        self.units[key] = {
            "id": key,
            "work_package_revision_id": revision,
            "attempt_count": attempts,
            "authority": {"constraints": constraints},
        }
        self.rows.append({"unit_id": key, "unit_key": key, "unit_state": state})

    def active_units(self) -> list[dict[str, Any]]:
        return self.rows

    def unit(self, unit_id: str) -> dict[str, Any]:
        return self.units[unit_id]

    def mid_overlap(self) -> list[str]:
        return mid_overlap(cast(OrchestratorClient, self), OWN)


def test_a_retire_still_listed_after_its_mint_completed_is_mid_overlap() -> None:
    # The drift-reporter's shape on 2026-10-23: mint completed, retire ready.
    ledger = Ledger()
    ledger.add("drift-reporter-bearer-retire", "drift", "orchestrator", "retire")
    assert ledger.mid_overlap() == ["drift-reporter-bearer-retire (ready)"]


def test_a_rotation_whose_store_unit_was_never_claimed_has_stored_nothing() -> None:
    ledger = Ledger()
    ledger.add("drift-mint", "drift", "orchestrator", "mint")
    ledger.add("drift-retire", "drift", "orchestrator", "retire")
    assert ledger.mid_overlap() == []


def test_a_store_unit_once_claimed_may_have_stored() -> None:
    ledger = Ledger()
    ledger.add("drift-mint", "drift", "orchestrator", "mint", "failed", attempts=1)
    ledger.add("drift-retire", "drift", "orchestrator", "retire")
    assert ledger.mid_overlap() == ["drift-mint (failed)", "drift-retire (ready)"]


def test_a_rotation_waiting_on_a_person_s_revoke_is_mid_overlap() -> None:
    # The BWS plan: store and verify completed, confirm waits on the revoke.
    ledger = Ledger()
    ledger.add("ops-mini-confirm", "ops", "bws-machine-token", "confirm")
    assert ledger.mid_overlap() == ["ops-mini-confirm (ready)"]


def test_units_before_the_store_never_count() -> None:
    # The fake plan has a machine unit before the one holding store.
    ledger = Ledger()
    ledger.add("canary-mint", "canary", "fake", "mint", "failed", attempts=1)
    ledger.add("canary-store", "canary", "fake", "store", "claimed", attempts=1)
    assert ledger.mid_overlap() == ["canary-store (claimed)"]


def test_a_store_unit_claimed_alone_is_mid_overlap() -> None:
    ledger = Ledger()
    ledger.add("ops-mini-store", "ops", "bws-machine-token", "store", "claimed", attempts=1)
    assert ledger.mid_overlap() == ["ops-mini-store (claimed)"]


def test_each_rotation_is_judged_on_its_own_units() -> None:
    ledger = Ledger()
    ledger.add("read-mint", "read", "change-manager", "mint")
    ledger.add("read-retire", "read", "change-manager", "retire")
    ledger.add("drift-retire", "drift", "orchestrator", "retire")
    assert ledger.mid_overlap() == ["drift-retire (ready)"]


def test_the_unit_s_own_rotation_never_counts() -> None:
    ledger = Ledger()
    ledger.add("ops-mini-verify", OWN, "bws-machine-token", "verify")
    assert ledger.mid_overlap() == []


def test_a_unit_the_executor_can_t_place_always_counts() -> None:
    ledger = Ledger()
    ledger.add("unknown-adapter", "a", "retired-adapter", "retire")
    ledger.add("unknown-segment", "b", "orchestrator", "renamed")
    ledger.add("unknown-mint", "c", "retired-adapter", "mint")
    assert ledger.mid_overlap() == [
        "unknown-adapter (ready)",
        "unknown-mint (ready)",
        "unknown-segment (ready)",
    ]


def test_units_that_aren_t_rotations_are_ignored() -> None:
    ledger = Ledger()
    ledger.add("deps-bump", "deps", None, "")
    assert ledger.mid_overlap() == []
