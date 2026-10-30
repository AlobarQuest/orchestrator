"""The Postgres role adapter: the executor rotates a role's password, which has no overlap.

ADR-0055 amendment 17. Postgres holds one password per role, so once the role changes the old value
stops working for every new connection, and the service's consumers can't reach the database until
they restart with the new one. The type declares that itself (`Capabilities.overlap`), and the
package's revision approval covers the outage. It's a different thing from `host_without_overlap`,
a per-envelope declaration about a value another identity shares, which is refused here.

The executor's reach is the VPS helper (`deploy/vps/sds-pg-rotate`, through `helper_client.py`) and
one Coolify service. The registry row names the helper's target (`helper_target`) and the service's
health endpoint (`health_url`).

* **Mint** 32 random bytes as 64 hex characters, the one shape the helper accepts. The known-bad
  control has the same shape, or the helper would refuse the request instead of the login.
* **Host** writes the Coolify variable, sets the role, restarts the service, and waits until every
  consumer shows the new fingerprint and the database accepts the value. The restart takes the
  database down too, and nothing in the compose orders the two, so a consumer loading the value
  doesn't mean the database is back. Coolify goes first so that a crash leaves a state the next
  attempt can repeat: the variable is written blind, and `set` does nothing to a role that already
  has the value. A role and consumers already on the new value get the variable written, which a
  crash may have left on the old, and no restart, since a retry after a later step's failure
  would be a second outage for nothing.
* **Probe** asks the helper to log in over the container's network address. Live is 200, dead is
  401, and a probe that can't tell is `None`.
* **Revoke** is never reached: the role stopped taking the old value at host, so `retire_old` finds
  it already refused. A live old value means the database was reset behind the rotation, so it
  raises.
* **Consumers** are verified by the database accepting the value, every `loaded` fingerprint
  matching it, and `health_url` answering exactly 200, because a database accepting a value says
  nothing about the service that needs it.

**HOST UNDOES ITSELF.** Nothing requeues a failed unit by itself, and `/review` sits behind the
service this type restarts, so a host step can't leave it down and wait for a person. Any exception
from the writes runs the same writes with the old value and then raises `ContractError`, so the
unit fails with the service on the old value, the keeper unchanged and staging holding the unused
value. A first variable write that failed with every consumer still on the old value changed
nothing, so it isn't restarted. "Restored" is only ever reported after a restart Coolify accepted
and the wait that follows, or in that one case. If the undo fails too, the error names both
failures and recovery is `restore` with the keeper's value, from `rotation-worker pg-restore`.

**WITHOUT COOLIFY.** When `restore` can't write the variable it can't restart either, so the only
thing it can move is the role, and the role must follow what the consumers loaded. It reads
`loaded`. All on the value being restored: it sets the role to it. All on another value: it stops,
since that is host's finished state. Otherwise it keeps reading to the end of the window, and if
they come up on the value being replaced, a restart that landed with its answer lost, the role
follows them. A `loaded` read that fails leaves everything alone. Each case raises, saying the
variable may still hold the replaced value and `pg-restore` is due once Coolify returns.

**THE WORST CASE IS BELOW THE 15-MINUTE LEASE**, which is renewed only before each step, so all of
this runs on one renewal. One deadline covers each write sequence, from its start, not one per
stage, and a retry or a poll only starts inside it. In seconds, with every call taking just under
its limit:

    before the writes: 4 probes                       4 x 30                     = 120
    host's writes:     LOADED_WAIT + a last poll      170 + (30 + 2 x 30)        = 260
    undo, no Coolify:  LOADED_WAIT + a read + a set   170 + 30 + 90              = 290
    total                                                                        = 670, 11m10s

The undo's ordinary writes are bounded by host's. So are writes that use up their calls instead of
the wait: a `set` starts by `SET_RETRY` plus one poll, then a set, a restart, and no poll past
`LOADED_WAIT`: 95 + 90 + 60 = 245. Reading a secret isn't included, since BWS's own retries
(`secret_store.RETRY_DELAYS`) can add about three minutes per secret, and a provider reads two.

**NO VALUE IN ANY MESSAGE.** A `ContractError`'s text is scrubbed, and any other exception is named
by its type alone, since its text could hold anything. Nothing is chained.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final, NoReturn
from urllib.parse import urlsplit

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure, base_url_problem
from rotation_worker.contract import ContractError, Rotation, accepts
from rotation_worker.coolify import TIMEOUT_SECONDS as COOLIFY_TIMEOUT_SECONDS
from rotation_worker.coolify import CoolifyClient
from rotation_worker.destinations import (
    HOSTED_KINDS,
    DestinationError,
    credential_destinations,
    parse,
)
from rotation_worker.helper_client import (
    LOADED_TIMEOUT_SECONDS,
    PROBE_TIMEOUT_SECONDS,
    SET_TIMEOUT_SECONDS,
    HelperClient,
    HelperRefused,
)
from rotation_worker.orchestrator_client import TIMEOUT_SECONDS, USER_AGENT
from rotation_worker.plans import derive
from rotation_worker.secret_store import SecretStore
from rotation_worker.types import TYPES
from rotation_worker.values import Fingerprint, Value, scrub

NAME: Final = "postgres-role"
HOSTED_KIND: Final = "coolify-env"
INSTANCE: Final = "prod"
PASSWORD_BYTES: Final = 32
# One write sequence's deadline, from its start.
LOADED_WAIT: Final = timedelta(seconds=170)
# How long, from the same start, a `set` the database wasn't up for is tried again.
SET_RETRY: Final = timedelta(seconds=90)
POLL_SECONDS: Final = 5.0
# The module docstring's arithmetic, in seconds.
BEFORE_WRITES: Final = 4 * PROBE_TIMEOUT_SECONDS
LAST_POLL: Final = LOADED_TIMEOUT_SECONDS + 2 * PROBE_TIMEOUT_SECONDS
HOST_WRITES: Final = LOADED_WAIT.total_seconds() + LAST_POLL
WRITES_BY_CALLS: Final = (
    SET_RETRY.total_seconds() + POLL_SECONDS + SET_TIMEOUT_SECONDS + COOLIFY_TIMEOUT_SECONDS
)
UNDO_WITHOUT_COOLIFY: Final = (
    LOADED_WAIT.total_seconds() + LOADED_TIMEOUT_SECONDS + SET_TIMEOUT_SECONDS
)
WORST_CASE: Final = timedelta(
    seconds=BEFORE_WRITES + max(HOST_WRITES, WRITES_BY_CALLS) + UNDO_WITHOUT_COOLIFY
)
# The refusal that means the database wasn't up. Anything else the helper refuses won't change.
SET_RETRIED: Final = "probe_unavailable"
UNREADABLE: Final = "what the consumers loaded couldn't be read, so nothing else was touched"
WRITTEN: Final = "wrote the variable, set the role, restarted, and every consumer loaded the value"
# This type has no packages from before derivation, so its one candidate is the derived plan of a
# credential with no copies to distribute.
PLAN: Final = derive(TYPES[NAME].capabilities, (), "", "bws-secret")


def row_fields(credential: Mapping[str, Any]) -> tuple[str, str]:
    """The registry row's helper target and health URL, or a refusal."""
    target, health_url = credential.get("helper_target"), credential.get("health_url")
    if not isinstance(target, str) or not target:
        raise ContractError("a postgres-role row names its helper_target")
    if not isinstance(health_url, str) or base_url_problem(health_url) is not None:
        raise ContractError("a postgres-role row's health_url is https with a host")
    parsed = urlsplit(health_url)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ContractError("a postgres-role row's health_url is a plain host and path")
    return target, health_url


def hosted_destination(destinations: Sequence[str]) -> Mapping[str, Any]:
    """The rotation's one variable: on a production service, which its restart must follow."""
    hosted = [parse(d) for d in destinations if parse(d)["kind"] in HOSTED_KINDS]
    if len(hosted) != 1:
        raise ContractError("a postgres-role row has exactly one hosted coolify-env")
    destination = hosted[0]
    if (
        destination["kind"] != HOSTED_KIND
        or destination["instance"] != INSTANCE
        or destination["resource_type"] != "service"
        or destination["redeploy"] is not True
    ):
        raise ContractError("the hosted coolify-env is a production service's, with redeploy true")
    return destination


def check_row(credential: Mapping[str, Any]) -> None:
    """Refuse, before a claim, a row this adapter couldn't act on."""
    row_fields(credential)
    try:
        hosted_destination(credential_destinations(credential))
    except DestinationError as error:
        raise ContractError(f"a postgres-role row's coolify-env is malformed: {error}") from None


def build_provider(
    *,
    rotation: Rotation | None,
    credential: Mapping[str, Any] | None,
    store: SecretStore,
    coolify_token_id: str | None,
    coolify_url: str | None,
    pg_helper: Callable[[], HelperClient] | None,
    transport: httpx.BaseTransport | None = None,
    coolify_transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> PostgresRoleProvider:
    if rotation is None or credential is None:
        raise ContractError("the postgres-role adapter needs its rotation and registry row")
    if coolify_token_id is None or not coolify_url:
        raise ContractError("the postgres-role adapter needs coolify_url and its token's id")
    if pg_helper is None:
        raise ContractError("the postgres-role adapter needs the helper's configuration")
    target, health_url = row_fields(credential)
    destination = hosted_destination(rotation.destinations)

    def coolify() -> CoolifyClient:
        token = store.read(coolify_token_id)
        return CoolifyClient(coolify_url, token, "service", destination["uuid"], coolify_transport)

    parsed = urlsplit(health_url)
    health_path = parsed.path or "/"
    try:
        health = ConfinedClient(
            base_url=f"https://{parsed.netloc}",
            user_agent=USER_AGENT,
            headers={},
            timeout=TIMEOUT_SECONDS,
            permits=lambda method, path: (method, path) == ("GET", health_path),
            refuse=lambda method, path: ContractError(f"the health check may not {method} {path}"),
            transport=transport,
        )
    except TransportFailure as failure:
        raise ContractError(f"the health_url is unusable: {failure.error_type}") from None
    return PostgresRoleProvider(
        target, destination["key"], coolify, pg_helper, health, health_path, clock, sleep
    )


class PostgresRoleProvider:
    name = NAME
    probe_target = "the database's login, over its network address"
    revoke_probe_target = probe_target
    dead_statuses = frozenset({401})
    hosts_destinations = True

    def __init__(
        self,
        target: str,
        variable: str,
        coolify: Callable[[], CoolifyClient],
        pg_helper: Callable[[], HelperClient],
        health: ConfinedClient,
        health_path: str,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
    ) -> None:
        self._target = target
        self._variable = variable
        self._coolify = coolify
        self._pg_helper = pg_helper
        self._health = health
        self._health_path = health_path
        self._clock = clock
        self._sleep = sleep
        self._coolify_client: CoolifyClient | None = None
        self._helper: HelperClient | None = None

    # Each reads its secret once, when a step first needs it: a provider lives for one unit.
    def _service(self) -> CoolifyClient:
        if self._coolify_client is None:
            self._coolify_client = self._coolify()
        return self._coolify_client

    def _database(self) -> HelperClient:
        if self._helper is None:
            self._helper = self._pg_helper()
        return self._helper

    def mint(self) -> Value:
        return Value(secrets.token_hex(PASSWORD_BYTES))

    def known_bad(self) -> Value:
        return Value(secrets.token_hex(PASSWORD_BYTES))

    def probe(self, value: Value) -> int | None:
        return self._database().probe(self._target, value)

    def probe_before_revoke(self, value: Value) -> int | None:
        return self.probe(value)

    def host(self, new: Value, old: Value) -> str:
        if accepts(self, new):
            if self._loaded_now(new):
                # No undo here: nothing else changes, and a service on the new value stays on it.
                self._service().set_variable(self._variable, new.reveal())
                return "the role and every consumer already hold the new value"
        elif not accepts(self, old):
            raise ContractError("the database accepts neither value, or may be down: never guess")
        # Read here, not inside the writes: a token that can't be read has changed nothing.
        service = self._service()
        start = self._clock()
        try:
            service.set_variable(self._variable, new.reveal())
        except Exception as failure:
            self._undo(old, new, failure, only_if_changed=True)
        try:
            self._finish(service, new, start)
        except Exception as failure:
            self._undo(old, new, failure)
        return WRITTEN

    def restore(self, value: Value, replaced: Value | None = None) -> str:
        """Put `value` everywhere host would, and confirm every consumer loaded it.

        `replaced` is the value it takes over from, when the caller knows it, so the role can
        follow consumers that came up on it. Host's undo gives both; `rotation-worker pg-restore`
        gives only the keeper's value.
        """
        self._restore(value, replaced)
        return WRITTEN

    def revoke(self, value: Value) -> str:
        raise ContractError(
            "the old value is still live after the role changed: the database was reset behind "
            "the rotation"
        )

    def consumer_loaded(self, consumer: Mapping[str, Any], new: Value) -> bool:
        """Never raises: a service that can't be asked hasn't shown it loaded the value."""
        try:
            return accepts(self, new) and self._loaded_with(new) and self._healthy()
        except ContractError:
            return False

    def _restore(
        self, value: Value, replaced: Value | None, *, only_if_changed: bool = False
    ) -> None:
        service = self._service()
        start = self._clock()
        unchanged = only_if_changed and self._loaded_now(value)
        try:
            service.set_variable(self._variable, value.reveal())
        except Exception as failure:
            raise ContractError(self._without_coolify(value, replaced, start, failure)) from None
        if not unchanged:
            self._finish(service, value, start)

    def _finish(self, service: CoolifyClient, value: Value, start: datetime) -> None:
        self._set(value, start)
        service.restart()
        self._await_loaded(value, start)

    def _set(self, value: Value, start: datetime) -> None:
        while not self._set_once(value, start + SET_RETRY):
            self._sleep(POLL_SECONDS)

    def _set_once(self, value: Value, retry_until: datetime) -> bool:
        """Whether the role took the value; False if it's worth asking again."""
        try:
            self._database().set(self._target, value)
        except HelperRefused as refusal:
            if refusal.reason != SET_RETRIED or self._clock() >= retry_until:
                raise
            return False
        except ContractError:
            # No answer, which a retry may get.
            if self._clock() >= retry_until:
                raise
            return False
        return True

    def _await_loaded(self, value: Value, start: datetime) -> None:
        deadline = start + LOADED_WAIT
        while self._clock() < deadline:
            if self._settled(value):
                return
            self._sleep(POLL_SECONDS)
        raise ContractError("the consumers and the database didn't take the value in time")

    def _settled(self, value: Value) -> bool:
        try:
            return self._loaded_with(value) and accepts(self, value)
        except ContractError:
            # The helper can't always answer in the middle of a restart.
            return False

    def _loaded_now(self, value: Value) -> bool:
        try:
            return self._loaded_with(value)
        except ContractError:
            return False

    def _without_coolify(
        self, value: Value, replaced: Value | None, start: datetime, failure: Exception
    ) -> str:
        try:
            outcome = self._follow_the_consumers(value, replaced, start)
        except ContractError as error:
            outcome = f"setting the role failed too ({_described(error)})"
        return (
            f"Coolify took no variable ({_described(failure)}); {outcome}; the variable may still "
            "hold the replaced value: run rotation-worker pg-restore once Coolify returns"
        )

    def _follow_the_consumers(self, value: Value, replaced: Value | None, start: datetime) -> str:
        prints = self._read_loaded()
        if prints is None:
            return UNREADABLE
        if _all_on(prints, value):
            self._set(value, start)
            return "every consumer is on the value, and the role is too"
        if _agree_on_another(prints):
            return "every consumer is on another value, so the role stays with them"
        deadline = start + LOADED_WAIT
        while self._clock() < deadline:
            self._sleep(POLL_SECONDS)
            prints = self._read_loaded()
            if prints is None:
                return UNREADABLE
            if _all_on(prints, value):
                self._set(value, start)
                return "every consumer came up on the value, and the role is too"
            if replaced is not None and _all_on(prints, replaced):
                self._set(replaced, start)
                return "every consumer came up on the replaced value, and the role followed"
        return "the consumers never agreed, so the role was left alone"

    def _read_loaded(self) -> dict[str, str] | None:
        try:
            return self._database().loaded(self._target)
        except ContractError:
            return None

    def _loaded_with(self, value: Value) -> bool:
        return _all_on(self._database().loaded(self._target), value)

    def _healthy(self) -> bool:
        try:
            return self._health.request("GET", self._health_path).status_code == 200
        except TransportFailure:
            return False

    def _undo(
        self, old: Value, new: Value, failure: Exception, *, only_if_changed: bool = False
    ) -> NoReturn:
        reason = _described(failure)
        try:
            self._restore(old, new, only_if_changed=only_if_changed)
        except Exception as undo_failure:
            raise ContractError(
                f"host failed ({reason}) and restoring the old value failed too "
                f"({_described(undo_failure)}): run rotation-worker pg-restore"
            ) from None
        raise ContractError(f"host failed ({reason}); the old value is restored") from None


def _all_on(prints: Mapping[str, str], value: Value) -> bool:
    # An empty answer names no consumer, which isn't every consumer agreeing.
    return bool(prints) and all(_same(print_, value) for print_ in prints.values())


def _agree_on_another(prints: Mapping[str, str]) -> bool:
    """Whether every consumer shows one value. `_all_on` has already ruled out the restored one."""
    return len(set(prints.values())) == 1 and "" not in prints.values()


def _same(print_: str, value: Value) -> bool:
    """Whether a `<prefix>:<length>` print names `value`. A blank print names nothing."""
    prefix, _, length = print_.partition(":")
    return bool(length) and value.fingerprint.matches(Fingerprint(prefix, int(length)))


def _described(error: Exception) -> str:
    return scrub(str(error)) if isinstance(error, ContractError) else type(error).__name__
