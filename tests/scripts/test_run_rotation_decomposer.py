"""`scripts/run-rotation-decomposer.sh` on both hosts, and the lane's tracked files.

The real launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it in the
container (the lane's token as `BWS_ACCESS_TOKEN`, a ping-URL secret id, PATH and HOME), or with the
two identity overrides on the Mac. `bws`, `gh` and `curl` are stubs on PATH and the venv's
`rotation-decomposer` is a stub that logs what reached it: nothing reads BWS, the Keychain,
change-manager, the orchestrator, alert-hub or Healthchecks.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from rotation_decomposer import alerts
from rotation_worker import git_mirror
from scripts import deploy_orchestrator as deploy
from scripts import lane_run
from tests.scripts.test_run_unit_verifier import lane_start_minutes

LAUNCHER = Path("scripts/run-rotation-decomposer.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh")
COMPOSE = Path("lanes/rotation-decomposer/compose.yaml")
CRONTAB = Path("lanes/rotation-decomposer/crontab")
PLIST = Path("scripts/com.devon.rotation-decomposer.plist")

SYSTEM_UUID = "221a48d5-3f29-4898-b300-b4820140c880"
READ_UUID = "314f276d-55ca-4ddc-a24d-b4a3013508cd"
ALERT_UUID = "b415d03b-f92d-46b1-8a9d-b4f10103df49"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
PING_URL = "https://hc.example/ping/rotation-decomposer"
LANE_TOKEN = "a-lane-token"
SYSTEM = f"value-of-{SYSTEM_UUID}"
READ = f"value-of-{READ_UUID}"
ALERT = f"value-of-{ALERT_UUID}"
SECRETS = (LANE_TOKEN, SYSTEM, READ, ALERT)

# The `bws` stub logs the UUID and the token it was given. A UUID in `$BWS_REFUSES` fails; the
# ping-URL secret holds `PING_URL`; every other secret `value-of-<uuid>`.
_BWS = f"""#!/bin/sh
echo "$3 token=${{BWS_ACCESS_TOKEN:-}}" >> "$BWS_LOG"
case " $BWS_REFUSES " in *" $3 "*) exit 1 ;; esac
[ "$3" = "{PING_URL_UUID}" ] && {{ echo '{{"value": "{PING_URL}"}}'; exit 0; }}
printf '{{"value": "value-of-%s"}}' "$3"
"""
_GH = '#!/bin/sh\necho "gh $*" >> "$GH_LOG"\necho gh-token\n'
# A ping's URL arrives on stdin as curl's config; anything else fails.
_CURL = """#!/bin/sh
for argument in "$@"; do [ "$argument" = "-" ] && { cat >> "$PING_LOG"; exit 0; }; done
exit 1
"""
_TOOL = """#!/bin/bash
echo "pass args=$* system=${ORCHESTRATOR_API_TOKEN:-} key=${ORCHESTRATOR_API_CREDENTIAL_KEY_ID:-} \\
cm=${CHANGE_MANAGER_TOKEN:-} alert=${SDS_BRIDGE_ALERT_TOKEN:-unset} \\
lane_token=${BWS_ACCESS_TOKEN:+set} state=${ROTATION_DECOMPOSER_STATE_DIR:-}" >> "$LANE_LOG"
echo "the program's report"
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
    _executable(venv / "rotation-decomposer", _TOOL)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("bws", _BWS), ("gh", _GH), ("curl", _CURL)):
        _executable(stubs / name, body)
    home = tmp_path / "home"
    home.mkdir()
    logs = {name: tmp_path / f"{name}.log" for name in ("lane", "bws", "gh", "ping")}
    for log in logs.values():
        log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN": LANE_TOKEN,
        "SDS_DEADMAN_PING_URL_UUID": PING_URL_UUID,
        "ROTATION_DECOMPOSER_STATE_DIR": "/var/lib/sds/rotation-decomposer",
        "LANE_LOG": str(logs["lane"]),
        "BWS_LOG": str(logs["bws"]),
        "GH_LOG": str(logs["gh"]),
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


# ---- the lane's container (ADR-0059 increment 4) ---------------------------------------------


def test_an_acting_pass_reads_all_three_secrets_with_the_lanes_one_token(tmp_path: Path) -> None:
    result, logs = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == [
        f"{PING_URL_UUID} token={LANE_TOKEN}",
        f"{READ_UUID} token={LANE_TOKEN}",
        f"{SYSTEM_UUID} token={LANE_TOKEN}",
        f"{ALERT_UUID} token={LANE_TOKEN}",
    ]
    # The pass gets every secret and its state directory, and never the lane's token.
    assert _passes(logs["lane"]) == [
        f"pass args= system={SYSTEM} key=orchestrator-system cm={READ} alert={ALERT}"
        " lane_token= state=/var/lib/sds/rotation-decomposer"
    ]
    assert logs["gh"] == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", PING_URL]
    for secret in SECRETS:
        assert secret not in result.stdout + result.stderr


def test_a_dry_run_reads_no_sender_token_and_pings_nothing(tmp_path: Path) -> None:
    """The lane's reporting mode (ADR-0059 cut-over step 2): it reads what it reports from, never
    the sender token, and neither reads the ping-URL secret nor pings."""
    result, logs = _run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == [
        f"{READ_UUID} token={LANE_TOKEN}",
        f"{SYSTEM_UUID} token={LANE_TOKEN}",
    ]
    assert _passes(logs["lane"]) == [
        f"pass args=--dry-run system={SYSTEM} key=orchestrator-system cm={READ} alert=unset"
        " lane_token= state=/var/lib/sds/rotation-decomposer"
    ]
    assert logs["ping"] == []


def test_the_container_uses_no_mac_identity_and_skips_activation(tmp_path: Path) -> None:
    """Set Mac identity overrides change nothing: every read is the lane's token."""
    result, logs = _run(tmp_path, BWS_ACCESS_TOKEN_SDS="narrow", BWS_ACCESS_TOKEN_BROAD="broad")
    assert result.returncode == 0, result.stderr
    assert all(line.endswith(f"token={LANE_TOKEN}") for line in logs["bws"]), logs["bws"]
    assert "[activation]" not in result.stdout + result.stderr


def test_the_keychain_is_read_only_on_the_macs_branch() -> None:
    lines = LAUNCHER.read_text().splitlines()
    branch = lines.index('if [ -n "${BWS_ACCESS_TOKEN:-}" ]; then')
    otherwise = lines.index("else", branch)
    end = lines.index("fi", otherwise)
    keychain = [i for i, line in enumerate(lines) if "/usr/bin/security" in line]
    assert len(keychain) == 2
    assert all(otherwise < i < end for i in keychain)


@pytest.mark.parametrize("refused", [READ_UUID, SYSTEM_UUID, ALERT_UUID])
def test_an_unreadable_secret_exits_1_before_the_pass(tmp_path: Path, refused: str) -> None:
    result, logs = _run(tmp_path, BWS_REFUSES=refused)
    assert result.returncode == 1
    assert "(the lane's token)" in result.stderr
    assert _passes(logs["lane"]) == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


def test_without_a_ping_url_secret_an_acting_pass_still_runs_and_pings_nothing(
    tmp_path: Path,
) -> None:
    """The compose's empty `SDS_DEADMAN_PING_URL_UUID` falls through to the name lookup, which
    needs the broad identity `lane-run` doesn't pass. `/usr/bin/security` is asked there (macOS
    has it, the image doesn't), so the stand-in broad identity keeps the real Keychain out of the
    test; in the container nothing would read the Healthchecks key at all."""
    result, logs = _run(tmp_path, SDS_DEADMAN_PING_URL_UUID="", BWS_ACCESS_TOKEN_BROAD="broad")
    assert result.returncode == 0, result.stderr
    assert logs["ping"] == []
    assert "alerting disabled this run" in result.stdout
    assert len(_passes(logs["lane"])) == 1
    # Only the stand-in reads the Healthchecks key; the lane's token reads the three secrets.
    assert [line for line in logs["bws"] if line.endswith(f"token={LANE_TOKEN}")] == [
        f"{READ_UUID} token={LANE_TOKEN}",
        f"{SYSTEM_UUID} token={LANE_TOKEN}",
        f"{ALERT_UUID} token={LANE_TOKEN}",
    ]


@pytest.mark.parametrize(("code", "expected"), [(0, 0), (1, 1), (2, 2), (3, 3), (127, 1)])
def test_the_programs_code_is_the_passs_code(tmp_path: Path, code: int, expected: int) -> None:
    result, logs = _run(tmp_path, FAKE_PASS_RC=str(code))
    assert result.returncode == expected
    assert len(_passes(logs["lane"])) == 1
    assert _pings(logs["ping"])[-1] == (PING_URL if code == 0 else f"{PING_URL}/fail")


# ---- the Mac, unchanged until increment 6 ----------------------------------------------------


@pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["acting", "dry-run"])
def test_on_the_mac_each_secret_is_read_with_its_own_identity(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    result, logs = _run(
        tmp_path,
        *args,
        BWS_ACCESS_TOKEN="",
        BWS_ACCESS_TOKEN_BROAD="broad",
        BWS_ACCESS_TOKEN_SDS="narrow",
        SDS_DEADMAN_PING_URL_UUID="",
    )
    assert result.returncode == 0, result.stderr
    reads = [
        line for line in logs["bws"] if line.split()[0] in (READ_UUID, SYSTEM_UUID, ALERT_UUID)
    ]
    expected = [f"{READ_UUID} token=broad", f"{SYSTEM_UUID} token=narrow"]
    if not args:
        expected.append(f"{ALERT_UUID} token=narrow")
    assert reads == expected
    assert logs["gh"] == []
    assert "[activation]" in result.stdout
    alert = "unset" if args else ALERT
    assert _passes(logs["lane"]) == [
        f"pass args={' '.join(args)} system={SYSTEM} key=orchestrator-system cm={READ}"
        f" alert={alert} lane_token= state=/var/lib/sds/rotation-decomposer"
    ]


def test_on_the_mac_an_unreadable_secret_keeps_its_message(tmp_path: Path) -> None:
    result, _ = _run(
        tmp_path,
        BWS_ACCESS_TOKEN="",
        BWS_ACCESS_TOKEN_BROAD="broad",
        BWS_ACCESS_TOKEN_SDS="narrow",
        SDS_DEADMAN_PING_URL_UUID="",
        BWS_REFUSES=SYSTEM_UUID,
    )
    assert result.returncode == 1
    assert "FATAL: could not read the orchestrator system credential from BWS\n" in result.stderr


# ---- the lane's state and mirrors ------------------------------------------------------------


def test_the_state_variable_reaches_the_launcher_and_names_the_decomposers_directory() -> None:
    assert alerts.STATE_DIR_ENV == "ROTATION_DECOMPOSER_STATE_DIR"
    assert alerts.STATE_DIR_ENV in lane_run.STATE_VARIABLES
    environment = lane_run.lane_environment(
        {"ROTATION_DECOMPOSER_STATE_DIR": "/var/lib/sds/rotation-decomposer"}, "/tmp/home"
    )
    assert environment["ROTATION_DECOMPOSER_STATE_DIR"] == "/var/lib/sds/rotation-decomposer"


def test_the_mirrors_sit_under_lane_runs_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`lane-run` passes no `XDG_CACHE_HOME`, so the mirrors land in the pass's new `HOME`, which
    each pass discards: they are refetched, never state (ADR-0059 decision 1)."""
    home = str(tmp_path / "lane-run-home")
    environment = lane_run.lane_environment({"BWS_ACCESS_TOKEN": "t"}, home)
    assert "XDG_CACHE_HOME" not in environment
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", environment["HOME"])
    root = git_mirror.default_mirror_root("rotation-decomposer")
    assert root == Path(home) / ".cache" / "rotation-decomposer" / "mirrors"


def test_the_image_carries_git_and_the_lanes_state_directory() -> None:
    dockerfile = Path("lanes/Dockerfile").read_text()
    assert '"git=${GIT_VERSION}"' in dockerfile
    assert "rotation-decomposer" in dockerfile.split("RUN for lane in", 1)[1].split("done", 1)[0]


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "rotation-decomposer")
    assert deploy.add_refusals(service, "rotation-decomposer") == []
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["rotation-decomposer"],
        "environment": {
            "BWS_ACCESS_TOKEN": "${ROTATION_DECOMPOSER_BWS_ACCESS_TOKEN}",
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            "ROTATION_DECOMPOSER_STATE_DIR": "/var/lib/sds/rotation-decomposer",
            # A placeholder until the lane's ping-URL keeper exists (test_deploy_lanes.py).
            "SDS_DEADMAN_PING_URL_UUID": "",
        },
        "volumes": ["sds-lanes-rotation-decomposer-state:/var/lib/sds/rotation-decomposer"],
        "restart": "unless-stopped",
    }
    document = yaml.safe_load(COMPOSE.read_text())
    assert set(document) == {"services", "volumes"}
    assert document["volumes"] == {"sds-lanes-rotation-decomposer-state": None}
    assert "rotation-decomposer" in deploy.STATEFUL_LANES
    assert deploy.LANE_CHECKS["rotation-decomposer"] == "sds-rotation-decomposer"


def test_the_crontab_runs_an_acting_pass_hourly_as_the_mac_job_does() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["50 * * * * lane-run rotation-decomposer"]
    plist = PLIST.read_text()
    assert "<key>StartInterval</key>\n    <integer>3600</integer>" in plist
    launcher = "<string>__REPO_ROOT__/scripts/run-rotation-decomposer.sh</string>"
    assert f"{launcher}\n    </array>" in plist


def test_no_two_lanes_start_a_pass_in_the_same_minute() -> None:
    """Minute 50 is clear of execution-watch's quarter-hours, the unit verifier's 2-52/10, the
    deploy watcher's 20, the pin watcher's 25, the landing ledger's 30, the change proposer's 35,
    the revision watcher's 40 and the membership check's 47."""
    minutes = lane_start_minutes()
    assert minutes["rotation-decomposer"] == {50}
    for lane, taken in minutes.items():
        if lane != "rotation-decomposer":
            assert 50 not in taken, lane


def test_the_repositorys_manifest_lists_the_lanes_secrets() -> None:
    """The lane's `--token` proof reads what the manifest lists for it; the launcher names each."""
    manifest = Path(".bws-secrets.toml").read_text()
    secrets = deploy.lane_secrets(manifest, "rotation-decomposer")
    assert sorted(secrets) == sorted([SYSTEM_UUID, READ_UUID, ALERT_UUID])
    launcher = LAUNCHER.read_text()
    for uuid in secrets:
        assert uuid in launcher, uuid
