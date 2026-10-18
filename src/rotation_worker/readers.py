"""Whether each scheduled reader of a keeper has run since the new value was stored.

ADR-0055 amendment 9, decision 5 and its section "Decision 5 for the drift-reporter's readers". A
`runtime-fetch` reader on a schedule fetches the keeper at the start of every run and keeps no copy,
so a run that started after the store used the new value. Neither read below can tell the old
value from the new, because the orchestrator accepts both during the overlap and records neither;
they rest on that one assumption, confirmed for every reader before the reads were built.

**WHAT A REGISTRY ROW CARRIES.** A scheduled reader's row names its read in parsed fields beside
`note`, never in the destination string, so the format pinned with intent-packages is unchanged:

* `healthchecks_check`: the exact name of the reader's Healthchecks check. The reader pings
  `/start` before it fetches the keeper, and exits non-zero (pinging `/fail`) on an empty value.
* `observation_source` and `observation_subject`: the `source_system` and `subject_reference` of
  the observation the reader posts each run. Its `observed_at` is taken before the fetch. Its
  Healthchecks success proves nothing, because its helper swallows a failed fetch or post.

These fields are read from the registry alone, which merges without review, and the three-way
destination comparison doesn't cover them. A wrong name can make a reader look switched early;
it can't touch a value.

**WHAT COUNTS AS SWITCHED.** A run that started more than `SKEW` after the keeper's revision date.
Three clocks meet here (BWS's, Healthchecks' and the reader's machine), so the margin sits on the
side that waits. For a check, the latest finished run's start is `last_ping - last_duration`,
which Healthchecks (`hc/api/models.py`, `Check.ping`, at b932fc4) computes only for a success or
failure that follows its own `/start`, resetting it otherwise; a `/start` touches neither field,
so a check whose next run is under way still describes its last finished one. A check that is
down (its latest run failed) or reports no duration has no answer yet.

**EACH READ IS MADE ONCE PER PASS.** A `ConsumerReads` lives for one unit in one pass, and keeps
every answer it got. So the wait before the claim, verify-consumers and retire-old all judge the
same readings, taken seconds apart, and a reader starting its next run, or a request timing out,
between the wait and the claim can't fail a unit the wait had cleared. Retire-old still reads
afresh relative to earlier attempts: nothing outlives the pass.

**A READ THAT CAN'T BE MADE NEVER COUNTS AS SWITCHED.** `ReadUnavailable` (no answer, no key) is
worth asking again; `ReadError` (no such check, two of them, a subject with nothing posted in the
last `LOOKBACK`, no Healthchecks key configured) is a fault in the registry or the executor's
configuration, refused before a claim rather than waited on forever.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx

from estate_clients.confined import ConfinedClient, TransportFailure
from rotation_worker.destinations import render
from rotation_worker.orchestrator_client import (
    TIMEOUT_SECONDS,
    USER_AGENT,
    OrchestratorClient,
    OrchestratorError,
)
from rotation_worker.registry import RegistryError
from rotation_worker.values import Value

HEALTHCHECKS_URL: Final = "https://healthchecks.io"
CHECKS: Final = "/api/v3/checks/"
SKEW: Final = timedelta(minutes=2)
# Long enough to hold a weekly reader's latest run after it has missed one.
LOOKBACK: Final = timedelta(days=15)
CHECK_FIELD: Final = "healthchecks_check"
SOURCE_FIELD: Final = "observation_source"
SUBJECT_FIELD: Final = "observation_subject"
READ_FIELDS: Final = (CHECK_FIELD, SOURCE_FIELD, SUBJECT_FIELD)
# A check's last ping was a success: `grace` is a success whose next run is late.
SUCCEEDED: Final = frozenset({"up", "grace"})


class ReadError(RuntimeError):
    """The registry names a reader that can't be found. Refused, never waited on."""


class ReadUnavailable(RuntimeError):
    """The read couldn't be made this time. Asked again on the next pass."""


@dataclass(frozen=True)
class CheckRead:
    check: str


@dataclass(frozen=True)
class ObservationRead:
    source_system: str
    subject_reference: str


type Read = CheckRead | ObservationRead


@dataclass(frozen=True)
class Answer:
    switched: bool
    # The start of the latest run the read found, if it found one.
    last_run: datetime | None


def scheduled_reads(credential: Mapping[str, Any]) -> dict[str, Read]:
    """Each scheduled reader's read, keyed by its destination string."""
    reads: dict[str, Read] = {}
    for consumer in credential.get("consumer", []):
        fields = {name: consumer[name] for name in READ_FIELDS if name in consumer}
        if not fields:
            continue
        if consumer.get("kind") != "runtime-fetch":
            raise RegistryError("only a runtime-fetch consumer names a scheduled read")
        if not all(isinstance(v, str) and v.strip() == v and v for v in fields.values()):
            raise RegistryError("a scheduled read's fields are non-blank, trimmed strings")
        if set(fields) == {CHECK_FIELD}:
            reads[render(consumer)] = CheckRead(fields[CHECK_FIELD])
        elif set(fields) == {SOURCE_FIELD, SUBJECT_FIELD}:
            reads[render(consumer)] = ObservationRead(fields[SOURCE_FIELD], fields[SUBJECT_FIELD])
        else:
            raise RegistryError(
                f"a scheduled read is {CHECK_FIELD}, or {SOURCE_FIELD} with {SUBJECT_FIELD}"
            )
    return reads


class Healthchecks:
    """The check listing, read with a read-only key fetched only when a read needs it."""

    def __init__(
        self, key: Callable[[], Value], transport: httpx.BaseTransport | None = None
    ) -> None:
        self._key = key
        self._transport = transport
        self._client: ConfinedClient | None = None
        self._listing: list[dict[str, Any]] | None = None

    def last_run_start(self, name: str) -> datetime | None:
        """When the check's latest successful run started, or None if it has no answer yet."""
        matches = [check for check in self._checks() if check.get("name") == name]
        if len(matches) != 1:
            raise ReadError(f"{len(matches)} Healthchecks checks are named {name!r}")
        check = matches[0]
        last, duration = check.get("last_ping"), check.get("last_duration")
        if check.get("status") not in SUCCEEDED:
            return None
        if type(duration) is not int or duration <= 0 or not isinstance(last, str):
            return None
        try:
            return instant(last) - timedelta(seconds=duration)
        except ValueError:
            raise ReadUnavailable(f"check {name!r} has no readable last_ping") from None

    def _checks(self) -> list[dict[str, Any]]:
        # One listing per pass: every reader's check is in it.
        if self._listing is None:
            self._listing = self._list()
        return self._listing

    def _list(self) -> list[dict[str, Any]]:
        try:
            response = self._connect().request("GET", CHECKS)
        except TransportFailure as failure:
            raise ReadUnavailable(f"Healthchecks gave no answer: {failure.error_type}") from None
        if response.status_code != httpx.codes.OK:
            raise ReadUnavailable(f"Healthchecks answered {response.status_code}")
        try:
            checks = response.json()["checks"]
        except ValueError, KeyError, TypeError:
            raise ReadUnavailable("Healthchecks' listing isn't a list of checks") from None
        if not isinstance(checks, list):
            raise ReadUnavailable("Healthchecks' listing isn't a list of checks")
        return [check for check in checks if isinstance(check, dict)]

    def _connect(self) -> ConfinedClient:
        if self._client is None:
            self._client = ConfinedClient(
                base_url=HEALTHCHECKS_URL,
                user_agent=USER_AGENT,
                headers={"X-Api-Key": self._key().reveal()},
                timeout=TIMEOUT_SECONDS,
                permits=lambda method, path: (method, path) == ("GET", CHECKS),
                refuse=lambda method, path: ReadError(f"may not {method} {path}"),
                transport=self._transport,
            )
        return self._client


class ConsumerReads:
    """The scheduled readers of one rotation, and whether each has run since the store."""

    def __init__(
        self,
        reads: Mapping[str, Read],
        healthchecks: Healthchecks | None,
        orchestrator: OrchestratorClient,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._reads = dict(reads)
        self._healthchecks = healthchecks
        self._orchestrator = orchestrator
        self._clock = clock
        self._answers: dict[tuple[str, datetime], Answer] = {}

    def covers(self, destination: str) -> bool:
        return destination in self._reads

    def switched(self, destination: str, stored_at: datetime) -> bool:
        """For the contract: never raises, and a read that can't be made isn't switched."""
        try:
            return self.answer(destination, stored_at).switched
        except ReadError, ReadUnavailable:
            return False

    def pending(self, stored_at: datetime) -> list[str]:
        """Each reader not yet switched, an unreadable one included. Raises `ReadError`."""
        waiting = []
        for destination in sorted(self._reads):
            try:
                if not self.answer(destination, stored_at).switched:
                    waiting.append(destination)
            except ReadUnavailable:
                waiting.append(destination)
        return waiting

    def answer(self, destination: str, stored_at: datetime) -> Answer:
        """Whether the reader has run since `stored_at`; asked once per pass, then remembered."""
        key = (destination, stored_at)
        if key not in self._answers:
            self._answers[key] = self._read(self._reads[destination], stored_at + SKEW)
        return self._answers[key]

    def _read(self, read: Read, after: datetime) -> Answer:
        if isinstance(read, CheckRead):
            if self._healthchecks is None:
                raise ReadError("a reader names a check and no Healthchecks key is configured")
            start = self._healthchecks.last_run_start(read.check)
            return Answer(start is not None and start > after, start)
        # Every run since the store is newer than the window, and a reader that posted nothing in
        # it isn't running at all.
        starts = self._observed(read, self._clock() - LOOKBACK)
        if not starts:
            raise ReadError(
                f"{read.subject_reference} posted nothing in the last {LOOKBACK.days} days"
            )
        latest = max(starts)
        return Answer(latest > after, latest)

    def _observed(self, read: ObservationRead, since: datetime) -> list[datetime]:
        try:
            rows = self._orchestrator.observations(
                read.source_system, read.subject_reference, since
            )
        except OrchestratorError as error:
            raise ReadUnavailable(str(error)) from None
        try:
            return [instant(str(row["observed_at"])) for row in rows]
        except KeyError, TypeError, ValueError:
            raise ReadUnavailable("an observation has no readable observed_at") from None


def instant(text: str) -> datetime:
    """An ISO 8601 instant, refused without a zone."""
    instant = datetime.fromisoformat(text)
    if instant.utcoffset() is None:
        raise ValueError("an instant without a zone")
    return instant
