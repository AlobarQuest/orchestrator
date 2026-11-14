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


# A pipe into bare python3, including one that sets variables first: `| VAR=value python3 -c ...`.
PIPE_INTO_PYTHON = re.compile(r"\|\s*(?:[A-Za-z_]\w*=\S*\s+)*python3?\b")


def test_the_pipe_pattern_catches_an_assignment_before_python() -> None:
    """The control: the pattern the rule below uses must fail on the shape it once missed."""
    assert PIPE_INTO_PYTHON.search("x | python3 -c 'pass'")
    assert PIPE_INTO_PYTHON.search("printf x | WANTED=\"$name\" python3 -c 'pass'")
    assert not PIPE_INTO_PYTHON.search('x | "$_SDS_BWS_PYTHON" -c "pass"')


@pytest.mark.parametrize(
    "script", [*_launchers(), SCRIPTS / "sds-deadman.sh"], ids=lambda p: p.name
)
def test_no_launcher_parses_bws_output_itself(script: Path) -> None:
    code = "\n".join(
        line for line in script.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert "_bws_value()" not in code
    assert "bws secret get" not in code
    assert not PIPE_INTO_PYTHON.search(code), f"{script} pipes into bare python3"


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


# ---- the token is never an argument -------------------------------------------------------------

# Records its own argv, then runs the real `env` with it.
_ENV_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$ENV_ARGV_LOG"
exec /usr/bin/env "$@"
"""
IDENTITY = "stub-identity-for-one-fetch"


def test_the_two_argument_token_reaches_bws_in_its_environment_and_no_argv(
    stubs: Path, tmp_path: Path
) -> None:
    """Spelled `env ... BWS_ACCESS_TOKEN=<token> bws`, the token was one of `env`'s arguments and
    showed in the process list. As an assignment prefix it is in `env`'s environment only."""
    _executable(stubs / "env", _ENV_STUB)
    env_log = tmp_path / "env-argv.log"
    result = _helper(stubs, tmp_path, "uuid-11", IDENTITY, extra_env={"ENV_ARGV_LOG": str(env_log)})
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{VALUE}\n"
    env_argv = env_log.read_text()
    assert "bws secret get uuid-11" in env_argv, "the stub env must be the one that ran"
    assert IDENTITY not in env_argv
    [line] = _log(tmp_path)
    seen_token, bws_argv = line.split(" args=", 1)
    assert seen_token == f"token={IDENTITY}"
    assert IDENTITY not in bws_argv
    assert bws_argv == "secret get uuid-11 --output json --color no"


# ---- the factory-events chain switch ------------------------------------------------------------

VALID_SWITCH = (
    "FACTORY_EVENTS_DB_HOST=100.64.0.1\n"
    "FACTORY_EVENTS_DB_PORT=15432\n"
    "FACTORY_EVENTS_DB_NAME=postgres\n"
)
# Prints the result as `rc=`, each setting (or `<unset>`), the refusal, and every exported name
# beginning `CHAIN_`, so a test sees what the reader left exported.
_READ = (
    'source "$PWD/scripts/sds-bws.sh"; factory_events_read_settings "$1"; rc=$?; '
    'echo "rc=$rc"; echo "host=${CHAIN_DB_HOST-<unset>}"; echo "port=${CHAIN_DB_PORT-<unset>}"; '
    'echo "name=${CHAIN_DB_NAME-<unset>}"; echo "refusal=$(factory_events_settings_refusal)"; '
    "/usr/bin/env | sed -n 's/^\\(CHAIN_[A-Z_]*\\)=.*/exported=\\1/p'"
)


def _read_switch(
    stubs: Path, tmp_path: Path, path: Path, **extra_env: str
) -> dict[str, str | list[str]]:
    result = _run(_READ, stubs, tmp_path, str(path), extra_env=extra_env)
    assert result.stderr == ""
    fields: dict[str, str | list[str]] = {"exported": []}
    for line in result.stdout.splitlines():
        key, value = line.split("=", 1)
        if key == "exported":
            exported = fields["exported"]
            assert isinstance(exported, list)
            exported.append(value)
        else:
            fields[key] = value
    return fields


def _switch(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "chain-db.env"
    path.write_text(text)
    return path


def test_a_valid_switch_sets_the_three_settings_unexported(stubs: Path, tmp_path: Path) -> None:
    fields = _read_switch(stubs, tmp_path, _switch(tmp_path, VALID_SWITCH))
    assert fields == {
        "rc": "0",
        "host": "100.64.0.1",
        "port": "15432",
        "name": "postgres",
        "refusal": "",
        "exported": [],
    }


def test_a_switch_without_a_final_newline_is_read(stubs: Path, tmp_path: Path) -> None:
    fields = _read_switch(stubs, tmp_path, _switch(tmp_path, VALID_SWITCH.rstrip("\n")))
    assert fields["rc"] == "0"
    assert fields["name"] == "postgres"


def test_a_name_the_caller_exported_is_not_left_exported(stubs: Path, tmp_path: Path) -> None:
    """`~/.factory/env` is sourced under `set -a` elsewhere: a plain assignment keeps an exported
    name exported, so the reader unsets each first."""
    fields = _read_switch(
        stubs,
        tmp_path,
        _switch(tmp_path, VALID_SWITCH),
        CHAIN_DB_HOST="exported-before",
        CHAIN_DB_PORT="1",
        CHAIN_DB_NAME="exported-before",
    )
    assert fields["host"] == "100.64.0.1"
    assert fields["exported"] == []


def test_control_a_plain_assignment_keeps_an_exported_name_exported(
    stubs: Path, tmp_path: Path
) -> None:
    """Without this, the case above would pass on a shell where assignment unexports."""
    result = _run(
        "CHAIN_DB_HOST=x; /usr/bin/env | grep -c '^CHAIN_DB_HOST='",
        stubs,
        tmp_path,
        extra_env={"CHAIN_DB_HOST": "exported-before"},
    )
    assert result.stdout.strip() == "1"


SECRET_LOOKING = "hunter2-pasted-by-mistake"

# Each refusal security-standards' `factory-events-nightly.sh` makes (4e83d7a), and the words
# the reader's message must carry. None of the files is valid.
REFUSALS = {
    "an unknown key": (
        VALID_SWITCH + f"FACTORY_EVENTS_APPEND_PASSWORD={SECRET_LOOKING}\n",
        "line 4 has an unknown key",
    ),
    "a missing host": (
        "FACTORY_EVENTS_DB_PORT=15432\nFACTORY_EVENTS_DB_NAME=postgres\n",
        "has no FACTORY_EVENTS_DB_HOST",
    ),
    "a missing port": (
        "FACTORY_EVENTS_DB_HOST=h\nFACTORY_EVENTS_DB_NAME=postgres\n",
        "has no FACTORY_EVENTS_DB_PORT",
    ),
    "a missing name": (
        "FACTORY_EVENTS_DB_HOST=h\nFACTORY_EVENTS_DB_PORT=15432\n",
        "has no FACTORY_EVENTS_DB_NAME",
    ),
    "a duplicate key": (
        VALID_SWITCH + "FACTORY_EVENTS_DB_PORT=15433\n",
        "sets FACTORY_EVENTS_DB_PORT twice",
    ),
    "an empty value": (
        VALID_SWITCH.replace("=postgres", "="),
        "line 3: FACTORY_EVENTS_DB_NAME is empty",
    ),
    "a blank line": (
        VALID_SWITCH.replace("\nFACTORY_EVENTS_DB_PORT", "\n\nFACTORY_EVENTS_DB_PORT"),
        "line 2 is not KEY=value",
    ),
    "a line with no equals sign": (SECRET_LOOKING + "\n", "line 1 is not KEY=value"),
    "a quoted value": (
        VALID_SWITCH.replace("=postgres", '="postgres"'),
        "FACTORY_EVENTS_DB_NAME is empty or has a character outside",
    ),
    "a space in a value": (
        VALID_SWITCH.replace("=postgres", f"=post {SECRET_LOOKING}"),
        "FACTORY_EVENTS_DB_NAME is empty or has a character outside",
    ),
    "a command substitution": (
        VALID_SWITCH.replace("=postgres", "=$(touch pwned)"),
        "FACTORY_EVENTS_DB_NAME is empty or has a character outside",
    ),
    "a carriage return": (
        VALID_SWITCH.replace("\n", "\r\n"),
        "FACTORY_EVENTS_DB_HOST is empty or has a character outside",
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_each_malformed_switch_is_refused_by_name_and_never_echoes_a_value(
    stubs: Path, tmp_path: Path, case: str
) -> None:
    text, message = REFUSALS[case]
    path = _switch(tmp_path, text)
    fields = _read_switch(stubs, tmp_path, path)
    assert fields["rc"] == "1"
    refusal = fields["refusal"]
    assert isinstance(refusal, str)
    assert refusal.startswith(str(path))
    assert message in refusal
    assert SECRET_LOOKING not in refusal
    assert "postgres" not in refusal and "100.64.0.1" not in refusal
    assert (fields["host"], fields["port"], fields["name"]) == ("<unset>",) * 3
    assert not (tmp_path / "pwned").exists()


def test_a_directory_is_not_a_regular_file(stubs: Path, tmp_path: Path) -> None:
    path = tmp_path / "chain-db.env"
    path.mkdir()
    fields = _read_switch(stubs, tmp_path, path)
    assert fields["rc"] == "1"
    assert fields["refusal"] == f"{path} is not a regular file"


def test_a_symlink_to_a_valid_switch_is_refused(stubs: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.env"
    target.write_text(VALID_SWITCH)
    path = tmp_path / "chain-db.env"
    path.symlink_to(target)
    fields = _read_switch(stubs, tmp_path, path)
    assert fields["rc"] == "1"
    assert fields["refusal"] == f"{path} is not a regular file"


def test_an_unreadable_switch_is_refused(stubs: Path, tmp_path: Path) -> None:
    path = _switch(tmp_path, VALID_SWITCH)
    path.chmod(0o000)
    try:
        fields = _read_switch(stubs, tmp_path, path)
    finally:
        path.chmod(0o600)
    assert fields["rc"] == "1"
    assert fields["refusal"] == f"{path} is not readable"


@pytest.mark.parametrize(
    ("shape", "present"),
    [("absent", False), ("a file", True), ("a dangling symlink", True), ("a directory", True)],
)
def test_the_switch_is_present_for_anything_at_its_path(
    stubs: Path, tmp_path: Path, shape: str, present: bool
) -> None:
    """Present means "read it, and refuse what isn't a regular file", never "treat it as absent"."""
    path = tmp_path / "chain-db.env"
    if shape == "a file":
        path.write_text(VALID_SWITCH)
    elif shape == "a dangling symlink":
        path.symlink_to(tmp_path / "missing")
    elif shape == "a directory":
        path.mkdir()
    result = _run(
        'source "$PWD/scripts/sds-bws.sh"; factory_events_switch_present "$1"',
        stubs,
        tmp_path,
        str(path),
    )
    assert (result.returncode == 0) is present


def test_with_no_path_both_functions_read_the_switch_the_plan_names(
    stubs: Path, tmp_path: Path
) -> None:
    (tmp_path / ".factory").mkdir()
    (tmp_path / ".factory" / "chain-db.env").write_text(VALID_SWITCH)
    result = _run(
        'source "$PWD/scripts/sds-bws.sh"; factory_events_switch_present'
        ' && factory_events_read_settings && printf %s "$CHAIN_DB_NAME"',
        stubs,
        tmp_path,
    )
    assert result.stdout == "postgres"
