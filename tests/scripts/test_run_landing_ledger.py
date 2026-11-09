"""`scripts/run-landing-ledger.sh` in the lane's container (ADR-0059 increment 3): which secrets it
reads with the lane's token, the GitHub mint's exact arguments, and that `gh` is never asked.

The Mac path's scope handling is held by `test_launcher_scope.py`, whose runs set no
`BWS_ACCESS_TOKEN`; its GitHub credential, the `gh` login, by one test here. Here the
launcher runs from a throwaway tree under `env -i` with what `lane-run` passes it: the lane's token
as `BWS_ACCESS_TOKEN`, and no Keychain identity. `bws`, `gh` and `curl` are stubs on PATH; the
venv's `python -m scripts.sds_scope`, `sds-github-mint` and `landing-ledger` are stubs that log.
Nothing reads the real Keychain, BWS, GitHub or Healthchecks.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from scripts import deploy_orchestrator as deploy

LAUNCHER = Path("scripts/run-landing-ledger.sh")
COMPOSE = Path("lanes/landing-ledger/compose.yaml")
CRONTAB = Path("lanes/landing-ledger/crontab")
PLIST = Path("scripts/com.devon.landing-ledger.plist")
OBSERVER_UUID = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
READ_APP_KEY_UUID = "314a966c-5654-4e3c-8358-b4fd01268775"
PING_URL_UUID = "33333333-3333-4333-8333-333333333333"
PING_URL = "https://hc.example/ping/landing-ledger"
LANE_TOKEN = "a-lane-token"
MINTED = "minted-installation-token"

# The container's `bws`: logs the UUID and whether the token arrived in its environment. A UUID in
# `$BWS_REFUSES` fails; the ping-URL secret holds `PING_URL`; every other secret `value-of-<uuid>`.
_BWS = f"""#!/bin/sh
echo "$3 token=${{BWS_ACCESS_TOKEN:+set}}" >> "$BWS_LOG"
case " $BWS_REFUSES " in *" $3 "*) exit 1 ;; esac
[ "$3" = "{PING_URL_UUID}" ] && {{ echo '{{"value": "{PING_URL}"}}'; exit 0; }}
printf '{{"value": "value-of-%s"}}' "$3"
"""
_GH = '#!/bin/sh\necho "gh $*" >> "$GH_LOG"\necho gh-token\n'
# A ping's URL arrives on stdin as curl's config; anything else (the name lookup's listing) fails.
_CURL = """#!/bin/sh
for argument in "$@"; do [ "$argument" = "-" ] && { cat >> "$PING_LOG"; exit 0; }; done
exit 1
"""
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
_LEDGER = """#!/bin/bash
echo "ledger $1 github=$LANDING_LEDGER_GITHUB_TOKEN observer=$LANDING_LEDGER_TOKEN \\
lane_token=${BWS_ACCESS_TOKEN:+set}" >> "$LANE_LOG"
exit "${FAKE_LEDGER_RC:-0}"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _run(tmp_path: Path, *args: str, **fake: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in (LAUNCHER.name, "sds-deadman.sh", "sds-bws.sh"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    _executable(venv / "python", _PYTHON)
    _executable(venv / "sds-github-mint", _MINT)
    _executable(venv / "landing-ledger", _LEDGER)
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
        "REAL_PYTHON": sys.executable,
        "BWS_ACCESS_TOKEN": LANE_TOKEN,
        "SDS_DEADMAN_PING_URL_UUID": PING_URL_UUID,
        "FAKE_SCOPE": "AlobarQuest/one\nAlobarQuest/two",
        "FAKE_MINTED": MINTED,
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


def test_the_pass_mints_its_github_token_with_exactly_these_arguments(tmp_path: Path) -> None:
    result, logs = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert logs["lane"] == [
        f"scope -m scripts.sds_scope ledger token=value-of-{OBSERVER_UUID} lane_token=",
        "mint --app-id 5684758 --repository AlobarQuest/one --repository AlobarQuest/two"
        " --permission metadata=read --permission contents=read"
        " --permission pull_requests=read --permission actions=read",
        # The key reaches the mint undecoded, in the form BWS holds it.
        f"stdin=value-of-{READ_APP_KEY_UUID}",
        "mint lane_token=",
        f"ledger record github={MINTED} observer=value-of-{OBSERVER_UUID} lane_token=",
        f"ledger audit github={MINTED} observer=value-of-{OBSERVER_UUID} lane_token=",
    ]


def test_every_read_uses_the_lanes_token_and_gh_is_never_asked(tmp_path: Path) -> None:
    result, logs = _run(tmp_path)
    assert result.returncode == 0
    assert logs["bws"] == [
        f"{PING_URL_UUID} token=set",
        f"{OBSERVER_UUID} token=set",
        f"{READ_APP_KEY_UUID} token=set",
    ]
    assert logs["gh"] == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", PING_URL]
    assert LANE_TOKEN not in result.stdout + result.stderr
    assert MINTED not in result.stdout + result.stderr


def test_the_container_skips_activation_rather_than_logging_it_missing(tmp_path: Path) -> None:
    result, _ = _run(tmp_path)
    assert "[activation]" not in result.stdout + result.stderr


def test_a_preset_github_token_is_replaced_by_the_mint(tmp_path: Path) -> None:
    _, logs = _run(tmp_path, LANDING_LEDGER_GITHUB_TOKEN="a-preset-token")
    assert all(f"github={MINTED}" in line for line in logs["lane"] if line.startswith("ledger"))


def test_a_failed_mint_exits_1_without_running_the_ledger_or_asking_gh(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, FAKE_MINT_RC="1")
    assert result.returncode == 1
    assert not any(line.startswith("ledger") for line in logs["lane"])
    assert logs["gh"] == []
    assert "could not mint the ledger's GitHub token" in result.stderr
    assert _pings(logs["ping"])[-1] == f"{PING_URL}/fail"


def test_an_unreadable_key_exits_1_without_running_the_ledger(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, BWS_REFUSES=READ_APP_KEY_UUID)
    assert result.returncode == 1
    assert "stdin=" in logs["lane"]
    assert not any(line.startswith("ledger") for line in logs["lane"])
    assert logs["gh"] == []


def test_an_empty_mint_answer_exits_1(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, FAKE_MINTED="")
    assert result.returncode == 1
    assert not any(line.startswith("ledger") for line in logs["lane"])


def test_an_empty_scope_exits_3_with_no_mint(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, FAKE_SCOPE="", FAKE_SCOPE_RC="3")
    assert result.returncode == 3
    assert [line.split()[0] for line in logs["lane"]] == ["scope"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])
    assert logs["gh"] == []


def test_a_scope_that_cannot_run_passes_its_code_through_with_no_mint(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, FAKE_SCOPE_RC="127")
    assert result.returncode == 127
    assert [line.split()[0] for line in logs["lane"]] == ["scope"]
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])


def test_an_incomplete_scope_mints_for_what_it_named_and_reports_3(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, FAKE_SCOPE="AlobarQuest/one", FAKE_SCOPE_RC="3")
    assert result.returncode == 3
    assert "mint --app-id 5684758 --repository AlobarQuest/one --permission" in logs["lane"][1]
    assert sum(line.startswith("ledger") for line in logs["lane"]) == 2


def test_an_unreadable_observer_bearer_exits_1_before_the_scope(tmp_path: Path) -> None:
    result, logs = _run(tmp_path, BWS_REFUSES=OBSERVER_UUID)
    assert result.returncode == 1
    assert logs["lane"] == []
    assert _pings(logs["ping"]) == [f"{PING_URL}/start", f"{PING_URL}/fail"]


def test_without_a_ping_url_secret_a_pass_pings_nothing(tmp_path: Path) -> None:
    """The compose's placeholder: an empty `SDS_DEADMAN_PING_URL_UUID` falls through to the name
    lookup. The stand-in broad identity keeps `/usr/bin/security` unasked, and the listing read
    fails, so this run's alerting is disabled: the pass can't report success."""
    result, logs = _run(tmp_path, SDS_DEADMAN_PING_URL_UUID="", BWS_ACCESS_TOKEN_BROAD="broad")
    assert result.returncode == 0
    assert logs["ping"] == []
    assert "alerting disabled this run" in result.stdout



def test_on_the_mac_the_github_token_is_the_gh_login_and_nothing_is_minted(tmp_path: Path) -> None:
    """The Mac path, unchanged until increment 6: no lane token, a Keychain identity (stood in
    for, so `/usr/bin/security` is never asked) and the operator's `gh` login."""
    result, logs = _run(
        tmp_path,
        BWS_ACCESS_TOKEN="",
        BWS_ACCESS_TOKEN_SDS="narrow",
        BWS_ACCESS_TOKEN_BROAD="broad",
        SDS_DEADMAN_PING_URL_UUID="",
    )
    assert result.returncode == 0, result.stderr
    assert logs["gh"] == ["gh auth token"]
    assert not any(line.startswith(("mint", "stdin=")) for line in logs["lane"])
    assert READ_APP_KEY_UUID not in "\n".join(logs["bws"])
    assert all("github=gh-token" in line for line in logs["lane"] if line.startswith("ledger"))
    assert sum(line.startswith("ledger") for line in logs["lane"]) == 2

# ---- the lane's tracked files --------------------------------------------------------------


def test_the_compose_meets_the_add_refusals_and_maps_nothing_else() -> None:
    service = deploy.lane_service(COMPOSE.read_text(), "landing-ledger")
    assert deploy.add_refusals(service, "landing-ledger") == []
    assert service == {
        "image": "ghcr.io/alobarquest/sds-lanes:${SDS_LANES_IMAGE_TAG}",
        "command": ["landing-ledger"],
        "environment": {
            "BWS_ACCESS_TOKEN": "${LANDING_LEDGER_BWS_ACCESS_TOKEN}",
            "LANE_SCHEDULE_ENABLED": "${LANE_SCHEDULE_ENABLED:-false}",
            # The placeholder until the lane's ping-URL secret exists (the compose's comment).
            "SDS_DEADMAN_PING_URL_UUID": "",
        },
        "restart": "unless-stopped",
    }
    assert set(yaml.safe_load(COMPOSE.read_text())) == {"services"}
    assert "landing-ledger" not in deploy.STATEFUL_LANES


def test_the_crontab_runs_the_pass_daily_at_0730_as_the_mac_job_does() -> None:
    lines = [
        line
        for line in CRONTAB.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["30 7 * * * lane-run landing-ledger"]
    plist = PLIST.read_text()
    assert "<key>Hour</key>\n        <integer>7</integer>" in plist
    assert "<key>Minute</key>\n        <integer>30</integer>" in plist
    assert "<string>__REPO_ROOT__/scripts/run-landing-ledger.sh</string>\n    </array>" in plist


def test_the_repositorys_manifest_lists_the_ledgers_secrets() -> None:
    manifest = Path(".bws-secrets.toml").read_text()
    assert deploy.lane_secrets(manifest, "landing-ledger") == [OBSERVER_UUID, READ_APP_KEY_UUID]
    launcher = LAUNCHER.read_text()
    for uuid in deploy.lane_secrets(manifest, "landing-ledger"):
        assert uuid in launcher, uuid
