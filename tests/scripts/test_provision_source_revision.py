"""provision.sh's marked blocks, each run on its own as root-less bash.

The `source-revision` block rewrites only the `source_revision` line of an existing config. The
`service-user` block refuses a `rotation` user that could reach root (ADR-0055 amendment 29 item
1), read through stubbed `id` and `getent`. Neither needs a VM, a host or root.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

PROVISION = Path("scripts/rotation-worker-vm/provision.sh")
COMMIT = "0123456789abcdef0123456789abcdef01234567"
CONFIG = 'store = "root"\nsource_revision = "aaaaaaaaaaaa"\nstate_dir = "/x"\n'


def _block() -> str:
    found = re.search(
        r"# BEGIN source-revision\n(.*?)# END source-revision", PROVISION.read_text(), re.S
    )
    assert found is not None
    return found.group(1)


def _run(etc: Path) -> None:
    subprocess.run(
        ["bash", "-c", f'set -euo pipefail\ncommit={COMMIT}\netc="{etc}"\n{_block()}'],
        check=True,
    )


def test_it_rewrites_only_the_revision_line(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(CONFIG)
    _run(tmp_path)
    assert (tmp_path / "config.toml").read_text() == CONFIG.replace("aaaaaaaaaaaa", COMMIT[:12])
    assert not (tmp_path / "config.toml.new").exists()


def test_it_creates_no_config_when_none_exists(tmp_path: Path) -> None:
    _run(tmp_path)
    assert list(tmp_path.iterdir()) == []


SERVICE_USER_BLOCK = re.compile(r"# BEGIN service-user\n(.*?)# END service-user", re.S)
FOUND_USER = {"uid": "998", "groups": "rotation", "shell": "/usr/sbin/nologin"}


def _service_user_check(tmp_path: Path, **found: str) -> subprocess.CompletedProcess[str]:
    """Run the service-user block against an `id` and `getent` that report `found`."""
    user = FOUND_USER | found
    block = SERVICE_USER_BLOCK.search(PROVISION.read_text())
    assert block is not None
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "id").write_text(
        f'#!/bin/sh\ncase "$1" in -u) echo {user["uid"]} ;; -Gn) echo "{user["groups"]}" ;; esac\n'
    )
    (stubs / "getent").write_text(
        f"#!/bin/sh\necho 'rotation:x:{user['uid']}:{user['uid']}::/var/lib/rotation-worker:"
        f"{user['shell']}'\n"
    )
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\nservice_user=rotation\n{block.group(1)}"],
        env={"PATH": f"{stubs}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_user_it_creates_passes(tmp_path: Path) -> None:
    assert _service_user_check(tmp_path).returncode == 0


def test_a_user_in_the_docker_group_is_refused(tmp_path: Path) -> None:
    result = _service_user_check(tmp_path, groups="rotation docker")
    assert result.returncode == 2
    assert "docker" in result.stderr


def test_a_user_whose_only_group_is_another_is_refused(tmp_path: Path) -> None:
    assert _service_user_check(tmp_path, groups="sudo").returncode == 2


def test_a_user_with_a_login_shell_is_refused(tmp_path: Path) -> None:
    result = _service_user_check(tmp_path, shell="/bin/bash")
    assert result.returncode == 2
    assert "/bin/bash" in result.stderr


def test_a_user_with_uid_0_is_refused(tmp_path: Path) -> None:
    assert _service_user_check(tmp_path, uid="0").returncode == 2


def test_the_check_runs_after_the_user_exists_and_before_anything_is_installed() -> None:
    text = PROVISION.read_text()
    assert text.index("useradd ") < text.index("# BEGIN service-user") < text.index("install -d")
