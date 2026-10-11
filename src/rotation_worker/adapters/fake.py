"""The fake adapter: a provider that lives in the executor's own VM, for the canary credential.

ADR-0055 increment 4 builds the executor with no live credential, so its end-to-end run rotates a
canary whose "provider" is a file of sha256 hashes: a value is live while its hash is listed.
Nothing is stored but hashes, and a probe answers as an HTTP endpoint would (200 live, 401 not),
in-process, so the executor still listens on no port and reaches nothing (decision 1). A revoke
derives its id from the value itself, as amendment 1 has OpenRouter's do: the hash of the
quarantined value, never an id from evidence.

Its plan exercises every contract path: three units, one human act between the second and the
third (a person attests the `runtime-fetch` consumer holds the new value), a quarantine, a
probe with its control at each step that probes, and a retire that ends the overlap.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Final

from rotation_worker.contract import (
    Actor,
    ContractError,
    HumanAct,
    Plan,
    Step,
    StepKind,
    UnitPlan,
    validate_plan,
)
from rotation_worker.values import Value

NAME: Final = "fake"
PROBE_TARGET: Final = "fake-provider/key"
VALUE_BYTES: Final = 32

M, H = Actor.MACHINE, Actor.HUMAN
PLAN: Final = Plan(
    (
        UnitPlan(
            "mint",
            (
                Step(StepKind.MINT, M, "AC-001"),
                Step(StepKind.STAGE, M, "AC-002"),
                Step(StepKind.VERIFY_NEW, M, "AC-003"),
            ),
        ),
        UnitPlan(
            "store",
            (Step(StepKind.STORE, M, "AC-004"), Step(StepKind.VERIFY_CONSUMERS, M, "AC-005")),
        ),
        HumanAct("attest", (Step(StepKind.VERIFY_CONSUMERS, H),)),
        UnitPlan(
            "retire",
            (
                Step(StepKind.VERIFY_CONSUMERS, M, "AC-006"),
                Step(StepKind.RETIRE_OLD, M, "AC-007"),
                Step(StepKind.CONFIRM_DEAD, M, "AC-008"),
            ),
        ),
    )
)
validate_plan(PLAN)


def _hash(value: Value) -> str:
    return hashlib.sha256(value.reveal().encode()).hexdigest()


class FakeProvider:
    """Live values are the hashes listed in `state_path`, a file only the service user reads."""

    name = NAME
    probe_target = PROBE_TARGET
    dead_statuses = frozenset({401})

    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path

    def mint(self) -> Value:
        value = Value(secrets.token_urlsafe(VALUE_BYTES))
        self._save(self._live() | {_hash(value)})
        return value

    def probe(self, value: Value) -> int | None:
        return 200 if _hash(value) in self._live() else 401

    def known_bad(self) -> Value:
        return Value(secrets.token_urlsafe(VALUE_BYTES))

    def revoke(self, value: Value) -> None:
        live = self._live()
        key = _hash(value)
        if key not in live:
            raise ContractError("the provider holds no live value with that id")
        self._save(live - {key})

    def _live(self) -> frozenset[str]:
        if not self._state_path.exists():
            return frozenset()
        return frozenset(json.loads(self._state_path.read_text())["live"])

    def _save(self, live: frozenset[str]) -> None:
        partial = self._state_path.with_name(f"{self._state_path.name}.partial")
        partial.write_text(json.dumps({"live": sorted(live)}))
        os.chmod(partial, 0o600)
        partial.replace(self._state_path)
