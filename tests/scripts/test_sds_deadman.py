"""`scripts/sds-deadman.sh` decides what reaches the alert channel. This tests the shell.

**A FINDING IS NOT A LIVENESS FAILURE, AND THE CODE THAT MEANS "FOUND" IS NOT SHARED.** The switch
answers one question -- did this lane run and report. Until 2026-08-29 it pinged `/fail` on every
non-zero exit, which collapsed "the tool broke" into "the lane found something" and produced the
silence it was added to prevent: while a check sits failed on a standing finding, a lane that STOPS
causes no state change at the alert channel.

**THE TWO GROUPS OF LAUNCHERS DISAGREE ABOUT 2 AND 3, IN OPPOSITE DIRECTIONS.** The ledger, the
deploy watcher and the activation sweep read 2 as found and 3 as could-not-be-read; the estate
lander, the change proposer, the work carrier and the bump proposer read 2 as could-not-use-its-
inputs and 3 as found. So a test that exercises one scheme proves the half that was already working
and says nothing about the three launchers a universal rule would invert. Every case below is
therefore driven from the code the launcher ITSELF declares, read out of its own source, and both
schemes are present in every parametrisation by construction.

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
        *healthchecks.io/api/v3/checks/*)
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
    """ELEVEN since the revision watcher, and the number is asserted rather than derived so a
    launcher that stopped arming the switch is a red build rather than a smaller count nobody
    notices. Editing this literal is how adding a lane is recorded."""
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
# A CONDITION IS A DIFFERENT QUESTION, AND IT GETS A DIFFERENT CHECK.
#
# The switch above answers "is this lane alive" and says SUCCESS on a declared finding, which is
# why it cannot also answer "is the estate healthy". `sds_condition_report` answers only the
# second, on its own check. Three verdicts, and `unknown` pinging NOTHING is the load-bearing one:
# a pass that could not tell has established neither that the condition holds nor that it does not,
# and reporting either would be an assertion nobody measured.
# ---------------------------------------------------------------------------------------------

CONDITION_URL = "https://hc.example/ping/condition"


def _run_condition(
    tmp_path: Path,
    stubs: Path,
    *,
    verdict: str,
    listing_name: str = "sds-a-condition",
    also_arm: bool = False,
) -> tuple[int, list[str], str]:
    """Source the real helper and report a verdict; return (rc, pings, output)."""
    ping_log = tmp_path / "pings.txt"
    ping_log.write_text("")
    listing = json.dumps(
        {
            "checks": [
                {"name": "sds-the-lane", "ping_url": PING_URL},
                {"name": listing_name, "ping_url": CONDITION_URL},
            ]
        }
    )
    arm = "sds_deadman_arm sds-the-lane --finding 2\n" if also_arm else ""
    driver = tmp_path / "driver.sh"
    driver.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f"source {HELPER.resolve()}\n"
        f"{arm}"
        f"sds_condition_report sds-a-condition {verdict}\n"
        "exit 0\n"
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


def test_a_healthy_condition_pings_success(tmp_path: Path, bin_stubs: Path) -> None:
    _, pings, _ = _run_condition(tmp_path, bin_stubs, verdict="ok")

    assert pings == [CONDITION_URL]


def test_an_unhealthy_condition_pings_fail(tmp_path: Path, bin_stubs: Path) -> None:
    """Red until a later pass says otherwise. The condition persists, so the signal must persist
    with it rather than scrolling past, which is the whole reason this check exists."""
    _, pings, _ = _run_condition(tmp_path, bin_stubs, verdict="not-ok")

    assert pings == [f"{CONDITION_URL}/fail"]


def test_a_condition_the_pass_COULD_NOT_TELL_pings_NOTHING(tmp_path: Path, bin_stubs: Path) -> None:
    """The load-bearing case. A missing credential or an unreachable subject has established
    neither that the condition holds nor that it does not; `/fail` would be crying wolf and a
    success would be a claim nobody measured. The check's own period speaks instead."""
    _, pings, output = _run_condition(tmp_path, bin_stubs, verdict="unknown")

    assert pings == []
    assert "could not tell" in output


def test_reporting_a_condition_does_not_STEAL_the_liveness_switchs_url(
    tmp_path: Path, bin_stubs: Path
) -> None:
    """THE HAZARD THIS FUNCTION IS SHAPED AROUND. `sds_deadman_resolve` writes the module globals
    and the EXIT handler reads them, so a resolve for the condition's check would leave the switch
    pinging the CONDITION's url on the way out -- reporting the wrong lane alive, silently. The
    driver arms the switch, reports a condition, then exits 0, so both pings are observable and
    must name different checks."""
    _, pings, _ = _run_condition(tmp_path, bin_stubs, verdict="ok", also_arm=True)

    assert f"{CONDITION_URL}" in pings
    # `/start` and the clean-exit ping both belong to the LANE's check, not the condition's.
    assert [ping for ping in pings if ping.startswith(PING_URL)] == [
        f"{PING_URL}/start",
        PING_URL,
    ]


def test_a_condition_check_that_does_not_resolve_never_gates_the_lane(
    tmp_path: Path, bin_stubs: Path
) -> None:
    """Same rule the switch already follows: a lane must not die because its reporting could not
    report."""
    rc, pings, output = _run_condition(
        tmp_path, bin_stubs, verdict="ok", listing_name="a-different-name"
    )

    assert rc == 0
    assert pings == []
    assert "not reported" in output


def test_an_unrecognised_verdict_reports_NOTHING_rather_than_guessing(
    tmp_path: Path, bin_stubs: Path
) -> None:
    rc, pings, output = _run_condition(tmp_path, bin_stubs, verdict="probably-fine")

    assert rc == 0
    assert pings == []
    assert "is not a verdict" in output


def test_the_revision_watcher_is_the_only_launcher_reporting_a_condition_so_far() -> None:
    """Asserted by value so a second lane adopting this cannot arrive without a person editing the
    literal -- the same construct as the finding-code inventory above, for the same reason."""
    reporting = sorted(
        path.name for path in SCRIPTS.glob("run-*.sh") if "sds_condition_report" in path.read_text()
    )

    assert reporting == ["run-revision-watcher.sh"]


def test_a_condition_reporting_launcher_suppresses_it_under_dry_run() -> None:
    """An operator inspecting a lane by hand must not move the estate's own signal -- the same
    reason the switch is not armed under `--dry-run`.

    CALL LINES, NOT MENTIONS. A first draft indexed the raw text and matched the function's name in
    the COMMENT that explains it, which sits ABOVE the guard -- so the assertion compared two
    offsets in prose and failed on correct code. A second draft looked for lines STARTING with the
    call and matched nothing, because every call site is a `case` arm. Both are the
    non-discriminating check this file exists to catch, written twice into this file.
    """
    for path in SCRIPTS.glob("run-*.sh"):
        lines = path.read_text().splitlines()
        calls = [
            i
            for i, line in enumerate(lines)
            if "sds_condition_report " in line and not line.strip().startswith("#")
        ]
        if not calls:
            continue
        guards = [i for i, line in enumerate(lines) if "sds_deadman_is_dry_run" in line]
        assert guards, f"{path.name} reports a condition without consulting --dry-run"
        assert min(guards) < min(calls), (
            f"{path.name} reports a condition before checking --dry-run"
        )
