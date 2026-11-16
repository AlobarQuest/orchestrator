"""`scripts/sds-git.sh`: git's GitHub credential from the environment (ADR-0059 decision 2).

The helper runs under `env -i` in a real bash. A `base64` stand-in on PATH records its argv and
stdin, so the token is shown to reach it only on stdin; real git then reads the exported
configuration, so the header is shown to be what git would send.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest

HELPER = Path("scripts/sds-git.sh").resolve()
TOKEN = "ghs_a-minted-installation-token"
HEADER = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
_BASE64 = """#!/bin/sh
printf '%s\\n' "$*" >> "$ARGV_LOG"
tee -a "$STDIN_LOG" | /usr/bin/base64
"""


def _run(tmp_path: Path, call: str, **env: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    (stubs / "base64").write_text(_BASE64)
    (stubs / "base64").chmod(0o755)
    logs = {"argv": tmp_path / "argv.log", "stdin": tmp_path / "stdin.log"}
    for log in logs.values():
        log.write_text("")
    script = f'source "{HELPER}"\n{call}\nrc=$?\nenv | sort\nexit $rc\n'
    result = subprocess.run(
        [
            "/usr/bin/env",
            "-i",
            f"PATH={stubs}:/usr/bin:/bin",
            f"HOME={tmp_path}",
            f"ARGV_LOG={logs['argv']}",
            f"STDIN_LOG={logs['stdin']}",
            f"LANE_GITHUB_TOKEN={TOKEN}",
            *(f"{key}={value}" for key, value in env.items()),
            "/bin/bash",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    exported = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return result, {"env": exported, **{k: v.read_text() for k, v in logs.items()}}


def test_it_exports_the_header_pair_and_git_reads_no_file_and_prompts_for_nothing(
    tmp_path: Path,
) -> None:
    result, seen = _run(tmp_path, "sds_git_credential LANE_GITHUB_TOKEN")
    assert result.returncode == 0, result.stderr
    env = seen["env"]
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_0"] == HEADER
    assert not any(key.startswith(("GIT_CONFIG_KEY_1", "GIT_CONFIG_KEY_2")) for key in env)
    assert TOKEN not in result.stderr


def test_the_token_reaches_base64_on_stdin_and_never_in_its_arguments(tmp_path: Path) -> None:
    result, seen = _run(tmp_path, "sds_git_credential LANE_GITHUB_TOKEN")
    assert result.returncode == 0, result.stderr
    assert seen["stdin"] == f"x-access-token:{TOKEN}"
    assert seen["argv"] == "\n"
    assert TOKEN not in seen["argv"]


def test_a_long_token_encodes_on_one_line(tmp_path: Path) -> None:
    """GNU base64 wraps at 76 columns; the header must be one unbroken value."""
    long_token = "x" * 120
    result, seen = _run(
        tmp_path, "sds_git_credential LANE_GITHUB_TOKEN", LANE_GITHUB_TOKEN=long_token
    )
    assert result.returncode == 0, result.stderr
    expected = base64.b64encode(f"x-access-token:{long_token}".encode()).decode()
    assert seen["env"]["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {expected}"


def test_real_git_reads_the_header_and_no_global_identity(tmp_path: Path) -> None:
    call = (
        "sds_git_credential LANE_GITHUB_TOKEN && "
        "git config --get http.https://github.com/.extraheader && "
        "{ git config --get user.name; echo user-rc=$?; }"
    )
    result, _ = _run(tmp_path, call)
    assert result.returncode == 0, result.stderr
    assert HEADER in result.stdout.splitlines()
    assert "user-rc=1" in result.stdout


def test_the_identity_pairs_are_set_only_when_passed(tmp_path: Path) -> None:
    result, seen = _run(
        tmp_path,
        "sds_git_credential LANE_GITHUB_TOKEN 'sds-lanes[bot]' "
        "'1+sds-lanes[bot]@users.noreply.github.com'",
    )
    assert result.returncode == 0, result.stderr
    env = seen["env"]
    assert env["GIT_CONFIG_COUNT"] == "3"
    assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == ("user.name", "sds-lanes[bot]")
    assert (env["GIT_CONFIG_KEY_2"], env["GIT_CONFIG_VALUE_2"]) == (
        "user.email",
        "1+sds-lanes[bot]@users.noreply.github.com",
    )


def test_git_trace_is_turned_off(tmp_path: Path) -> None:
    result, seen = _run(
        tmp_path,
        "sds_git_credential LANE_GITHUB_TOKEN",
        GIT_TRACE="1",
        GIT_TRACE_CURL="1",
        GIT_TRACE_PACKET="1",
        GIT_CURL_VERBOSE="1",
    )
    assert result.returncode == 0, result.stderr
    for name in ("GIT_TRACE", "GIT_TRACE_CURL", "GIT_TRACE_PACKET", "GIT_CURL_VERBOSE"):
        assert name not in seen["env"]


@pytest.mark.parametrize(
    ("call", "env", "message"),
    [
        ("sds_git_credential LANE_GITHUB_TOKEN", {"LANE_GITHUB_TOKEN": ""}, "is empty"),
        ("sds_git_credential UNSET_VARIABLE", {}, "is empty"),
        ("sds_git_credential 'not a name'", {}, "is not a variable name"),
        ("sds_git_credential ''", {}, "is not a variable name"),
        ("sds_git_credential LANE_GITHUB_TOKEN 'sds-lanes[bot]'", {}, "pass a variable name"),
        ("sds_git_credential LANE_GITHUB_TOKEN '' 'a@b'", {}, "needs both"),
    ],
)
def test_a_refusal_exports_nothing(
    tmp_path: Path, call: str, env: dict[str, str], message: str
) -> None:
    result, seen = _run(tmp_path, call, **env)
    assert result.returncode == 1
    assert message in result.stderr
    assert not any(key.startswith("GIT_CONFIG") for key in seen["env"])
    assert "GIT_TERMINAL_PROMPT" not in seen["env"]
