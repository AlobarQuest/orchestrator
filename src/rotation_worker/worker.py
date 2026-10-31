"""One pass of the executor: find ready rotation units, check each, and work the ones that pass.

For each ready unit whose envelope names a rotation adapter, in order:

1. **Check before claiming** (`checks.check_unit`), from git, with no side effect. A transient
   failure (git or the network) leaves the unit for the next pass. A refusal is a finding about
   the unit itself, so the executor claims it and fails it with the reason, where the dead-letter
   report shows it and the owning instance raises `rotation-unit-failed`. A refusal it can't claim
   leaves nothing in the ledger, so it raises `rotation-unit-stuck` once the unit is overdue.
2. **Wait, unclaimed, for a person's mint**, taking the value from the inbox once it binds
   (`inbox.py`), and **for the scheduled readers** (`readers.py`). Each one is read before any
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

After the units, one alert sync (`notify.py`) tells a person about this instance's units only:
acts they wait on, failed units, reviews overdue, and units that have stayed unmoved past a
threshold, on clocks kept in the state directory. A unit this pass couldn't read keeps its alerts
and its clocks as they were, and no failure of the sync touches a unit or the pass.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from rotation_worker.adapters import AdapterContext
from rotation_worker.adapters.orchestrator_bearer import NAME as ORCHESTRATOR_PROVIDER
from rotation_worker.checks import Approved, approved_package, check_unit
from rotation_worker.config import ConfigError
from rotation_worker.contract import (
    HELD_KIND,
    ContractError,
    Provider,
    Rotation,
    Step,
    StepKind,
    UnitRun,
)
from rotation_worker.coolify import CoolifyClient
from rotation_worker.destinations import DestinationError, parse
from rotation_worker.envelope import ADAPTER_KEY, KEYCHAIN_ADAPTERS, is_rotation_unit
from rotation_worker.envelope import constraints as envelope_constraints
from rotation_worker.evidence import StepEvidence
from rotation_worker.git_mirror import MirrorError, RepoMirror
from rotation_worker.github_actions import ActionsSecrets, FingerprintReports, RotationApp
from rotation_worker.held import HeldSettings
from rotation_worker.helper_client import HelperClient
from rotation_worker.inbox import (
    INBOX_PROJECTS,
    Inbox,
    Refusals,
    bind,
    other_mints,
)
from rotation_worker.live_runs import stranded_by_a_restart
from rotation_worker.notify import (
    Act,
    Notifier,
    behind_key,
    first_sightings,
    unit_failed_key,
    unit_reader_clock_key,
    unit_review_key,
    unit_stuck_key,
    unit_waiting_key,
)
from rotation_worker.orchestrator_client import Lease, OrchestratorClient, OrchestratorError
from rotation_worker.overlaps import mid_overlap
from rotation_worker.plans import WRITING_STEPS, person_acts_beside
from rotation_worker.readers import (
    ConsumerReads,
    Healthchecks,
    Read,
    ReadError,
    ReadUnavailable,
)
from rotation_worker.registry import (
    PACKAGES_REPOSITORY,
    RegistryError,
    find_credential,
    provider_entries,
    standing_credential,
)
from rotation_worker.revokes import ObservedRevokes, Revoke, person_revokes_beside, revoke_wait
from rotation_worker.secret_store import SecretStore, StoreError
from rotation_worker.values import Fingerprint, Value, scrub

LOG = logging.getLogger("rotation_worker")
EVIDENCE_TYPE: Final = "rotation.step"
_REVIEW: Final = "https://sds.alobar.net/review/units"
# alert-hub refuses a longer title.
TITLE_LIMIT: Final = 200
MAX_REASON: Final = 400
FAILED_RUNBOOK: Final = 'docs/operations/rotation-executor.md, "If a rotation stalls after host"'
# A refusal: the unit itself is wrong, and a person must see it.
REFUSALS: Final = (ContractError, RegistryError, DestinationError, ReadError)
# Nothing could be asked: try again next pass.
TRANSIENT: Final = (MirrorError, OrchestratorError, StoreError)
# Past the longest wait measured on a shut change window, about 31 hours.
ROTATION_WAIT_ALERT_HOURS: Final = 48
# Retire waits for weekly readers, so a healthy rotation can wait 7 days.
ROTATION_READER_WAIT_ALERT_HOURS: Final = 192
ROTATION_REVIEW_ALERT_HOURS: Final = 48
# How long this build may trail `main` on the code it runs before a person is told.
ROTATION_BEHIND_ALERT_DAYS: Final = 7
BEHIND_REPOSITORY: Final = "AlobarQuest/orchestrator"
BEHIND_PATHS: Final = ("src/rotation_worker", "src/estate_clients", "src/alert_hub_client")
SOURCE_PREFIX: Final = "rotation-worker@"


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
    # Tells a person when one of this instance's units waits on them (`notify.py`).
    notifier: Notifier | None = None
    # Where a person pastes a value they minted (`inbox.py`); absent, they stage it by hand.
    inbox: Inbox | None = None
    # The BWS organization, which a provider lists a token's reach in.
    organization_id: str | None = None
    # The VPS Postgres helper, built when a step needs it (`AdapterContext.pg_helper`).
    pg_helper: Callable[[], HelperClient] | None = None


def run_pass(setup: Setup) -> int:
    """Work every ready rotation unit once. Returns how many it submitted."""
    submitted = 0
    found = PassActs()
    for unit_id in setup.client.ready_units():
        try:
            submitted += _take(setup, unit_id, found)
        except TRANSIENT as error:
            # Nothing was decided about the unit: a claim refused (a shut window, a version that
            # moved), or a service that didn't answer. The next pass asks again.
            reason = scrub(str(error))
            LOG.warning("unit_skipped", extra={"unit": unit_id, "reason": reason})
            if unit_id in found.seen:
                found.waits.setdefault(unit_id, Wait(reason))
        except Exception as error:
            # Something no handler foresaw, before or after the claim: logged, and the pass goes
            # on to the next unit rather than leaving every one behind it unworked.
            reason = scrub(f"{type(error).__name__}: {error}")
            LOG.error("unit_not_worked", extra={"unit": unit_id, "reason": reason})
            if unit_id in found.seen:
                found.waits[unit_id] = Wait(reason, stuck=True)
        else:
            continue
        # Nothing was learned of this unit's acts: their alerts stay as they were.
        found.keep |= {_revoke_key(unit_id), _mint_key(unit_id), _inbox_key(unit_id)}
        if unit_id not in found.seen:
            found.keep |= _clock_keys(unit_id)
    if setup.notifier is not None:
        try:
            acts, unread = unit_alerts(setup)
            late, unchecked = behind(setup, _now())
            acts, unread = {**acts, **late}, unread | unchecked
            hold = unread | found.keep
            overdue, unsaved = _overdue(setup, found, hold)
            setup.notifier.announce({**acts, **found.due, **overdue}, hold | unsaved)
        except Exception as error:
            # An unread ledger says nothing about what is pending: announce nothing this pass,
            # rather than resolve every open alert. Never fails the pass.
            reason = scrub(f"{type(error).__name__}: {error}")
            LOG.warning("acts_unread", extra={"reason": reason})
    return submitted


def unit_alerts(setup: Setup) -> tuple[dict[str, Act], set[str]]:
    """What this instance's rotation units need from a person, keyed for alert-hub: each act a
    unit waits on, each failed unit and each review overdue. Also the keys of units that couldn't
    be read this pass, which are left as they are. A row with nothing to say is never read."""
    acts: dict[str, Act] = {}
    unread: set[str] = set()
    main: list[str | None] = []
    for row in setup.client.active_units():
        pending = [
            b
            for b in row.get("blockers") or []
            if b.get("kind") == "external_system" and b.get("status") == "pending"
        ]
        failed = row.get("unit_state") in ("failed", "blocked", "revision_required")
        overdue = _review_overdue(row)
        if not (pending or failed or overdue):
            continue
        unit_id = str(row["unit_id"])
        try:
            unit = setup.client.unit(unit_id)
            ours = is_rotation_unit(unit) and _ours(setup, unit)
            where = _where(setup, unit, main) if ours and failed else ""
        except TRANSIENT as error:
            LOG.warning("unit_unread", extra={"unit": unit_id, "reason": scrub(str(error))})
            unread |= {f"rotation-act:{b['dependency_id']}" for b in pending}
            unread |= _clock_keys(unit_id) | {unit_failed_key(unit_id), unit_review_key(unit_id)}
            continue
        if not ours:
            continue
        name = row.get("unit_title") or row.get("unit_key")
        link = f"{_REVIEW}/{unit_id}"
        for blocker in pending:
            key = f"rotation-act:{blocker['dependency_id']}"
            what = blocker.get("external_ref") or blocker.get("required_state_or_condition")
            acts[key] = Act(
                key,
                f"A rotation waits on you: {name}"[:TITLE_LIMIT],
                f"{what}. Record it on the unit's review page.",
                link,
            )
        if failed:
            key = unit_failed_key(unit_id)
            state, message = _stopped(row)
            acts[key] = Act(
                key, f"A rotation unit {state}: {name}"[:TITLE_LIMIT], f"{where} {message}", link
            )
        if overdue:
            key = unit_review_key(unit_id)
            acts[key] = Act(
                key,
                f"A rotation unit awaits review: {name}"[:TITLE_LIMIT],
                f"It has waited over {ROTATION_REVIEW_ALERT_HOURS} hours for a review.",
                link,
            )
    return acts, unread


def _stopped(row: Mapping[str, Any]) -> tuple[str, str]:
    """What a stopped unit's alert says. `last_failure` is the newest move to `failed`, so only a
    unit now failed may quote it, and requeue takes only a failed or blocked unit."""
    if row.get("unit_state") == "revision_required":
        return "needs revision", "It needs revision: a person decides what happens on /review."
    next_step = (
        "Next: requeue it as SYSTEM while attempts remain, retry on /review once spent, "
        f"else cancel ({FAILED_RUNBOOK})."
    )
    if row.get("unit_state") == "blocked":
        return "is blocked", f"It is blocked. {next_step}"
    why = (row.get("last_failure") or {}).get("reason") or "no reason recorded"
    return "failed", f"{scrub(str(why))[:MAX_REASON]} {next_step}"


def behind(setup: Setup, now: datetime) -> tuple[dict[str, Act], set[str]]:
    """An alert when `main` holds a change to the code this build runs that is over
    `ROTATION_BEHIND_ALERT_DAYS` old. Anything that stops it reading holds the key."""
    key = behind_key(setup.store.read_back_kind)
    try:
        setup.mirror.sync(BEHIND_REPOSITORY)
        since = setup.mirror.oldest_change_since(
            BEHIND_REPOSITORY, setup.source_revision.removeprefix(SOURCE_PREFIX), BEHIND_PATHS
        )
    except Exception as error:
        # Whatever went wrong, this alert's key is held and the pass's other alerts go out.
        LOG.warning("behind_unread", extra={"reason": scrub(f"{type(error).__name__}: {error}")})
        return {}, {key}
    if since is None or now - since < timedelta(days=ROTATION_BEHIND_ALERT_DAYS):
        return {}, set()
    return {
        key: Act(
            key,
            f"A rotation worker is behind main: {setup.store.read_back_kind}"[:TITLE_LIMIT],
            f"main has held a change to its code since {since.date().isoformat()}; "
            f"it runs {setup.source_revision}. Update and restart it.",
        )
    }, set()


def _review_overdue(row: Mapping[str, Any]) -> bool:
    event = row.get("last_event_at")
    if row.get("unit_state") != "awaiting_review" or not event:
        return False
    at = datetime.fromisoformat(str(event))
    return _now() - at.replace(tzinfo=at.tzinfo or UTC) >= timedelta(
        hours=ROTATION_REVIEW_ALERT_HOURS
    )


def _where(setup: Setup, unit: Mapping[str, Any], main: list[str | None]) -> str:
    """The credential a failed unit rotates and its staging key's name, as far as can be told: a
    package that can't be read may be why the unit failed, and doesn't silence its alert."""
    intake = setup.client.package_intake(str(unit["work_package_revision_id"]))
    credential = standing_credential(str(intake.get("package_id"))) or "unknown"
    omitted = f"Credential {credential}; its package can't be read, so the staging key's name is"
    commit = _packages_main(setup, main)
    if commit is None:
        return f"{omitted} omitted: intent-packages couldn't be fetched."
    try:
        document = approved_package(intake, setup.mirror, commit)
    except REFUSALS as error:
        return f"{omitted} omitted: {scrub(str(error))[:MAX_REASON]}."
    occurrence = str(document["profile_fields"].get("occurrence"))
    staging = Rotation(credential, occurrence, "", setup.work_project_id, ()).staging_key
    return f"Credential {credential}, staging key {staging}."


def _packages_main(setup: Setup, main: list[str | None]) -> str | None:
    """intent-packages' `main`, fetched once in `main`, this pass's memo; `None` if it can't be."""
    if not main:
        try:
            main.append(setup.mirror.sync(PACKAGES_REPOSITORY))
        except MirrorError as error:
            LOG.warning("packages_unread", extra={"reason": scrub(str(error))})
            main.append(None)
    return main[0]


def _overdue(setup: Setup, found: PassActs, hold: Set[str]) -> tuple[dict[str, Act], set[str]]:
    """Move the clock on, and return an alert for each unit past its threshold.

    A unit has two clocks. The ordinary one, under its `unit_waiting_key`, runs whether it is
    waiting or stuck, so a unit that alternates between the two still comes due. Reader waits run
    their own, `unit_reader_clock_key`, so a healthy week on readers can't lend its hours to one
    skip. A pass sights only its own clock and judges only by it, and keeps the other: a wait
    alternating between the two kinds still comes due on either. A clock neither sighted nor held
    is forgotten, so a unit no longer waiting starts afresh.

    The second value is the alert keys to hold, every waiting unit's: an open waiting or stuck
    alert stays until the unit is no longer waiting, whichever kind this pass is, and a key that
    fires this pass fires all the same. A clock that can't be saved fires nothing this pass.
    """
    now = _now()
    current = {_clock_key(unit_id, wait) for unit_id, wait in found.waits.items()}
    waiting = {key for unit_id in found.waits for key in _clock_keys(unit_id)}
    since = first_sightings(setup.state_dir, current, now, hold | waiting)
    if since is None:
        return {}, waiting
    overdue = {}
    for unit_id, wait in found.waits.items():
        first = since[_clock_key(unit_id, wait)]
        if now - first < timedelta(hours=wait.hours):
            continue
        key = unit_stuck_key(unit_id) if wait.stuck else unit_waiting_key(unit_id)
        what = "can't be worked" if wait.stuck else "is waiting"
        overdue[key] = Act(
            key,
            f"A rotation unit {what}: {unit_id}"[:TITLE_LIMIT],
            f"Since {first.date().isoformat()}: {scrub(wait.reason)[:MAX_REASON]}",
            f"{_REVIEW}/{unit_id}",
        )
    return overdue, waiting


def _clock_key(unit_id: str, wait: Wait) -> str:
    return unit_reader_clock_key(unit_id) if wait.reader else unit_waiting_key(unit_id)


def _clock_keys(unit_id: str) -> set[str]:
    return {unit_stuck_key(unit_id), unit_waiting_key(unit_id), unit_reader_clock_key(unit_id)}


@dataclass(frozen=True)
class Wait:
    """Why a unit stayed unmoved this pass. `reader` says it waits on a schedule measured in days:
    a weekly reader still to run, or another rotation's overlap, which holds one. A reader mid-run
    ends within its run, so it is an ordinary wait. `stuck` says nothing in the ledger shows it."""

    reason: str
    reader: bool = False
    stuck: bool = False

    @property
    def hours(self) -> int:
        return ROTATION_READER_WAIT_ALERT_HOURS if self.reader else ROTATION_WAIT_ALERT_HOURS


@dataclass
class PassActs:
    """What the executor's own waits learned this pass: the acts now due, the keys of acts nothing
    could tell about, whose alerts stay as they were, why each unit stayed unmoved, by unit id,
    and the units reached past `_ours`, the only ones this instance may say anything about."""

    due: dict[str, Act] = field(default_factory=dict)
    keep: set[str] = field(default_factory=set)
    waits: dict[str, Wait] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)


def _revoke_key(unit_id: str) -> str:
    return f"rotation-revoke:{unit_id}"


def _mint_key(unit_id: str) -> str:
    return f"rotation-mint:{unit_id}"


def _inbox_key(unit_id: str) -> str:
    return f"rotation-inbox:{unit_id}"


def _take(setup: Setup, unit_id: str, found: PassActs) -> int:
    unit = setup.client.unit(unit_id)
    if not is_rotation_unit(unit) or not _ours(setup, unit):
        return 0
    found.seen.add(unit_id)
    intake = setup.client.package_intake(str(unit["work_package_revision_id"]))
    reads: ConsumerReads | None = None
    try:
        approved = check_unit(unit, intake, setup.mirror, setup.work_project_id, setup.store)
        held = _held(setup, approved)
        if approved.reads and _verifies(approved):
            reads = _consumer_reads(setup, approved)
            waiting = reads.pending(setup.store.revised(approved.rotation.keeper_id))
            if waiting and _gates_retire(approved):
                until = _overlap_until(setup, approved)
                LOG.info(
                    "unit_waiting",
                    extra={"unit": str(unit["id"]), "readers": waiting, "overlap_until": until},
                )
                backstop = f"; the overlap ends {until}" if until else ""
                found.waits[unit_id] = Wait(
                    f"Readers still to run: {', '.join(waiting)}{backstop}", reader=True
                )
                return 0
    except REFUSALS as error:
        _refuse(setup, unit, scrub(str(error)), found)
        return 0
    # Built before the claim: a provider that can't be built (an admin secret missing from the
    # configuration) is the executor's fault, not the unit's, so no attempt is spent on it.
    provider = _provider(setup, approved)
    if (
        _mint_waits(setup, approved, provider, unit, found)
        or _adapter_waits(setup, approved, unit_id, found)
        or _revoke_waits(setup, approved, provider, unit_id, found)
        or _overlap_waits(setup, approved, unit, found)
        or _restart_waits(setup, approved, held, unit_id, found)
    ):
        return 0
    if isinstance(provider, GatedRestarts) and _writes(approved):
        try:
            busy = provider.readers_mid_run()
        except ReadError as error:
            _refuse(setup, unit, scrub(str(error)), found)
            return 0
        except ReadUnavailable as error:
            busy = [f"unreadable: {scrub(str(error))}"]
        if busy:
            LOG.info("unit_waiting", extra={"unit": str(unit["id"]), "readers_mid_run": busy})
            found.waits[unit_id] = Wait(f"Readers mid-run: {', '.join(busy)}")
            return 0
    return 1 if _work(setup, unit, approved, provider, reads, held) else 0


def _ours(setup: Setup, unit: Mapping[str, Any]) -> bool:
    """Whether this instance owns the unit, by the orchestrator's rule (ADR-0055 amendment 12).

    The Keychain instance owns exactly the units whose adapter is in `KEYCHAIN_ADAPTERS`; the VM
    instance owns every other rotation unit, including one naming an adapter this build does not
    know, which its checks refuse. The orchestrator refuses the other instance's claim, so it is
    left unclaimed rather than failed, which would spend the unit's attempts.
    """
    name = str(envelope_constraints(unit).get(ADAPTER_KEY))
    return (name in KEYCHAIN_ADAPTERS) == (setup.store.read_back_kind == "keychain")


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
            organization_id=setup.organization_id,
            pg_helper=setup.pg_helper,
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
        lambda app: CoolifyClient(coolify_url, store.read(token_id), "application", app),
        application,
    )
    for consumer in destinations:
        held.check(consumer)
    return held


def _adapter_waits(setup: Setup, approved: Approved, unit_id: str, found: PassActs) -> bool:
    """Whether the adapter says the unit can't be claimed yet; the reason is logged."""
    wait = approved.adapter.wait
    reason = wait(approved.unit, setup.client.history(unit_id), _now()) if wait else None
    if reason is not None:
        LOG.info("unit_waiting", extra={"unit": unit_id, "reason": reason})
        found.waits[unit_id] = Wait(reason)
    return reason is not None


def _mint_waits(
    setup: Setup,
    approved: Approved,
    provider: Provider,
    unit: Mapping[str, Any],
    found: PassActs,
) -> bool:
    """Whether the unit after a person's mint waits for the value; one pasted into the inbox is
    staged for it once it binds, and the inbox emptied (`inbox.py`)."""
    if not person_acts_beside(approved.plan, approved.unit, -1, StepKind.MINT):
        return False
    rotation, unit_id = approved.rotation, str(unit["id"])
    staged = setup.store.find(rotation.work_project_id, rotation.staging_key)
    if setup.inbox is None:
        if staged is None:
            found.due[_mint_key(unit_id)] = _mint_act(setup, approved, unit_id)
            LOG.info("unit_waiting", extra={"unit": unit_id, "reason": "nothing staged yet"})
        return staged is None
    try:
        new = setup.inbox.read()
    except StoreError as error:
        reason = scrub(str(error))
        LOG.warning("inbox_unread", extra={"unit": unit_id, "reason": reason})
        found.keep |= {_mint_key(unit_id), _inbox_key(unit_id)}
        if staged is None:
            # No act can be announced, so only the clock tells a person this has gone on too long.
            found.waits[unit_id] = Wait(f"the inbox can't be read: {reason}")
        return staged is None
    if staged is not None:
        if new is not None:
            _tidy(setup, setup.inbox, new, staged.id, rotation, unit_id, found)
        return False
    if new is None:
        found.due[_mint_key(unit_id)] = _mint_act(setup, approved, unit_id)
        LOG.info("unit_waiting", extra={"unit": unit_id, "reason": "the inbox is empty"})
        return True
    return _bind_inbox(setup, setup.inbox, approved, provider, unit, new, found)


def _bind_inbox(
    setup: Setup,
    inbox: Inbox,
    approved: Approved,
    provider: Provider,
    unit: Mapping[str, Any],
    new: Value,
    found: PassActs,
) -> bool:
    """Bind the inbox's value to the rotation, or say why not. Returns whether the unit waits."""
    rotation, unit_id = approved.rotation, str(unit["id"])
    try:
        reason = bind(
            new,
            rotation=rotation,
            store=setup.store,
            provider=provider,
            others_waiting=other_mints(setup.client, str(unit["work_package_revision_id"])),
            other_keepers=lambda: _other_keepers(setup, approved),
        )
    except (*REFUSALS, *TRANSIENT) as error:
        # Nothing was decided about the value; it stays, and the person is told why.
        reason = f"it couldn't be checked: {scrub(str(error))}"
    if reason is None:
        LOG.info("inbox_bound", extra={"unit": unit_id, "fingerprint": str(new.fingerprint)})
        # A failure here leaves the inbox holding what is staged, which the next pass empties.
        inbox.empty()
        return False
    found.due[_mint_key(unit_id)] = _mint_act(setup, approved, unit_id)
    if Refusals(setup.state_dir).again(new):
        found.due[_inbox_key(unit_id)] = Act(
            _inbox_key(unit_id),
            f"The value in the inbox isn't taken for {rotation.credential_id}"[:TITLE_LIMIT],
            f"It stays in the inbox, unused: {reason}.",
            f"{_REVIEW}/{unit_id}",
        )
    LOG.info("unit_waiting", extra={"unit": unit_id, "reason": f"the inbox value: {reason}"})
    return True


def _tidy(
    setup: Setup,
    inbox: Inbox,
    new: Value,
    staged_id: str,
    rotation: Rotation,
    unit_id: str,
    found: PassActs,
) -> None:
    """Empty an inbox still holding the value staged from it; report one holding any other."""
    if new.same_as(setup.store.read(staged_id)):
        inbox.empty()
        return
    found.due[_inbox_key(unit_id)] = Act(
        _inbox_key(unit_id),
        f"The inbox holds a second value for {rotation.credential_id}"[:TITLE_LIMIT],
        "A value is already staged for this rotation, so the one in the inbox is left unused. "
        "Clear it, or put back the word empty, if it was pasted by mistake.",
        f"{_REVIEW}/{unit_id}",
    )


def _mint_act(setup: Setup, approved: Approved, unit_id: str) -> Act:
    credential = approved.registry.credential
    where = credential.get("provider_identity")
    project = INBOX_PROJECTS.get(setup.store.read_back_kind, "its inbox project")
    if setup.inbox is None:
        how = (
            "This executor has no inbox: stage the value by hand with rotation-worker stage. "
            'See "Mint and stage a value by hand" in the rotation executor runbook.'
        )
    else:
        how = (
            f"Paste the new value into the secret rotation-inbox in the BWS project {project}. "
            "There is nothing to record: the executor checks it and takes it from there."
        )
    return Act(
        _mint_key(unit_id),
        f"Create a new {approved.rotation.credential_id} value"[:TITLE_LIMIT],
        (f"It is {where}. " if isinstance(where, str) else "") + how,
        f"{_REVIEW}/{unit_id}",
    )


def _other_keepers(setup: Setup, approved: Approved) -> list[tuple[str, Value]]:
    """Each other SDS-rotated credential of this type whose keeper this instance reads."""
    found = []
    for entry in provider_entries(setup.mirror, approved.adapter.provider_name):
        credential = str(entry.credential.get("id"))
        if credential == approved.rotation.credential_id or not entry.rotated_by_sds:
            continue
        secret_id = setup.store.locate(parse(entry.keeper))
        if secret_id is not None:
            found.append((credential, setup.store.read(secret_id)))
    return found


def _revoke_waits(
    setup: Setup, approved: Approved, provider: Provider, unit_id: str, found: PassActs
) -> bool:
    """Whether the unit after a person's revoke waits for the revoke to be seen, or for the old
    value's sessions to end; a revoke not yet seen is announced (`revokes.py`)."""
    if not person_revokes_beside(approved.plan, approved.unit, -1):
        return False
    rotation = approved.rotation
    key = _revoke_key(unit_id)
    try:
        quarantine = setup.store.find(rotation.work_project_id, rotation.quarantine_key)
        old = setup.store.read(quarantine.id) if quarantine is not None else None
        state, reason = revoke_wait(
            provider,
            old,
            ObservedRevokes(setup.state_dir),
            f"{rotation.credential_id}/{rotation.occurrence}",
            setup.client.history(unit_id),
            approved.adapter.revoke_settles,
            _now(),
        )
    except (*REFUSALS, StoreError) as error:
        state, reason = Revoke.UNKNOWN, f"the revoke can't be looked for: {scrub(str(error))}"
    if state is Revoke.LIVE:
        found.due[key] = Act(
            key,
            f"Revoke the old {rotation.credential_id} value"[:TITLE_LIMIT],
            "Every consumer has switched to the new value. Revoke the old one in its provider's "
            "console; there is nothing to record, the executor sees the revoke itself.",
            f"{_REVIEW}/{unit_id}",
        )
    elif state is Revoke.UNKNOWN:
        found.keep.add(key)
    if reason is None:
        return False
    LOG.info("unit_waiting", extra={"unit": unit_id, "reason": reason})
    if state is Revoke.UNKNOWN:
        # No act can be announced, so only the clock tells a person this has gone on too long.
        found.waits[unit_id] = Wait(reason)
    return True


def _overlap_waits(
    setup: Setup, approved: Approved, unit: Mapping[str, Any], found: PassActs
) -> bool:
    """Whether the unit switches a value while another rotation is mid-overlap (ADR-0055
    amendment 12); the units it waits on are logged."""
    if approved.unit.name not in approved.adapter.overlap_waits:
        return False
    others = mid_overlap(setup.client, str(unit["work_package_revision_id"]))
    if others:
        LOG.info("unit_waiting", extra={"unit": str(unit["id"]), "mid_overlap": others})
        found.waits[str(unit["id"])] = Wait(
            f"Rotations mid-overlap: {', '.join(others)}", reader=True
        )
    return bool(others)


def _restart_waits(
    setup: Setup, approved: Approved, held: HeldSettings | None, unit_id: str, found: PassActs
) -> bool:
    """Whether writing the unit's held copy would restart the orchestrator under a live run.

    Distribute runs after host and store, so a live run is waited out here, unclaimed, rather
    than refused mid-unit at an attempt's cost. The step still asks again before it writes.
    """
    if held is None or not any(s.kind is StepKind.DISTRIBUTE for s in approved.unit.steps):
        return False
    stranded = stranded_by_a_restart(setup.client)
    if stranded:
        LOG.info("unit_waiting", extra={"unit": unit_id, "restart_would_strand": stranded})
        found.waits[unit_id] = Wait(f"A restart would strand: {', '.join(stranded)}")
    return bool(stranded)


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
    return person_revokes_beside(approved.plan, approved.unit, 1)


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


def _refuse(setup: Setup, unit: Mapping[str, Any], reason: str, found: PassActs) -> None:
    unit_id = str(unit["id"])
    LOG.error("unit_refused", extra={"unit": unit_id, "reason": reason})
    try:
        lease, version = _claim(setup, unit)
    except TRANSIENT:
        found.waits[unit_id] = Wait(f"Refused, and the claim to fail it: {reason}", stuck=True)
        raise
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
