"""`scripts/run-inert-landing.sh` in the lane's container and on the Mac, and the lane's files.

The real launcher runs under `env -i` from a throwaway tree, with what `lane-run` passes it in the
container: the lane's token as `BWS_ACCESS_TOKEN` (or, on the secrets broker, `SDS_SECRETS_SOCKET`
naming a stand-in broker's socket, `conftest.py`'s), the state directory, `PATH` and `HOME`. `bws`,
`gh` and `curl` are stubs on PATH. In the venv, `inert-landing` and `sds-github-mint` are stubs that
log what reached them, and `python` is the real interpreter (`sds-bws.sh` parses BWS's JSON with
it). On the Mac, `scripts/sds-token.sh` is a stub that exports a stand-in, and the broad identity
is a stand-in too, so no test reads the Keychain. Nothing reads BWS, GitHub, change-manager, the
orchestrator, alert-hub or Healthchecks.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import deploy_orchestrator as deploy
from scripts import lane_run
from tests.scripts.test_run_unit_verifier import lane_start_minutes

LAUNCHER = Path("scripts/run-inert-landing.sh")
HELPERS = ("sds-deadman.sh", "sds-bws.sh")
COMPOSE = Path("lanes/inert-landing/compose.yaml")
CRONTAB = Path("lanes/inert-landing/crontab")
PLIST = Path("scripts/com.devon.inert-landing.plist")

READ_UUID = "314f276d-55ca-4ddc-a24d-b4a3013508cd"
SYSTEM_UUID = "221a48d5-3f29-4898-b300-b4820140c880"
ALERT_UUID = "b415d03b-f92d-46b1-8a9d-b4f10103df49"
READ_APP_KEY_UUID = "314a966c-5654-4e3c-8358-b4fd01268775"
HEALTHCHECKS_KEY_UUID = "51509d5e-64f5-4781-ae82-b4ed00b644e1"
# The order the container reads them in: the policy's credential, which the repository list
# needs, then the App key the mint needs, then the bearer only the pass uses.
EVERY_PASS = (READ_UUID, READ_APP_KEY_UUID, SYSTEM_UUID)
LANE_SECRETS = (*EVERY_PASS, ALERT_UUID)

LANE_TOKEN = "a-lane-token"
READ_TOKEN = "ghs-minted-read-token"
BROAD_STANDIN = "broad-stand-in"
NARROW_STANDIN = "narrow-stand-in"
REPOSITORIES = ("alobarquest/agent-sites", "alobarquest/crm")


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
# inert-landing: `repositories` logs what it was handed and prints the lane's list; a pass logs its
# argv and every variable it was handed, one per line.
_LANDER = """#!/bin/bash
if [ "$1" = repositories ]; then
  echo "derive args=$* lane_token=${BWS_ACCESS_TOKEN:+set} \\
socket=${SDS_SECRETS_SOCKET:+set} \\
cm=${INERT_LANDING_CHANGE_MANAGER_TOKEN:-} system=${INERT_LANDING_ORCHESTRATOR_TOKEN:-}" \\
    >> "$LANE_LOG"
  if [ -n "${FAKE_DERIVE_RC:-}" ]; then
    echo "[UNUSABLE] the landing policy could not be read" >&2
    exit "$FAKE_DERIVE_RC"
  fi
  printf '%s' "${FAKE_REPOSITORIES-alobarquest/agent-sites
alobarquest/crm}"
  exit 0
fi
{
  echo "lander args=$*"
  env | sort | sed 's/^/lander-env /'
} >> "$LANE_LOG"
exit "${FAKE_LANDER_RC:-0}"
"""
_MINT = """#!/bin/bash
key="$(cat)"
echo "mint args=$* key=$key lane_token=${BWS_ACCESS_TOKEN:+set} \\
socket=${SDS_SECRETS_SOCKET:+set}" >> "$LANE_LOG"
[ -n "${FAKE_MINT_RC:-}" ] && exit "$FAKE_MINT_RC"
echo "%s"
"""
# The Mac's narrow identity, as `sds-token.sh` exports it, without the Keychain.
_SDS_TOKEN = (
    f'if [ -z "${{BWS_ACCESS_TOKEN:-}}" ]; then export BWS_ACCESS_TOKEN={NARROW_STANDIN}; fi\n'
)


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


class Run:
    def __init__(self, result: subprocess.CompletedProcess[str], logs: dict) -> None:
        self.result = result
        self.logs = logs

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
        """The variables the lander's pass was handed."""
        pairs = (line.removeprefix("lander-env ") for line in self.lines("lander-env"))
        return dict(pair.split("=", 1) for pair in pairs)

    def passes(self) -> list[str]:
        return self.lines("lander")


def _run(tmp_path: Path, *args: str, **fake: str | None) -> Run:
    """A `None` in `fake` removes that variable from the launcher's environment."""
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "scripts" / LAUNCHER.name)
    for helper in HELPERS:
        shutil.copy(Path("scripts") / helper, root / "scripts" / helper)
    (root / "scripts" / "sds-token.sh").write_text(_SDS_TOKEN)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    _executable(venv / "inert-landing", _LANDER)
    _executable(venv / "sds-github-mint", _MINT % READ_TOKEN)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("bws", _BWS), ("gh", _GH), ("curl", _CURL)):
        _executable(stubs / name, body)
    home = tmp_path / "home"
    home.mkdir()
    logs = {name: tmp_path / f"{name}.log" for name in ("lane", "bws", "gh", "ping")}
    for log in logs.values():
        log.write_text("")
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "BWS_ACCESS_TOKEN": LANE_TOKEN,
        "SDS_DEADMAN_PING_URL_UUID": "",
        "LANDER_STATE_DIR": str(tmp_path / "state"),
        # The dead-man helper's name lookup, with no ping-URL id, asks for the broad identity; the
        # stand-in keeps the real Keychain out of the test. The container has neither the
        # Keychain nor this variable, which `lane-run` never passes.
        "BWS_ACCESS_TOKEN_BROAD": BROAD_STANDIN,
        "LANE_LOG": str(logs["lane"]),
        "BWS_LOG": str(logs["bws"]),
        "GH_LOG": str(logs["gh"]),
        "PING_LOG": str(logs["ping"]),
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
    return Run(result, {name: log.read_text().splitlines() for name, log in logs.items()})


def _mac(tmp_path: Path, *args: str, **fake: str | None) -> Run:
    return _run(tmp_path, *args, BWS_ACCESS_TOKEN=None, LANDER_STATE_DIR=None, **fake)


# ---- the secrets, read with the lane's one token ---------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected"),
    [((), EVERY_PASS), (("--submit",), LANE_SECRETS)],
    ids=["bare", "submit"],
)
def test_each_pass_reads_its_secrets_with_the_lanes_one_token(
    tmp_path: Path, args: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    assert run.code == 0, run.output
    assert run.lane_reads() == list(expected)
    # The only other read is the dead-man helper's Healthchecks key, by the broad stand-in that
    # exists only in this test.
    others = [(uuid, token) for uuid, token in run.reads() if token != LANE_TOKEN]
    assert others == [(HEALTHCHECKS_KEY_UUID, BROAD_STANDIN)]
    for uuid in LANE_SECRETS:
        assert value(uuid) not in run.output
    assert LANE_TOKEN not in run.output and READ_TOKEN not in run.output


def test_with_a_ping_url_named_the_healthchecks_key_is_never_read(tmp_path: Path) -> None:
    """The container's dead-man reads its own ping-URL secret and nothing else of its own."""
    ping_url_secret = "11111111-2222-4333-8444-555555555555"
    run = _run(
        tmp_path, "--submit", SDS_DEADMAN_PING_URL_UUID=ping_url_secret, BWS_ACCESS_TOKEN_BROAD=None
    )
    assert run.code == 0, run.output
    assert run.lane_reads() == [ping_url_secret, *LANE_SECRETS]
    assert HEALTHCHECKS_KEY_UUID not in [uuid for uuid, _ in run.reads()]


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["bare", "submit"])
def test_the_pass_is_handed_its_credentials_and_not_the_lanes_token(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    assert run.code == 0, run.output
    env = run.env()
    assert env["INERT_LANDING_CHANGE_MANAGER_TOKEN"] == value(READ_UUID)
    assert env["INERT_LANDING_ORCHESTRATOR_TOKEN"] == value(SYSTEM_UUID)
    assert env["INERT_LANDING_GITHUB_TOKEN"] == READ_TOKEN
    assert env["LANDER_STATE_DIR"] == str(tmp_path / "state")
    assert "BWS_ACCESS_TOKEN" not in env
    if args:
        assert env["SDS_BRIDGE_ALERT_TOKEN"] == value(ALERT_UUID)
    else:
        assert "SDS_BRIDGE_ALERT_TOKEN" not in env


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["bare", "submit"])
def test_credentials_already_in_the_environment_are_never_used(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    """Each secret is always read: `lane-run` passes none, and a preset value is discarded, the
    alert sender's on a bare pass too, which reads none."""
    preset = {
        name: "preset"
        for name in (
            "INERT_LANDING_CHANGE_MANAGER_TOKEN",
            "INERT_LANDING_ORCHESTRATOR_TOKEN",
            "INERT_LANDING_GITHUB_TOKEN",
            "SDS_BRIDGE_ALERT_TOKEN",
        )
    }
    run = _run(tmp_path, *args, **preset)
    assert run.code == 0, run.output
    assert run.lane_reads() == list(LANE_SECRETS if args else EVERY_PASS)
    assert "preset" not in run.env().values()


@pytest.mark.parametrize(
    ("args", "uuid"),
    [((), uuid) for uuid in EVERY_PASS] + [(("--submit",), ALERT_UUID)],
)
def test_an_unreadable_secret_fails_the_pass_before_the_lander(
    tmp_path: Path, args: tuple[str, ...], uuid: str
) -> None:
    run = _run(tmp_path, *args, BWS_REFUSES=uuid)
    assert run.code == 1, run.output
    assert "FATAL:" in run.output
    assert run.passes() == []


def test_the_state_directory_must_be_set(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit", LANDER_STATE_DIR=None)
    assert run.code == 1
    assert "LANDER_STATE_DIR is not set" in run.output
    assert run.lane_reads() == [] and run.passes() == []


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["bare", "submit"])
def test_no_token_reaches_an_argument(tmp_path: Path, args: tuple[str, ...]) -> None:
    run = _run(tmp_path, *args)
    assert run.code == 0, run.output
    argvs = [
        *(line.split(" argv=", 1)[1] for line in run.logs["bws"]),
        *(line.split(" key=", 1)[0] for line in run.lines("mint")),
        *(line.split(" lane_token=", 1)[0] for line in run.lines("derive")),
        *run.passes(),
        *run.logs["ping"],
    ]
    secrets = (LANE_TOKEN, READ_TOKEN, *(value(uuid) for uuid in LANE_SECRETS))
    for argv in argvs:
        for secret in secrets:
            assert secret not in argv, argv


# ---- the read token --------------------------------------------------------------------------


def test_the_mint_names_the_derived_repositories_and_two_read_permissions(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit")
    assert run.code == 0, run.output
    targets = " ".join(f"--repository {repository}" for repository in REPOSITORIES)
    assert run.lines("mint") == [
        f"mint args=--app-id 5684758 {targets}"
        " --permission metadata=read --permission pull_requests=read"
        # The key reaches the mint undecoded, in the form BWS holds it, without the lane's token.
        f" key={value(READ_APP_KEY_UUID)} lane_token= socket="
    ]


def test_the_list_is_derived_from_the_policy_credential_alone(tmp_path: Path) -> None:
    """Without the lane's token, and before the SYSTEM bearer is read."""
    run = _run(tmp_path)
    assert run.lines("derive") == [
        f"derive args=repositories lane_token= socket= cm={value(READ_UUID)} system="
    ]


@pytest.mark.parametrize("rc", [1, 2])
def test_an_underivable_list_mints_nothing_and_its_code_is_the_passs(
    tmp_path: Path, rc: int
) -> None:
    run = _run(tmp_path, "--submit", FAKE_DERIVE_RC=str(rc))
    assert run.code == rc, run.output
    assert "[UNUSABLE] the landing policy could not be read" in run.output
    assert f"inert-landing repositories exited {rc}" in run.output
    assert run.lines("mint") == [] and run.passes() == []
    assert run.lane_reads() == [READ_UUID]


def test_an_empty_list_mints_nothing_and_is_unusable(tmp_path: Path) -> None:
    run = _run(tmp_path, FAKE_REPOSITORIES="")
    assert run.code == 2, run.output
    assert "(an empty list)" in run.output
    assert run.lines("mint") == [] and run.passes() == []


def test_a_failed_mint_fails_the_pass(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit", FAKE_MINT_RC="1")
    assert run.code == 1, run.output
    assert "could not mint the inert lane's GitHub read token" in run.output
    assert run.passes() == []
    assert SYSTEM_UUID not in run.lane_reads()


# ---- the pass --------------------------------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["bare", "submit"])
def test_the_lander_runs_with_the_launchers_arguments(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _run(tmp_path, *args)
    assert run.passes() == [f"lander args={' '.join(args)}"]


@pytest.mark.parametrize("rc", [0, 1, 2, 3])
def test_the_programs_code_is_the_passs(tmp_path: Path, rc: int) -> None:
    assert _run(tmp_path, FAKE_LANDER_RC=str(rc)).code == rc


# ---- the container and the Mac ---------------------------------------------------------------


def test_the_container_skips_activation_and_reads_no_mac_identity(tmp_path: Path) -> None:
    run = _run(tmp_path, "--submit", BWS_ACCESS_TOKEN_SDS=NARROW_STANDIN)
    assert run.code == 0, run.output
    assert "[activation]" not in run.output
    assert NARROW_STANDIN not in [token for _, token in run.reads()]
    assert run.logs["gh"] == []


# ---- the container on the secrets broker (ADR-0055 amendment 30) ------------------------------

PING_URL_SECRET = "35557473-f80c-44e3-892a-b507013158b1"


def _brokered(tmp_path: Path, socket_path: Path, *args: str, **fake: str | None) -> Run:
    """The container as `lane-run` starts it on the broker: no token, the socket's marker, and the
    compose's ping-URL id. The broad stand-in is gone, so nothing can fall back to the Mac."""
    return _run(
        tmp_path,
        *args,
        BWS_ACCESS_TOKEN=None,
        BWS_ACCESS_TOKEN_BROAD=None,
        SDS_SECRETS_SOCKET=str(socket_path),
        SDS_DEADMAN_PING_URL_UUID=PING_URL_SECRET,
        **fake,
    )


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["bare", "submit"])
def test_container_branch_runs_on_the_socket_without_a_token(
    tmp_path: Path, broker, args: tuple[str, ...]
) -> None:
    socket_path = broker(
        {PING_URL_SECRET: "https://ping.example/x", **{u: value(u) for u in LANE_SECRETS}}
    )
    run = _brokered(tmp_path, socket_path, *args)
    assert run.code == 0, run.output
    # Every secret came through the socket: `bws` never ran, and the Mac's steps never did.
    assert run.logs["bws"] == []
    assert "[activation]" not in run.output
    assert run.logs["gh"] == []
    assert run.lines("derive") == [
        f"derive args=repositories lane_token= socket= cm={value(READ_UUID)} system="
    ]
    assert run.lines("mint")[0].endswith(f" key={value(READ_APP_KEY_UUID)} lane_token= socket=")
    env = run.env()
    assert env["INERT_LANDING_CHANGE_MANAGER_TOKEN"] == value(READ_UUID)
    assert env["INERT_LANDING_ORCHESTRATOR_TOKEN"] == value(SYSTEM_UUID)
    assert env["INERT_LANDING_GITHUB_TOKEN"] == READ_TOKEN
    # Neither the token nor the socket's marker reaches the lander.
    assert "BWS_ACCESS_TOKEN" not in env and "SDS_SECRETS_SOCKET" not in env
    if args:
        assert env["SDS_BRIDGE_ALERT_TOKEN"] == value(ALERT_UUID)
    else:
        assert "SDS_BRIDGE_ALERT_TOKEN" not in env
    assert run.passes() == [f"lander args={' '.join(args)}"]


def test_a_dead_socket_fails_the_pass_and_never_reaches_the_mac(
    tmp_path: Path, dead_socket: Path
) -> None:
    run = _brokered(tmp_path, dead_socket, "--submit")
    assert run.code == 1, run.output
    assert "FATAL: could not read the change-manager credential" in run.output
    assert run.logs["bws"] == [] and run.logs["gh"] == []
    assert "[activation]" not in run.output
    assert run.passes() == []


def test_the_keychain_and_gh_are_reached_only_after_the_container_block() -> None:
    """The container block exits, so every Keychain read and `gh` call after its closing `fi` is
    the Mac's. The dead-man helper's own Keychain read is reached only without a broad identity
    and a ping-URL id, which the container's compose names."""
    lines = LAUNCHER.read_text().splitlines()
    block = lines.index("if sds_in_lane_container; then")
    end = lines.index("fi", block)
    assert lines[end - 1].strip() == 'exit "$rc"'
    code = [(i, line) for i, line in enumerate(lines) if not line.lstrip().startswith("#")]
    keychain = [i for i, line in code if "/usr/bin/security" in line or "sds-token.sh" in line]
    gh = [i for i, line in code if "gh auth token" in line]
    assert len(keychain) == 2 and len(gh) == 1
    assert all(end < i for i in (*keychain, *gh))


@pytest.mark.parametrize("args", [(), ("--submit",)], ids=["bare", "submit"])
def test_on_the_mac_the_identities_and_gh_are_as_before_and_nothing_is_minted(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    run = _mac(tmp_path, *args)
    assert run.code == 0, run.output
    assert "[activation] helper missing" in run.output
    assert run.lines("mint") == [] and run.lines("derive") == []
    assert run.logs["gh"] == ["gh auth token"]
    expected = [
        (HEALTHCHECKS_KEY_UUID, BROAD_STANDIN),
        (READ_UUID, BROAD_STANDIN),
        (SYSTEM_UUID, NARROW_STANDIN),
    ]
    if args:
        expected.append((ALERT_UUID, NARROW_STANDIN))
    assert run.reads() == expected
    env = run.env()
    assert env["INERT_LANDING_CHANGE_MANAGER_TOKEN"] == value(READ_UUID)
    assert env["INERT_LANDING_ORCHESTRATOR_TOKEN"] == value(SYSTEM_UUID)
    assert env["INERT_LANDING_GITHUB_TOKEN"] == "gh-token"
    # As before, the Mac's narrow identity stays exported to the lander.
    assert env["BWS_ACCESS_TOKEN"] == NARROW_STANDIN
    assert "LANDER_STATE_DIR" not in env
    assert run.passes() == [f"lander args={' '.join(args)}"]


def test_on_the_mac_preset_credentials_are_used_as_before(tmp_path: Path) -> None:
    """The Mac's overrides stay: a preset credential is used and not read."""
    run = _mac(
        tmp_path,
        "--submit",
        INERT_LANDING_CHANGE_MANAGER_TOKEN="cm",
        INERT_LANDING_ORCHESTRATOR_TOKEN="orch",
        INERT_LANDING_GITHUB_TOKEN="gh",
        SDS_BRIDGE_ALERT_TOKEN="alert",
    )
    assert run.code == 0, run.output
    assert run.reads() == [(HEALTHCHECKS_KEY_UUID, BROAD_STANDIN)]
    assert run.logs["gh"] == []
    env = run.env()
    assert [
        env[name]
        for name in (
            "INERT_LANDING_CHANGE_MANAGER_TOKEN",
            "INERT_LANDING_ORCHESTRATOR_TOKEN",
            "INERT_LANDING_GITHUB_TOKEN",
            "SDS_BRIDGE_ALERT_TOKEN",
        )
    ] == ["cm", "orch", "gh", "alert"]


@pytest.mark.parametrize("rc", [0, 3])
def test_on_the_mac_the_programs_code_is_the_passs(tmp_path: Path, rc: int) -> None:
    assert _mac(tmp_path, "--submit", FAKE_LANDER_RC=str(rc)).code == rc


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "inert-landing")
    assert deploy.add_refusals(service, "inert-landing") == []
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["inert-landing"],
        # No `BWS_ACCESS_TOKEN`: the lane reads through its secrets-broker socket.
        "environment": {
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            "LANDER_STATE_DIR": "/var/lib/sds/inert-landing",
            # The BWS secret id of `inert-landing-ping-url`: not a secret, not the ping URL.
            "SDS_DEADMAN_PING_URL_UUID": "35557473-f80c-44e3-892a-b507013158b1",
        },
        "volumes": [
            "sds-lanes-inert-landing-state:/var/lib/sds/inert-landing",
            "/data/sds/broker/inert-landing:/run/sds-broker:ro",
        ],
        "restart": "unless-stopped",
    }
    document = yaml.safe_load(COMPOSE.read_text())
    # No `networks`: every service the lane calls is an HTTPS URL.
    assert set(document) == {"services", "volumes"}
    assert document["volumes"] == {"sds-lanes-inert-landing-state": None}
    assert deploy._state_volume(service, "inert-landing") == "sds-lanes-inert-landing-state"
    assert "inert-landing" in deploy.STATEFUL_LANES
    assert deploy.LANE_CHECKS["inert-landing"] == "sds-inert-landing"


def test_every_compose_variable_lane_run_must_pass_is_in_its_list() -> None:
    environment = deploy._environment(deploy.lane_service(COMPOSE.read_text(), "inert-landing"))
    passed = set(lane_run.lane_environment(environment, "/tmp/home"))
    assert set(environment) - passed == {"LANE_SCHEDULE_ENABLED"}


def test_the_crontab_runs_a_submitting_pass_hourly_at_37_beside_the_mac_jobs_35() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["37 * * * * lane-run inert-landing --submit"]
    plist = PLIST.read_text()
    assert "<dict><key>Minute</key><integer>35</integer></dict>" in plist
    assert "<string>__REPO_ROOT__/scripts/run-inert-landing.sh</string>" in plist
    assert "<string>--submit</string>" in plist


def test_no_two_lanes_start_a_pass_in_the_same_minute() -> None:
    minutes = lane_start_minutes()
    assert minutes["inert-landing"] == {37}
    # 35, the Mac job's minute, is the change proposer's.
    assert 35 in minutes["change-proposer"]
    for lane, taken in minutes.items():
        if lane != "inert-landing":
            assert 37 not in taken, lane


def test_the_repositorys_manifest_lists_the_lanes_secrets() -> None:
    """The lane's `--token` proof reads what the manifest lists for it: its four secrets and its
    ping-URL secret. The launcher names each secret it reads, except the ping-URL secret, whose id
    reaches it from the compose through `lane-run`."""
    manifest = Path(".bws-secrets.toml").read_text()
    ping_url_secret = "35557473-f80c-44e3-892a-b507013158b1"
    secrets = deploy.lane_secrets(manifest, "inert-landing")
    assert sorted(secrets) == sorted([*LANE_SECRETS, ping_url_secret])
    launcher = LAUNCHER.read_text()
    for uuid in secrets:
        assert uuid in launcher or uuid == ping_url_secret, uuid
