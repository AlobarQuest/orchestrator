"""Architecture guard: the security-standards registry-bundle pin file.

`security-standards.pin.toml` at the repo root is the single source of truth for the
production registry-bundle pin. The release workflow (Task 3) reads `revision` to check
out security-standards and passes both `revision` and `artifact_sha256` as Docker build
args; the Dockerfile's in-build gate fails closed if the assembled bundle's digest does
not match `artifact_sha256`.

This module is extended in Task 3 with assertions over the release workflow itself.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pin_file_is_well_formed():
    pin = tomllib.loads((ROOT / "security-standards.pin.toml").read_text())
    assert set(pin) >= {"revision", "artifact_sha256"}
    assert len(pin["artifact_sha256"]) == 64
    assert all(c in "0123456789abcdef" for c in pin["artifact_sha256"])
    assert len(pin["revision"]) >= 7 and all(c in "0123456789abcdef" for c in pin["revision"])
