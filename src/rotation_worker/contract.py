"""The rotation contract (ADR-0055 decision 3): every adapter's steps, and the invariants they keep.

**AN ADAPTER DECLARES; THE CONTRACT ACTS.** An adapter is a `Provider` (how to mint, probe and
revoke one kind of credential) and a `Plan`: its steps in order, each `machine` or `human`,
grouped into the units its decomposition holds, with every human step a human act between units
(a dependency Devon resolves at `/review`, never a unit). The nine step kinds are implemented
here, once, so the invariants hold for every adapter rather than being re-written by each:

* **Retire-old is reachable only after every registered consumer passes verify-consumers**, checked
  again at retire time by re-reading each one, never by trusting earlier evidence; and only
  confirm-dead may follow it. A scheduled `runtime-fetch` reader passes once it has run since the
  keeper was written (`readers.py`); any other is attested by a person.
* **New is never old.** Every step after mint refuses if the new value's fingerprint equals the
  old one's.
* **The old value is quarantined before the keeper changes**, and a keeper that already holds the
  new value with no quarantine is refused rather than guessed about.
* **Every probe runs beside a known-bad control in the same step**, and its answer counts only if
  the control was refused. Live is exactly 200; dead is exactly the provider's dead statuses;
  anything else stops the rotation.
* **After confirm-dead the quarantine and staging secrets are deleted**, and the deletion is
  filed as evidence.
* **A provider that is a hosted service learns the new value before anything probes it.** A
  self-issued bearer works only once the service accepts it, so its plan has a `host` step
  between stage and verify_new (ADR-0055 amendment 9). Host writes only the service, never BWS,
  and the service keeps accepting the old value until retire_old, or until the overlap's
  backstop passes, so nothing unproved reaches the keeper and no consumer loses its value while
  the rotation moves. A rotation stalled between host and store must be finished or undone
  before that backstop (the runbook's "If a rotation stalls after host").

**STATE IS RE-DERIVED, NEVER TRUSTED (decision 1).** Where a rotation stands is read from BWS
(the keeper, and the staging and quarantine secrets named for this credential and occurrence) and
from probes. So every step is safe to run again: a step whose result is already in place
re-proves it and files its evidence, and a state no step could have left is refused.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final, Protocol

from rotation_worker.destinations import HOSTED_KINDS, parse
from rotation_worker.evidence import ProbeAnswer, State, StepEvidence, Subject, Which
from rotation_worker.secret_store import SecretStore, StoredSecret
from rotation_worker.values import Fingerprint, Value

LIVE_STATUS: Final = 200
# Destination kinds a machine verifies by reading BWS back, and kinds only a person can attest
# (ADR-0055's verification table: a `runtime-fetch` run only when invoked, an Actions secret, and
# the operator machine's own files have no probe the executor can run).
READ_BACK_KINDS: Final = frozenset({"bws-secret"})
ATTESTED_KINDS: Final = frozenset(
    {"runtime-fetch", "gh-actions-secret", "keychain", "shell-export"}
)


class StepKind(StrEnum):
    MINT = "mint"
    STAGE = "stage"
    HOST = "host"
    VERIFY_NEW = "verify_new"
    STORE = "store"
    DISTRIBUTE = "distribute"
    VERIFY_CONSUMERS = "verify_consumers"
    RETIRE_OLD = "retire_old"
    CONFIRM_DEAD = "confirm_dead"


class Actor(StrEnum):
    MACHINE = "machine"
    HUMAN = "human"


class ContractError(RuntimeError):
    """A refusal: the rotation stops here, and the unit fails with this message."""


@dataclass(frozen=True)
class Step:
    kind: StepKind
    actor: Actor
    # The acceptance criterion a machine step files its evidence against. Human steps file none.
    ac_id: str | None = None


@dataclass(frozen=True)
class UnitPlan:
    name: str
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class HumanAct:
    name: str
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Plan:
    segments: tuple[UnitPlan | HumanAct, ...]

    def unit(self, name: str) -> UnitPlan:
        for segment in self.segments:
            if isinstance(segment, UnitPlan) and segment.name == name:
                return segment
        raise ContractError(f"the adapter has no unit named {name!r}")

    def steps(self) -> list[Step]:
        return [step for segment in self.segments for step in segment.steps]


class Provider(Protocol):
    """One kind of credential's issuer: how to mint, probe and revoke a value of it.

    A provider has two probes, each with its own target. `probe` asks what a consumer would: it
    proves a new value works and an old one is dead (verify_new, confirm_dead).
    `probe_before_revoke` asks the endpoint the revoke itself relies on, so retire_old's evidence
    that the old value was live comes from the same place the revoke reads (ADR-0055 amendment 1:
    OpenRouter's key-info answers 200 for a live key and lags a revoke, so it can prove live-before
    but not dead-after). A provider whose two probes are one endpoint gives both the same target.
    """

    name: str
    probe_target: str
    revoke_probe_target: str
    dead_statuses: frozenset[int]
    # Whether this provider is the hosted service its credential's hosted destinations name, so
    # those destinations are verified by probing it (ADR-0055's verification table).
    hosts_destinations: bool

    def mint(self) -> Value: ...

    def probe(self, value: Value) -> int | None: ...

    def probe_before_revoke(self, value: Value) -> int | None: ...

    def known_bad(self) -> Value: ...

    def host(self, new: Value, old: Value) -> str:
        """Make the service accept `new` beside `old`, and confirm it does. Returns a note.

        Only a provider whose plan has a host step implements it. Like every step, it re-derives
        where things stand: a service that already accepts both is re-proved, not written again.
        """
        ...

    def revoke(self, value: Value) -> str:
        """Revoke `value`, refusing (`ContractError`) unless the provider confirms which key it is.

        Returns a note for the step's evidence. The note names what was checked, never a value,
        an id or a label: a key's label carries characters of the key, and an id is a run the
        orchestrator's evidence scan refuses.
        """
        ...


class ScheduledReads(Protocol):
    """A rotation's scheduled readers: whether each has run since the keeper was written."""

    def covers(self, destination: str) -> bool: ...

    def switched(self, destination: str, stored_at: datetime) -> bool:
        """Never raises: a read that can't be made isn't switched."""
        ...


def validate_plan(plan: Plan) -> None:
    """Refuse a plan whose order could break an invariant. Run once, when the adapter loads."""
    _validate_segments(plan)
    kinds = [step.kind for step in plan.steps()]
    _validate_counts(kinds)
    _validate_order(kinds)
    _validate_host(kinds)
    _validate_mint_and_stage(plan)


ONCE: Final = (
    StepKind.MINT,
    StepKind.STAGE,
    StepKind.VERIFY_NEW,
    StepKind.STORE,
    StepKind.RETIRE_OLD,
    StepKind.CONFIRM_DEAD,
)


def _validate_counts(kinds: list[StepKind]) -> None:
    for kind in ONCE:
        if kinds.count(kind) != 1:
            raise ContractError(f"a rotation has exactly one {kind} step")


def _validate_order(kinds: list[StepKind]) -> None:
    if kinds[:2] != [StepKind.MINT, StepKind.STAGE]:
        raise ContractError("a rotation starts with mint, then stage")
    retire = kinds.index(StepKind.RETIRE_OLD)
    if kinds[retire + 1 :] != [StepKind.CONFIRM_DEAD]:
        raise ContractError("only confirm_dead may follow retire_old, and it ends the rotation")
    store = kinds.index(StepKind.STORE)
    if kinds.index(StepKind.VERIFY_NEW) > retire or store > retire:
        raise ContractError("verify_new and store come before retire_old")
    verifies = [i for i, kind in enumerate(kinds) if kind is StepKind.VERIFY_CONSUMERS]
    if not verifies or verifies[-1] != retire - 1:
        raise ContractError("retire_old comes straight after a verify_consumers step")
    if verifies[0] < store:
        raise ContractError("verify_consumers comes after store")
    if store < kinds.index(StepKind.VERIFY_NEW):
        raise ContractError("store comes after verify_new: nothing unproved reaches the keeper")
    if StepKind.DISTRIBUTE in kinds:
        distribute = kinds.index(StepKind.DISTRIBUTE)
        if distribute < store or distribute > verifies[-1]:
            raise ContractError("distribute comes after store, before the last verify_consumers")


def _validate_host(kinds: list[StepKind]) -> None:
    if StepKind.HOST not in kinds:
        return
    if kinds.count(StepKind.HOST) != 1:
        raise ContractError("a rotation has at most one host step")
    if (
        not kinds.index(StepKind.STAGE)
        < kinds.index(StepKind.HOST)
        < kinds.index(StepKind.VERIFY_NEW)
    ):
        raise ContractError("host comes after stage, before verify_new")


def _validate_segments(plan: Plan) -> None:
    names = [segment.name for segment in plan.segments]
    if len(set(names)) != len(names):
        raise ContractError("segment names are unique")
    for segment in plan.segments:
        if not segment.steps:
            raise ContractError(f"{segment.name}: a segment holds at least one step")
        if isinstance(segment, UnitPlan):
            if any(step.actor is not Actor.MACHINE or not step.ac_id for step in segment.steps):
                raise ContractError(f"{segment.name}: a unit holds machine steps, each with an AC")
        elif any(step.actor is not Actor.HUMAN or step.ac_id for step in segment.steps):
            raise ContractError(f"{segment.name}: a human act holds human steps and files nothing")
    ac_ids = [step.ac_id for step in plan.steps() if step.ac_id]
    if len(set(ac_ids)) != len(ac_ids):
        raise ContractError("each machine step files against its own acceptance criterion")


def _validate_mint_and_stage(plan: Plan) -> None:
    # A machine-minted value exists only in memory until it is staged, so the two share a unit.
    for segment in plan.segments:
        kinds = [step.kind for step in segment.steps]
        if StepKind.MINT in kinds and kinds[kinds.index(StepKind.MINT) + 1 :][:1] != [
            StepKind.STAGE
        ]:
            raise ContractError(f"{segment.name}: stage shares mint's segment, straight after it")


@dataclass(frozen=True)
class Rotation:
    """The one credential and occurrence a unit works, and where its values are kept."""

    credential_id: str
    occurrence: str
    keeper_id: str
    work_project_id: str
    destinations: tuple[str, ...]

    @property
    def staging_key(self) -> str:
        return f"rotation/{self.credential_id}/{self.occurrence}/staging"

    @property
    def quarantine_key(self) -> str:
        return f"rotation/{self.credential_id}/{self.occurrence}/quarantine"


@dataclass
class UnitRun:
    """One unit's work: the rotation, the services it reaches, and what it has filed."""

    rotation: Rotation
    store: SecretStore
    provider: Provider
    # Satisfied human acts on this unit, each the fingerprint the person recorded.
    attestations: Sequence[Fingerprint]
    # Files one step's evidence. Evidence is the log of a step, never its proof.
    record: Callable[[Step, StepEvidence], None]
    # Runs before every step: the registry still hands this credential to the SDS (R6).
    before_step: Callable[[], None]
    # Step kinds this unit already filed evidence for, in an earlier attempt.
    filed: frozenset[StepKind] = frozenset()
    scheduled: ScheduledReads | None = None
    _minted: Value | None = field(default=None, repr=False)

    def run(self, unit: UnitPlan) -> None:
        if self.finished():
            return
        steps = unit.steps
        if self.staged() is None and self.quarantined() is not None:
            # Only confirm_dead deletes staging, and only after proving the old value dead, so
            # this is its cleanup interrupted. Nothing but confirm_dead may run: it proves death
            # again from the quarantine and finishes deleting.
            steps = tuple(step for step in steps if step.kind is StepKind.CONFIRM_DEAD)
            if not steps:
                raise ContractError("staging is gone with the quarantine kept: never guess")
        for step in steps:
            self.before_step()
            HANDLERS[step.kind](self, step)

    def finished(self) -> bool:
        """Whether an earlier attempt of this unit already confirmed, cleaned up and filed it.

        Only a unit that filed confirm_dead can be finished, and nothing is skipped on that
        evidence alone: both secrets must also be gone, which only confirm_dead's cleanup does.
        """
        return (
            StepKind.CONFIRM_DEAD in self.filed
            and self.quarantined() is None
            and self.staged() is None
        )

    # ---- where the values are ---------------------------------------------------------------

    def staged(self) -> StoredSecret | None:
        return self.store.find(self.rotation.work_project_id, self.rotation.staging_key)

    def quarantined(self) -> StoredSecret | None:
        return self.store.find(self.rotation.work_project_id, self.rotation.quarantine_key)

    def old(self) -> Value:
        quarantine = self.quarantined()
        if quarantine is not None:
            return self.store.read(quarantine.id)
        return self.store.read(self.rotation.keeper_id)

    def new(self) -> Value:
        staging = self.staged()
        if staging is None:
            raise ContractError("no staged value: the new value exists nowhere")
        return self.store.read(staging.id)

    def new_and_old(self) -> tuple[Value, Value]:
        new, old = self.new(), self.old()
        if new.same_as(old):
            raise ContractError("the new value's fingerprint equals the old one's")
        return new, old

    # ---- probes -----------------------------------------------------------------------------

    def probe(
        self,
        value: Value,
        subject: Subject,
        expected: State | None,
        *,
        before_revoke: bool = False,
    ) -> list[ProbeAnswer]:
        """The control, then the subject, on one target. Refuses unless both answer as they must.

        With `expected` None the subject may be live or dead, never indeterminate; its answer is
        filed as expected live, since a value still live is the one the step acts on. The control
        and the subject are asked through the same probe, so a refused control proves the target
        the subject's answer came from.
        """
        provider = self.provider
        if before_revoke:
            ask, target = provider.probe_before_revoke, provider.revoke_probe_target
        else:
            ask, target = provider.probe, provider.probe_target
        control = self._answer(ask, target, provider.known_bad(), "known_bad", "dead")
        if control.outcome != "dead":
            raise ContractError(
                f"the probe didn't refuse a known-bad value ({control.http_status})"
            )
        answer = self._answer(ask, target, value, subject, expected or "live")
        if answer.outcome == "indeterminate" or (expected and answer.outcome != expected):
            raise ContractError(
                f"the {subject} value probed {answer.outcome} ({answer.http_status}), "
                f"expected {expected}"
            )
        return [control, answer]

    def _answer(
        self,
        ask: Callable[[Value], int | None],
        target: str,
        value: Value,
        subject: Subject,
        expected: State,
    ) -> ProbeAnswer:
        status = ask(value)
        if status == LIVE_STATUS:
            outcome = "live"
        elif status in self.provider.dead_statuses:
            outcome = "dead"
        else:
            outcome = "indeterminate"
        return ProbeAnswer(target, subject, expected, status, outcome)

    def accepts(self, new: Value) -> bool:
        """Whether the provider's service accepts `new` right now, beside a refused control."""
        provider = self.provider
        control = self._answer(
            provider.probe, provider.probe_target, provider.known_bad(), "known_bad", "dead"
        )
        answer = self._answer(provider.probe, provider.probe_target, new, "new", "live")
        return control.outcome == "dead" and answer.outcome == "live"

    # ---- consumers --------------------------------------------------------------------------

    def unverified(self, new: Value) -> list[str]:
        """Each approved destination that does not, right now, hold or attest the new value."""
        missing = []
        attested = sum(1 for print_ in self.attestations if new.fingerprint.matches(print_))
        needs_attestation = 0
        for destination in self.rotation.destinations:
            kind = parse(destination)["kind"]
            if kind == "provider":
                continue
            if kind in ATTESTED_KINDS and not self._scheduled(destination):
                needs_attestation += 1
                if needs_attestation > attested:
                    missing.append(destination)
            elif not self._holds(destination, new):
                missing.append(destination)
        return missing

    def _scheduled(self, destination: str) -> bool:
        return self.scheduled is not None and self.scheduled.covers(destination)

    def _holds(self, destination: str, new: Value) -> bool:
        """Whether a machine-read consumer holds the new value, or has run since it was stored."""
        consumer = parse(destination)
        kind = consumer["kind"]
        if self.scheduled is not None and self.scheduled.covers(destination):
            stored_at = self.store.revised(self.rotation.keeper_id)
            return self.scheduled.switched(destination, stored_at)
        if kind in HOSTED_KINDS:
            if not self.provider.hosts_destinations:
                raise ContractError(f"this adapter can't reach a {kind} consumer")
            return self.accepts(new)
        if kind in READ_BACK_KINDS:
            return self.store.read(consumer["uuid"]).same_as(new)
        raise ContractError(f"no way to verify a {kind} consumer")


def _fingerprints(new: Value, old: Value) -> tuple[tuple[Which, Fingerprint], ...]:
    return (("old", old.fingerprint), ("new", new.fingerprint))


def _mint(run: UnitRun, step: Step) -> None:
    staging = run.staged()
    if staging is not None:
        # An earlier attempt minted and staged; re-prove what it left rather than mint again.
        new, old = run.new_and_old()
        note = "already minted and staged by an earlier attempt"
    else:
        if run.quarantined() is not None:
            raise ContractError("a quarantine exists with nothing staged: never guess")
        # The old value is read first: a mint that fails to stage leaves a key nobody holds.
        old = run.old()
        new = run.provider.mint()
        if new.same_as(old):
            raise ContractError("the minted value's fingerprint equals the old one's")
        run._minted = new
        note = None
    run.record(step, StepEvidence(step.kind, fingerprints=_fingerprints(new, old), note=note))


def _stage(run: UnitRun, step: Step) -> None:
    if run.staged() is None:
        if run._minted is None:
            raise ContractError("nothing minted in this attempt to stage")
        run.store.create(run.rotation.work_project_id, run.rotation.staging_key, run._minted)
    elif run._minted is not None and not run.new().same_as(run._minted):
        raise ContractError("a different value is already staged")
    new, old = run.new_and_old()
    run.record(step, StepEvidence(step.kind, fingerprints=_fingerprints(new, old)))


def _host(run: UnitRun, step: Step) -> None:
    new, old = run.new_and_old()
    note = run.provider.host(new, old)
    run.record(step, StepEvidence(step.kind, fingerprints=_fingerprints(new, old), note=note))


def _verify_new(run: UnitRun, step: Step) -> None:
    new, old = run.new_and_old()
    probes = run.probe(new, "new", "live")
    run.record(
        step, StepEvidence(step.kind, fingerprints=_fingerprints(new, old), probes=tuple(probes))
    )


def _store(run: UnitRun, step: Step) -> None:
    new = run.new()
    keeper = run.store.read(run.rotation.keeper_id)
    quarantine = run.quarantined()
    if quarantine is None:
        if keeper.same_as(new):
            raise ContractError("the keeper already holds the new value and nothing is quarantined")
        run.store.create(run.rotation.work_project_id, run.rotation.quarantine_key, keeper)
        old = keeper
    else:
        old = run.store.read(quarantine.id)
        if not keeper.same_as(new) and not keeper.same_as(old):
            raise ContractError("the keeper holds neither the old value nor the new one")
    if new.same_as(old):
        raise ContractError("the new value's fingerprint equals the old one's")
    if not keeper.same_as(new):
        run.store.write(run.rotation.keeper_id, new)
    if not run.store.read(run.rotation.keeper_id).same_as(new):
        raise ContractError("the keeper doesn't read back the new value")
    run.record(step, StepEvidence(step.kind, fingerprints=_fingerprints(new, old)))


def _distribute(run: UnitRun, step: Step) -> None:
    new, old = run.new_and_old()
    written = 0
    for destination in run.rotation.destinations:
        consumer = parse(destination)
        if consumer["kind"] in READ_BACK_KINDS and consumer["uuid"] != run.rotation.keeper_id:
            run.store.write(consumer["uuid"], new)
            written += 1
    note = f"wrote {written} secret(s) beside the keeper"
    run.record(step, StepEvidence(step.kind, fingerprints=_fingerprints(new, old), note=note))


def _verify_consumers(run: UnitRun, step: Step) -> None:
    new, old = run.new_and_old()
    missing = run.unverified(new)
    if any(parse(d)["kind"] in READ_BACK_KINDS for d in missing):
        raise ContractError("a BWS consumer doesn't read back the new value")
    verified = sum(1 for d in run.rotation.destinations if parse(d)["kind"] != "provider")
    note = f"{verified - len(missing)} of {verified} consumers hold or attest the new value"
    run.record(step, StepEvidence(step.kind, fingerprints=_fingerprints(new, old), note=note))


def _retire_old(run: UnitRun, step: Step) -> None:
    if run.quarantined() is None:
        raise ContractError("no quarantined old value to retire: never guess")
    new, old = run.new_and_old()
    if run.unverified(new):
        raise ContractError("a consumer hasn't verified the new value; the old stays live")
    probes = run.probe(old, "old", None, before_revoke=True)
    if probes[-1].outcome == "live":
        note = run.provider.revoke(old)
    else:
        note = "the old value was already refused; nothing to revoke"
    run.record(
        step,
        StepEvidence(
            step.kind, fingerprints=_fingerprints(new, old), probes=tuple(probes), note=note
        ),
    )


def _confirm_dead(run: UnitRun, step: Step) -> None:
    quarantine, staging = run.quarantined(), run.staged()
    if quarantine is None:
        raise ContractError("the quarantined old value is gone before confirm_dead")
    # The old value is deleted only once it is proved dead in this attempt. Staging goes first:
    # the keeper already holds the new value, so after a crash between the two deletions the next
    # attempt still has the quarantine to prove death with.
    new = (
        run.store.read(staging.id)
        if staging is not None
        else run.store.read(run.rotation.keeper_id)
    )
    old = run.store.read(quarantine.id)
    if new.same_as(old):
        raise ContractError("the new value's fingerprint equals the old one's")
    probes = run.probe(old, "old", "dead") + run.probe(new, "new", "live")
    prints = _fingerprints(new, old)
    run.record(step, StepEvidence(step.kind, fingerprints=prints, probes=tuple(probes)))
    deleted = []
    if staging is not None:
        run.store.delete(staging.id)
        deleted.append(f"staging {staging.id}")
    run.store.delete(quarantine.id)
    deleted.append(f"quarantine {quarantine.id}")
    run.record(
        step,
        StepEvidence(
            step.kind,
            fingerprints=prints,
            probes=tuple(probes),
            note="deleted " + ", ".join(deleted),
        ),
    )


HANDLERS: Final[dict[StepKind, Callable[[UnitRun, Step], None]]] = {
    StepKind.MINT: _mint,
    StepKind.STAGE: _stage,
    StepKind.HOST: _host,
    StepKind.VERIFY_NEW: _verify_new,
    StepKind.STORE: _store,
    StepKind.DISTRIBUTE: _distribute,
    StepKind.VERIFY_CONSUMERS: _verify_consumers,
    StepKind.RETIRE_OLD: _retire_old,
    StepKind.CONFIRM_DEAD: _confirm_dead,
}
