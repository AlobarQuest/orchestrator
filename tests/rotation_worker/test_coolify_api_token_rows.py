"""The repository's three Coolify API token rows, read by the code that acts on them.

ADR-0055 amendment 33 and D-82. Each row declares its abilities, derives the person-mints,
person-revokes plan, and names a scheduled read for each reader, so the revoke waits on machine
evidence (R10, Infra Brain 1032). No row is `rotated_by_sds` yet: the change window's waits on the
dev Coolify measurement and the canary (D-82 items 14 and 13 C), and the other two on Devon moving
their keepers into `Rotation / Keepers` (item 11 A). What a green ping proves differs per row:

* **Change window:** the launcher's read is fatal, so a green `--submit` pass read the new value.
* **Revision watcher:** the launcher reads every pass and refuses to run outside its container, so
  a green pass read the new value.
* **Estate landing:** the read is not fatal and the check is pinged every pass, so a green pass
  proves only that a pass began after the keeper changed. That gap is carried (item 15 A).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rotation_proposer.generate import plan_for
from rotation_worker.adapters import ADAPTERS, coolify_api_token
from rotation_worker.checks import _check_human_retire_verifiable, _check_runtime_fetch_verifiable
from rotation_worker.git_mirror import RepoMirror
from rotation_worker.readers import CheckRead, scheduled_reads
from rotation_worker.registry import (
    REGISTRY_PATH,
    REGISTRY_REPOSITORIES,
    RegistryEntry,
    credentials,
)
from tests.rotation_worker.conftest import Origins
from tests.rotation_worker.stores import MemoryStore

ROOT = Path(__file__).resolve().parents[2]
EVERY = frozenset({"read", "write", "deploy"})
ROWS = {
    "coolify-estate-lander-token": (EVERY, "scripts/run-estate-landing.sh", "sds-estate-landing"),
    "coolify-revision-watcher-read-token": (
        frozenset({"read"}),
        "scripts/run-revision-watcher.sh",
        "sds-revision-watcher",
    ),
    "coolify-change-window-token": (
        EVERY,
        "scripts/run-change-window.sh",
        "Change Manager OPs Check",
    ),
}


@pytest.fixture
def rows(mirror: RepoMirror, origins: Origins) -> dict[str, RegistryEntry]:
    registry = (ROOT / REGISTRY_PATH).read_text()
    for repository in REGISTRY_REPOSITORIES:
        text = registry if repository == "AlobarQuest/orchestrator" else "version = 1\n"
        origins.commit(repository, {REGISTRY_PATH: text})
    return credentials(mirror)


@pytest.mark.parametrize("credential_id", sorted(ROWS))
def test_each_row_declares_its_abilities_and_derives_the_type_s_plan(
    rows: dict[str, RegistryEntry], credential_id: str
) -> None:
    entry = rows[credential_id]
    abilities, _, _ = ROWS[credential_id]
    coolify_api_token.check_row(entry.credential)
    assert coolify_api_token.declared_abilities(entry.credential) == abilities
    assert entry.credential["provider"] == ADAPTERS[coolify_api_token.NAME].provider_name
    adapter_name, plan = plan_for(entry)
    assert adapter_name == coolify_api_token.NAME
    assert plan == coolify_api_token.PLAN


@pytest.mark.parametrize("credential_id", sorted(ROWS))
def test_each_reader_is_machine_read_so_the_revoke_waits_on_evidence(
    rows: dict[str, RegistryEntry], credential_id: str
) -> None:
    entry = rows[credential_id]
    _, launcher, check = ROWS[credential_id]
    reads = scheduled_reads(entry.credential)
    readers = [d for d in entry.destinations if d.startswith("runtime-fetch ")]
    assert len(readers) == 1
    assert reads[readers[0]] == CheckRead(check)
    assert launcher in readers[0]
    _check_runtime_fetch_verifiable(entry.destinations, coolify_api_token.PLAN, reads)
    _check_human_retire_verifiable(entry.destinations, coolify_api_token.PLAN, reads, MemoryStore())


@pytest.mark.parametrize("credential_id", sorted(ROWS))
def test_no_row_is_handed_to_the_sds_yet(
    rows: dict[str, RegistryEntry], credential_id: str
) -> None:
    entry = rows[credential_id]
    assert not entry.rotated_by_sds
    assert "rotate_requested" not in entry.credential


@pytest.mark.parametrize("credential_id", sorted(ROWS))
def test_no_note_names_a_mac_reader(rows: dict[str, RegistryEntry], credential_id: str) -> None:
    """No scheduled job on the Mac runs these launchers, and a reader run only when invoked is a
    human-attested exception a derived plan refuses (amendment 33 item 5)."""
    for consumer in rows[credential_id].credential["consumer"]:
        assert "Mac" not in consumer.get("note", "")


def test_the_estate_lander_row_carries_the_read_proof_gap(
    rows: dict[str, RegistryEntry],
) -> None:
    [reader] = [
        c
        for c in rows["coolify-estate-lander-token"].credential["consumer"]
        if c["kind"] == "runtime-fetch"
    ]
    assert "not fatal" in reader["note"]
    assert "D-82 item 15 A" in reader["note"]


def test_what_a_green_pass_proves_follows_each_launcher_s_read() -> None:
    """The change window and the revision watcher fail a pass whose read came back empty, so a
    green pass read the new value; the estate lander carries on without it."""
    change_window = (ROOT / "scripts" / "run-change-window.sh").read_text()
    assert (
        'COOLIFY_API_TOKEN="$(sds_bws_value "$COOLIFY_WINDOW_TOKEN_UUID")"\n'
        '  [ -n "$COOLIFY_API_TOKEN" ] || sds_outcome_fail'
    ) in change_window
    revision_watcher = (ROOT / "scripts" / "run-revision-watcher.sh").read_text()
    assert (
        'if [ -z "$REVISION_WATCHER_PLATFORM_TOKEN" ]; then\n'
        '  echo "FATAL: could not read the platform token'
    ) in revision_watcher
    estate_landing = (ROOT / "scripts" / "run-estate-landing.sh").read_text()
    unreadable = 'if [ -z "$ESTATE_LANDING_COOLIFY_TOKEN" ]; then'
    assert estate_landing.count(unreadable) == 1
    branch = estate_landing.split(unreadable, 1)[1].split("else", 1)[0]
    assert "re-points are refused" in branch
    assert "exit" not in branch
    assert "fail" not in branch
