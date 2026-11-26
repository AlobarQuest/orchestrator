"""`scripts/run-rotation-proposer.sh` in the lane's container, and the lane's files.

The real launcher runs under `env -i` from a throwaway tree with no `.git` (`/app` has none), with
what `lane-run` passes it in the container: the lane's token as `BWS_ACCESS_TOKEN`, or the secrets
broker's socket as `SDS_SECRETS_SOCKET` (a stand-in broker, `conftest.py`'s), the state directory,
infraops' two directory settings, `PATH` and `HOME`. `bws`, `git` and `curl` are stubs on
PATH. In the venv, `rotation-proposer` and `sds-github-mint` are stubs that log what reached them,
and `python` is the real interpreter (`sds-bws.sh` parses BWS's JSON with it). Nothing reads BWS,
GitHub, change-manager, the orchestrator, alert-hub or Healthchecks. Outside the container it
refuses.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import deploy_orchestrator as deploy
from scripts import lane_run
from scripts.secrets_broker import load_allowlist
from tests.scripts.test_run_unit_verifier import lane_start_minutes

LAUNCHER = Path("scripts/run-rotation-proposer.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh", "sds-git.sh", "sds-cred-registries.sh")
COMPOSE = Path("lanes/rotation-proposer/compose.yaml")
CRONTAB = Path("lanes/rotation-proposer/crontab")

PROPOSE_UUID = "acccb346-4baa-43ec-a1d4-b4a400c048ee"
OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
ALERT_UUID = "b415d03b-f92d-46b1-8a9d-b4f10103df49"
PUSH_APP_KEY_UUID = "b8f547b9-67d7-4417-adc0-b4fd0126b0f0"
PING_URL_KEEPER = "506a5436-4643-4040-8a8b-b50601312be2"
HEALTHCHECKS_KEY_UUID = "51509d5e-64f5-4781-ae82-b4ed00b644e1"
SUBMIT_ONLY = (PROPOSE_UUID, OBSERVER_UUID, ALERT_UUID, PUSH_APP_KEY_UUID)

LANE_TOKEN = "a-lane-token"
PUSH_TOKEN = "ghs-minted-push-token"
BROAD_STANDIN = "broad-stand-in"
NARROW_STANDIN = "narrow-stand-in"
FENCE = ("INFRADRIFT_CONFIG_DIR", "SECURITY_DRIFT_STATE_DIR")
# The four credential registries, in the list's order (ADR-0059 decision 5).
REGISTRIES = ("infraops-mcp-server", "change-manager", "factory-runner", "orchestrator")
CLONED = ("intent-packages", *REGISTRIES)


def value(uuid: str) -> str:
    return f"value-of-{uuid}"


def header(token: str) -> str:
    return "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()


# The `bws` stub logs its argv and the token it was given; a UUID in `$BWS_REFUSES` fails.
_BWS = """#!/bin/sh
echo "$3 token=${BWS_ACCESS_TOKEN:-} argv=$*" >> "$BWS_LOG"
case " ${BWS_REFUSES:-} " in *" $3 "*) exit 1 ;; esac
printf '{"value": "value-of-%s"}' "$3"
"""
_CURL = '#!/bin/sh\necho "curl $*" >> "$PING_LOG"\nexit 1\n'
# git: outside a repository `rev-parse` fails, as in `/app`; a clone logs its argv and the
# credential environment, then makes the target a checkout with a registry file, unless the target
# is named in `$FAKE_NO_REGISTRY`.
_GIT = """#!/bin/bash
case " $* " in *" rev-parse "*) echo "fatal: not a git repository" >&2; exit 128 ;; esac
echo "git $* | global=${GIT_CONFIG_GLOBAL:-} nosystem=${GIT_CONFIG_NOSYSTEM:-} \\
prompt=${GIT_TERMINAL_PROMPT:-} count=${GIT_CONFIG_COUNT:-} value0=${GIT_CONFIG_VALUE_0:-} \\
lane_token=${BWS_ACCESS_TOKEN:+set} socket=${SDS_SECRETS_SOCKET:+set}" >> "$GIT_LOG"
target="${@: -1}"
case " ${FAKE_CLONE_FAILS:-} " in *" $(basename "$target") "*)
  echo "fatal: repository not found" >&2; exit 128 ;;
esac
if [ -d "$target" ] && [ -n "$(ls -A "$target")" ]; then
  echo "fatal: destination path '$target' already exists and is not an empty directory." >&2
  exit 128
fi
mkdir -p "$target/src"
case " ${FAKE_NO_REGISTRY:-} " in *" $(basename "$target") "*) ;; *)
  echo "[[credential]]" > "$target/.cred-consumers.toml" ;;
esac
"""
# rotation-proposer: logs its argv, every variable it was handed (one per line) and the clones.
_PROPOSER = """#!/bin/bash
{
  echo "proposer args=$*"
  packages="${BUMP_PROPOSER_PACKAGES_CHECKOUT:-/none}"
  infraops="${ROTATION_PROPOSER_INFRAOPS_CHECKOUT:-/none}"
  echo "proposer packages=$([ -d "$packages/src" ] && echo present)"
  echo "proposer infraops=$([ -d "$infraops/src" ] && echo present)"
  list="${INFRADRIFT_CONFIG_DIR:-/none}/cred-consumers.list"
  [ -f "$list" ] && sed 's/^/proposer-list /' "$list"
  env | sort | sed 's/^/proposer-env /'
} >> "$LANE_LOG"
exit "${FAKE_PROPOSER_RC:-0}"
"""
_MINT = """#!/bin/bash
key="$(cat)"
echo "mint args=$* key=$key lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
echo "mint-child socket=${SDS_SECRETS_SOCKET:+set}" >> "$LANE_LOG"
[ -n "${FAKE_PUSH_MINT_RC:-}" ] && exit "$FAKE_PUSH_MINT_RC"
echo "%s"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


class Run:
    def __init__(self, result: subprocess.CompletedProcess[str], logs: dict, clones: Path):
        self.result = result
        self.logs = logs
        self.clones = clones

    @property
    def code(self) -> int:
        return self.result.returncode

    @property
    def output(self) -> str:
        return self.result.stdout + self.result.stderr

    def lane_reads(self) -> list[str]:
        """The UUIDs read with the lane's token, in order."""
        return [line.split()[0] for line in self.logs["bws"] if f"token={LANE_TOKEN} " in line]

    def lines(self, prefix: str) -> list[str]:
        return [line for line in self.logs["lane"] if line.startswith(prefix + " ")]

    def env(self) -> dict[str, str]:
        """The variables `rotation-proposer` was handed."""
        pairs = (line.removeprefix("proposer-env ") for line in self.lines("proposer-env"))
        return dict(pair.split("=", 1) for pair in pairs)

    def clone_lines(self) -> list[str]:
        return [line for line in self.logs["git"] if line.startswith("git clone ")]


def _fence(tmp_path: Path) -> dict[str, str]:
    """The two directories: the list's, which the launcher fills, and the state file's, as the
    cut-over copy leaves it."""
    config = tmp_path / "infra-drift-config"
    state = tmp_path / "infra-drift-state"
    state.mkdir()
    (state / "cred-rotation-state.json").write_text("{}\n")
    state.joinpath("cred-rotation-state.json").chmod(0o600)
    return {"INFRADRIFT_CONFIG_DIR": str(config), "SECURITY_DRIFT_STATE_DIR": str(state)}


def _run(tmp_path: Path, *args: str, **fake: str | None) -> Run:
    """A `None` in `fake` removes that variable from the launcher's environment."""
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    _executable(venv / "rotation-proposer", _PROPOSER)
    _executable(venv / "sds-github-mint", _MINT % PUSH_TOKEN)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("bws", _BWS), ("curl", _CURL), ("git", _GIT)):
        _executable(stubs / name, body)
    home = tmp_path / "home"
    home.mkdir()
    clones = tmp_path / "clones"
    logs = {name: tmp_path / f"{name}.log" for name in ("lane", "bws", "ping", "git")}
    for log in logs.values():
        log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN": LANE_TOKEN,
        "SDS_DEADMAN_PING_URL_UUID": "",
        "ROTATION_PROPOSER_STATE_DIR": str(tmp_path / "state"),
        "ROTATION_PROPOSER_CLONE_ROOT": str(clones),
        **_fence(tmp_path),
        # The dead-man helper's name lookup, with no ping-URL id, asks for the broad identity; the
        # stand-in keeps the real Keychain out of the test. The container has neither the
        # Keychain nor this variable, which `lane-run` never passes.
        "BWS_ACCESS_TOKEN_BROAD": BROAD_STANDIN,
        "LANE_LOG": str(logs["lane"]),
        "BWS_LOG": str(logs["bws"]),
        "PING_LOG": str(logs["ping"]),
        "GIT_LOG": str(logs["git"]),
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
    return Run(result, {name: log.read_text().splitlines() for name, log in logs.items()}, clones)


# ---- the secrets, read with the lane's one token, on `--submit` only ------------------------


def test_a_submitting_pass_reads_its_four_secrets_with_the_lanes_one_token(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "--submit")
    assert run.code == 0, run.output
    assert run.lane_reads() == list(SUBMIT_ONLY)
    # The only other read is the dead-man helper's Healthchecks key, by the broad stand-in that
    # exists only in this test.
    others = [line for line in run.logs["bws"] if f"token={LANE_TOKEN} " not in line]
    assert [line.split()[0] for line in others] == [HEALTHCHECKS_KEY_UUID]
    assert all(f"token={BROAD_STANDIN} " in line for line in others)
    for uuid in SUBMIT_ONLY:
        assert value(uuid) not in run.output
    assert LANE_TOKEN not in run.output


def test_a_container_pass_asks_git_for_no_worktree(tmp_path: Path) -> None:
    """`/app` is no checkout, so no `git rev-parse` of the tree runs and fails."""
    run = _run(tmp_path)
    assert run.code == 0, run.output
    assert "not a git repository" not in run.result.stderr


def test_a_dry_run_reads_no_secret_and_mints_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.code == 0, run.output
    assert run.lane_reads() == []
    assert run.lines("mint") == []
    env = run.env()
    for name in (
        "ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN",
        "ORCHESTRATOR_API_TOKEN",
        "SDS_BRIDGE_ALERT_TOKEN",
    ):
        assert name not in env


@pytest.mark.parametrize("uuid", SUBMIT_ONLY)
def test_an_unreadable_secret_fails_the_pass_after_its_inputs_and_before_the_program(
    tmp_path: Path, uuid: str
) -> None:
    """The inputs come first, so a pass that can't use them reads no secret; a secret it can't
    read then stops it before git gets a credential or the program runs."""
    run = _run(tmp_path, "--submit", BWS_REFUSES=uuid)
    assert run.code == 1, run.output
    assert run.lines("proposer") == []
    assert len(run.clone_lines()) == len(CLONED)
    assert all("count= " in line for line in run.clone_lines())


def test_a_submitting_pass_hands_the_program_its_credentials(tmp_path: Path) -> None:
    env = _run(tmp_path, "--submit").env()
    assert env["ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN"] == value(PROPOSE_UUID)
    assert env["ORCHESTRATOR_API_TOKEN"] == value(OBSERVER_UUID)
    assert env["ORCHESTRATOR_API_CREDENTIAL_KEY_ID"] == "orchestrator-observer"
    assert env["ORCHESTRATOR_API_URL"] == "https://sds.alobar.net"
    assert env["SDS_BRIDGE_ALERT_TOKEN"] == value(ALERT_UUID)
    assert env["ROTATION_PROPOSER_STATE_DIR"] == str(tmp_path / "state")
    assert (tmp_path / "state").is_dir()


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_no_token_reaches_an_argument_a_program_or_git(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    assert run.code == 0, run.output
    assert "BWS_ACCESS_TOKEN" not in run.env()
    assert "SDS_SECRETS_SOCKET" not in run.env()
    assert all("lane_token=set" not in line for line in run.logs["lane"])
    assert all("lane_token=set" not in line for line in run.logs["git"])
    argvs = [
        *(line.split(" argv=", 1)[1] for line in run.logs["bws"]),
        *(line.partition(" | ")[0] for line in run.logs["git"]),
        *(line.split(" key=", 1)[0] for line in run.lines("mint")),
        *(line for line in run.logs["lane"] if line.startswith("proposer args=")),
        *run.logs["ping"],
    ]
    for argv in argvs:
        for token in (LANE_TOKEN, PUSH_TOKEN, value(PUSH_APP_KEY_UUID)):
            assert token not in argv, argv


def test_the_state_directory_must_be_set(tmp_path: Path) -> None:
    run = _run(tmp_path, ROTATION_PROPOSER_STATE_DIR=None)
    assert run.code == 1
    assert "ROTATION_PROPOSER_STATE_DIR is not set" in run.output
    assert run.lines("proposer") == []


# ---- the fence -------------------------------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
@pytest.mark.parametrize("setting", FENCE)
@pytest.mark.parametrize("given", [None, ""], ids=["unset", "empty"])
def test_an_unset_or_empty_directory_setting_exits_2_before_anything(
    tmp_path: Path, args: tuple[str, ...], setting: str, given: str | None
) -> None:
    run = _run(tmp_path, *args, **{setting: given})
    assert run.code == 2, run.output
    assert f"FATAL: {setting} is not set in the lane's container" in run.output
    assert run.lane_reads() == [] and run.lines("mint") == []
    assert run.clone_lines() == [] and run.lines("proposer") == []


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_a_missing_state_file_exits_2_before_anything(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    run = _run(tmp_path, *args, SECURITY_DRIFT_STATE_DIR=str(empty))
    assert run.code == 2, run.output
    assert f"FATAL: no cred-rotation-state.json in SECURITY_DRIFT_STATE_DIR ({empty})" in run.output
    assert run.lane_reads() == [] and run.lines("mint") == []
    assert run.clone_lines() == [] and run.lines("proposer") == []


# ---- the list --------------------------------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_the_list_names_the_four_registries_each_in_its_clone(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    assert run.code == 0, run.output
    assert [line.removeprefix("proposer-list ") for line in run.lines("proposer-list")] == [
        f"{run.clones}/{repository}/.cred-consumers.toml" for repository in REGISTRIES
    ]
    # The list is written where the compose's setting names, which needn't exist beforehand.
    assert run.env()["INFRADRIFT_CONFIG_DIR"] == f"{tmp_path}/infra-drift-config"


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
@pytest.mark.parametrize("repository", REGISTRIES)
def test_a_clone_without_its_registry_file_exits_2_and_reads_no_secret(
    tmp_path: Path, args: tuple[str, ...], repository: str
) -> None:
    """A shorter list would hide every exposure the dropped registry declares (rule 1032)."""
    run = _run(tmp_path, *args, FAKE_NO_REGISTRY=repository)
    assert run.code == 2, run.output
    assert (
        f"FATAL: no readable .cred-consumers.toml at main in AlobarQuest/{repository} "
        f"({run.clones}/{repository}/.cred-consumers.toml)"
    ) in run.output
    assert not (tmp_path / "infra-drift-config" / "cred-consumers.list").exists()
    assert run.lane_reads() == [] and run.lines("mint") == [] and run.lines("proposer") == []


def test_a_list_that_cant_be_written_fails_the_pass_before_its_secrets(tmp_path: Path) -> None:
    blocked = tmp_path / "read-only"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        run = _run(tmp_path, "--submit", INFRADRIFT_CONFIG_DIR=str(blocked / "infra-drift"))
    finally:
        blocked.chmod(0o700)
    assert run.code == 1, run.output
    assert f"FATAL: could not write cred-consumers.list in {blocked}/infra-drift" in run.output
    assert run.lane_reads() == [] and run.lines("mint") == [] and run.lines("proposer") == []


def test_a_list_left_by_an_earlier_pass_is_never_read(tmp_path: Path) -> None:
    """A stale list could name clones this pass didn't make; the pass removes it first."""
    config = tmp_path / "infra-drift-config"
    config.mkdir()
    (config / "cred-consumers.list").write_text("/stale/.cred-consumers.toml\n")
    run = _run(tmp_path, FAKE_NO_REGISTRY="orchestrator")
    assert run.code == 2, run.output
    assert not (config / "cred-consumers.list").exists()


def test_with_a_ping_url_named_the_fence_reads_only_that_secret(tmp_path: Path) -> None:
    """The switch arms before the fence: once the compose names the ping-URL secret, a fenced pass
    reads it (and pings `/fail`), and still none of the pass's own secrets."""
    ping_url_secret = "11111111-2222-4333-8444-555555555555"
    run = _run(
        tmp_path,
        "--submit",
        SDS_DEADMAN_PING_URL_UUID=ping_url_secret,
        BWS_ACCESS_TOKEN_BROAD=None,
        INFRADRIFT_CONFIG_DIR="",
    )
    assert run.code == 2, run.output
    assert run.lane_reads() == [ping_url_secret]
    assert run.clone_lines() == [] and run.lines("proposer") == []


def test_the_program_gets_the_directories_the_compose_named(tmp_path: Path) -> None:
    env = _run(tmp_path, "--submit").env()
    assert env["INFRADRIFT_CONFIG_DIR"] == f"{tmp_path}/infra-drift-config"
    assert env["SECURITY_DRIFT_STATE_DIR"] == f"{tmp_path}/infra-drift-state"


# ---- no chain --------------------------------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_no_chain_variable_reaches_the_program_whatever_the_environment_held(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(
        tmp_path,
        *args,
        FACTORY_EVENTS_APPEND_PASSWORD="ambient",
        FACTORY_EVENTS_DB_HOST="ambient",
        FACTORY_EVENTS_APPEND_DSN="ambient",
    )
    assert run.code == 0, run.output
    assert not any(name.startswith("FACTORY_EVENTS_") for name in run.env())


def test_the_lane_files_name_no_chain_setting_and_join_no_network() -> None:
    assert "FACTORY_EVENTS" not in COMPOSE.read_text()
    document = yaml.safe_load(COMPOSE.read_text())
    assert "networks" not in document
    assert "networks" not in document["services"]["rotation-proposer"]


# ---- the clones and the push token -----------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_every_clone_is_anonymous_shallow_at_main(tmp_path: Path, args: tuple[str, ...]) -> None:
    run = _run(tmp_path, *args)
    lines = run.clone_lines()
    assert len(lines) == len(CLONED)
    for line, repository in zip(lines, CLONED, strict=True):
        argv, _, environment = line.partition(" | ")
        assert argv == (
            "git clone --quiet --depth 1 --single-branch --branch main "
            f"https://github.com/AlobarQuest/{repository}.git {run.clones}/{repository}"
        )
        assert environment.startswith("global=/dev/null nosystem=1 prompt=0 count= value0= ")


def test_the_push_mint_names_intent_packages_and_contents_write(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit")
    assert run.lines("mint") == [
        "mint args=--app-id 5684778 --repository AlobarQuest/intent-packages "
        "--permission metadata=read --permission contents=write --allow-write "
        f"key={value(PUSH_APP_KEY_UUID)} lane_token="
    ]


def test_a_failed_push_mint_fails_the_pass_after_the_clones_and_before_the_program(
    tmp_path: Path,
) -> None:
    """The mint follows the clones, so no clone can carry its token's header."""
    run = _run(tmp_path, "--submit", FAKE_PUSH_MINT_RC="1")
    assert run.code == 1
    assert "sds-github-mint exited 1" in run.output
    assert len(run.clone_lines()) == len(CLONED)
    assert all("value0= " in line for line in run.clone_lines())
    assert run.lines("proposer") == []


def test_a_submitting_pass_hands_git_the_push_token_and_the_apps_identity(
    tmp_path: Path,
) -> None:
    env = _run(tmp_path, "--submit").env()
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_0"] == header(PUSH_TOKEN)
    assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == (
        "user.name",
        "alobar-sds-lanes[bot]",
    )
    assert (env["GIT_CONFIG_KEY_2"], env["GIT_CONFIG_VALUE_2"]) == (
        "user.email",
        "354576843+alobar-sds-lanes[bot]@users.noreply.github.com",
    )
    assert env["GIT_CONFIG_COUNT"] == "3"
    # Only git's extraheader carries the push token: no variable a program reads holds it.
    assert [name for name, held in env.items() if PUSH_TOKEN in held] == []
    assert [name for name, held in env.items() if header(PUSH_TOKEN) == held] == [
        "GIT_CONFIG_VALUE_0"
    ]


def test_a_dry_run_gives_git_no_credential_and_no_identity(tmp_path: Path) -> None:
    env = _run(tmp_path).env()
    assert not any(name.startswith(("GIT_CONFIG_KEY", "GIT_CONFIG_VALUE")) for name in env)
    assert "GIT_CONFIG_COUNT" not in env
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"


@pytest.mark.parametrize("repository", CLONED)
def test_a_failed_clone_fails_the_pass_and_names_gits_message(
    tmp_path: Path, repository: str
) -> None:
    run = _run(tmp_path, "--submit", FAKE_CLONE_FAILS=repository)
    assert run.code == 1
    assert f"FATAL: could not clone https://github.com/AlobarQuest/{repository}.git" in run.output
    assert "fatal: repository not found" in run.output
    assert not (tmp_path / "infra-drift-config" / "cred-consumers.list").exists()
    assert run.lane_reads() == [] and run.lines("proposer") == []


def test_the_program_reads_the_pass_clones_and_they_are_discarded_after(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit")
    env = run.env()
    assert env["BUMP_PROPOSER_PACKAGES_CHECKOUT"] == f"{run.clones}/intent-packages"
    assert env["ROTATION_PROPOSER_INFRAOPS_CHECKOUT"] == f"{run.clones}/infraops-mcp-server"
    assert "proposer packages=present" in run.logs["lane"]
    assert "proposer infraops=present" in run.logs["lane"]
    assert list(run.clones.iterdir()) == []
    assert list((tmp_path / "infra-drift-config").iterdir()) == []


def test_clones_left_by_an_earlier_pass_are_replaced(tmp_path: Path) -> None:
    for repository in (*CLONED, "cache"):
        stale = tmp_path / "clones" / repository
        stale.mkdir(parents=True)
        (stale / "left-behind").write_text("x")
    run = _run(tmp_path)
    assert run.code == 0, run.output


def test_the_registry_mirrors_live_in_the_pass_directory(tmp_path: Path) -> None:
    env = _run(tmp_path).env()
    assert env["XDG_CACHE_HOME"] == f"{tmp_path}/clones/cache"


# ---- the lifecycle's interpreter -------------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_the_lifecycle_runs_under_the_images_interpreter(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    env = run.env()
    root = tmp_path / "tree"
    assert env["BUMP_PROPOSER_PACKAGES_PYTHON"] == f"{root}/.venv/bin/python"
    assert env["PYTHONPATH"] == f"{run.clones}/intent-packages/src"
    assert env["SECURITY_STANDARDS_DIR"] == f"{root}/security-standards"


def test_the_program_runs_with_the_launchers_arguments(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit")
    assert [line for line in run.logs["lane"] if line.startswith("proposer args=")] == [
        "proposer args=--submit"
    ]


@pytest.mark.parametrize("rc", [0, 1, 2, 3])
def test_the_programs_code_is_the_passs(tmp_path: Path, rc: int) -> None:
    assert _run(tmp_path, FAKE_PROPOSER_RC=str(rc)).code == rc


# ---- the container only ----------------------------------------------------------------------


def test_the_container_reads_every_secret_with_the_lanes_token_and_logs_no_activation(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "--submit", BWS_ACCESS_TOKEN_SDS=NARROW_STANDIN)
    assert run.code == 0, run.output
    assert "[activation]" not in run.output
    assert not any(f"token={NARROW_STANDIN} " in line for line in run.logs["bws"])


@pytest.mark.parametrize("token", [None, ""], ids=["unset", "empty"])
@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_outside_the_container_it_refuses_and_reads_and_runs_nothing(
    tmp_path: Path, args: tuple[str, ...], token: str | None
) -> None:
    run = _run(tmp_path, *args, BWS_ACCESS_TOKEN=token)
    assert run.code == 1, run.output
    assert (
        "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN: the rotation-proposer lane runs only"
        " in its container, through lane-run" in run.result.stderr
    )
    assert all(lines == [] for lines in run.logs.values()), run.logs


def test_a_socket_variable_naming_no_socket_refuses_and_reads_nothing(tmp_path: Path) -> None:
    """`SDS_SECRETS_SOCKET` alone isn't the container: it must name a socket."""
    not_a_socket = tmp_path / "broker.sock"
    not_a_socket.write_text("")
    run = _run(tmp_path, "--submit", BWS_ACCESS_TOKEN=None, SDS_SECRETS_SOCKET=str(not_a_socket))
    assert run.code == 1, run.output
    assert "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN" in run.result.stderr
    assert all(lines == [] for lines in run.logs.values()), run.logs


# ---- the container on the secrets broker (ADR-0055 amendment 30) ------------------------------

# The ping-URL secret a broker pass names, so the dead-man helper reads by id through the socket and
# never looks the check up by name with a broad identity.
BROKER_PING_URL_UUID = "33333333-3333-4333-8333-333333333333"


def _brokered(tmp_path: Path, socket_path: Path, *args: str) -> Run:
    """The container as `lane-run` starts it on the broker: no token, and the socket's marker."""
    return _run(
        tmp_path,
        *args,
        BWS_ACCESS_TOKEN=None,
        BWS_ACCESS_TOKEN_BROAD=None,
        SDS_SECRETS_SOCKET=str(socket_path),
        SDS_DEADMAN_PING_URL_UUID=BROKER_PING_URL_UUID,
    )


def _served() -> dict[str, str | None]:
    return {
        **{uuid: value(uuid) for uuid in SUBMIT_ONLY},
        BROKER_PING_URL_UUID: "https://hc.example/ping/rotation-proposer",
    }


def test_on_the_socket_without_a_token_a_submitting_pass_reads_every_secret_through_it(
    tmp_path: Path, broker
) -> None:
    """The socket serves the ping URL and the four secrets; `bws` never runs, and neither git, the
    mint nor the program gets the lane's token or the socket's marker."""
    run = _brokered(tmp_path, broker(_served()), "--submit")
    assert run.code == 0, run.output
    assert run.logs["bws"] == []
    env = run.env()
    assert env["ROTATION_PROPOSER_CHANGE_MANAGER_TOKEN"] == value(PROPOSE_UUID)
    assert env["ORCHESTRATOR_API_TOKEN"] == value(OBSERVER_UUID)
    assert env["SDS_BRIDGE_ALERT_TOKEN"] == value(ALERT_UUID)
    assert "BWS_ACCESS_TOKEN" not in env and "SDS_SECRETS_SOCKET" not in env
    assert run.lines("mint")[0].endswith(f"key={value(PUSH_APP_KEY_UUID)} lane_token=")
    assert run.lines("mint-child") == ["mint-child socket="]
    assert len(run.clone_lines()) == len(CLONED)
    assert all(line.endswith("lane_token= socket=") for line in run.logs["git"]), run.logs["git"]
    for uuid in SUBMIT_ONLY:
        assert value(uuid) not in run.output


def test_on_the_socket_a_dry_run_reads_no_secret_and_mints_nothing(tmp_path: Path, broker) -> None:
    served = {BROKER_PING_URL_UUID: "https://hc.example/ping/rotation-proposer"}
    run = _brokered(tmp_path, broker(served))
    assert run.code == 0, run.output
    assert run.logs["bws"] == []
    assert run.lines("mint") == []
    # The program ran, on no secret.
    assert "proposer args=" in run.logs["lane"]
    assert "SDS_SECRETS_SOCKET" not in run.env()


@pytest.mark.parametrize(
    ("refused", "reason"),
    [
        (PROPOSE_UUID, "bws_failed"),
        (OBSERVER_UUID, "not_allowed"),
        (ALERT_UUID, "not_allowed"),
        (PUSH_APP_KEY_UUID, "not_allowed"),
    ],
    ids=[
        "propose-read-failed",
        "observer-not-on-the-allowlist",
        "sender-not-on-the-allowlist",
        "push-key-not-on-the-allowlist",
    ],
)
def test_a_secret_the_socket_refuses_fails_the_pass_before_the_program(
    tmp_path: Path, broker, refused: str, reason: str
) -> None:
    served = _served()
    if reason == "bws_failed":
        served[refused] = None
    else:
        del served[refused]
    run = _brokered(tmp_path, broker(served), "--submit")
    assert run.code == 1, run.output
    assert f"the secrets broker gave no value for {refused}: {reason}" in run.result.stderr
    assert run.logs["bws"] == []
    assert run.lines("proposer") == []


def test_a_dead_socket_fails_the_pass_and_never_falls_back_to_bws(
    tmp_path: Path, dead_socket: Path
) -> None:
    run = _brokered(tmp_path, dead_socket, "--submit")
    assert run.code == 1, run.output
    assert f"the secrets broker gave no value for {PROPOSE_UUID}: no_reply" in run.result.stderr
    assert "FATAL: could not read the propose-scoped change-manager credential" in run.result.stderr
    assert run.logs["bws"] == []
    assert run.lines("proposer") == [] and run.lines("mint") == []


SWITCH = "ROTATION_PROPOSER_REPORT_NOT_HANDED_OVER"


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
@pytest.mark.parametrize("inherited", [None, "", "0", "1"], ids=["unset", "empty", "off", "on"])
def test_the_container_always_turns_on_the_findings_for_credentials_not_handed_over(
    tmp_path: Path, args: tuple[str, ...], inherited: str | None
) -> None:
    """ADR-0059 Amendment 12 decision item 4: whatever the compose or `lane-run` passed."""
    run = _run(tmp_path, *args, **{SWITCH: inherited})
    assert run.code == 0, run.output
    assert run.env()[SWITCH] == "1"


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "rotation-proposer")
    # A broker lane: it mounts its socket and the allowlist names it.
    allowlist = load_allowlist(Path(deploy.BROKER_ALLOWLIST))
    assert deploy.broker_lane(service, "rotation-proposer", allowlist)
    assert deploy.add_refusals(service, "rotation-proposer", allowlist) == []
    assert deploy.compose_broker_refusals(COMPOSE.read_text(), "rotation-proposer") == []
    assert allowlist["rotation-proposer"] == {*SUBMIT_ONLY, PING_URL_KEEPER}
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["rotation-proposer"],
        # No `BWS_ACCESS_TOKEN`: the lane reads through its secrets-broker socket.
        "environment": {
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            "ROTATION_PROPOSER_STATE_DIR": "/var/lib/sds/rotation-proposer",
            # The BWS secret id of `rotation-proposer-ping-url`: not a secret, not the ping URL.
            "SDS_DEADMAN_PING_URL_UUID": PING_URL_KEEPER,
            # The list's directory, which the launcher fills each pass beside its clones, and the
            # state file's, on the lane's volume (ADR-0059 Amendment 12 decision 1).
            "INFRADRIFT_CONFIG_DIR": "/var/tmp/sds-rotation-proposer/infra-drift",
            "SECURITY_DRIFT_STATE_DIR": "/var/lib/sds/rotation-proposer/security-drift",
        },
        "volumes": [
            "sds-lanes-rotation-proposer-state:/var/lib/sds/rotation-proposer",
            "/data/sds/broker/rotation-proposer:/run/sds-broker:ro",
        ],
        "restart": "unless-stopped",
    }
    document = yaml.safe_load(COMPOSE.read_text())
    assert set(document) == {"services", "volumes"}
    assert document["volumes"] == {"sds-lanes-rotation-proposer-state": None}
    assert "rotation-proposer" in deploy.STATEFUL_LANES
    assert deploy.LANE_CHECKS["rotation-proposer"] == "sds-rotation-proposer"


def test_every_compose_variable_lane_run_must_pass_is_in_its_list() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "rotation-proposer")
    environment = deploy._environment(service)
    passed = set(lane_run.lane_environment(environment, "/tmp/home"))
    assert set(environment) - passed == {"LANE_SCHEDULE_ENABLED"}
    listed = (*lane_run.COPIED, *lane_run.STATE_VARIABLES, *lane_run.INFRAOPS_SETTINGS)
    assert "ROTATION_PROPOSER_CLONE_ROOT" not in listed


def test_the_crontab_runs_a_submitting_pass_daily_at_06_31() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["31 6 * * * lane-run rotation-proposer --submit"]


def test_no_two_lanes_start_a_pass_in_the_same_minute() -> None:
    minutes = lane_start_minutes()
    assert minutes["rotation-proposer"] == {31}
    for lane, taken in minutes.items():
        if lane != "rotation-proposer":
            assert 31 not in taken, lane


def test_the_repositorys_manifest_lists_the_lanes_secrets() -> None:
    """The lane's `--token` proof reads what the manifest lists for it: its four secrets and its
    ping-URL secret. The launcher names each secret it reads, except the ping-URL secret, whose id
    reaches it from the compose through `lane-run`."""
    manifest = Path(".bws-secrets.toml").read_text()
    ping_url_secret = PING_URL_KEEPER
    secrets = deploy.lane_secrets(manifest, "rotation-proposer")
    assert sorted(secrets) == sorted([*SUBMIT_ONLY, ping_url_secret])
    launcher = LAUNCHER.read_text()
    for uuid in secrets:
        assert uuid in launcher or uuid == ping_url_secret, uuid
