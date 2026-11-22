"""`scripts/run-bump-proposer.sh` in the lane's container, and the lane's files.

The real launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it in the
container: the lane's token as `BWS_ACCESS_TOKEN`, the state directory, the chain's three settings,
`PATH` and `HOME`. `bws`, `git`, `gh`, `uv` and `curl` are stubs on PATH. In the venv,
`bump-proposer` and `sds-github-mint` are stubs that log what reached them, and `python` is the real
interpreter (`sds-bws.sh` parses BWS's JSON with it). Nothing reads BWS, the Keychain, GitHub,
change-manager, the orchestrator, the chain database or Healthchecks.
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
from tests.scripts.test_run_unit_verifier import lane_start_minutes

LAUNCHER = Path("scripts/run-bump-proposer.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh", "sds-git.sh")
COMPOSE = Path("lanes/bump-proposer/compose.yaml")
CRONTAB = Path("lanes/bump-proposer/crontab")

READ_UUID = "314f276d-55ca-4ddc-a24d-b4a3013508cd"
OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
ALERT_UUID = "b415d03b-f92d-46b1-8a9d-b4f10103df49"
PROPOSE_UUID = "acccb346-4baa-43ec-a1d4-b4a400c048ee"
APPEND_UUID = "2ce0dbc7-eeac-4bf0-8204-b50200c97516"
READ_APP_KEY_UUID = "314a966c-5654-4e3c-8358-b4fd01268775"
PUSH_APP_KEY_UUID = "b8f547b9-67d7-4417-adc0-b4fd0126b0f0"
EVERY_PASS = (READ_UUID, OBSERVER_UUID, ALERT_UUID)
SUBMIT_ONLY = (PROPOSE_UUID, APPEND_UUID)
LANE_SECRETS = (*EVERY_PASS, *SUBMIT_ONLY, READ_APP_KEY_UUID, PUSH_APP_KEY_UUID)

LANE_TOKEN = "a-lane-token"
READ_TOKEN = "ghs-minted-read-token"
PUSH_TOKEN = "ghs-minted-push-token"
BROAD_STANDIN = "broad-stand-in"


def value(uuid: str) -> str:
    return f"value-of-{uuid}"


def header(token: str) -> str:
    return "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()


CHAIN = {
    "FACTORY_EVENTS_DB_HOST": "b82fa1vg5whecgnjreuqb5tk",
    "FACTORY_EVENTS_DB_PORT": "5432",
    "FACTORY_EVENTS_DB_NAME": "postgres",
}

PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
PING_URL = "https://hc.example/ping/bump-proposer"
# The `bws` stub logs the UUID and the token it was given; a UUID in `$BWS_REFUSES` fails; the
# ping-URL secret holds `PING_URL`.
_BWS = f"""#!/bin/sh
echo "$3 token=${{BWS_ACCESS_TOKEN:-}}" >> "$BWS_LOG"
case " ${{BWS_REFUSES:-}} " in *" $3 "*) exit 1 ;; esac
[ "$3" = "{PING_URL_UUID}" ] && {{ echo '{{"value": "{PING_URL}"}}'; exit 0; }}
printf '{{"value": "value-of-%s"}}' "$3"
"""
_GH = '#!/bin/sh\necho "gh $*" >> "$GH_LOG"\necho gh-token\n'
# A ping's URL arrives on stdin as curl's config, and its body is the file `--data-binary` names;
# anything else fails.
_CURL = """#!/bin/sh
config=0
for argument in "$@"; do [ "$argument" = "-" ] && config=1; done
[ "$config" -eq 1 ] || exit 1
url="$(sed -n 's/^url = "\\(.*\\)"$/\\1/p')"
body=""
previous=""
for argument in "$@"; do
  [ "$previous" = "--data-binary" ] && body="$(cat "${argument#@}")"
  previous="$argument"
done
printf '%s\\t%s\\n' "$url" "$(printf '%s' "$body" | tr '\\n' '|')" >> "$PING_LOG"
"""
_UV = '#!/bin/sh\necho "uv $*" >> "$UV_LOG"\n'
# git: logs its argv and the credential environment, then makes the clone a checkout.
_GIT = """#!/bin/bash
echo "git $* | global=${GIT_CONFIG_GLOBAL:-} count=${GIT_CONFIG_COUNT:-} \\
value0=${GIT_CONFIG_VALUE_0:-} lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$GIT_LOG"
[ -n "${FAKE_CLONE_FAILS:-}" ] && { echo "fatal: repository not found" >&2; exit 128; }
target="${@: -1}"
if [ -d "$target" ] && [ -n "$(ls -A "$target")" ]; then
  echo "fatal: destination path '$target' already exists and is not an empty directory." >&2
  exit 128
fi
mkdir -p "$target/src/intent_packages" "$target/packages"
"""
# bump-proposer: `repositories` prints the lane's list; a pass logs every variable it was handed,
# one per line, so a test can read any of them, and the clone it sees.
_PROPOSER = """#!/bin/bash
if [ "$1" = repositories ]; then
  echo "derive lane_token=${BWS_ACCESS_TOKEN:+set} members=${SDS_MEMBERS_OBSERVER_TOKEN:-}" \\
    >> "$LANE_LOG"
  if [ -n "${FAKE_DERIVE_RC:-}" ]; then
    echo "[UNUSABLE] the member record could not be read" >&2
    exit "$FAKE_DERIVE_RC"
  fi
  printf '%s' "${FAKE_REPOSITORIES-AlobarQuest/crm
AlobarQuest/intent-packages}"
  exit 0
fi
{
  echo "proposer args=$*"
  echo "proposer clone=$([ -d "${BUMP_PROPOSER_PACKAGES_CHECKOUT:-/none}/src" ] && echo present)"
  env | sort | sed 's/^/proposer-env /'
} >> "$LANE_LOG"
echo "bump-proposer found: a subject"
exit "${FAKE_PROPOSER_RC:-0}"
"""
_MINT = """#!/bin/bash
key="$(cat)"
echo "mint args=$* key=$key lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
case " $* " in
  *" --allow-write "*) [ -n "${FAKE_PUSH_MINT_RC:-}" ] && exit "$FAKE_PUSH_MINT_RC"; echo "%s" ;;
  *) [ -n "${FAKE_MINT_RC:-}" ] && exit "$FAKE_MINT_RC"; echo "%s" ;;
esac
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
        return [
            line.split()[0] for line in self.logs["bws"] if line.endswith(f"token={LANE_TOKEN}")
        ]

    def lines(self, prefix: str) -> list[str]:
        return [line for line in self.logs["lane"] if line.startswith(prefix + " ")]

    def env(self) -> dict[str, str]:
        """The variables `bump-proposer` was handed (a pass, not `repositories`)."""
        pairs = (line.removeprefix("proposer-env ") for line in self.lines("proposer-env"))
        return dict(pair.split("=", 1) for pair in pairs)

    def mints(self) -> list[str]:
        return self.lines("mint")


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
    _executable(venv / "bump-proposer", _PROPOSER)
    _executable(venv / "sds-github-mint", _MINT % (PUSH_TOKEN, READ_TOKEN))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("bws", _BWS), ("gh", _GH), ("curl", _CURL), ("git", _GIT), ("uv", _UV)):
        _executable(stubs / name, body)
    home = tmp_path / "home"
    home.mkdir()
    clones = tmp_path / "clones"
    logs = {name: tmp_path / f"{name}.log" for name in ("lane", "bws", "gh", "ping", "git", "uv")}
    for log in logs.values():
        log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN": LANE_TOKEN,
        "SDS_DEADMAN_PING_URL_UUID": "",
        "BUMP_PROPOSER_STATE_DIR": str(tmp_path / "state"),
        "BUMP_PROPOSER_CLONE_ROOT": str(clones),
        # The dead-man helper's name lookup, on a `--submit` pass with no ping-URL id, asks for the
        # broad identity; the stand-in keeps the real Keychain out of the test.
        "BWS_ACCESS_TOKEN_BROAD": BROAD_STANDIN,
        **CHAIN,
        "LANE_LOG": str(logs["lane"]),
        "BWS_LOG": str(logs["bws"]),
        "GH_LOG": str(logs["gh"]),
        "PING_LOG": str(logs["ping"]),
        "GIT_LOG": str(logs["git"]),
        "UV_LOG": str(logs["uv"]),
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


# ---- the secrets, read with the lane's one token ---------------------------------------------


def test_a_submitting_pass_reads_all_seven_secrets_with_the_lanes_one_token(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "--submit")
    assert run.code == 0, run.output
    assert run.lane_reads() == [*EVERY_PASS, *SUBMIT_ONLY, READ_APP_KEY_UUID, PUSH_APP_KEY_UUID]
    # The only other read is the dead-man helper's Healthchecks key, by the broad stand-in.
    assert all(
        line.endswith((f"token={LANE_TOKEN}", f"token={BROAD_STANDIN}")) for line in run.logs["bws"]
    )
    assert run.logs["gh"] == []
    for uuid in LANE_SECRETS:
        assert value(uuid) not in run.output
    assert LANE_TOKEN not in run.output


def test_a_dry_run_reads_only_the_every_pass_secrets_and_pings_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.code == 0, run.output
    assert run.lane_reads() == [*EVERY_PASS, READ_APP_KEY_UUID]
    assert all(line.endswith(f"token={LANE_TOKEN}") for line in run.logs["bws"])
    assert run.logs["ping"] == [] and run.logs["gh"] == []


@pytest.mark.parametrize(
    ("uuid", "args"),
    [
        (READ_UUID, ()),
        (OBSERVER_UUID, ()),
        (ALERT_UUID, ()),
        (READ_APP_KEY_UUID, ()),
        (PROPOSE_UUID, ("--submit",)),
        (APPEND_UUID, ("--submit",)),
        (PUSH_APP_KEY_UUID, ("--submit",)),
    ],
)
def test_an_unreadable_secret_fails_the_pass_before_the_program(
    tmp_path: Path, uuid: str, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args, BWS_REFUSES=uuid)
    assert run.code == 1, run.output
    assert run.lines("proposer") == []
    assert run.logs["git"] == []


def test_a_submitting_pass_hands_the_program_its_credentials(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit")
    env = run.env()
    assert env["BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN"] == value(READ_UUID)
    assert env["BUMP_PROPOSER_CHANGE_MANAGER_TOKEN"] == value(PROPOSE_UUID)
    assert env["SDS_MEMBERS_OBSERVER_TOKEN"] == value(OBSERVER_UUID)
    assert env["ORCHESTRATOR_API_TOKEN"] == value(OBSERVER_UUID)
    assert env["ORCHESTRATOR_API_CREDENTIAL_KEY_ID"] == "orchestrator-observer"
    assert env["ORCHESTRATOR_API_URL"] == "https://sds.alobar.net"
    assert env["SDS_BRIDGE_ALERT_TOKEN"] == value(ALERT_UUID)
    assert env["BUMP_PROPOSER_GITHUB_TOKEN"] == READ_TOKEN
    assert env["BUMP_PROPOSER_STATE_DIR"] == str(tmp_path / "state")
    assert (tmp_path / "state").is_dir()


def test_a_dry_run_holds_no_credential_that_writes(tmp_path: Path) -> None:
    run = _run(tmp_path)
    env = run.env()
    assert env["BUMP_PROPOSER_CHANGE_MANAGER_READ_TOKEN"] == value(READ_UUID)
    assert env["SDS_MEMBERS_OBSERVER_TOKEN"] == value(OBSERVER_UUID)
    assert env["BUMP_PROPOSER_GITHUB_TOKEN"] == READ_TOKEN
    for name in ("BUMP_PROPOSER_CHANGE_MANAGER_TOKEN", "ORCHESTRATOR_API_TOKEN"):
        assert name not in env
    assert all("--allow-write" not in line for line in run.mints())
    assert all(PUSH_TOKEN not in line for line in run.logs["lane"])


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_no_program_and_no_git_gets_the_lanes_token(tmp_path: Path, args: tuple[str, ...]) -> None:
    run = _run(tmp_path, *args)
    assert run.code == 0, run.output
    assert "BWS_ACCESS_TOKEN" not in run.env()
    assert all("lane_token=set" not in line for line in run.logs["lane"])
    assert all("lane_token=set" not in line for line in run.logs["git"])


def test_the_state_directory_must_be_set(tmp_path: Path) -> None:
    run = _run(tmp_path, BUMP_PROPOSER_STATE_DIR=None)
    assert run.code == 1
    assert "BUMP_PROPOSER_STATE_DIR is not set" in run.output
    assert run.lines("proposer") == []


# ---- the chain -------------------------------------------------------------------------------


def test_a_submitting_pass_hands_the_append_password_and_settings_to_the_program(
    tmp_path: Path,
) -> None:
    env = _run(tmp_path, "--submit").env()
    assert env["FACTORY_EVENTS_APPEND_PASSWORD"] == value(APPEND_UUID)
    for name, setting in CHAIN.items():
        assert env[name] == setting
    assert "FACTORY_EVENTS_READ_PASSWORD" not in env


def test_a_dry_run_hands_the_program_no_chain_variable(tmp_path: Path) -> None:
    env = _run(tmp_path).env()
    assert not any(name.startswith("FACTORY_EVENTS_") for name in env)


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_an_ambient_chain_variable_never_reaches_the_program(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    ambient = {
        "CHAIN_DB_HOST": "ambient-host",
        "CHAIN_DB_PORT": "6543",
        "CHAIN_DB_NAME": "ambient-db",
        "CHAIN_PASSWORD": "ambient-password",
        "FACTORY_EVENTS_READ_PASSWORD": "ambient-read-password",
    }
    run = _run(tmp_path, *args, **ambient)
    assert run.code == 0, run.output
    env = run.env()
    assert not any(name.startswith("CHAIN_") for name in env)
    assert "FACTORY_EVENTS_READ_PASSWORD" not in env
    assert not any(value.startswith("ambient-") for value in env.values())
    if args:
        assert env["FACTORY_EVENTS_APPEND_PASSWORD"] == value(APPEND_UUID)
        assert env["FACTORY_EVENTS_DB_HOST"] == CHAIN["FACTORY_EVENTS_DB_HOST"]


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
@pytest.mark.parametrize("setting", sorted(CHAIN))
def test_a_missing_chain_setting_fails_the_pass_before_any_read(
    tmp_path: Path, setting: str, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args, **{setting: None})
    assert run.code == 1
    assert f"{setting} is not set in the lane's container" in run.output
    assert run.lane_reads() == []
    assert run.lines("proposer") == []


# ---- the two GitHub tokens and the clone -----------------------------------------------------


def test_the_read_mint_names_the_derived_repositories_and_the_four_read_permissions(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    (derive,) = run.lines("derive")
    assert derive == f"derive lane_token= members={value(OBSERVER_UUID)}"
    (mint,) = run.mints()
    assert mint == (
        "mint args=--app-id 5684758 --repository AlobarQuest/crm "
        "--repository AlobarQuest/intent-packages "
        "--permission metadata=read --permission contents=read "
        "--permission pull_requests=read --permission actions=read "
        f"key={value(READ_APP_KEY_UUID)} lane_token="
    )


def test_the_push_mint_names_intent_packages_and_contents_write_only_on_submit(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "--submit")
    read, push = run.mints()
    assert "--allow-write" not in read
    assert push == (
        "mint args=--app-id 5684778 --repository AlobarQuest/intent-packages "
        "--permission metadata=read --permission contents=write --allow-write "
        f"key={value(PUSH_APP_KEY_UUID)} lane_token="
    )


@pytest.mark.parametrize("rc", ["1", "2", "7"])
def test_an_underivable_list_mints_nothing_and_passes_its_code_through(
    tmp_path: Path, rc: str
) -> None:
    run = _run(tmp_path, FAKE_DERIVE_RC=rc)
    assert run.code == int(rc)
    assert run.mints() == [] and run.logs["git"] == [] and run.lines("proposer") == []
    # The subcommand's reason is in the failure's message, which `/fail` carries.
    assert f"exited {rc} ([UNUSABLE] the member record could not be read)" in run.output


def test_an_empty_list_mints_nothing_and_is_unusable_input(tmp_path: Path) -> None:
    run = _run(tmp_path, FAKE_REPOSITORIES="\n")
    assert run.code == 2
    assert run.mints() == []


@pytest.mark.parametrize(
    ("fake", "args"),
    [({"FAKE_MINT_RC": "1"}, ()), ({"FAKE_PUSH_MINT_RC": "1"}, ("--submit",))],
    ids=["read", "push"],
)
def test_a_failed_mint_fails_the_pass_before_the_clone(
    tmp_path: Path, fake: dict[str, str], args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args, **fake)
    assert run.code == 1
    assert run.logs["git"] == [] and run.lines("proposer") == []


def test_the_clone_uses_the_read_token_in_gits_environment_only(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit")
    (clone,) = run.logs["git"]
    argv, _, environment = clone.partition(" | ")
    assert argv == (
        "git clone --quiet --depth 1 --single-branch --branch main "
        f"https://github.com/AlobarQuest/intent-packages.git {run.clones}/intent-packages"
    )
    assert f"value0={header(READ_TOKEN)}" in environment
    assert "global=/dev/null count=1 " in environment
    for token in (READ_TOKEN, PUSH_TOKEN, LANE_TOKEN):
        assert token not in argv


def test_a_submitting_pass_hands_git_the_push_token_and_the_apps_identity(
    tmp_path: Path,
) -> None:
    env = _run(tmp_path, "--submit").env()
    assert env["GIT_CONFIG_VALUE_0"] == header(PUSH_TOKEN)
    assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == (
        "user.name",
        "alobar-sds-lanes[bot]",
    )
    assert env["GIT_CONFIG_KEY_2"] == "user.email"
    # The bot user's id (`GET /users/alobar-sds-lanes%5Bbot%5D`), neither the App's nor the
    # installation's.
    assert env["GIT_CONFIG_VALUE_2"] == "354576843+alobar-sds-lanes[bot]@users.noreply.github.com"
    assert env["GIT_CONFIG_COUNT"] == "3"
    # Only git's extraheader carries the push token: no variable a program reads holds it.
    assert [name for name, held in env.items() if PUSH_TOKEN in held] == []
    assert [name for name, held in env.items() if header(PUSH_TOKEN) == held] == [
        "GIT_CONFIG_VALUE_0"
    ]


def test_a_dry_run_leaves_git_the_read_token_and_no_identity(tmp_path: Path) -> None:
    env = _run(tmp_path).env()
    assert env["GIT_CONFIG_VALUE_0"] == header(READ_TOKEN)
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert "GIT_CONFIG_KEY_1" not in env


def test_the_program_reads_the_pass_clone_and_it_is_discarded_after(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit")
    env = run.env()
    assert env["BUMP_PROPOSER_PACKAGES_CHECKOUT"] == f"{run.clones}/intent-packages"
    assert run.lines("proposer")[1] == "proposer clone=present"
    assert not (run.clones / "intent-packages").exists()


def test_a_clone_left_by_an_earlier_pass_is_replaced(tmp_path: Path) -> None:
    stale = tmp_path / "clones" / "intent-packages"
    stale.mkdir(parents=True)
    (stale / "left-behind").write_text("x")
    run = _run(tmp_path)
    assert run.code == 0, run.output


def test_a_failed_clone_fails_the_pass_and_names_gits_message(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit", FAKE_CLONE_FAILS="1")
    assert run.code == 1
    assert "fatal: repository not found" in run.output
    assert run.lines("proposer") == []


# ---- the lifecycle's interpreter -------------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_the_lifecycle_runs_under_the_images_interpreter_and_no_venv_is_built(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    env = run.env()
    root = tmp_path / "tree"
    assert env["BUMP_PROPOSER_PACKAGES_PYTHON"] == f"{root}/.venv/bin/python"
    assert env["PYTHONPATH"] == f"{run.clones}/intent-packages/src"
    assert env["SECURITY_STANDARDS_DIR"] == f"{root}/security-standards"
    assert run.logs["uv"] == []


def test_the_program_runs_with_the_launchers_arguments(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit", "--repository", "AlobarQuest/crm")
    assert run.lines("proposer")[0] == "proposer args=--submit --repository AlobarQuest/crm"


@pytest.mark.parametrize("rc", [0, 1, 2, 3])
def test_the_programs_code_is_the_passs(tmp_path: Path, rc: int) -> None:
    assert _run(tmp_path, FAKE_PROPOSER_RC=str(rc)).code == rc


# ---- the container ---------------------------------------------------------------


def test_the_container_prints_no_activation_and_asks_gh_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit")
    assert run.code == 0, run.output
    assert "[activation]" not in run.output
    assert run.logs["gh"] == []


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_outside_the_container_it_refuses_and_reads_and_runs_nothing(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args, BWS_ACCESS_TOKEN=None, BUMP_PROPOSER_STATE_DIR=None)
    assert run.code == 1, run.output
    assert (
        "FATAL: no BWS_ACCESS_TOKEN: the bump-proposer lane runs only in its container, "
        "through lane-run"
    ) in run.output
    assert run.lines("proposer-env") == [] and run.lines("derive") == [] and run.mints() == []
    assert run.logs["gh"] == [] and run.logs["git"] == [] and run.logs["uv"] == []
    assert run.logs["bws"] == [] and run.logs["ping"] == []


# ---- the outcome alarm (ADR-0021 amendment 2) ----------------------------------------------


def _pings(run: Run) -> list[tuple[str, ...]]:
    return [tuple(line.split("\t")) for line in run.logs["ping"]]


def test_an_acting_clean_pass_pings_start_then_success(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit", SDS_DEADMAN_PING_URL_UUID=PING_URL_UUID)
    assert run.code == 0, run.output
    assert _pings(run) == [(f"{PING_URL}/start", ""), (PING_URL, "")]


@pytest.mark.parametrize("rc", [3, 2, 1, 127])
def test_every_other_exit_pings_fail_with_the_report_as_its_body(tmp_path: Path, rc: int) -> None:
    """3 (a finding) above all: this check pages on it, it is no dead-man success."""
    run = _run(
        tmp_path, "--submit", FAKE_PROPOSER_RC=str(rc), SDS_DEADMAN_PING_URL_UUID=PING_URL_UUID
    )
    assert run.code == rc, run.output
    pings = _pings(run)
    assert pings[0] == (f"{PING_URL}/start", "")
    assert pings[1][0] == f"{PING_URL}/fail"
    assert "bump-proposer found: a subject" in pings[1][1]
    assert len(pings) == 2


def test_a_dry_run_pings_nothing_whatever_the_code(tmp_path: Path) -> None:
    run = _run(tmp_path, FAKE_PROPOSER_RC="3", SDS_DEADMAN_PING_URL_UUID=PING_URL_UUID)
    assert run.code == 3, run.output
    assert run.logs["ping"] == []
    assert "reporting a failure" not in run.output


def test_a_failing_acting_pass_prints_the_failure_line(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit", FAKE_PROPOSER_RC="3", SDS_DEADMAN_PING_URL_UUID=PING_URL_UUID)
    assert "exit 3 \u2014 reporting a failure" in run.output


def test_a_missing_credential_pings_fail_with_its_reason(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        "--submit",
        BWS_REFUSES=PROPOSE_UUID,
        SDS_DEADMAN_PING_URL_UUID=PING_URL_UUID,
    )
    assert run.code == 1, run.output
    pings = _pings(run)
    assert pings[0] == (f"{PING_URL}/start", "")
    assert pings[1][0] == f"{PING_URL}/fail"
    assert "FATAL: could not read the propose-scoped change-manager credential" in pings[1][1]
    assert len(pings) == 2 and run.lines("proposer-env") == []


def test_the_launcher_does_not_arm_the_dead_man_switch() -> None:
    """Arming it maps the finding code to a success ping, the inversion this check removes; and
    `test_sds_deadman.py` selects launchers by that function's name in their text."""
    assert "sds_deadman" + "_arm" not in LAUNCHER.read_text()


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "bump-proposer")
    assert deploy.add_refusals(service, "bump-proposer") == []
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["bump-proposer"],
        "environment": {
            "BWS_ACCESS_TOKEN": "${BUMP_PROPOSER_BWS_ACCESS_TOKEN}",
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            "BUMP_PROPOSER_STATE_DIR": "/var/lib/sds/bump-proposer",
            # The BWS secret id of `bump-proposer-ping-url`: not a secret, not the ping URL.
            "SDS_DEADMAN_PING_URL_UUID": "786f75e2-3d02-4667-8607-b50601312a9d",
            **CHAIN,
        },
        "volumes": ["sds-lanes-bump-proposer-state:/var/lib/sds/bump-proposer"],
        "networks": ["coolify"],
        "restart": "unless-stopped",
    }
    document = yaml.safe_load(COMPOSE.read_text())
    assert set(document) == {"services", "volumes", "networks"}
    assert document["volumes"] == {"sds-lanes-bump-proposer-state": None}
    assert document["networks"] == {"coolify": {"external": True}}
    assert "ports" not in service and "labels" not in service
    assert "bump-proposer" in deploy.STATEFUL_LANES
    assert deploy.LANE_CHECKS["bump-proposer"] == "sds-bump-proposer"


def test_every_compose_variable_lane_run_must_pass_is_in_its_list() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "bump-proposer")
    environment = deploy._environment(service)
    passed = set(lane_run.lane_environment(environment, "/tmp/home"))
    assert set(environment) - passed == {"LANE_SCHEDULE_ENABLED"}
    assert "BUMP_PROPOSER_CLONE_ROOT" not in (*lane_run.COPIED, *lane_run.STATE_VARIABLES)


def test_the_crontab_runs_a_submitting_pass_daily_at_06_55() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["55 6 * * * lane-run bump-proposer --submit"]


def test_no_two_lanes_start_a_pass_in_the_same_minute() -> None:
    minutes = lane_start_minutes()
    assert minutes["bump-proposer"] == {55}
    # 50 is the rotation decomposer's.
    assert 50 in minutes["rotation-decomposer"]
    for lane, taken in minutes.items():
        if lane != "bump-proposer":
            assert 55 not in taken, lane


def test_the_repositorys_manifest_lists_the_lanes_secrets() -> None:
    """The lane's `--token` proof reads what the manifest lists for it: its seven secrets and its
    ping-URL secret. The launcher names each secret it reads, except the ping-URL secret, whose id
    reaches it from the compose through `lane-run`."""
    manifest = Path(".bws-secrets.toml").read_text()
    ping_url_secret = "786f75e2-3d02-4667-8607-b50601312a9d"
    secrets = deploy.lane_secrets(manifest, "bump-proposer")
    assert sorted(secrets) == sorted([*LANE_SECRETS, ping_url_secret])
    launcher = LAUNCHER.read_text()
    for uuid in secrets:
        assert uuid in launcher or uuid == ping_url_secret, uuid
