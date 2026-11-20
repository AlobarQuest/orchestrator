"""The secrets broker hands a lane only its allowlisted secrets, and no value leaks on a failure.

Each test fetches through a stand-in `bws` on `PATH`, never real BWS. The stand-in records each
call's arguments and environment, prints the JSON shape `bws secret get --output json` prints, and
can fail, print non-JSON, or hang on request.
"""

from __future__ import annotations

import json
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from functools import partial
from pathlib import Path

import pytest

from scripts import secrets_broker
from scripts.secrets_broker import AllowlistError

SECRET = "SECRET-value-never-printed"
GOOD = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
# The stand-in's behaviour for each UUID other than GOOD and OTHER.
FAILS = "33333333-3333-4333-8333-333333333333"
NOT_JSON = "44444444-4444-4444-8444-444444444444"
HANGS = "55555555-5555-4555-8555-555555555555"
# Holds letters, so its upper-case form differs from it.
LETTERS = "abcdef01-2345-4678-89ab-cdef01234567"


class Bws:
    """A stand-in `bws` on a `bin` directory, recording into `record/`."""

    def __init__(self, root: Path) -> None:
        self.bin = root / "bin"
        self.record = root / "record"
        self.bin.mkdir()
        self.record.mkdir()
        self.path = self.bin / "bws"
        self.path.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{self.record}/argv"\n'
            f'printf "HOME=%s FORCE_COLOR=%s CLICOLOR_FORCE=%s HOME_EMPTY=%s\\n" '
            '"$HOME" "${FORCE_COLOR-unset}" "${CLICOLOR_FORCE-unset}" '
            f'"$(ls -A "$HOME" | wc -l | tr -d " ")" >> "{self.record}/env"\n'
            'case "$3" in\n'
            f'  {FAILS}) echo "{SECRET}"; echo "error: {SECRET}" >&2; exit 1;;\n'
            f'  {NOT_JSON}) echo "{SECRET} is not json";;\n'
            # Not `exec`: the shell's child keeps the pipe open, as a stuck bws's helper would.
            f'  {HANGS}) echo "{SECRET}"; sleep 30;;\n'
            f'  *) printf \'{{"id": "%s", "key": "k", "value": "%s"}}\\n\' '
            f'"$3" "{SECRET}";;\n'
            "esac\n"
        )
        self.path.chmod(0o755)

    def calls(self) -> list[str]:
        argv = self.record / "argv"
        return argv.read_text().splitlines() if argv.exists() else []

    def environments(self) -> list[str]:
        return (self.record / "env").read_text().splitlines()


@pytest.fixture
def bws(tmp_path: Path) -> Bws:
    return Bws(tmp_path)


def _fetcher(bws: Bws, timeout_s: float = 5.0):
    return partial(secrets_broker.fetch, bws=str(bws.path), timeout_s=timeout_s)


def _allowlist(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "allowlist.toml"
    path.write_text(text)
    return path


def test_allowlist_rejects_duplicates_bad_names_and_bad_uuids(tmp_path: Path) -> None:
    good = _allowlist(
        tmp_path,
        f'[lanes]\n\n[lanes.inert-landing]\nread = ["{GOOD}", "{OTHER}"]\n\n'
        f'[lanes.unit-verifier]\nread = ["{GOOD}"]\n',
    )
    assert secrets_broker.load_allowlist(good) == {
        "inert-landing": frozenset({GOOD, OTHER}),
        "unit-verifier": frozenset({GOOD}),
    }
    assert secrets_broker.load_allowlist(_allowlist(tmp_path, "[lanes]\n")) == {}

    refused = {
        "a lane twice": f'[lanes.a]\nread = ["{GOOD}"]\n[lanes.a]\nread = ["{OTHER}"]\n',
        "an upper-case lane": f'[lanes.Inert]\nread = ["{GOOD}"]\n',
        "a lane starting with a digit": f'[lanes.1lane]\nread = ["{GOOD}"]\n',
        "an underscore": f'[lanes.inert_landing]\nread = ["{GOOD}"]\n',
        "an upper-case uuid": f'[lanes.a]\nread = ["{LETTERS.upper()}"]\n',
        "a short uuid": '[lanes.a]\nread = ["1111-1111"]\n',
        "a uuid with a newline": f'[lanes.a]\nread = ["{GOOD}\\n"]\n',
        "a non-string uuid": "[lanes.a]\nread = [1]\n",
        "a lane without read": "[lanes.a]\n",
        "an unknown key": f'[lanes.a]\nread = ["{GOOD}"]\nwrite = []\n',
        "no lanes table": "",
        "not TOML": "[lanes\n",
    }
    for text in refused.values():
        with pytest.raises(AllowlistError):
            secrets_broker.load_allowlist(_allowlist(tmp_path, text))
    with pytest.raises(AllowlistError):
        secrets_broker.load_allowlist(tmp_path / "missing.toml")


def test_listed_uuid_returns_the_bws_json_shape(bws: Bws, capfd: pytest.CaptureFixture) -> None:
    reply = secrets_broker.handle(
        "inert-landing", f"{GOOD}\n".encode(), frozenset({GOOD}), _fetcher(bws)
    )

    assert reply == (json.dumps({"id": GOOD, "value": SECRET}) + "\n").encode()
    assert json.loads(reply) == {"id": GOOD, "value": SECRET}
    assert bws.calls() == [f"secret get {GOOD} --output json --color no"]
    out, err = capfd.readouterr()
    assert json.loads(out) == {"lane": "inert-landing", "uuid": GOOD, "outcome": "ok"}
    assert SECRET not in out + err


def test_unlisted_uuid_is_refused_without_calling_bws(
    bws: Bws, capfd: pytest.CaptureFixture
) -> None:
    allowed = frozenset({GOOD, LETTERS})
    unlisted = secrets_broker.handle("inert-landing", f"{OTHER}\n".encode(), allowed, _fetcher(bws))
    garbage = [
        b"",
        b"\n",
        GOOD.encode(),  # no newline
        f"{GOOD}\n\n".encode(),
        f" {GOOD}\n".encode(),
        f"{LETTERS.upper()}\n".encode(),
        b"\xff" * 10 + b"\n",
        b"x" * 65,
    ]

    assert json.loads(unlisted) == {"error": "not_allowed"}
    for request in garbage:
        reply = secrets_broker.handle("inert-landing", request, allowed, _fetcher(bws))
        assert json.loads(reply) == {"error": "bad_request"}, request
    assert bws.calls() == []
    lines = [json.loads(line) for line in capfd.readouterr().out.splitlines()]
    assert lines[0] == {"lane": "inert-landing", "uuid": OTHER, "outcome": "not_allowed"}
    assert all(
        line == {"lane": "inert-landing", "uuid": None, "outcome": "bad_request"}
        for line in lines[1:]
    )


def test_bws_failure_and_timeout_leak_nothing(bws: Bws, capfd: pytest.CaptureFixture) -> None:
    allowed = frozenset({FAILS, NOT_JSON, HANGS})
    fetcher = _fetcher(bws, timeout_s=0.5)

    for uuid in (FAILS, NOT_JSON, HANGS):
        started = time.monotonic()
        reply = secrets_broker.handle("inert-landing", f"{uuid}\n".encode(), allowed, fetcher)
        assert time.monotonic() - started < 5, "a hung bws is killed at the timeout"
        assert reply == b'{"error": "bws_failed"}\n'
        assert SECRET.encode() not in reply
    out, err = capfd.readouterr()
    assert SECRET not in out + err
    assert [json.loads(line)["outcome"] for line in out.splitlines()] == ["bws_failed"] * 3
    # A missing bws is a failure too, not an exception.
    assert secrets_broker.fetch(GOOD, str(bws.bin / "absent"), 1.0) is None


def test_each_fetch_gets_an_empty_home_and_no_colour(
    bws: Bws, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    monkeypatch.setenv("HOME", "/home/broker")

    assert secrets_broker.fetch(GOOD, str(bws.path), 5.0) == SECRET
    assert secrets_broker.fetch(OTHER, str(bws.path), 5.0) == SECRET
    homes = []
    for line in bws.environments():
        fields = dict(field.split("=", 1) for field in line.split())
        assert fields["FORCE_COLOR"] == "unset"
        assert fields["CLICOLOR_FORCE"] == "unset"
        assert fields["HOME_EMPTY"] == "0"
        homes.append(fields["HOME"])
    assert len(set(homes)) == 2, "a fresh HOME per call"
    assert "/home/broker" not in homes
    assert not any(Path(home).exists() for home in homes), "each HOME is removed afterwards"


@pytest.fixture
def served(bws: Bws) -> Iterator[Path]:
    """`serve` for two lanes on a root under `/tmp`, stopped after the test. Not `tmp_path`: on
    macOS its socket paths pass the 104-byte limit on a Unix socket's address."""
    root = Path(tempfile.mkdtemp(prefix="broker-", dir="/tmp"))
    # A stale socket file from an earlier run, in a directory with the wrong mode.
    (root / "inert-landing").mkdir(mode=0o777)
    (root / "inert-landing" / "broker.sock").write_text("stale")
    stop = threading.Event()
    allowlist = {"inert-landing": frozenset({GOOD}), "unit-verifier": frozenset({OTHER})}
    thread = threading.Thread(
        target=secrets_broker.serve, args=(allowlist, root, _fetcher(bws)), kwargs={"stop": stop}
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not all(_listening(root / lane / "broker.sock") for lane in allowlist):
        assert time.monotonic() < deadline, "the broker never bound its sockets"
        time.sleep(0.02)
    try:
        yield root
    finally:
        stop.set()
        thread.join(timeout=5)
        shutil.rmtree(root)
    assert not thread.is_alive()


def _listening(path: Path) -> bool:
    """True once `path` is a socket. The broker may remove the stale file between two calls."""
    try:
        return stat.S_ISSOCK(path.stat().st_mode)
    except FileNotFoundError:
        return False


def _connect(path: Path) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(10)
    client.connect(str(path))
    return client


def _ask(path: Path, request: bytes) -> bytes:
    with _connect(path) as client:
        client.sendall(request)
        reply = b""
        while chunk := client.recv(4096):
            reply += chunk
        return reply


def test_sockets_are_0600_in_0750_lane_directories(served: Path) -> None:
    for lane in ("inert-landing", "unit-verifier"):
        directory = served / lane
        assert stat.S_IMODE(directory.stat().st_mode) == 0o750, lane
        assert stat.S_IMODE((directory / "broker.sock").stat().st_mode) == 0o600, lane
    # The stale file was replaced, and each lane's socket answers for that lane's list alone.
    assert json.loads(_ask(served / "inert-landing" / "broker.sock", f"{GOOD}\n".encode())) == {
        "id": GOOD,
        "value": SECRET,
    }
    assert json.loads(_ask(served / "unit-verifier" / "broker.sock", f"{GOOD}\n".encode())) == {
        "error": "not_allowed"
    }


def test_oversized_and_silent_clients_dont_block_others(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(secrets_broker, "READ_TIMEOUT_S", 2.0)
    inert = served / "inert-landing" / "broker.sock"
    verifier = served / "unit-verifier" / "broker.sock"

    silent = _connect(inert)
    trickle = _connect(inert)
    trickle.sendall(GOOD[:4].encode())  # starts a request and never finishes it
    try:
        assert json.loads(_ask(inert, b"x" * 200)) == {"error": "bad_request"}
        assert json.loads(_ask(inert, b"garbage\n")) == {"error": "bad_request"}
        assert json.loads(_ask(verifier, f"{OTHER}\n".encode())) == {"id": OTHER, "value": SECRET}
        assert json.loads(_ask(inert, f"{GOOD}\n".encode())) == {"id": GOOD, "value": SECRET}
        # Others were served while both still held on: neither has had a reply yet.
        for held in (silent, trickle):
            held.setblocking(False)
            with pytest.raises(BlockingIOError):
                held.recv(4096)
            held.setblocking(True)
        # Each held connection is answered and closed at the read timeout.
        started = time.monotonic()
        for held in (silent, trickle):
            reply = b""
            while chunk := held.recv(4096):
                reply += chunk
            assert json.loads(reply) == {"error": "bad_request"}
        assert time.monotonic() - started < 4
    finally:
        silent.close()
        trickle.close()


def test_an_empty_allowlist_binds_nothing_and_stops_on_sigterm(tmp_path: Path, bws: Bws) -> None:
    allowlist = _allowlist(tmp_path, "[lanes]\n")
    root = tmp_path / "broker"
    root.mkdir()
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            secrets_broker.__file__,
            "--allowlist",
            str(allowlist),
            "--root",
            str(root),
        ],
        env={"PATH": f"{bws.bin}:/usr/bin:/bin", "BWS_ACCESS_TOKEN": "token"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.5)
    assert process.poll() is None, process.communicate()
    process.send_signal(signal.SIGTERM)
    out, err = process.communicate(timeout=10)

    assert process.returncode == 0, err
    assert list(root.iterdir()) == []
    assert "token" not in out + err


def test_main_refuses_a_bad_allowlist(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = tmp_path / "broker"
    code = secrets_broker.main(["--allowlist", str(tmp_path / "missing.toml"), "--root", str(root)])

    assert code == 2
    assert "allowlist" in capsys.readouterr().err
    assert not root.exists()


@pytest.mark.parametrize("token", [None, ""])
def test_main_refuses_to_start_without_a_token(
    tmp_path: Path,
    bws: Bws,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture,
    token: str | None,
) -> None:
    allowlist = _allowlist(tmp_path, f'[lanes.inert-landing]\nread = ["{GOOD}"]\n')
    root = tmp_path / "broker"
    monkeypatch.setenv("PATH", str(bws.bin))
    if token is None:
        monkeypatch.delenv("BWS_ACCESS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BWS_ACCESS_TOKEN", token)

    code = secrets_broker.main(["--allowlist", str(allowlist), "--root", str(root)])

    out, err = capfd.readouterr()
    assert code == 2
    assert out == ""
    assert [json.loads(line) for line in err.splitlines()] == [
        {"error": "BWS_ACCESS_TOKEN is not set"}
    ]
    assert not root.exists(), "nothing is bound"
    assert bws.calls() == []
