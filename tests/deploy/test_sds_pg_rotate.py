"""The VPS helper, driven against a fake `docker` on PATH."""

import importlib.machinery
import importlib.util
import json
import stat
from pathlib import Path
from types import ModuleType

import pytest

HELPER = Path(__file__).resolve().parents[2] / "deploy" / "vps" / "sds-pg-rotate"
GOOD = "a" * 64
NEW = "b" * 64

FAKE_DOCKER = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
stdin = sys.stdin.read()
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"argv": args, "stdin": stdin}) + "\\n")
if "printf" in " ".join(args):
    sys.stdout.write(os.environ.get("FAKE_ENV_VALUE", ""))
elif "-v" in args:
    sys.exit(0 if os.environ.get("FAKE_ALTER", "ok") == "ok" else 1)
else:
    mode = os.environ.get("FAKE_PROBE", "match")
    if mode == "match" and stdin.strip() == os.environ["FAKE_GOOD"]:
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


@pytest.fixture
def helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    targets = tmp_path / "targets.toml"
    targets.write_text(TARGETS)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("FAKE_GOOD", GOOD)
    loader = importlib.machinery.SourceFileLoader("sds_pg_rotate", str(HELPER))
    spec = importlib.util.spec_from_loader("sds_pg_rotate", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(module, "TARGETS_PATH", str(targets))
    return module


def calls(tmp_path: Path) -> list[dict]:
    log = tmp_path / "log.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def ask(helper: ModuleType, **request: object) -> tuple[dict, int]:
    return helper.run(json.dumps(request))


def test_set_alters_role_over_stdin(helper, tmp_path):
    answer, code = ask(helper, verb="set", target="app", password=NEW)
    assert (answer, code) == ({"changed": True}, 0)
    alter = calls(tmp_path)[-1]
    assert alter["argv"][:3] == ["exec", "-i", "db1"]
    assert "ON_ERROR_STOP=1" in alter["argv"]
    assert alter["stdin"] == f"ALTER ROLE \"app\" WITH PASSWORD '{NEW}';\n"


def test_set_is_a_no_op_when_the_password_already_works(helper, tmp_path):
    answer, code = ask(helper, verb="set", target="app", password=GOOD)
    assert (answer, code) == ({"changed": False}, 0)
    assert all("-v" not in c["argv"] for c in calls(tmp_path))


def test_set_failure_is_a_refusal_without_the_password(helper, monkeypatch):
    monkeypatch.setenv("FAKE_ALTER", "fail")
    answer, code = ask(helper, verb="set", target="app", password=NEW)
    assert code == 2
    assert NEW not in json.dumps(answer)


def test_probe_maps_success_to_200(helper):
    assert ask(helper, verb="probe", target="app", password=GOOD) == ({"status": 200}, 0)


def test_probe_maps_auth_failure_to_401(helper):
    assert ask(helper, verb="probe", target="app", password=NEW) == ({"status": 401}, 0)


def test_probe_maps_any_other_failure_to_null(helper, monkeypatch):
    monkeypatch.setenv("FAKE_PROBE", "down")
    assert ask(helper, verb="probe", target="app", password=GOOD) == ({"status": None}, 0)


def test_probe_never_uses_loopback(helper, tmp_path):
    ask(helper, verb="probe", target="app", password=GOOD)
    argv = calls(tmp_path)[0]["argv"]
    script = " ".join(argv)
    assert "hostname -i" in script
    assert "localhost" not in script
    assert "127.0.0.1" not in script
    assert "read -r PGPASSWORD" in script


def test_unknown_target_is_refused(helper, tmp_path):
    answer, code = ask(helper, verb="probe", target="nope", password=GOOD)
    assert (answer, code) == ({"refused": "unknown_target"}, 2)
    assert calls(tmp_path) == []


def test_unknown_verb_is_refused(helper):
    answer, code = ask(helper, verb="drop", target="app", password=GOOD)
    assert (answer, code) == ({"refused": "unknown_verb"}, 2)


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
    print_, length = answer["fingerprints"]["web1"].split(":")
    assert length == "64" and len(print_) == 16
    import hashlib

    assert print_ == hashlib.sha256(NEW.encode()).hexdigest()[:16]


def test_loaded_env_name_is_validated(helper, tmp_path):
    bad = Path(helper.TARGETS_PATH)
    bad.write_text(TARGETS.replace('"DATABASE_PASSWORD"', "'X\"; rm -rf /; \"'"))
    answer, code = ask(helper, verb="loaded", target="app")
    assert (answer, code) == ({"refused": "bad_target"}, 2)
    assert calls(tmp_path) == []
