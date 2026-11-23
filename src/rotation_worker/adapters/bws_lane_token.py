"""The BWS lane-token adapter: one lane's own Bitwarden access token (ADR-0055 amendment 19).

Each lane runs as its own BWS machine account, in its own Coolify service, `sds-lane-<lane>`
(ADR-0059). A person creates and revokes the token in the Bitwarden web app; the VM instance takes
it from the inbox, keeps it in its keeper in `Rotation / Keepers` (Devon's D-24), and writes the
lane's `<LANE>_BWS_ACCESS_TOKEN` variable.

* **Issue and reissue.** A first token is an issue (`plans.derive_issue`): the keeper holds the
  inbox's empty marker, and nothing is retired. A later token is a rotation whose revoke is a
  person's, as a BWS machine token's is.
* **Bind by declared reach** (item 4), never by `same_identity`: the registry row lists what the
  lane's account sees (`reach_secrets`, and `reach_projects`, empty unless a grant is a project's),
  and the pasted token must see exactly that, beside a login a known-bad control fails. So it binds
  after a leak's early revoke, when the old token no longer answers. No two rows may declare the
  same set, or the check couldn't tell one lane's token from another's.
* **Distribute writes the variable with one `PATCH` and never restarts** (item 6): the lane takes
  the token at its next start, a lane phases' redeploy in its quiet slot. The client it builds
  can't send a restart, and a row whose `coolify-env` has `redeploy` true is refused before a
  claim. A `PATCH` answering 404 (no such variable) fails the unit with `CoolifyRefused`.
* **Verify consumers** (item 7): the keeper is proved by reading it back. The Coolify variable has
  one of two proofs, chosen by the registry row:
  - A row with no `helper_target`, and every issue: the `PATCH` answering 2xx, since the
    executor's Coolify token can't read a variable back (amendment 1). That the lane loaded the
    token is then the lane phases' proof, outside the plan (amendment 19 item 7, Devon's D-27).
    An issue runs `verify_consumers` in its `store` unit, right after the `PATCH` and before any
    restart, and has no revoke, so R10 doesn't reach it.
  - A reissue on a row naming its `helper_target`: the fingerprint of the `BWS_ACCESS_TOKEN` the
    restarted lane container loaded, read through the VPS helper's `loaded` verb, must equal the
    keeper's (ADR-0055 amendment 29 item 3, D-63 C). The `verify` unit waits unclaimed until it
    does (`loaded_wait`), and the claimed step reads once more and fails the unit on anything
    else. A mismatch or an unreadable answer never lets `verify` pass.
* **Revoke** is a person's, after `verify` passes, or first, after a leak (item 10, D-26), so the
  probe before it accepts the old token live or refused. Confirm-dead waits out a session's
  lifetime after the revoke, as a machine token's does.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

from rotation_worker.adapters.bws_machine_token import BwsMachineTokenProvider, reach_digest
from rotation_worker.contract import ENV_KIND, ContractError, Rotation, StepKind, UnitPlan, accepts
from rotation_worker.coolify import CoolifyClient
from rotation_worker.destinations import (
    DestinationError,
    credential_destinations,
    parse,
    render,
)
from rotation_worker.helper_client import HelperClient
from rotation_worker.plans import derive
from rotation_worker.registry import RegistryEntry
from rotation_worker.types import TYPES
from rotation_worker.values import Fingerprint, Value

NAME: Final = "bws-lane-token"
# Its own provider name, as `postgres-role`'s is: a registry row of this type names it, its
# Coolify token is `admin_secrets["bws-lane-token"]`, and every row naming it is a lane's.
PROVIDER: Final = NAME
INSTANCE: Final = "prod"
VARIABLE: Final = re.compile(r"^[A-Z][A-Z0-9_]*_BWS_ACCESS_TOKEN$")
UUID: Final = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
# The registry row's field naming the helper's consumers-only target for this lane, and that
# target's name: the helper's `IDENT` (`deploy/vps/sds-pg-rotate`).
HELPER_TARGET_FIELD: Final = "helper_target"
HELPER_TARGET: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
# The units a person's mint and revoke sit between, for code that reads a unit by name: the issue
# plan's are its first two. The plan that runs is derived per credential (`plans.candidates`).
PLAN: Final = derive(TYPES[NAME].capabilities, (), "", "bws-secret")


def declared_reach(credential: Mapping[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    """The row's declared projects and secrets: what the lane's account must see, exactly."""
    secrets = credential.get("reach_secrets")
    projects = credential.get("reach_projects", [])
    for field, ids in (("reach_secrets", secrets), ("reach_projects", projects)):
        if not isinstance(ids, list) or not all(
            isinstance(i, str) and UUID.fullmatch(i) for i in ids
        ):
            raise ContractError(f"a bws-lane-token row's {field} is a list of lowercase BWS ids")
        if len(set(ids)) != len(ids):
            raise ContractError(f"a bws-lane-token row's {field} names an id twice")
    if not secrets:
        raise ContractError("a bws-lane-token row names the secrets its lane reads (reach_secrets)")
    return frozenset(projects), frozenset(secrets)


def lane_variable(destinations: Sequence[str], keeper: str) -> Mapping[str, Any]:
    """The lane's one variable: a production service's, which the executor never redeploys."""
    others = [d for d in destinations if d != keeper and parse(d)["kind"] != "provider"]
    if len(others) != 1 or parse(others[0])["kind"] != ENV_KIND:
        raise ContractError("a bws-lane-token row names its keeper and exactly one coolify-env")
    variable = parse(others[0])
    if variable["redeploy"] is not False:
        raise ContractError(
            "a lane token's coolify-env has redeploy false: the lane redeploys itself"
        )
    if variable["instance"] != INSTANCE or variable["resource_type"] != "service":
        raise ContractError("a lane token's coolify-env is a production service's")
    if not VARIABLE.fullmatch(variable["key"]):
        raise ContractError("a lane token's coolify-env key is <LANE>_BWS_ACCESS_TOKEN")
    return variable


def helper_target(credential: Mapping[str, Any]) -> str | None:
    """The row's helper target, or `None` when it names none: the field is optional, and a row
    without it keeps the `PATCH` proof."""
    if HELPER_TARGET_FIELD not in credential:
        return None
    target = credential[HELPER_TARGET_FIELD]
    if not isinstance(target, str) or not HELPER_TARGET.fullmatch(target):
        raise ContractError("a bws-lane-token row's helper_target is the helper's target name")
    return target


def check_row(credential: Mapping[str, Any]) -> None:
    """Refuse, before a claim, a row this adapter couldn't act on."""
    declared_reach(credential)
    helper_target(credential)
    try:
        lane_variable(credential_destinations(credential), _keeper(credential))
    except DestinationError as error:
        raise ContractError(f"a bws-lane-token row's consumers are malformed: {error}") from None


def _keeper(credential: Mapping[str, Any]) -> str:
    return render({"kind": "bws-secret", "uuid": credential.get("bws_uuid")})


def sharing_reach(entries: Sequence[RegistryEntry], credential: Mapping[str, Any]) -> list[str]:
    """Each other lane-token row (`entries`, every row naming this provider) declaring the same
    reach as `credential`. A row that doesn't say what it sees can't be told apart, so it counts."""
    mine = declared_reach(credential)
    shared = []
    for entry in entries:
        other = entry.credential
        if other.get("id") == credential.get("id"):
            continue
        try:
            if declared_reach(other) == mine:
                shared.append(str(other.get("id")))
        except ContractError:
            shared.append(str(other.get("id")))
    return sorted(shared)


def build_provider(
    *,
    rotation: Rotation | None,
    credential: Mapping[str, Any] | None,
    store_read: Callable[[str], Value],
    coolify_token_id: str | None,
    coolify_url: str | None,
    organization_id: str | None,
    siblings: Callable[[], list[str]],
    pg_helper: Callable[[], HelperClient] | None,
    issue: bool,
) -> BwsLaneTokenProvider:
    """A row naming a helper target takes the `loaded` read unless `issue` says the plan is an
    issue (`AdapterContext.issue`, whose default is that strict path)."""
    if rotation is None or credential is None:
        raise ContractError("the bws-lane-token adapter needs its rotation and registry row")
    if coolify_token_id is None or not coolify_url:
        raise ContractError("the bws-lane-token adapter needs coolify_url and its token's id")
    projects, secrets = declared_reach(credential)
    target, loaded = helper_target(credential), None
    if target is not None:
        if pg_helper is None:
            raise ContractError("a bws-lane-token row naming a helper_target needs the helper")
        loaded = LoadedRead(target, pg_helper)
    try:
        variable = lane_variable(rotation.destinations, _keeper(credential))
    except DestinationError as error:
        raise ContractError(f"a bws-lane-token row's keeper is malformed: {error}") from None

    def coolify() -> CoolifyClient:
        token = store_read(coolify_token_id)
        return CoolifyClient(coolify_url, token, "service", variable["uuid"], restarts=False)

    return BwsLaneTokenProvider(
        BwsMachineTokenProvider(organization_id=organization_id),
        reach_digest(projects, secrets),
        variable,
        coolify,
        siblings,
        loaded=loaded,
        issue=issue,
    )


def printed(fingerprint: Fingerprint) -> str:
    """A fingerprint in the helper's form: 16 hex characters, a colon, the length."""
    return f"{fingerprint.sha256_prefix}:{fingerprint.length}"


class LoadedRead:
    """One read of what the lane container loaded, through the helper's `loaded` verb."""

    def __init__(self, target: str, helper: Callable[[], HelperClient]) -> None:
        self.target = target
        self._helper = helper

    def mismatch(self, expected: Fingerprint) -> str | None:
        """Why the container's loaded token isn't `expected`, or `None` when every consumer of
        the target loaded exactly it. Raises when the helper can't be asked or doesn't answer;
        the reason names fingerprints only, never a value."""
        prints = self._helper().loaded(self.target)
        want = printed(expected)
        if not prints:
            return f"the helper's {self.target} target lists no consumer"
        wrong = sorted(
            f"{name} {print_ or 'nothing'}" for name, print_ in prints.items() if print_ != want
        )
        if wrong:
            return f"the lane hasn't loaded the keeper's token {want}: {', '.join(wrong)}"
        return None


class BwsLaneTokenProvider:
    name = PROVIDER
    probe_target = BwsMachineTokenProvider.probe_target
    revoke_probe_target = probe_target
    dead_statuses = BwsMachineTokenProvider.dead_statuses
    hosts_destinations = True

    def __init__(
        self,
        login: BwsMachineTokenProvider,
        declared: str,
        variable: Mapping[str, Any],
        coolify: Callable[[], CoolifyClient],
        siblings: Callable[[], list[str]],
        loaded: LoadedRead | None = None,
        issue: bool = False,
    ) -> None:
        self._login = login
        self._declared = declared
        self._variable = variable
        self._coolify = coolify
        self._client: CoolifyClient | None = None
        self._siblings = siblings
        # The value this provider's `PATCH` wrote and Coolify answered 2xx for, in this unit.
        self._written: Fingerprint | None = None
        # Set only for a reissue on a row naming its helper target: the `loaded` proof.
        self._loaded = None if issue else loaded

    def mint(self) -> Value:
        raise ContractError("a person creates a lane's BWS access token, in the web app")

    def known_bad(self) -> Value:
        return self._login.known_bad()

    def probe(self, value: Value) -> int | None:
        return self._login.probe(value)

    def probe_before_revoke(self, value: Value) -> int | None:
        return self._login.probe(value)

    def bind_refusal(self, new: Value, old: Value) -> str | None:
        """Why a pasted token isn't this lane's (item 4), or `None`. `old` isn't asked: after a
        leak it no longer answers, and before the first issue it is the empty marker."""
        shared = self._siblings()
        if shared:
            return (
                "the registry declares the same reach for "
                f"{', '.join(shared)}, so what a token sees can't tell the lanes apart"
            )
        if not accepts(self, new):
            return "a fresh login doesn't accept it beside a refused known-bad value"
        if self._login.reach(new) != self._declared:
            return "what it can see isn't exactly the reach its registry row declares"
        return None

    def write_variable(self, consumer: Mapping[str, Any], new: Value) -> None:
        """One `PATCH` of the lane's variable, never a restart. A 404 (no such variable) raises
        `CoolifyRefused` and fails the unit; it is never skipped."""
        if dict(consumer) != dict(self._variable):
            raise ContractError("the approved coolify-env is the only variable a lane token writes")
        if self._client is None:
            self._client = self._coolify()
        self._client.set_variable(self._variable["key"], new.reveal())
        self._written = new.fingerprint

    def loaded_wait(self, unit: UnitPlan, keeper: Callable[[], Fingerprint]) -> str | None:
        """Why this unit can't be claimed yet, or `None`: a reissue's unit verifying consumers
        on a row with a helper target waits until the lane loaded the keeper's token. Any other
        unit, and every unit of a row without one, reads nothing. Raises when the keeper or the
        helper can't be read; the worker waits on that too."""
        if self._loaded is None or not any(
            step.kind is StepKind.VERIFY_CONSUMERS for step in unit.steps
        ):
            return None
        return self._loaded.mismatch(keeper())

    def consumer_loaded(self, consumer: Mapping[str, Any], new: Value) -> bool:
        """Whether the lane's variable holds `new`. Reached only from `verify_consumers` and
        `retire_old` (`UnitRun.unverified`), never from store or distribute.

        A row with no helper target, and every issue: the `PATCH` answering 2xx is the proof
        (item 7), since the executor's Coolify token can't read a variable back. Distribute's
        write in this unit counts; a unit that didn't write it writes it again, which changes
        nothing the lane loads before its next start. That the lane runs on it is the lane phases'
        read (D-27).

        A reissue on a row with a helper target: this runs in its `verify` unit, after the
        session restarted the lane. One `loaded` read, and no `PATCH`. Anything but the new
        token's fingerprint raises, because `verify_consumers` would only count a hosted consumer
        that returned `False`, and a mismatch must fail the unit (Infra Brain 1032)."""
        if self._loaded is None:
            if self._written != new.fingerprint:
                self.write_variable(consumer, new)
            return True
        if dict(consumer) != dict(self._variable):
            raise ContractError("the approved coolify-env is the only variable a lane token loads")
        mismatch = self._loaded.mismatch(new.fingerprint)
        if mismatch is not None:
            raise ContractError(mismatch)
        return True

    def host(self, new: Value, old: Value) -> str:
        raise ContractError("a lane token has no hosted service to host it")

    def revoke(self, value: Value) -> str:
        raise ContractError("a person revokes a lane's BWS access token, in the web app")
