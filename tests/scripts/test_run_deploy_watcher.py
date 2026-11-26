"""`scripts/run-deploy-watcher.sh` in the lane's container (ADR-0059 increment 3): which secrets it
reads through the lane's socket or token, the GitHub mint's exact arguments, that `gh` is never
asked, and that it refuses to run anywhere else.

The launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it: the lane's
token as `BWS_ACCESS_TOKEN`, or the secrets broker's socket as `SDS_SECRETS_SOCKET` (a stand-in
broker, `conftest.py`'s). `bws`, `gh` and `curl` are stubs on PATH; the venv's `deploy-watcher` and
`sds-github-mint` are stubs that log. Nothing reads the real BWS, GitHub or Healthchecks.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from deploy_watcher.workflows import ROLLOUT_WORKFLOWS
from scripts import deploy_orchestrator as deploy
from scripts.secrets_broker import load_allowlist

LAUNCHER = Path("scripts/run-deploy-watcher.sh")
COMPOSE = Path("lanes/deploy-watcher/compose.yaml")
CRONTAB = Path("lanes/deploy-watcher/crontab")
OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
CHANGE_MANAGER_UUID = "3b9503da-eb7e-401d-b4a7-b4a400c07efb"
READ_APP_KEY_UUID = "314a966c-5654-4e3c-8358-b4fd01268775"
PING_URL_KEEPER = "4f7f3d8b-a83e-4ae5-a665-b4ff0004b875"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
PING_URL = "https://hc.example/ping/deploy-watcher"
LANE_TOKEN = "a-lane-token"
MINTED = "minted-installation-token"
REPOSITORIES = "\n".join(sorted(ROLLOUT_WORKFLOWS))

# The `bws` stub logs the UUID and the token it was given, so a test can tell the identities
# apart. A UUID in `$BWS_REFUSES` fails; the ping-URL secret holds `PING_URL`; every other secret
# `value-of-<uuid>`.
_BWS = f"""#!/bin/sh
echo "$3 token=${{BWS_ACCESS_TOKEN:-}}" >> "$BWS_LOG"
case " $BWS_REFUSES " in *" $3 "*) exit 1 ;; esac
[ "$3" = "{PING_URL_UUID}" ] && {{ echo '{{"value": "{PING_URL}"}}'; exit 0; }}
printf '{{"value": "value-of-%s"}}' "$3"
"""
_GH = '#!/bin/sh\necho "gh $*" >> "$GH_LOG"\necho gh-token\n'
# A ping's URL arrives on stdin as curl's config; anything else fails.
_CURL = """#!/bin/sh
for argument in "$@"; do [ "$argument" = "-" ] && { cat >> "$PING_LOG"; exit 0; }; done
exit 1
"""
# `repositories` answers from the environment; `watch` and `recheck` log what they were given.
_WATCHER = """#!/bin/bash
if [ "$1" = "repositories" ]; then
  echo "repositories lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
  echo "repositories socket=${SDS_SECRETS_SOCKET:+set}" >> "$CHILD_LOG"
  printf '%s' "$FAKE_REPOSITORIES"
  exit "${FAKE_REPOSITORIES_RC:-0}"
fi
echo "$* github=$DEPLOY_WATCHER_GITHUB_TOKEN cm=$DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN \\
observer=$DEPLOY_WATCHER_ORCHESTRATOR_TOKEN lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
echo "$1 socket=${SDS_SECRETS_SOCKET:+set}" >> "$CHILD_LOG"
case "$1" in
  watch) exit "${FAKE_WATCH_RC:-0}" ;;
  recheck) exit "${FAKE_RECHECK_RC:-0}" ;;
esac
exit 64
"""
# The mint logs its arguments, what reached its stdin, and whether the lane token was in its
# environment.
_MINT = """#!/bin/bash
echo "mint $*" >> "$LANE_LOG"
echo "stdin=$(cat)" >> "$LANE_LOG"
echo "mint lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
echo "mint socket=${SDS_SECRETS_SOCKET:+set}" >> "$CHILD_LOG"
[ "${FAKE_MINT_RC:-0}" -eq 0 ] || { echo "FATAL: refused" >&2; exit "$FAKE_MINT_RC"; }
printf '%s' "$FAKE_MINTED"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(
    tmp_path: Path, *args: str, gh: str = _GH, **fake: str | None
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """`fake` overrides the environment; `None` removes a variable."""
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (LAUNCHER.name, "sds-deadman.sh", "sds-bws.sh"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    # `sds-bws.sh` parses with the venv's python.
    (venv / "python").symlink_to(sys.executable)
    _executable(venv / "deploy-watcher", _WATCHER)
    _executable(venv / "sds-github-mint", _MINT)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("bws", _BWS), ("gh", gh), ("curl", _CURL)):
        _executable(stubs / name, body)
    home = tmp_path / "home"
    home.mkdir()
    logs = {name: tmp_path / f"{name}.log" for name in ("lane", "bws", "gh", "ping", "child")}
    for log in logs.values():
        log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN": LANE_TOKEN,
        "SDS_DEADMAN_PING_URL_UUID": PING_URL_UUID,
        "FAKE_REPOSITORIES": REPOSITORIES,
        "FAKE_MINTED": MINTED,
        "LANE_LOG": str(logs["lane"]),
        "BWS_LOG": str(logs["bws"]),
        "GH_LOG": str(logs["gh"]),
        "PING_LOG": str(logs["ping"]),
        "CHILD_LOG": str(logs["child"]),
        **fake,
    }
    env = {k: v for k, v in env.items() if v is not None}
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
    return result, {name: log.read_text().splitlines() for name, log in logs.items()}


def _pings(lines: list[str]) -> list[str]:
    return [line.removeprefix('url = "').removesuffix('"') for line in lines]


def _watcher_runs(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith(("watch", "recheck"))]


CM = f"value-of-{CHANGE_MANAGER_UUID}"
OBSERVER = f"value-of-{OBSERVER_UUID}"


def test_the_pass_mints_its_github_token_with_exactly_these_arguments(tmp_path: Path) -> None:
    result, logs = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    repository_arguments = "".join(f" --repository {name}" for name in sorted(ROLLOUT_WORKFLOWS))
    assert logs["lane"] == [
        "repositories lane_token=",
        f"mint --app-id 5684758{repository_arguments}"
        " --permission metadata=read --permission contents=read"
        " --permission pull_requests=read --permission actions=read",
        # The key reaches the mint undecoded, in the form BWS holds it.
        f"stdin=value-of-{READ_APP_KEY_UUID}",
        "mint lane_token=",
        # `watch` takes the launcher's arguments; `recheck` none. The lane token is gone first.
        f"watch github={MINTED} cm={CM} observer={OBSERVER} lane_token=",
        f"recheck github={MINTED} cm={CM} observer={OBSERVER} lane_token=",
    ]


def test_every_read_uses_the_lanes_one_token_and_gh_is_never_asked(tmp_path: Path) -> None:
    result, logs = _run(tmp_path)
    assert result.returncode == 0
    assert logs["bws"] == [
        f"{PING_URL_UUID} token={LANE_TOKEN}",
        f"{CHANGE_MANAGER_UUID} token={LANE_TOKEN}",
        f"{OBSERVER_UUID} token={LANE_TOKEN}",
        f"{READ_APP_KEY_UUID} token={LANE_TOKEN}",
    ]
    assert logs["gh"] == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", PING_URL]
    for secret in (LANE_TOKEN, MINTED, CM, OBSERVER):
        assert secret not in result.stdout + result.stderr


def test_the_container_runs_the_images_own_code_with_no_activation(tmp_path: Path) -> None:
    result, _ = _run(tmp_path)
    assert "[activation]" not in result.stdout + result.stderr


def test_a_preset_github_token_is_replaced_by_the_mint(tmp_path: Path) -> None:
    _, logs = _run(tmp_path, DEPLOY_WATCHER_GITHUB_TOKEN="a-preset-token")
    runs = _watcher_runs(logs["lane"])
    assert len(runs) == 2
    assert all(f"github={MINTED}" in line for line in runs)


def test_dry_run_reaches_watch_and_not_recheck_and_arms_nothing(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    runs = _watcher_runs(logs["lane"])
    assert runs[0].startswith("watch --dry-run github=")
    assert runs[1].startswith("recheck github=")
    assert logs["ping"] == []


def test_a_failed_mint_exits_1_without_watching_or_asking_gh(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, FAKE_MINT_RC="1")
    assert result.returncode == 1
    assert _watcher_runs(logs["lane"]) == []
    assert logs["gh"] == []
    assert "could not mint the watcher's GitHub token" in result.stderr
    assert _pings(logs["ping"])[-1] == f"{PING_URL}/fail"


def test_an_unreadable_key_exits_1_without_watching(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, BWS_REFUSES=READ_APP_KEY_UUID)
    assert result.returncode == 1
    assert "stdin=" in logs["lane"]
    assert _watcher_runs(logs["lane"]) == []
    assert logs["gh"] == []


def test_an_empty_mint_answer_exits_1(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, FAKE_MINTED="")
    assert result.returncode == 1
    assert _watcher_runs(logs["lane"]) == []


def test_an_underivable_list_exits_3_with_no_mint(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, FAKE_REPOSITORIES="", FAKE_REPOSITORIES_RC="3")
    assert result.returncode == 3
    assert [line.split()[0] for line in logs["lane"]] == ["repositories"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])
    assert logs["gh"] == []
    assert _pings(logs["ping"])[-1] == f"{PING_URL}/fail"


def test_an_empty_list_with_exit_0_still_exits_3_with_no_mint(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, FAKE_REPOSITORIES="")
    assert result.returncode == 3
    assert [line.split()[0] for line in logs["lane"]] == ["repositories"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])


def test_a_derivation_that_cannot_run_passes_its_code_through_with_no_mint(
    tmp_path: Path,
) -> None:
    result, logs = _run(tmp_path, FAKE_REPOSITORIES_RC="127")
    assert result.returncode == 127
    assert [line.split()[0] for line in logs["lane"]] == ["repositories"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])


def test_an_unreadable_bearer_exits_1_before_the_derivation(tmp_path: Path) -> None:
    for uuid in (CHANGE_MANAGER_UUID, OBSERVER_UUID):
        run_dir = tmp_path / uuid
        run_dir.mkdir()
        result, logs = _run(run_dir, BWS_REFUSES=uuid)
        assert result.returncode == 1, uuid
        assert logs["lane"] == [], uuid
        assert _pings(logs["ping"]) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


def test_the_exit_codes_fold_as_before(tmp_path: Path) -> None:
    cases = [
        ({"FAKE_WATCH_RC": "2"}, 2),
        ({"FAKE_WATCH_RC": "2", "FAKE_RECHECK_RC": "3"}, 3),
        ({"FAKE_WATCH_RC": "3", "FAKE_RECHECK_RC": "1"}, 1),
        ({"FAKE_RECHECK_RC": "127"}, 127),
    ]
    for index, (fake, expected) in enumerate(cases):
        run_dir = tmp_path / str(index)
        run_dir.mkdir()
        result, logs = _run(run_dir, **fake)
        assert result.returncode == expected, fake
        assert len(_watcher_runs(logs["lane"])) == 2


def test_without_a_ping_url_secret_a_pass_pings_nothing(tmp_path: Path) -> None:
    """An empty `SDS_DEADMAN_PING_URL_UUID` falls through to the name lookup, which
    needs the broad identity `lane-run` doesn't pass. The pass runs, and its alerting is disabled.
    `/usr/bin/security` is asked here (macOS has it, the image doesn't), so the stand-in broad
    identity keeps the real Keychain out of the test."""
    result, logs = _run(tmp_path, SDS_DEADMAN_PING_URL_UUID="", BWS_ACCESS_TOKEN_BROAD="broad")
    assert result.returncode == 0
    assert logs["ping"] == []
    assert "alerting disabled this run" in result.stdout
    assert len(_watcher_runs(logs["lane"])) == 2


def test_preset_bearers_are_kept_and_only_the_mint_key_is_read(tmp_path: Path) -> None:
    result, logs = _run(
        tmp_path,
        DEPLOY_WATCHER_CHANGE_MANAGER_TOKEN="cm-preset",
        DEPLOY_WATCHER_ORCHESTRATOR_TOKEN="observer-preset",
    )
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == [
        f"{PING_URL_UUID} token={LANE_TOKEN}",
        f"{READ_APP_KEY_UUID} token={LANE_TOKEN}",
    ]
    assert _watcher_runs(logs["lane"])[0] == (
        f"watch github={MINTED} cm=cm-preset observer=observer-preset lane_token="
    )


@pytest.mark.parametrize("token", [None, ""], ids=["unset", "empty"])
def test_outside_the_container_it_refuses_and_runs_nothing(
    tmp_path: Path, token: str | None
) -> None:
    result, logs = _run(tmp_path, BWS_ACCESS_TOKEN=token)
    assert result.returncode == 1
    assert "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN" in result.stderr
    assert "the deploy-watcher lane runs only in its container, through lane-run" in result.stderr
    assert logs == {"lane": [], "bws": [], "gh": [], "ping": [], "child": []}


def test_a_socket_variable_naming_no_socket_refuses_and_runs_nothing(tmp_path: Path) -> None:
    """`SDS_SECRETS_SOCKET` alone isn't the container: it must name a socket."""
    not_a_socket = tmp_path / "broker.sock"
    not_a_socket.write_text("")
    result, logs = _run(tmp_path, BWS_ACCESS_TOKEN=None, SDS_SECRETS_SOCKET=str(not_a_socket))
    assert result.returncode == 1
    assert "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN" in result.stderr
    assert logs == {"lane": [], "bws": [], "gh": [], "ping": [], "child": []}


# ---- the container on the secrets broker (ADR-0055 amendment 30) ------------------------------


def _brokered(
    tmp_path: Path, socket_path: Path, *args: str
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """The container as `lane-run` starts it on the broker: no token, and the socket's marker."""
    return _run(tmp_path, *args, BWS_ACCESS_TOKEN=None, SDS_SECRETS_SOCKET=str(socket_path))


def _served() -> dict[str, str | None]:
    return {
        CHANGE_MANAGER_UUID: CM,
        OBSERVER_UUID: OBSERVER,
        READ_APP_KEY_UUID: f"value-of-{READ_APP_KEY_UUID}",
        PING_URL_UUID: PING_URL,
    }


def test_on_the_socket_without_a_token_a_pass_reads_every_secret_through_it(
    tmp_path: Path, broker
) -> None:
    """The socket serves the ping URL, both bearers and the App's key; `bws` never runs, and no
    child gets the lane's token or the socket's marker."""
    result, logs = _brokered(tmp_path, broker(_served()))
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == []
    assert logs["gh"] == []
    assert f"stdin=value-of-{READ_APP_KEY_UUID}" in logs["lane"]
    assert _watcher_runs(logs["lane"]) == [
        f"watch github={MINTED} cm={CM} observer={OBSERVER} lane_token=",
        f"recheck github={MINTED} cm={CM} observer={OBSERVER} lane_token=",
    ]
    assert logs["child"] == [
        "repositories socket=",
        "mint socket=",
        "watch socket=",
        "recheck socket=",
    ]
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", PING_URL]


@pytest.mark.parametrize(
    ("refused", "reason"),
    [
        (CHANGE_MANAGER_UUID, "bws_failed"),
        (OBSERVER_UUID, "not_allowed"),
        (READ_APP_KEY_UUID, "not_allowed"),
    ],
    ids=["change-manager-read-failed", "observer-not-on-the-allowlist", "key-not-on-the-allowlist"],
)
def test_a_secret_the_socket_refuses_fails_the_pass(
    tmp_path: Path, broker, refused: str, reason: str
) -> None:
    served = _served()
    if reason == "bws_failed":
        served[refused] = None
    else:
        del served[refused]
    result, logs = _brokered(tmp_path, broker(served))
    assert result.returncode == 1
    assert f"the secrets broker gave no value for {refused}: {reason}" in result.stderr
    assert logs["bws"] == []
    assert _watcher_runs(logs["lane"]) == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


def test_a_dead_socket_fails_the_pass_and_never_falls_back_to_bws(
    tmp_path: Path, dead_socket: Path
) -> None:
    result, logs = _brokered(tmp_path, dead_socket)
    assert result.returncode == 1
    assert f"the secrets broker gave no value for {CHANGE_MANAGER_UUID}: no_reply" in result.stderr
    assert "FATAL: could not read the change-manager credential" in result.stderr
    assert logs["bws"] == []
    assert logs["lane"] == []


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "deploy-watcher")
    # A broker lane: it mounts its socket and the allowlist names it.
    allowlist = load_allowlist(Path(deploy.BROKER_ALLOWLIST))
    assert deploy.broker_lane(service, "deploy-watcher", allowlist)
    assert deploy.add_refusals(service, "deploy-watcher", allowlist) == []
    assert deploy.compose_broker_refusals(COMPOSE.read_text(), "deploy-watcher") == []
    assert allowlist["deploy-watcher"] == {
        CHANGE_MANAGER_UUID,
        OBSERVER_UUID,
        READ_APP_KEY_UUID,
        PING_URL_KEEPER,
    }
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["deploy-watcher"],
        # No `BWS_ACCESS_TOKEN`: the lane reads through its secrets-broker socket.
        "environment": {
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            # The BWS secret id of `deploy-watcher-ping-url`, not a secret and not the ping URL.
            "SDS_DEADMAN_PING_URL_UUID": PING_URL_KEEPER,
        },
        "volumes": ["/data/sds/broker/deploy-watcher:/run/sds-broker:ro"],
        "restart": "unless-stopped",
    }
    assert set(yaml.safe_load(COMPOSE.read_text())) == {"services"}
    assert "deploy-watcher" not in deploy.STATEFUL_LANES


def test_the_crontab_runs_the_pass_hourly() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["20 * * * * lane-run deploy-watcher"]


def test_the_repositorys_manifest_lists_the_watchers_secrets() -> None:
    manifest = Path(".bws-secrets.toml").read_text()
    ping_url_secret = PING_URL_KEEPER
    assert deploy.lane_secrets(manifest, "deploy-watcher") == [
        OBSERVER_UUID,
        CHANGE_MANAGER_UUID,
        READ_APP_KEY_UUID,
        ping_url_secret,
    ]
    # The launcher names each secret it reads, except the ping-URL secret, whose id reaches it
    # from the compose through `lane-run`.
    launcher = LAUNCHER.read_text()
    for uuid in deploy.lane_secrets(manifest, "deploy-watcher"):
        assert uuid in launcher or uuid == ping_url_secret, uuid
