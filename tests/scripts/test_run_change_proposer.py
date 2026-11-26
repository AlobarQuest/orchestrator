"""`scripts/run-change-proposer.sh` in the lane's container, and the lane's files.

The real launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it, with
`bws`, `gh`, `curl` and the tools stubbed: no credential is read and nothing leaves the machine.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import deploy_orchestrator as deploy
from scripts.secrets_broker import load_allowlist
from tests.scripts.test_run_unit_verifier import lane_start_minutes

LAUNCHER = Path("scripts/run-change-proposer.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh")
PROPOSE_UUID = "acccb346-4baa-43ec-a1d4-b4a400c048ee"


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


# ---- the lane's container (ADR-0059 increment 4) ---------------------------------------------
#
# The launcher runs under `env -i` with what `lane-run` passes it: the lane's token as
# `BWS_ACCESS_TOKEN`, a ping-URL secret id, PATH and HOME. `bws`, `gh` and `curl` are stubs on
# PATH; the venv's `change-proposer` (the derivation and the pass) and `sds-github-mint` are stubs
# that log. Nothing reads BWS, GitHub, the member record or Healthchecks.

OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
READ_APP_KEY_UUID = "314a966c-5654-4e3c-8358-b4fd01268775"
PING_URL_KEEPER = "7651ccc2-7e13-417b-86da-b50100c0d4b2"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
PING_URL = "https://hc.example/ping/change-proposer"
LANE_TOKEN = "a-lane-token"
MINTED = "minted-installation-token"
DERIVED = "alobarquest/brain\nalobarquest/change-manager"
OBSERVER = f"value-of-{OBSERVER_UUID}"
PROPOSE = f"value-of-{PROPOSE_UUID}"
SECRET_UUIDS = (PROPOSE_UUID, OBSERVER_UUID, READ_APP_KEY_UUID)
SECRETS = (LANE_TOKEN, MINTED, OBSERVER, PROPOSE, f"value-of-{READ_APP_KEY_UUID}")
COMPOSE = Path("lanes/change-proposer/compose.yaml")
CRONTAB = Path("lanes/change-proposer/crontab")
# The `bws` stub logs the UUID and the token it was given. A UUID in `$BWS_REFUSES` fails; the
# ping-URL secret holds `PING_URL`; every other secret `value-of-<uuid>`.
_LANE_BWS = f"""#!/bin/sh
echo "$3 token=${{BWS_ACCESS_TOKEN:-}}" >> "$BWS_LOG"
case " $BWS_REFUSES " in *" $3 "*) exit 1 ;; esac
[ "$3" = "{PING_URL_UUID}" ] && {{ echo '{{"value": "{PING_URL}"}}'; exit 0; }}
printf '{{"value": "value-of-%s"}}' "$3"
"""
_LANE_GH = '#!/bin/sh\necho "gh $*" >> "$GH_LOG"\necho gh-token\n'
# A ping's URL arrives on stdin as curl's config; anything else fails.
_LANE_CURL = """#!/bin/sh
for argument in "$@"; do [ "$argument" = "-" ] && { cat >> "$PING_LOG"; exit 0; }; done
exit 1
"""
# `repositories` is the derivation, answered from the environment; anything else is the pass.
_LANE_TOOL = """#!/bin/bash
if [ "$*" = "repositories" ]; then
  echo "derive observer=${SDS_MEMBERS_OBSERVER_TOKEN:-} lane_token=${BWS_ACCESS_TOKEN:+set}" \\
    >> "$LANE_LOG"
  echo "derive socket=${SDS_SECRETS_SOCKET:+set}" >> "$CHILD_LOG"
  printf '%s' "$FAKE_DERIVED"
  exit "${FAKE_DERIVE_RC:-0}"
fi
echo "pass args=$* github=${CHANGE_PROPOSER_GITHUB_TOKEN:-} \\
observer=${SDS_MEMBERS_OBSERVER_TOKEN:-} cm=${CHANGE_PROPOSER_CHANGE_MANAGER_TOKEN:-unset} \\
lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
echo "pass socket=${SDS_SECRETS_SOCKET:+set}" >> "$CHILD_LOG"
echo "the program's report"
exit "${FAKE_PASS_RC:-0}"
"""
# The mint logs its arguments, what reached its stdin, and whether the lane token reached it.
_LANE_MINT = """#!/bin/bash
echo "mint $*" >> "$LANE_LOG"
echo "stdin=$(cat)" >> "$LANE_LOG"
echo "mint lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
echo "mint socket=${SDS_SECRETS_SOCKET:+set}" >> "$CHILD_LOG"
[ "${FAKE_MINT_RC:-0}" -eq 0 ] || { echo "FATAL: refused" >&2; exit "$FAKE_MINT_RC"; }
printf '%s' "$FAKE_MINTED"
"""
MINT_CALL = (
    "mint --app-id 5684758 --repository alobarquest/brain --repository alobarquest/change-manager"
    " --permission metadata=read --permission contents=read --permission pull_requests=read"
)


def _lane(
    tmp_path: Path, *args: str, **fake: str | None
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]]]:
    """`fake` overrides the environment; `None` removes a variable."""
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    # `sds-bws.sh` parses BWS's JSON with the venv's interpreter, for real.
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    _executable(venv / "change-proposer", _LANE_TOOL)
    _executable(venv / "sds-github-mint", _LANE_MINT)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("bws", _LANE_BWS), ("gh", _LANE_GH), ("curl", _LANE_CURL)):
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
        "FAKE_DERIVED": DERIVED,
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


def _passes(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("pass ")]


def test_a_submitting_pass_mints_its_github_token_with_exactly_these_arguments(
    tmp_path: Path,
) -> None:
    result, logs = _lane(tmp_path, "--submit")
    assert result.returncode == 0, result.stderr
    assert logs["lane"] == [
        # The derivation reads the member record with the observer bearer, without the lane token.
        f"derive observer={OBSERVER} lane_token=",
        MINT_CALL,
        # The key reaches the mint undecoded, in the form BWS holds it, and the lane token doesn't.
        f"stdin=value-of-{READ_APP_KEY_UUID}",
        "mint lane_token=",
        # The pass runs on the minted token, both bearers, and no lane token.
        f"pass args=--submit github={MINTED} observer={OBSERVER} cm={PROPOSE} lane_token=",
    ]


def test_a_submitting_pass_reads_every_secret_with_the_lanes_one_token(tmp_path: Path) -> None:
    result, logs = _lane(tmp_path, "--submit")
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == [
        f"{PING_URL_UUID} token={LANE_TOKEN}",
        f"{PROPOSE_UUID} token={LANE_TOKEN}",
        f"{OBSERVER_UUID} token={LANE_TOKEN}",
        f"{READ_APP_KEY_UUID} token={LANE_TOKEN}",
    ]
    assert logs["gh"] == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", PING_URL]
    for secret in SECRETS:
        assert secret not in result.stdout + result.stderr


def test_a_dry_run_reads_no_propose_bearer_still_mints_and_pings_nothing(tmp_path: Path) -> None:
    """A bare run is the lane's dry run (ADR-0059 cut-over, reporting mode): it reads what it
    reports from and mints its read token, but never the credential that could write, and it
    neither reads the ping-URL secret nor pings."""
    result, logs = _lane(tmp_path)
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == [
        f"{OBSERVER_UUID} token={LANE_TOKEN}",
        f"{READ_APP_KEY_UUID} token={LANE_TOKEN}",
    ]
    assert MINT_CALL in logs["lane"]
    assert _passes(logs["lane"]) == [
        f"pass args= github={MINTED} observer={OBSERVER} cm=unset lane_token="
    ]
    assert logs["ping"] == []
    assert logs["gh"] == []


def test_the_container_skips_activation_rather_than_logging_it_missing(tmp_path: Path) -> None:
    result, _ = _lane(tmp_path, "--submit")
    assert "[activation]" not in result.stdout + result.stderr


def test_a_preset_github_token_is_replaced_by_the_mint(tmp_path: Path) -> None:
    _, logs = _lane(tmp_path, "--submit", CHANGE_PROPOSER_GITHUB_TOKEN="a-preset-token")
    assert f"github={MINTED} " in _passes(logs["lane"])[0]


@pytest.mark.parametrize("refused", [PROPOSE_UUID, OBSERVER_UUID])
def test_an_unreadable_bearer_exits_1_before_the_derivation(tmp_path: Path, refused: str) -> None:
    result, logs = _lane(tmp_path, "--submit", BWS_REFUSES=refused)
    assert result.returncode == 1
    assert "(the lane's socket or token)" in result.stderr
    assert logs["lane"] == []
    assert logs["gh"] == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry-run", "submit"])
def test_a_failed_mint_exits_1_without_a_pass_or_asking_gh(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    result, logs = _lane(tmp_path, *args, FAKE_MINT_RC="1")
    assert result.returncode == 1
    assert _passes(logs["lane"]) == []
    assert logs["gh"] == []
    assert "could not mint the change proposer's GitHub token" in result.stderr
    assert "sds-github-mint exited 1" in result.stderr


def test_an_unreadable_key_exits_1_without_a_pass(tmp_path: Path) -> None:
    result, logs = _lane(tmp_path, "--submit", BWS_REFUSES=READ_APP_KEY_UUID)
    assert result.returncode == 1
    assert "stdin=" in logs["lane"]
    assert _passes(logs["lane"]) == []
    assert logs["gh"] == []


def test_an_empty_mint_answer_exits_1(tmp_path: Path) -> None:
    result, logs = _lane(tmp_path, "--submit", FAKE_MINTED="")
    assert result.returncode == 1
    assert _passes(logs["lane"]) == []


@pytest.mark.parametrize(
    ("derived", "derive_rc"), [("", "2"), (DERIVED, "2"), ("", "0")], ids=["2", "2-out", "empty"]
)
def test_an_underivable_list_exits_2_with_no_mint(
    tmp_path: Path, derived: str, derive_rc: str
) -> None:
    result, logs = _lane(tmp_path, "--submit", FAKE_DERIVED=derived, FAKE_DERIVE_RC=derive_rc)
    assert result.returncode == 2
    assert [line.split()[0] for line in logs["lane"]] == ["derive"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])
    assert logs["gh"] == []
    assert "nothing was minted or proposed" in result.stderr
    assert _pings(logs["ping"])[-1] == f"{PING_URL}/fail"


@pytest.mark.parametrize("derive_rc", ["1", "127"])
def test_a_derivation_that_fails_otherwise_passes_its_code_through_with_no_mint(
    tmp_path: Path, derive_rc: str
) -> None:
    result, logs = _lane(tmp_path, "--submit", FAKE_DERIVE_RC=derive_rc)
    assert result.returncode == int(derive_rc)
    assert [line.split()[0] for line in logs["lane"]] == ["derive"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])
    assert f"change-proposer repositories exited {derive_rc}" in result.stderr


def test_without_a_ping_url_secret_a_submitting_pass_still_runs_and_pings_nothing(
    tmp_path: Path,
) -> None:
    """An empty `SDS_DEADMAN_PING_URL_UUID` falls through to the name lookup, which
    needs the broad identity `lane-run` doesn't pass. `/usr/bin/security` is asked there (macOS
    has it, the image doesn't), so the stand-in broad identity keeps the real Keychain out of the
    test; in the container nothing would read the Healthchecks key at all."""
    result, logs = _lane(
        tmp_path, "--submit", SDS_DEADMAN_PING_URL_UUID="", BWS_ACCESS_TOKEN_BROAD="broad"
    )
    assert result.returncode == 0, result.stderr
    assert logs["ping"] == []
    assert "alerting disabled this run" in result.stdout
    assert len(_passes(logs["lane"])) == 1
    # Only the stand-in reads the Healthchecks key; the lane's token reads the three secrets.
    assert [line for line in logs["bws"] if line.endswith(f"token={LANE_TOKEN}")] == [
        f"{PROPOSE_UUID} token={LANE_TOKEN}",
        f"{OBSERVER_UUID} token={LANE_TOKEN}",
        f"{READ_APP_KEY_UUID} token={LANE_TOKEN}",
    ]


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_the_programs_code_is_the_passs_code(tmp_path: Path, code: int) -> None:
    result, logs = _lane(tmp_path, "--submit", FAKE_PASS_RC=str(code))
    assert result.returncode == code
    assert len(_passes(logs["lane"])) == 1
    assert _pings(logs["ping"])[-1] == (PING_URL if code == 0 else f"{PING_URL}/fail")


@pytest.mark.parametrize("token", [None, ""], ids=["unset", "empty"])
@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["dry", "submit"])
def test_outside_the_container_it_refuses_and_reads_and_runs_nothing(
    tmp_path: Path, args: tuple[str, ...], token: str | None
) -> None:
    result, logs = _lane(tmp_path, *args, BWS_ACCESS_TOKEN=token)
    assert result.returncode == 1, result.stderr
    assert (
        "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN: the change-proposer lane runs only "
        "in its container, through lane-run"
    ) in result.stdout + result.stderr
    assert logs == {"lane": [], "bws": [], "gh": [], "ping": [], "child": []}


def test_a_socket_variable_naming_no_socket_refuses_and_reads_nothing(tmp_path: Path) -> None:
    """`SDS_SECRETS_SOCKET` alone isn't the container: it must name a socket."""
    not_a_socket = tmp_path / "broker.sock"
    not_a_socket.write_text("")
    result, logs = _lane(
        tmp_path, "--submit", BWS_ACCESS_TOKEN=None, SDS_SECRETS_SOCKET=str(not_a_socket)
    )
    assert result.returncode == 1
    assert "FATAL: no secrets-broker socket or BWS_ACCESS_TOKEN" in result.stderr
    assert logs == {"lane": [], "bws": [], "gh": [], "ping": [], "child": []}


# ---- the container on the secrets broker (ADR-0055 amendment 30) ------------------------------


def _brokered(
    tmp_path: Path, socket_path: Path, *args: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]]]:
    """The container as `lane-run` starts it on the broker: no token, and the socket's marker."""
    return _lane(tmp_path, *args, BWS_ACCESS_TOKEN=None, SDS_SECRETS_SOCKET=str(socket_path))


def _served() -> dict[str, str | None]:
    return {
        PROPOSE_UUID: PROPOSE,
        OBSERVER_UUID: OBSERVER,
        READ_APP_KEY_UUID: f"value-of-{READ_APP_KEY_UUID}",
        PING_URL_UUID: PING_URL,
    }


def test_on_the_socket_without_a_token_a_submitting_pass_reads_every_secret_through_it(
    tmp_path: Path, broker
) -> None:
    """The socket serves the ping URL, both bearers and the App's key; `bws` never runs, and no
    child gets the lane's token or the socket's marker."""
    result, logs = _brokered(tmp_path, broker(_served()), "--submit")
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == []
    assert logs["gh"] == []
    assert f"stdin=value-of-{READ_APP_KEY_UUID}" in logs["lane"]
    assert _passes(logs["lane"]) == [
        f"pass args=--submit github={MINTED} observer={OBSERVER} cm={PROPOSE} lane_token="
    ]
    assert logs["child"] == ["derive socket=", "mint socket=", "pass socket="]
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", PING_URL]
    for secret in SECRETS:
        assert secret not in result.stdout + result.stderr


def test_on_the_socket_a_dry_run_reads_no_propose_bearer_and_pings_nothing(
    tmp_path: Path, broker
) -> None:
    served = _served()
    del served[PROPOSE_UUID]
    result, logs = _brokered(tmp_path, broker(served))
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == []
    assert _passes(logs["lane"]) == [
        f"pass args= github={MINTED} observer={OBSERVER} cm=unset lane_token="
    ]
    assert logs["child"] == ["derive socket=", "mint socket=", "pass socket="]
    assert logs["ping"] == []


@pytest.mark.parametrize(
    ("refused", "reason"),
    [
        (PROPOSE_UUID, "bws_failed"),
        (OBSERVER_UUID, "not_allowed"),
        (READ_APP_KEY_UUID, "not_allowed"),
    ],
    ids=["propose-read-failed", "observer-not-on-the-allowlist", "key-not-on-the-allowlist"],
)
def test_a_secret_the_socket_refuses_fails_the_pass(
    tmp_path: Path, broker, refused: str, reason: str
) -> None:
    served = _served()
    if reason == "bws_failed":
        served[refused] = None
    else:
        del served[refused]
    result, logs = _brokered(tmp_path, broker(served), "--submit")
    assert result.returncode == 1
    assert f"the secrets broker gave no value for {refused}: {reason}" in result.stderr
    assert logs["bws"] == []
    assert _passes(logs["lane"]) == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


def test_a_dead_socket_fails_the_pass_and_never_falls_back_to_bws(
    tmp_path: Path, dead_socket: Path
) -> None:
    result, logs = _brokered(tmp_path, dead_socket, "--submit")
    assert result.returncode == 1
    assert f"the secrets broker gave no value for {PROPOSE_UUID}: no_reply" in result.stderr
    assert "FATAL: could not read the propose-scoped change-manager credential" in result.stderr
    assert logs["bws"] == []
    assert logs["lane"] == []


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "change-proposer")
    # A broker lane: it mounts its socket and the allowlist names it.
    allowlist = load_allowlist(Path(deploy.BROKER_ALLOWLIST))
    assert deploy.broker_lane(service, "change-proposer", allowlist)
    assert deploy.add_refusals(service, "change-proposer", allowlist) == []
    assert deploy.compose_broker_refusals(COMPOSE.read_text(), "change-proposer") == []
    assert allowlist["change-proposer"] == {
        PROPOSE_UUID,
        OBSERVER_UUID,
        READ_APP_KEY_UUID,
        PING_URL_KEEPER,
    }
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["change-proposer"],
        # No `BWS_ACCESS_TOKEN`: the lane reads through its secrets-broker socket.
        "environment": {
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            # The BWS secret id of `change-proposer-ping-url`, not a secret and not the ping URL.
            "SDS_DEADMAN_PING_URL_UUID": PING_URL_KEEPER,
        },
        "volumes": ["/data/sds/broker/change-proposer:/run/sds-broker:ro"],
        "restart": "unless-stopped",
    }
    assert set(yaml.safe_load(COMPOSE.read_text())) == {"services"}
    # Stateless (ADR-0059 decision 1): no state volume.
    assert "change-proposer" not in deploy.STATEFUL_LANES
    assert deploy.LANE_CHECKS["change-proposer"] == "sds-change-proposer"


def test_the_crontab_runs_a_submitting_pass_hourly() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["35 * * * * lane-run change-proposer --submit"]


def test_lane_run_passes_the_submit_flag_through_to_the_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import lane_run

    seen: list[list[str]] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(lane_run.subprocess, "run", run)
    assert lane_run.run_launcher("change-proposer", ["--submit"], {}) == 0
    assert seen == [[str(lane_run.LAUNCHER_DIR / LAUNCHER.name), "--submit"]]


def test_no_two_lanes_start_a_pass_in_the_same_minute() -> None:
    """Minute 35 is clear of execution-watch's quarter-hours, the unit verifier's 2-52/10, the
    deploy watcher's 20, the pin watcher's 25, the landing ledger's 30, the revision watcher's 40
    and the membership check's 47."""
    minutes = lane_start_minutes()
    assert minutes["change-proposer"] == {35}
    for lane, taken in minutes.items():
        if lane != "change-proposer":
            assert 35 not in taken, lane


def test_the_repositorys_manifest_lists_the_lanes_secrets() -> None:
    """The lane's `--token` proof reads what the manifest lists for it; the launcher names each
    secret it reads, except the ping-URL secret, whose id reaches it from the compose through
    `lane-run`."""
    manifest = Path(".bws-secrets.toml").read_text()
    ping_url_secret = PING_URL_KEEPER
    secrets = deploy.lane_secrets(manifest, "change-proposer")
    assert sorted(secrets) == sorted(
        [OBSERVER_UUID, PROPOSE_UUID, READ_APP_KEY_UUID, ping_url_secret]
    )
    launcher = LAUNCHER.read_text()
    for uuid in secrets:
        assert uuid in launcher or uuid == ping_url_secret, uuid
