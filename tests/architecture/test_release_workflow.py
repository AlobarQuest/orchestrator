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

import yaml

ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = ROOT / ".github/workflows/release-image.yml"


def test_pin_file_is_well_formed():
    pin = tomllib.loads((ROOT / "security-standards.pin.toml").read_text())
    assert set(pin) >= {"revision", "artifact_sha256"}
    assert len(pin["artifact_sha256"]) == 64
    assert all(c in "0123456789abcdef" for c in pin["artifact_sha256"])
    assert len(pin["revision"]) >= 7 and all(c in "0123456789abcdef" for c in pin["revision"])


def _workflow():
    return yaml.safe_load(RELEASE_WORKFLOW.read_text())


def test_workflow_is_dispatch_only_build_and_push_no_deploy():
    wf = _workflow()
    # `on:` parses as the YAML 1.1 boolean key `True` under PyYAML's safe_load; handle both
    # in case that resolver behavior ever changes.
    triggers = wf.get("on", wf.get(True))
    assert set(triggers) == {"workflow_dispatch"}
    # The control-plane guards (test_no_automatic_merge.py, test_ws33_scope_guards.py) exempt
    # this workflow by name, on the assumption that `contents: read` makes it structurally
    # incapable of pushing to the repo / merging / pushing main. Pin that invariant here so a
    # future widen to `contents: write` fails this test, not just the exempted guards.
    assert wf["permissions"] == {"contents": "read", "packages": "write"}
    text = RELEASE_WORKFLOW.read_text()
    # Build+push only: pushes to ghcr, but never calls Coolify / deploy.
    assert "ghcr.io/alobarquest/orchestrator:" in text
    assert "--platform linux/amd64" in text
    assert "SECURITY_STANDARDS_DEPLOY_KEY" in text
    assert "security-standards.pin.toml" in text
    assert "shape_registry_context.py" in text
    for forbidden in ("coolify", "sds.alobar.net", "curl -x post"):
        assert forbidden not in text.lower()
    # No moving tag.
    assert ":latest" not in text and ":main-" not in text
