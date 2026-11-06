"""`lane-run` starts a launcher with exactly its tracked list, and `--read` prints no value.

Each test runs the real program as the image runs it, `python lane_run.py ...`, from a copy beside
a fake launcher, with a fake `bws` on the caller's `PATH`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import lane_run

TOKEN_VALUE = "token-value-never-printed"
SECRET_VALUE = "secret-value-never-printed"
GOOD = "11111111-1111-4111-8111-111111111111"
BAD = "22222222-2222-4222-8222-222222222222"


class Lane:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.scripts = root / "scripts"
        self.bin = root / "bin"
        self.record = root / "record"
        self.scripts.mkdir()
        self.bin.mkdir()
        self.record.mkdir()
        shutil.copy(Path(lane_run.__file__), self.scripts / "lane_run.py")
        # A Python launcher, so no shell sits between lane-run and the recorded environment.
        self._executable(
            self.scripts / "run-example.sh",
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "json.dump({'env': dict(os.environ), 'argv': sys.argv[1:],\n"
            "           'home': sorted(os.listdir(os.environ['HOME']))},\n"
            f"          open('{self.record}/launcher.json', 'w'))\n"
            "sys.exit(7)\n",
        )
        # A fake bws: records each call's argv and environment, prints the secret, and refuses BAD.
        self._executable(
            self.bin / "bws",
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{self.record}/bws-argv"\n'
            f'env >> "{self.record}/bws-env"\n'
            f'case "$3" in {BAD}) echo "error: {SECRET_VALUE}" >&2; exit 1;; esac\n'
            f'echo \'{{"value": "{SECRET_VALUE}"}}\'\n',
        )

    @staticmethod
    def _executable(path: Path, text: str) -> None:
        path.write_text(text)
        path.chmod(0o755)

    def run(self, *argv: str, **environment: str) -> subprocess.CompletedProcess[str]:
        base = {"PATH": f"{self.bin}:/usr/bin:/bin"}
        return subprocess.run(
            [sys.executable, str(self.scripts / "lane_run.py"), *argv],
            env={**base, **environment},
            capture_output=True,
            text=True,
            check=False,
        )

    def launcher_saw(self) -> dict:
        return json.loads((self.record / "launcher.json").read_text())


@pytest.fixture
def lane(tmp_path: Path) -> Lane:
    return Lane(tmp_path)


def test_the_launcher_gets_exactly_the_listed_variables(lane: Lane) -> None:
    result = lane.run(
        "example",
        "--submit",
        BWS_ACCESS_TOKEN=TOKEN_VALUE,
        TZ="America/New_York",
        SDS_DEADMAN_PING_URL_UUID=GOOD,
        EXECUTION_WATCH_STATE_DIR="/var/lib/sds/execution-watch",
        LANE_SCHEDULE_ENABLED="true",
        EXECUTION_WATCH_BWS_ACCESS_TOKEN="another-lane-variable",
        HOSTNAME="container",
    )
    saw = lane.launcher_saw()
    # The recording interpreter adds these to its own environment: PEP 538's locale coercion
    # sets `LC_CTYPE`, and macOS's CoreFoundation sets `__CF_USER_TEXT_ENCODING`.
    added = {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
    environment = {k: v for k, v in saw["env"].items() if k not in added}

    assert result.returncode == 7, "lane-run exits with the launcher's own code"
    assert saw["argv"] == ["--submit"]
    assert set(environment) == {
        "PATH",
        "HOME",
        "BWS_ACCESS_TOKEN",
        "TZ",
        "SDS_DEADMAN_PING_URL_UUID",
        "EXECUTION_WATCH_STATE_DIR",
    }
    assert environment["PATH"] == lane_run.LANE_PATH
    assert environment["BWS_ACCESS_TOKEN"] == TOKEN_VALUE
    assert environment["EXECUTION_WATCH_STATE_DIR"] == "/var/lib/sds/execution-watch"


def test_each_state_variable_passes_only_when_set(lane: Lane) -> None:
    lane.run("example")
    absent = lane.launcher_saw()["env"]
    lane.run("example", **dict.fromkeys(lane_run.STATE_VARIABLES, "/var/lib/sds/x"))
    present = lane.launcher_saw()["env"]

    assert len(lane_run.STATE_VARIABLES) == 10
    for name in lane_run.STATE_VARIABLES:
        assert name not in absent and present[name] == "/var/lib/sds/x"
    assert "BWS_ACCESS_TOKEN" not in absent


def test_home_is_new_empty_and_removed_after_the_run(lane: Lane) -> None:
    lane.run("example")
    first = lane.launcher_saw()
    lane.run("example")
    second = lane.launcher_saw()

    assert first["home"] == []
    assert first["env"]["HOME"] != second["env"]["HOME"]
    assert not Path(first["env"]["HOME"]).exists()


@pytest.mark.parametrize("name", ["missing", "../scripts/run-example", "Example", ""])
def test_an_unknown_lane_starts_nothing(lane: Lane, name: str) -> None:
    result = lane.run(name)

    assert result.returncode == lane_run.EXIT_USAGE
    assert not (lane.record / "launcher.json").exists()


def test_read_prints_only_ok_or_refused_and_never_a_value(lane: Lane) -> None:
    result = lane.run("--read", GOOD, BAD, BWS_ACCESS_TOKEN=TOKEN_VALUE)

    assert result.returncode == lane_run.EXIT_REFUSED
    assert result.stdout.splitlines() == [f"{GOOD} ok", f"{BAD} refused"]
    assert f"bws exited 1 reading {BAD}" in result.stderr
    for value in (SECRET_VALUE, TOKEN_VALUE):
        assert value not in result.stdout + result.stderr


def test_read_runs_bws_under_the_lane_environment_with_the_token_in_no_argument(
    lane: Lane,
) -> None:
    lane.run("--read", GOOD, BWS_ACCESS_TOKEN=TOKEN_VALUE, OTHER="not-passed")
    argv = (lane.record / "bws-argv").read_text()
    environment = (lane.record / "bws-env").read_text()

    assert argv.splitlines() == [f"secret get {GOOD} --color no"]
    assert TOKEN_VALUE not in argv
    assert f"BWS_ACCESS_TOKEN={TOKEN_VALUE}" in environment
    assert "OTHER=" not in environment
    assert f"PATH={lane_run.LANE_PATH}" in environment


def test_read_exits_zero_only_when_every_read_is_ok(lane: Lane) -> None:
    result = lane.run("--read", GOOD, GOOD, BWS_ACCESS_TOKEN=TOKEN_VALUE)

    assert result.returncode == lane_run.EXIT_OK
    assert result.stdout.splitlines() == [f"{GOOD} ok", f"{GOOD} ok"]


@pytest.mark.parametrize(
    ("argv", "environment", "reason"),
    [
        (["--read"], {"BWS_ACCESS_TOKEN": TOKEN_VALUE}, "usage"),
        (["--read", "--help"], {"BWS_ACCESS_TOKEN": TOKEN_VALUE}, "not a BWS secret UUID"),
        (["--read", GOOD], {}, "BWS_ACCESS_TOKEN is not set"),
        (["--read", GOOD], {"BWS_ACCESS_TOKEN": ""}, "BWS_ACCESS_TOKEN is not set"),
        ([], {}, "usage"),
    ],
)
def test_read_refuses_before_running_bws(
    lane: Lane, argv: list[str], environment: dict[str, str], reason: str
) -> None:
    result = lane.run(*argv, **environment)

    assert result.returncode == lane_run.EXIT_USAGE
    assert reason in result.stderr
    assert not (lane.record / "bws-argv").exists()


def test_read_without_bws_on_path_is_a_usage_error(lane: Lane) -> None:
    (lane.bin / "bws").unlink()
    result = lane.run("--read", GOOD, BWS_ACCESS_TOKEN=TOKEN_VALUE)

    assert result.returncode == lane_run.EXIT_USAGE
    assert "bws is not on PATH" in result.stderr


def test_the_schedule_switch_and_lane_tokens_are_not_in_the_list() -> None:
    passed = (*lane_run.COPIED, *lane_run.STATE_VARIABLES)

    assert "LANE_SCHEDULE_ENABLED" not in passed
    assert [name for name in passed if "TOKEN" in name] == ["BWS_ACCESS_TOKEN"]
    assert os.path.basename(lane_run.LAUNCHER_DIR) == "scripts"
