"""One pass of the executor: find ready rotation units, check each, and work the ones that pass.

For each ready unit whose envelope names a rotation adapter, in order:

1. **Check before claiming** (`checks.check_unit`), from git, with no side effect. A transient
   failure (git or the network) leaves the unit for the next pass. A refusal is a finding about
   the unit itself, so the executor claims it and fails it with the reason, where the dead-letter
   report shows it to a person.
2. **Claim and start**, then run the unit's steps through the contract. Before each step the lease
   is renewed and the registry is read again, so a credential handed back mid-rotation stops it.
3. **File each step's evidence**, superseding this unit's current row for that criterion when an
   earlier attempt left one, then **submit**. A contract refusal fails the unit with its reason.

Nothing here decides what a step does; the contract does. Nothing here holds a value; the
contract's unit run does, for the length of the step that needs it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from rotation_worker.checks import Approved, check_unit, is_rotation_unit
from rotation_worker.contract import ContractError, Step, StepKind, UnitRun
from rotation_worker.destinations import DestinationError
from rotation_worker.evidence import StepEvidence
from rotation_worker.git_mirror import MirrorError, RepoMirror
from rotation_worker.orchestrator_client import Lease, OrchestratorClient, OrchestratorError
from rotation_worker.registry import RegistryError, find_credential
from rotation_worker.secret_store import SecretStore, StoreError
from rotation_worker.values import Fingerprint, scrub

LOG = logging.getLogger("rotation_worker")
EVIDENCE_TYPE: Final = "rotation.step"
MAX_REASON: Final = 400
# A refusal: the unit itself is wrong, and a person must see it.
REFUSALS: Final = (ContractError, RegistryError, DestinationError)
# Nothing could be asked: try again next pass.
TRANSIENT: Final = (MirrorError, OrchestratorError, StoreError)


@dataclass(frozen=True)
class Setup:
    client: OrchestratorClient
    mirror: RepoMirror
    store: SecretStore
    work_project_id: str
    state_dir: Path
    # The executor's own commit, short: a full SHA is a 32-character run the evidence scan refuses.
    source_revision: str


def run_pass(setup: Setup) -> int:
    """Work every ready rotation unit once. Returns how many it submitted."""
    submitted = 0
    for unit_id in setup.client.ready_units():
        try:
            submitted += _take(setup, unit_id)
        except TRANSIENT as error:
            # Nothing was decided about the unit: a claim refused (a shut window, a version that
            # moved), or a service that didn't answer. The next pass asks again.
            LOG.warning("unit_skipped", extra={"unit": unit_id, "reason": scrub(str(error))})
        except Exception as error:
            # Something no handler foresaw, before or after the claim: logged, and the pass goes
            # on to the next unit rather than leaving every one behind it unworked.
            reason = scrub(f"{type(error).__name__}: {error}")
            LOG.error("unit_not_worked", extra={"unit": unit_id, "reason": reason})
    return submitted


def _take(setup: Setup, unit_id: str) -> int:
    unit = setup.client.unit(unit_id)
    if not is_rotation_unit(unit):
        return 0
    intake = setup.client.package_intake(str(unit["work_package_revision_id"]))
    try:
        approved = check_unit(unit, intake, setup.mirror, setup.work_project_id)
    except REFUSALS as error:
        _refuse(setup, unit, scrub(str(error)))
        return 0
    return 1 if _work(setup, unit, approved) else 0


def _refuse(setup: Setup, unit: Mapping[str, Any], reason: str) -> None:
    unit_id = str(unit["id"])
    LOG.error("unit_refused", extra={"unit": unit_id, "reason": reason})
    lease, version = _claim(setup, unit)
    setup.client.fail(unit_id, version, lease, _key(unit_id, lease, "fail"), reason[:MAX_REASON])


def _claim(setup: Setup, unit: Mapping[str, Any]) -> tuple[Lease, int]:
    unit_id, version = str(unit["id"]), int(unit["version"])
    lease = setup.client.claim(unit_id, version, f"rotation-worker:{unit_id}:claim:{version}")
    # A claim is a transition, so it moves the version on by one.
    return lease, version + 1


def _work(setup: Setup, unit: Mapping[str, Any], approved: Approved) -> bool:
    unit_id = str(unit["id"])
    lease, version = _claim(setup, unit)
    # From here the unit is this attempt's: whatever goes wrong fails it with a reason, so it
    # never waits out its lease holding a claim nobody works.
    try:
        version = setup.client.start(unit_id, version, lease, _key(unit_id, lease, "start"))
        kinds, criteria = _filed(setup.client.evidence(unit_id), approved)
        recorder = _Recorder(setup, unit, lease, version, criteria)
        run = UnitRun(
            rotation=approved.rotation,
            store=setup.store,
            provider=approved.adapter.build(setup.state_dir),
            attestations=_attestations(setup.client.history(unit_id)),
            record=recorder.record,
            before_step=lambda: _before_step(setup, recorder, approved),
            filed=kinds,
        )
        run.run(approved.unit)
    except Exception as error:
        reason = scrub(f"{type(error).__name__}: {error}")
        LOG.error("unit_failed", extra={"unit": unit_id, "reason": reason})
        setup.client.fail(
            unit_id, version, lease, _key(unit_id, lease, "fail"), reason[:MAX_REASON]
        )
        return False
    setup.client.submit(unit_id, version, lease, _key(unit_id, lease, "submit"))
    LOG.info("unit_submitted", extra={"unit": unit_id, "steps": len(approved.unit.steps)})
    return True


def _before_step(setup: Setup, recorder: _Recorder, approved: Approved) -> None:
    recorder.renew()
    entry = find_credential(setup.mirror, approved.rotation.credential_id)
    if not entry.rotated_by_sds:
        raise ContractError(f"{approved.rotation.credential_id} was handed back mid-rotation")


class _Recorder:
    """Files each step's evidence on this attempt, superseding where a row already stands."""

    def __init__(
        self,
        setup: Setup,
        unit: Mapping[str, Any],
        lease: Lease,
        version: int,
        filed: frozenset[str],
    ) -> None:
        self._setup = setup
        self._unit_id = str(unit["id"])
        self._revision_id = str(unit["work_package_revision_id"])
        self._lease = lease
        self._version = version
        self._current = set(filed)
        self._sequence = 0

    def record(self, step: Step, evidence: StepEvidence) -> None:
        if step.ac_id is None:
            raise ContractError("a human step files no evidence")
        self._sequence += 1
        body = {
            "idempotency_key": _key(self._unit_id, self._lease, f"evidence:{self._sequence}"),
            "expected_version": self._version,
            "work_package_revision_id": self._revision_id,
            "ac_id": step.ac_id,
            "attempt": self._lease.attempt,
            "lease_token": self._lease.token,
            "evidence_type": EVIDENCE_TYPE,
            "payload": evidence.as_payload(),
            "source_revision": self._setup.source_revision,
            "supersede": step.ac_id in self._current,
        }
        self._setup.client.file_evidence(self._unit_id, body)
        self._current.add(step.ac_id)
        LOG.info("step_filed", extra={"unit": self._unit_id, "step": evidence.step})

    def renew(self) -> None:
        self._sequence += 1
        key = _key(self._unit_id, self._lease, f"renew:{self._sequence}")
        self._setup.client.renew(self._unit_id, self._version, self._lease, key)


def _filed(
    rows: list[dict[str, Any]], approved: Approved
) -> tuple[frozenset[StepKind], frozenset[str]]:
    """This unit's criteria that already hold evidence, and the step kinds they belong to."""
    by_ac = {step.ac_id: step.kind for step in approved.unit.steps}
    criteria = frozenset(str(row["ac_id"]) for row in rows if row.get("ac_id") in by_ac)
    return frozenset(by_ac[ac] for ac in criteria), criteria


def _attestations(events: list[dict[str, Any]]) -> list[Fingerprint]:
    """The fingerprints people recorded resolving this unit's human acts as satisfied."""
    found = []
    for event in events:
        if event.get("action") != "dependency.resolved":
            continue
        command = (event.get("payload") or {}).get("command") or {}
        print_ = (command.get("detail") or {}).get("fingerprint")
        if command.get("status") == "satisfied" and isinstance(print_, dict):
            prefix, length = print_.get("sha256_prefix"), print_.get("length")
            if isinstance(prefix, str) and isinstance(length, int):
                found.append(Fingerprint(prefix, length))
    return found


def _key(unit_id: str, lease: Lease, what: str) -> str:
    return f"rotation-worker:{unit_id}:{lease.attempt}:{what}"
