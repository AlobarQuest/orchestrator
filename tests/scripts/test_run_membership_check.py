"""`scripts/run-membership-check.sh` in the lane's container (ADR-0059 increment 3, amendment 5):
which secrets it reads with the lane's token, the repositories it derives, the GitHub mint's exact
arguments, the anonymous clone of intent-packages and its line in the report, and the failure codes;
and that the Mac branch keeps its reads and clones nothing. `tests/scripts/test_launcher_scope.py`
covers the Mac pass's credentials and outcome pings.

The launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it: the lane's
token as `BWS_ACCESS_TOKEN` (the container), or the stand-in Keychain identities (the Mac). `bws`,
`gh`, `git` and `curl` are stubs on PATH; the venv's `python -m scripts.sds_scope`,
`sds-github-mint` and `membership-check` are stubs that log. Nothing reads the real Keychain, BWS,
GitHub, the member record or Healthchecks, and nothing is cloned.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from bump_proposer.standing import PACKAGES_REPOSITORY
from scripts import deploy_orchestrator as deploy
from tests.scripts.test_run_unit_verifier import lane_start_minutes

LAUNCHER = Path("scripts/run-membership-check.sh")
COMPOSE = Path("lanes/membership-check/compose.yaml")
CRONTAB = Path("lanes/membership-check/crontab")
PLIST = Path("scripts/com.devon.sds-membership-check.plist")
OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
CHANGE_MANAGER_UUID = "314f276d-55ca-4ddc-a24d-b4a3013508cd"
READ_APP_KEY_UUID = "314a966c-5654-4e3c-8358-b4fd01268775"
# The Healthchecks key the Mac's dead-man lookup by check name reads with the broad identity.
HEALTHCHECKS_KEY_UUID = "51509d5e-64f5-4781-ae82-b4ed00b644e1"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
PING_URL = "https://hc.example/ping/membership-check"
LANE_TOKEN = "a-lane-token"
MINTED = "minted-installation-token"
REPOSITORIES = "Owner/one\nOwner/intent-packages"
CLONE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
CLONE_URL = f"https://github.com/{PACKAGES_REPOSITORY}.git"

OBSERVER = f"value-of-{OBSERVER_UUID}"
CHANGE_MANAGER = f"value-of-{CHANGE_MANAGER_UUID}"
SECRETS = (LANE_TOKEN, MINTED, OBSERVER, CHANGE_MANAGER, f"value-of-{READ_APP_KEY_UUID}")

# The `bws` stub logs the UUID and the token it was given. A UUID in `$BWS_REFUSES` fails; the
# ping-URL secret holds `PING_URL`; every other secret `value-of-<uuid>`.
_BWS = f"""#!/bin/sh
echo "$3 token=${{BWS_ACCESS_TOKEN:-}}" >> "$BWS_LOG"
case " $BWS_REFUSES " in *" $3 "*) exit 1 ;; esac
[ "$3" = "{PING_URL_UUID}" ] && {{ echo '{{"value": "{PING_URL}"}}'; exit 0; }}
printf '{{"value": "value-of-%s"}}' "$3"
"""
_GH = '#!/bin/sh\necho "gh $*" >> "$GH_LOG"\necho gh-token\n'
# A ping's URL arrives on stdin as curl's config; anything else (the Mac's check listing) fails.
_CURL = """#!/bin/sh
for argument in "$@"; do [ "$argument" = "-" ] && { cat >> "$PING_LOG"; exit 0; }; done
exit 1
"""
# git logs each call with its whole environment, one `name=value` per line after the call, so a
# test sees every variable that reached it. A clone makes the directory unless `FAKE_GIT_RC` fails
# it, with a two-line error as git would print.
_GIT = f"""#!/bin/bash
{{ echo "git $*"; env | sort | sed 's/^/  /'; }} >> "$GIT_LOG"
case "$1" in
  clone)
    if [ "${{FAKE_GIT_RC:-0}}" -ne 0 ]; then
      echo "Cloning into '...'" >&2
      echo "fatal: could not read Username for 'https://github.com': terminal prompts disabled" >&2
      exit "$FAKE_GIT_RC"
    fi
    mkdir -p "${{@: -1}}/packages"
    ;;
  -C) echo "{CLONE_COMMIT}" ;;
esac
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
# The program logs what it was handed and prints one line into the report.
_CHECK = """#!/bin/bash
echo "check args=$* github=$SDS_MEMBERS_GITHUB_TOKEN gh=${GH_TOKEN:-} \\
observer=$SDS_MEMBERS_OBSERVER_TOKEN change_manager=$SDS_MEMBERS_CHANGE_MANAGER_TOKEN \\
packages=${BUMP_PROPOSER_PACKAGES_CHECKOUT:-unset} \
lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
echo "the program's report"
exit "${FAKE_CHECK_RC:-0}"
"""
# The Mac path: no lane token, stand-in Keychain identities so `/usr/bin/security` is never asked.
MAC = {
    "BWS_ACCESS_TOKEN": "",
    "BWS_ACCESS_TOKEN_SDS": "narrow",
    "BWS_ACCESS_TOKEN_BROAD": "broad",
    "SDS_DEADMAN_PING_URL_UUID": "",
}


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(
    tmp_path: Path, *args: str, **fake: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]], Path]:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (LAUNCHER.name, "sds-deadman.sh", "sds-bws.sh"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    _executable(venv / "python", _PYTHON)
    _executable(venv / "sds-github-mint", _MINT)
    _executable(venv / "membership-check", _CHECK)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (("bws", _BWS), ("gh", _GH), ("curl", _CURL), ("git", _GIT)):
        _executable(stubs / name, body)
    home = tmp_path / "home"
    home.mkdir()
    logs = {name: tmp_path / f"{name}.log" for name in ("lane", "bws", "gh", "git", "ping")}
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
        "GIT_LOG": str(logs["git"]),
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
    return result, {name: log.read_text().splitlines() for name, log in logs.items()}, home


def _pings(lines: list[str]) -> list[str]:
    return [line.removeprefix('url = "').removesuffix('"') for line in lines]


def _check_runs(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("check")]


def _git_calls(lines: list[str]) -> list[tuple[str, dict[str, str]]]:
    """Each git call and the environment it ran with."""
    calls: list[tuple[str, dict[str, str]]] = []
    for line in lines:
        if line.startswith("git "):
            calls.append((line, {}))
        else:
            name, _, value = line.strip().partition("=")
            calls[-1][1][name] = value
    return calls


def _report(result: subprocess.CompletedProcess[str]) -> list[str]:
    """The report `sds_outcome_end` copies to stdout, after any alerting line."""
    lines = result.stdout.splitlines()
    return [line for line in lines if not line.startswith("[membership-check] exit")]


MINT_CALL = (
    "mint --app-id 5684758 --repository Owner/one --repository Owner/intent-packages"
    " --permission metadata=read --permission contents=read --permission administration=read"
)


# ---- the container ---------------------------------------------------------------------------


def test_the_pass_mints_its_github_token_with_exactly_these_arguments(tmp_path: Path) -> None:
    result, logs, home = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert logs["lane"] == [
        # The derivation reads the member record's observed repositories with the observer
        # bearer, without the lane token.
        f"scope -m scripts.sds_scope ledger token={OBSERVER} lane_token=",
        MINT_CALL,
        # The key reaches the mint undecoded, in the form BWS holds it.
        f"stdin=value-of-{READ_APP_KEY_UUID}",
        "mint lane_token=",
        # The minted token is both GitHub variables, the checkout is the pass's clone, and the
        # lane token is gone before the program runs.
        f"check args= github={MINTED} gh={MINTED} observer={OBSERVER} "
        f"change_manager={CHANGE_MANAGER} packages={home}/intent-packages lane_token=",
    ]


def test_every_read_uses_the_lanes_one_token_and_gh_is_never_asked(tmp_path: Path) -> None:
    result, logs, _ = _run(tmp_path)
    assert result.returncode == 0
    assert logs["bws"] == [
        f"{PING_URL_UUID} token={LANE_TOKEN}",
        f"{OBSERVER_UUID} token={LANE_TOKEN}",
        f"{CHANGE_MANAGER_UUID} token={LANE_TOKEN}",
        f"{READ_APP_KEY_UUID} token={LANE_TOKEN}",
    ]
    assert HEALTHCHECKS_KEY_UUID not in "\n".join(logs["bws"])
    assert logs["gh"] == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", PING_URL]
    for secret in SECRETS:
        assert secret not in result.stdout + result.stderr


def test_the_clone_is_anonymous_shallow_at_main_into_the_passs_home(tmp_path: Path) -> None:
    """Every token variable is preset, so each is already exported when the pass assigns it: the
    clone's `env -u` list, not the order of the exports, keeps them from git."""
    preset = {
        name: "preset"
        for name in (
            "GH_TOKEN",
            "SDS_MEMBERS_GITHUB_TOKEN",
            "SDS_MEMBERS_OBSERVER_TOKEN",
            "SDS_MEMBERS_CHANGE_MANAGER_TOKEN",
        )
    }
    result, logs, home = _run(tmp_path, **preset)
    assert result.returncode == 0, result.stderr
    calls = _git_calls(logs["git"])
    assert [call for call, _ in calls] == [
        f"git clone --quiet --depth 1 --branch main {CLONE_URL} {home}/intent-packages",
        f"git -C {home}/intent-packages rev-parse HEAD",
    ]
    for _, environment in calls:
        assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
        assert environment["GIT_TERMINAL_PROMPT"] == "0"
        # No credential reaches git, by name or by value.
        for name in (
            "BWS_ACCESS_TOKEN",
            "GH_TOKEN",
            "SDS_MEMBERS_GITHUB_TOKEN",
            "SDS_MEMBERS_OBSERVER_TOKEN",
            "SDS_MEMBERS_CHANGE_MANAGER_TOKEN",
        ):
            assert name not in environment, name
        # The stubs' own `FAKE_*` answers hold the minted value: the harness's, not the pass's.
        reached = [value for name, value in environment.items() if not name.startswith("FAKE_")]
        for secret in SECRETS:
            assert not any(secret in value for value in reached), secret


def test_the_clone_url_names_the_repository_packages_repository_names() -> None:
    """ADR-0059 amendment 5 open item 2: the launcher spells the URL as a literal, held here to
    `PACKAGES_REPOSITORY`."""
    literal = re.search(r'^PACKAGES_CLONE_URL="([^"]+)"$', LAUNCHER.read_text(), re.MULTILINE)
    assert literal is not None
    assert literal.group(1) == CLONE_URL == "https://github.com/AlobarQuest/intent-packages.git"


def test_the_report_carries_the_clone_line_then_the_programs_output(tmp_path: Path) -> None:
    result, _, _ = _run(tmp_path)
    assert result.returncode == 0
    assert _report(result) == [
        f"[membership-check] intent-packages clone: commit {CLONE_COMMIT}",
        "the program's report",
    ]


def test_a_failed_clone_still_runs_the_program_and_exits_its_code(tmp_path: Path) -> None:
    """The program records the missing checkout as a gap and exits 3; the launcher never makes the
    clone its own failure (amendment 5 item 5)."""
    result, logs, home = _run(tmp_path, FAKE_GIT_RC="128", FAKE_CHECK_RC="3")
    assert result.returncode == 3
    runs = _check_runs(logs["lane"])
    assert len(runs) == 1
    assert f"packages={home}/intent-packages" in runs[0]
    # One line naming git's code and its whole error.
    assert _report(result) == [
        "[membership-check] intent-packages clone failed: git exited 128: Cloning into '...' "
        "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
        "the program's report",
    ]
    assert [call for call, _ in _git_calls(logs["git"])] == [
        f"git clone --quiet --depth 1 --branch main {CLONE_URL} {home}/intent-packages"
    ]
    assert _pings(logs["ping"])[-1] == f"{PING_URL}/fail"


def test_a_failed_clone_with_a_disagreement_exits_2(tmp_path: Path) -> None:
    result, logs, _ = _run(tmp_path, FAKE_GIT_RC="128", FAKE_CHECK_RC="2")
    assert result.returncode == 2
    assert len(_check_runs(logs["lane"])) == 1


def test_the_container_skips_activation_rather_than_logging_it_missing(tmp_path: Path) -> None:
    result, _, _ = _run(tmp_path)
    assert "[activation]" not in result.stdout + result.stderr


def test_a_preset_github_token_is_replaced_by_the_mint(tmp_path: Path) -> None:
    _, logs, _ = _run(tmp_path, SDS_MEMBERS_GITHUB_TOKEN="a-preset-token", GH_TOKEN="a-preset")
    runs = _check_runs(logs["lane"])
    assert len(runs) == 1
    assert f"github={MINTED} gh={MINTED} " in runs[0]


@pytest.mark.parametrize("refused", [OBSERVER_UUID, CHANGE_MANAGER_UUID])
def test_an_unreadable_bearer_exits_1_before_the_derivation(tmp_path: Path, refused: str) -> None:
    result, logs, _ = _run(tmp_path, BWS_REFUSES=refused)
    assert result.returncode == 1
    assert "the lane's token" in result.stderr
    assert logs["lane"] == []
    assert logs["git"] == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


def test_a_failed_mint_exits_1_without_checking_or_asking_gh(tmp_path: Path) -> None:
    result, logs, _ = _run(tmp_path, FAKE_MINT_RC="1")
    assert result.returncode == 1
    assert _check_runs(logs["lane"]) == []
    assert logs["gh"] == []
    assert logs["git"] == []
    assert "could not mint the membership check's GitHub token" in result.stderr
    assert "sds-github-mint exited 1" in result.stderr
    assert _pings(logs["ping"])[-1] == f"{PING_URL}/fail"


def test_an_unreadable_key_exits_1_without_checking(tmp_path: Path) -> None:
    result, logs, _ = _run(tmp_path, BWS_REFUSES=READ_APP_KEY_UUID)
    assert result.returncode == 1
    assert "stdin=" in logs["lane"]
    assert _check_runs(logs["lane"]) == []


def test_an_empty_mint_answer_exits_1(tmp_path: Path) -> None:
    result, logs, _ = _run(tmp_path, FAKE_MINTED="")
    assert result.returncode == 1
    assert _check_runs(logs["lane"]) == []


@pytest.mark.parametrize(
    ("scope", "scope_rc"), [("", "3"), (REPOSITORIES, "3"), ("", "0")], ids=["3", "3-out", "empty"]
)
def test_an_underivable_list_exits_3_with_no_mint(
    tmp_path: Path, scope: str, scope_rc: str
) -> None:
    result, logs, _ = _run(tmp_path, FAKE_SCOPE=scope, FAKE_SCOPE_RC=scope_rc)
    assert result.returncode == 3
    assert [line.split()[0] for line in logs["lane"]] == ["scope"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])
    assert logs["git"] == []
    assert "nothing was minted or checked" in result.stderr
    assert _pings(logs["ping"])[-1] == f"{PING_URL}/fail"


@pytest.mark.parametrize("scope_rc", ["1", "127"])
def test_a_derivation_that_fails_otherwise_passes_its_code_through_with_no_mint(
    tmp_path: Path, scope_rc: str
) -> None:
    result, logs, _ = _run(tmp_path, FAKE_SCOPE_RC=scope_rc)
    assert result.returncode == int(scope_rc)
    assert [line.split()[0] for line in logs["lane"]] == ["scope"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])
    assert f"sds_scope exited {scope_rc}" in result.stderr


def test_without_a_ping_url_secret_the_pass_still_runs_and_pings_nothing(tmp_path: Path) -> None:
    """An empty `SDS_DEADMAN_PING_URL_UUID` falls through to the name lookup, which
    needs the broad identity `lane-run` doesn't pass. `/usr/bin/security` is asked there (macOS
    has it, the image doesn't), so the stand-in broad identity keeps the real Keychain out of the
    test; in the container nothing would read the Healthchecks key at all."""
    result, logs, _ = _run(tmp_path, SDS_DEADMAN_PING_URL_UUID="", BWS_ACCESS_TOKEN_BROAD="broad")
    assert result.returncode == 0
    assert logs["ping"] == []
    assert "alerting disabled this run" in result.stdout
    assert len(_check_runs(logs["lane"])) == 1
    # Only the stand-in reads the key; the lane's token never does.
    assert f"{HEALTHCHECKS_KEY_UUID} token={LANE_TOKEN}" not in logs["bws"]


@pytest.mark.parametrize(("program_exit", "expected"), [(0, 0), (1, 1), (2, 2), (3, 3)])
def test_the_programs_code_is_the_passs_code(
    tmp_path: Path, program_exit: int, expected: int
) -> None:
    result, logs, _ = _run(tmp_path, FAKE_CHECK_RC=str(program_exit))
    assert result.returncode == expected
    assert len(_check_runs(logs["lane"])) == 1


# ---- the Mac ---------------------------------------------------------------------------------


def test_on_the_mac_the_reads_are_unchanged_and_nothing_is_cloned_or_minted(
    tmp_path: Path,
) -> None:
    """The Mac path, unchanged until increment 6: the narrow identity reads the observer bearer,
    the broad one the change-manager bearer and the dead-man's Healthchecks key, the GitHub token
    is the operator's `gh` login, and the standing packages come from the default checkout."""
    result, logs, _ = _run(tmp_path, **MAC)
    assert result.returncode == 0, result.stderr
    assert logs["bws"] == [
        f"{HEALTHCHECKS_KEY_UUID} token=broad",
        f"{OBSERVER_UUID} token=narrow",
        f"{CHANGE_MANAGER_UUID} token=broad",
    ]
    assert logs["gh"] == ["gh auth token"]
    assert logs["git"] == []
    assert not any(line.startswith(("scope", "mint", "stdin=")) for line in logs["lane"])
    assert _check_runs(logs["lane"]) == [
        f"check args= github=gh-token gh= observer={OBSERVER} "
        f"change_manager={CHANGE_MANAGER} packages=unset lane_token="
    ]
    assert "intent-packages clone" not in result.stdout


# ---- the lane's tracked files ----------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "membership-check")
    assert deploy.add_refusals(service, "membership-check") == []
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["membership-check"],
        "environment": {
            "BWS_ACCESS_TOKEN": "${MEMBERSHIP_CHECK_BWS_ACCESS_TOKEN}",
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            # The BWS secret id of `membership-check-ping-url`, not a secret and not the ping URL.
            "SDS_DEADMAN_PING_URL_UUID": "5fc44897-b4df-44ae-8491-b5000129d9be",
        },
        "restart": "unless-stopped",
    }
    assert set(yaml.safe_load(COMPOSE.read_text())) == {"services"}
    # Stateless (ADR-0059 decision 1): no volume, and the outcome alarm's check is the Mac's.
    assert "membership-check" not in deploy.STATEFUL_LANES
    assert deploy.LANE_CHECKS["membership-check"] == "sds-membership-check"


def test_the_crontab_runs_the_pass_daily_beside_the_mac_jobs_time() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["47 7 * * * lane-run membership-check"]
    plist = PLIST.read_text()
    assert "<key>Hour</key><integer>7</integer><key>Minute</key><integer>45</integer>" in plist
    # The image's zone, which the landing ledger's 07:30 runs in too.
    assert "ENV TZ=America/New_York" in Path("lanes/Dockerfile").read_text()


def test_no_two_lanes_start_a_pass_in_the_same_minute() -> None:
    """Minute 47 is clear of execution-watch's quarter-hours (the Mac job's 45), the unit
    verifier's 2-52/10, the deploy watcher's 20, the pin watcher's 25, the landing ledger's 30 and
    the revision watcher's 40."""
    minutes = lane_start_minutes()
    assert minutes["membership-check"] == {47}
    for lane, taken in minutes.items():
        if lane != "membership-check":
            assert 47 not in taken, lane


def test_the_repositorys_manifest_lists_the_checks_secrets() -> None:
    manifest = Path(".bws-secrets.toml").read_text()
    ping_url_secret = "5fc44897-b4df-44ae-8491-b5000129d9be"
    assert deploy.lane_secrets(manifest, "membership-check") == [
        OBSERVER_UUID,
        CHANGE_MANAGER_UUID,
        READ_APP_KEY_UUID,
        ping_url_secret,
    ]
    # The launcher names each secret it reads, except the ping-URL secret, whose id reaches it
    # from the compose through `lane-run`.
    launcher = LAUNCHER.read_text()
    for uuid in deploy.lane_secrets(manifest, "membership-check"):
        assert uuid in launcher or uuid == ping_url_secret, uuid
