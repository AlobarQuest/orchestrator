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
    """The whole change, for every armed launcher, so both schemes (2 or 3 as found) are here."""
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
    """THE PIN. The declaration restates a vocabulary that already lives in each header, so
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
    """A universal rule inverts one scheme, so a suite that happened to cover only one scheme
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


# ---------------------------------------------------------------------------------------------
# `sds_outcome_end --told <code>`: a delivered finding pings success (ADR-0021 amendment 3).
# ---------------------------------------------------------------------------------------------


def _end(tmp_path: Path, arguments: str, rc: int) -> tuple[int, list[str], list[str], str]:
    """Run `sds_outcome_end <rc> <arguments>`; return (rc, ping urls, ping bodies, output)."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    curl = stubs / "curl"
    # The URL arrives as curl config on stdin; a body, when there is one, as `--data-binary @file`.
    curl.write_text(
        "#!/usr/bin/env bash\n"
        'url="$(cat)"; echo "$url" >> "$PING_LOG"\n'
        'while [ $# -gt 0 ]; do case "$1" in --data-binary) cat "${2#@}" >> "$BODY_LOG";'
        ' echo "--" >> "$BODY_LOG";; esac; shift; done\n'
    )
    curl.chmod(0o755)
    report = tmp_path / "report.txt"
    report.write_text("the report\n")
    driver = tmp_path / "driver.sh"
    driver.write_text(
        "set -uo pipefail\n"
        f"source {HELPER.resolve()}\n"
        f"SDS_DEADMAN_PING_URL={PING_URL!r}\n"
        "SDS_OUTCOME_TAG=lane\n"
        f"SDS_OUTCOME_REPORT={report}\n"
        f"sds_outcome_end {rc} {arguments}\n"
    )
    environment = dict(os.environ)
    environment["PATH"] = f"{stubs}{os.pathsep}{environment['PATH']}"
    environment["PING_LOG"] = str(tmp_path / "pings")
    environment["BODY_LOG"] = str(tmp_path / "bodies")
    completed = subprocess.run(
        ["/bin/bash", str(driver)], capture_output=True, text=True, env=environment
    )
    pings, bodies = tmp_path / "pings", tmp_path / "bodies"
    urls = [
        line.removeprefix('url = "').removesuffix('"')
        for line in (pings.read_text().splitlines() if pings.exists() else [])
    ]
    return (
        completed.returncode,
        urls,
        (bodies.read_text().split("--\n")[:-1] if bodies.exists() else []),
        completed.stdout + completed.stderr,
    )


def test_the_told_code_pings_success_with_the_report_as_the_body(tmp_path: Path) -> None:
    rc, urls, bodies, output = _end(tmp_path, "--told 2", 2)

    assert rc == 2, "the exit code is the pass's own"
    assert urls == [PING_URL]
    assert bodies == ["the report\n"]
    assert "exit 2 is this lane's told code" in output
    assert "delivered as a keyed alert" in output
    assert "reporting a failure" not in output


def test_any_other_code_still_pings_fail(tmp_path: Path) -> None:
    rc, urls, bodies, output = _end(tmp_path, "--told 2", 3)

    assert rc == 3
    assert urls == [f"{PING_URL}/fail"]
    assert bodies == ["the report\n"]
    assert "reporting a failure" in output


@pytest.mark.parametrize("arguments", ["--told 2 --body-on-success", "--body-on-success --told 2"])
def test_the_flags_parse_in_either_order(tmp_path: Path, arguments: str) -> None:
    _, urls, bodies, _ = _end(tmp_path, arguments, 2)
    assert (urls, bodies) == ([PING_URL], ["the report\n"])

    other = tmp_path / "zero"
    other.mkdir()
    _, urls, bodies, _ = _end(other, arguments, 0)
    assert (urls, bodies) == ([PING_URL], ["the report\n"]), "--body-on-success keeps its meaning"


def test_success_without_body_on_success_sends_no_body(tmp_path: Path) -> None:
    _, urls, bodies, _ = _end(tmp_path, "--told 2", 0)
    assert (urls, bodies) == ([PING_URL], [])


@pytest.mark.parametrize("told", ["0", "1"])
def test_a_reserved_told_code_is_ignored_with_a_log_line(tmp_path: Path, told: str) -> None:
    """`--told 1` would ping a broken lane healthy, as `--finding 1` would; refused alike."""
    rc, urls, _, output = _end(tmp_path, f"--told {told}", int(told))

    assert rc == int(told)
    assert f"'--told {told}' is a reserved code" in output
    if told == "1":
        assert urls == [f"{PING_URL}/fail"]


@pytest.mark.parametrize("told", ["two", ""])
def test_a_malformed_told_code_is_ignored_and_fails_closed(tmp_path: Path, told: str) -> None:
    rc, urls, _, output = _end(tmp_path, f"--told {told}", 2)

    assert rc == 2
    assert urls == [f"{PING_URL}/fail"]
    assert "is not a number" in output


def test_an_unknown_end_flag_is_logged_and_still_fails_closed(tmp_path: Path) -> None:
    """A misspelt `--told` must not be swallowed: the exit still pings `/fail`."""
    rc, urls, _, output = _end(tmp_path, "--tol 2", 2)

    assert rc == 2
    assert urls == [f"{PING_URL}/fail"]
    assert "unknown flag '--tol'" in output


# ---------------------------------------------------------------------------------------------
# The key is read through the shared parser, and no `python3` from PATH is involved.
# ---------------------------------------------------------------------------------------------


def test_the_key_and_listing_are_read_with_no_python3_on_path(
    tmp_path: Path, bin_stubs: Path
) -> None:
    """Under launchd a bare `python3` is an Xcode shim that can refuse to run. The resolve path
    must work on a PATH holding no `python3` at all: the venv interpreter does both parses."""
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "env", "dirname", "cat", "rm", "mktemp", "tee"):
        found = shutil.which(name)
        if found:
            (tools / name).symlink_to(found)
    path = f"{bin_stubs}{os.pathsep}{tools}"
    assert shutil.which("python3", path=path) is None, "the control: no python3 on this PATH"

    ping_log = tmp_path / "pings.txt"
    ping_log.write_text("")
    driver = tmp_path / "driver.sh"
    driver.write_text(
        "set -uo pipefail\n"
        f"source {HELPER.resolve()}\n"
        "sds_deadman_resolve sds-landing-ledger\n"
        'echo "URL=$SDS_DEADMAN_PING_URL REASON=$SDS_DEADMAN_REASON"\n'
    )
    environment = {
        "PATH": path,
        "HOME": str(tmp_path),
        "BWS_ACCESS_TOKEN_BROAD": "not-a-real-identity",
        "LISTING": json.dumps({"checks": [{"name": "sds-landing-ledger", "ping_url": PING_URL}]}),
        "PING_LOG": str(ping_log),
    }
    completed = subprocess.run(
        ["/bin/bash", str(driver)], capture_output=True, text=True, env=environment
    )

    assert f"URL={PING_URL} REASON=" in completed.stdout, completed.stdout + completed.stderr


# ---------------------------------------------------------------------------------------------
# A launcher's `--told` code is held to a sentence in its own EXIT CODES block.
# ---------------------------------------------------------------------------------------------

# The phrase an `EXIT CODES` line must carry to justify `--told`: every exit with that code was a
# keyed alert that alert-hub accepted. The same phrase is what the helper logs.
DELIVERED_MARKER = "delivered as a keyed alert"
TOLD = re.compile(r"^[ \t]*sds_outcome_end\b.*--told (?P<code>\d+)", re.MULTILINE)
CODE_LINE = re.compile(r"^#\s{1,4}(?P<code>\d+)\s{2,}(?P<text>.*)$")


def told_violations(text: str) -> list[str]:
    """Each `--told` code the launcher text passes without saying so in its EXIT CODES block."""
    lines = text.splitlines()
    block: dict[str, str] = {}
    current: str | None = None
    inside = False
    for line in lines:
        if line.startswith("# EXIT CODES"):
            inside = True
            continue
        if not inside:
            continue
        if not line.startswith("#") or not line.lstrip("# ").strip():
            break
        match = CODE_LINE.match(line)
        if match:
            current = str(match.group("code"))
            block[current] = str(match.group("text"))
        elif current is not None:
            block[current] += " " + line.lstrip("# ").strip()
    return [
        f"--told {code} is not described as {DELIVERED_MARKER!r}"
        for code in TOLD.findall(text)
        if DELIVERED_MARKER not in " ".join(block.get(code, "").split())
    ]


@pytest.mark.parametrize("launcher", sorted(p.name for p in SCRIPTS.glob("run-*.sh")))
def test_a_told_code_is_one_the_launchers_header_calls_a_delivered_alert(launcher: str) -> None:
    """Vacuous for a launcher that passes no `--told`; the control below proves it can fail."""
    assert told_violations((SCRIPTS / launcher).read_text()) == []


def test_the_told_header_check_fails_on_a_launcher_that_does_not_say_so() -> None:
    declared = (
        "# EXIT CODES:\n"
        "#   0  fine.\n"
        "#   2  a person decides; every such outcome is delivered as a keyed alert\n"
        "#      to alert-hub.\n"
        "#   3  unreadable.\n"
        '\nsds_outcome_end "$rc" --told 2\n'
    )
    silent = declared.replace(DELIVERED_MARKER, "raised")
    wrong_code = declared.replace("--told 2", "--told 3")

    assert told_violations(declared) == []
    assert told_violations(silent) != []
    assert told_violations(wrong_code) != []


def test_the_told_header_check_finds_an_indented_call() -> None:
    indented = '# EXIT CODES:\n#   0  fine.\n\nif x; then\n    sds_outcome_end "$rc" --told 2\nfi\n'

    assert told_violations(indented) != []
