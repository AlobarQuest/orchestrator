"""`scripts/run-change-window.sh` in the lane's container (ADR-0059 increment 8), and the lane's
tracked files.

The real launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it (the
lane's token as `BWS_ACCESS_TOKEN`, a ping-URL secret id, PATH and HOME), plus the test seams that
name the four secret ids, which are empty in the launcher until their secrets exist. `bws`, `git`,
`node` and `curl` are stubs on PATH that log what reached them: nothing reads BWS, clones
infraops-mcp-server, runs the window, or reaches change-manager, Coolify, alert-hub or
Healthchecks.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import deploy_orchestrator as deploy

LAUNCHER = Path("scripts/run-change-window.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh", "sds-cred-registries.sh")
COMPOSE = Path("lanes/change-window/compose.yaml")
CRONTAB = Path("lanes/change-window/crontab")

CHANGE_MANAGER_UUID = "11111111-1111-4111-8111-111111111111"
COOLIFY_UUID = "22222222-2222-4222-8222-222222222222"
ANTHROPIC_UUID = "44444444-4444-4444-8444-444444444444"
SENDER_UUID = "55555555-5555-4555-8555-555555555555"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
PING_URL = "https://hc.example/ping/change-window"
LANE_TOKEN = "a-lane-token"
SECRETS = {
    uuid: f"value-of-{uuid}"
    for uuid in (CHANGE_MANAGER_UUID, COOLIFY_UUID, ANTHROPIC_UUID, SENDER_UUID)
}
# The test seams that name each secret id, and the launcher variable each one sets.
SEAMS = {
    "CHANGE_WINDOW_CHANGE_MANAGER_UUID": ("CHANGE_MANAGER_EXECUTE_UUID", CHANGE_MANAGER_UUID),
    "CHANGE_WINDOW_COOLIFY_UUID": ("COOLIFY_WINDOW_TOKEN_UUID", COOLIFY_UUID),
    "CHANGE_WINDOW_ANTHROPIC_UUID": ("ANTHROPIC_KEY_UUID", ANTHROPIC_UUID),
    "CHANGE_WINDOW_DIGEST_SENDER_UUID": ("DIGEST_SENDER_UUID", SENDER_UUID),
}
DIGEST = "# Change window\n\n- app-one: done\n"

# The `bws` stub logs the UUID and the token it was given. A UUID in `$BWS_REFUSES` fails, and one
# in `$BWS_EMPTY` holds an empty value, and `$BWS_RAW_UUID` answers `$BWS_RAW` as it is; the
# ping-URL secret holds `PING_URL`; every other secret
# `value-of-<uuid>`.
_BWS = f"""#!/bin/sh
echo "$3 token=${{BWS_ACCESS_TOKEN:-}}" >> "$BWS_LOG"
echo "bws $*" >> "$ARGV_LOG"
case " $BWS_REFUSES " in *" $3 "*) exit 1 ;; esac
case " $BWS_EMPTY " in *" $3 "*) echo '{{"value": ""}}'; exit 0 ;; esac
[ "$3" = "${{BWS_RAW_UUID:-}}" ] && {{ printf '%s' "$BWS_RAW"; exit 0; }}
[ "$3" = "{PING_URL_UUID}" ] && {{ echo '{{"value": "{PING_URL}"}}'; exit 0; }}
printf '{{"value": "value-of-%s"}}' "$3"
"""
# `git clone ... <url> <directory>`: makes the directory, with the bundle unless `$FAKE_NO_BUNDLE`.
_GIT = """#!/bin/bash
echo "git $* lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
echo "git $*" >> "$ARGV_LOG"
[ -n "${FAKE_CLONE_FAILS:-}" ] && { echo "fatal: unable to access" >&2; exit 128; }
directory="${@: -1}"
mkdir -p "$directory/dist/bundle"
[ -n "${FAKE_NO_BUNDLE:-}" ] || : > "$directory/dist/bundle/change-mgr-cli.js"
"""
# `node <bundle> run-window ...`: logs its arguments and its whole environment, writes the digest
# when given `--report-dir` (unless `$FAKE_NO_DIGEST`), and exits `$FAKE_RC`.
_NODE = """#!/bin/bash
echo "node $*" >> "$LANE_LOG"
echo "node $*" >> "$ARGV_LOG"
env > "$NODE_ENV"
report_dir="" now=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --report-dir) report_dir="$2"; shift ;;
    --now) now="$2"; shift ;;
  esac
  shift
done
if [ -n "$report_dir" ] && [ -z "${FAKE_NO_DIGEST:-}" ]; then
  if [ -n "${FAKE_DIGEST_FILE:-}" ]; then
    cp "$FAKE_DIGEST_FILE" "$report_dir/${now%%T*}.change-window.md"
  else
    printf '%s' "$FAKE_DIGEST" > "$report_dir/${now%%T*}.change-window.md"
  fi
fi
echo '{"dry_run":true,"instance":"prod","would_claim":[7]}'
exit "${FAKE_RC:-0}"
"""
# A ping's URL arrives on stdin as curl's config. The digest's send is the call with `-w`: its
# config (the auth header) and its body file are logged, and it prints `$SEND_STATUS`.
_CURL = """#!/bin/bash
config="$(cat)"
echo "curl $*" >> "$ARGV_LOG"
case " $* " in
  *" -w "*)
    printf '%s\\n' "$config" >> "$SEND_LOG"
    for argument in "$@"; do
      case "$argument" in @*) cp "${argument#@}" "$SEND_BODY" ;; esac
    done
    [ -n "${SEND_FAILS:-}" ] && { printf '000'; exit 7; }
    printf '%s' "${SEND_STATUS:-202}"
    exit 0
    ;;
esac
printf '%s\\n' "$config" >> "$PING_LOG"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


class Run:
    def __init__(self, result: subprocess.CompletedProcess[str], tmp_path: Path) -> None:
        self.result = result
        self.returncode = result.returncode
        self.stdout = result.stdout
        self.stderr = result.stderr

        def lines(name: str) -> list[str]:
            path = tmp_path / f"{name}.log"
            return path.read_text().splitlines() if path.exists() else []

        self.lane = lines("lane")
        self.bws = lines("bws")
        self.argv = lines("argv")
        self.send = lines("send")
        self.pings = [line.removeprefix('url = "').removesuffix('"') for line in lines("ping")]
        node_env = tmp_path / "node.env"
        self.node_env: dict[str, str] | None = None
        if node_env.exists():
            self.node_env = dict(
                line.split("=", 1) for line in node_env.read_text().splitlines() if "=" in line
            )
        body = tmp_path / "send-body.json"
        self.body = json.loads(body.read_text()) if body.exists() else None

    def nodes(self) -> list[str]:
        return [line for line in self.lane if line.startswith("node ")]

    def gits(self) -> list[str]:
        return [line for line in self.lane if line.startswith("git ")]

    def secret_reads(self) -> list[str]:
        return [line.split()[0] for line in self.bws if not line.startswith(PING_URL_UUID)]


def _run(tmp_path: Path, *args: str, **fake: str) -> Run:
    """`fake` is added to the environment; `NO_NODE="1"` leaves `node` off PATH instead."""
    no_node = fake.pop("NO_NODE", "") == "1"
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    # `sds-bws.sh` parses BWS's JSON, and the digest's request is built, with the venv's
    # interpreter, for real.
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    tools = {"bws": _BWS, "git": _GIT, "curl": _CURL, "node": _NODE}
    if no_node:
        del tools["node"]
    for name, body in tools.items():
        _executable(stubs / name, body)
    home = tmp_path / "home"
    home.mkdir()
    pass_tmp = tmp_path / "tmp"
    pass_tmp.mkdir()
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(pass_tmp),
        "BWS_ACCESS_TOKEN": LANE_TOKEN,
        "SDS_DEADMAN_PING_URL_UUID": PING_URL_UUID,
        **{seam: uuid for seam, (_, uuid) in SEAMS.items()},
        "LANE_LOG": str(tmp_path / "lane.log"),
        "BWS_LOG": str(tmp_path / "bws.log"),
        "ARGV_LOG": str(tmp_path / "argv.log"),
        "PING_LOG": str(tmp_path / "ping.log"),
        "SEND_LOG": str(tmp_path / "send.log"),
        "SEND_BODY": str(tmp_path / "send-body.json"),
        "NODE_ENV": str(tmp_path / "node.env"),
        "FAKE_DIGEST": DIGEST,
        **fake,
    }
    result = subprocess.run(
        [
            "/usr/bin/env",
            "-i",
            *(f"{k}={v}" for k, v in env.items()),
            "/bin/bash",
            str(root / "scripts" / LAUNCHER.name),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    run = Run(result, tmp_path)
    # Every pass removes its directory: the clone, the report and the digest's request body.
    assert list(pass_tmp.iterdir()) == [], list(pass_tmp.iterdir())
    return run


_NOW = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"


# ---- the dry run -----------------------------------------------------------------------------


def test_a_bare_pass_is_a_prod_dry_run_that_reads_only_the_change_manager_bearer(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    assert run.returncode == 0, run.stderr
    [node] = run.nodes()
    assert re.fullmatch(
        r"node \S+/infraops-mcp-server/dist/bundle/change-mgr-cli\.js"
        r" run-window --dry-run --instance prod",
        node,
    ), node
    # The ping-URL secret isn't read either: a dry run is no window, so it pings nothing.
    assert run.bws == [f"{CHANGE_MANAGER_UUID} token={LANE_TOKEN}"]
    assert run.pings == []
    assert run.send == []
    assert run.node_env is not None
    assert run.node_env["CHANGE_MGR_API_BASE"] == "https://change-mgr.alobar.net"
    assert run.node_env["CHANGE_MGR_M2M_TOKEN"] == SECRETS[CHANGE_MANAGER_UUID]
    for name in ("COOLIFY_API_TOKEN", "COOLIFY_BASE_URL", "ANTHROPIC_API_KEY", "BWS_ACCESS_TOKEN"):
        assert name not in run.node_env, name
    # The dry run's one JSON line reaches the lane log.
    assert '{"dry_run":true,"instance":"prod","would_claim":[7]}' in run.stdout


# ---- the window ------------------------------------------------------------------------------


def test_a_submit_pass_runs_the_prod_window_with_every_name_the_bundle_reads(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "--submit")
    assert run.returncode == 0, run.stderr
    [node] = run.nodes()
    assert re.fullmatch(
        r"node \S+/infraops-mcp-server/dist/bundle/change-mgr-cli\.js run-window --instance prod"
        rf" --report-dir \S+/reports --now {_NOW}",
        node,
    ), node
    assert "--dry-run" not in node
    assert run.bws == [
        f"{PING_URL_UUID} token={LANE_TOKEN}",
        f"{CHANGE_MANAGER_UUID} token={LANE_TOKEN}",
        f"{COOLIFY_UUID} token={LANE_TOKEN}",
        f"{ANTHROPIC_UUID} token={LANE_TOKEN}",
        f"{SENDER_UUID} token={LANE_TOKEN}",
    ]
    assert run.node_env is not None
    assert {
        name: run.node_env[name]
        for name in (
            "COOLIFY_BASE_URL",
            "COOLIFY_API_TOKEN",
            "ANTHROPIC_API_KEY",
            "CHANGE_MGR_API_BASE",
            "CHANGE_MGR_M2M_TOKEN",
        )
    } == {
        "COOLIFY_BASE_URL": "http://100.92.138.19:8000",
        "COOLIFY_API_TOKEN": SECRETS[COOLIFY_UUID],
        "ANTHROPIC_API_KEY": SECRETS[ANTHROPIC_UUID],
        "CHANGE_MGR_API_BASE": "https://change-mgr.alobar.net",
        "CHANGE_MGR_M2M_TOKEN": SECRETS[CHANGE_MANAGER_UUID],
    }
    # The digest's token never reaches the window's program.
    assert SECRETS[SENDER_UUID] not in run.node_env.values()
    assert run.pings == [f"{PING_URL}/start", PING_URL]


def test_no_coolify_instance_setting_but_the_prod_base_reaches_the_window(tmp_path: Path) -> None:
    """infraops' client reads `COOLIFY_PROD_*` before the base pair, and `COOLIFY_DEV_*` for a dev
    item: none of them reaches the program, whatever the environment held."""
    planted = {
        "COOLIFY_DEV_BASE_URL": "http://dev.example",
        "COOLIFY_DEV_API_TOKEN": "a-dev-token",
        "COOLIFY_PROD_BASE_URL": "http://elsewhere.example",
        "COOLIFY_PROD_API_TOKEN": "another-token",
    }
    run = _run(tmp_path, "--submit", **planted)
    assert run.returncode == 0, run.stderr
    assert run.node_env is not None
    assert sorted(name for name in run.node_env if name.startswith("COOLIFY_")) == [
        "COOLIFY_API_TOKEN",
        "COOLIFY_BASE_URL",
    ]
    assert run.node_env["COOLIFY_BASE_URL"] == "http://100.92.138.19:8000"


@pytest.mark.parametrize("mode", [(), ("--submit",)], ids=["bare", "submit"])
def test_the_lanes_token_reaches_neither_git_nor_node(tmp_path: Path, mode: tuple[str]) -> None:
    run = _run(tmp_path, *mode)
    assert run.returncode == 0, run.stderr
    [git] = run.gits()
    assert git.endswith(" lane_token=")
    assert "https://github.com/AlobarQuest/infraops-mcp-server.git" in git
    assert "--depth 1" in git and "--branch main" in git
    assert run.node_env is not None
    assert "BWS_ACCESS_TOKEN" not in run.node_env


@pytest.mark.parametrize("mode", [(), ("--submit",)], ids=["bare", "submit"])
def test_no_secret_is_a_process_argument(tmp_path: Path, mode: tuple[str]) -> None:
    run = _run(tmp_path, *mode)
    assert run.returncode == 0, run.stderr
    argv = "\n".join(run.argv)
    for secret in (LANE_TOKEN, PING_URL, *SECRETS.values()):
        assert secret not in argv, secret
        assert secret not in run.stdout + run.stderr, secret


# ---- the digest ------------------------------------------------------------------------------


def test_a_submit_pass_sends_the_digest_as_an_info_notice(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit")
    assert run.returncode == 0, run.stderr
    assert run.send == [f'header = "Authorization: Bearer {SECRETS[SENDER_UUID]}"']
    assert run.body is not None
    date = run.body["key"].removeprefix("report:change-window:")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
    assert run.body == {
        "key": f"report:change-window:{date}",
        "severity": "info",
        "kind": "notice",
        "title": f"Change window {date}",
        "message": DIGEST,
    }
    send = next(line for line in run.argv if line.startswith("curl ") and " -w " in line)
    assert " https://alerts.alobar.net/v1/alerts " in f"{send} "
    assert f"[change-window] digest sent to alert-hub (report:change-window:{date})" in run.stdout
    # The request body is never logged.
    assert '"kind": "notice"' not in run.stdout + run.stderr


def test_a_long_digest_is_cut_to_the_notices_limit_with_a_footer(tmp_path: Path) -> None:
    digest = tmp_path / "long.md"
    digest.write_text("é" * 20000)
    run = _run(tmp_path, "--submit", FAKE_DIGEST_FILE=str(digest))
    assert run.returncode == 0, run.stderr
    assert run.body is not None
    message = run.body["message"]
    assert len(message.encode("utf-16-le")) // 2 <= 12000
    assert len(json.dumps(run.body, ensure_ascii=False).encode()) <= 16384
    assert message.endswith(
        "(cut at 12,000 of 20,000 characters; the whole report is the window run's report_md"
        " in change-manager)"
    )


@pytest.mark.parametrize(
    "fault", [{"SEND_STATUS": "500"}, {"SEND_FAILS": "1"}], ids=["refused", "unreachable"]
)
def test_a_failed_digest_send_logs_one_line_and_keeps_the_pass_green(
    tmp_path: Path, fault: dict[str, str]
) -> None:
    run = _run(tmp_path, "--submit", **fault)
    assert run.returncode == 0, run.stderr
    status = fault.get("SEND_STATUS", "000")
    lines = [line for line in run.stdout.splitlines() if "digest" in line]
    assert lines == [
        f"[change-window] digest not sent: alert-hub answered '{status}'"
        " (the pass's exit is unchanged)"
    ]
    assert run.pings == [f"{PING_URL}/start", PING_URL]


def test_a_window_that_wrote_no_digest_sends_none(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit", FAKE_NO_DIGEST="1", FAKE_RC="1")
    assert run.returncode == 1
    assert run.send == []
    assert "[change-window] the window wrote no digest; none sent" in run.stdout
    assert run.pings == [f"{PING_URL}/start", f"{PING_URL}/fail"]


# ---- failure handling ------------------------------------------------------------------------


@pytest.mark.parametrize(("code", "expected"), [(1, 1), (3, 1), (127, 1)])
def test_a_failed_window_fails_the_outcome(tmp_path: Path, code: int, expected: int) -> None:
    run = _run(tmp_path, "--submit", FAKE_RC=str(code))
    assert run.returncode == expected
    assert len(run.nodes()) == 1
    assert run.pings == [f"{PING_URL}/start", f"{PING_URL}/fail"]
    if code != 1:
        assert f"FATAL: run-window exited {code}" in run.stdout


def test_a_failed_dry_run_exits_1_and_pings_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path, FAKE_RC="1")
    assert run.returncode == 1
    assert run.pings == []


@pytest.mark.parametrize(
    ("mode", "seam"),
    [
        ((), "CHANGE_WINDOW_CHANGE_MANAGER_UUID"),
        *((("--submit",), seam) for seam in SEAMS),
    ],
    ids=["bare-change-manager", *(f"submit-{seam}" for seam in SEAMS)],
)
def test_an_empty_secret_id_the_pass_needs_fails_closed_before_anything_runs(
    tmp_path: Path, mode: tuple[str], seam: str
) -> None:
    run = _run(tmp_path, *mode, **{seam: ""})
    assert run.returncode == 1
    variable = SEAMS[seam][0]
    assert run.stderr.strip().splitlines() == [
        f"FATAL: {variable} is empty: its secret doesn't exist yet, so this pass can't run"
    ]
    assert run.gits() == [] and run.nodes() == [] and run.secret_reads() == []
    assert run.pings == ([f"{PING_URL}/start", f"{PING_URL}/fail"] if mode else [])


@pytest.mark.parametrize("seam", [s for s in SEAMS if s != "CHANGE_WINDOW_CHANGE_MANAGER_UUID"])
def test_a_dry_run_needs_none_of_the_windows_other_secret_ids(tmp_path: Path, seam: str) -> None:
    run = _run(tmp_path, **{seam: ""})
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize("fault", ["BWS_REFUSES", "BWS_EMPTY"], ids=["refused", "empty"])
@pytest.mark.parametrize("uuid", list(SECRETS))
def test_an_unreadable_or_empty_secret_fails_the_pass_before_the_window(
    tmp_path: Path, fault: str, uuid: str
) -> None:
    run = _run(tmp_path, "--submit", **{fault: uuid})
    assert run.returncode == 1
    assert "FATAL: could not read the" in run.stderr
    assert "(the lane's token), or it is empty" in run.stderr
    assert run.nodes() == []
    assert run.send == []
    assert run.pings == [f"{PING_URL}/start", f"{PING_URL}/fail"]


@pytest.mark.parametrize("value", ['a"b', "a\\\\b", "a b"], ids=["quote", "backslash", "space"])
def test_a_sender_token_curls_config_cant_carry_fails_before_the_window(
    tmp_path: Path, value: str
) -> None:
    raw = json.dumps({"value": value})
    run = _run(tmp_path, "--submit", BWS_RAW_UUID=SENDER_UUID, BWS_RAW=raw)
    assert run.returncode == 1
    assert "holds a character curl's config can't carry" in run.stderr
    assert run.nodes() == [] and run.send == []


@pytest.mark.parametrize("mode", [(), ("--submit",)], ids=["bare", "submit"])
def test_a_missing_bundle_exits_2_before_any_secret_is_read(
    tmp_path: Path, mode: tuple[str]
) -> None:
    run = _run(tmp_path, *mode, FAKE_NO_BUNDLE="1")
    assert run.returncode == 2
    assert "has no dist/bundle/change-mgr-cli.js" in run.stderr
    assert run.secret_reads() == [] and run.nodes() == []
    assert run.pings == ([f"{PING_URL}/start", f"{PING_URL}/fail"] if mode else [])


def test_no_node_exits_2_before_any_secret_is_read(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit", NO_NODE="1")
    assert run.returncode == 2
    assert "FATAL: no node on PATH" in run.stderr
    assert run.secret_reads() == []


def test_a_failed_clone_exits_1_before_any_secret_is_read(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit", FAKE_CLONE_FAILS="1")
    assert run.returncode == 1
    assert "FATAL: could not clone https://github.com/AlobarQuest/infraops-mcp-server.git" in (
        run.stderr
    )
    assert run.secret_reads() == [] and run.nodes() == []
    assert run.pings == [f"{PING_URL}/start", f"{PING_URL}/fail"]


def test_without_the_lanes_token_it_refuses_and_calls_nothing(tmp_path: Path) -> None:
    """No Mac branch: without `BWS_ACCESS_TOKEN` it reads no Keychain, resolves no ping URL,
    clones nothing and runs nothing."""
    run = _run(tmp_path, "--submit", BWS_ACCESS_TOKEN="")
    assert run.returncode == 1
    assert run.stderr.strip().splitlines() == [
        "FATAL: no BWS_ACCESS_TOKEN: the change-window lane runs only in its container,"
        " through lane-run"
    ]
    assert run.argv == [] and run.pings == []


@pytest.mark.parametrize("argv", [["--dry-run"], ["--submit", "--submit"], ["submit"]])
def test_any_other_argument_is_refused_before_anything_runs(
    tmp_path: Path, argv: list[str]
) -> None:
    run = _run(tmp_path, *argv)
    assert run.returncode == 1
    assert "takes no argument or --submit alone" in run.stderr
    assert run.argv == [] and run.pings == []


# ---- the launcher's text ---------------------------------------------------------------------

# The four secret ids that are empty until their secrets exist (handoff "Facts"). A change that
# writes one also moves it out of this set.
EMPTY_IDS = {
    "COOLIFY_WINDOW_TOKEN_UUID",
    "CHANGE_MANAGER_EXECUTE_UUID",
    "ANTHROPIC_KEY_UUID",
    "DIGEST_SENDER_UUID",
}
_ID_LINE = re.compile(r'^([A-Z_]+_UUID)="\$\{(CHANGE_WINDOW_[A-Z_]+_UUID):-([^}]*)\}"$', re.M)


def test_the_four_secret_ids_are_the_empty_placeholders() -> None:
    ids = {name: (seam, default) for name, seam, default in _ID_LINE.findall(LAUNCHER.read_text())}
    assert {name: seam for name, (seam, _) in ids.items()} == {
        variable: seam for seam, (variable, _) in SEAMS.items()
    }
    assert {name for name, (_, default) in ids.items() if default == ""} == EMPTY_IDS


def test_the_launcher_reads_no_keychain_and_runs_no_gh_or_npm() -> None:
    code = "\n".join(
        line for line in LAUNCHER.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert "/usr/bin/security" not in code
    assert re.search(r"\b(gh|npm|npx)\b", code) is None


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "change-window")
    assert deploy.add_refusals(service, "change-window") == []
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["change-window"],
        "environment": {
            "BWS_ACCESS_TOKEN": "${CHANGE_WINDOW_BWS_ACCESS_TOKEN}",
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            # Empty until the lane's ping-URL secret exists (`test_deploy_lanes.py`'s
            # `placeholders`).
            "SDS_DEADMAN_PING_URL_UUID": "",
        },
        "restart": "unless-stopped",
    }
    assert set(yaml.safe_load(COMPOSE.read_text())) == {"services"}
    assert "change-window" not in deploy.STATEFUL_LANES
    assert deploy.LANE_CHECKS["change-window"] == "Change Manager OPs Check"


def test_the_crontab_runs_one_submitting_window_at_four() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["0 4 * * * lane-run change-window --submit"]
