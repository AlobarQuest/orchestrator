"""`scripts/sds-deadman.sh` decides what reaches the alert channel. This tests the shell.

**A FINDING IS NOT A LIVENESS FAILURE, AND THE CODE THAT MEANS "FOUND" IS NOT SHARED.** The switch
answers one question -- did this lane run and report. Until 2026-08-29 it pinged `/fail` on every
non-zero exit, which collapsed "the tool broke" into "the lane found something" and produced the
silence it was added to prevent: while a check sits failed on a standing finding, a lane that STOPS
causes no state change at the alert channel.

**THE TWO GROUPS OF ARMED LAUNCHERS DISAGREE ABOUT 2 AND 3, IN OPPOSITE DIRECTIONS.** The ledger,
the deploy watcher and the activation sweep read 2 as found and 3 as could-not-be-read; the estate
lander and the rotation proposer read 2 as could-not-use-its-inputs and 3 as found. So a test that
exercises one scheme proves the half that was already working and says nothing about the launchers
a universal rule would invert. Every case below is therefore driven from the code the launcher
ITSELF declares, read out of its own source, and
`test_the_two_schemes_are_both_present_so_neither_test_above_is_vacuous` fails if the armed
launchers stop covering both. Outcome-alarm launchers arm nothing and are not selected here.

The REAL helper is sourced by a driver script whose `curl` and `bws` are stubs on a prepended PATH,
so the resolve path executes exactly as it does in production -- one listing request, one match by
exact name -- while no credential is read and nothing is sent anywhere. `BWS_ACCESS_TOKEN_BROAD` is
pre-set, which is the escape hatch the helper already offers, so the Keychain is never touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path("scripts")
HELPER = SCRIPTS / "sds-deadman.sh"
PING_URL = "https://hc.example/ping/abcdef"

# One line per launcher that arms the switch, and the code it declares.
ARMS = re.compile(r"^sds_deadman_arm (?P<check>\S+) --finding (?P<code>\d+) ", re.MULTILINE)
# The one line of a launcher's own `EXIT CODES` block that names a finding. Every launcher spells
# it "something was found"; the zero line says "nothing was found", which this does not match.
DECLARES_FINDING = re.compile(r"^#\s+(?P<code>\d+)\s+something was found", re.MULTILINE)

_CURL_STUB = """#!/usr/bin/env bash
for argument in "$@"; do
    case "$argument" in
        *healthcheck.alobar.net/api/v3/checks/*)
            printf '%s' "$LISTING"
            exit 0
            ;;
        https://hc.example/*)
            echo "$argument" >> "$PING_LOG"
            exit 0
            ;;
    esac
done
exit 1
"""

_BWS_STUB = """#!/usr/bin/env bash
printf '{"value": "not-a-real-key"}'
"""


def launchers() -> list[Path]:
    """Every launcher that arms the switch, found by what it does rather than by a list here."""
    return sorted(
        path for path in SCRIPTS.glob("run-*.sh") if "sds_deadman_arm" in path.read_text()
    )


def declared_codes() -> dict[str, tuple[Path, int]]:
    """`check name -> (launcher, declared finding code)`, read from the launchers themselves."""
    found: dict[str, tuple[Path, int]] = {}
    for path in launchers():
        match = ARMS.search(path.read_text())
        assert match is not None, f"{path} arms the switch without declaring a --finding code"
        found[match.group("check")] = (path, int(match.group("code")))
    return found


@pytest.fixture()
def bin_stubs(tmp_path: Path) -> Path:
    """A directory holding a `curl` and a `bws` this test can prepend to PATH."""
    stubs = tmp_path / "stubbin"
    stubs.mkdir()
    for name, body in (("curl", _CURL_STUB), ("bws", _BWS_STUB)):
        script = stubs / name
        script.write_text(body)
        script.chmod(0o755)
    return stubs


def _run(
    tmp_path: Path,
    stubs: Path,
    *,
    check_name: str,
    exit_code: int,
    finding: str | None,
    listing_name: str | None = None,
    arguments: str = "",
) -> tuple[int, list[str], str]:
    """Source the real helper, arm it, exit with `exit_code`; return (rc, pings, stderr+stdout)."""
    ping_log = tmp_path / "pings.txt"
    ping_log.write_text("")
    listing = json.dumps({"checks": [{"name": listing_name or check_name, "ping_url": PING_URL}]})
    declaration = "" if finding is None else f"--finding {finding} "
    driver = tmp_path / "driver.sh"
    driver.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f"source {HELPER.resolve()}\n"
        f"sds_deadman_arm {check_name} {declaration}{arguments}\n"
        f"exit {exit_code}\n"
    )
    driver.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = f"{stubs}{os.pathsep}{environment['PATH']}"
    environment["BWS_ACCESS_TOKEN_BROAD"] = "not-a-real-identity"
    environment["LISTING"] = listing
    environment["PING_LOG"] = str(ping_log)
    completed = subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(driver)],
        capture_output=True,
        text=True,
        env=environment,
    )
    pings = [line for line in ping_log.read_text().splitlines() if line]
    return completed.returncode, pings, completed.stdout + completed.stderr


# ---------------------------------------------------------------------------------------------
# The mapping, exercised against BOTH schemes with each launcher's own declared code.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("check_name", sorted(declared_codes()))
def test_a_launchers_own_finding_code_reports_the_pass_as_healthy(
    tmp_path: Path, bin_stubs: Path, check_name: str
) -> None:
    """The whole change. Both schemes are here: three launchers declare 2 and four declare 3."""
    _, code = declared_codes()[check_name]

    rc, pings, _ = _run(tmp_path, bin_stubs, check_name=check_name, exit_code=code, finding=None)
    unmapped = pings

    rc, pings, output = _run(
        tmp_path, bin_stubs, check_name=check_name, exit_code=code, finding=str(code)
    )

    assert rc == code, "the switch must never change the pass's own exit code"
    assert pings == [f"{PING_URL}/start", PING_URL]
    assert "the pass ran and reported" in output
    # The control that makes the assertion above mean something: without the declaration the very
    # same exit pings `/fail`, so the pass is not merely being reported healthy by default.
    assert unmapped == [f"{PING_URL}/start", f"{PING_URL}/fail"]


@pytest.mark.parametrize("check_name", sorted(declared_codes()))
def test_the_other_groups_code_reports_a_failure(
    tmp_path: Path, bin_stubs: Path, check_name: str
) -> None:
    """2 and 3 mean opposite things in the two groups, so each launcher's OTHER code must fail.

    For a scheme-A launcher that is `could not be read`; for a scheme-B launcher it is `could not
    use its inputs`. Both are failures of the pass, and a universal rule keyed on either literal
    would get one of the two groups backwards.
    """
    _, code = declared_codes()[check_name]
    other = 3 if code == 2 else 2

    rc, pings, _ = _run(
        tmp_path, bin_stubs, check_name=check_name, exit_code=other, finding=str(code)
    )

    assert rc == other
    assert pings == [f"{PING_URL}/start", f"{PING_URL}/fail"]


@pytest.mark.parametrize("check_name", sorted(declared_codes()))
def test_a_clean_pass_and_a_tool_failure_are_unchanged(
    tmp_path: Path, bin_stubs: Path, check_name: str
) -> None:
    _, code = declared_codes()[check_name]

    _, clean, _ = _run(tmp_path, bin_stubs, check_name=check_name, exit_code=0, finding=str(code))
    _, broken, _ = _run(tmp_path, bin_stubs, check_name=check_name, exit_code=1, finding=str(code))

    assert clean == [f"{PING_URL}/start", PING_URL]
    assert broken == [f"{PING_URL}/start", f"{PING_URL}/fail"]


def test_a_code_outside_the_vocabulary_reports_a_failure(tmp_path: Path, bin_stubs: Path) -> None:
    """127 is the one that actually happens -- a missing binary -- and it is not a finding."""
    rc, pings, _ = _run(
        tmp_path, bin_stubs, check_name="sds-landing-ledger", exit_code=127, finding="2"
    )

    assert rc == 127
    assert pings == [f"{PING_URL}/start", f"{PING_URL}/fail"]


# ---------------------------------------------------------------------------------------------
# The declaration cannot be a code the mapping would invert.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("declared", ["1", "0", "two", ""])
def test_a_reserved_or_malformed_finding_code_is_refused(
    tmp_path: Path, bin_stubs: Path, declared: str
) -> None:
    """`--finding 1` would map a broken lane to a healthy ping, which is the inversion this whole
    change exists to prevent arriving through the mechanism meant to prevent it. Every refused
    value falls back to reporting a failure, and says so."""
    _, pings, output = _run(
        tmp_path, bin_stubs, check_name="sds-landing-ledger", exit_code=1, finding=declared
    )

    assert pings == [f"{PING_URL}/start", f"{PING_URL}/fail"]
    assert "ignored" in output


# ---------------------------------------------------------------------------------------------
# Everything else about the helper is unchanged.
# ---------------------------------------------------------------------------------------------


def test_a_dry_run_does_not_arm_even_on_a_finding_exit(tmp_path: Path, bin_stubs: Path) -> None:
    """A ping is a write to the one thing watching the lane, so an operator inspecting by hand
    must not reset the timer of a schedule that has in fact stopped."""
    _, pings, output = _run(
        tmp_path,
        bin_stubs,
        check_name="sds-landing-ledger",
        exit_code=2,
        finding="2",
        arguments="--dry-run",
    )

    assert pings == []
    assert "not arming" in output


def test_a_check_that_does_not_resolve_never_gates_the_lane(
    tmp_path: Path, bin_stubs: Path
) -> None:
    rc, pings, output = _run(
        tmp_path,
        bin_stubs,
        check_name="sds-landing-ledger",
        exit_code=2,
        finding="2",
        listing_name="some-other-check",
    )

    assert rc == 2
    assert pings == []
    assert "no check named" in output


# ---------------------------------------------------------------------------------------------
# The declaration is pinned to the launcher's own prose.
# ---------------------------------------------------------------------------------------------


def test_every_launcher_that_arms_the_switch_declares_a_finding_code() -> None:
    """ELEVEN with execution-watch (ten since the bridge launchers -- bump proposer, change
    proposer, work carrier -- became outcome alarms), and the number is asserted rather than
    derived so a launcher that stopped arming the switch is a red build rather than a smaller
    count nobody notices.
    Editing this literal is how adding a lane is recorded."""
    assert len(declared_codes()) == len(launchers()) == 11


@pytest.mark.parametrize("launcher", [path.name for path in launchers()])
def test_the_declared_code_is_the_one_the_launchers_own_header_calls_a_finding(
    launcher: str,
) -> None:
    """THE PIN. The declaration restates a vocabulary that already lives in seven headers, so
    without this it is one more copy free to drift -- and a drifted copy inverts the signal
    silently rather than failing. Read from the header the launcher documents itself with, so
    editing one and not the other is a red build."""
    text = (SCRIPTS / launcher).read_text()
    prose = DECLARES_FINDING.findall(text)
    armed = ARMS.search(text)

    assert armed is not None
    assert len(prose) == 1, f"{launcher} names a finding code on {len(prose)} lines, not one"
    assert armed.group("code") == prose[0]


def test_the_two_schemes_are_both_present_so_neither_test_above_is_vacuous() -> None:
    """A universal rule inverts three of the eight, so a suite that happened to cover only one
    scheme
    would prove the half that already worked."""
    codes = {code for _, code in declared_codes().values()}

    assert codes == {2, 3}


# ---------------------------------------------------------------------------------------------
# The outcome ping a lane that does not arm the switch shares.
# ---------------------------------------------------------------------------------------------

_OUTCOME_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$CURL_ARGS"
cat >> "$CURL_STDIN"
"""


def _outcome(tmp_path: Path, url: str, call: str) -> tuple[str, str, str]:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    curl = stubs / "curl"
    curl.write_text(_OUTCOME_CURL)
    curl.chmod(0o755)
    report = tmp_path / "report.txt"
    report.write_text("a finding\n")
    driver = tmp_path / "driver.sh"
    driver.write_text(
        "set -uo pipefail\n"
        f"source {HELPER.resolve()}\n"
        f"SDS_DEADMAN_PING_URL={url!r}\n"
        f"{call.format(report=report)}\n"
        "echo rc=$?\n"
    )
    environment = dict(os.environ)
    environment["PATH"] = f"{stubs}{os.pathsep}{environment['PATH']}"
    environment["CURL_ARGS"] = str(tmp_path / "args")
    environment["CURL_STDIN"] = str(tmp_path / "stdin")
    completed = subprocess.run(
        ["/bin/bash", str(driver)], capture_output=True, text=True, env=environment
    )
    args, stdin = tmp_path / "args", tmp_path / "stdin"
    return (
        args.read_text() if args.exists() else "",
        stdin.read_text() if stdin.exists() else "",
        completed.stdout + completed.stderr,
    )


def test_the_outcome_ping_sends_the_report_as_the_body_and_the_url_on_stdin(
    tmp_path: Path,
) -> None:
    args, stdin, output = _outcome(tmp_path, PING_URL, 'sds_outcome_ping "/fail" "{report}"')
    assert PING_URL not in args + output
    assert stdin == f'url = "{PING_URL}/fail"\n'
    assert f"--data-binary @{tmp_path / 'report.txt'}" in args
    assert "rc=0" in output


def test_the_outcome_ping_does_nothing_without_a_resolved_url(tmp_path: Path) -> None:
    args, stdin, output = _outcome(tmp_path, "", 'sds_outcome_ping "/fail" "{report}"')
    assert (args, stdin) == ("", "")
    assert "rc=0" in output
