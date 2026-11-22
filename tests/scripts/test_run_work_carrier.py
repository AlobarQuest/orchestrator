"""`scripts/run-work-carrier.sh` in the lane's container, and the lane's files.

The real launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it in the
container: the lane's token as `BWS_ACCESS_TOKEN`, the state directory, the chain's three settings,
`PATH` and `HOME`. `bws`, `git`, `gh` and `curl` are stubs on PATH. In the venv, `work-watcher`,
`work-carrier` and `sds-github-mint` are stubs that log what reached them, and `python` runs the
real interpreter except for the chain verify, which it answers itself. Nothing reads BWS,
GitHub, change-manager, the orchestrator, the chain database or Healthchecks.
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
from tests.scripts.test_run_unit_verifier import lane_start_minutes

LAUNCHER = Path("scripts/run-work-carrier.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh", "sds-git.sh")
COMPOSE = Path("lanes/work-carrier/compose.yaml")
CRONTAB = Path("lanes/work-carrier/crontab")

READ_UUID = "314f276d-55ca-4ddc-a24d-b4a3013508cd"
SYSTEM_UUID = "221a48d5-3f29-4898-b300-b4820140c880"
OBSERVATION_UUID = "a9843d17-e599-4e80-ab1b-b4ff0164d8e2"
ALERT_UUID = "b415d03b-f92d-46b1-8a9d-b4f10103df49"
PROPOSE_UUID = "acccb346-4baa-43ec-a1d4-b4a400c048ee"
OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
CHAIN_UUID = "2f708d08-ea5d-4d11-bde0-b50200c9a60b"
APP_KEY_UUID = "314a966c-5654-4e3c-8358-b4fd01268775"
EVERY_PASS = (READ_UUID, SYSTEM_UUID, OBSERVER_UUID, CHAIN_UUID)
REGISTER_ONLY = (OBSERVATION_UUID, ALERT_UUID, PROPOSE_UUID)
LANE_SECRETS = (*EVERY_PASS, *REGISTER_ONLY, APP_KEY_UUID)

LANE_TOKEN = "a-lane-token"
MINTED = "ghs-minted-read-token"
BROAD_STANDIN = "broad-stand-in"


def value(uuid: str) -> str:
    return f"value-of-{uuid}"


CHAIN = {
    "FACTORY_EVENTS_DB_HOST": "b82fa1vg5whecgnjreuqb5tk",
    "FACTORY_EVENTS_DB_PORT": "5432",
    "FACTORY_EVENTS_DB_NAME": "postgres",
}
# A verified record carries every `package.approved` event; only its summary reaches the report.
APPROVAL_EVENT = '{"action": "package.approved", "event_id": "an-approval-event"}'
# And the scan feed's answer, which reaches the carry as a file and never the report.
PORTFOLIO_SCAN = {
    "scan": {
        "scanned_at": "2026-11-17T08:00:35Z",
        "scanned_epoch": 1794902435,
        "seq": 6,
        "result": "success",
        "rows": [{"repository": "example-owner/feed-row", "checks": {"runner.caller": "pass"}}],
    },
    "skipped": 0,
    "skipped_reason": None,
}
VERIFIED = (
    '{"events": 7, "head": "0123456789ab", "logger": "orchestrator.factory_chain_read", '
    '"op": "read-package-approvals", "outcome": "verified", '
    f'"package_approved": [{APPROVAL_EVENT}], "portfolio_scan": {json.dumps(PORTFOLIO_SCAN)}, '
    '"seq": 7}'
)
VERIFIED_SUMMARY = "outcome=verified seq=7 head=0123456789ab events=7"

# The `bws` stub logs the UUID and the token it was given; a UUID in `$BWS_REFUSES` fails.
_BWS = """#!/bin/sh
echo "$3 token=${BWS_ACCESS_TOKEN:-}" >> "$BWS_LOG"
case " ${BWS_REFUSES:-} " in *" $3 "*) exit 1 ;; esac
printf '{"value": "value-of-%s"}' "$3"
"""
_GH = '#!/bin/sh\necho "gh $*" >> "$GH_LOG"\necho gh-token\n'
_CURL = '#!/bin/sh\necho curl >> "$PING_LOG"\nexit 1\n'
# git: logs its argv and the credential environment, then makes the clone a checkout.
_GIT = """#!/bin/bash
echo "git $* | global=${GIT_CONFIG_GLOBAL:-} nosystem=${GIT_CONFIG_NOSYSTEM:-} \\
prompt=${GIT_TERMINAL_PROMPT:-} count=${GIT_CONFIG_COUNT:-} key0=${GIT_CONFIG_KEY_0:-} \\
value0=${GIT_CONFIG_VALUE_0:-} lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$GIT_LOG"
[ -n "${FAKE_CLONE_FAILS:-}" ] && { echo "fatal: repository not found" >&2; exit 128; }
target="${@: -1}"
# As real git does, a clone into a non-empty directory refuses.
if [ -d "$target" ] && [ -n "$(ls -A "$target")" ]; then
  echo "fatal: destination path '$target' already exists and is not an empty directory." >&2
  exit 128
fi
mkdir -p "$target/src" "$target/packages"
"""
_COMMON = (
    "lane_token=${BWS_ACCESS_TOKEN:+set} cm=${CHANGE_MANAGER_TOKEN:-} "
    "password=${FACTORY_EVENTS_READ_PASSWORD:-} host=${FACTORY_EVENTS_DB_HOST:-} "
    "port=${FACTORY_EVENTS_DB_PORT:-} name=${FACTORY_EVENTS_DB_NAME:-} "
    "github=${WORK_CARRIER_GITHUB_TOKEN:-} sec=${SECURITY_STANDARDS_DIR:-} "
    "header=${GIT_CONFIG_KEY_0:+set}"
)
_WATCHER = f"""#!/bin/bash
if [ "$1" = repositories ]; then
  echo "derive lane_token=${{BWS_ACCESS_TOKEN:+set}} \\
cm=${{WORK_WATCHER_CHANGE_MANAGER_TOKEN:-}}" >> "$LANE_LOG"
  [ -n "${{FAKE_DERIVE_RC:-}}" ] && exit "$FAKE_DERIVE_RC"
  printf '%s' "${{FAKE_REPOSITORIES-AlobarQuest/intent-packages
AlobarQuest/crm}}"
  exit 0
fi
echo "watcher args=$* wcm=${{WORK_WATCHER_CHANGE_MANAGER_TOKEN:-}} \\
orch=${{WORK_WATCHER_ORCHESTRATOR_TOKEN:-}} alert=${{SDS_BRIDGE_ALERT_TOKEN:-unset}} \\
state=${{WORK_WATCHER_STATE_DIR:-}} packages=${{WORK_WATCHER_PACKAGES_CHECKOUT:-}} \\
{_COMMON}" >> "$LANE_LOG"
exit "${{FAKE_WATCHER_RC:-0}}"
"""
_CARRIER = f"""#!/bin/bash
echo "carrier args=$* orch=${{WORK_CARRIER_ORCHESTRATOR_TOKEN:-}} \\
observation=${{WORK_CARRIER_OBSERVATION_TOKEN:-}} key=${{WORK_CARRIER_OBSERVATION_KEY_ID:-}} \\
members=${{SDS_MEMBERS_OBSERVER_TOKEN:-}} root=${{WORK_CARRIER_CHECKOUT_ROOT:-}} \\
clone=$([ -d "${{WORK_CARRIER_CHECKOUT_ROOT:-/none}}/intent-packages/src" ] && echo present) \\
{_COMMON}" >> "$LANE_LOG"
# The scan feed it was handed, as one line, so a test can read the file the launcher wrote.
while [ "$#" -gt 0 ]; do
  if [ "$1" = --portfolio-scan ]; then echo "feed $(cat "$2")" >> "$LANE_LOG"; fi
  shift
done
exit "${{FAKE_CARRIER_RC:-0}}"
"""
_MINT = """#!/bin/bash
key="$(cat)"
echo "mint args=$* key=$key lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
[ -n "${FAKE_MINT_RC:-}" ] && exit "$FAKE_MINT_RC"
echo "%s"
"""
# The chain verify is the one python call the stub answers; every other runs the real interpreter
# (`sds-bws.sh` parses BWS's JSON with it).
_PYTHON = """#!/bin/bash
if [ "$1 $2" = "-m orchestrator.factory_chain_read" ]; then
  echo "verify password=${FACTORY_EVENTS_READ_PASSWORD:-} host=${FACTORY_EVENTS_DB_HOST:-} \\
port=${FACTORY_EVENTS_DB_PORT:-} name=${FACTORY_EVENTS_DB_NAME:-} pythonpath=${PYTHONPATH:-} \\
lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
  echo "psycopg.OperationalError: connection to server at b82fa1vg5whecgnjreuqb5tk failed" >&2
  printf '%%s\n' "$FAKE_VERIFY_RECORD"
  exit "${FAKE_VERIFY_RC:-0}"
fi
exec "%s" "$@"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


class Run:
    def __init__(self, result: subprocess.CompletedProcess[str], logs: dict, root: Path):
        self.result = result
        self.logs = logs
        self.root = root

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

    def field(self, prefix: str, name: str) -> str:
        (line,) = self.lines(prefix)
        for token in line.split():
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
        raise AssertionError(f"{name} not in {line}")


def _run(tmp_path: Path, *args: str, **fake: str | None) -> Run:
    """A `None` in `fake` removes that variable from the launcher's environment."""
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    real_python = (Path.cwd() / ".venv" / "bin" / "python").resolve()
    _executable(venv / "python", _PYTHON % real_python)
    _executable(venv / "work-watcher", _WATCHER)
    _executable(venv / "work-carrier", _CARRIER)
    _executable(venv / "sds-github-mint", _MINT % MINTED)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("bws", _BWS), ("gh", _GH), ("curl", _CURL), ("git", _GIT)):
        _executable(stubs / name, body)
    home = tmp_path / "home"
    home.mkdir()
    clones = tmp_path / "clones"
    clones.mkdir(exist_ok=True)
    scratch = tmp_path / "tmp"
    scratch.mkdir(exist_ok=True)
    logs = {name: tmp_path / f"{name}.log" for name in ("lane", "bws", "gh", "ping", "git")}
    for log in logs.values():
        log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN": LANE_TOKEN,
        "SDS_DEADMAN_PING_URL_UUID": "",
        "WORK_WATCHER_STATE_DIR": str(tmp_path / "state"),
        "WORK_CARRIER_CLONE_ROOT": str(clones),
        "TMPDIR": str(scratch),
        # The dead-man helper's name lookup, on a `--register` pass with no ping-URL id, asks for
        # the broad identity; the stand-in keeps the real Keychain out of the test.
        "BWS_ACCESS_TOKEN_BROAD": BROAD_STANDIN,
        **CHAIN,
        "LANE_LOG": str(logs["lane"]),
        "BWS_LOG": str(logs["bws"]),
        "GH_LOG": str(logs["gh"]),
        "PING_LOG": str(logs["ping"]),
        "GIT_LOG": str(logs["git"]),
        "FAKE_VERIFY_RECORD": VERIFIED,
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


def test_a_registering_pass_reads_all_eight_secrets_with_the_lanes_one_token(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "--register")
    assert run.code == 0, run.output
    assert run.lane_reads() == [*EVERY_PASS, *REGISTER_ONLY, APP_KEY_UUID]
    # The only other read is the dead-man helper's Healthchecks key, by the broad stand-in.
    assert all(
        line.endswith((f"token={LANE_TOKEN}", f"token={BROAD_STANDIN}")) for line in run.logs["bws"]
    )
    assert run.logs["gh"] == []
    for uuid in LANE_SECRETS:
        assert value(uuid) not in run.output
    assert LANE_TOKEN not in run.output


def test_a_bare_pass_reads_only_the_every_pass_secrets_and_pings_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.code == 0, run.output
    assert run.lane_reads() == [*EVERY_PASS, APP_KEY_UUID]
    assert run.logs["bws"] == [f"{uuid} token={LANE_TOKEN}" for uuid in run.lane_reads()]
    assert run.logs["ping"] == []
    # The watcher lists with the READ bearer and reads the orchestrator with SYSTEM; the carry
    # holds no SYSTEM bearer, as on the Mac's bare pass.
    assert run.field("watcher", "args") == ""
    assert run.field("watcher", "wcm") == value(READ_UUID)
    assert run.field("watcher", "orch") == value(SYSTEM_UUID)
    assert run.field("watcher", "alert") == "unset"
    # No `--register`; the scan feed is the one argument the container pass adds.
    assert run.field("carrier", "args") == "--portfolio-scan"
    assert run.field("carrier", "orch") == ""
    assert run.field("carrier", "observation") == ""


def test_a_registering_pass_hands_each_program_its_credentials(tmp_path: Path) -> None:
    run = _run(tmp_path, "--register")
    assert run.code == 0, run.output
    assert run.field("watcher", "args") == "--retire"
    assert run.field("watcher", "wcm") == value(PROPOSE_UUID)
    assert run.field("watcher", "orch") == value(SYSTEM_UUID)
    assert run.field("watcher", "alert") == value(ALERT_UUID)
    assert run.field("watcher", "state") == str(tmp_path / "state")
    assert run.field("carrier", "args") == "--register"
    assert run.field("carrier", "orch") == value(SYSTEM_UUID)
    assert run.field("carrier", "observation") == value(OBSERVATION_UUID)
    assert run.field("carrier", "key") == "work-carrier-observer"
    assert run.field("carrier", "members") == value(OBSERVER_UUID)
    assert run.field("carrier", "cm") == value(READ_UUID)
    assert (tmp_path / "state").is_dir()
    # The retirement runs before the carry.
    order = [line.split()[0] for line in run.logs["lane"]]
    assert order.index("watcher") < order.index("carrier")


@pytest.mark.parametrize("refused", LANE_SECRETS)
def test_an_unreadable_secret_fails_the_pass_before_any_program(
    tmp_path: Path, refused: str
) -> None:
    run = _run(tmp_path, "--register", BWS_REFUSES=refused)
    assert run.code == 1
    assert run.lines("watcher") == [] and run.lines("carrier") == []
    if refused != APP_KEY_UUID:
        assert "(the lane's token)" in run.result.stderr
        assert run.lines("mint") == []


def test_no_program_gets_the_lanes_token(tmp_path: Path) -> None:
    run = _run(tmp_path, "--register")
    assert run.code == 0, run.output
    for prefix in ("derive", "mint", "verify", "watcher", "carrier"):
        assert run.field(prefix, "lane_token") == "", prefix
    assert all(line.endswith("lane_token=") for line in run.logs["git"])


# ---- the chain: the read password and settings reach the carrier's side alone ----------------


def test_the_read_password_and_settings_reach_the_verify_and_the_carry_and_never_the_watcher(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "--register")
    assert run.code == 0, run.output
    for prefix in ("verify", "carrier"):
        assert run.field(prefix, "password") == value(CHAIN_UUID), prefix
        assert run.field(prefix, "host") == "b82fa1vg5whecgnjreuqb5tk", prefix
        assert run.field(prefix, "port") == "5432", prefix
        assert run.field(prefix, "name") == "postgres", prefix
    for name in ("password", "host", "port", "name"):
        assert run.field("watcher", name) == "", name


def test_an_ambient_chain_variable_never_reaches_a_program(tmp_path: Path) -> None:
    ambient = {
        "CHAIN_DB_HOST": "ambient-host",
        "CHAIN_DB_PORT": "6543",
        "CHAIN_DB_NAME": "ambient-db",
        "CHAIN_PASSWORD": "ambient-password",
        "FACTORY_EVENTS_APPEND_PASSWORD": "ambient-append-password",
        "FACTORY_EVENTS_READ_PASSWORD": "ambient-read-password",
    }
    run = _run(tmp_path, "--register", **ambient)
    assert run.code == 0, run.output
    for prefix in ("verify", "carrier"):
        assert run.field(prefix, "password") == value(CHAIN_UUID), prefix
        assert run.field(prefix, "host") == CHAIN["FACTORY_EVENTS_DB_HOST"], prefix
        assert run.field(prefix, "port") == CHAIN["FACTORY_EVENTS_DB_PORT"], prefix
        assert run.field(prefix, "name") == CHAIN["FACTORY_EVENTS_DB_NAME"], prefix
    for name in ("password", "host", "port", "name"):
        assert run.field("watcher", name) == "", name


@pytest.mark.parametrize("missing", sorted(CHAIN))
def test_a_missing_chain_setting_fails_the_pass_before_any_read(
    tmp_path: Path, missing: str
) -> None:
    run = _run(tmp_path, **{missing: ""})
    assert run.code == 1
    assert f"{missing} is not set in the lane's container" in run.result.stderr
    assert run.lane_reads() == []
    assert run.logs["lane"] == []


def test_the_chain_verify_runs_every_pass_under_the_images_security_standards(
    tmp_path: Path,
) -> None:
    for args in ((), ("--register",)):
        run = _run(tmp_path / "-".join(args or ("bare",)), *args)
        assert run.code == 0, run.output
        expected = str(tmp_path / "-".join(args or ("bare",)) / "tree" / "security-standards")
        assert run.field("verify", "pythonpath") == f"{expected}/src"
        assert run.field("carrier", "sec") == expected
        # The verify's record is the step's whole log; its stderr is discarded.
        assert f"[chain] {VERIFIED_SUMMARY}\n" in run.result.stdout
        assert "an-approval-event" not in run.output
        assert "OperationalError" not in run.output
        order = [line.split()[0] for line in run.logs["lane"]]
        # The retirement reads no chain, so it runs first; the verify gates the carry alone.
        assert order.index("watcher") < order.index("verify") < order.index("carrier")


@pytest.mark.parametrize(
    ("record", "rc", "summary"),
    [
        (
            '{"error": "seq 4: hash mismatch", "head": "abc", "outcome": "broken", "seq": 4}',
            "2",
            "outcome=broken seq=4 head=abc error=seq 4: hash mismatch",
        ),
        (
            '{"error_type": "OperationalError", "outcome": "failure"}',
            "2",
            "outcome=failure error_type=OperationalError",
        ),
        ('{"outcome": "verified", "seq": 7}', "2", "outcome=verified seq=7"),
        ('{"outcome": "failure"}', "0", "outcome=failure"),
        ('{"outcome": "verified"', "0", "outcome=unreadable"),
        ("", "0", "outcome=unreadable"),
    ],
)
def test_anything_but_a_verified_chain_fails_the_pass_and_carries_nothing(
    tmp_path: Path, record: str, rc: str, summary: str
) -> None:
    run = _run(tmp_path, "--register", FAKE_VERIFY_RECORD=record, FAKE_VERIFY_RC=rc)
    assert run.code == 1
    assert (
        f"[chain] FATAL: the factory-events chain did not verify (exit {rc}): {summary}; "
        "nothing was carried"
    ) in run.result.stdout
    # The retirement still ran: a chain outage mustn't stop built work being retired.
    assert len(run.lines("watcher")) == 1
    assert run.lines("carrier") == []


@pytest.mark.parametrize("args", [(), ("--register",)], ids=["bare", "register"])
def test_a_verified_pass_hands_the_carry_the_scan_feed_as_a_file_it_then_removes(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    assert run.code == 0, run.output
    (carrier,) = run.lines("carrier")
    assert carrier.startswith(
        f"carrier args={' '.join(args)}{' ' if args else ''}--portfolio-scan "
    )
    (feed,) = run.lines("feed")
    assert json.loads(feed.removeprefix("feed ")) == PORTFOLIO_SCAN
    # The report keeps only the summary, and the pass leaves no feed file behind.
    assert "example-owner/feed-row" not in run.output
    assert list((tmp_path / "tmp").glob("sds-work-carrier-feed.*")) == []


def test_a_verified_record_with_no_scan_hands_over_null(tmp_path: Path) -> None:
    record = '{"outcome": "verified", "seq": 0, "events": 0, "package_approved": []}'
    run = _run(tmp_path, FAKE_VERIFY_RECORD=record)
    assert run.code == 0, run.output
    assert run.lines("feed") == ["feed null"]


def test_a_chain_that_did_not_verify_hands_over_no_feed(tmp_path: Path) -> None:
    run = _run(tmp_path, FAKE_VERIFY_RC="2")
    assert run.lines("carrier") == [] and run.lines("feed") == []
    assert list((tmp_path / "tmp").glob("sds-work-carrier-feed.*")) == []


def test_a_worse_retirement_outcome_still_wins_over_a_failed_verify(tmp_path: Path) -> None:
    run = _run(tmp_path, FAKE_VERIFY_RC="2", FAKE_WATCHER_RC="2")
    assert run.code == 2
    assert run.lines("carrier") == []


# ---- the GitHub token and the clone ----------------------------------------------------------


def test_the_mint_names_the_derived_repositories_and_the_read_permissions(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.code == 0, run.output
    (mint,) = run.lines("mint")
    assert mint == (
        "mint args=--app-id 5684758 --repository AlobarQuest/intent-packages "
        "--repository AlobarQuest/crm --permission metadata=read --permission contents=read "
        f"--permission pull_requests=read key={value(APP_KEY_UUID)} lane_token="
    )
    # The derivation lists with the change-manager bearer the watcher will use.
    assert run.field("derive", "cm") == value(READ_UUID)
    assert run.field("watcher", "github") == MINTED
    assert run.logs["gh"] == []


@pytest.mark.parametrize("rc", ["1", "2", "127"])
def test_an_underivable_list_mints_nothing_and_passes_its_code_through(
    tmp_path: Path, rc: str
) -> None:
    run = _run(tmp_path, FAKE_DERIVE_RC=rc)
    assert run.code == int(rc)
    assert f"work-watcher repositories exited {rc}" in run.result.stderr
    assert run.lines("mint") == [] and run.logs["git"] == []
    assert run.lines("carrier") == []


@pytest.mark.parametrize("listed", ["", "\n\n"], ids=["empty", "blank-lines"])
def test_an_empty_list_mints_nothing_and_is_unusable_input(tmp_path: Path, listed: str) -> None:
    run = _run(tmp_path, FAKE_REPOSITORIES=listed)
    assert run.code == 2
    assert "an empty list" in run.result.stderr
    assert run.lines("mint") == []


def test_a_blank_line_in_the_list_names_no_repository(tmp_path: Path) -> None:
    run = _run(tmp_path, FAKE_REPOSITORIES="AlobarQuest/intent-packages\n\nAlobarQuest/crm\n")
    assert run.code == 0, run.output
    (mint,) = run.lines("mint")
    assert "--repository AlobarQuest/intent-packages --repository AlobarQuest/crm --permission" in (
        mint
    )
    assert "--repository  " not in mint


def test_a_failed_mint_fails_the_pass_before_the_clone(tmp_path: Path) -> None:
    run = _run(tmp_path, FAKE_MINT_RC="1")
    assert run.code == 1
    assert "could not mint the work lane's GitHub token" in run.result.stderr
    assert run.logs["git"] == []
    assert run.lines("carrier") == []


def test_the_clone_carries_the_token_only_in_its_environment(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.code == 0, run.output
    (clone,) = run.logs["git"]
    argv, environment = clone.split(" | ")
    assert argv == (
        "git clone --quiet --depth 1 --single-branch --branch main "
        f"https://github.com/AlobarQuest/intent-packages.git {run.root}/intent-packages"
    )
    assert MINTED not in argv
    header = base64.b64encode(f"x-access-token:{MINTED}".encode()).decode()
    assert environment.split() == [
        "global=/dev/null",
        "nosystem=1",
        "prompt=0",
        "count=1",
        "key0=http.https://github.com/.extraheader",
        "value0=AUTHORIZATION:",
        "basic",
        header,
        "lane_token=",
    ]
    assert header not in run.output


def test_both_programs_read_the_pass_clone_and_it_is_discarded_after(tmp_path: Path) -> None:
    clone = tmp_path / "clones" / "intent-packages"
    clone.mkdir(parents=True)
    (clone / "left-from-a-previous-pass").write_text("")
    run = _run(tmp_path)
    assert run.code == 0, run.output
    assert run.field("carrier", "root") == str(run.root)
    assert run.field("carrier", "clone") == "present"
    assert run.field("watcher", "packages") == str(clone)
    assert not clone.exists()


def test_a_failed_clone_fails_the_pass_and_names_gits_message(tmp_path: Path) -> None:
    run = _run(tmp_path, FAKE_CLONE_FAILS="1")
    assert run.code == 1
    assert "could not clone https://github.com/AlobarQuest/intent-packages.git" in run.output
    assert "fatal: repository not found" in run.result.stderr
    assert run.lines("carrier") == []


# ---- the outcome -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("watcher", "carrier", "expected"),
    [
        ("0", "0", 0),
        ("3", "0", 3),
        ("0", "2", 2),
        ("3", "1", 1),
        ("2", "1", 2),
        ("1", "2", 2),
        # An unrecognised code (127 is a missing binary) is preserved and dominates, in either
        # position: the `for rc in 1 3 2` fold would let it fall through to success.
        ("0", "127", 127),
        ("127", "3", 127),
        ("3", "127", 127),
    ],
)
def test_the_worst_outcome_of_the_two_phases_wins(
    tmp_path: Path, watcher: str, carrier: str, expected: int
) -> None:
    run = _run(tmp_path, "--register", FAKE_WATCHER_RC=watcher, FAKE_CARRIER_RC=carrier)
    assert run.code == expected


def test_the_retirement_runs_before_the_carry(tmp_path: Path) -> None:
    """Reversed, the carry would re-register a finished record's revision before the watcher
    retires it; only the launcher decides the order."""
    run = _run(tmp_path, "--register")
    order = [
        line.split()[0] for line in run.logs["lane"] if line.split()[0] in ("watcher", "carrier")
    ]
    assert order == ["watcher", "carrier"]


def test_an_acting_pass_asks_the_watcher_to_retire_and_the_carry_to_register(
    tmp_path: Path,
) -> None:
    """The flags are translated, not forwarded: `--register` is the carry's word for acting,
    `--retire` the watcher's."""
    run = _run(tmp_path, "--register")
    assert run.field("watcher", "args") == "--retire"
    assert run.field("carrier", "args") == "--register"


def test_a_bare_pass_asks_neither_to_act_and_still_runs_both(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.lines("watcher") and run.lines("carrier")
    assert run.field("watcher", "args") == ""
    assert "--register" not in run.lines("carrier")[0]


def test_outside_the_container_it_refuses_and_runs_and_reads_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path, "--register", BWS_ACCESS_TOKEN=None)
    assert run.code == 1
    assert "the work-carrier lane runs only in its container, through lane-run" in run.output
    assert run.logs["lane"] == run.logs["bws"] == run.logs["gh"] == run.logs["git"] == []
    assert run.logs["ping"] == []


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "work-carrier")
    assert deploy.add_refusals(service, "work-carrier") == []
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["work-carrier"],
        "environment": {
            "BWS_ACCESS_TOKEN": "${WORK_CARRIER_BWS_ACCESS_TOKEN}",
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            "WORK_WATCHER_STATE_DIR": "/var/lib/sds/work-carrier",
            # The BWS secret id of `work-carrier-ping-url`: not a secret, not the ping URL.
            "SDS_DEADMAN_PING_URL_UUID": "55fbae20-e503-4eb5-b75c-b5040164d394",
            **CHAIN,
        },
        "volumes": ["sds-lanes-work-carrier-state:/var/lib/sds/work-carrier"],
        "networks": ["coolify"],
        "restart": "unless-stopped",
    }
    document = yaml.safe_load(COMPOSE.read_text())
    assert set(document) == {"services", "volumes", "networks"}
    assert document["volumes"] == {"sds-lanes-work-carrier-state": None}
    assert document["networks"] == {"coolify": {"external": True}}
    assert "ports" not in service and "labels" not in service
    assert "work-carrier" in deploy.STATEFUL_LANES
    assert deploy.LANE_CHECKS["work-carrier"] == "sds-work-carrier"


def test_every_compose_variable_lane_run_must_pass_is_in_its_list() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "work-carrier")
    environment = deploy._environment(service)
    passed = set(lane_run.lane_environment(environment, "/tmp/home"))
    assert set(environment) - passed == {"LANE_SCHEDULE_ENABLED"}


def test_the_crontab_runs_a_registering_pass_daily_at_07_05() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["5 7 * * * lane-run work-carrier --register"]


def test_no_two_lanes_start_a_pass_in_the_same_minute() -> None:
    minutes = lane_start_minutes()
    assert minutes["work-carrier"] == {5}
    for lane, taken in minutes.items():
        if lane != "work-carrier":
            assert 5 not in taken, lane


def test_the_repositorys_manifest_lists_the_lanes_secrets() -> None:
    """The lane's `--token` proof reads what the manifest lists for it: its eight secrets and its
    ping-URL secret. The launcher names each secret it reads, except the ping-URL secret, whose id
    reaches it from the compose through `lane-run`."""
    manifest = Path(".bws-secrets.toml").read_text()
    ping_url_secret = "55fbae20-e503-4eb5-b75c-b5040164d394"
    secrets = deploy.lane_secrets(manifest, "work-carrier")
    assert sorted(secrets) == sorted([*LANE_SECRETS, ping_url_secret])
    launcher = LAUNCHER.read_text()
    for uuid in secrets:
        assert uuid in launcher or uuid == ping_url_secret, uuid


def test_the_image_links_the_packages_clone_where_package_sources_looks() -> None:
    """`package_sources._intent_packages_src` looks beside the orchestrator's checkout, so
    `/intent-packages` for `/app`; the link's target is the launcher's default clone."""
    dockerfile = Path("lanes/Dockerfile").read_text()
    assert "ln -s /var/tmp/sds-work-carrier/intent-packages /intent-packages" in dockerfile
    assert "install -d -m 0700 -o orchestrator -g orchestrator /var/tmp/sds-work-carrier" in (
        dockerfile
    )
    assert "${WORK_CARRIER_CLONE_ROOT:-/var/tmp/sds-work-carrier}" in LAUNCHER.read_text()
    assert "WORK_CARRIER_CLONE_ROOT" not in (*lane_run.COPIED, *lane_run.STATE_VARIABLES)
