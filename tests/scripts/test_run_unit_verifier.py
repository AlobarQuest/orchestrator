"""`scripts/run-unit-verifier.sh` on both hosts (ADR-0059 increment 3): in the lane's container,
which secrets it reads with the lane's token, the repositories it derives, the GitHub mint's exact
arguments, that `gh` is never asked, and that a token it can't derive or mint never stops the pass
but makes it report 3; on the Mac, `sds-token.sh`'s identity and the `gh` login, unchanged. The
lane's compose and crontab are checked here too.

The launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it: the lane's
token as `BWS_ACCESS_TOKEN` (the container), or nothing and a stand-in `sds-token.sh` (the Mac).
`bws`, `gh` and `curl` are stubs on PATH; the venv's `sds-github-mint` and `unit-verifier` are stubs
that log. Nothing reads the real Keychain, BWS, GitHub, the member record or Healthchecks.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import deploy_orchestrator as deploy

LAUNCHER = Path("scripts/run-unit-verifier.sh")
COMPOSE = Path("lanes/unit-verifier/compose.yaml")
CRONTAB = Path("lanes/unit-verifier/crontab")
PLIST = Path("scripts/com.devon.unit-verifier.plist")
VERIFIER_UUID = "660d5846-abcb-4751-be86-b483012899eb"
ALERT_UUID = "b415d03b-f92d-46b1-8a9d-b4f10103df49"
READ_APP_KEY_UUID = "314a966c-5654-4e3c-8358-b4fd01268775"
# The Healthchecks key the Mac's dead-man lookup by check name reads with the broad identity.
HEALTHCHECKS_KEY_UUID = "51509d5e-64f5-4781-ae82-b4ed00b644e1"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
PING_URL = "https://hc.example/ping/unit-verifier"
LANE_TOKEN = "a-lane-token"
MAC_IDENTITY = "a-mac-identity"
MINTED = "minted-installation-token"
REPOSITORIES = "Owner/one\nOwner/two"

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
# `python -c` is `sds-bws.sh`'s parse and runs for real.
_PYTHON = '#!/bin/bash\nexec "$REAL_PYTHON" "$@"\n'
# The Mac's `sds-token.sh`, in place of its Keychain read.
_SDS_TOKEN = f"BWS_ACCESS_TOKEN={MAC_IDENTITY}\nexport BWS_ACCESS_TOKEN\n"
# The mint logs its arguments, what reached its stdin, and whether the lane token was in its
# environment. It prints its token before a refusal, so a launcher that kept a failed mint's output
# would run the pass on it.
_MINT = """#!/bin/bash
echo "mint $*" >> "$LANE_LOG"
echo "stdin=$(cat)" >> "$LANE_LOG"
echo "mint lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
printf '%s' "$FAKE_MINTED"
[ "${FAKE_MINT_RC:-0}" -eq 0 ] || { echo "FATAL: refused" >&2; exit "$FAKE_MINT_RC"; }
"""
# `unit-verifier repositories` is the derivation, answered from the environment; any other run is
# the pass.
_VERIFIER = """#!/bin/bash
if [ "${1:-}" = "repositories" ]; then
  echo "repositories bearer=$ORCHESTRATOR_API_TOKEN key=$ORCHESTRATOR_API_CREDENTIAL_KEY_ID \\
lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
  printf '%s' "$FAKE_REPOSITORIES"
  exit "${FAKE_REPOSITORIES_RC:-0}"
fi
echo "verifier args=$* github=${ROTATION_VERIFIER_GITHUB_TOKEN:-} bearer=$ORCHESTRATOR_API_TOKEN \\
alert=${SDS_BRIDGE_ALERT_TOKEN:-} lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
exit "${FAKE_VERIFIER_RC:-0}"
"""
# The Mac path: no lane token, so `sds-token.sh` supplies the identity; a stand-in broad identity
# keeps `/usr/bin/security` from being asked by the dead-man lookup.
MAC = {"BWS_ACCESS_TOKEN": "", "BWS_ACCESS_TOKEN_BROAD": "broad", "SDS_DEADMAN_PING_URL_UUID": ""}

BEARER = f"value-of-{VERIFIER_UUID}"
ALERT = f"value-of-{ALERT_UUID}"


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(
    tmp_path: Path, *args: str, gh: str = _GH, **fake: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]]]:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (LAUNCHER.name, "sds-deadman.sh", "sds-bws.sh"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    (root / "scripts" / "sds-token.sh").write_text(_SDS_TOKEN)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    _executable(venv / "python", _PYTHON)
    _executable(venv / "sds-github-mint", _MINT)
    _executable(venv / "unit-verifier", _VERIFIER)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("bws", _BWS), ("gh", gh), ("curl", _CURL)):
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
        "SDS_DEADMAN_PING_URL_UUID": PING_URL_UUID,
        "FAKE_REPOSITORIES": REPOSITORIES,
        "FAKE_MINTED": MINTED,
        "REAL_PYTHON": sys.executable,
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
    return [line for line in lines if line.startswith("verifier")]


def _minted(lines: list[str]) -> bool:
    return any(line.startswith(("mint", "stdin=")) for line in lines)


def lane_start_minutes() -> dict[str, set[int]]:
    """Each lane's crontab start minutes: `M`, `*/S` or `A-B/S`, the forms the lanes use."""
    minutes: dict[str, set[int]] = {}
    for path in sorted(Path("lanes").glob("*/crontab")):
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            field = line.split()[0]
            span, _, step = field.partition("/")
            if span == "*":
                first, last = 0, 59
            elif "-" in span:
                first, last = (int(bound) for bound in span.split("-"))
            else:
                first = last = int(span)
            minutes[path.parent.name] = set(range(first, last + 1, int(step or 1)))
    return minutes


# ---- the container ---------------------------------------------------------------------------


def test_the_pass_mints_its_github_token_with_exactly_these_arguments(tmp_path: Path) -> None:
    result, logs = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert logs["lane"] == [
        # The derivation reads the member record with the VERIFIER bearer, without the lane token.
        f"repositories bearer={BEARER} key=orchestrator-verifier lane_token=",
        "mint --app-id 5684758 --repository Owner/one --repository Owner/two"
        " --permission metadata=read --permission administration=read",
        # The key reaches the mint undecoded, in the form BWS holds it.
        f"stdin=value-of-{READ_APP_KEY_UUID}",
        "mint lane_token=",
        # The lane token is gone before the pass runs.
        f"verifier args= github={MINTED} bearer={BEARER} alert={ALERT} lane_token=",
    ]


def test_every_read_uses_the_lanes_one_token_and_gh_is_never_asked(tmp_path: Path) -> None:
    result, logs = _run(tmp_path)
    assert result.returncode == 0
    assert logs["bws"] == [
        f"{PING_URL_UUID} token={LANE_TOKEN}",
        f"{VERIFIER_UUID} token={LANE_TOKEN}",
        f"{ALERT_UUID} token={LANE_TOKEN}",
        f"{READ_APP_KEY_UUID} token={LANE_TOKEN}",
    ]
    assert logs["gh"] == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", PING_URL]
    for secret in (LANE_TOKEN, MINTED, BEARER, ALERT):
        assert secret not in result.stdout + result.stderr


def test_the_container_skips_activation_rather_than_logging_it_missing(tmp_path: Path) -> None:
    result, _ = _run(tmp_path)
    assert "[activation]" not in result.stdout + result.stderr


def test_a_preset_github_token_is_replaced_by_the_mint(tmp_path: Path) -> None:
    _, logs = _run(tmp_path, ROTATION_VERIFIER_GITHUB_TOKEN="a-preset-token")
    [run] = _passes(logs["lane"])
    assert f"github={MINTED}" in run


def test_a_dry_run_still_mints_and_passes_its_flag(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert _passes(logs["lane"]) == [
        f"verifier args=--dry-run github={MINTED} bearer={BEARER} alert={ALERT} lane_token="
    ]


def test_a_failed_alert_token_fetch_still_runs_the_pass(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, BWS_REFUSES=ALERT_UUID)
    assert result.returncode == 0
    assert "alert-hub sender credential" in result.stderr
    assert _passes(logs["lane"]) == [
        f"verifier args= github={MINTED} bearer={BEARER} alert= lane_token="
    ]


def test_an_unreadable_verifier_bearer_exits_1_before_anything_runs(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, BWS_REFUSES=VERIFIER_UUID)
    assert result.returncode == 1
    assert logs["lane"] == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


@pytest.mark.parametrize(
    "failure",
    [
        {"FAKE_REPOSITORIES": "", "FAKE_REPOSITORIES_RC": "3"},
        {"FAKE_REPOSITORIES": ""},
        {"FAKE_REPOSITORIES": "", "FAKE_REPOSITORIES_RC": "127"},
        # Names on stdout from a derivation that failed are never a shorter list to mint for.
        {"FAKE_REPOSITORIES_RC": "3"},
    ],
    ids=["unreadable-record", "empty-list", "cannot-run", "names-then-failure"],
)
def test_an_underivable_list_mints_nothing_runs_the_pass_tokenless_and_exits_3(
    tmp_path: Path, failure: dict[str, str]
) -> None:
    result, logs = _run(tmp_path, ROTATION_VERIFIER_GITHUB_TOKEN="a-preset-token", **failure)
    assert result.returncode == 3
    assert "repositories could not be derived" in result.stderr
    assert not _minted(logs["lane"])
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])
    assert logs["gh"] == []
    # The pass ran, with no GitHub token at all: rotation units need none.
    assert _passes(logs["lane"]) == [
        f"verifier args= github= bearer={BEARER} alert={ALERT} lane_token="
    ]
    assert _pings(logs["ping"])[-1] == f"{PING_URL}/fail"
    for secret in (LANE_TOKEN, BEARER, ALERT):
        assert secret not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "failure",
    [{"FAKE_MINT_RC": "1"}, {"FAKE_MINTED": ""}, {"BWS_REFUSES": READ_APP_KEY_UUID}],
    ids=["mint-refused", "mint-empty", "key-unreadable"],
)
def test_a_failed_mint_runs_the_pass_tokenless_and_exits_3(
    tmp_path: Path, failure: dict[str, str]
) -> None:
    result, logs = _run(tmp_path, **failure)
    assert result.returncode == 3
    assert "could not mint the verifier's GitHub token" in result.stderr
    assert logs["gh"] == []
    assert _passes(logs["lane"]) == [
        f"verifier args= github= bearer={BEARER} alert={ALERT} lane_token="
    ]
    assert _pings(logs["ping"])[-1] == f"{PING_URL}/fail"
    for secret in (LANE_TOKEN, MINTED, BEARER, ALERT):
        assert secret not in result.stdout + result.stderr


@pytest.mark.parametrize(("program_exit", "expected"), [(0, 3), (2, 3), (3, 3), (1, 1), (127, 1)])
def test_a_tokenless_pass_reports_3_unless_the_tool_failed(
    tmp_path: Path, program_exit: int, expected: int
) -> None:
    result, _ = _run(tmp_path, FAKE_MINT_RC="1", FAKE_VERIFIER_RC=str(program_exit))
    assert result.returncode == expected


def test_without_a_ping_url_secret_a_pass_pings_nothing(tmp_path: Path) -> None:
    """An empty `SDS_DEADMAN_PING_URL_UUID` falls through to the name lookup, which
    needs the broad identity `lane-run` doesn't pass. The pass runs, and its alerting is disabled.
    `/usr/bin/security` is asked here (macOS has it, the image doesn't), so the stand-in broad
    identity keeps the real Keychain out of the test."""
    result, logs = _run(tmp_path, SDS_DEADMAN_PING_URL_UUID="", BWS_ACCESS_TOKEN_BROAD="broad")
    assert result.returncode == 0
    assert logs["ping"] == []
    assert "alerting disabled this run" in result.stdout
    assert len(_passes(logs["lane"])) == 1


# ---- both hosts ------------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["container", "mac"])
@pytest.mark.parametrize(("program_exit", "expected"), [(0, 0), (2, 2), (3, 3), (1, 1), (127, 1)])
def test_exit_codes_fold_as_before(
    tmp_path: Path, host: str, program_exit: int, expected: int
) -> None:
    environment = MAC if host == "mac" else {}
    result, logs = _run(tmp_path, **environment, FAKE_VERIFIER_RC=str(program_exit))
    assert result.returncode == expected
    assert len(_passes(logs["lane"])) == 1


# ---- the Mac ---------------------------------------------------------------------------------


def test_on_the_mac_sds_tokens_identity_and_the_gh_login_and_nothing_is_minted(
    tmp_path: Path,
) -> None:
    """The Mac path, unchanged until increment 6: `sds-token.sh`'s identity reads the bearer and the
    alert token, and the GitHub token is the operator's `gh` login."""
    result, logs = _run(tmp_path, **MAC)
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == [
        f"{HEALTHCHECKS_KEY_UUID} token=broad",
        f"{VERIFIER_UUID} token={MAC_IDENTITY}",
        f"{ALERT_UUID} token={MAC_IDENTITY}",
    ]
    assert logs["gh"] == ["gh auth token"]
    assert not _minted(logs["lane"])
    assert _passes(logs["lane"]) == [
        f"verifier args= github=gh-token bearer={BEARER} alert={ALERT} lane_token=set"
    ]


def test_on_the_mac_a_preset_github_token_is_kept(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, **MAC, ROTATION_VERIFIER_GITHUB_TOKEN="gh-preset")
    assert result.returncode == 0, result.stderr
    assert logs["gh"] == []
    assert "github=gh-preset" in _passes(logs["lane"])[0]


def test_on_the_mac_a_missing_gh_login_still_runs_the_pass(tmp_path: Path) -> None:
    # A `gh` that prints nothing stands for a missing login: the pass runs, as it always has.
    result, logs = _run(tmp_path, **MAC, gh='#!/bin/sh\necho "gh $*" >> "$GH_LOG"\n')
    assert result.returncode == 0
    assert _passes(logs["lane"])[0].startswith("verifier args= github= ")


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "unit-verifier")
    assert deploy.add_refusals(service, "unit-verifier") == []
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["unit-verifier"],
        "environment": {
            "BWS_ACCESS_TOKEN": "${UNIT_VERIFIER_BWS_ACCESS_TOKEN}",
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            "VERIFIER_STATE_DIR": "/var/lib/sds/unit-verifier",
            # The BWS secret id of `unit-verifier-ping-url`, not a secret and not the ping URL.
            "SDS_DEADMAN_PING_URL_UUID": "1cfc8352-c960-4afc-bd49-b4ff00e8783e",
        },
        "volumes": ["sds-lanes-unit-verifier-state:/var/lib/sds/unit-verifier"],
        "restart": "unless-stopped",
    }
    document = yaml.safe_load(COMPOSE.read_text())
    assert set(document) == {"services", "volumes"}
    assert document["volumes"] == {"sds-lanes-unit-verifier-state": None}
    assert "unit-verifier" in deploy.STATEFUL_LANES


def test_the_state_variable_reaches_the_launcher_and_names_the_verifiers_directory() -> None:
    from scripts import lane_run
    from unit_verifier import alerts

    assert alerts.STATE_DIR_ENV == "VERIFIER_STATE_DIR"
    assert alerts.STATE_DIR_ENV in lane_run.STATE_VARIABLES


def test_the_crontab_runs_the_pass_every_ten_minutes_as_the_mac_job_does() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["2-52/10 * * * * lane-run unit-verifier"]
    plist = PLIST.read_text()
    assert "<key>StartInterval</key>\n    <integer>600</integer>" in plist
    assert "<string>__REPO_ROOT__/scripts/run-unit-verifier.sh</string>\n    </array>" in plist


def test_no_other_lane_starts_a_pass_in_the_verifiers_minutes() -> None:
    """Clear of execution-watch's quarter-hours, the deploy watcher's 20, the revision watcher's 40
    and the landing ledger's 07:30."""
    minutes = lane_start_minutes()
    assert minutes["unit-verifier"] == {2, 12, 22, 32, 42, 52}
    for lane, taken in minutes.items():
        if lane != "unit-verifier":
            assert not taken & minutes["unit-verifier"], lane


def test_the_repositorys_manifest_lists_the_verifiers_secrets() -> None:
    manifest = Path(".bws-secrets.toml").read_text()
    ping_url_secret = "1cfc8352-c960-4afc-bd49-b4ff00e8783e"
    assert deploy.lane_secrets(manifest, "unit-verifier") == [
        VERIFIER_UUID,
        ALERT_UUID,
        READ_APP_KEY_UUID,
        ping_url_secret,
    ]
    # The launcher names each secret it reads, except the ping-URL secret, whose id reaches it
    # from the compose through `lane-run`.
    launcher = LAUNCHER.read_text()
    for uuid in deploy.lane_secrets(manifest, "unit-verifier"):
        assert uuid in launcher or uuid == ping_url_secret, uuid
