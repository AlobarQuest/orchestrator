"""The SSH client to the VPS Postgres helper: argv, stdin, the key file, and refusals."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from rotation_worker.contract import ContractError
from rotation_worker.helper_client import HelperClient
from rotation_worker.values import Value

PASSWORD = Value("ab" * 32)
KEY = Value(
    "-----BEGIN OPENSSH PRIVATE KEY-----\nthrowaway-key-body\n-----END OPENSSH PRIVATE KEY-----"
)
KNOWN = "ovh-vps1 ssh-ed25519 AAAAthrowaway"


class Ssh:
    def __init__(self, stdout: str = '{"changed": true}', code: int = 0) -> None:
        self.stdout, self.code = stdout, code
        self.argv: list[str] = []
        self.stdin = ""
        self.key_mode = 0
        self.key_text = ""
        self.known_text = ""
        self.key_path = Path()

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.argv = argv
        self.stdin = kwargs["input"].decode()
        assert kwargs["timeout"] == 120 and kwargs["capture_output"]
        self.key_path = Path(argv[argv.index("-i") + 1])
        self.key_mode = stat.S_IMODE(os.stat(self.key_path).st_mode)
        self.key_text = self.key_path.read_text()
        known = next(a for a in argv if a.startswith("UserKnownHostsFile="))
        self.known_text = Path(known.split("=", 1)[1]).read_text()
        return subprocess.CompletedProcess(argv, self.code, self.stdout.encode(), b"stderr-secret")


def client(ssh: Ssh, tmp_path: Path) -> HelperClient:
    return HelperClient("root@ovh-vps1", KNOWN, KEY, tmp_path, run=ssh)


def test_ssh_argv_pins_the_host_key_and_batch_mode(tmp_path: Path) -> None:
    ssh = Ssh()
    client(ssh, tmp_path).set("t", PASSWORD)
    assert ssh.argv[0] == "/usr/bin/ssh" and ssh.argv[-1] == "root@ovh-vps1"
    for option in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes"):
        assert option in ssh.argv
    assert ssh.known_text.strip() == KNOWN


def test_value_goes_on_stdin_not_argv(tmp_path: Path) -> None:
    ssh = Ssh()
    client(ssh, tmp_path).set("t", PASSWORD)
    assert json.loads(ssh.stdin) == {"verb": "set", "target": "t", "password": PASSWORD.reveal()}
    assert PASSWORD.reveal() not in " ".join(ssh.argv)
    assert KEY.reveal() not in " ".join(ssh.argv)


def test_key_file_is_0600_and_removed_after_the_call(tmp_path: Path) -> None:
    ssh = Ssh()
    client(ssh, tmp_path).set("t", PASSWORD)
    assert ssh.key_mode == 0o600
    assert ssh.key_text.strip() == KEY.reveal()
    assert not ssh.key_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_key_file_is_removed_when_the_call_fails(tmp_path: Path) -> None:
    def boom(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(argv, 120)

    with pytest.raises(ContractError, match="in time"):
        HelperClient("h", KNOWN, KEY, tmp_path, run=boom).set("t", PASSWORD)
    assert list(tmp_path.iterdir()) == []


def test_host_key_mismatch_raises_without_the_value(tmp_path: Path) -> None:
    ssh = Ssh(stdout="", code=255)
    with pytest.raises(ContractError) as raised:
        client(ssh, tmp_path).set("t", PASSWORD)
    text = str(raised.value)
    assert "255" in text
    assert PASSWORD.reveal() not in text and "stderr-secret" not in text


def test_a_refusal_names_its_reason_only(tmp_path: Path) -> None:
    ssh = Ssh(stdout='{"refused": "alter_unproven"}', code=2)
    with pytest.raises(ContractError, match="alter_unproven"):
        client(ssh, tmp_path).set("t", PASSWORD)
    ssh = Ssh(stdout='{"refused": "bad reason " }', code=2)
    with pytest.raises(ContractError, match="unknown"):
        client(ssh, tmp_path).probe("t", PASSWORD)


def test_probe_and_loaded_answers(tmp_path: Path) -> None:
    assert client(Ssh('{"status": 401}'), tmp_path).probe("t", PASSWORD) == 401
    assert client(Ssh('{"status": null}'), tmp_path).probe("t", PASSWORD) is None
    prints = {"web-1/DB_PASSWORD": "0123456789abcdef:64", "web-1/OTHER": ""}
    ssh = Ssh(json.dumps({"fingerprints": prints}))
    assert client(ssh, tmp_path).loaded("t") == prints
    assert "password" not in json.loads(ssh.stdin)


@pytest.mark.parametrize("stdout", ['{"status": 500}', "not json", '{"fingerprints": []}'])
def test_malformed_answers_are_refused(tmp_path: Path, stdout: str) -> None:
    with pytest.raises(ContractError):
        client(Ssh(stdout), tmp_path).loaded("t")


@pytest.mark.parametrize("stdout", ['{"status": 500}', "not json"])
def test_a_malformed_probe_is_refused(tmp_path: Path, stdout: str) -> None:
    with pytest.raises(ContractError):
        client(Ssh(stdout), tmp_path).probe("t", PASSWORD)
