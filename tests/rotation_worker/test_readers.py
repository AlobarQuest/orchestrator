"""The scheduled readers' two reads: each answers "switched" only for a run that started after the
store, and "not yet" for every known-bad input beside it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest

from rotation_worker.orchestrator_client import OrchestratorClient, OrchestratorError
from rotation_worker.readers import (
    CHECKS,
    HEALTHCHECKS_URL,
    LOOKBACK,
    SKEW,
    Answer,
    CheckRead,
    ConsumerReads,
    Healthchecks,
    ObservationRead,
    ReadError,
    ReadUnavailable,
    scheduled_reads,
)
from rotation_worker.registry import RegistryError
from rotation_worker.values import Value

STORED = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
NOW = STORED + timedelta(days=1)
KEY = "throwaway-healthchecks-key"
SWEEP = 'runtime-fetch repo="orchestrator" ref="scripts/run-activation-sweep.sh"'
BACKUP = 'runtime-fetch repo="vps-backup" ref="backup.sh"'


def _check(name: str = "sds-activation-sweep", **fields: Any) -> dict[str, Any]:
    started = STORED + timedelta(hours=1)
    check: dict[str, Any] = {
        "name": name,
        "status": "up",
        "started": False,
        "last_ping": (started + timedelta(seconds=7)).isoformat(),
        "last_duration": 7,
    }
    check.update(fields)
    return check


class HealthchecksServer:
    def __init__(self, *checks: dict[str, Any], status: int = 200) -> None:
        self.checks = list(checks)
        self.status = status
        self.requests: list[httpx.Request] = []
        self.keys_read = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"checks": self.checks})

    def client(self) -> Healthchecks:
        def key() -> Value:
            self.keys_read += 1
            return Value(KEY)

        return Healthchecks(key, httpx.MockTransport(self))


class Observations:
    """The orchestrator's observation listing, as `ConsumerReads` asks it."""

    def __init__(self, *observed_at: datetime, error: bool = False) -> None:
        self.observed_at = list(observed_at)
        self.error = error
        self.asked: list[tuple[str, str, datetime]] = []

    def observations(
        self, source_system: str, subject_reference: str, observed_from: datetime
    ) -> list[dict[str, Any]]:
        self.asked.append((source_system, subject_reference, observed_from))
        if self.error:
            raise OrchestratorError("no answer")
        return [{"observed_at": at.isoformat()} for at in self.observed_at if at >= observed_from]


def _reads(
    server: HealthchecksServer | None = None, observations: Observations | None = None
) -> ConsumerReads:
    return ConsumerReads(
        {SWEEP: CheckRead("sds-activation-sweep"), BACKUP: ObservationRead("recovery_floor", "x")},
        server.client() if server else None,
        cast(OrchestratorClient, observations or Observations()),
        clock=lambda: NOW,
    )


# ---- the registry fields ------------------------------------------------------------------------


def _credential(**consumer: Any) -> dict[str, Any]:
    row = {"kind": "runtime-fetch", "repo": "orchestrator", "ref": "scripts/run-x.sh", **consumer}
    return {"consumer": [{"kind": "bws-secret", "uuid": "u"}, row]}


def test_a_check_row_and_an_observation_row_each_name_their_read() -> None:
    assert scheduled_reads(_credential(healthchecks_check="sds-x")) == {
        'runtime-fetch repo="orchestrator" ref="scripts/run-x.sh"': CheckRead("sds-x")
    }
    observed = _credential(observation_source="recovery_floor", observation_subject="backup:a")
    assert list(scheduled_reads(observed).values()) == [
        ObservationRead("recovery_floor", "backup:a")
    ]


def test_a_row_without_a_read_has_none_and_a_note_is_not_one() -> None:
    assert scheduled_reads(_credential(note="Healthchecks check sds-x")) == {}


@pytest.mark.parametrize(
    "fields",
    [
        {"healthchecks_check": "sds-x", "observation_subject": "backup:a"},
        {"observation_subject": "backup:a"},
        {"observation_source": "recovery_floor"},
        {"healthchecks_check": ""},
        {"healthchecks_check": " sds-x"},
        {"healthchecks_check": 3},
    ],
)
def test_a_malformed_read_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(RegistryError):
        scheduled_reads(_credential(**fields))


def test_only_a_runtime_fetch_row_names_a_read() -> None:
    credential = {"consumer": [{"kind": "bws-secret", "uuid": "u", "healthchecks_check": "x"}]}
    with pytest.raises(RegistryError, match="runtime-fetch"):
        scheduled_reads(credential)


# ---- Healthchecks ------------------------------------------------------------------------------


def test_a_check_whose_last_run_started_after_the_store_is_switched() -> None:
    answer = _reads(HealthchecksServer(_check())).answer(SWEEP, STORED)
    assert answer.switched
    assert answer.last_run == STORED + timedelta(hours=1)


def test_a_run_that_started_before_the_store_and_ended_after_is_not_switched() -> None:
    straddling = _check(
        last_ping=(STORED + timedelta(minutes=10)).isoformat(), last_duration=20 * 60
    )
    answer = _reads(HealthchecksServer(straddling)).answer(SWEEP, STORED)
    assert not answer.switched
    assert answer.last_run == STORED - timedelta(minutes=10)


def test_a_check_whose_last_run_predates_the_store_is_not_switched() -> None:
    before = _check(last_ping=(STORED - timedelta(hours=20)).isoformat())
    assert not _reads(HealthchecksServer(before)).answer(SWEEP, STORED).switched


@pytest.mark.parametrize(
    ("offset", "switched"), [(SKEW, False), (SKEW + timedelta(seconds=1), True)]
)
def test_a_run_inside_the_skew_margin_waits(offset: timedelta, switched: bool) -> None:
    close = _check(last_ping=(STORED + offset + timedelta(seconds=7)).isoformat())
    assert _reads(HealthchecksServer(close)).answer(SWEEP, STORED).switched is switched


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "down"},
        {"status": "new"},
        {"status": "paused"},
        {"last_duration": None},
        {"last_duration": 0},
        {"last_duration": True},
        {"last_duration": "7"},
        {"last_ping": None},
    ],
)
def test_a_check_with_no_successful_timed_run_has_no_answer(fields: dict[str, Any]) -> None:
    answer = _reads(HealthchecksServer(_check(**fields))).answer(SWEEP, STORED)
    assert answer == Answer(False, None)


def test_a_check_that_reports_no_duration_has_no_answer() -> None:
    # Healthchecks leaves `last_duration` out when the last success followed no `/start`.
    check = _check()
    del check["last_duration"]
    assert _reads(HealthchecksServer(check)).answer(SWEEP, STORED) == Answer(False, None)


def test_a_check_whose_next_run_is_under_way_answers_from_its_last_finished_one() -> None:
    # A `/start` sets `started` and touches neither `last_ping` nor `last_duration`.
    assert _reads(HealthchecksServer(_check(started=True))).answer(SWEEP, STORED).switched
    before = _check(started=True, last_ping=(STORED - timedelta(hours=1)).isoformat())
    assert not _reads(HealthchecksServer(before)).answer(SWEEP, STORED).switched


def test_a_late_check_still_answers_from_its_last_success() -> None:
    assert _reads(HealthchecksServer(_check(status="grace"))).answer(SWEEP, STORED).switched


@pytest.mark.parametrize("checks", [(), (_check(), _check()), (_check(name="sds-activation"),)])
def test_a_check_name_that_isn_t_exactly_one_check_is_a_registry_fault(
    checks: tuple[dict[str, Any], ...],
) -> None:
    with pytest.raises(ReadError):
        _reads(HealthchecksServer(*checks)).answer(SWEEP, STORED)


@pytest.mark.parametrize("status", [401, 500])
def test_a_refused_listing_is_unavailable_not_a_fault(status: int) -> None:
    with pytest.raises(ReadUnavailable):
        _reads(HealthchecksServer(_check(), status=status)).answer(SWEEP, STORED)


def test_an_unreachable_healthchecks_is_unavailable() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    reads = ConsumerReads(
        {SWEEP: CheckRead("sds-activation-sweep")},
        Healthchecks(lambda: Value(KEY), httpx.MockTransport(down)),
        cast(OrchestratorClient, Observations()),
    )
    with pytest.raises(ReadUnavailable):
        reads.answer(SWEEP, STORED)


def test_no_configured_key_is_a_fault_not_a_wait() -> None:
    with pytest.raises(ReadError, match="no Healthchecks key"):
        _reads(None).answer(SWEEP, STORED)


def test_the_listing_is_the_one_request_and_the_key_is_read_only_when_asked() -> None:
    server = HealthchecksServer(_check())
    client = server.client()
    assert server.keys_read == 0
    client.last_run_start("sds-activation-sweep")
    [request] = server.requests
    assert (request.method, str(request.url)) == ("GET", HEALTHCHECKS_URL + CHECKS)
    assert request.headers["X-Api-Key"] == KEY
    assert request.headers["User-Agent"].startswith("rotation-worker/")
    assert server.keys_read == 1


# ---- observations ------------------------------------------------------------------------------


def test_an_observation_after_the_store_is_switched_and_the_query_is_bounded() -> None:
    observations = Observations(STORED - timedelta(days=1), STORED + timedelta(hours=20))
    answer = _reads(observations=observations).answer(BACKUP, STORED)
    assert answer.switched
    assert answer.last_run == STORED + timedelta(hours=20)
    assert observations.asked == [("recovery_floor", "x", NOW - LOOKBACK)]


def test_only_observations_before_the_store_are_not_switched() -> None:
    observations = Observations(STORED - timedelta(days=7), STORED - timedelta(seconds=1))
    assert not _reads(observations=observations).answer(BACKUP, STORED).switched


@pytest.mark.parametrize(
    ("offset", "switched"), [(SKEW, False), (SKEW + timedelta(seconds=1), True)]
)
def test_an_observation_inside_the_skew_margin_waits(offset: timedelta, switched: bool) -> None:
    observations = Observations(STORED + offset)
    assert _reads(observations=observations).answer(BACKUP, STORED).switched is switched


def test_a_subject_that_posted_nothing_in_the_lookback_is_a_registry_fault() -> None:
    with pytest.raises(ReadError, match="posted nothing in the last 8 days"):
        _reads(observations=Observations(NOW - LOOKBACK - timedelta(hours=1))).answer(
            BACKUP, STORED
        )


def test_an_old_store_is_judged_by_the_lookback_s_recent_posts() -> None:
    long_ago = NOW - timedelta(days=90)
    observations = Observations(NOW - timedelta(days=1))
    assert _reads(observations=observations).answer(BACKUP, long_ago).switched
    assert observations.asked == [("recovery_floor", "x", NOW - LOOKBACK)]


def test_an_unanswered_observation_read_is_unavailable() -> None:
    with pytest.raises(ReadUnavailable):
        _reads(observations=Observations(error=True)).answer(BACKUP, STORED)


def test_an_observation_without_a_zone_is_unavailable() -> None:
    class Naive(Observations):
        def observations(self, *args: Any) -> list[dict[str, Any]]:
            return [{"observed_at": "2026-10-16T06:00:00"}]

    with pytest.raises(ReadUnavailable):
        _reads(observations=Naive()).answer(BACKUP, STORED)


# ---- what the contract and the pass see --------------------------------------------------------


def test_switched_never_raises_and_a_read_that_can_t_be_made_isn_t_switched() -> None:
    reads = _reads(HealthchecksServer(status=500), Observations(error=True))
    assert reads.switched(SWEEP, STORED) is False
    assert reads.switched(BACKUP, STORED) is False
    faulty = _reads(HealthchecksServer(), Observations())
    assert faulty.switched(SWEEP, STORED) is False
    assert faulty.switched(BACKUP, STORED) is False


def test_pending_lists_unswitched_and_unreadable_readers() -> None:
    reads = _reads(HealthchecksServer(status=500), Observations(STORED + timedelta(hours=1)))
    assert reads.pending(STORED) == [SWEEP]
    assert _reads(HealthchecksServer(_check()), Observations(STORED)).pending(STORED) == [BACKUP]
    switched = _reads(HealthchecksServer(_check()), Observations(STORED + timedelta(hours=1)))
    assert switched.pending(STORED) == []


def test_pending_refuses_a_registry_fault() -> None:
    with pytest.raises(ReadError):
        _reads(HealthchecksServer(), Observations(STORED + timedelta(hours=1))).pending(STORED)


def test_covers_only_the_readers_the_registry_names() -> None:
    reads = _reads()
    assert reads.covers(SWEEP) and reads.covers(BACKUP)
    assert not reads.covers('runtime-fetch repo="orchestrator" ref="scripts/other.sh"')


@pytest.mark.parametrize(
    ("method", "path"), [("POST", CHECKS), ("GET", CHECKS + "abc/pings/"), ("GET", "/api/v3/")]
)
def test_any_other_healthchecks_request_is_refused_before_the_transport(
    method: str, path: str
) -> None:
    server = HealthchecksServer(_check())
    client = server.client()
    with pytest.raises(ReadError, match="may not"):
        client._connect().request(method, path)
    assert server.requests == []


def test_a_pass_lists_the_checks_once_and_remembers_each_answer() -> None:
    server = HealthchecksServer(_check(), _check(name="sds-other"))
    observations = Observations(STORED + timedelta(hours=1))
    reads = ConsumerReads(
        {
            SWEEP: CheckRead("sds-activation-sweep"),
            'runtime-fetch repo="orchestrator" ref="o.sh"': CheckRead("sds-other"),
            BACKUP: ObservationRead("recovery_floor", "x"),
        },
        server.client(),
        cast(OrchestratorClient, observations),
        clock=lambda: NOW,
    )
    assert reads.pending(STORED) == []
    # The listing then changes, as a reader starting its next run or failing would change it:
    # the pass keeps judging what it already read.
    server.checks = []
    observations.error = True
    assert reads.switched(SWEEP, STORED) and reads.switched(BACKUP, STORED)
    assert len(server.requests) == 1
    assert len(observations.asked) == 1
