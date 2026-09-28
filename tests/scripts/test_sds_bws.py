"""`scripts/sds-bws.sh` is the one place a launcher turns a BWS response into a secret value.

**THE DEFECT THIS PINS IS THE INTERPRETER, NOT THE COLOUR.** Every launcher already passed
`--color no` with the forcing variables unset. What each of them also did was pipe the response into
bare `python3`, and under launchd that is `/usr/bin/python3` -- an Xcode shim that refuses to run
while the Xcode license is unaccepted. On 2026-09-15 an Xcode update reset the license and every
lane stopped at this parse, each reporting that BWS could not be read. So the discriminating control
below puts a `python3` on PATH that behaves as the shim does, and shows the old body dying on it
while the helper does not.

Everything runs under `env -i` with only PATH and HOME (plus the stub's own log path), which is the
environment launchd gives a job. `bws` is a stub on a prepended PATH: no real credential is read and
no request leaves the machine.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path("scripts")
HELPER = SCRIPTS / "sds-bws.sh"
VALUE = "stub-secret-value"
ESC = "\x1b"
SOURCE_LINE = 'source "$REPO_ROOT/scripts/sds-bws.sh"'

# The body every launcher carried until 2026-09-27, byte-identical in the six single-identity
# launchers (e.g. `scripts/run-landing-ledger.sh` at 22ff678). Held as a literal rather than read
# from git history, because a CI checkout is shallow.
OLD_BODY = """_bws_value() {
  env -u FORCE_COLOR -u CLICOLOR_FORCE bws secret get "$1" --output json --color no \\
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}
"""

# Emits ANSI-wrapped JSON when a forcing variable is set and `--color no` is absent -- the real
# trigger -- or unconditionally under STUB_ANSI_ALWAYS. Records which identity it was handed,
# distinguishing an unset BWS_ACCESS_TOKEN from an empty one.
_BWS_STUB = r"""#!/bin/sh
echo "token=${BWS_ACCESS_TOKEN-<unset>} args=$*" >> "$STUB_LOG"
body='{"id": "x", "key": "k", "value": "stub-secret-value"}'
case " $* " in
    *" --color no "*) colour=0 ;;
    *) if [ -n "${FORCE_COLOR:-}${CLICOLOR_FORCE:-}" ]; then colour=1; else colour=0; fi ;;
esac
if [ -n "${STUB_ANSI_ALWAYS:-}" ]; then colour=1; fi
if [ "$colour" = 1 ]; then
    printf '\033[38;5;15m%s\033[0m\n' "$body"
else
    printf '%s\n' "$body"
fi
"""

# What /usr/bin/python3 does while the Xcode license is unaccepted.
_SHIM_PYTHON3 = """#!/bin/sh
echo "You have not agreed to the Xcode license agreements." >&2
exit 69
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def stubs(tmp_path: Path) -> Path:
    directory = tmp_path / "stubs"
    directory.mkdir()
    _executable(directory / "bws", _BWS_STUB)
    return directory


def _run(
    script: str,
    stubs: Path,
    tmp_path: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "STUB_LOG": str(tmp_path / "bws.log"),
        **(extra_env or {}),
    }
    return subprocess.run(
        ["/bin/bash", "-c", script, "driver", *args],
        env=env,
        cwd=cwd or Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )


def _helper(stubs: Path, tmp_path: Path, *args: str, **kwargs) -> subprocess.CompletedProcess[str]:
    return _run(
        'source "$PWD/scripts/sds-bws.sh"; sds_bws_value "$@"', stubs, tmp_path, *args, **kwargs
    )


def _log(tmp_path: Path) -> list[str]:
    return (tmp_path / "bws.log").read_text().splitlines()


def test_the_repository_venv_the_helper_parses_with_exists() -> None:
    """The helper's interpreter is a precondition of every other case here, so say so first."""
    assert (Path.cwd() / ".venv" / "bin" / "python").exists()


def test_one_argument_inherits_the_ambient_identity(stubs: Path, tmp_path: Path) -> None:
    result = _helper(stubs, tmp_path, "uuid-1", extra_env={"BWS_ACCESS_TOKEN": "ambient"})
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{VALUE}\n"
    assert _log(tmp_path) == ["token=ambient args=secret get uuid-1 --output json --color no"]


def test_two_arguments_override_the_ambient_identity(stubs: Path, tmp_path: Path) -> None:
    result = _helper(stubs, tmp_path, "uuid-2", "narrow", extra_env={"BWS_ACCESS_TOKEN": "broad"})
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{VALUE}\n"
    assert _log(tmp_path)[0].startswith("token=narrow ")


def test_an_empty_second_argument_is_an_empty_identity_not_the_ambient_one(
    stubs: Path, tmp_path: Path
) -> None:
    """Keyed on the argument COUNT: the two-identity launchers must never fall back to ambient."""
    _helper(stubs, tmp_path, "uuid-3", "", extra_env={"BWS_ACCESS_TOKEN": "ambient"})
    assert _log(tmp_path)[0].startswith("token= ")


def test_one_argument_with_no_ambient_identity_passes_none(stubs: Path, tmp_path: Path) -> None:
    _helper(stubs, tmp_path, "uuid-4")
    assert _log(tmp_path)[0].startswith("token=<unset> ")


def test_forcing_variables_do_not_reach_the_parse(stubs: Path, tmp_path: Path) -> None:
    result = _helper(
        stubs, tmp_path, "uuid-5", extra_env={"FORCE_COLOR": "3", "CLICOLOR_FORCE": "1"}
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{VALUE}\n"


def test_control_the_ansi_stub_really_breaks_an_unguarded_parse(
    stubs: Path, tmp_path: Path
) -> None:
    """Without this, the case above would pass against a stub that never emits colour at all."""
    result = _run(
        'bws secret get "$1" --output json | "$PWD/.venv/bin/python" -c '
        "'import sys, json; print(json.load(sys.stdin)[\"value\"])'",
        stubs,
        tmp_path,
        "uuid-6",
        extra_env={"FORCE_COLOR": "3"},
    )
    assert result.returncode != 0
    assert result.stdout == ""


def test_a_response_that_is_still_coloured_fails_closed_and_prints_nothing(
    stubs: Path, tmp_path: Path
) -> None:
    """A garbage token silently assigned is the worst outcome; empty stdout is what callers test."""
    result = _helper(stubs, tmp_path, "uuid-7", extra_env={"STUB_ANSI_ALWAYS": "1"})
    assert result.returncode != 0
    assert result.stdout == ""
    assert VALUE not in result.stderr
    assert ESC not in result.stderr


def test_the_helper_survives_the_xcode_shim_on_path(stubs: Path, tmp_path: Path) -> None:
    _executable(stubs / "python3", _SHIM_PYTHON3)
    result = _helper(stubs, tmp_path, "uuid-8")
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{VALUE}\n"


def test_control_the_old_body_dies_on_the_xcode_shim(stubs: Path, tmp_path: Path) -> None:
    """The discriminating control: the same stubs, the body every launcher carried, and no value."""
    _executable(stubs / "python3", _SHIM_PYTHON3)
    result = _run(OLD_BODY + '_bws_value "$1"', stubs, tmp_path, "uuid-9")
    assert result.returncode == 69
    assert result.stdout == ""
    assert "Xcode license" in result.stderr


def test_a_missing_venv_fails_closed_with_a_named_message(stubs: Path, tmp_path: Path) -> None:
    """The venv is resolved from the helper's own location, so a copy with none beside it."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(HELPER, root / "scripts" / "sds-bws.sh")
    result = _helper(stubs, tmp_path, "uuid-10", cwd=root)
    assert result.returncode == 1
    assert result.stdout == ""
    assert ".venv/bin/python is missing" in result.stderr
    assert not (tmp_path / "bws.log").exists(), "bws must not be asked when nothing can parse it"


# ---- every launcher uses the one helper ---------------------------------------------------------


def _launchers() -> list[Path]:
    return sorted(
        p
        for p in SCRIPTS.glob("run-*.sh")
        if re.search(r"bws secret get|sds_bws_value", p.read_text())
    )


def test_the_launcher_population_is_what_was_consolidated() -> None:
    """Found by glob, so a new launcher joins every assertion below without editing this file.

    The floor guards against the glob matching nothing; it was 12 until Tier 3 item 24b deleted
    run-follow-up-mint.sh, whose daily pass now lives in infraops-mcp-server's drift-audit.sh.
    """
    assert len(_launchers()) >= 11


@pytest.mark.parametrize("launcher", _launchers(), ids=lambda p: p.name)
def test_no_launcher_parses_bws_output_itself(launcher: Path) -> None:
    code = "\n".join(
        line for line in launcher.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert "_bws_value()" not in code
    assert "bws secret get" not in code
    assert not re.search(r"\|\s*python3\b", code), f"{launcher} pipes into bare python3"


@pytest.mark.parametrize("launcher", _launchers(), ids=lambda p: p.name)
def test_every_launcher_sources_the_helper_before_its_first_fetch(launcher: Path) -> None:
    lines = launcher.read_text().splitlines()
    sources = [i for i, line in enumerate(lines) if line == SOURCE_LINE]
    calls = [
        i
        for i, line in enumerate(lines)
        if "sds_bws_value " in line and not line.lstrip().startswith("#")
    ]
    assert len(sources) == 1, f"{launcher} must source scripts/sds-bws.sh exactly once"
    assert calls, f"{launcher} sources the helper and never calls it"
    assert sources[0] < calls[0]
    root_defined = [i for i, line in enumerate(lines) if line.startswith("REPO_ROOT=")]
    assert root_defined and root_defined[0] < sources[0]


def test_the_helper_names_no_interpreter_from_path() -> None:
    text = HELPER.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"(^|[\s|])python3?\b", code)
