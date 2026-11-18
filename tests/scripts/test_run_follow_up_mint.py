"""`scripts/run-follow-up-mint.sh` in the lane's container (ADR-0059 Amendment 10), and the
lane's tracked files.

The real launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it (the
lane's token as `BWS_ACCESS_TOKEN`, a ping-URL secret id, PATH and HOME). `bws` and `curl` are
stubs on PATH and the venv's `orchestrator` is a stub that logs what reached it: nothing reads
BWS, the Keychain, the orchestrator or Healthchecks.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import deploy_orchestrator as deploy
from tests.scripts.test_run_unit_verifier import lane_start_minutes

LAUNCHER = Path("scripts/run-follow-up-mint.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh")
COMPOSE = Path("lanes/follow-up-mint/compose.yaml")
CRONTAB = Path("lanes/follow-up-mint/crontab")

SYSTEM_UUID = "221a48d5-3f29-4898-b300-b4820140c880"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
PING_URL = "https://hc.example/ping/follow-up-mint"
LANE_TOKEN = "a-lane-token"
SYSTEM = f"value-of-{SYSTEM_UUID}"

# The `bws` stub logs the UUID and the token it was given. A UUID in `$BWS_REFUSES` fails, and one
# in `$BWS_EMPTY` holds an empty value; the ping-URL secret holds `PING_URL`; every other secret
# `value-of-<uuid>`.
_BWS = f"""#!/bin/sh
echo "$3 token=${{BWS_ACCESS_TOKEN:-}}" >> "$BWS_LOG"
case " $BWS_REFUSES " in *" $3 "*) exit 1 ;; esac
case " $BWS_EMPTY " in *" $3 "*) echo '{{"value": ""}}'; exit 0 ;; esac
[ "$3" = "{PING_URL_UUID}" ] && {{ echo '{{"value": "{PING_URL}"}}'; exit 0; }}
printf '{{"value": "value-of-%s"}}' "$3"
"""
# A ping's URL arrives on stdin as curl's config; anything else fails.
_CURL = """#!/bin/sh
for argument in "$@"; do [ "$argument" = "-" ] && { cat >> "$PING_LOG"; exit 0; }; done
exit 1
"""
_TOOL = """#!/bin/bash
echo "pass args=$* url=${ORCHESTRATOR_API_URL:-} token=${ORCHESTRATOR_API_TOKEN:-} \\
key=${ORCHESTRATOR_API_CREDENTIAL_KEY_ID:-} lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
echo '{"minted":[],"skipped":[],"considered":0}'
exit "${FAKE_PASS_RC:-0}"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(
    tmp_path: Path, *args: str, **fake: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]]]:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    # `sds-bws.sh` parses BWS's JSON with the venv's interpreter, for real.
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    _executable(venv / "orchestrator", _TOOL)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("bws", _BWS), ("curl", _CURL)):
        _executable(stubs / name, body)
    home = tmp_path / "home"
    home.mkdir()
    logs = {name: tmp_path / f"{name}.log" for name in ("lane", "bws", "ping")}
    for log in logs.values():
        log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN": LANE_TOKEN,
        "SDS_DEADMAN_PING_URL_UUID": PING_URL_UUID,
        "LANE_LOG": str(logs["lane"]),
        "BWS_LOG": str(logs["bws"]),
        "PING_LOG": str(logs["ping"]),
        **fake,
    }
    result = subprocess.run(
        [
            "/usr/bin/env",
            "-i",
            *(f"{k}={v}" for k, v in env.items()),
            "/bin/bash",
            str(root / "scripts" / LAUNCHER.name),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result, {name: log.read_text().splitlines() for name, log in logs.items()}


def _pings(lines: list[str]) -> list[str]:
    return [line.removeprefix('url = "').removesuffix('"') for line in lines]


def _passes(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("pass ")]


# ---- the pass --------------------------------------------------------------------------------

_KEY = r"follow-up-mint:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"


def test_a_pass_reads_the_bearer_with_the_lanes_one_token_and_mints_as_system(
    tmp_path: Path,
) -> None:
    result, logs = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == [
        f"{PING_URL_UUID} token={LANE_TOKEN}",
        f"{SYSTEM_UUID} token={LANE_TOKEN}",
    ]
    # The CLI gets the bearer, the SYSTEM key id and the production URL, and never the lane's
    # token.
    [line] = _passes(logs["lane"])
    assert re.fullmatch(
        rf"pass args=mint-follow-ups --idempotency-key {_KEY} --json"
        rf" url=https://sds\.alobar\.net token={SYSTEM} key=orchestrator-system lane_token=",
        line,
    ), line
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", PING_URL]
    # The CLI's JSON report reaches the lane log.
    assert '{"minted":[],"skipped":[],"considered":0}' in result.stdout
    for secret in (LANE_TOKEN, SYSTEM):
        assert secret not in result.stdout + result.stderr


def test_no_secret_is_a_process_argument() -> None:
    """The bearer reaches the CLI in its environment and the lane's token stays in the launcher's:
    the launcher reads with `sds_bws_value`'s one-argument form and hands the CLI only the key."""
    text = LAUNCHER.read_text()
    assert 'sds_bws_value "$ORCHESTRATOR_SYSTEM_UUID")' in text
    # The whole call, its continuation lines joined.
    joined = text.replace("\\\n", " ")
    call = next(line for line in joined.splitlines() if "/.venv/bin/orchestrator" in line)
    assert call.split() == [
        '"$REPO_ROOT/.venv/bin/orchestrator"',
        "mint-follow-ups",
        "--idempotency-key",
        '"$IDEMPOTENCY_KEY"',
        "--json",
        ">",
        '"$SDS_OUTCOME_REPORT"',
        "2>&1",
    ]
    key = next(line for line in text.splitlines() if line.startswith("IDEMPOTENCY_KEY="))
    assert key == 'IDEMPOTENCY_KEY="follow-up-mint:$(date -u +%Y-%m-%dT%H:%M:%SZ)"'
    assert "/usr/bin/security" not in text
    assert re.search(r"\bgh\b", text) is None


def test_the_api_url_can_be_overridden_and_defaults_to_production(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, ORCHESTRATOR_API_URL="https://orchestrator.example")
    assert result.returncode == 0, result.stderr
    assert " url=https://orchestrator.example " in _passes(logs["lane"])[0]


def test_without_the_lanes_token_it_refuses_and_calls_nothing(tmp_path: Path) -> None:
    """The lane has no Mac job: without `BWS_ACCESS_TOKEN` it reads no Keychain, resolves no ping
    URL and runs no CLI."""
    result, logs = _run(tmp_path, BWS_ACCESS_TOKEN="")
    assert result.returncode == 1
    assert result.stderr.strip().splitlines() == [
        "FATAL: no BWS_ACCESS_TOKEN: the follow-up-mint lane runs only in its container,"
        " through lane-run"
    ]
    assert logs == {"lane": [], "bws": [], "ping": []}


def test_an_argument_is_refused_before_anything_runs(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, "--dry-run")
    assert result.returncode == 1
    assert "takes no arguments" in result.stderr
    assert logs == {"lane": [], "bws": [], "ping": []}


@pytest.mark.parametrize("fault", ["BWS_REFUSES", "BWS_EMPTY"], ids=["refused", "empty"])
def test_an_unreadable_or_empty_bearer_fails_the_pass_before_the_call(
    tmp_path: Path, fault: str
) -> None:
    result, logs = _run(tmp_path, **{fault: SYSTEM_UUID})
    assert result.returncode == 1
    assert "FATAL: could not read the orchestrator system credential" in result.stderr
    assert "(the lane's token)" in result.stderr
    assert _passes(logs["lane"]) == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


@pytest.mark.parametrize(("code", "expected"), [(0, 0), (1, 1), (2, 1), (127, 1)])
def test_the_clis_failure_fails_the_outcome(tmp_path: Path, code: int, expected: int) -> None:
    result, logs = _run(tmp_path, FAKE_PASS_RC=str(code))
    assert result.returncode == expected
    assert len(_passes(logs["lane"])) == 1
    assert _pings(logs["ping"])[-1] == (PING_URL if code == 0 else f"{PING_URL}/fail")
    if code not in (0, 1):
        assert f"FATAL: orchestrator mint-follow-ups exited {code}" in result.stdout


def test_without_a_ping_url_secret_a_pass_still_runs_and_pings_nothing(tmp_path: Path) -> None:
    """The compose's empty `SDS_DEADMAN_PING_URL_UUID` falls through to the name lookup, which
    needs the broad identity `lane-run` doesn't pass. `/usr/bin/security` is asked there (macOS
    has it, the image doesn't), so a stand-in broad identity keeps the real Keychain out of the
    test; in the container nothing would read the Healthchecks key at all."""
    result, logs = _run(tmp_path, SDS_DEADMAN_PING_URL_UUID="", BWS_ACCESS_TOKEN_BROAD="broad")
    assert result.returncode == 0, result.stderr
    assert logs["ping"] == []
    assert "alerting disabled this run" in result.stdout
    assert len(_passes(logs["lane"])) == 1
    assert [line for line in logs["bws"] if line.endswith(f"token={LANE_TOKEN}")] == [
        f"{SYSTEM_UUID} token={LANE_TOKEN}",
    ]


def test_the_console_script_is_the_orchestrator_cli() -> None:
    pyproject = Path("pyproject.toml").read_text()
    assert 'orchestrator = "orchestrator.cli:app"' in pyproject


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "follow-up-mint")
    assert deploy.add_refusals(service, "follow-up-mint") == []
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["follow-up-mint"],
        "environment": {
            "BWS_ACCESS_TOKEN": "${FOLLOW_UP_MINT_BWS_ACCESS_TOKEN}",
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            # Empty until the lane's ping-URL secret exists (`test_deploy_lanes.py`'s
            # `placeholders`).
            "SDS_DEADMAN_PING_URL_UUID": "",
        },
        "restart": "unless-stopped",
    }
    document = yaml.safe_load(COMPOSE.read_text())
    assert set(document) == {"services"}
    assert "follow-up-mint" not in deploy.STATEFUL_LANES
    assert deploy.LANE_CHECKS["follow-up-mint"] == "sds-follow-up-mint"


def test_the_crontab_runs_one_flagless_daily_pass_after_the_audit_outside_the_window() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["43 6 * * * lane-run follow-up-mint"]
    hour = int(lines[0].split()[1])
    assert hour > 3
    assert hour not in deploy.LANDING_HOURS


def test_no_two_lanes_start_a_pass_in_the_same_minute() -> None:
    minutes = lane_start_minutes()
    assert minutes["follow-up-mint"] == {43}
    for lane, taken in minutes.items():
        if lane != "follow-up-mint":
            assert 43 not in taken, lane


def test_the_repositorys_manifest_lists_the_lanes_one_secret() -> None:
    """The lane's `--token` proof reads what the manifest lists for it: the SYSTEM bearer alone,
    until its ping-URL secret joins it."""
    manifest = Path(".bws-secrets.toml").read_text()
    assert deploy.lane_secrets(manifest, "follow-up-mint") == [SYSTEM_UUID]
    assert SYSTEM_UUID in LAUNCHER.read_text()
