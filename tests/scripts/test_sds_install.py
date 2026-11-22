"""`sds_upsert_check` in `scripts/sds-install.sh`: one Healthchecks check upserted from its payload.

No installer in this repository calls it; a person runs it by hand to create a new lane's check
(`docs/operations/deploy.md`, the follow-up mint's step 3). Each case sources the real helper and
`sds-bws.sh` from a throwaway tree, with `curl` and `bws` stubs on PATH and the broad identity
preset, so the Keychain is never read and nothing is sent anywhere.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

KEY_UUID = "51509d5e-64f5-4781-ae82-b4ed00b644e1"
KEY = "not-a-real-healthchecks-key"
PAYLOAD = json.dumps({"name": "sds-example", "unique": ["name"]})

_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$CURL_ARGS"
cat >> "$CURL_STDIN"
printf '%s' "${HC_STATUS:-201}"
"""
_BWS = f"""#!/bin/sh
printf '%s\\n' "$*" >> "$BWS_LOG"
[ -z "${{BWS_FAIL:-}}" ] || exit 1
printf '{{"value": "{KEY}"}}'
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _upsert(tmp_path: Path, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    for name in ("sds-install.sh", "sds-bws.sh"):
        shutil.copy(Path("scripts") / name, root / "scripts" / name)
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to((Path.cwd() / ".venv" / "bin" / "python").resolve())
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "curl", _CURL)
    _executable(stubs / "bws", _BWS)
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "BWS_ACCESS_TOKEN_BROAD": "not-a-real-identity",
        "CURL_ARGS": str(tmp_path / "args"),
        "CURL_STDIN": str(tmp_path / "stdin"),
        "BWS_LOG": str(tmp_path / "bws.log"),
        "PAYLOAD": PAYLOAD,
        **env_overrides,
    }
    script = f'source "{root}/scripts/sds-install.sh"; sds_upsert_check "$PAYLOAD"'
    return subprocess.run(
        ["/bin/bash", "-c", script], env=env, capture_output=True, text=True, check=False
    )


def test_a_new_check_is_created_and_the_key_reaches_curl_only_on_stdin(tmp_path: Path) -> None:
    result = _upsert(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "created"
    args = (tmp_path / "args").read_text()
    assert KEY not in args + result.stdout + result.stderr
    assert f'header = "X-Api-Key: {KEY}"' in (tmp_path / "stdin").read_text()
    assert "https://healthcheck.alobar.net/api/v3/checks/" in args
    assert json.loads(args.split("--data-binary ", 1)[1].strip()) == json.loads(PAYLOAD)
    assert KEY_UUID in (tmp_path / "bws.log").read_text()


def test_an_existing_check_is_updated(tmp_path: Path) -> None:
    result = _upsert(tmp_path, HC_STATUS="200")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "updated"


def test_any_other_answer_warns_and_fails(tmp_path: Path) -> None:
    result = _upsert(tmp_path, HC_STATUS="500")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "WARNING: upserting the Healthchecks check answered '500'" in result.stderr


def test_an_unreadable_key_warns_and_sends_nothing(tmp_path: Path) -> None:
    result = _upsert(tmp_path, BWS_FAIL="1")
    assert result.returncode != 0
    assert "the Healthchecks API key could not be read" in result.stderr
    assert not (tmp_path / "args").exists()
