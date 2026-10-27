"""The landing ledger, the activation sweep and fork-harden take their scope from the member record.
The membership check is an outcome alarm that reads the record itself.

Each launcher runs from a throwaway tree holding copies of its helper scripts and a fake
`.venv/bin`: `python -m scripts.sds_scope <population>` is a stub that answers from the
environment, and the lane's own program only logs its arguments. `bws` and `curl` are stubs on
PATH, so nothing is read from BWS or the Keychain and nothing is sent anywhere. The launchers run
under `env -i`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

LEDGER = Path("scripts/run-landing-ledger.sh")
SWEEP = Path("scripts/run-activation-sweep.sh")
HARDEN = Path("scripts/run-fork-harden.sh")
OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
HEALTHCHECKS_KEY_UUID = "51509d5e-64f5-4781-ae82-b4ed00b644e1"

_PYTHON = """#!/bin/bash
if [ "$1" = "-c" ]; then exec "$REAL_PYTHON" "$@"; fi
population="$3"
key="$(printf '%s' "$population" | tr a-z A-Z)"
printf '%s token=%s\\n' "$population" "$SDS_SCOPE_ORCHESTRATOR_TOKEN" >> "$SCOPE_LOG"
rc_var="FAKE_${key}_RC"
names_var="FAKE_${key}"
printf '%s' "${!names_var}"
exit "${!rc_var:-0}"
"""
_LANE = """#!/bin/bash
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$LANE_LOG"
exit "${FAKE_LANE_RC:-0}"
"""
_BWS = """#!/bin/sh
printf '%s\\n' "$3" >> "$BWS_LOG"
printf '{"value": "value-of-%s"}' "$3"
"""
_CURL = "#!/bin/sh\nexit 1\n"


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(
    tmp_path: Path, launcher: Path, programs: list[str], **fake: str
) -> tuple[int, list[str], list[str], list[str]]:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (launcher.name, "sds-deadman.sh", "sds-bws.sh", "sds-token.sh"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    _executable(venv / "python", _PYTHON)
    for program in programs:
        _executable(venv / program, _LANE)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "bws", _BWS)
    _executable(stubs / "curl", _CURL)
    home = tmp_path / "home"
    home.mkdir()
    logs = {name: tmp_path / f"{name}.log" for name in ("scope", "lane", "bws")}
    for log in logs.values():
        log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "REAL_PYTHON": sys.executable,
        "BWS_ACCESS_TOKEN": "not-a-real-identity",
        "BWS_ACCESS_TOKEN_SDS": "not-a-real-identity",
        "BWS_ACCESS_TOKEN_BROAD": "not-a-real-identity",
        "LANDING_LEDGER_GITHUB_TOKEN": "not-a-real-token",
        "SCOPE_LOG": str(logs["scope"]),
        "LANE_LOG": str(logs["lane"]),
        "BWS_LOG": str(logs["bws"]),
        **fake,
    }
    result = subprocess.run(
        [
            "/usr/bin/env",
            "-i",
            *(f"{k}={v}" for k, v in env.items()),
            "/bin/bash",
            str(root / "scripts" / launcher.name),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [log.read_text().splitlines() for log in logs.values()]
    return result.returncode, lines[0], lines[1], lines[2]


def test_ledger_passes_each_scoped_repository(tmp_path: Path) -> None:
    rc, scope, lane, bws = _run(tmp_path, LEDGER, ["landing-ledger"], FAKE_LEDGER="o/one\no/two")
    assert rc == 0
    assert scope == [f"ledger token=value-of-{OBSERVER_UUID}"]
    assert lane[0].startswith(
        "landing-ledger record --repository o/one --repository o/two --days 7"
    )
    assert lane[1].startswith("landing-ledger audit --repository o/one --repository o/two")
    assert OBSERVER_UUID in bws
    assert "314f276d-55ca-4ddc-a24d-b4a3013508cd" not in bws


def test_ledger_still_runs_and_reports_incomplete_when_scope_is_incomplete(tmp_path: Path) -> None:
    rc, _, lane, _ = _run(
        tmp_path, LEDGER, ["landing-ledger"], FAKE_LEDGER="o/one", FAKE_LEDGER_RC="3"
    )
    assert rc == 3
    assert len(lane) == 2


def test_ledger_refuses_an_empty_scope(tmp_path: Path) -> None:
    rc, _, lane, _ = _run(tmp_path, LEDGER, ["landing-ledger"], FAKE_LEDGER="")
    assert rc == 3
    assert lane == []


def test_ledger_passes_through_a_scope_failure(tmp_path: Path) -> None:
    rc, _, lane, _ = _run(
        tmp_path, LEDGER, ["landing-ledger"], FAKE_LEDGER="o/one", FAKE_LEDGER_RC="127"
    )
    assert rc == 127
    assert lane == []


def test_sweep_takes_its_checkouts_from_scope(tmp_path: Path) -> None:
    rc, scope, lane, _ = _run(
        tmp_path,
        SWEEP,
        ["activation-sweep"],
        FAKE_CHECKOUTS="alpha\nbeta",
        FAKE_BINDABLE="alpha",
    )
    home = tmp_path / "home"
    assert rc == 0
    assert scope == [
        f"checkouts token=value-of-{OBSERVER_UUID}",
        f"bindable token=value-of-{OBSERVER_UUID}",
    ]
    assert lane[0].startswith(
        f"activation-sweep sweep --checkout {home}/Projects/alpha --checkout {home}/Projects/beta"
    )
    assert lane[1].startswith(f"activation-sweep bind --checkout {home}/Projects/alpha")
    assert "beta" not in lane[1]


def test_sweep_reports_incomplete_when_scope_fails(tmp_path: Path) -> None:
    rc, _, lane, _ = _run(
        tmp_path,
        SWEEP,
        ["activation-sweep"],
        FAKE_CHECKOUTS_RC="3",
        FAKE_BINDABLE="alpha",
    )
    assert rc == 3
    assert [line.split()[1] for line in lane] == ["bind"]


def test_sweep_does_not_swallow_an_unrecognised_scope_failure(tmp_path: Path) -> None:
    rc, _, lane, _ = _run(
        tmp_path,
        SWEEP,
        ["activation-sweep"],
        FAKE_CHECKOUTS="alpha",
        FAKE_BINDABLE_RC="127",
    )
    assert rc == 127
    assert [line.split()[1] for line in lane] == ["sweep"]


def test_fork_harden_passes_each_granted_name(tmp_path: Path) -> None:
    rc, scope, lane, bws = _run(
        tmp_path, HARDEN, ["fork-harden"], FAKE_GRANTED="o/one\no/two", FAKE_LANE_RC="0"
    )
    assert rc == 0
    assert scope == [f"granted token=value-of-{OBSERVER_UUID}"]
    assert lane == ["fork-harden run --push --granted o/one --granted o/two"]
    assert OBSERVER_UUID in bws
    assert "314f276d-55ca-4ddc-a24d-b4a3013508cd" not in bws


def test_fork_harden_reads_only_the_observer_bearer_and_the_ping_key(tmp_path: Path) -> None:
    _, _, _, bws = _run(tmp_path, HARDEN, ["fork-harden"], FAKE_GRANTED="o/one")
    assert set(bws) <= {OBSERVER_UUID, HEALTHCHECKS_KEY_UUID}
    assert bws.count(OBSERVER_UUID) == 1


def test_fork_harden_exits_2_without_hardening_when_scope_fails(tmp_path: Path) -> None:
    rc, _, lane, _ = _run(
        tmp_path, HARDEN, ["fork-harden"], FAKE_GRANTED="o/one", FAKE_GRANTED_RC="3"
    )
    assert rc == 2
    assert lane == []


def test_fork_harden_exits_2_without_hardening_when_scope_cannot_run(tmp_path: Path) -> None:
    rc, _, lane, _ = _run(
        tmp_path, HARDEN, ["fork-harden"], FAKE_GRANTED="o/one", FAKE_GRANTED_RC="127"
    )
    assert rc == 2
    assert lane == []


def test_fork_harden_passes_the_programs_exit_code_through(tmp_path: Path) -> None:
    rc, _, lane, _ = _run(tmp_path, HARDEN, ["fork-harden"], FAKE_GRANTED="o/one", FAKE_LANE_RC="3")
    assert rc == 3
    assert len(lane) == 1


MEMBERSHIP = Path("scripts/run-membership-check.sh")
CHANGE_MANAGER_UUID = "314f276d-55ca-4ddc-a24d-b4a3013508cd"

_CHECK_PROGRAM = """#!/bin/bash
printf 'args=%s observer=%s change_manager=%s github=%s\\n' "$*" "$SDS_MEMBERS_OBSERVER_TOKEN" \\
  "$SDS_MEMBERS_CHANGE_MANAGER_TOKEN" "$SDS_MEMBERS_GITHUB_TOKEN" >> "$LANE_LOG"
echo "the report"
exit "${FAKE_CHECK_RC:-0}"
"""
_REAL_PYTHON = """#!/bin/bash
exec "$REAL_PYTHON" "$@"
"""
_RECORDING_BWS = """#!/bin/sh
printf '%s\\n' "$3" >> "$BWS_LOG"
if [ "$3" = "$FAKE_EMPTY_UUID" ]; then printf '{"value": ""}'; exit 0; fi
printf '{"value": "value-of-%s"}' "$3"
"""
_PINGING_CURL = """#!/bin/sh
case "$*" in
  *"--config -"*) cat >> "$PING_LOG"; echo "body=$*" >> "$PING_LOG" ;;
  *) printf '{"checks": [{"name": "sds-membership-check", "ping_url": "https://hc.test/p"}]}' ;;
esac
"""
_GH = "#!/bin/sh\necho gh-token\n"


def _membership(tmp_path: Path, **fake: str) -> tuple[int, list[str], list[str], list[str]]:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (MEMBERSHIP.name, "sds-deadman.sh", "sds-bws.sh"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    _executable(venv / "python", _REAL_PYTHON)
    _executable(venv / "sds-members", _CHECK_PROGRAM)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "bws", _RECORDING_BWS)
    _executable(stubs / "curl", _PINGING_CURL)
    _executable(stubs / "gh", _GH)
    home = tmp_path / "home"
    home.mkdir()
    logs = {name: tmp_path / f"{name}.log" for name in ("lane", "bws", "ping")}
    for log in logs.values():
        log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "REAL_PYTHON": sys.executable,
        "BWS_ACCESS_TOKEN_SDS": "not-a-real-identity",
        "BWS_ACCESS_TOKEN_BROAD": "not-a-real-identity",
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
            str(root / "scripts" / MEMBERSHIP.name),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        result.returncode,
        logs["lane"].read_text().splitlines(),
        logs["bws"].read_text().splitlines(),
        logs["ping"].read_text().splitlines(),
    )


def test_membership_check_hands_each_credential_to_the_program(tmp_path: Path) -> None:
    rc, lane, bws, _ = _membership(tmp_path)
    assert rc == 0
    assert lane == [
        f"args=check observer=value-of-{OBSERVER_UUID} "
        f"change_manager=value-of-{CHANGE_MANAGER_UUID} github=gh-token"
    ]
    assert set(bws) == {OBSERVER_UUID, CHANGE_MANAGER_UUID, HEALTHCHECKS_KEY_UUID}


def test_membership_check_pings_start_then_success_with_the_report(tmp_path: Path) -> None:
    _, _, _, pings = _membership(tmp_path)
    urls = [line for line in pings if line.startswith("url =")]
    assert urls == ['url = "https://hc.test/p/start"', 'url = "https://hc.test/p"']
    assert any("--data-binary" in line for line in pings)


def test_membership_check_pings_fail_with_the_report_on_every_nonzero_exit(tmp_path: Path) -> None:
    for code in ("1", "2", "3"):
        rc, _, _, pings = _membership(tmp_path / code, FAKE_CHECK_RC=code)
        assert rc == int(code)
        urls = [line for line in pings if line.startswith("url =")]
        assert urls[-1] == 'url = "https://hc.test/p/fail"'
        assert any("--data-binary" in line for line in pings)


def test_membership_check_fails_without_running_when_a_credential_is_empty(tmp_path: Path) -> None:
    rc, lane, _, pings = _membership(tmp_path, FAKE_EMPTY_UUID=CHANGE_MANAGER_UUID)
    assert rc == 1
    assert lane == []
    assert [line for line in pings if line.startswith("url =")][-1].endswith('/fail"')


def test_membership_check_never_arms_the_dead_man_switch() -> None:
    assert "sds_deadman_arm" not in MEMBERSHIP.read_text()
