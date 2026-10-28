"""The three bridge launchers ping an OUTCOME, as `run-fork-sync-watch.sh` does: on an acting run,
`/start` before the program, then success on exit 0 or `/fail` with the report as its body on
anything else. A finding (3) pages; it is no longer a dead-man success.

Each real launcher runs from a throwaway tree with the real `sds-deadman.sh` and `sds-bws.sh`, fake
programs that print a report and exit as told, and `curl`, `bws` and `gh` stubs on PATH. Both BWS
identities are pre-set, so the Keychain is never read, and nothing is sent anywhere.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

PING_URL = "https://hc.example/ping/abcdef"
HEALTHCHECKS_KEY_UUID = "51509d5e-64f5-4781-ae82-b4ed00b644e1"
# Fetched by all three, and only on an acting run.
PROPOSE_UUID = "acccb346-4baa-43ec-a1d4-b4a400c048ee"
OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"


@dataclass(frozen=True)
class Lane:
    launcher: str
    installer: str
    plist: str
    check: str
    flag: str
    programs: tuple[str, ...]
    cadence: dict[str, object]
    grace: int


LANES = [
    Lane(
        "run-bump-proposer.sh",
        "install-bump-proposer-launchd.sh",
        "com.devon.bump-proposer.plist",
        "sds-bump-proposer",
        "--submit",
        ("bump-proposer",),
        {"schedule": "50 6 * * *"},
        14400,
    ),
    Lane(
        "run-change-proposer.sh",
        "install-change-proposer-launchd.sh",
        "com.devon.change-proposer.plist",
        "sds-change-proposer",
        "--submit",
        ("change-proposer",),
        {"timeout": 3600},
        1800,
    ),
    Lane(
        "run-work-carrier.sh",
        "install-work-carrier-launchd.sh",
        "com.devon.work-carrier.plist",
        "sds-work-carrier",
        "--register",
        ("work-watcher", "work-carrier"),
        {"schedule": "5 7 * * *"},
        14400,
    ),
]
CARRIER = LANES[2]
CHANGE_PROPOSER = LANES[1]

_CURL = """#!/usr/bin/env bash
config=0
for argument in "$@"; do
    case "$argument" in
        *healthcheck.alobar.net/api/v3/checks/*) printf '%s' "$LISTING"; exit 0 ;;
        --config) config=1 ;;
    esac
done
[ "$config" -eq 1 ] || exit 1
url="$(sed -n 's/^url = "\\(.*\\)"$/\\1/p')"
body=""
previous=""
for argument in "$@"; do
    [ "$previous" = "--data-binary" ] && body="$(cat "${argument#@}")"
    previous="$argument"
done
printf '%s\\t%s\\n' "$url" "$(printf '%s' "$body" | tr '\\n' '|')" >> "$PING_LOG"
"""
# Answers every secret except the one named in BWS_FAIL_UUID, which reads as unreadable.
_BWS = """#!/bin/sh
[ -z "${BWS_LOG:-}" ] || printf '%s\\n' "$*" >> "$BWS_LOG"
case " $* " in
    *" ${BWS_FAIL_UUID:-none} "*) exit 1 ;;
esac
printf '{"value": "not-a-real-key"}'
"""
_GH = "#!/bin/sh\necho not-a-real-gh-token\n"
# Records the NAMES of the variables it receives, never their values.
_PROGRAM = """#!/bin/sh
[ -z "${{ENV_LOG:-}}" ] || env | sed 's/=.*//' >> "$ENV_LOG"
echo "{name} found: subject-{name}"
exit "${{{variable}:-0}}"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _rc_variable(program: str) -> str:
    return "RC_" + program.upper().replace("-", "_")


def _run(
    tmp_path: Path,
    lane: Lane,
    *args: str,
    rc: int = 0,
    check: str | None = None,
    **env_overrides: str,
) -> tuple[int, list[tuple[str, ...]], str]:
    """Run `lane`'s launcher; its last program exits `rc`, the others 0 unless overridden."""
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (lane.launcher, "sds-deadman.sh", "sds-bws.sh"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    for program in lane.programs:
        _executable(venv / program, _PROGRAM.format(name=program, variable=_rc_variable(program)))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "curl", _CURL)
    _executable(stubs / "bws", _BWS)
    _executable(stubs / "gh", _GH)
    home = tmp_path / "home"
    home.mkdir()
    ping_log = tmp_path / "pings.txt"
    ping_log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN_BROAD": "not-a-real-identity",
        "BWS_ACCESS_TOKEN_SDS": "not-a-real-identity",
        "LISTING": json.dumps({"checks": [{"name": check or lane.check, "ping_url": PING_URL}]}),
        "PING_LOG": str(ping_log),
        "BWS_LOG": str(tmp_path / "bws.log"),
        _rc_variable(lane.programs[-1]): str(rc),
        **env_overrides,
    }
    result = subprocess.run(
        ["/bin/bash", str(root / "scripts" / lane.launcher), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    pings = [tuple(line.split("\t")) for line in ping_log.read_text().splitlines()]
    return result.returncode, pings, result.stdout + result.stderr


ids = [lane.check for lane in LANES]


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_an_acting_clean_pass_pings_start_then_success(tmp_path: Path, lane: Lane) -> None:
    rc, pings, output = _run(tmp_path, lane, lane.flag)
    assert rc == 0, output
    assert pings == [(f"{PING_URL}/start", ""), (PING_URL, "")]


@pytest.mark.parametrize("rc", [3, 2, 1, 127])
@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_every_other_exit_pings_fail_with_the_report_as_its_body(
    tmp_path: Path, lane: Lane, rc: int
) -> None:
    """3 (a finding) above all: the dead-man switch reported it healthy; this check pages."""
    code, pings, output = _run(tmp_path, lane, lane.flag, rc=rc)
    assert code == rc, output
    assert pings[0] == (f"{PING_URL}/start", "")
    assert pings[1][0] == f"{PING_URL}/fail"
    assert f"{lane.programs[-1]} found: subject-{lane.programs[-1]}" in pings[1][1]
    assert len(pings) == 2


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_a_run_without_the_acting_flag_pings_nothing(tmp_path: Path, lane: Lane) -> None:
    rc, pings, output = _run(tmp_path, lane, rc=3)
    assert rc == 3, output
    assert pings == []


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_the_report_is_also_written_to_the_lane_log(tmp_path: Path, lane: Lane) -> None:
    _, _, output = _run(tmp_path, lane, lane.flag, rc=3)
    for program in lane.programs:
        assert f"{program} found: subject-{program}" in output


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_an_unresolved_check_never_gates_the_pass(tmp_path: Path, lane: Lane) -> None:
    rc, pings, output = _run(tmp_path, lane, lane.flag, rc=3, check="some-other-check")
    assert rc == 3
    assert pings == []
    assert f"no check named '{lane.check}'" in output
    assert f"{lane.programs[-1]} found" in output


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_the_launcher_does_not_arm_the_dead_man_switch(lane: Lane) -> None:
    """Arming it maps the finding code to a success ping, the inversion this change removes; and
    `test_sds_deadman.py` selects launchers by that function's name in their text."""
    assert "sds_deadman_arm" not in (Path("scripts") / lane.launcher).read_text()


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_a_missing_credential_pings_fail_with_its_reason(tmp_path: Path, lane: Lane) -> None:
    rc, pings, output = _run(tmp_path, lane, lane.flag, BWS_FAIL_UUID=PROPOSE_UUID)
    assert rc == 1, output
    assert pings[0] == (f"{PING_URL}/start", "")
    assert pings[1][0] == f"{PING_URL}/fail"
    assert "FATAL: could not read the" in pings[1][1]
    assert "propose" in pings[1][1]
    assert len(pings) == 2
    assert "found: subject" not in output


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_the_failure_line_is_printed_only_when_a_failure_is_pinged(
    tmp_path: Path, lane: Lane
) -> None:
    _, _, bare = _run(tmp_path / "bare", lane, rc=3)
    _, _, acting = _run(tmp_path / "acting", lane, lane.flag, rc=3)
    assert "reporting a failure" not in bare
    assert "exit 3 — reporting a failure" in acting


def test_the_carrier_report_holds_both_programs_and_pings_the_worst_code(tmp_path: Path) -> None:
    rc, pings, _ = _run(tmp_path, CARRIER, "--register", rc=0, RC_WORK_WATCHER="3")
    assert rc == 3
    assert pings[1][0] == f"{PING_URL}/fail"
    assert "work-watcher found: subject-work-watcher" in pings[1][1]
    assert "work-carrier found: subject-work-carrier" in pings[1][1]


def test_the_carrier_fetches_the_member_record_bearer_without_the_acting_flag(
    tmp_path: Path,
) -> None:
    """The grant is the scope authority, so even a bare pass needs the observer bearer."""
    rc, _, output = _run(tmp_path, CARRIER, rc=0)
    assert rc == 0, output
    assert OBSERVER_UUID in (tmp_path / "bws.log").read_text()


@pytest.mark.parametrize("args", [(), ("--register",)], ids=["bare", "register"])
def test_the_carrier_exports_the_member_bearer(tmp_path: Path, args: tuple[str, ...]) -> None:
    env_log = tmp_path / "env.txt"
    rc, _, output = _run(tmp_path, CARRIER, *args, rc=0, ENV_LOG=str(env_log))
    assert rc == 0, output
    assert "SDS_MEMBERS_OBSERVER_TOKEN" in set(env_log.read_text().split())


def test_the_bump_proposer_fetches_the_member_record_bearer_without_the_acting_flag(
    tmp_path: Path,
) -> None:
    """The grant is the scope authority, so even a dry run needs the observer bearer."""
    rc, _, output = _run(tmp_path, LANES[0], rc=0)
    assert rc == 0, output
    assert OBSERVER_UUID in (tmp_path / "bws.log").read_text()


def test_a_dry_run_bump_proposer_whose_observer_bearer_is_unreadable_never_runs(
    tmp_path: Path,
) -> None:
    """A dry run pings nothing, so the failure shows as the exit code and the message alone."""
    env_log = tmp_path / "env.txt"
    rc, pings, output = _run(tmp_path, LANES[0], BWS_FAIL_UUID=OBSERVER_UUID, ENV_LOG=str(env_log))
    assert rc == 1, output
    assert pings == []
    assert "observer bearer" in output
    assert "found: subject" not in output
    assert not env_log.exists()


def test_an_acting_bump_proposer_whose_observer_bearer_is_unreadable_pings_fail(
    tmp_path: Path,
) -> None:
    rc, pings, output = _run(tmp_path, LANES[0], "--submit", BWS_FAIL_UUID=OBSERVER_UUID)
    assert rc == 1, output
    assert pings[1][0] == f"{PING_URL}/fail"
    assert "observer bearer" in pings[1][1]
    assert "found: subject" not in output


def test_the_change_proposer_fetches_the_member_record_bearer_without_the_acting_flag(
    tmp_path: Path,
) -> None:
    """The grant is the scope authority, so even a dry run needs the observer bearer."""
    rc, _, output = _run(tmp_path, CHANGE_PROPOSER, rc=0)
    assert rc == 0, output
    assert OBSERVER_UUID in (tmp_path / "bws.log").read_text()
    assert PROPOSE_UUID not in (tmp_path / "bws.log").read_text()


def test_a_dry_run_change_proposer_whose_observer_bearer_is_unreadable_never_runs(
    tmp_path: Path,
) -> None:
    """A dry run pings nothing, so the failure shows as the exit code and the message alone."""
    env_log = tmp_path / "env.txt"
    rc, pings, output = _run(
        tmp_path, CHANGE_PROPOSER, BWS_FAIL_UUID=OBSERVER_UUID, ENV_LOG=str(env_log)
    )
    assert rc == 1, output
    assert pings == []
    assert "observer bearer" in output
    assert "found: subject" not in output
    assert not env_log.exists()


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry-run", "submit"])
def test_the_change_proposer_exports_the_member_bearer(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    env_log = tmp_path / "env.txt"
    rc, _, output = _run(tmp_path, CHANGE_PROPOSER, *args, ENV_LOG=str(env_log))
    assert rc == 0, output
    assert "SDS_MEMBERS_OBSERVER_TOKEN" in set(env_log.read_text().split())


def _exported_names(tmp_path: Path, *args: str) -> set[str]:
    env_log = tmp_path / "env.txt"
    rc, _, output = _run(tmp_path, LANES[0], *args, ENV_LOG=str(env_log))
    assert rc == 0, output
    return set(env_log.read_text().split())


ORCHESTRATOR_API_NAMES = {
    "ORCHESTRATOR_API_URL",
    "ORCHESTRATOR_API_CREDENTIAL_KEY_ID",
    "ORCHESTRATOR_API_TOKEN",
}


def test_a_dry_run_bump_proposer_exports_the_member_bearer_and_not_the_write_names(
    tmp_path: Path,
) -> None:
    names = _exported_names(tmp_path)
    assert "SDS_MEMBERS_OBSERVER_TOKEN" in names
    assert not names & ORCHESTRATOR_API_NAMES


def test_an_acting_bump_proposer_exports_the_member_bearer_and_the_write_names(
    tmp_path: Path,
) -> None:
    names = _exported_names(tmp_path, "--submit")
    assert "SDS_MEMBERS_OBSERVER_TOKEN" in names
    assert ORCHESTRATOR_API_NAMES <= names


def test_an_acting_carrier_whose_observer_bearer_is_unreadable_pings_fail(tmp_path: Path) -> None:
    rc, pings, output = _run(tmp_path, CARRIER, "--register", BWS_FAIL_UUID=OBSERVER_UUID)
    assert rc == 1, output
    assert pings[1][0] == f"{PING_URL}/fail"
    assert "observer credential" in pings[1][1]
    assert "found: subject" not in output


# ---- the installers' Healthchecks checks ------------------------------------------------------

_INSTALL_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$CURL_ARGS"
cat >> "$CURL_STDIN"
printf '%s' "${HC_STATUS:-201}"
"""


def _install(tmp_path: Path, lane: Lane, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    """Run `lane`'s installer in a throwaway main tree with every outside call stubbed."""
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (lane.installer, "sds-install.sh", "sds-bws.sh", lane.plist):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    for program in (*lane.programs, "orchestrator"):
        _executable(venv / program, "#!/bin/sh\n")
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    home = tmp_path / "home"
    home.mkdir()
    # The bump proposer's installer reads its packages checkout back out of the plist.
    packages = tmp_path / "intent-packages"
    (packages / "packages").mkdir(parents=True)
    (packages / ".venv" / "bin").mkdir(parents=True)
    _executable(packages / ".venv" / "bin" / "python", "#!/bin/sh\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "curl", _INSTALL_CURL)
    _executable(stubs / "bws", _BWS)
    _executable(stubs / "launchctl", '#!/bin/sh\necho "$*" >> "$LAUNCHCTL_LOG"\n')
    _executable(stubs / "plutil", f"#!/bin/sh\necho '{packages}'\n")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN_BROAD": "not-a-real-identity",
        "CURL_ARGS": str(tmp_path / "args"),
        "CURL_STDIN": str(tmp_path / "stdin"),
        "LAUNCHCTL_LOG": str(tmp_path / "launchctl.log"),
        **env_overrides,
    }
    return subprocess.run(
        ["/bin/bash", str(root / "scripts" / lane.installer)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("lane", LANES, ids=ids)
def test_the_installer_upserts_its_check_to_alert_hub(tmp_path: Path, lane: Lane) -> None:
    result = _install(tmp_path, lane)
    assert result.returncode == 0, result.stderr
    assert f"Healthchecks check {lane.check} created" in result.stdout
    args = (tmp_path / "args").read_text()
    assert "not-a-real-key" not in args + result.stdout + result.stderr
    payload = json.loads(args.split("--data-binary ", 1)[1].strip())
    assert payload["name"] == lane.check
    assert payload["channels"] == "alert-hub"
    assert payload["unique"] == ["name"]
    assert payload["tz"] == "America/New_York"
    assert payload["grace"] == lane.grace
    for key, value in lane.cadence.items():
        assert payload[key] == value


def test_an_installer_whose_upsert_fails_still_installs(tmp_path: Path) -> None:
    """The pass still runs and logs without its check; rerunning the installer retries it."""
    lane = LANES[1]
    result = _install(tmp_path, lane, HC_STATUS="500")
    assert result.returncode == 0, result.stderr
    assert f"WARNING: Healthchecks check {lane.check} was not upserted" in result.stderr
    assert "bootstrap" in (tmp_path / "launchctl.log").read_text()
    assert (tmp_path / "home" / "Library" / "LaunchAgents" / lane.plist).exists()
