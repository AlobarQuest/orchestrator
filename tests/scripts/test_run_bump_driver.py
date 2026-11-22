"""`scripts/run-bump-driver.sh` in the lane's container, and the lane's files.

The real launcher runs under `env -i` from a throwaway tree, with what `lane-run` passes it in the
container: the lane's token as `BWS_ACCESS_TOKEN` (or, on the secrets broker, `SDS_SECRETS_SOCKET`
naming a stand-in broker's socket, `conftest.py`'s), the state directory, `PATH` and a new `HOME`.
`bws`, `gh`, `curl` and `git` are stubs on PATH; the `git` stub "clones" by writing a stand-in
`intent_packages.factory_cli` and `code_standards.cli` into the destination, so the `factory` and
`code-standards` shims run for real. In the venv, `bump-driver` and `sds-github-mint` are stubs
that log what reached them, and `python` runs the real interpreter (`sds-bws.sh` parses BWS's JSON
with it, and the shims run it) except for `-m orchestrator.factory_chain_read`, the chain walk,
which a stand-in answers with `$FAKE_WALK` after logging what reached it. A broad-identity
stand-in keeps the real Keychain out of the outcome alarm's by-name lookup. Nothing reads BWS,
GitHub, change-manager, the orchestrator, alert-hub, the Brains or Healthchecks.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import deploy_orchestrator as deploy
from scripts import lane_run
from scripts.secrets_broker import load_allowlist
from tests.scripts.test_run_unit_verifier import lane_start_minutes

LAUNCHER = Path("scripts/run-bump-driver.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh", "sds-git.sh")
COMPOSE = Path("lanes/bump-driver/compose.yaml")
CRONTAB = Path("lanes/bump-driver/crontab")

SYSTEM_UUID = "221a48d5-3f29-4898-b300-b4820140c880"
ALERT_UUID = "b415d03b-f92d-46b1-8a9d-b4f10103df49"
READ_UUID = "314f276d-55ca-4ddc-a24d-b4a3013508cd"
CODE_BRAIN_UUID = "750f737f-4cb6-4876-9a98-b48200ea1c0b"
INFRA_BRAIN_UUID = "da8134b0-565f-45c8-8965-b48200ea1c40"
READ_APP_KEY_UUID = "314a966c-5654-4e3c-8358-b4fd01268775"
CHAIN_READ_UUID = "2f708d08-ea5d-4d11-bde0-b50200c9a60b"
HEALTHCHECKS_KEY_UUID = "51509d5e-64f5-4781-ae82-b4ed00b644e1"
# The launcher's five, then the chain's read password and the App key the mint needs.
FIVE = (SYSTEM_UUID, ALERT_UUID, READ_UUID, CODE_BRAIN_UUID, INFRA_BRAIN_UUID)
LANE_SECRETS = (*FIVE, CHAIN_READ_UUID, READ_APP_KEY_UUID)
CLONES = ("intent-packages", "project-standards", "security-standards", "code-standards")
CHAIN_SETTINGS = {
    "FACTORY_EVENTS_DB_HOST": "b82fa1vg5whecgnjreuqb5tk",
    "FACTORY_EVENTS_DB_PORT": "5432",
    "FACTORY_EVENTS_DB_NAME": "postgres",
}
# A verified walk's record, as `orchestrator.factory_chain_read` prints it, with no event yet.
VERIFIED_WALK = (
    '{"outcome": "verified", "seq": 7, "head": "abcdef012345", "events": 7, '
    '"package_approved": [], "portfolio_scan": {"scan": null}, '
    '"mac_conformance": {"event": null, "reason": "no mac_conformance.reported event in the chain",'
    ' "skipped": 0, "skipped_reason": null}}'
)

LANE_TOKEN = "a-lane-token"
READ_TOKEN = "ghs-minted-read-token"
BROAD_STANDIN = "broad-stand-in"
REPOSITORIES = (
    "AlobarQuest/change-manager",
    "AlobarQuest/intent-packages",
    "AlobarQuest/project-standards",
    "AlobarQuest/security-standards",
)


def value(uuid: str) -> str:
    return f"value-of-{uuid}"


# The `bws` stub logs the UUID, the token it was given and its argv; a UUID in `$BWS_REFUSES` fails.
_BWS = """#!/bin/sh
echo "$3 token=${BWS_ACCESS_TOKEN:-} argv=$*" >> "$BWS_LOG"
case " ${BWS_REFUSES:-} " in *" $3 "*) exit 1 ;; esac
printf '{"value": "value-of-%s"}' "$3"
"""
_GH = '#!/bin/sh\necho "gh $*" >> "$GH_LOG"\necho gh-token\n'
_CURL = '#!/bin/sh\necho "curl $*" >> "$PING_LOG"\nexit 1\n'
# git: logs its argv and the credential git's environment carries, refuses a URL naming
# `$GIT_REFUSES`, and otherwise writes a stand-in intent-packages module into the destination.
_GIT = """#!/bin/bash
# Records whether the chain's read password is in its environment.
echo "envscan $0 $(env | grep -c "value-of-2f708d08" || true)" >> "$LANE_LOG"
echo "git args=$* count=${GIT_CONFIG_COUNT:-} key0=${GIT_CONFIG_KEY_0:-} \\
value0=${GIT_CONFIG_VALUE_0:-} lane_token=${BWS_ACCESS_TOKEN:+set} \\
socket=${SDS_SECRETS_SOCKET:+set}" >> "$STUB_GIT_LOG"
if [ -n "${GIT_REFUSES:-}" ]; then
  case "$*" in *"$GIT_REFUSES"*) echo "fatal: repository not found" >&2; exit 128 ;; esac
fi
dest="${@: -1}"
mkdir -p "$dest/src/intent_packages" "$dest/src/code_standards"
touch "$dest/src/intent_packages/__init__.py" "$dest/src/code_standards/__init__.py"
cat > "$dest/src/intent_packages/factory_cli.py" <<'PY'
import os
import sys

if __name__ == "__main__":
    print("factory argv=" + " ".join(sys.argv[1:]))
    print("factory pythonpath=" + os.environ.get("PYTHONPATH", ""))
PY
cat > "$dest/src/code_standards/cli.py" <<'PY'
import os
import sys

import tomlkit

if __name__ == "__main__":
    print("code-standards argv=" + " ".join(sys.argv[1:]) + " tomlkit=" + tomlkit.__name__)
    print("code-standards pythonpath=" + os.environ.get("PYTHONPATH", ""))
    print("code-standards path0=" + sys.path[0])
PY
"""
# python: the chain walk is a stand-in that logs what reached it; everything else is the real
# interpreter.
_PYTHON = """#!/bin/bash
if [ "${1:-}" = -m ] && [ "${2:-}" = orchestrator.factory_chain_read ]; then
  echo "walk args=$* password=${FACTORY_EVENTS_READ_PASSWORD:-} \\
host=${FACTORY_EVENTS_DB_HOST:-} port=${FACTORY_EVENTS_DB_PORT:-} \\
name=${FACTORY_EVENTS_DB_NAME:-} pythonpath=${PYTHONPATH:-} \\
lane_token=${BWS_ACCESS_TOKEN:+set} socket=${SDS_SECRETS_SOCKET:+set} \\
github=${BUMP_DRIVER_GITHUB_TOKEN:+set}" >> "$LANE_LOG"
  echo "a driver's text with connection details" >&2
  printf '%s\\n' "$FAKE_WALK"
  exit "${FAKE_WALK_RC:-0}"
fi
exec "__PYTHON__" "$@"
"""
# bump-driver: `repositories` logs what it was handed and prints the lane's list; a pass logs its
# argv and every variable it was handed, one per line, and runs `factory` as the generator does
# when asked to.
_DRIVER = """#!/bin/bash
# Records whether the chain's read password is in its environment.
echo "envscan $0 $(env | grep -c "value-of-2f708d08" || true)" >> "$LANE_LOG"
if [ "$1" = repositories ]; then
  echo "derive args=$* lane_token=${BWS_ACCESS_TOKEN:+set} \\
socket=${SDS_SECRETS_SOCKET:+set} system=${ORCHESTRATOR_API_TOKEN:-} \\
key=${ORCHESTRATOR_API_CREDENTIAL_KEY_ID:-}" >> "$LANE_LOG"
  if [ -n "${FAKE_DERIVE_RC:-}" ]; then
    echo "the member record could not be read: the orchestrator answered 503" >&2
    exit "$FAKE_DERIVE_RC"
  fi
  printf '%s' "${FAKE_REPOSITORIES-AlobarQuest/change-manager
AlobarQuest/intent-packages
AlobarQuest/project-standards
AlobarQuest/security-standards}"
  exit 0
fi
{
  echo "driver args=$*"
  env | sort | sed 's/^/driver-env /'
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --conformance-facts ]; then
      echo "driver-facts $(cat "$2")"
      echo "$2" > "$LANE_LOG.facts-path"
    fi
    shift
  done
} >> "$LANE_LOG"
if [ -n "${FAKE_RUN_FACTORY:-}" ]; then
  env -i PATH="$PATH" HOME="$HOME" PYTHONPATH="$FACTORY_PYTHONPATH" \\
    factory decompose --no-execute >> "$LANE_LOG" 2>&1
fi
echo "a bump-driver report line"
exit "${FAKE_DRIVER_RC:-0}"
"""
_MINT = """#!/bin/bash
# Records whether the chain's read password is in its environment.
echo "envscan $0 $(env | grep -c "value-of-2f708d08" || true)" >> "$LANE_LOG"
key="$(cat)"
echo "mint args=$* key=$key lane_token=${BWS_ACCESS_TOKEN:+set} \\
socket=${SDS_SECRETS_SOCKET:+set}" >> "$LANE_LOG"
[ -n "${FAKE_MINT_RC:-}" ] && exit "$FAKE_MINT_RC"
echo "%s"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


class Run:
    def __init__(self, result: subprocess.CompletedProcess[str], logs: dict, home: Path) -> None:
        self.result = result
        self.logs = logs
        self.home = home

    @property
    def code(self) -> int:
        return self.result.returncode

    @property
    def output(self) -> str:
        return self.result.stdout + self.result.stderr

    def reads(self) -> list[tuple[str, str]]:
        """Each BWS read, in order, as (UUID, the token it was read with)."""
        return [
            (line.split()[0], line.split(" token=", 1)[1].split(" argv=", 1)[0])
            for line in self.logs["bws"]
        ]

    def lane_reads(self) -> list[str]:
        return [uuid for uuid, token in self.reads() if token == LANE_TOKEN]

    def lines(self, prefix: str) -> list[str]:
        return [line for line in self.logs["lane"] if line.startswith(prefix + " ")]

    def env(self) -> dict[str, str]:
        """The variables the driver's pass was handed."""
        pairs = (line.removeprefix("driver-env ") for line in self.lines("driver-env"))
        return dict(pair.split("=", 1) for pair in pairs)

    def passes(self) -> list[str]:
        return self.lines("driver")

    def facts(self) -> dict:
        """The conformance facts file the driver was handed."""
        [line] = self.lines("driver-facts")
        return json.loads(line.removeprefix("driver-facts "))


def _run(tmp_path: Path, *args: str, **fake: str | None) -> Run:
    """A `None` in `fake` removes that variable from the launcher's environment."""
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    # The venv's own path, not its resolved target, so the interpreter keeps the venv's packages.
    real = str(Path.cwd() / ".venv" / "bin" / "python")
    _executable(venv / "python", _PYTHON.replace("__PYTHON__", real))
    _executable(venv / "bump-driver", _DRIVER)
    _executable(venv / "sds-github-mint", _MINT % READ_TOKEN)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("bws", _BWS), ("gh", _GH), ("curl", _CURL), ("git", _GIT)):
        _executable(stubs / name, body)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    logs = {name: tmp_path / f"{name}.log" for name in ("lane", "bws", "gh", "ping", "git")}
    for log in logs.values():
        log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN": LANE_TOKEN,
        "SDS_DEADMAN_PING_URL_UUID": "",
        "BUMP_DRIVER_STATE_DIR": str(tmp_path / "state"),
        # The outcome alarm's name lookup, with no ping-URL id, asks for the broad identity on an
        # acting pass; the stand-in keeps the real Keychain out of the test. The container has
        # neither the Keychain nor this variable, which `lane-run` never passes.
        "BWS_ACCESS_TOKEN_BROAD": BROAD_STANDIN,
        "LANE_LOG": str(logs["lane"]),
        "BWS_LOG": str(logs["bws"]),
        "GH_LOG": str(logs["gh"]),
        "PING_LOG": str(logs["ping"]),
        "STUB_GIT_LOG": str(logs["git"]),
        "FAKE_WALK": VERIFIED_WALK,
        **CHAIN_SETTINGS,
        **fake,
    }
    result = subprocess.run(
        [
            "/usr/bin/env",
            "-i",
            *(f"{k}={v}" for k, v in env.items() if v is not None),
            "/bin/bash",
            str(root / "scripts" / LAUNCHER.name),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return Run(result, {name: log.read_text().splitlines() for name, log in logs.items()}, home)


# ---- the secrets, read with the lane's one token ---------------------------------------------


@pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["acting", "dry-run"])
def test_each_pass_reads_its_six_secrets_with_the_lanes_one_token(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    assert run.code == 0, run.output
    assert run.lane_reads() == list(LANE_SECRETS)
    others = [(uuid, token) for uuid, token in run.reads() if token != LANE_TOKEN]
    # Only an acting pass resolves a ping URL; with no ping-URL id its name lookup reads the
    # Healthchecks key, by the broad stand-in that exists only in this test.
    assert others == ([] if args else [(HEALTHCHECKS_KEY_UUID, BROAD_STANDIN)])
    for uuid in LANE_SECRETS:
        assert value(uuid) not in run.output
    assert LANE_TOKEN not in run.output and READ_TOKEN not in run.output


def test_with_a_ping_url_named_the_healthchecks_key_is_never_read(tmp_path: Path) -> None:
    """The container's outcome alarm reads its own ping-URL secret and nothing else of its own."""
    ping_url_secret = "11111111-2222-4333-8444-555555555555"
    run = _run(tmp_path, SDS_DEADMAN_PING_URL_UUID=ping_url_secret, BWS_ACCESS_TOKEN_BROAD=None)
    assert run.code == 0, run.output
    assert run.lane_reads() == [ping_url_secret, *LANE_SECRETS]
    assert HEALTHCHECKS_KEY_UUID not in [uuid for uuid, _ in run.reads()]


@pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["acting", "dry-run"])
def test_the_pass_is_handed_its_credentials_and_not_the_lanes_token(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    assert run.code == 0, run.output
    env = run.env()
    assert env["ORCHESTRATOR_API_TOKEN"] == value(SYSTEM_UUID)
    assert env["ORCHESTRATOR_API_CREDENTIAL_KEY_ID"] == "orchestrator-system"
    assert env["SDS_BRIDGE_ALERT_TOKEN"] == value(ALERT_UUID)
    assert env["CHANGE_MANAGER_TOKEN"] == value(READ_UUID)
    assert env["CODE_BRAIN_KEY"] == value(CODE_BRAIN_UUID)
    assert env["INFRA_BRAIN_KEY"] == value(INFRA_BRAIN_UUID)
    assert env["BUMP_DRIVER_GITHUB_TOKEN"] == READ_TOKEN
    assert env["BUMP_DRIVER_STATE_DIR"] == str(tmp_path / "state")
    assert "BWS_ACCESS_TOKEN" not in env
    # The service URLs are the program's defaults: nothing in the container overrides them.
    assert "ORCHESTRATOR_API_URL" not in env and "CHANGE_MANAGER_URL" not in env
    # git's credential went with the clones: the driver's own git builds its environment itself.
    assert not [name for name in env if name.startswith("GIT_")]


def test_credentials_already_in_the_environment_are_never_used(tmp_path: Path) -> None:
    """Each secret is always read: `lane-run` passes none, and a preset value is discarded."""
    preset = {
        name: "preset"
        for name in (
            "ORCHESTRATOR_API_TOKEN",
            "SDS_BRIDGE_ALERT_TOKEN",
            "CHANGE_MANAGER_TOKEN",
            "CODE_BRAIN_KEY",
            "INFRA_BRAIN_KEY",
            "BUMP_DRIVER_GITHUB_TOKEN",
            "FACTORY_PYTHONPATH",
        )
    }
    run = _run(tmp_path, **preset)
    assert run.code == 0, run.output
    assert run.lane_reads() == list(LANE_SECRETS)
    assert "preset" not in run.env().values()


@pytest.mark.parametrize("uuid", FIVE)
def test_an_unreadable_secret_fails_the_pass_before_the_driver(tmp_path: Path, uuid: str) -> None:
    run = _run(tmp_path, BWS_REFUSES=uuid)
    assert run.code == 1, run.output
    assert "FATAL: could not read" in run.output
    assert "(the lane's socket or token)" in run.output
    assert run.passes() == [] and run.lines("mint") == []


def test_the_state_directory_must_be_set(tmp_path: Path) -> None:
    run = _run(tmp_path, BUMP_DRIVER_STATE_DIR=None)
    assert run.code == 1
    assert "BUMP_DRIVER_STATE_DIR is not set" in run.output
    assert run.lane_reads() == [] and run.passes() == []


@pytest.mark.parametrize(
    "existing", ["Projects", "Developer", ".local", "infra-drift", "Library", ".claude"]
)
def test_a_home_that_is_not_new_fails_before_any_read(tmp_path: Path, existing: str) -> None:
    """The clones, the shims and the Mac's facts go under `HOME`, so a real one is refused before
    anything: each path the pass writes, `Developer` and the driver's three included."""
    (tmp_path / "home" / existing).mkdir(parents=True)
    run = _run(tmp_path)
    assert run.code == 1, run.output
    assert "is not a new directory" in run.output
    assert run.lane_reads() == [] and run.logs["git"] == [] and run.passes() == []


@pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["acting", "dry-run"])
def test_no_token_reaches_an_argument(tmp_path: Path, args: tuple[str, ...]) -> None:
    run = _run(tmp_path, *args, FAKE_RUN_FACTORY="1")
    assert run.code == 0, run.output
    argvs = [
        *(line.split(" argv=", 1)[1] for line in run.logs["bws"]),
        *(line.split(" key=", 1)[0] for line in run.lines("mint")),
        *(line.split(" lane_token=", 1)[0] for line in run.lines("derive")),
        *(line.split(" count=", 1)[0] for line in run.logs["git"]),
        *run.passes(),
        *run.lines("factory"),
        *run.logs["ping"],
    ]
    secrets = (LANE_TOKEN, READ_TOKEN, *(value(uuid) for uuid in LANE_SECRETS))
    for argv in argvs:
        for secret in secrets:
            assert secret not in argv, argv


# ---- the read token --------------------------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["acting", "dry-run"])
def test_the_mint_names_the_derived_repositories_and_three_read_permissions(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    assert run.code == 0, run.output
    targets = " ".join(f"--repository {repository}" for repository in REPOSITORIES)
    assert run.lines("mint") == [
        f"mint args=--app-id 5684758 {targets}"
        " --permission metadata=read --permission pull_requests=read --permission contents=read"
        # The key reaches the mint undecoded, in the form BWS holds it, without the lane's token.
        f" key={value(READ_APP_KEY_UUID)} lane_token= socket="
    ]


def test_the_list_is_derived_from_the_member_record_as_system_without_the_lanes_token(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    assert run.lines("derive") == [
        f"derive args=repositories lane_token= socket= system={value(SYSTEM_UUID)}"
        " key=orchestrator-system"
    ]


@pytest.mark.parametrize("rc", [1, 3])
def test_an_underivable_list_mints_nothing_and_its_code_is_the_passs(
    tmp_path: Path, rc: int
) -> None:
    run = _run(tmp_path, FAKE_DERIVE_RC=str(rc))
    assert run.code == rc, run.output
    assert f"bump-driver repositories exited {rc}" in run.output
    assert "the member record could not be read: the orchestrator answered 503" in run.output
    assert run.lines("mint") == [] and run.passes() == []
    assert READ_APP_KEY_UUID not in run.lane_reads()


def test_an_empty_list_mints_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path, FAKE_REPOSITORIES="")
    assert run.code == 1, run.output
    assert "printed no repository" in run.output
    assert run.lines("mint") == [] and run.passes() == []


def test_a_failed_mint_fails_the_pass(tmp_path: Path) -> None:
    run = _run(tmp_path, FAKE_MINT_RC="1")
    assert run.code == 1, run.output
    assert "could not mint the bump driver's GitHub read token" in run.output
    assert run.passes() == [] and run.logs["git"] == []


# ---- the clones and `factory` ----------------------------------------------------------------


def test_the_four_clones_are_shallow_at_main_under_home_with_the_read_token(
    tmp_path: Path,
) -> None:
    """code-standards goes to `~/Developer`, the one path the matrix resolves for it."""
    run = _run(tmp_path)
    assert run.code == 0, run.output
    header = (
        "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{READ_TOKEN}".encode()).decode()
    )
    destinations = {name: run.home / "Projects" / name for name in CLONES}
    destinations["code-standards"] = run.home / "Developer" / "code-standards"
    assert run.logs["git"] == [
        f"git args=clone --quiet --depth 1 --single-branch --branch main"
        f" https://github.com/AlobarQuest/{name}.git {destinations[name]}"
        f" count=1 key0=http.https://github.com/.extraheader value0={header} lane_token= socket="
        for name in CLONES
    ]


def test_the_cloned_repositories_are_the_programs() -> None:
    """The launcher clones exactly what `bump-driver repositories` puts in the read mint."""
    from bump_driver.cli import CLONED

    assert sorted(f"AlobarQuest/{name}" for name in CLONES) == sorted(CLONED)


def test_a_failed_clone_fails_the_pass_with_gits_message(tmp_path: Path) -> None:
    run = _run(tmp_path, GIT_REFUSES="project-standards")
    assert run.code == 1, run.output
    assert "could not clone AlobarQuest/project-standards" in run.output
    assert "fatal: repository not found" in run.output
    assert run.passes() == []


def test_factory_is_the_images_interpreter_on_the_clone(tmp_path: Path) -> None:
    """The shim is the `factory` on the driver's PATH, and it runs the intent-packages clone's
    code under the image's interpreter, with the claim's path after the clone's `src`."""
    run = _run(tmp_path, FAKE_RUN_FACTORY="1")
    assert run.code == 0, run.output
    projects = run.home / "Projects"
    env = run.env()
    assert env["FACTORY_PYTHONPATH"] == (
        f"{projects}/project-standards/src:{projects}/security-standards/src"
    )
    root = tmp_path / "tree"
    assert env["PATH"].split(":")[:2] == [str(run.home / ".local" / "bin"), f"{root}/.venv/bin"]
    assert run.lines("factory") == [
        "factory argv=decompose --no-execute",
        f"factory pythonpath={projects}/intent-packages/src:{env['FACTORY_PYTHONPATH']}",
    ]
    shim = (run.home / ".local" / "bin" / "factory").read_text()
    assert f"exec '{root}/.venv/bin/python' -P -m intent_packages.factory_cli" in shim


def test_code_standards_is_the_images_interpreter_on_the_clone_at_the_path_the_matrix_tries_first(
    tmp_path: Path,
) -> None:
    """`checkers._code_cmd` runs `~/Developer/code-standards/.venv/bin/code-standards` when it is
    a file, else `uv run`, which would reach PyPI. The shim is that file: the image's interpreter,
    whose venv carries `tomlkit`, on the clone's `src`, with the caller's directory (a member's
    mirror) kept off `sys.path`."""
    run = _run(tmp_path)
    assert run.code == 0, run.output
    clone = run.home / "Developer" / "code-standards"
    shim = clone / ".venv" / "bin" / "code-standards"
    root = tmp_path / "tree"
    assert f"exec '{root}/.venv/bin/python' -P -m code_standards.cli" in shim.read_text()
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "code_standards").mkdir()  # a shadowing package in the caller's directory
    result = subprocess.run(
        [str(shim), "check", "--repo", str(mirror)],
        cwd=mirror,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/claim/path"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"code-standards argv=check --repo {mirror} tomlkit=tomlkit",
        f"code-standards pythonpath={clone}/src:/claim/path",
        f"code-standards path0={clone}/src",
    ]


# ---- the pass --------------------------------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["acting", "dry-run"])
def test_the_driver_runs_with_the_launchers_arguments_and_its_report_is_kept(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    [driver] = run.passes()
    path = (tmp_path / "lane.log.facts-path").read_text().strip()
    assert driver == " ".join(["driver args=", *args, "--conformance-facts", path]).replace(
        "= ", "=", 1
    )
    assert "a bump-driver report line" in run.result.stdout
    # The file is under the pass's `HOME`, which `lane-run` discards on every exit path.
    assert Path(path).parent == run.home


# ---- the chain walk (ADR-0059 Amendment 13 decision item 4) ------------------------------------


@pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["acting", "dry-run"])
def test_every_pass_walks_the_chain_once_with_the_password_and_the_images_security_standards(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    assert run.code == 0, run.output
    root = tmp_path / "tree"
    assert run.lines("walk") == [
        f"walk args=-m orchestrator.factory_chain_read password={value(CHAIN_READ_UUID)}"
        " host=b82fa1vg5whecgnjreuqb5tk port=5432 name=postgres"
        f" pythonpath={root}/security-standards/src lane_token= socket= github=set"
    ]
    # A verified walk's selection reaches the driver; only its summary reaches the report.
    assert run.facts() == {
        "verified": True,
        "summary": "outcome=verified seq=7 head=abcdef012345 events=7 exit=0",
        "mac_conformance": json.loads(VERIFIED_WALK)["mac_conformance"],
    }
    assert "[chain] outcome=verified seq=7 head=abcdef012345 events=7 (exit 0)" in run.output
    assert "connection details" not in run.output
    assert "package_approved" not in run.output


def test_the_read_password_reaches_the_chain_walk_and_no_other_process(tmp_path: Path) -> None:
    """Never exported, never in an argument: not the derive, the mint, git, the driver or
    `factory`, and nowhere in the output."""
    password = value(CHAIN_READ_UUID)
    run = _run(tmp_path, FAKE_RUN_FACTORY="1", FACTORY_EVENTS_READ_PASSWORD="ambient")
    assert run.code == 0, run.output
    [walk] = run.lines("walk")
    assert f"password={password} " in walk
    others = [line for line in run.logs["lane"] if not line.startswith("walk ")]
    others += run.logs["git"] + run.logs["bws"] + run.logs["ping"]
    assert others and not [line for line in others if password in line or "ambient" in line]
    env = run.env()
    assert "FACTORY_EVENTS_READ_PASSWORD" not in env and "CHAIN_PASSWORD" not in env
    assert not [name for name in env if name.startswith(("FACTORY_EVENTS_", "CHAIN_"))]
    assert password not in run.output
    scans = run.lines("envscan")
    # derive, mint, four clones, the pass: none holds the password in its environment.
    assert len(scans) == 7 and all(line.endswith(" 0") for line in scans), scans


@pytest.mark.parametrize(
    ("walk", "rc", "summary"),
    [
        (
            '{"outcome": "broken", "seq": 4, "head": "abcdef012345", "events": 4,'
            ' "error": "seq 4: hash mismatch"}',
            2,
            "outcome=broken seq=4 head=abcdef012345 events=4 error=seq 4: hash mismatch exit=2",
        ),
        (
            '{"outcome": "failure", "error_type": "OperationalError"}',
            2,
            "outcome=failure error_type=OperationalError exit=2",
        ),
        ("not json", 2, "outcome=unreadable exit=2"),
        ("", 1, "outcome=unreadable exit=1"),
        # A verified record from a walk that exited non-zero is not a verified walk.
        (VERIFIED_WALK, 2, "outcome=verified seq=7 head=abcdef012345 events=7 exit=2"),
    ],
    ids=["broken", "failure", "unreadable", "empty", "verified-but-failed"],
)
def test_a_walk_that_did_not_verify_reaches_the_driver_as_unverified_and_the_pass_goes_on(
    tmp_path: Path, walk: str, rc: int, summary: str
) -> None:
    """The driver holds every bump that reads the facts and alerts; dispatches and merges of bumps
    already decomposed don't wait on the chain, so the pass still runs."""
    run = _run(tmp_path, FAKE_WALK=walk, FAKE_WALK_RC=str(rc))
    assert run.code == 0, run.output
    assert run.facts() == {"verified": False, "summary": summary}
    assert len(run.passes()) == 1


@pytest.mark.parametrize("setting", sorted(CHAIN_SETTINGS))
def test_a_missing_chain_setting_fails_the_pass_before_any_read(
    tmp_path: Path, setting: str
) -> None:
    run = _run(tmp_path, **{setting: None})
    assert run.code == 1, run.output
    assert f"FATAL: {setting} is not set in the lane's container" in run.output
    assert run.lane_reads() == [] and run.passes() == [] and run.lines("walk") == []


@pytest.mark.parametrize(("rc", "expected"), [(0, 0), (1, 1), (2, 2), (3, 3), (127, 1)])
def test_the_programs_code_is_the_passs(tmp_path: Path, rc: int, expected: int) -> None:
    run = _run(tmp_path, FAKE_DRIVER_RC=str(rc))
    assert run.code == expected
    if rc == 127:
        assert "FATAL: bump-driver exited 127" in run.result.stdout


# ---- the container ---------------------------------------------------------------


def test_the_container_asks_gh_nothing_and_prints_no_activation(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.code == 0, run.output
    assert "[activation]" not in run.output
    assert run.logs["gh"] == []


# ---- the container on the secrets broker (ADR-0055 amendment 30) ------------------------------

PING_URL_SECRET = "11111111-2222-4333-8444-555555555555"


def _brokered(tmp_path: Path, socket_path: Path, *args: str, **fake: str | None) -> Run:
    """The container as `lane-run` starts it on the broker: no token and the socket's marker. The
    harness's ping-URL id is empty, so an acting pass's outcome alarm asks for the broad identity;
    the stand-in keeps the real Keychain out of the test."""
    return _run(tmp_path, *args, BWS_ACCESS_TOKEN=None, SDS_SECRETS_SOCKET=str(socket_path), **fake)


@pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["acting", "dry-run"])
def test_container_branch_runs_on_the_socket_without_a_token(
    tmp_path: Path, broker, args: tuple[str, ...]
) -> None:
    socket_path = broker({u: value(u) for u in LANE_SECRETS})
    run = _brokered(tmp_path, socket_path, *args)
    assert run.code == 0, run.output
    # Every lane secret came through the socket: the only `bws` call is an acting pass's outcome
    # alarm reading the Healthchecks key, by the broad stand-in that exists only in this test, and
    # `gh` was never asked.
    assert run.reads() == ([] if args else [(HEALTHCHECKS_KEY_UUID, BROAD_STANDIN)])
    assert "[activation]" not in run.output
    assert run.logs["gh"] == []
    # Neither the token nor the socket's marker reaches the derive, the mint, git or the walk.
    assert run.lines("derive") == [
        f"derive args=repositories lane_token= socket= system={value(SYSTEM_UUID)}"
        " key=orchestrator-system"
    ]
    [mint] = run.lines("mint")
    assert mint.endswith(f" key={value(READ_APP_KEY_UUID)} lane_token= socket=")
    assert len(run.logs["git"]) == len(CLONES)
    assert all(line.endswith(" lane_token= socket=") for line in run.logs["git"])
    [walk] = run.lines("walk")
    assert f"password={value(CHAIN_READ_UUID)} " in walk
    assert walk.endswith(" lane_token= socket= github=set")
    env = run.env()
    assert env["ORCHESTRATOR_API_TOKEN"] == value(SYSTEM_UUID)
    assert env["SDS_BRIDGE_ALERT_TOKEN"] == value(ALERT_UUID)
    assert env["CHANGE_MANAGER_TOKEN"] == value(READ_UUID)
    assert env["CODE_BRAIN_KEY"] == value(CODE_BRAIN_UUID)
    assert env["INFRA_BRAIN_KEY"] == value(INFRA_BRAIN_UUID)
    assert env["BUMP_DRIVER_GITHUB_TOKEN"] == READ_TOKEN
    # Nor the driver.
    assert "BWS_ACCESS_TOKEN" not in env and "SDS_SECRETS_SOCKET" not in env
    assert len(run.passes()) == 1
    for uuid in LANE_SECRETS:
        assert value(uuid) not in run.output
    assert READ_TOKEN not in run.output


def test_with_a_ping_url_named_the_socket_serves_every_read(tmp_path: Path, broker) -> None:
    """With a ping-URL id named, as the compose names one, the outcome alarm reads it
    through the socket too, and `bws` never runs. No broad identity: nothing falls back to
    the by-name lookup."""
    socket_path = broker(
        {PING_URL_SECRET: "https://ping.example/x", **{u: value(u) for u in LANE_SECRETS}}
    )
    run = _brokered(
        tmp_path,
        socket_path,
        BWS_ACCESS_TOKEN_BROAD=None,
        SDS_DEADMAN_PING_URL_UUID=PING_URL_SECRET,
    )
    assert run.code == 0, run.output
    assert run.logs["bws"] == []
    assert len(run.passes()) == 1


@pytest.mark.parametrize("uuid", (*FIVE, CHAIN_READ_UUID))
def test_a_secret_the_socket_refuses_fails_the_pass_before_the_driver(
    tmp_path: Path, broker, uuid: str
) -> None:
    socket_path = broker({u: (None if u == uuid else value(u)) for u in LANE_SECRETS})
    run = _brokered(tmp_path, socket_path)
    assert run.code == 1, run.output
    assert "FATAL: could not read" in run.output
    assert "(the lane's socket or token)" in run.output
    assert run.passes() == [] and run.lines("mint") == []


def test_a_dead_socket_fails_the_pass_and_never_falls_back_to_bws(
    tmp_path: Path, dead_socket: Path
) -> None:
    run = _brokered(tmp_path, dead_socket)
    assert run.code == 1, run.output
    assert "FATAL: could not read the orchestrator system credential" in run.output
    # Only the outcome alarm's Healthchecks key, by the broad stand-in: no lane secret fell back
    # to `bws`.
    assert run.reads() == [(HEALTHCHECKS_KEY_UUID, BROAD_STANDIN)]
    assert run.logs["gh"] == []
    assert "[activation]" not in run.output
    assert run.lines("derive") == [] and run.lines("mint") == [] and run.passes() == []


def test_outside_the_container_it_refuses_and_reads_and_runs_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path, "--dry-run", BWS_ACCESS_TOKEN=None, BUMP_DRIVER_STATE_DIR=None)
    assert run.code == 1, run.output
    assert (
        "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN: the bump-driver lane runs only in "
        "its container, through lane-run"
    ) in run.output
    assert run.logs["bws"] == [] and run.logs["ping"] == [] and run.logs["gh"] == []
    assert run.passes() == [] and run.lines("derive") == [] and run.lines("mint") == []
    assert run.logs["git"] == []


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "bump-driver")
    # A broker lane: it mounts its socket and the allowlist names it.
    allowlist = load_allowlist(Path(deploy.BROKER_ALLOWLIST))
    assert deploy.broker_lane(service, "bump-driver", allowlist)
    assert deploy.add_refusals(service, "bump-driver", allowlist) == []
    assert deploy.compose_broker_refusals(COMPOSE.read_text(), "bump-driver") == []
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["bump-driver"],
        # No `BWS_ACCESS_TOKEN`: the lane reads through its secrets-broker socket.
        "environment": {
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            "BUMP_DRIVER_STATE_DIR": "/var/lib/sds/bump-driver",
            # The BWS secret id of `bump-driver-ping-url`: not a secret, not the ping URL.
            "SDS_DEADMAN_PING_URL_UUID": "430691e2-4a36-4261-a28f-b50900e064b7",
            # The work carrier's three chain settings: the chain's container inside `coolify`.
            **CHAIN_SETTINGS,
        },
        "volumes": [
            "sds-lanes-bump-driver-state:/var/lib/sds/bump-driver",
            "/data/sds/broker/bump-driver:/run/sds-broker:ro",
        ],
        # The chain walk connects to a separate Coolify database resource (workspace invariant).
        "networks": ["coolify"],
        "restart": "unless-stopped",
    }
    document = yaml.safe_load(COMPOSE.read_text())
    assert set(document) == {"services", "volumes", "networks"}
    assert document["volumes"] == {"sds-lanes-bump-driver-state": None}
    assert document["networks"] == {"coolify": {"external": True}}
    carrier = deploy.lane_service(
        Path("lanes/work-carrier/compose.yaml").read_text(), "work-carrier"
    )
    assert {k: carrier["environment"][k] for k in CHAIN_SETTINGS} == CHAIN_SETTINGS
    assert set(CHAIN_SETTINGS) == set(lane_run.CHAIN_SETTINGS)
    assert deploy._state_volume(service, "bump-driver") == "sds-lanes-bump-driver-state"
    assert "bump-driver" in deploy.STATEFUL_LANES
    assert deploy.LANE_CHECKS["bump-driver"] == "sds-bump-driver"


def test_every_compose_variable_lane_run_must_pass_is_in_its_list() -> None:
    environment = deploy._environment(deploy.lane_service(COMPOSE.read_text(), "bump-driver"))
    passed = set(lane_run.lane_environment(environment, "/tmp/home"))
    assert set(environment) - passed == {"LANE_SCHEDULE_ENABLED"}


def test_the_crontab_runs_a_flagless_pass_hourly_at_51() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["51 * * * * lane-run bump-driver"]


def test_no_two_lanes_start_a_pass_in_the_same_minute() -> None:
    minutes = lane_start_minutes()
    assert minutes["bump-driver"] == {51}
    # 50 is the rotation decomposer's.
    assert 50 in minutes["rotation-decomposer"]
    for lane, taken in minutes.items():
        if lane != "bump-driver":
            assert 51 not in taken, lane


def test_the_repositorys_manifest_lists_the_lanes_secrets() -> None:
    """The lane's `--token` proof reads what the manifest lists for it: its seven secrets, the
    chain's read password included, and its ping-URL secret. The launcher names each secret it
    reads, except the ping-URL secret, whose id reaches it from the compose through `lane-run`."""
    manifest = Path(".bws-secrets.toml").read_text()
    ping_url_secret = "430691e2-4a36-4261-a28f-b50900e064b7"
    secrets = deploy.lane_secrets(manifest, "bump-driver")
    assert sorted(secrets) == sorted([*LANE_SECRETS, ping_url_secret])
    launcher = LAUNCHER.read_text()
    for uuid in secrets:
        assert uuid in launcher or uuid == ping_url_secret, uuid
