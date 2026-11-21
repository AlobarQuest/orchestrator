"""`scripts/run-follow-up-mint.sh` in the lane's container (ADR-0059 Amendment 10), and the
lane's tracked files.

The real launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it (on the
secrets broker, `SDS_SECRETS_SOCKET` naming a stand-in broker's socket, `conftest.py`'s; or a
lane's token as `BWS_ACCESS_TOKEN`; a ping-URL secret id, PATH and HOME). `bws` and `curl` are
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
from scripts.secrets_broker import load_allowlist
from tests.scripts.test_run_unit_verifier import lane_start_minutes

LAUNCHER = Path("scripts/run-follow-up-mint.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh")
COMPOSE = Path("lanes/follow-up-mint/compose.yaml")
CRONTAB = Path("lanes/follow-up-mint/crontab")

SYSTEM_UUID = "221a48d5-3f29-4898-b300-b4820140c880"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
PING_URL = "https://hc.example/ping/follow-up-mint"
# The BWS secret id of `follow-up-mint-ping-url`, which the compose names: not a secret, not the
# ping URL, and not the harness's stand-in id above.
PING_URL_KEEPER = "46e714ca-32b7-4e89-9817-b509013363a5"
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
key=${ORCHESTRATOR_API_CREDENTIAL_KEY_ID:-} lane_token=${BWS_ACCESS_TOKEN:+set} \\
socket=${SDS_SECRETS_SOCKET:+set}" >> "$LANE_LOG"
echo '{"minted":[],"skipped":[],"considered":0}'
exit "${FAKE_PASS_RC:-0}"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(
    tmp_path: Path, *args: str, **fake: str | None
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]]]:
    """A `None` in `fake` removes that variable from the launcher's environment."""
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
            *(f"{k}={v}" for k, v in env.items() if v is not None),
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
        rf" url=https://sds\.alobar\.net token={SYSTEM} key=orchestrator-system lane_token="
        r" socket=",
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


_OUTSIDE = (
    "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN: the follow-up-mint lane runs only in"
    " its container, through lane-run"
)


@pytest.mark.parametrize("token", [None, ""], ids=["unset", "empty"])
def test_outside_the_container_it_refuses_and_calls_nothing(
    tmp_path: Path, token: str | None
) -> None:
    """The lane has no Mac job: with neither a socket nor a token it reads no Keychain, resolves
    no ping URL and runs no CLI."""
    result, logs = _run(tmp_path, BWS_ACCESS_TOKEN=token)
    assert result.returncode == 1
    assert result.stderr.strip().splitlines() == [_OUTSIDE]
    assert logs == {"lane": [], "bws": [], "ping": []}


def test_a_socket_variable_naming_no_socket_refuses_and_calls_nothing(tmp_path: Path) -> None:
    """`SDS_SECRETS_SOCKET` alone isn't the container: it must name a socket."""
    not_a_socket = tmp_path / "broker.sock"
    not_a_socket.write_text("")
    result, logs = _run(tmp_path, BWS_ACCESS_TOKEN=None, SDS_SECRETS_SOCKET=str(not_a_socket))
    assert result.returncode == 1
    assert result.stderr.strip().splitlines() == [_OUTSIDE]
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
    assert "(the lane's socket or token)" in result.stderr
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
    """An empty `SDS_DEADMAN_PING_URL_UUID` falls through to the name lookup, which
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


# ---- the container on the secrets broker (ADR-0055 amendment 30) ------------------------------


def _brokered(
    tmp_path: Path, socket_path: Path, **fake: str | None
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]]]:
    """The container as `lane-run` starts it on the broker: no token, and the socket's marker."""
    return _run(tmp_path, BWS_ACCESS_TOKEN=None, SDS_SECRETS_SOCKET=str(socket_path), **fake)


def test_on_the_socket_without_a_token_a_pass_reads_every_secret_through_it(
    tmp_path: Path, broker
) -> None:
    """With the ping-URL secret named, the socket serves both reads and `bws` never runs. The CLI
    gets the bearer and neither the token nor the socket's marker."""
    socket_path = broker({SYSTEM_UUID: SYSTEM, PING_URL_UUID: PING_URL})
    result, logs = _brokered(tmp_path, socket_path)
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == []
    [line] = _passes(logs["lane"])
    assert line.endswith(f" token={SYSTEM} key=orchestrator-system lane_token= socket="), line
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", PING_URL]
    assert SYSTEM not in result.stdout + result.stderr


def test_on_the_socket_with_an_empty_ping_id_no_lane_secret_reaches_bws(
    tmp_path: Path, broker
) -> None:
    """With the ping-URL id empty, the outcome alarm falls through to the
    name lookup, which only the broad stand-in (never in the container) reaches. The bearer still
    comes through the socket alone."""
    socket_path = broker({SYSTEM_UUID: SYSTEM})
    result, logs = _brokered(
        tmp_path, socket_path, SDS_DEADMAN_PING_URL_UUID="", BWS_ACCESS_TOKEN_BROAD="broad"
    )
    assert result.returncode == 0, result.stderr
    assert not [line for line in logs["bws"] if line.startswith(SYSTEM_UUID)]
    assert logs["ping"] == []
    [line] = _passes(logs["lane"])
    assert line.endswith(" lane_token= socket="), line


@pytest.mark.parametrize(
    ("served", "reason"),
    [({SYSTEM_UUID: None}, "bws_failed"), ({}, "not_allowed")],
    ids=["broker-read-failed", "not-on-the-allowlist"],
)
def test_a_bearer_the_socket_refuses_fails_the_pass_before_the_call(
    tmp_path: Path, broker, served: dict[str, str | None], reason: str
) -> None:
    socket_path = broker({PING_URL_UUID: PING_URL, **served})
    result, logs = _brokered(tmp_path, socket_path)
    assert result.returncode == 1
    assert f"the secrets broker gave no value for {SYSTEM_UUID}: {reason}" in result.stderr
    assert "FATAL: could not read the orchestrator system credential" in result.stderr
    assert "(the lane's socket or token)" in result.stderr
    assert logs["bws"] == []
    assert _passes(logs["lane"]) == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


def test_a_dead_socket_fails_the_pass_through_the_outcome_and_never_falls_back_to_bws(
    tmp_path: Path, dead_socket: Path
) -> None:
    """A mounted socket nothing listens on: the read fails by name, `sds_outcome_fail` ends the
    pass non-zero, and no lane secret is asked of `bws`."""
    result, logs = _brokered(
        tmp_path, dead_socket, SDS_DEADMAN_PING_URL_UUID="", BWS_ACCESS_TOKEN_BROAD="broad"
    )
    assert result.returncode == 1
    assert f"the secrets broker gave no value for {SYSTEM_UUID}: no_reply" in result.stderr
    assert "FATAL: could not read the orchestrator system credential" in result.stderr
    assert not [line for line in logs["bws"] if line.startswith(SYSTEM_UUID)]
    assert _passes(logs["lane"]) == []


def test_the_console_script_is_the_orchestrator_cli() -> None:
    pyproject = Path("pyproject.toml").read_text()
    assert 'orchestrator = "orchestrator.cli:app"' in pyproject


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "follow-up-mint")
    # A broker lane: it mounts its socket and the allowlist names it.
    allowlist = load_allowlist(Path(deploy.BROKER_ALLOWLIST))
    assert deploy.broker_lane(service, "follow-up-mint", allowlist)
    assert deploy.add_refusals(service, "follow-up-mint", allowlist) == []
    assert deploy.compose_broker_refusals(COMPOSE.read_text(), "follow-up-mint") == []
    assert allowlist["follow-up-mint"] == {SYSTEM_UUID, PING_URL_KEEPER}
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["follow-up-mint"],
        # No `BWS_ACCESS_TOKEN`: the lane reads through its secrets-broker socket.
        "environment": {
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            # The BWS secret id of `follow-up-mint-ping-url`: not a secret, not the ping URL.
            "SDS_DEADMAN_PING_URL_UUID": PING_URL_KEEPER,
        },
        "volumes": ["/data/sds/broker/follow-up-mint:/run/sds-broker:ro"],
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


def test_the_repositorys_manifest_lists_the_lanes_secrets() -> None:
    """The lane's `--token` proof reads what the manifest lists for it: the SYSTEM bearer and its
    ping-URL secret. The launcher names the bearer; the ping-URL secret's id reaches it from the
    compose through `lane-run`."""
    manifest = Path(".bws-secrets.toml").read_text()
    secrets = deploy.lane_secrets(manifest, "follow-up-mint")
    assert sorted(secrets) == sorted([SYSTEM_UUID, PING_URL_KEEPER])
    launcher = LAUNCHER.read_text()
    assert SYSTEM_UUID in launcher
    assert PING_URL_KEEPER not in launcher
