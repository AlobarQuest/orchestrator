"""set-root-token.sh's two modes, run root-less against a temporary target.

The seam is a copy of the script with two lines substituted: the target path and the root check.
`chown` and `stat` resolve to stubs on PATH, so the test needs no root and no `rotation` user; the
real `umask`, `mktemp`, `chmod` and `mv` run. The script itself carries no test-only switch.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import termios
import time
from pathlib import Path

import pytest

SCRIPT = Path("scripts/rotation-worker-vm/set-root-token.sh")
TARGET_LINE = "target=/etc/rotation-worker/root-token\n"
ROOT_CHECK = "[[ $EUID -ne 0 ]]"
VALUE = "placeholder-value-1"
EXISTING = "placeholder-existing-value\n"


class Runner:
    def __init__(self, tmp_path: Path) -> None:
        self.target = tmp_path / "etc" / "root-token"
        self.target.parent.mkdir()
        self.chown_log = tmp_path / "chown.log"
        text = SCRIPT.read_text()
        assert text.count(TARGET_LINE) == 1
        assert text.count(ROOT_CHECK) == 1
        script = tmp_path / "set-root-token"
        script.write_text(
            text.replace(TARGET_LINE, f"target={self.target}\n").replace(
                ROOT_CHECK, "[[ 0 -ne 0 ]]"
            )
        )
        stubs = tmp_path / "bin"
        stubs.mkdir()
        (stubs / "chown").write_text(f'#!/bin/sh\necho "$1" >> {self.chown_log}\n')
        (stubs / "stat").write_text("#!/bin/sh\necho 'rotation 400'\n")
        for stub in stubs.iterdir():
            stub.chmod(0o755)
        bash = shutil.which("bash")
        assert bash is not None
        self.command = [bash, str(script)]
        self.env = {"PATH": f"{stubs}:/usr/bin:/bin"}

    def run(self, *args: str, stdin: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [*self.command, *args],
            input=stdin,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert VALUE not in result.stdout + result.stderr, "the value reached the output"
        return result

    def run_on_a_terminal(self, *args: str, typed: str = "") -> subprocess.CompletedProcess[str]:
        """Run with a pseudo-terminal on stdin, typing `typed` once the prompt stops echoing."""
        primary, secondary = os.openpty()
        try:
            process = subprocess.Popen(
                [*self.command, *args],
                stdin=secondary,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                text=True,
            )
            if typed:
                # `read -s` flushes pending input when it turns echo off, so type only after.
                deadline = time.monotonic() + 10
                while termios.tcgetattr(secondary)[3] & termios.ECHO:
                    assert time.monotonic() < deadline, "the prompt never turned echo off"
                    time.sleep(0.01)
                os.write(primary, typed.encode())
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
        finally:
            os.close(primary)
            os.close(secondary)
        assert VALUE not in stdout + stderr, "the value reached the output"
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.target.parent.iterdir() if p != self.target)


@pytest.fixture
def runner(tmp_path: Path) -> Runner:
    return Runner(tmp_path)


def test_without_the_flag_a_pipe_is_refused(runner: Runner) -> None:
    result = runner.run(stdin=f"{VALUE}\n")
    assert result.returncode == 2
    assert "needs a terminal" in result.stderr
    assert not runner.target.exists()


def test_without_the_flag_a_terminal_is_read_at_the_hidden_prompt(runner: Runner) -> None:
    result = runner.run_on_a_terminal(typed=f"{VALUE}\n")
    assert result.returncode == 0, result.stderr
    assert "input hidden" in result.stderr
    assert runner.target.read_text() == f"{VALUE}\n"
    assert stat.S_IMODE(runner.target.stat().st_mode) == 0o400


def test_the_flag_refuses_a_terminal(runner: Runner) -> None:
    result = runner.run_on_a_terminal("--stdin")
    assert result.returncode == 2
    assert "not a terminal" in result.stderr
    assert not runner.target.exists()


def test_an_unknown_argument_is_refused_without_being_echoed(runner: Runner) -> None:
    result = runner.run(VALUE, stdin="")
    assert result.returncode == 2
    assert "unknown argument" in result.stderr


def test_a_second_argument_is_refused(runner: Runner) -> None:
    assert runner.run("--stdin", "--stdin", stdin="").returncode == 2


@pytest.mark.parametrize("line", [f"{VALUE}\n", f"{VALUE}\r\n", VALUE])
def test_one_line_is_stored_with_one_newline_readable_only_by_the_owner(
    runner: Runner, line: str
) -> None:
    runner.target.write_text(EXISTING)
    result = runner.run("--stdin", stdin=line)
    assert result.returncode == 0, result.stderr
    assert runner.target.read_text() == f"{VALUE}\n"
    assert stat.S_IMODE(runner.target.stat().st_mode) == 0o400
    assert runner.chown_log.read_text() == "rotation:rotation\n"
    assert runner.leftovers() == []


@pytest.mark.parametrize(
    ("stdin", "reason"),
    [
        ("", "nothing on stdin"),
        ("\n", "the line is empty"),
        ("\r\n", "the line is empty"),
        (f"{VALUE}\nplaceholder-value-2\n", "more than one line"),
        (f"{VALUE}\n\n", "more than one line"),
        (f"\n{VALUE}\n", "the line is empty"),
        ("placeholder value-1\n", "whitespace inside"),
        ("placeholder\tvalue-1\n", "whitespace inside"),
        (" placeholder-value-1\n", "whitespace inside"),
        ("x" * 4097 + "\n", "longer than 4096"),
        ("x " * 2049 + "\n", "longer than 4096"),
    ],
)
def test_a_bad_input_is_refused_and_the_file_is_unchanged(
    runner: Runner, stdin: str, reason: str
) -> None:
    runner.target.write_text(EXISTING)
    runner.target.chmod(0o400)
    result = runner.run("--stdin", stdin=stdin)
    assert result.returncode == 1
    assert reason in result.stderr
    assert "the file is unchanged" in result.stderr
    assert runner.target.read_text() == EXISTING
    assert runner.leftovers() == []
    assert not runner.chown_log.exists()


def test_the_longest_accepted_value_is_stored(runner: Runner) -> None:
    value = "x" * 4096
    assert runner.run("--stdin", stdin=f"{value}\n").returncode == 0
    assert runner.target.read_text() == f"{value}\n"
