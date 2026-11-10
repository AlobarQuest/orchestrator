"""ADR-0055 amendment 23: the executor reads a lane check's ping URL from the Healthchecks API.

Before the claim, the executor reads the Management key's check listing and takes the `ping_url`
of the one check the registry row names. No answer waits unclaimed; a read that can't succeed is
refused before the claim; the claimed mint takes the URL from the same pass's listing, so nothing
is read after the claim; and no log line holds the URL.
"""

from __future__ import annotations

import json
import logging
import textwrap
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

import rotation_worker.worker as worker
from rotation_worker import cli
from rotation_worker.adapters import ADAPTERS
from rotation_worker.adapters import healthchecks_ping_url as ping
from rotation_worker.checks import Approved
from rotation_worker.config import ConfigError, load
from rotation_worker.contract import EMPTY, ContractError, Rotation, StepKind
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.orchestrator_client import OrchestratorClient
from rotation_worker.readers import CHECKS, HEALTHCHECKS_URL, PingUrls, ReadError, _permits_listing
from rotation_worker.registry import RegistryEntry
from rotation_worker.secret_store import StoreError
from rotation_worker.values import Value
from rotation_worker.worker import PassActs, Setup, run_pass
from tests.rotation_worker.stores import MemoryStore
from tests.rotation_worker.test_config_logs_store import CONFIG, PROJECT
from tests.rotation_worker.test_lane_credentials import HC_KEY, HealthchecksStub
from tests.rotation_worker.test_worker import FakeClient, Harness

WORK = "work"
CHECK = "sds-example-lane"
# Placeholders, and no real check. The read-only lookup's stub answers 200 for this URL's
# `unique_key`, since its UUID's first half is the lane-credentials suite's `PING_URL`'s; its
# second half is the marker a log search looks for.
MARKER = "8000-00000000beef"
PING_UUID = f"00000000-0000-4000-{MARKER}"
PING_URL = f"{ping.PING_BASE}{PING_UUID}"
OTHER_URL = f"{ping.PING_BASE}11111111-1111-4111-8111-111111111111"
MANAGEMENT_KEY = Value("hc-management-key-placeholder")
# What the listing serves beside each `ping_url`, which parsing must drop.
UPDATE_URL = "https://healthcheck.alobar.net/api/v3/checks/update-url-placeholder"


def _check(name: str, url: str | None = PING_URL) -> dict[str, Any]:
    check: dict[str, Any] = {"name": name, "update_url": UPDATE_URL, "status": "up"}
    if url is not None:
        check["ping_url"] = url
    return check


class Listing:
    """The Management key's listing behind a mock transport: `checks`, answered with `status`,
    or every request raising `error`. Records every request."""

    def __init__(
        self,
        checks: list[dict[str, Any]] | None = None,
        status: int = 200,
        error: Exception | None = None,
    ) -> None:
        self.checks = (
            [_check(CHECK), _check("sds-other-lane", OTHER_URL)] if checks is None else checks
        )
        self.status, self.error = status, error
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status, json={"checks": self.checks})

    def client(self, key: Any = None) -> PingUrls:
        return PingUrls(key or (lambda: MANAGEMENT_KEY), transport=httpx.MockTransport(self))


class Unit:
    """A ping URL's issue unit, through `run_pass` with the real adapter and contract: the checks
    and the registry read are replaced, and the keeper holds the inbox's marker."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.store = MemoryStore()
        self.keeper = self.store.put("keepers", "keeper", Value(EMPTY))
        destinations = (f'bws-secret uuid="{self.keeper}"', 'provider name="healthchecks-ping-url"')
        self.rotation = Rotation(
            "example-lane-ping-url", "2026-11-10-requested", self.keeper, WORK, destinations
        )
        self.row: dict[str, Any] = {
            "id": "example-lane-ping-url",
            "provider": "healthchecks-ping-url",
            "rotated_by_sds": True,
            "healthchecks_check": CHECK,
        }
        self.others: list[tuple[str, Value]] = []
        self.client = FakeClient({})
        self.client.units["u1"] = {
            "id": "u1",
            "version": 4,
            "work_package_revision_id": "revision",
            "authority": {
                "constraints": {
                    "rotation_adapter": "healthchecks-ping-url",
                    "rotation_unit": "store",
                }
            },
        }
        self.listing = Listing()
        self.lookups = HealthchecksStub()
        self.state = tmp_path
        monkeypatch.setattr(worker, "check_unit", self._approved)
        monkeypatch.setattr(worker, "find_credential", lambda _mirror, _id: self._entry())
        monkeypatch.setattr(worker, "_other_keepers", lambda _setup, _approved: self.others)

    def _entry(self) -> RegistryEntry:
        return RegistryEntry("AlobarQuest/orchestrator", "c" * 40, self.row)

    def _approved(self, *_arguments: Any) -> Approved:
        adapter = ADAPTERS["healthchecks-ping-url"]
        plan = adapter.plan
        return Approved(adapter, plan, plan.unit("store"), self.rotation, self._entry(), {})

    def setup(self, *, ping_urls: PingUrls | None | str = "listing") -> Setup:
        return Setup(
            client=cast(OrchestratorClient, self.client),
            # Never fetched: the checks and the registry reads are replaced.
            mirror=RepoMirror(self.state / "mirrors"),
            store=self.store,
            work_project_id=WORK,
            state_dir=self.state,
            source_revision="rotation-worker@0123456789ab",
            healthchecks=self.lookups.client(),
            ping_urls=self.listing.client() if ping_urls == "listing" else cast(Any, ping_urls),
        )

    def take(self, setup: Setup | None = None) -> PassActs:
        found = PassActs()
        worker._take(setup or self.setup(), "u1", found)
        return found

    def calls(self) -> list[str]:
        return [call for call, _ in self.client.calls]


@pytest.fixture
def unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Unit:
    return Unit(tmp_path, monkeypatch)


# ---- the plan and the package ----------------------------------------------------------------


def test_a_ping_url_issue_is_one_machine_unit_that_mints_by_reading() -> None:
    plan = ADAPTERS["healthchecks-ping-url"].plan
    (segment,) = plan.segments
    assert segment.name == "store"
    assert [step.kind for step in segment.steps] == [
        StepKind.MINT,
        StepKind.STAGE,
        StepKind.VERIFY_NEW,
        StepKind.STORE,
        StepKind.VERIFY_CONSUMERS,
    ]


# ---- the read before the claim --------------------------------------------------------------


def test_the_url_is_read_before_the_claim_and_stored_with_no_second_listing(unit: Unit) -> None:
    assert run_pass(unit.setup()) == 1
    assert unit.store.read(unit.keeper).reveal() == PING_URL
    assert [(r.method, str(r.url)) for r in unit.listing.requests] == [
        ("GET", f"{HEALTHCHECKS_URL}{CHECKS}")
    ]
    request = unit.listing.requests[0]
    assert request.url.scheme == "https"
    assert request.headers["X-Api-Key"] == MANAGEMENT_KEY.reveal()
    steps = [body["payload"]["step"] for body in unit.client.evidence_bodies]
    assert steps == ["mint", "stage", "verify_new", "store", "verify_consumers", "verify_consumers"]
    assert unit.calls()[:2] == ["claim", "start"]


def test_the_claimed_mint_reads_nothing_after_the_claim(
    unit: Unit, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills: a mint that lists the checks again once the unit is claimed."""
    listed_at_claim: list[int] = []
    claim = unit.client.claim

    def claimed(*arguments: Any) -> Any:
        listed_at_claim.append(len(unit.listing.requests))
        return claim(*arguments)

    monkeypatch.setattr(unit.client, "claim", claimed)
    assert run_pass(unit.setup()) == 1
    assert listed_at_claim == [1]
    assert len(unit.listing.requests) == 1


@pytest.mark.parametrize(
    "listing",
    [
        Listing(status=503),
        Listing(status=401),
        Listing(error=httpx.ConnectError("unreachable")),
        Listing(checks=cast(Any, "not a list")),
    ],
    ids=["unavailable", "unauthorized", "no-answer", "unparseable"],
)
def test_a_listing_that_gives_no_answer_waits_unclaimed_on_the_ordinary_clock(
    unit: Unit, listing: Listing
) -> None:
    unit.listing = listing
    found = unit.take()
    assert unit.client.calls == []
    wait = found.waits["u1"]
    assert not wait.reader and not wait.stuck and wait.hours == 48
    assert CHECK in wait.reason
    assert unit.store.read(unit.keeper).reveal() == EMPTY


def test_a_key_the_store_won_t_give_waits_unclaimed(unit: Unit) -> None:
    def refused() -> Value:
        raise StoreError("the root holds no grant on the key")

    found = unit.take(unit.setup(ping_urls=unit.listing.client(refused)))
    assert unit.client.calls == []
    assert "no grant" in found.waits["u1"].reason
    assert unit.listing.requests == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"ping_urls": None}, "no Healthchecks Management key configured"),
        ({"checks": []}, "0 Healthchecks checks are named 'sds-example-lane'"),
        ({"checks": [_check(CHECK), _check(CHECK)]}, "2 Healthchecks checks are named"),
        ({"checks": [_check(CHECK, url=None)]}, "listed without a ping_url"),
        (
            {
                "checks": [
                    _check(CHECK, url="https://hc-ping.com/00000000-0000-4000-8000-00000000beef")
                ]
            },
            "isn't the self-hosted ping base",
        ),
        ({"row": {"healthchecks_check": None}}, "names no check"),
        # The reason's tail is left out: `scrub` redacts any text a test has made a `Value`.
        ({"others": [("another-lane-ping-url", Value(PING_URL))]}, "is another-lane-ping-url"),
    ],
    ids=["no-key", "no-check", "two-checks", "no-url", "wrong-shape", "no-field", "another-keeper"],
)
def test_a_read_that_can_t_succeed_is_refused_before_the_claim(
    unit: Unit, change: dict[str, Any], reason: str
) -> None:
    if "checks" in change:
        unit.listing = Listing(checks=change["checks"])
    if "row" in change:
        unit.row = {key: v for key, v in unit.row.items() if key != "healthchecks_check"}
    if "others" in change:
        unit.others = change["others"]
    setup = unit.setup(ping_urls=None) if "ping_urls" in change else unit.setup()
    found = unit.take(setup)
    assert unit.calls() == ["claim", "fail"]
    assert reason in unit.client.failures["u1"]
    assert PING_URL not in unit.client.failures["u1"]
    assert unit.store.read(unit.keeper).reveal() == EMPTY
    assert "u1" not in found.waits


def test_a_row_naming_no_check_is_refused_at_the_checks() -> None:
    row = {
        "provider": "healthchecks-ping-url",
        "consumer": [{"kind": "bws-secret", "uuid": "11111111-2222-4333-8444-555555555555"}],
    }
    with pytest.raises(ContractError, match="names its check in healthchecks_check"):
        ping.check_row(row)
    for bad in ("", " sds-example-lane", 7):
        with pytest.raises(ContractError, match="healthchecks_check"):
            ping.check_row({**row, "healthchecks_check": bad})
    ping.check_row({**row, "healthchecks_check": CHECK})


def test_another_type_s_unit_never_reads_the_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path, monkeypatch)
    h.add("u1", "mint")
    listing = Listing()
    assert run_pass(replace(h.setup(), ping_urls=listing.client())) == 1
    assert listing.requests == []


# ---- the Management key's client ------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", CHECKS),
        ("DELETE", CHECKS),
        ("GET", CHECKS + "07c99f22ce44ac16c776ebcecbb911799bcc675c"),
        ("GET", CHECKS + "00000000-0000-4000-8000-00000000beef"),
        ("POST", CHECKS + "00000000-0000-4000-8000-00000000beef/pause"),
        ("GET", "/api/v3/channels/"),
        ("GET", "/ping/00000000-0000-4000-8000-00000000beef"),
        ("GET", CHECKS.rstrip("/")),
    ],
)
def test_the_management_client_makes_only_the_listing(method: str, path: str) -> None:
    assert not _permits_listing(method, path)
    assert _permits_listing("GET", CHECKS)


def test_parsing_keeps_only_each_check_s_name_and_url() -> None:
    """Kills: a client that keeps the listing's other fields, other checks' URLs among them."""
    listing = Listing()
    client = listing.client()
    assert client.ping_url(CHECK).reveal() == PING_URL
    kept = json.dumps(vars(client), default=repr)
    for text in (PING_URL, OTHER_URL, UPDATE_URL, "status"):
        assert text not in kept
    assert client.ping_url("sds-other-lane").reveal() == OTHER_URL
    assert len(listing.requests) == 1


def test_the_key_is_read_only_when_the_listing_is() -> None:
    reads: list[int] = []

    def key() -> Value:
        reads.append(1)
        return MANAGEMENT_KEY

    client = Listing().client(key)
    assert reads == []
    client.ping_url(CHECK)
    client.ping_url(CHECK)
    assert reads == [1]


# ---- what is logged -------------------------------------------------------------------------


def _logged(caplog: pytest.LogCaptureFixture) -> str:
    return json.dumps([vars(record) for record in caplog.records], default=str)


def test_no_log_record_holds_the_url_or_the_key(
    unit: Unit, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Code Brain lesson 6: an HTTP library can log a full request URL at INFO."""
    # A test that ran alembic's `fileConfig` on this xdist worker disabled the existing loggers.
    for name in ("rotation_worker", "httpx"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    # The executor's own configuration raises httpx to WARNING (`logs.configure`); this proves the
    # URL is absent even with that guard off.
    caplog.set_level(logging.INFO, logger="httpx")
    with caplog.at_level(logging.INFO):
        assert run_pass(unit.setup()) == 1
    (read,) = [r for r in caplog.records if r.getMessage() == "ping_url_read"]
    assert vars(read)["check"] == CHECK and vars(read)["unit"] == "u1"
    assert vars(read)["fingerprint"] == str(Value(PING_URL).fingerprint)
    logged = _logged(caplog)
    assert any(r.name.startswith("httpx") for r in caplog.records)
    for text in (PING_URL, PING_UUID, MARKER, OTHER_URL, MANAGEMENT_KEY.reveal(), HC_KEY.reveal()):
        assert text not in logged, text


@pytest.mark.parametrize(
    ("listing", "error_type"),
    [(Listing(status=503), "ReadUnavailable"), (Listing(checks=[]), "ReadError")],
)
def test_an_unreadable_url_is_logged_by_unit_check_and_error_type(
    unit: Unit,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    listing: Listing,
    error_type: str,
) -> None:
    monkeypatch.setattr(logging.getLogger("rotation_worker"), "disabled", False)
    unit.listing = listing
    with caplog.at_level(logging.INFO):
        unit.take()
    (record,) = [r for r in caplog.records if r.getMessage() == "ping_url_unreadable"]
    assert (vars(record)["unit"], vars(record)["check"]) == ("u1", CHECK)
    assert vars(record)["error_type"] == error_type
    assert PING_URL not in _logged(caplog)


def test_a_read_error_is_never_a_pass(unit: Unit) -> None:
    """Code Brain rule 4: a failed read waits or refuses, and never reaches the claim's work."""
    for listing in (Listing(status=500), Listing(checks=[])):
        unit.client.calls.clear()
        unit.listing = listing
        unit.take()
        assert "start" not in unit.calls()
    assert unit.store.find(WORK, unit.rotation.staging_key) is None
    with pytest.raises(ReadError):
        Listing(checks=[]).client().ping_url(CHECK)


# ---- the configuration ----------------------------------------------------------------------


def test_the_vm_names_the_management_key_by_its_bws_id(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG)
    assert load(path).healthchecks_management_key_id is None
    path.write_text(CONFIG + f'healthchecks_management_key_id = "{PROJECT}"\n')
    assert load(path).healthchecks_management_key_id == PROJECT
    path.write_text(CONFIG + 'healthchecks_management_key_id = "Ops / Platform"\n')
    with pytest.raises(ConfigError, match="lowercase UUID"):
        load(path)


def test_the_keychain_instance_names_no_management_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        textwrap.dedent(
            f"""
            store = "keychain"
            orchestrator_url = "https://sds.example"
            credential_key_id = "rotation-executor-keychain-1"
            work_project_id = "rotation-worker"
            state_dir = "/tmp/rotation-worker"
            source_revision = "0123456789ab"
            healthchecks_management_key_id = "{PROJECT}"
            """
        )
    )
    with pytest.raises(ConfigError, match="names no healthchecks_management_key_id"):
        load(path)


def test_the_listing_client_is_built_only_with_a_configured_key(tmp_path: Path) -> None:
    store = MemoryStore()
    key_id = store.put("ops-platform", "healthchecks_selfhosted_api_key", MANAGEMENT_KEY)
    path = tmp_path / "config.toml"
    path.write_text(CONFIG)
    assert cli.open_ping_urls(load(path), store) is None
    path.write_text(CONFIG + f'healthchecks_management_key_id = "{PROJECT}"\n')
    configured = replace(load(path), healthchecks_management_key_id=key_id)
    assert isinstance(cli.open_ping_urls(configured, store), PingUrls)
