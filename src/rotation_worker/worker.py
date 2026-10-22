"""One pass of the executor: find ready rotation units, check each, and work the ones that pass.

For each ready unit whose envelope names a rotation adapter, in order:

1. **Check before claiming** (`checks.check_unit`), from git, with no side effect. A transient
   failure (git or the network) leaves the unit for the next pass. A refusal is a finding about
   the unit itself, so the executor claims it and fails it with the reason, where the dead-letter
   report shows it to a person.
2. **Wait, unclaimed, for the scheduled readers** (`readers.py`). Each one is read before any
   unit that verifies consumers is claimed, so a reader the registry names wrongly is refused
   here. A unit that retires the old value stays ready, spending nothing, until every reader has
   run since the keeper was written: some run weekly, which no lease could wait out. Each pass
   logs who it waits for and when the overlap's backstop ends; if the backstop comes first, the
   overlap ends there and retire finds the old value already refused.
   A unit that restarts a service whose own clients are scheduled readers (change-manager) also
   waits, unclaimed, while any of them is mid-run, so a busy reader costs a pass, not an attempt.
3. **Claim and start**, then run the unit's steps through the contract. Before each step the lease
   is renewed and the registry is read again, so a credential handed back mid-rotation stops it.
4. **File each step's evidence**, superseding this unit's current row for that criterion when an
   earlier attempt left one, then **submit**. A contract refusal fails the unit with its reason.

Nothing here decides what a step does; the contract does. Nothing here holds a value; the
contract's unit run does, for the length of the step that needs it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from rotation_worker.adapters import ADAPTERS, AdapterContext
from rotation_worker.adapters.orchestrator_bearer import NAME as ORCHESTRATOR_PROVIDER
from rotation_worker.checks import WRITING_STEPS, Approved, check_unit
from rotation_worker.config import ConfigError
from rotation_worker.contract import (
    HELD_KIND,
    ContractError,
    HumanAct,
    Provider,
    Step,
    StepKind,
    UnitRun,
)
from rotation_worker.coolify import CoolifyClient
from rotation_worker.destinations import DestinationError, parse
from rotation_worker.envelope import ADAPTER_KEY, is_rotation_unit
from rotation_worker.envelope import constraints as envelope_constraints
from rotation_worker.evidence import StepEvidence
from rotation_worker.git_mirror import MirrorError, RepoMirror
from rotation_worker.github_actions import ActionsSecrets, FingerprintReports, RotationApp
from rotation_worker.held import HeldSettings
from rotation_worker.orchestrator_client import Lease, OrchestratorClient, OrchestratorError
from rotation_worker.readers import (
    ConsumerReads,
    Healthchecks,
    Read,
    ReadError,
    ReadUnavailable,
)
from rotation_worker.registry import RegistryError, find_credential
from rotation_worker.secret_store import SecretStore, StoreError
from rotation_worker.values import Fingerprint, scrub

LOG = logging.getLogger("rotation_worker")
EVIDENCE_TYPE: Final = "rotation.step"
MAX_REASON: Final = 400
# A refusal: the unit itself is wrong, and a person must see it.
REFUSALS: Final = (ContractError, RegistryError, DestinationError, ReadError)
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
    # Each provider's admin secret id (`Config.admin_secrets`).
    admin_secrets: Mapping[str, str] = field(default_factory=dict)
    orchestrator_url: str | None = None
    coolify_url: str | None = None
    orchestrator_application: str | None = None
    change_manager_application: str | None = None
    # The key id the executor's own bearer is registered under.
    executor_key_id: str | None = None
    # The Healthchecks listing, read with the key in `Rotation / Healthchecks`.
    healthchecks: Healthchecks | None = None
    # The rotation GitHub App, which writes Actions secrets and reads their reports.
    github_app: RotationApp | None = None


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
    if not is_rotation_unit(unit) or not _ours(setup, unit):
        return 0
    intake = setup.client.package_intake(str(unit["work_package_revision_id"]))
    reads: ConsumerReads | None = None
    try:
        approved = check_unit(unit, intake, setup.mirror, setup.work_project_id, setup.store)
        held = _held(setup, approved)
        if approved.reads and _verifies(approved):
            reads = _consumer_reads(setup, approved)
            waiting = reads.pending(setup.store.revised(approved.rotation.keeper_id))
            if waiting and _gates_retire(approved):
                LOG.info(
                    "unit_waiting",
                    extra={
                        "unit": str(unit["id"]),
                        "readers": waiting,
                        "overlap_until": _overlap_until(setup, approved),
                    },
                )
                return 0
    except REFUSALS as error:
        _refuse(setup, unit, scrub(str(error)))
        return 0
    if _adapter_waits(setup, approved, unit_id):
        return 0
    # Built before the claim: a provider that can't be built (an admin secret missing from the
    # configuration) is the executor's fault, not the unit's, so no attempt is spent on it.
    provider = _provider(setup, approved)
    if isinstance(provider, GatedRestarts) and _writes(approved):
        try:
            busy = provider.readers_mid_run()
        except ReadError as error:
            _refuse(setup, unit, scrub(str(error)))
            return 0
        except ReadUnavailable as error:
            busy = [f"unreadable: {scrub(str(error))}"]
        if busy:
            LOG.info("unit_waiting", extra={"unit": str(unit["id"]), "readers_mid_run": busy})
            return 0
    return 1 if _work(setup, unit, approved, provider, reads, held) else 0


def _ours(setup: Setup, unit: Mapping[str, Any]) -> bool:
    """Whether this instance's store keeps the unit's credentials (ADR-0055 amendment 12).

    The other instance's units are left alone, unclaimed: the orchestrator would refuse this
    instance's claim on them anyway, and failing them would spend their attempts. An adapter no
    instance knows is the checks' to refuse, so it counts as ours.
    """
    adapter = ADAPTERS.get(str(envelope_constraints(unit).get(ADAPTER_KEY)))
    return adapter is None or adapter.store_kind == setup.store.read_back_kind


def _now() -> datetime:
    return datetime.now(UTC)


@runtime_checkable
class GatedRestarts(Protocol):
    """A provider whose writes would cut short something running: change-manager's readers during
    its restart, or a factory run holding a PAT the GitHub PAT adapter is about to revoke."""

    def readers_mid_run(self) -> list[str]: ...


def _writes(approved: Approved) -> bool:
    return any(step.kind in WRITING_STEPS for step in approved.unit.steps)


def _provider(setup: Setup, approved: Approved) -> Provider:
    return approved.adapter.build(
        AdapterContext(
            setup.state_dir,
            setup.store,
            setup.admin_secrets.get(approved.adapter.provider_name),
            rotation=approved.rotation,
            orchestrator=setup.client,
            orchestrator_url=setup.orchestrator_url,
            coolify_url=setup.coolify_url,
            orchestrator_application=setup.orchestrator_application,
            executor_key_id=setup.executor_key_id,
            change_manager_application=setup.change_manager_application,
            mirror=setup.mirror,
            healthchecks=setup.healthchecks,
            host_without_overlap=approved.host_without_overlap,
            credential=approved.registry.credential,
        )
    )


def _held_destinations(approved: Approved) -> list[dict[str, Any]]:
    return [c for c in map(parse, approved.rotation.destinations) if c["kind"] == HELD_KIND]


def _held(setup: Setup, approved: Approved) -> HeldSettings | None:
    """The writer for the unit's held copies, checked against each one; `None` when it has none.

    It reaches the orchestrator's application through production Coolify with the orchestrator
    adapter's token. A configuration without those is the executor's fault, not the unit's, so it
    raises `ConfigError`, which no attempt is spent on.
    """
    destinations = _held_destinations(approved)
    if not destinations:
        return None
    token_id = setup.admin_secrets.get(ORCHESTRATOR_PROVIDER)
    coolify_url, application, store = setup.coolify_url, setup.orchestrator_application, setup.store
    if token_id is None or not coolify_url or application is None:
        raise ConfigError("a held copy is approved and this executor can't write one")
    held = HeldSettings(
        setup.client,
        lambda app: CoolifyClient(coolify_url, store.read(token_id), app),
        application,
    )
    for consumer in destinations:
        held.check(consumer)
    return held


def _adapter_waits(setup: Setup, approved: Approved, unit_id: str) -> bool:
    """Whether the adapter says the unit can't be claimed yet; the reason is logged."""
    wait = approved.adapter.wait
    reason = wait(approved.unit, setup.client.history(unit_id), _now()) if wait else None
    if reason is not None:
        LOG.info("unit_waiting", extra={"unit": unit_id, "reason": reason})
    return reason is not None


def _verifies(approved: Approved) -> bool:
    return any(step.kind is StepKind.VERIFY_CONSUMERS for step in approved.unit.steps)


def _gates_retire(approved: Approved) -> bool:
    """Whether the old value is retired in this unit, or by the person acting straight after it.

    Either way every consumer must have switched first, and a claimed unit can't wait, so the
    unit waits unclaimed (the Bitbucket plan's console revoke follows its `verify` unit).
    """
    if any(step.kind is StepKind.RETIRE_OLD for step in approved.unit.steps):
        return True
    return _person_revokes_next(approved)


def _person_revokes_next(approved: Approved) -> bool:
    """Whether the segment straight after this unit is a person retiring the old value."""
    segments = approved.adapter.plan.segments
    following = segments[segments.index(approved.unit) + 1 :][:1]
    return any(
        isinstance(segment, HumanAct)
        and any(step.kind is StepKind.RETIRE_OLD for step in segment.steps)
        for segment in following
    )


def _consumer_reads(setup: Setup, approved: Approved) -> ConsumerReads:
    return consumer_reads(setup, approved.reads, approved.rotation.keeper_id)


def consumer_reads(setup: Setup, reads: Mapping[str, Read], keeper: str) -> ConsumerReads:
    """One rotation's reads for one pass: a scheduled reader's, and an Actions copy's report,
    compared with what the keeper holds."""
    return ConsumerReads(
        reads,
        setup.healthchecks,
        setup.client,
        reports=FingerprintReports(setup.github_app) if setup.github_app else None,
        keeper=lambda: setup.store.read(keeper).fingerprint,
    )


def _overlap_until(setup: Setup, approved: Approved) -> str | None:
    """When the orchestrator stops accepting the old value unasked, if this rotation hosts one."""
    consumers = [parse(d) for d in approved.rotation.destinations]
    hosted = [c for c in consumers if c["kind"] == "coolify-env-hash"]
    if len(hosted) != 1:
        return None
    try:
        entry = setup.client.identities().get(hosted[0]["entry"]) or {}
    except OrchestratorError:
        return None
    previous = entry.get("previous")
    return str(previous.get("until")) if isinstance(previous, Mapping) else None


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


def _work(
    setup: Setup,
    unit: Mapping[str, Any],
    approved: Approved,
    provider: Provider,
    reads: ConsumerReads | None,
    held: HeldSettings | None = None,
) -> bool:
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
            provider=provider,
            attestations=_attestations(setup.client.history(unit_id)),
            record=recorder.record,
            before_step=lambda: _before_step(setup, recorder, approved),
            filed=kinds,
            scheduled=reads,
            actions_secrets=ActionsSecrets(setup.github_app) if setup.github_app else None,
            person_revokes_next=_person_revokes_next(approved),
            held=held,
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
