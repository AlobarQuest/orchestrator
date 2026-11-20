"""The secrets broker's allowlist lists exactly what each lane's launcher reads, and every lane
without a token is on it and mounts its socket (ADR-0055 amendment 30).

A lane's reads are the full UUID literals on its launcher's code lines (comments skipped,
conditional reads included) and the ping-URL secret its compose names in
`SDS_DEADMAN_PING_URL_UUID`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts import deploy_orchestrator as deploy
from scripts.secrets_broker import load_allowlist

ALLOWLIST = Path("lanes/secrets-broker/allowlist.toml")
BROKER = "secrets-broker"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Per lane, any UUID literal on a launcher's code line that isn't a secret it reads. None today.
NOT_SECRETS: dict[str, frozenset[str]] = {}


def _service(lane: str) -> dict:
    document = yaml.safe_load(Path(f"lanes/{lane}/compose.yaml").read_text())
    return document["services"][lane]


def _launcher_reads(lane: str) -> set[str]:
    code = (
        line
        for line in Path(f"scripts/run-{lane}.sh").read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    literals = {uuid for line in code for uuid in UUID.findall(line)}
    return literals - NOT_SECRETS.get(lane, frozenset())


def _compose_reads(lane: str) -> set[str]:
    ping_url_secret = _service(lane).get("environment", {}).get("SDS_DEADMAN_PING_URL_UUID")
    return {ping_url_secret} if ping_url_secret else set()


def test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads() -> None:
    allowlist = load_allowlist(ALLOWLIST)
    # The first lane cut over; without it this test would pass on an empty allowlist.
    assert "inert-landing" in allowlist
    for lane, read in allowlist.items():
        assert read == _launcher_reads(lane) | _compose_reads(lane), lane


def test_every_tokenless_lane_is_allowlisted_and_mounts_its_socket() -> None:
    allowlist = load_allowlist(ALLOWLIST)
    tokenless = set()
    for compose in sorted(Path("lanes").glob("*/compose.yaml")):
        lane = compose.parent.name
        if lane == BROKER:
            continue
        service = _service(lane)
        if "BWS_ACCESS_TOKEN" in (service.get("environment") or {}):
            continue
        tokenless.add(lane)
        assert lane in allowlist, lane
        assert f"/data/sds/broker/{lane}:/run/sds-broker:ro" in service.get("volumes", []), lane
    assert "inert-landing" in tokenless
    # Each allowlisted lane is a tokenless one: a lane with a token reads with it, not the socket.
    assert set(allowlist) == tokenless


def test_no_compose_mounts_the_broker_directory_beyond_its_own_lanes_bind() -> None:
    """Each lane's service may mount only `/data/sds/broker/<its lane>:/run/sds-broker:ro` (a token
    lane none), and no other service any of it: the socket a lane reaches is its identity. No
    other route reaches it either (`volumes_from`, a privileged service, the Docker socket, a
    named volume's device): the deploy check's own rule, over every tracked compose."""
    for compose in sorted(Path("lanes").glob("*/compose.yaml")):
        lane = compose.parent.name
        if lane == BROKER:
            continue
        assert deploy.compose_broker_refusals(compose.read_text(), lane) == [], lane
        services = yaml.safe_load(compose.read_text())["services"]
        for name, service in services.items():
            own = f"/data/sds/broker/{lane}:/run/sds-broker:ro"
            assert deploy.stray_broker_mounts(service, lane) == [], (lane, name)
            if name != lane:
                assert own not in (service.get("volumes") or []), (lane, name)
