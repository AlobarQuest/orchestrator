"""The checks before acting (ADR-0055 decision 1 and amendment 2): what a unit may do, from git.

The orchestrator says which unit is ready and hands over its envelope and package revision. None
of that is trusted on its own (T3), so before the executor claims a rotation unit it establishes,
from intent-packages' and the registries' public git:

* the revision is intent-packages' own: its `source_commit` is an ancestor of `main`, and the
  package at that commit hashes to the `content_hash` the orchestrator holds;
* it is a standing rotation package for one credential and one occurrence;
* that credential's registry entry, read at its repository's `main`, still hands it to the SDS
  (`rotated_by_sds = true`, R6) and names the adapter's provider;
* the plan is the one the approved package's criteria describe, step for step: for a credential
  type, the plan derived from its registry row (`plans.derive`), or the plan its packages were
  approved with before derivation; for any other name, its one fixed plan;
* the registry's destinations, the package's and the unit envelope's are exactly equal;
* the keeper (the registry's `bws_uuid`, or a machine token's Keychain item) is one of them and
  is kept where this instance's store reads, every one fits a `rotation-step/1` row, and a unit
  marked as touching no hosted service writes none;
* a unit declaring a host with no overlap has a host step (whether an overlap is impossible is
  `m2m_setting.build`'s to refuse, from what the orchestrator serves);
* every `runtime-fetch` consumer can be verified: the registry names its scheduled read, or the
  plan holds a human act to attest it; and an Actions secret has a distribute step to write it.

The result names everything a unit run needs, so nothing the unit does reads the orchestrator's
word for any of it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from rotation_worker import envelope
from rotation_worker.adapters import ADAPTERS, Adapter
from rotation_worker.canonical import CanonicalError, package_hash
from rotation_worker.contract import (
    ACTIONS_KIND,
    HELD_KIND,
    ContractError,
    HumanAct,
    Plan,
    Rotation,
    StepKind,
    UnitPlan,
)
from rotation_worker.destinations import HOSTED_KINDS, parse
from rotation_worker.envelope import ADAPTER_KEY
from rotation_worker.evidence import MAX_DESTINATION
from rotation_worker.git_mirror import COMMIT_PATTERN as COMMIT
from rotation_worker.git_mirror import MirrorError, RepoMirror
from rotation_worker.plans import derive, select_plan
from rotation_worker.readers import Read, scheduled_reads
from rotation_worker.registry import (
    PACKAGES_REPOSITORY,
    RegistryEntry,
    RegistryError,
    find_credential,
    lineage_path,
    load_package,
    package_path,
    standing_credential,
    standing_destinations,
)
from rotation_worker.secret_store import SecretStore

PROFILE: Final = "non-software-operational"
UNIT_KEY: Final = "rotation_unit"
DESTINATIONS_KEY: Final = "destinations"
NON_HOSTED_KEY: Final = "touches_no_hosted_service"
# Declares a host with no overlap, for one rotation whose old value another identity still holds
# (ADR-0055 amendment 9). `True` declares it, only on a unit that hosts; `False` is the default.
WITHOUT_OVERLAP_KEY: Final = "host_without_overlap"
# The steps that write a hosted service: host gives it the new value, and retire_old takes the
# old one away. Store and distribute write BWS only.
WRITING_STEPS: Final = frozenset({StepKind.HOST, StepKind.RETIRE_OLD})


@dataclass(frozen=True)
class Approved:
    """A unit the checks passed: its adapter, its segment of the plan, and the rotation."""

    adapter: Adapter
    # The plan this credential runs, which the approved package's criteria describe. A type's
    # `adapter.plan` is only its pre-derivation candidate: read this one.
    plan: Plan
    unit: UnitPlan
    rotation: Rotation
    registry: RegistryEntry
    # Each scheduled reader's read, by destination (`readers.py`).
    reads: Mapping[str, Read]
    host_without_overlap: bool = False


def check_unit(
    unit_view: Mapping[str, Any],
    intake: Mapping[str, Any],
    mirror: RepoMirror,
    work_project_id: str,
    store: SecretStore,
) -> Approved:
    """Every check before a claim. A refusal raises `ContractError` or `RegistryError`.

    `unit_view` is the orchestrator's `GET /work-units/{id}`; `intake` is its package revision.
    """
    if intake.get("id") != unit_view.get("work_package_revision_id"):
        raise ContractError("the package revision isn't the unit's")
    constraints = envelope.constraints(unit_view)
    adapter = ADAPTERS.get(str(constraints.get(ADAPTER_KEY)))
    if adapter is None:
        raise ContractError(f"no adapter named {constraints.get(ADAPTER_KEY)!r}")
    document = _approved_package(intake, mirror)
    fields = document["profile_fields"]
    credential_id = str(fields.get("credential_id"))
    packaged = standing_destinations(fields, credential_id, "the approved revision")
    entry = find_credential(mirror, credential_id)
    if not entry.rotated_by_sds:
        raise ContractError(f"{credential_id} isn't rotated_by_sds in the registry")
    if entry.credential.get("provider") != adapter.provider_name:
        raise ContractError(
            f"{credential_id}'s provider isn't the {adapter.provider_name} adapter's"
        )
    enveloped = constraints.get(DESTINATIONS_KEY)
    if not (entry.destinations == packaged == enveloped):
        raise ContractError("the registry's, the package's and the envelope's destinations differ")
    keeper = _keeper_id(entry, packaged, store)
    reads = scheduled_reads(entry.credential)
    plan = _plan(adapter, entry, store, document.get("acceptance"))
    unit = plan.unit(str(constraints.get(UNIT_KEY)))
    criteria = {criterion.get("ac_id") for criterion in intake.get("acceptance_criteria", [])}
    for step in unit.steps:
        if step.ac_id not in criteria:
            raise ContractError(f"the package has no {step.ac_id} for the {step.kind} step")
    _check_destination_fit(
        packaged, plan, unit, marked_non_hosted=constraints.get(NON_HOSTED_KEY) is True
    )
    without_overlap = _declared_without_overlap(constraints, unit)
    _check_runtime_fetch_verifiable(packaged, plan, reads)
    _check_human_retire_verifiable(packaged, plan, reads, store)
    occurrence = fields.get("occurrence")
    if not isinstance(occurrence, str) or not occurrence.strip():
        raise ContractError("the approved revision names no occurrence")
    rotation = Rotation(credential_id, occurrence, keeper, work_project_id, tuple(packaged))
    return Approved(adapter, plan, unit, rotation, entry, reads, without_overlap)


def _plan(
    adapter: Adapter,
    entry: RegistryEntry,
    store: SecretStore,
    acceptance: object,
) -> Plan:
    if adapter.capabilities is None:
        return adapter.plan
    derived = derive(adapter.capabilities, entry.destinations, entry.keeper, store.read_back_kind)
    return select_plan((derived, adapter.plan), acceptance)


def _keeper_id(entry: RegistryEntry, packaged: list[str], store: SecretStore) -> str:
    credential_id = entry.credential.get("id")
    if entry.keeper not in packaged:
        raise ContractError(f"{credential_id}'s keeper isn't one of its destinations")
    keeper = store.locate(parse(entry.keeper))
    if keeper is None:
        raise ContractError(f"{credential_id}'s keeper isn't kept where this executor reads")
    return keeper


def _declared_without_overlap(constraints: Mapping[str, Any], unit: UnitPlan) -> bool:
    declared = constraints.get(WITHOUT_OVERLAP_KEY, False)
    if declared is False:
        return False
    if declared is not True:
        raise ContractError(f"{WITHOUT_OVERLAP_KEY} is a boolean")
    if StepKind.HOST not in {step.kind for step in unit.steps}:
        raise ContractError(f"{WITHOUT_OVERLAP_KEY} is declared on a unit with no host step")
    return True


def _approved_package(package: Mapping[str, Any], mirror: RepoMirror) -> Mapping[str, Any]:
    """The approved package document, read at its revision's commit and hash-checked."""
    if package.get("source_repository") != PACKAGES_REPOSITORY:
        raise ContractError("the revision isn't from intent-packages")
    # The intake's source_path is the package directory on whichever machine emitted it, which git
    # can't confirm, so the package is read at its id's path in intent-packages instead.
    commit, package_id = package.get("source_commit"), package.get("package_id")
    expected = standing_credential(package_id) if isinstance(package_id, str) else None
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit) or expected is None:
        raise ContractError("the revision names no full commit and standing rotation package")
    path = package_path(expected)
    main = mirror.sync(PACKAGES_REPOSITORY)
    if not _local(mirror.is_ancestor, PACKAGES_REPOSITORY, commit, main):
        raise ContractError("the revision's commit isn't on intent-packages' main")
    document = load_package(_local(mirror.show, PACKAGES_REPOSITORY, commit, path))
    fields = document.get("profile_fields")
    if document.get("profile") != PROFILE:
        raise ContractError(f"the revision's profile isn't {PROFILE}")
    if not isinstance(fields, dict):
        raise RegistryError("the approved revision has no profile_fields")
    credential_id = str(fields.get("credential_id"))
    if credential_id != expected:
        raise ContractError("the revision isn't its credential's standing package")
    try:
        recomputed = package_hash(document)
    except CanonicalError as error:
        raise RegistryError(f"the approved revision doesn't hash: {error}") from error
    if recomputed != package.get("content_hash"):
        raise ContractError("the revision at its commit doesn't hash to the approved content hash")
    lineage = load_package(
        _local(mirror.show, PACKAGES_REPOSITORY, main, lineage_path(credential_id))
    )
    approvals = lineage.get("approvals")
    latest = approvals[-1] if isinstance(approvals, list) and approvals else None
    if not isinstance(latest, dict) or latest.get("approved_hash") != recomputed:
        raise ContractError("the revision isn't the package's latest approval at main")
    return document


def _local[T](read: Callable[..., T], *arguments: str) -> T:
    """A read of the mirror the sync just filled: a failure is about the revision, not the
    network, so it refuses rather than waiting for the next pass."""
    try:
        return read(*arguments)
    except MirrorError as error:
        raise ContractError(f"the revision can't be read: {error}") from error


def _check_destination_fit(
    destinations: list[str], plan: Plan, unit: UnitPlan, *, marked_non_hosted: bool
) -> None:
    hosted = [d for d in destinations if parse(d)["kind"] in HOSTED_KINDS]
    writes = any(step.kind in WRITING_STEPS for step in unit.steps)
    if marked_non_hosted and hosted and writes:
        raise ContractError("the unit is marked non-hosted but writes a hosted consumer")
    if hosted and StepKind.HOST not in {step.kind for step in plan.steps()}:
        # Only an adapter whose provider is the hosted service can write or verify one: refuse
        # before claiming rather than strand a rotation half done.
        raise ContractError("this adapter reaches no hosted consumer")
    actions = [d for d in destinations if parse(d)["kind"] == ACTIONS_KIND]
    if actions and StepKind.DISTRIBUTE not in {step.kind for step in plan.steps()}:
        # Nothing else writes an Actions secret, so its copies would keep the old value.
        raise ContractError("this adapter writes no Actions secret")
    held = [d for d in destinations if parse(d)["kind"] == HELD_KIND]
    if held and StepKind.DISTRIBUTE not in {step.kind for step in plan.steps()}:
        raise ContractError("this adapter writes no held copy")
    if held and marked_non_hosted and StepKind.DISTRIBUTE in {s.kind for s in unit.steps}:
        # Writing a held copy restarts the application that holds it.
        raise ContractError("the unit is marked non-hosted but restarts an application")
    if any(len(destination) > MAX_DESTINATION for destination in destinations):
        raise ContractError("a destination is longer than rotation-step/1 evidence can carry")


def _check_runtime_fetch_verifiable(
    destinations: list[str], plan: Plan, reads: Mapping[str, Read]
) -> None:
    # Only a human act that verifies consumers can attest a reader (a person minting or
    # revoking attests nothing), so without one each reader needs a read, or retire could
    # never run.
    if any(_attests(segment) for segment in plan.segments):
        return
    for destination in destinations:
        if parse(destination)["kind"] == "runtime-fetch" and destination not in reads:
            raise ContractError(f"no read and no human act verifies {destination}")


def _check_human_retire_verifiable(
    destinations: list[str], plan: Plan, reads: Mapping[str, Read], store: SecretStore
) -> None:
    # A person who revokes has no machine step after the last verify_consumers to re-check
    # every consumer, so the wait before that unit's claim must be able to see each one.
    human_retire = any(
        isinstance(segment, HumanAct)
        and any(step.kind is StepKind.RETIRE_OLD for step in segment.steps)
        for segment in plan.segments
    )
    if not human_retire:
        return
    for destination in destinations:
        consumer = parse(destination)
        stored = store.locate(consumer) is not None or consumer["kind"] == HELD_KIND
        if consumer["kind"] != "provider" and not stored and destination not in reads:
            raise ContractError(f"a person revokes, and nothing machine-verifies {destination}")


def _attests(segment: UnitPlan | HumanAct) -> bool:
    return isinstance(segment, HumanAct) and any(
        step.kind is StepKind.VERIFY_CONSUMERS for step in segment.steps
    )
