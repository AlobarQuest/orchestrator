"""The VPS helper, driven against a fake `docker` and, for the probe script, fake psql/hostname."""

import ast
import base64
import hashlib
import hmac
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from rotation_worker.adapters.bws_lane_token import LoadedRead
from rotation_worker.helper_client import PROBE_TIMEOUT_SECONDS, SET_TIMEOUT_SECONDS, HelperClient
from rotation_worker.values import Value

HELPER = Path(__file__).resolve().parents[2] / "deploy" / "vps" / "sds-pg-rotate"
GOOD = "a" * 64
NEW = "b" * 64

FAKE_DOCKER = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
stdin = sys.stdin.read()
state = os.path.dirname(os.environ["FAKE_LOG"])
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"argv": args, "stdin": stdin}) + "\\n")
if "printf" in " ".join(args):
    sys.stdout.write(os.environ.get("FAKE_ENV_VALUE", ""))
elif "-v" in args:
    if os.environ.get("FAKE_ALTER", "ok") == "fail":
        sys.exit(1)
    if os.environ.get("FAKE_ALTER", "ok") == "ok":
        open(os.path.join(state, "altered"), "w").close()
else:
    mode = os.environ.get("FAKE_PROBE", "match")
    altered = os.path.exists(os.path.join(state, "altered"))
    if mode == "match" and (stdin.strip() == os.environ["FAKE_GOOD"] or altered):
        sys.exit(0)
    if mode in ("match", "authfail"):
        sys.stderr.write('FATAL:  password authentication failed for user "app"\\n')
    else:
        sys.stderr.write("could not connect to server\\n")
    sys.exit(2)
"""

TARGETS = """
[target.app]
container = "db1"
role = "app"
database = "appdb"

[[target.app.consumer]]
container = "web1"
env = "DATABASE_PASSWORD"
"""


def executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    docker = executable(tmp_path / "docker", FAKE_DOCKER)
    targets = tmp_path / "targets.toml"
    targets.write_text(TARGETS)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("FAKE_GOOD", GOOD)
    loader = importlib.machinery.SourceFileLoader("sds_pg_rotate", str(HELPER))
    spec = importlib.util.spec_from_loader("sds_pg_rotate", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(module, "TARGETS_PATH", str(targets))
    monkeypatch.setattr(module, "DOCKER", str(docker))
    return module


def calls(tmp_path: Path) -> list[dict]:
    log = tmp_path / "log.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def ask(helper: ModuleType, **request: object) -> tuple[dict, int]:
    return helper.run(json.dumps(request).encode())


def retarget(helper: ModuleType, text: str) -> None:
    Path(helper.TARGETS_PATH).write_text(text)


def test_source_parses_as_python_312():
    ast.parse(HELPER.read_text(), feature_version=(3, 12))


def test_uses_the_absolute_interpreter_and_docker():
    assert HELPER.read_text().startswith("#!/usr/bin/python3\n")
    assert 'DOCKER = "/usr/bin/docker"' in HELPER.read_text()


def test_set_alters_role_with_a_verifier_over_stdin(helper, tmp_path):
    answer, code = ask(helper, verb="set", target="app", password=NEW)
    assert (answer, code) == ({"changed": True}, 0)
    alter = next(c for c in calls(tmp_path) if "-v" in c["argv"])
    assert alter["argv"][:5] == [
        "exec",
        "-i",
        "-e",
        "PGOPTIONS=-c statement_timeout=20000",
        "db1",
    ]
    assert "ON_ERROR_STOP=1" in alter["argv"]
    assert alter["stdin"].startswith('ALTER ROLE "app" WITH PASSWORD \'SCRAM-SHA-256$4096:')
    assert NEW not in alter["stdin"]


def test_set_is_a_no_op_when_the_password_already_works(helper, tmp_path):
    answer, code = ask(helper, verb="set", target="app", password=GOOD)
    assert (answer, code) == ({"changed": False}, 0)
    assert all("ALTER ROLE" not in c["stdin"] for c in calls(tmp_path))


def test_set_refuses_when_the_probe_is_unavailable(helper, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PROBE", "down")
    answer, code = ask(helper, verb="set", target="app", password=NEW)
    assert (answer, code) == ({"refused": "probe_unavailable"}, 2)
    assert all("ALTER ROLE" not in c["stdin"] for c in calls(tmp_path))


def test_set_refuses_when_the_alter_does_not_take_effect(helper, monkeypatch):
    monkeypatch.setenv("FAKE_ALTER", "noeffect")
    answer, code = ask(helper, verb="set", target="app", password=NEW)
    assert (answer, code) == ({"refused": "alter_unproven"}, 2)


def test_set_failure_is_a_refusal_without_the_password(helper, monkeypatch):
    monkeypatch.setenv("FAKE_ALTER", "fail")
    answer, code = ask(helper, verb="set", target="app", password=NEW)
    assert (answer, code) == ({"refused": "alter_failed"}, 2)
    assert NEW not in json.dumps(answer)


def test_scram_verifier_matches_rfc_7677(helper):
    salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    # The RFC 7677 exchange for password "pencil".
    text = helper.scram_verifier("pencil", salt)
    head, rest = text.split("$", 1)
    iterations, salt_b64, keys = rest.replace(":", "$", 1).split("$")
    stored_b64, server_b64 = keys.split(":")
    assert (head, iterations, salt_b64) == ("SCRAM-SHA-256", "4096", "W22ZaJ0SNY7soEsUEjb6gQ==")
    auth_message = (
        b"n=user,r=rOprNGfwEbeRWgbNEkqO,"
        b"r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,"
        b"c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    )
    stored = base64.b64decode(stored_b64)
    proof = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    signature = hmac.new(stored, auth_message, hashlib.sha256).digest()
    client_key = bytes(a ^ b for a, b in zip(proof, signature, strict=True))
    assert hashlib.sha256(client_key).digest() == stored
    server_signature = hmac.new(base64.b64decode(server_b64), auth_message, hashlib.sha256)
    assert base64.b64encode(server_signature.digest()).decode() == (
        "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="
    )


def test_probe_maps_success_to_200(helper):
    assert ask(helper, verb="probe", target="app", password=GOOD) == ({"status": 200}, 0)


def test_probe_maps_auth_failure_to_401(helper):
    assert ask(helper, verb="probe", target="app", password=NEW) == ({"status": 401}, 0)


def test_probe_maps_any_other_failure_to_null(helper, monkeypatch):
    monkeypatch.setenv("FAKE_PROBE", "down")
    assert ask(helper, verb="probe", target="app", password=GOOD) == ({"status": None}, 0)


def test_probe_maps_a_docker_failure_to_null(helper, monkeypatch):
    monkeypatch.setattr(helper, "DOCKER", "/nonexistent/docker")
    assert ask(helper, verb="probe", target="app", password=GOOD) == ({"status": None}, 0)


def run_probe_script(helper, tmp_path, address: str) -> subprocess.CompletedProcess[str]:
    bindir = tmp_path / "probebin"
    bindir.mkdir()
    executable(bindir / "hostname", f"#!/bin/sh\necho '{address}'\n")
    executable(
        bindir / "psql",
        f'#!/bin/sh\necho "$@" > {tmp_path}/psql-args\ncat > {tmp_path}/psql-stdin\n',
    )
    env = {"PATH": f"{bindir}:/usr/bin:/bin"}
    return subprocess.run(
        ["sh", "-c", helper.PROBE_SCRIPT, "sh", "app", "appdb"],
        input="secret\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_probe_script_passes_a_real_address_to_psql(helper, tmp_path):
    done = run_probe_script(helper, tmp_path, "10.0.4.3 fd00::3")
    assert done.returncode == 0
    args = (tmp_path / "psql-args").read_text().split()
    assert args[:4] == ["-w", "-h", "10.0.4.3", "-U"]


@pytest.mark.parametrize("address", ["", "127.0.0.1", "127.0.1.1", "::1"])
def test_probe_script_never_reaches_psql_on_empty_or_loopback(helper, tmp_path, address):
    done = run_probe_script(helper, tmp_path, address)
    assert done.returncode != 0
    assert not (tmp_path / "psql-args").exists()


def test_unknown_target_is_refused(helper, tmp_path):
    answer, code = ask(helper, verb="probe", target="nope", password=GOOD)
    assert (answer, code) == ({"refused": "unknown_target"}, 2)
    assert calls(tmp_path) == []


def test_unknown_verb_is_refused(helper):
    answer, code = ask(helper, verb="drop", target="app", password=GOOD)
    assert (answer, code) == ({"refused": "unknown_verb"}, 2)


@pytest.mark.parametrize("raw", [b"\xff\xfe", b"{not json", b"[]", b"", b"[" * 100000])
def test_malformed_input_is_a_refusal(helper, raw):
    assert helper.run(raw) == ({"refused": "bad_request"}, 2)


@pytest.mark.parametrize("bad", ["short", "A" * 64, "a" * 63 + "'", "a" * 65, 7])
def test_non_hex_password_is_refused_before_docker_runs(helper, tmp_path, bad):
    answer, code = ask(helper, verb="set", target="app", password=bad)
    assert (answer, code) == ({"refused": "bad_password"}, 2)
    assert calls(tmp_path) == []


def test_no_password_appears_in_docker_argv(helper, tmp_path):
    ask(helper, verb="set", target="app", password=NEW)
    ask(helper, verb="probe", target="app", password=GOOD)
    for call in calls(tmp_path):
        assert NEW not in " ".join(call["argv"])
        assert GOOD not in " ".join(call["argv"])


def test_loaded_returns_fingerprints_only(helper, monkeypatch):
    monkeypatch.setenv("FAKE_ENV_VALUE", NEW)
    answer, code = ask(helper, verb="loaded", target="app")
    assert code == 0
    assert NEW not in json.dumps(answer)
    print_, length = answer["fingerprints"]["web1/DATABASE_PASSWORD"].split(":")
    assert length == "64"
    assert print_ == hashlib.sha256(NEW.encode()).hexdigest()[:16]


def test_loaded_keeps_two_variables_in_one_container_apart(helper, monkeypatch):
    retarget(helper, TARGETS + '\n[[target.app.consumer]]\ncontainer = "web1"\nenv = "OTHER"\n')
    monkeypatch.setenv("FAKE_ENV_VALUE", NEW)
    answer, _ = ask(helper, verb="loaded", target="app")
    assert set(answer["fingerprints"]) == {"web1/DATABASE_PASSWORD", "web1/OTHER"}


def test_loaded_env_name_is_validated(helper, tmp_path):
    retarget(helper, TARGETS.replace('"DATABASE_PASSWORD"', "'X\"; rm -rf /; \"'"))
    answer, code = ask(helper, verb="loaded", target="app")
    assert (answer, code) == ({"refused": "bad_target"}, 2)
    assert calls(tmp_path) == []


@pytest.mark.parametrize(
    "field",
    [
        ('container = "db1"', 'container = "db 1; x"'),
        ('role = "app"', 'role = "a\'pp"'),
        ('database = "appdb"', 'database = "app db"'),
        ('container = "web1"', 'container = "web1 --privileged"'),
    ],
)
def test_bad_identifiers_are_refused(helper, tmp_path, field):
    retarget(helper, TARGETS.replace(*field))
    verbs = ("loaded",) if "web1" in field[1] else ("probe", "loaded")
    for verb in verbs:
        answer, code = ask(helper, verb=verb, target="app", password=GOOD)
        assert (answer, code) == ({"refused": "bad_target"}, 2)
    assert calls(tmp_path) == []


@pytest.mark.parametrize(
    "tail",
    ["", "consumer = ['x']\n"],
)
def test_loaded_refuses_no_consumers_or_a_non_table_entry(helper, tail):
    head = TARGETS.split("[[target.app.consumer]]")[0]
    retarget(helper, head + tail)
    assert ask(helper, verb="loaded", target="app") == ({"refused": "bad_target"}, 2)


def test_targets_not_owned_by_root_are_refused_when_running_as_root(helper, monkeypatch):
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    if os.stat(helper.TARGETS_PATH).st_uid == 0:
        pytest.skip("test file is already root-owned")
    answer, code = ask(helper, verb="probe", target="app", password=GOOD)
    assert (answer, code) == ({"refused": "targets_untrusted"}, 2)


# `set` runs a probe, the ALTER, and a probe to prove it.
SET_DOCKER_CALLS = 3


def test_the_helper_finishes_before_the_client_stops_waiting(helper):
    """Otherwise an ALTER can land after the client gave up, behind the undo's `set`."""
    assert SET_DOCKER_CALLS * helper.TIMEOUT < SET_TIMEOUT_SECONDS
    assert helper.TIMEOUT < PROBE_TIMEOUT_SECONDS


# ---- a consumers-only target (ADR-0055 amendment 29 item 3) ---------------------------------

LANE_TARGETS = """
[target.lane]

[[target.lane.consumer]]
container = "lane1"
env = "BWS_ACCESS_TOKEN"
"""


def test_a_consumers_only_target_answers_loaded(helper, monkeypatch, tmp_path):
    retarget(helper, LANE_TARGETS)
    monkeypatch.setenv("FAKE_ENV_VALUE", NEW)
    answer, code = ask(helper, verb="loaded", target="lane")
    assert code == 0
    assert answer == {
        "fingerprints": {
            "lane1/BWS_ACCESS_TOKEN": f"{hashlib.sha256(NEW.encode()).hexdigest()[:16]}:64"
        }
    }
    assert calls(tmp_path)[0]["argv"] == [
        "exec",
        "lane1",
        "sh",
        "-c",
        'printf %s "$BWS_ACCESS_TOKEN"',
    ]


@pytest.mark.parametrize("verb", ["set", "probe"])
def test_a_consumers_only_target_refuses_set_and_probe(helper, tmp_path, verb):
    retarget(helper, LANE_TARGETS)
    assert ask(helper, verb=verb, target="lane", password=GOOD) == ({"refused": "bad_target"}, 2)
    assert calls(tmp_path) == []


@pytest.mark.parametrize("key", ['container = "db1"', 'role = "app"', 'database = "appdb"'])
@pytest.mark.parametrize("verb", ["set", "probe", "loaded"])
def test_a_target_naming_only_part_of_a_database_is_refused_for_every_verb(
    helper, tmp_path, key, verb
):
    retarget(helper, LANE_TARGETS.replace("[target.lane]\n", f"[target.lane]\n{key}\n"))
    assert ask(helper, verb=verb, target="lane", password=GOOD) == ({"refused": "bad_target"}, 2)
    assert calls(tmp_path) == []


@pytest.mark.parametrize(
    "consumers",
    [
        "",
        "consumer = []\n",
        '[[target.lane.consumer]]\ncontainer = "lane1"\n',
        '[[target.lane.consumer]]\nenv = "BWS_ACCESS_TOKEN"\n',
        '[[target.lane.consumer]]\ncontainer = "lane1"\nenv = "bws_access_token"\n',
        '[[target.lane.consumer]]\ncontainer = "lane1"\nenv = "X; id"\n',
    ],
    ids=["no-consumer", "empty", "no-env", "no-container", "lowercase-env", "injected-env"],
)
def test_a_consumers_only_target_with_a_missing_or_malformed_consumer_is_refused(
    helper, tmp_path, consumers
):
    retarget(helper, "[target.lane]\n" + consumers)
    assert ask(helper, verb="loaded", target="lane") == ({"refused": "bad_target"}, 2)
    assert calls(tmp_path) == []


def test_a_postgres_target_still_serves_every_verb(helper, monkeypatch):
    monkeypatch.setenv("FAKE_ENV_VALUE", NEW)
    assert ask(helper, verb="probe", target="app", password=GOOD) == ({"status": 200}, 0)
    assert ask(helper, verb="set", target="app", password=GOOD) == ({"changed": False}, 0)
    assert ask(helper, verb="loaded", target="app")[1] == 0


def test_the_helper_s_print_and_the_lane_token_adapter_s_compare_equal(
    helper, monkeypatch, tmp_path
):
    """The helper's `loaded` answer, through the executor's client and the adapter's comparison,
    matches the value it read and no other: a drift in either side's format fails here instead of
    making every reissue wait forever."""
    retarget(helper, LANE_TARGETS)
    monkeypatch.setenv("FAKE_ENV_VALUE", NEW)

    def ssh(argv: list[str], *, input: bytes, **_options: object) -> subprocess.CompletedProcess:
        answer, code = helper.run(input)
        return subprocess.CompletedProcess(argv, code, (json.dumps(answer) + "\n").encode(), b"")

    client = HelperClient("root@vps", "vps ssh-ed25519 AAAA", Value("k" * 32), tmp_path, run=ssh)
    read = LoadedRead("lane", lambda: client)
    assert read.mismatch(Value(NEW).fingerprint) is None
    assert read.mismatch(Value(GOOD).fingerprint) is not None
    assert read.mismatch(Value(NEW[:-1]).fingerprint) is not None
