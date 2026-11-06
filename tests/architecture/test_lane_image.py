"""The lane image and the placeholder lane's compose hold the shape ADR-0059 increment 1 sets.

What a build proves (the tools run, the state directories are owned by `orchestrator`, supercronic
keeps a comment-only crontab running) is in the increment's build report. These tests hold the
tracked files to the rules a build can't show: every download is checked against a digest, no
tool floats, the inherited health check and `CMD` are replaced, and the placeholder's compose maps
nothing beyond its two variables.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts import lane_run

ROOT = Path(__file__).resolve().parents[2]
LANES = ROOT / "lanes"
DOCKERFILE = LANES / "Dockerfile"
ENTRYPOINT = LANES / "entrypoint.sh"
PLACEHOLDER_COMPOSE = LANES / "placeholder" / "compose.yaml"
STATEFUL_LANES = {
    "bump-driver",
    "bump-proposer",
    "estate-landing",
    "execution-watch",
    "inert-landing",
    "pin-watcher",
    "revision-watcher",
    "rotation-decomposer",
    "rotation-proposer",
    "unit-verifier",
    "work-carrier",
}


def _instructions() -> list[str]:
    """The Dockerfile's instructions, continuation lines joined, comments dropped."""
    text = re.sub(r"\\\n", " ", DOCKERFILE.read_text())
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def test_every_download_is_checked_against_a_sha256_digest() -> None:
    adds = [line for line in _instructions() if line.startswith("ADD ")]

    assert len(adds) == 5, "supercronic, gh, uv, node and bws"
    for add in adds:
        assert re.search(r"--checksum=sha256:[0-9a-f]{64} ", add), add
    runs = [line for line in _instructions() if line.startswith("RUN")]
    assert not any(re.search(r"\b(curl|wget) ", line) for line in runs), "a download skips ADD"


def test_the_scheduler_is_the_pinned_supercronic_asset() -> None:
    text = DOCKERFILE.read_text()

    assert "ARG SUPERCRONIC_VERSION=v0.2.51" in text
    assert "releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" in text


def test_no_tool_or_image_floats() -> None:
    text = DOCKERFILE.read_text()

    assert ":latest" not in text and "/latest/" not in text
    for package in ("git", "curl", "ca-certificates", "libatomic1"):
        assert re.search(rf'"{re.escape(package)}=\$\{{[A-Z_]+_VERSION\}}"', text), package
    for argument in re.findall(r"^ARG ([A-Z_]+_VERSION)=(\S*)$", text, re.MULTILINE):
        assert argument[1], f"{argument[0]} has no pinned value"


def test_both_stages_build_from_the_orchestrator_image_argument() -> None:
    froms = [line for line in _instructions() if line.startswith("FROM ")]

    assert froms == ["FROM ${ORCHESTRATOR_IMAGE} AS tools", "FROM ${ORCHESTRATOR_IMAGE}"]


def test_the_inherited_health_check_and_command_are_replaced_and_it_ends_as_orchestrator() -> None:
    instructions = _instructions()
    final = instructions[instructions.index("FROM ${ORCHESTRATOR_IMAGE}") :]

    assert "HEALTHCHECK NONE" in final
    assert final[-1] == 'ENTRYPOINT ["/app/lanes/entrypoint.sh"]'
    assert [line for line in final if line.startswith("USER ")][-1] == "USER orchestrator"
    assert "ENV TZ=America/New_York" in final
    assert not any(line.startswith("CMD") for line in final)


def test_every_stateful_lane_and_the_placeholder_have_an_orchestrator_owned_directory() -> None:
    loop = next(line for line in _instructions() if "/var/lib/sds/$lane" in line)
    lanes = set(loop.split(" in ", 1)[1].split(";", 1)[0].split())

    assert lanes == STATEFUL_LANES | {"placeholder"}
    assert "-o orchestrator -g orchestrator" in loop


def test_the_image_copies_the_launchers_helpers_and_programs() -> None:
    copy = next(line for line in _instructions() if line.startswith("COPY scripts/"))

    assert copy.split()[1:] == [
        "scripts/run-*.sh",
        "scripts/sds-*.sh",
        "scripts/sds_scope.py",
        "scripts/lane_run.py",
        "/app/scripts/",
    ]
    assert len(list((ROOT / "scripts").glob("sds-*.sh"))) == 4
    assert "COPY lanes/lane-run /usr/local/bin/lane-run" in _instructions()
    assert (LANES / "lane-run").read_text().splitlines()[-1] == (
        'exec /app/.venv/bin/python -I /app/scripts/lane_run.py "$@"'
    )


def test_every_tracked_crontab_is_checked_at_build_time() -> None:
    assert any(
        'for crontab in /app/lanes/*/crontab; do supercronic -test "$crontab"' in line
        for line in _instructions()
    )


def test_the_entrypoint_runs_the_tracked_crontab_only_on_exactly_true() -> None:
    text = ENTRYPOINT.read_text()

    assert '[ "${LANE_SCHEDULE_ENABLED:-}" = "true" ]' in text
    assert 'tracked="/app/lanes/$lane/crontab"' in text
    assert 'supercronic -json -no-reap -test "$crontab"' in text
    assert text.rstrip().splitlines()[-1] == 'exec supercronic -json "$crontab"'


def test_lane_run_lives_beside_the_launchers_it_starts() -> None:
    assert lane_run.LAUNCHER_DIR == ROOT / "scripts"


def test_the_placeholder_compose_maps_only_its_two_variables() -> None:
    compose = yaml.safe_load(PLACEHOLDER_COMPOSE.read_text())
    ((name, service),) = compose["services"].items()
    text = PLACEHOLDER_COMPOSE.read_text()

    assert name == "placeholder"
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["placeholder"],
        "environment": {"LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}"},
        "volumes": ["sds-lanes-placeholder-state:/var/lib/sds/placeholder"],
        "restart": "unless-stopped",
    }
    assert compose["volumes"] == {"sds-lanes-placeholder-state": None}
    assert set(re.findall(r"\$\{(\w+)", text)) == {"SDS_LANES_IMAGE_TAG", "LANE_SCHEDULE_ENABLED"}
    assert (LANES / "placeholder" / "crontab").is_file()


def test_the_placeholder_crontab_has_no_job() -> None:
    lines = (LANES / "placeholder" / "crontab").read_text().splitlines()

    assert all(line.startswith("#") or not line.strip() for line in lines)
