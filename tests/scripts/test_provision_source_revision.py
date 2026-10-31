"""provision.sh rewrites only the `source_revision` line of an existing config.

The block between the BEGIN/END markers is run on its own, as root-less bash against a temporary
config, so the test needs neither a VM nor root.
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
