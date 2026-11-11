"""`scripts/run-revision-watcher.sh` on both hosts (ADR-0059 increment 3): in the lane's container,
which secrets it reads with the lane's token, the repositories it derives, the GitHub mint's exact
arguments, and that `gh` is never asked; on the Mac, the two Keychain identities and the `gh`
login, unchanged. On both, the alert token is fetched on the acting pass only and the exit codes
fold as before.

The launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it: the lane's
token as `BWS_ACCESS_TOKEN` (the container), or the stand-in Keychain identities (the Mac). `bws`,
`gh` and `curl` are stubs on PATH; the venv's `python -m scripts.sds_scope`, `sds-github-mint` and
`revision-watcher` are stubs that log. Nothing reads the real Keychain, BWS, GitHub, the member
record or Healthchecks.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import deploy_orchestrator as deploy
from tests.scripts.test_run_unit_verifier import lane_start_minutes

LAUNCHER = Path("scripts/run-revision-watcher.sh")
COMPOSE = Path("lanes/revision-watcher/compose.yaml")
CRONTAB = Path("lanes/revision-watcher/crontab")
PLIST = Path("scripts/com.devon.revision-watcher.plist")
OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
PLATFORM_UUID = "bbd71f41-b7df-4ae9-8fdb-b41501447308"
ESTATE_UUID = "726a18ba-7a38-4ecc-aa03-b49a015fd302"
ALERT_UUID = "b415d03b-f92d-46b1-8a9d-b4f10103df49"
READ_APP_KEY_UUID = "314a966c-5654-4e3c-8358-b4fd01268775"
# The Healthchecks key the Mac's dead-man lookup by check name reads with the broad identity.
HEALTHCHECKS_KEY_UUID = "51509d5e-64f5-4781-ae82-b4ed00b644e1"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
PING_URL = "https://hc.example/ping/revision-watcher"
LANE_TOKEN = "a-lane-token"
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
# `python -c` is `sds-bws.sh`'s parse and runs for real; `python -m scripts.sds_scope` is the
# derivation, answered from the environment.
_PYTHON = """#!/bin/bash
if [ "$1" = "-c" ]; then exec "$REAL_PYTHON" "$@"; fi
echo "scope $* token=$SDS_SCOPE_ORCHESTRATOR_TOKEN \\
lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
printf '%s' "$FAKE_SCOPE"
exit "${FAKE_SCOPE_RC:-0}"
"""
# The mint logs its arguments, what reached its stdin, and whether the lane token was in its
# environment.
_MINT = """#!/bin/bash
echo "mint $*" >> "$LANE_LOG"
echo "stdin=$(cat)" >> "$LANE_LOG"
echo "mint lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
[ "${FAKE_MINT_RC:-0}" -eq 0 ] || { echo "FATAL: refused" >&2; exit "$FAKE_MINT_RC"; }
printf '%s' "$FAKE_MINTED"
"""
_WATCHER = """#!/bin/bash
echo "watcher args=$* github=$REVISION_WATCHER_GITHUB_TOKEN observer=$ORCHESTRATOR_API_TOKEN \\
platform=$REVISION_WATCHER_PLATFORM_TOKEN estate=$REVISION_WATCHER_ESTATE_KEY \\
alert=${SDS_BRIDGE_ALERT_TOKEN:-} lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
exit "${FAKE_WATCHER_RC:-0}"
"""
# The Mac path: no lane token, stand-in Keychain identities so `/usr/bin/security` is never asked.
MAC = {
    "BWS_ACCESS_TOKEN": "",
    "BWS_ACCESS_TOKEN_SDS": "narrow",
    "BWS_ACCESS_TOKEN_BROAD": "broad",
    "SDS_DEADMAN_PING_URL_UUID": "",
}

OBSERVER = f"value-of-{OBSERVER_UUID}"
PLATFORM = f"value-of-{PLATFORM_UUID}"
ESTATE = f"value-of-{ESTATE_UUID}"
ALERT = f"value-of-{ALERT_UUID}"


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(
    tmp_path: Path, *args: str, gh: str = _GH, **fake: str
) -> tuple[subprocess.CompletedProcess[str], dict]:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (LAUNCHER.name, "sds-deadman.sh", "sds-bws.sh"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    _executable(venv / "python", _PYTHON)
    _executable(venv / "sds-github-mint", _MINT)
    _executable(venv / "revision-watcher", _WATCHER)
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
        "FAKE_SCOPE": REPOSITORIES,
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


def _watcher_runs(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("watcher")]


def _reached_derivation_or_later(lines: list[str]) -> bool:
    return any(line.startswith(("scope", "mint", "stdin=", "watcher")) for line in lines)


# ---- the container ---------------------------------------------------------------------------


def test_the_pass_mints_its_github_token_with_exactly_these_arguments(tmp_path: Path) -> None:
    result, logs = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert logs["lane"] == [
        # The derivation reads the member record with the observer bearer, without the lane token.
        f"scope -m scripts.sds_scope apps token={OBSERVER} lane_token=",
        "mint --app-id 5684758 --repository Owner/one --repository Owner/two"
        " --permission metadata=read --permission contents=read",
        # The key reaches the mint undecoded, in the form BWS holds it.
        f"stdin=value-of-{READ_APP_KEY_UUID}",
        "mint lane_token=",
        # The lane token is gone before the watcher runs.
        f"watcher args= github={MINTED} observer={OBSERVER} platform={PLATFORM} estate={ESTATE}"
        f" alert={ALERT} lane_token=",
    ]


def test_every_read_uses_the_lanes_one_token_and_gh_is_never_asked(tmp_path: Path) -> None:
    result, logs = _run(tmp_path)
    assert result.returncode == 0
    assert logs["bws"] == [
        f"{PING_URL_UUID} token={LANE_TOKEN}",
        f"{OBSERVER_UUID} token={LANE_TOKEN}",
        f"{PLATFORM_UUID} token={LANE_TOKEN}",
        f"{ESTATE_UUID} token={LANE_TOKEN}",
        f"{ALERT_UUID} token={LANE_TOKEN}",
        f"{READ_APP_KEY_UUID} token={LANE_TOKEN}",
    ]
    assert logs["gh"] == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", PING_URL]
    for secret in (LANE_TOKEN, MINTED, OBSERVER, PLATFORM, ESTATE, ALERT):
        assert secret not in result.stdout + result.stderr


def test_the_container_skips_activation_rather_than_logging_it_missing(tmp_path: Path) -> None:
    result, _ = _run(tmp_path)
    assert "[activation]" not in result.stdout + result.stderr


def test_a_preset_github_token_is_replaced_by_the_mint(tmp_path: Path) -> None:
    _, logs = _run(tmp_path, REVISION_WATCHER_GITHUB_TOKEN="a-preset-token")
    runs = _watcher_runs(logs["lane"])
    assert len(runs) == 1
    assert f"github={MINTED}" in runs[0]


def test_a_dry_run_fetches_no_alert_token_and_pings_nothing(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert ALERT_UUID not in "\n".join(logs["bws"])
    assert _watcher_runs(logs["lane"]) == [
        f"watcher args=--dry-run github={MINTED} observer={OBSERVER} platform={PLATFORM}"
        f" estate={ESTATE} alert= lane_token="
    ]
    assert logs["ping"] == []


def test_an_acting_pass_that_cannot_read_the_alert_token_exits_1_before_the_mint(
    tmp_path: Path,
) -> None:
    result, logs = _run(tmp_path, BWS_REFUSES=ALERT_UUID)
    assert result.returncode == 1
    assert "alert sender token" in result.stderr
    assert not _reached_derivation_or_later(logs["lane"])


def test_an_unreadable_secret_exits_1_before_the_derivation(tmp_path: Path) -> None:
    for uuid in (OBSERVER_UUID, PLATFORM_UUID, ESTATE_UUID):
        run_dir = tmp_path / uuid
        run_dir.mkdir()
        result, logs = _run(run_dir, BWS_REFUSES=uuid)
        assert result.returncode == 1, uuid
        assert "the lane's token" in result.stderr, uuid
        assert logs["lane"] == [], uuid
        assert _pings(logs["ping"]) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


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
    result, logs = _run(tmp_path, FAKE_SCOPE="", FAKE_SCOPE_RC="3")
    assert result.returncode == 3
    assert [line.split()[0] for line in logs["lane"]] == ["scope"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])
    assert logs["gh"] == []
    assert _pings(logs["ping"])[-1] == f"{PING_URL}/fail"


def test_an_empty_list_with_exit_0_still_exits_3_with_no_mint(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, FAKE_SCOPE="")
    assert result.returncode == 3
    assert [line.split()[0] for line in logs["lane"]] == ["scope"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])


def test_a_derivation_that_cannot_run_passes_its_code_through_with_no_mint(
    tmp_path: Path,
) -> None:
    result, logs = _run(tmp_path, FAKE_SCOPE_RC="127")
    assert result.returncode == 127
    assert [line.split()[0] for line in logs["lane"]] == ["scope"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])


def test_without_a_ping_url_secret_a_pass_pings_nothing(tmp_path: Path) -> None:
    """The compose's empty `SDS_DEADMAN_PING_URL_UUID` falls through to the name lookup, which
    needs the broad identity `lane-run` doesn't pass. The pass runs, and its alerting is disabled.
    `/usr/bin/security` is asked here (macOS has it, the image doesn't), so the stand-in broad
    identity keeps the real Keychain out of the test."""
    result, logs = _run(tmp_path, SDS_DEADMAN_PING_URL_UUID="", BWS_ACCESS_TOKEN_BROAD="broad")
    assert result.returncode == 0
    assert logs["ping"] == []
    assert "alerting disabled this run" in result.stdout
    assert len(_watcher_runs(logs["lane"])) == 1


# ---- both hosts ------------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["container", "mac"])
@pytest.mark.parametrize(("program_exit", "expected"), [(0, 0), (2, 2), (3, 3), (1, 1), (127, 1)])
def test_exit_codes_fold_as_before(
    tmp_path: Path, host: str, program_exit: int, expected: int
) -> None:
    environment = MAC if host == "mac" else {}
    result, logs = _run(tmp_path, **environment, FAKE_WATCHER_RC=str(program_exit))
    assert result.returncode == expected
    assert len(_watcher_runs(logs["lane"])) == 1


# ---- the Mac ---------------------------------------------------------------------------------


def test_on_the_mac_two_identities_and_the_gh_login_and_nothing_is_minted(tmp_path: Path) -> None:
    """The Mac path, unchanged until increment 6: the narrow identity reads the observer bearer and
    the alert token, the broad one the platform token and App Brain's key, and the GitHub token is
    the operator's `gh` login."""
    result, logs = _run(tmp_path, **MAC)
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == [
        f"{HEALTHCHECKS_KEY_UUID} token=broad",
        f"{OBSERVER_UUID} token=narrow",
        f"{PLATFORM_UUID} token=broad",
        f"{ESTATE_UUID} token=broad",
        f"{ALERT_UUID} token=narrow",
    ]
    assert logs["gh"] == ["gh auth token"]
    assert not any(line.startswith(("scope", "mint", "stdin=")) for line in logs["lane"])
    assert _watcher_runs(logs["lane"]) == [
        f"watcher args= github=gh-token observer={OBSERVER} platform={PLATFORM} estate={ESTATE}"
        f" alert={ALERT} lane_token="
    ]


def test_on_the_mac_a_dry_run_fetches_no_alert_token(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, "--dry-run", **MAC)
    assert result.returncode == 0, result.stderr
    assert ALERT_UUID not in "\n".join(logs["bws"])
    assert _watcher_runs(logs["lane"])[0].startswith("watcher args=--dry-run github=gh-token")


def test_on_the_mac_a_preset_github_token_is_kept(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, **MAC, REVISION_WATCHER_GITHUB_TOKEN="gh-preset")
    assert result.returncode == 0, result.stderr
    assert logs["gh"] == []
    assert "github=gh-preset" in _watcher_runs(logs["lane"])[0]


def test_on_the_mac_an_acting_pass_that_cannot_read_the_alert_token_exits_1(
    tmp_path: Path,
) -> None:
    result, logs = _run(tmp_path, **MAC, BWS_REFUSES=ALERT_UUID)
    assert result.returncode == 1
    assert "alert sender token" in result.stderr
    assert "narrow identity" in result.stderr
    assert _watcher_runs(logs["lane"]) == []


def test_on_the_mac_a_missing_gh_login_exits_1(tmp_path: Path) -> None:
    # A `gh` that prints nothing stands for a missing login.
    result, logs = _run(tmp_path, **MAC, gh='#!/bin/sh\necho "gh $*" >> "$GH_LOG"\n')
    assert result.returncode == 1
    assert "no GitHub credential" in result.stderr
    assert logs["gh"] == ["gh auth token"]
    assert _watcher_runs(logs["lane"]) == []


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "revision-watcher")
    assert deploy.add_refusals(service, "revision-watcher") == []
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["revision-watcher"],
        "environment": {
            "BWS_ACCESS_TOKEN": "${REVISION_WATCHER_BWS_ACCESS_TOKEN}",
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            "REVISION_WATCHER_STATE_DIR": "/var/lib/sds/revision-watcher",
            # Empty until the lane's ping-URL secret exists (`test_deploy_lanes.py`'s
            # `placeholders`).
            "SDS_DEADMAN_PING_URL_UUID": "",
        },
        "volumes": ["sds-lanes-revision-watcher-state:/var/lib/sds/revision-watcher"],
        "restart": "unless-stopped",
    }
    document = yaml.safe_load(COMPOSE.read_text())
    assert set(document) == {"services", "volumes"}
    assert document["volumes"] == {"sds-lanes-revision-watcher-state": None}
    assert "revision-watcher" in deploy.STATEFUL_LANES


def test_the_state_variable_reaches_the_launcher_and_names_the_watchers_directory() -> None:
    from revision_watcher import alerts
    from scripts import lane_run

    assert alerts.STATE_DIR_ENV == "REVISION_WATCHER_STATE_DIR"
    assert alerts.STATE_DIR_ENV in lane_run.STATE_VARIABLES


def test_the_crontab_runs_the_pass_hourly_as_the_mac_job_does() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["40 * * * * lane-run revision-watcher"]
    plist = PLIST.read_text()
    assert "<key>StartInterval</key>\n    <integer>3600</integer>" in plist
    assert "<string>__REPO_ROOT__/scripts/run-revision-watcher.sh</string>\n    </array>" in plist


def test_no_two_lanes_start_a_pass_in_the_same_minute() -> None:
    """Minute 40 is clear of the deploy watcher's 20, execution-watch's quarter-hours and the
    landing ledger's 07:30."""
    minutes = lane_start_minutes()
    assert minutes["revision-watcher"] == {40}
    for lane, taken in minutes.items():
        if lane != "revision-watcher":
            assert 40 not in taken, lane


def test_the_repositorys_manifest_lists_the_watchers_secrets() -> None:
    manifest = Path(".bws-secrets.toml").read_text()
    assert deploy.lane_secrets(manifest, "revision-watcher") == [
        OBSERVER_UUID,
        PLATFORM_UUID,
        ESTATE_UUID,
        ALERT_UUID,
        READ_APP_KEY_UUID,
    ]
    launcher = LAUNCHER.read_text()
    for uuid in deploy.lane_secrets(manifest, "revision-watcher"):
        assert uuid in launcher, uuid
